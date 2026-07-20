"""
Adapter WAHA (WhatsApp HTTP API self-host).

Implementa a porta ChannelAdapter contra a API REST do WAHA, ligada ao inbound
(`webhooks/waha_receiver`) e ao envio (`webhooks/ghl_provider` e o pipeline).

Diferenças-chave vs Z-API (ver ChannelCapabilities):
- WAHA REENTREGA as próprias mensagens (fromMe) -> dedup por provider_message_id
  é obrigatório (o pipeline compartilhado cuida disso).
- Áudio: o TTS entrega data-url (vai como base64 em file.data), o anexo do CRM
  entrega URL http (vai como file.url, com o WAHA transcodificando).
"""

from __future__ import annotations

import re
from typing import Optional

from channels.base import ChannelCapabilities, OutboundResult, ParsedMessage
from services.waha_service import get_global_waha_config, waha_service
from utils.config import settings
from utils.logger import logger

# Sufixos de identidade do WhatsApp. Removemos @c.us/@s.whatsapp.net (contato
# normal) mas PRESERVAMOS @lid (leads de anúncio) para o fluxo GHL, igual à Z-API.
_STRIP_SUFFIX = re.compile(r"@(c\.us|s\.whatsapp\.net)$")

# Só dígitos do começo de um jid, descartando device (":77") e sufixo de servidor.
# "554797838884:77@s.whatsapp.net" -> "554797838884" · "..@lid" não casa (sem dígito de telefone).
_JID_DIGITS = re.compile(r"^(\d{6,})")

# Extensão que o WhatsApp entrega -> extensão que o player do GHL reconhece.
# Só normaliza áudio; imagem/vídeo/doc já vêm com extensão que o GHL entende.
# O proxy sabe reverter para buscar o arquivo real no WAHA (ver _WAHA_EXT_FALLBACK).
_EXT_EXIBICAO = {"oga": "ogg"}
_WAHA_EXT_FALLBACK = {"ogg": ["ogg", "oga"]}


def _display_ext(filename: str) -> str:
    """Reescreve só a extensão do basename para a que o GHL renderiza como mídia."""
    stem, _, ext = filename.rpartition(".")
    if not stem:
        return filename
    nova = _EXT_EXIBICAO.get(ext.lower())
    return f"{stem}.{nova}" if nova else filename


def _rotulo_de_midia(mimetype: str, filename: Optional[str] = None) -> str:
    """
    Texto que representa a mídia quando ela chega sem legenda.

    Serve a dois propósitos: o operador vê no CRM o que o lead mandou (em vez de
    uma linha vazia), e o turno deixa de ser descartado por falta de texto.
    Figurinha tem rótulo próprio — responder a uma figurinha como se fosse foto
    faz o agente parecer que não entendeu.
    """
    mt = (mimetype or "").lower()
    if mt.startswith("audio/"):
        return "🎤 Áudio recebido"
    if "webp" in mt:
        return "🩹 Figurinha recebida"
    if mt.startswith("image/"):
        return "📸 Imagem recebida"
    if mt.startswith("video/"):
        return "🎬 Vídeo recebido"
    if filename:
        return f"📄 Documento recebido: {filename}"
    return "📎 Arquivo recebido"


def _phone_from_jid(jid: str) -> Optional[str]:
    """Telefone (só dígitos) a partir de um jid @c.us/@s.whatsapp.net, ou None se for @lid/vazio."""
    if not jid or "@lid" in jid:
        return None
    m = _JID_DIGITS.match(jid)
    return m.group(1) if m else None


class WAHAChannel:
    channel = "whatsapp"
    provider = "waha"
    capabilities = ChannelCapabilities(
        supports_audio_ptt=True,
        supports_typing_delay=True,
        outbound_media_accepts_data_url=False,  # WAHA precisa base64 em file.data
        provider_reechoes_own_msgs=True,         # WAHA reentrega fromMe -> dedup obrigatório
    )

    # ── Config por tenant ──

    def _cfg(self, tenant) -> tuple[str, str, str]:
        """Servidor vem do config GLOBAL (um WAHA para todos); o tenant guarda só a
        sessão (o número). As colunas waha_base_url/waha_api_key ficam como override
        opcional, para o caso raro de um tenant ter servidor dedicado."""
        base = getattr(tenant, "waha_base_url", None) or ""
        key = getattr(tenant, "waha_api_key", None) or ""
        if not base or not key:
            g_url, g_key = get_global_waha_config()
            base = base or (g_url or "")
            key = key or (g_key or "")
        session = getattr(tenant, "waha_session", None) or getattr(tenant, "location_id", "") or ""
        return base, key, session

    def public_media_url(self, tenant, media_url: Optional[str]) -> Optional[str]:
        """
        URL do NOSSO proxy que o CRM consegue baixar, ou None.

        A URL crua do WAHA é interna e autenticada; o CRM não a busca. Extraímos o
        basename (o messageId.ext) e montamos o link do proxy público
        (`/media/whatsapp/{location_id}/{filename}`), que serve o arquivo com a
        credencial do lado de cá.

        A extensão é normalizada por `_display_ext`: o GHL guarda a URL como está
        (não re-hospeda o anexo de entrada) e decide o modo de exibição pela
        extensão. Voz do WhatsApp chega como `.oga`, que o GHL trata como arquivo;
        renomeando para `.ogg` — mesmo container Ogg — ele mostra o player.
        """
        if not media_url:
            return None
        marcador = "/api/files/"
        if marcador not in media_url:
            return None
        base = (getattr(settings, "public_base_url", "") or "").rstrip("/")
        if not base:
            return None
        filename = media_url.split("/")[-1].split("?")[0]
        if not filename:
            return None
        loc = getattr(tenant, "location_id", "")
        return f"{base}/media/whatsapp/{loc}/{_display_ext(filename)}"

    def media_fetch(self, tenant, url: Optional[str]) -> tuple[Optional[str], dict]:
        """
        (URL alcançável, headers) para baixar mídia deste provedor.

        O WAHA monta `media.url` com o host interno dele — em produção sai como
        `http://localhost:3000/api/files/...`, que não resolve de dentro do nosso
        container nem da internet. Reescrevemos para o servidor configurado e
        devolvemos a credencial em HEADER, nunca embutida na URL: essa URL
        aparece em log e poderia acabar num payload, e a chave é GLOBAL do
        servidor compartilhado — vazá-la daria acesso às sessões de todo mundo.
        """
        if not url:
            return None, {}
        base, key, _ = self._cfg(tenant)
        alcancavel = url
        if base:
            marcador = "/api/files/"
            if marcador in url:
                alcancavel = f"{base.rstrip('/')}{marcador}{url.split(marcador, 1)[1]}"
        return alcancavel, ({"X-Api-Key": key} if key else {})

    def credentials_ok(self, tenant) -> bool:
        base, key, session = self._cfg(tenant)
        return bool(base and session)

    def _chat_id(self, to: str) -> str:
        """
        Destinatário no formato que o WAHA espera.

        A Z-API tolerava telefone formatado porque descartava tudo que não é
        dígito; o WAHA aceita o POST com um chatId torto e a mensagem
        simplesmente não sai — falha silenciosa. Por isso normalizamos aqui:
        "+55 47 99720-4869" → "5547997204869@c.us".
        """
        alvo = (to or "").strip()
        if "@" in alvo:
            if alvo.endswith("@lid"):
                # Identidade de lead de anúncio: não é um chatId comum. Deixamos
                # passar (o GOWS resolve alguns casos) mas registramos, porque é
                # candidato número 1 quando "enviou e não chegou".
                logger.warning(f"[WAHA] Enviando para identidade @lid ({alvo}); entrega não é garantida.")
            return alvo
        digitos = re.sub(r"\D", "", alvo)
        return f"{digitos or alvo}@c.us"

    @staticmethod
    def _extract_message_id(resp: Optional[dict]) -> Optional[str]:
        """
        Id da mensagem enviada, sempre string — ou None.

        O id do WAHA muda de forma por engine: string direta, `key.id`, ou um
        objeto `{"_serialized": "..."}`. Devolver o objeto cru faria o
        `save_message_mapping` gravar um dict na chave primária, e o status de
        entrega dessa mensagem nunca mais casaria.
        """
        if not isinstance(resp, dict):
            return None
        bruto = (
            resp.get("id")
            or (resp.get("key") or {}).get("id")
            or (resp.get("_data") or {}).get("id")
        )
        if isinstance(bruto, dict):
            bruto = bruto.get("_serialized") or bruto.get("id")
        return bruto if isinstance(bruto, str) and bruto.strip() else None

    def _result(self, resp: Optional[dict]) -> OutboundResult:
        """
        Resposta do WAHA -> OutboundResult.

        `resp is not None` significa que o servidor aceitou (2xx). Mantemos isso
        como sucesso mesmo sem id: marcar 'failed' uma mensagem que de fato saiu
        levaria o operador a reenviar, e o cliente receberia duas vezes. Mas sem
        id o vínculo com o CRM se perde e o status congela — por isso o aviso.
        """
        msg_id = self._extract_message_id(resp)
        if resp is not None and not msg_id:
            logger.warning(
                "[WAHA] Envio aceito sem id de mensagem — status de entrega não subirá para o CRM. "
                f"Resposta: {str(resp)[:200]}"
            )
        return OutboundResult(ok=resp is not None, provider_message_id=msg_id)

    # ── Inbound ──

    def parse_inbound(
        self, location_id: str, payload: dict, headers: Optional[dict] = None
    ) -> Optional[ParsedMessage]:
        if payload.get("event") != "message":
            return None  # message.ack / session.status / etc. não são inbound de conversa

        p = payload.get("payload") or {}
        raw_from = p.get("from") or ""
        is_group = raw_from.endswith("@g.us")
        sender_id = _STRIP_SUFFIX.sub("", raw_from)  # preserva @lid
        sender_lid = None

        # Resolução LID -> telefone SEM I/O: o motor GOWS já entrega o número real
        # no próprio payload (Info.SenderAlt) para quem chega como @lid. Quando o
        # `from` é @lid mas o SenderAlt traz o telefone, adotamos o telefone como
        # identidade e guardamos o @lid original — assim o contato no CRM nasce com
        # número (não "Lead do WhatsApp (Anúncio)" sem telefone) e a resposta do
        # operador tem para onde ir. Se SenderAlt não vier, o receiver tenta o
        # fallback HTTP; se esse também falhar, seguimos com @lid (comportamento antigo).
        info = (p.get("_data") or {}).get("Info") or {}
        if "@lid" in sender_id:
            fone = _phone_from_jid(info.get("SenderAlt") or "")
            if fone:
                sender_lid = sender_id
                sender_id = fone

        media = p.get("media") or {}
        mimetype = str(media.get("mimetype") or "")
        is_audio = mimetype.startswith("audio/")
        media_url = media.get("url")
        media_filename = media.get("filename")

        # Mídia do WAHA fica atrás de X-Api-Key (/api/files) e a URL vem com o
        # host interno do container. Ou seja: NÃO é anexo que o CRM consiga
        # buscar. Vai em media_url (para o STT, que passa credencial) e o CRM
        # recebe um texto descritivo. `attachments` fica vazio de propósito.
        if p.get("hasMedia") and not media_url:
            logger.warning(
                f"[WAHA] hasMedia=true mas sem url (mimetype={mimetype or '?'}, "
                f"erro={media.get('error') or '-'}) — mídia não será processada."
            )

        text = p.get("body") or ""
        audio_url = media_url if is_audio else None
        if not text and media_url:
            # Sem rótulo, a mensagem chega vazia no CRM e some no guard do
            # pipeline. A Z-API já faz isso; o WAHA passava direto com body "".
            text = _rotulo_de_midia(mimetype, media_filename)

        return ParsedMessage(
            channel=self.channel,
            provider=self.provider,
            location_id=location_id,
            sender_id=sender_id,
            provider_message_id=p.get("id"),
            text=text,
            is_audio=is_audio,
            audio_url=audio_url,
            attachments=[],  # mídia do WAHA é autenticada; ver ParsedMessage.attachments
            media_url=media_url,
            media_mimetype=mimetype or None,
            media_filename=media_filename,
            is_group=is_group,
            from_me=bool(p.get("fromMe")),
            # notifyName costuma vir vazio no GOWS; PushName (em Info) traz o nome real.
            sender_name=p.get("notifyName") or info.get("PushName") or "",
            message_type=payload.get("event") or "",
            event_kind="message",
            sender_lid=sender_lid,
            raw=payload,
        )

    # ── Outbound ──

    async def send_text(self, tenant, to: str, text: str, *, typing_delay: int = 0) -> OutboundResult:
        base, key, session = self._cfg(tenant)
        resp = await waha_service.send_text(base, key, session, self._chat_id(to), text)
        return self._result(resp)

    async def send_image(self, tenant, to: str, image_url: str, caption: str = "") -> OutboundResult:
        base, key, session = self._cfg(tenant)
        resp = await waha_service.send_image(base, key, session, self._chat_id(to), image_url, caption)
        return self._result(resp)

    async def send_audio(self, tenant, to: str, audio_data_url: str) -> OutboundResult:
        """
        Aceita as duas origens de áudio do sistema, que têm formatos diferentes:
        o TTS entrega "data:audio/ogg;base64,<b64>" e o anexo do CRM entrega uma
        URL http. Tratar a URL como base64 (o que o split por vírgula fazia)
        colocava a própria URL dentro de file.data — o áudio nunca saía, e se a
        URL assinada tivesse uma vírgula ainda ia cortada ao meio.
        """
        base, key, session = self._cfg(tenant)
        alvo = self._chat_id(to)
        if audio_data_url.startswith("http"):
            resp = await waha_service.send_voice(base, key, session, alvo, audio_url=audio_data_url)
        else:
            b64 = audio_data_url.split(",", 1)[1] if "," in audio_data_url else audio_data_url
            resp = await waha_service.send_voice(base, key, session, alvo, b64)
        return self._result(resp)

    async def send_document(self, tenant, to: str, document_url: str, filename: str = "documento") -> OutboundResult:
        base, key, session = self._cfg(tenant)
        # O WAHA baixa a URL do lado dele — anexo do CRM precisa ser público.
        resp = await waha_service.send_file(base, key, session, self._chat_id(to), document_url, filename)
        return self._result(resp)

    async def register_webhook(self, tenant, public_base_url: str) -> bool:
        base, key, session = self._cfg(tenant)
        if not (base and session):
            return False
        webhook_url = f"{public_base_url.rstrip('/')}/webhook/whatsapp/{tenant.location_id}"
        hmac_key = getattr(settings, "waha_webhook_hmac_key", None) or None
        return await waha_service.set_session_webhook(
            base, key, session, webhook_url,
            events=["message", "message.ack", "session.status"],
            hmac_key=hmac_key,
        )
