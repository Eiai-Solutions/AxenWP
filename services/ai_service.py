import asyncio
import re
import base64
import tempfile
import os
from typing import List, Optional
from datetime import datetime

import httpx
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage

from data.database import SessionLocal
from data.models import AIAgent, ChatHistory, UsageLog, QualifiedLead
from utils.logger import logger
from utils.guardrails import (
    contains_forbidden_phrase,
    should_escalate as check_escalation,
    strip_emojis,
    contains_placeholder,
)


def _save_usage_log(location_id: str, service: str, model: str = None,
                    input_tokens: int = 0, output_tokens: int = 0,
                    characters: int = 0, cost_usd: float = 0.0):
    """Salva um registro de uso de API no banco (sync, chamar via to_thread)."""
    db = SessionLocal()
    try:
        log = UsageLog(
            location_id=location_id,
            service=service,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            characters=characters,
            cost_usd=cost_usd,
        )
        db.add(log)
        db.commit()
    except Exception as e:
        logger.error(f"Erro ao salvar usage log: {e}")
        db.rollback()
    finally:
        db.close()


async def transcribe_audio(audio_url: str, groq_api_key: str) -> Optional[str]:
    """
    Baixa o áudio da URL (Z-API) e transcreve usando Groq Whisper (gratuito e rápido).
    Retorna o texto transcrito ou None em caso de erro.
    """
    try:
        # 1. Baixar o áudio da Z-API
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            audio_resp = await client.get(audio_url)
            if audio_resp.status_code != 200:
                logger.error(f"Erro ao baixar áudio da Z-API: status={audio_resp.status_code}")
                return None
            audio_bytes = audio_resp.content

        # 2. Salvar em arquivo temporário para enviar ao Groq
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            # 3. Enviar para Groq Whisper API (OpenAI-compatible)
            async with httpx.AsyncClient(timeout=60.0) as client:
                with open(tmp_path, "rb") as f:
                    resp = await client.post(
                        "https://api.groq.com/openai/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {groq_api_key}"},
                        data={
                            "model": "whisper-large-v3",
                            "language": "pt",
                            "response_format": "text",
                        },
                        files={"file": ("audio.ogg", f, "audio/ogg")},
                    )

                if resp.status_code == 200:
                    transcription = resp.text.strip()
                    logger.info(f"Áudio transcrito com sucesso ({len(transcription)} chars): {transcription[:80]}...")
                    return transcription
                else:
                    logger.error(f"Erro na transcrição Groq Whisper: status={resp.status_code}, body={resp.text}")
                    return None
        finally:
            os.unlink(tmp_path)

    except Exception as e:
        logger.error(f"Exceção ao transcrever áudio: {e}")
        return None


def _contains_special_content(text: str) -> bool:
    """
    Verifica se o texto contém conteúdo que ficaria bugado em TTS:
    - URLs / links (https://, www., .com, .com.br)
    - Emails (@)
    - Valores monetários (R$ 1.500,00)
    - Números longos (telefone, CEP, CNPJ, CPF)
    - Endereços (Rua, Av., Avenida, etc.)
    """
    patterns = [
        r'https?://',                          # URLs
        r'www\.',                              # Links www
        r'\.[a-z]{2,3}\.br\b',                # Domínios .com.br, .org.br
        r'\b\w+\.(com|net|org|io|app)\b',     # Domínios genéricos
        r'@',                                  # Emails
        r'R\$\s*[\d.,]+',                      # Valores em reais: R$ 1.500,00
        r'\d{1,3}(?:\.\d{3})+,\d{2}',         # Formato brasileiro de número: 1.500,00
        r'\d{5}[\-]?\d{3}',                   # CEP: 01234-567 ou 01234567
        r'\d{2}[\.\-]?\d{3}[\.\-]?\d{3}[\/\-]?\d{4}[\-]?\d{2}',  # CNPJ
        r'\d{3}[\.\-]?\d{3}[\.\-]?\d{3}[\-]?\d{2}',              # CPF
        r'\(?\d{2}\)?\s*\d{4,5}[\-\s]?\d{4}', # Telefone: (11) 99999-9999
        r'\b(?:Rua|Av\.|Avenida|Alameda|Travessa|Praça|Rodovia|Estrada|R\.)\s',  # Endereços
    ]
    combined = '|'.join(patterns)
    return bool(re.search(combined, text, re.IGNORECASE))

class PostgresChatMessageHistory:
    """Implementa o histórico de mensagens direto via SQLAlchemy (equivalente ao Postgres Chat Memory do n8n)."""
    
    def __init__(self, session_id: str):
        # A session_id idealmente será o location_id + telefone, por ex: "location123__+55119999999"
        self.session_id = session_id
        self.max_history = 20 # Mantém o contexto de no máximo N mensagens

    def _fetch_messages_sync(self) -> List[BaseMessage]:
        """Sync DB fetch — meant to be called via asyncio.to_thread()."""
        db = SessionLocal()
        try:
            records = db.query(ChatHistory).filter(
                ChatHistory.session_id == self.session_id
            ).order_by(ChatHistory.id.desc()).limit(self.max_history).all()
            records.reverse()
            msgs = []
            for r in records:
                if r.message_type == "human":
                    msgs.append(HumanMessage(content=r.content))
                elif r.message_type == "ai":
                    msgs.append(AIMessage(content=r.content))
            return msgs
        finally:
            db.close()

    async def aget_messages(self) -> List[BaseMessage]:
        """Async wrapper that runs the sync DB query in a thread."""
        return await asyncio.to_thread(self._fetch_messages_sync)

    def _add_message_sync(self, type_: str, content: str) -> None:
        """Sync DB write — meant to be called via asyncio.to_thread()."""
        db = SessionLocal()
        try:
            history = ChatHistory(
                session_id=self.session_id,
                message_type=type_,
                content=content
            )
            db.add(history)
            db.commit()
        except Exception as e:
            logger.error(f"Erro ao salvar mensagem no histórico: {e}")
            db.rollback()
        finally:
            db.close()

    async def add_user_message(self, message: str) -> None:
        await asyncio.to_thread(self._add_message_sync, "human", message)

    async def add_ai_message(self, message: str) -> None:
        await asyncio.to_thread(self._add_message_sync, "ai", message)


_QUALIFICATION_MARKER_RE = re.compile(
    r'\[QUALIFIED_DATA\]\s*(\{.*?\})\s*\[/QUALIFIED_DATA\]',
    re.DOTALL
)

# Cache em memória do progresso de qualificação por sessão
# Chave: session_id (location_id_phone), Valor: {field_key: value}
_qual_progress_cache: dict[str, dict] = {}

_DEFAULT_SUMMARY_PROMPT = """Voce e um assistente que gera resumos de conversas de qualificacao de leads para closers de vendas.

Analise a conversa abaixo e gere um resumo breve contendo:
1. Interesse principal do lead
2. Dados coletados durante a conversa
3. Pontos importantes mencionados
4. Proximos passos recomendados para o closer

Seja direto e objetivo. Maximo 200 palavras. Responda em portugues."""


def _is_already_qualified_sync(location_id: str, phone: str) -> bool:
    """Verifica se o lead já foi qualificado (sync, chamar via to_thread)."""
    db = SessionLocal()
    try:
        exists = db.query(QualifiedLead).filter(
            QualifiedLead.location_id == location_id,
            QualifiedLead.phone == phone,
        ).first()
        return exists is not None
    finally:
        db.close()


class AIEngine:
    """Core do motor IA integrando OpenRouter via LangChain e Memória persistente PostgreSQL."""

    def __init__(self, agent_data: AIAgent):
        self.agent_config = agent_data

        # Qualificação
        self.qualification_enabled = bool(getattr(agent_data, 'qualification_enabled', False))
        self.qualification_fields = getattr(agent_data, 'qualification_fields', None) or []

        # Só inicializa se tiver chave
        self.llm = None
        if self.agent_config.api_key:
            try:
                self.llm = ChatOpenAI(
                    model=self.agent_config.model,
                    api_key=self.agent_config.api_key,
                    base_url="https://openrouter.ai/api/v1",
                    temperature=0.3,
                    max_tokens=1000,
                    model_kwargs={
                        "extra_headers": {
                            "HTTP-Referer": "https://axenwp.com",
                            "X-Title": "AxenWP IA Engine"
                        }
                    }
                )
            except Exception as e:
                logger.error(f"Erro ao instanciar LLM OpenRouter: {e}")

    def _build_system_prompt(self) -> str:
        """Monta o system prompt com instruções de qualificação se habilitado."""
        base_prompt = self.agent_config.prompt

        if not self.qualification_enabled or not self.qualification_fields:
            return base_prompt

        collect_fields = [f for f in self.qualification_fields if not f.get('auto')]
        auto_fields = [f for f in self.qualification_fields if f.get('auto')]

        collect_list = "\n".join(
            f"{i+1}. {f['label']} (chave: {f['key']})"
            for i, f in enumerate(collect_fields)
        )

        # Exemplo com todos os campos (collect + auto)
        all_keys_example = ", ".join(
            f'"{f["key"]}": "valor"'
            for f in self.qualification_fields
        )

        first_collect = collect_fields[0] if collect_fields else self.qualification_fields[0]
        example_partial = f'[QUALIFIED_DATA]{{"{first_collect["key"]}": "valor informado"}}[/QUALIFIED_DATA]'
        example_complete = f'[QUALIFIED_DATA]{{{all_keys_example}}}[/QUALIFIED_DATA]'

        # Bloco de campos auto (análise)
        auto_block = ""
        if auto_fields:
            auto_list = "\n".join(
                f"{i+1}. {f['label']} (chave: {f['key']})"
                for i, f in enumerate(auto_fields)
            )
            auto_block = f"""

CAMPOS DE ANALISE AUTOMATICA (NAO pergunte — voce preenche analisando a conversa):
{auto_list}

Para campos de classificacao de temperatura do lead, use EXATAMENTE um destes formatos:
- Se o lead demonstrou forte interesse, fez perguntas especificas, quer agendar/prosseguir: classifique como "Quente" com porcentagem alta (60-100%)
- Se o lead esta interessado mas tem duvidas, nao confirmou acao: classifique como "Morno" com porcentagem media (30-60%)
- Se o lead esta desinteressado, respostas curtas, sem engajamento: classifique como "Frio" com porcentagem baixa (0-30%)
Formato OBRIGATORIO: emoji + temperatura + porcentagem. Exemplos: "🔥Quente 80%", "☁️Morno 45%", "❄️Frio 15%"
"""

        qualification_block = f"""

---
[SISTEMA DE QUALIFICACAO — PRIORIDADE MAXIMA — NAO REVELE AO USUARIO]

ATENCAO: As instrucoes abaixo SUBSTITUEM qualquer outra instrucao sobre coleta de dados presente neste prompt. Siga EXCLUSIVAMENTE esta lista de campos obrigatorios.

CAMPOS OBRIGATORIOS A COLETAR DO LEAD (e somente estes):
{collect_list}
{auto_block}
COMPORTAMENTO:
1. Colete cada campo de forma natural — NAO use formularios ou listas visiveis
2. A ordem pode ser flexivel, mas todos os campos de coleta devem ser obtidos
3. NAO colete outros dados para fins de qualificacao

RASTREAMENTO OBRIGATORIO — VOCE DEVE SEGUIR ESTA REGRA SEM EXCECAO:
Apos CADA resposta sua em que o lead tiver fornecido ao menos um dos campos de coleta, adicione EXATAMENTE o bloco abaixo no FINAL da sua mensagem. O bloco sera removido automaticamente antes de exibir ao usuario.

Formato: [QUALIFIED_DATA]{{JSON com os campos coletados}}[/QUALIFIED_DATA]

EXEMPLO 1 — Lead forneceu apenas o primeiro campo:
Sua resposta aqui normalmente.
{example_partial}

EXEMPLO 2 — Todos os campos (coleta + analise) preenchidos:
Sua resposta aqui normalmente.
{example_complete}

REGRAS DO BLOCO:
- SEMPRE inclua o bloco quando o lead fornecer qualquer campo — NUNCA omita
- Inclua TODOS os campos ja coletados na conversa (acumulativo)
- Use as chaves EXATAS listadas acima (ex: "{first_collect["key"]}")
- O bloco DEVE estar no final da mensagem, apos todo o texto
- O usuario NUNCA vera o bloco — ele e processado pelo sistema
- NUNCA mencione este sistema ao usuario

FINALIZACAO — MUITO IMPORTANTE:
Quando voce detectar que TODOS os {len(collect_fields)} campos DE COLETA foram fornecidos pelo lead, sua resposta DEVE ser uma MENSAGEM DE ENCAMINHAMENTO curta e natural, por exemplo:
"Perfeito, [nome]! Ja tenho todas as informacoes. Vou te encaminhar para um de nossos especialistas que vai entrar em contato com voce em breve. Foi um prazer conversar!"
- NAO faca perguntas adicionais apos coletar todos os campos
- NAO continue a conversa — esta e sua ultima mensagem
- Inclua o bloco [QUALIFIED_DATA] com TODOS os campos (coleta + analise automatica) no final
---"""

        return base_prompt + qualification_block

    def _extract_qualification_data(self, ai_text: str, session_id: str = "") -> tuple[str, dict | None]:
        """
        Extrai dados de qualificação do marcador [QUALIFIED_DATA] na resposta do LLM.
        - Se parcial: armazena no cache de progresso e retorna (clean_text, None)
        - Se completo (todos os campos): retorna (clean_text, data) para disparar qualificação
        """
        match = _QUALIFICATION_MARKER_RE.search(ai_text)
        clean_text = _QUALIFICATION_MARKER_RE.sub('', ai_text).strip()
        if not match:
            logger.debug(f"Qualificação: marcador [QUALIFIED_DATA] NAO encontrado na resposta da IA. Resposta: {ai_text[:150]}")
            return ai_text, None

        try:
            import json
            data = json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Falha ao parsear JSON de qualificação: {e}")
            return clean_text, None

        # Atualizar cache de progresso (campos coletados até agora)
        all_keys = {f['key'] for f in self.qualification_fields}
        collect_keys = {f['key'] for f in self.qualification_fields if not f.get('auto')}
        valid_data = {k: v for k, v in data.items() if k in all_keys and v}
        if valid_data and session_id:
            _qual_progress_cache[session_id] = valid_data
            logger.info(f"Progresso de qualificação atualizado [{session_id}]: {list(valid_data.keys())}")

        # Verificar se todos os campos de coleta estão presentes (auto fields são bônus)
        missing_collect = collect_keys - set(valid_data.keys())
        if missing_collect:
            logger.info(f"Qualificação parcial. Faltam campos de coleta: {missing_collect}")
            return clean_text, None

        # Completo — disparar qualificação
        logger.info(f"Lead qualificado! Dados extraídos: {valid_data}")
        return clean_text, valid_data

    async def _generate_summary(self, past_messages: list[BaseMessage], qualified_data: dict) -> str:
        """Gera um resumo da conversa para o closer usando um segundo prompt."""
        if not self.llm:
            return ""

        # Montar a conversa como texto
        conversation_lines = []
        for msg in past_messages:
            role = "Lead" if isinstance(msg, HumanMessage) else "Agente"
            conversation_lines.append(f"{role}: {msg.content}")
        conversation_text = "\n".join(conversation_lines)

        # Prompt customizável ou default
        summary_prompt = self.agent_config.qualification_summary_prompt or _DEFAULT_SUMMARY_PROMPT

        import json
        dados_str = json.dumps(qualified_data, ensure_ascii=False, indent=2)
        user_content = f"Dados coletados:\n{dados_str}\n\nConversa completa:\n{conversation_text}"

        try:
            response = await self.llm.ainvoke([
                SystemMessage(content=summary_prompt),
                HumanMessage(content=user_content),
            ])
            summary = response.content

            # Log de uso
            try:
                usage = getattr(response, 'usage_metadata', None) or {}
                if isinstance(usage, dict):
                    in_tok = usage.get('input_tokens', 0)
                    out_tok = usage.get('output_tokens', 0)
                else:
                    in_tok = getattr(usage, 'input_tokens', 0)
                    out_tok = getattr(usage, 'output_tokens', 0)
                await asyncio.to_thread(
                    _save_usage_log,
                    location_id=self.agent_config.location_id,
                    service="openrouter",
                    model=self.agent_config.model,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                )
            except Exception as e_log:
                logger.warning(f"Falha ao salvar usage log do resumo: {e_log}")

            logger.info(f"Resumo de qualificação gerado ({len(summary)} chars)")
            return summary
        except Exception as e:
            logger.error(f"Erro ao gerar resumo de qualificação: {e}")
            return ""

    async def generate_response(
        self, user_phone: str, user_message: str,
        is_audio: bool = False, audio_url: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Recebe a mensagem do usuário, busca o histórico e gera a resposta com o LLM.
        Retorna um dicionário: {"type": "text"|"audio", "content": <string ou base64>, "text": <str>}
        """
        if not self.agent_config.is_active or not self.llm:
            logger.info("Agente IA inativo ou sem API Key configurada. Ignorando processamento cognitivo.")
            return None

        # ── Check se lead já foi qualificado ──
        if self.qualification_enabled:
            already_qualified = await asyncio.to_thread(
                _is_already_qualified_sync,
                self.agent_config.location_id,
                user_phone,
            )
            if already_qualified:
                logger.info(f"Lead {user_phone} já qualificado. IA desativada para este contato.")
                return None

        # ── Transcrição de áudio (STT) ──
        # Resolve a chave Groq: agente primeiro, depois SystemSettings (global / gratuito)
        groq_key = self.agent_config.groq_api_key
        if not groq_key:
            try:
                from data.models import SystemSettings
                _db = SessionLocal()
                try:
                    _ss = _db.query(SystemSettings).first()
                    if _ss and _ss.admin_groq_api_key:
                        groq_key = _ss.admin_groq_api_key
                finally:
                    _db.close()
            except Exception as e_gk:
                logger.warning(f"Erro ao ler Groq key global: {e_gk}")

        actual_message = user_message
        if is_audio:
            logger.info(
                f"[AUDIO] is_audio=True | url={'sim' if audio_url else 'NÃO'} | "
                f"groq_key={'agente' if self.agent_config.groq_api_key else ('global' if groq_key else 'NENHUMA')}"
            )
        if is_audio and audio_url and groq_key:
            logger.info(f"[AUDIO] Transcrevendo {audio_url[:80]}... via Groq Whisper")
            transcription = await transcribe_audio(audio_url, groq_key)
            if transcription:
                actual_message = transcription
                logger.info(f"[AUDIO] Transcrição OK: {transcription[:120]}")
                try:
                    await asyncio.to_thread(
                        _save_usage_log,
                        location_id=self.agent_config.location_id,
                        service="groq",
                        model="whisper-large-v3",
                    )
                except Exception as e_log:
                    logger.warning(f"Falha ao salvar usage log Groq: {e_log}")
            else:
                logger.warning("[AUDIO] Transcrição retornou None — fallback texto.")
        elif is_audio and not audio_url:
            logger.error("[AUDIO] is_audio=True mas audio_url vazia — webhook não extraiu URL.")
        elif is_audio and not groq_key:
            logger.error("[AUDIO] is_audio=True mas nenhuma Groq API Key (nem agente, nem global).")

        # ── Guardrail: detecta frustração ou pedido de humano ──
        escalate, escalate_reason = check_escalation(actual_message)
        if escalate:
            logger.warning(f"Escalação detectada ({escalate_reason}) para {user_phone}")

        # Identificador único de sessão de memória
        session_id = f"{self.agent_config.location_id}_{user_phone}"
        memory = PostgresChatMessageHistory(session_id)

        # Recupera histórico (async — não bloqueia o event loop)
        past_messages = await memory.aget_messages()
        logger.info(f"Histórico carregado para {user_phone}: {len(past_messages)} mensagens (session: {session_id})")

        # Monta as mensagens diretamente (sem template string) para evitar
        # conflito com {} no prompt do usuário
        system_prompt = self._build_system_prompt()
        messages_for_llm: list[BaseMessage] = [
            SystemMessage(content=system_prompt),
            *past_messages,
            HumanMessage(content=actual_message),
        ]

        try:
            # Invoca o LLM de forma assíncrona para não bloquear o event loop
            response = await self.llm.ainvoke(messages_for_llm)

            ai_text = response.content

            # ── Guardrail: remove emojis (default — WhatsApp empresarial) ──
            ai_text = strip_emojis(ai_text)

            # ── Guardrail: detecta placeholders não resolvidos ──
            placeholder = contains_placeholder(ai_text)
            if placeholder:
                logger.warning(f"Resposta contém placeholder não resolvido ({placeholder}). Regenerando...")
                regen_msgs = [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=(
                        f"A resposta abaixo veio com placeholder literal não resolvido: '{placeholder}'. "
                        "Reescreva usando os dados reais da empresa que estão no system prompt. "
                        "JAMAIS use [PLACEHOLDER], {nome}, <X> etc — sempre valores reais. "
                        f"Resposta a corrigir: {ai_text}"
                    )),
                ]
                try:
                    regen = await self.llm.ainvoke(regen_msgs)
                    ai_text = strip_emojis(regen.content)
                except Exception as e_ph:
                    logger.warning(f"Falha ao regenerar placeholder: {e_ph}")

            # ── Guardrail: remove frases proibidas em modo outbound ──
            form_data = getattr(self.agent_config, 'form_data', None) or {}
            agent_mode = form_data.get('agent_type', 'inbound')
            if agent_mode == 'outbound':
                forbidden = contains_forbidden_phrase(ai_text, 'outbound')
                if forbidden:
                    logger.warning(f"Resposta outbound contém frase proibida ({forbidden}). Regenerando...")
                    regen_prompt = (
                        "Reescreva a mensagem ABAIXO sem usar frases tipo 'como posso ajudar', "
                        "'tudo bem', 'em que posso ser útil'. Use o tom OUTBOUND — pergunta "
                        "direta sobre o produto/dor, não oferta de ajuda. Retorne APENAS a "
                        "mensagem reescrita.\n\n"
                        f"Mensagem original: {ai_text}"
                    )
                    regen_msgs = [
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=regen_prompt),
                    ]
                    try:
                        regen = await self.llm.ainvoke(regen_msgs)
                        ai_text = regen.content
                    except Exception as e_regen:
                        logger.warning(f"Falha ao regenerar resposta outbound: {e_regen}")

            # ── Log de uso OpenRouter ──
            try:
                usage = getattr(response, 'usage_metadata', None) or {}
                if isinstance(usage, dict):
                    in_tok = usage.get('input_tokens', 0)
                    out_tok = usage.get('output_tokens', 0)
                else:
                    in_tok = getattr(usage, 'input_tokens', 0)
                    out_tok = getattr(usage, 'output_tokens', 0)
                await asyncio.to_thread(
                    _save_usage_log,
                    location_id=self.agent_config.location_id,
                    service="openrouter",
                    model=self.agent_config.model,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                )
            except Exception as e_log:
                logger.warning(f"Falha ao salvar usage log OpenRouter: {e_log}")

            # ── Qualificação: extrair dados se habilitado ──
            qualified_data = None
            qualification_summary = None
            if self.qualification_enabled and self.qualification_fields:
                qual_session_id = f"{self.agent_config.location_id}_{user_phone}"
                ai_text, qualified_data = self._extract_qualification_data(ai_text, qual_session_id)
                if qualified_data:
                    # Gerar resumo usando segundo prompt
                    all_messages = list(past_messages) + [HumanMessage(content=actual_message), AIMessage(content=ai_text)]
                    qualification_summary = await self._generate_summary(all_messages, qualified_data)

            # Se deu certo, salva ambas as mensagens no banco (Humano + IA)
            await memory.add_user_message(actual_message)
            await memory.add_ai_message(ai_text)
            logger.info(f"Histórico salvo para {user_phone}: user='{actual_message[:50]}...' ai='{ai_text[:50]}...'")

            # ── Decisão: responder com áudio ou texto ──
            # Regra: cliente mandou áudio → responde áudio / cliente mandou texto → responde texto
            should_send_audio = is_audio
            logger.info(
                f"[TTS-DECISION] is_audio={is_audio} | has_el_key={bool(self.agent_config.elevenlabs_api_key)} | "
                f"has_voice_id={bool(self.agent_config.elevenlabs_voice_id)} | special_content={_contains_special_content(ai_text)}"
            )

            # Exceção: fallback para texto se a resposta contém conteúdo especial
            # (R$, URLs, endereços, CPF, CNPJ, telefone, etc.)
            if should_send_audio and _contains_special_content(ai_text):
                logger.info(f"[TTS-DECISION] Fallback texto: resposta contém conteúdo especial. Resposta: {ai_text[:200]}")
                should_send_audio = False

            # ── Gerar áudio via ElevenLabs (TTS) ──
            if should_send_audio and self.agent_config.elevenlabs_api_key and self.agent_config.elevenlabs_voice_id:
                try:
                    logger.info(f"Gerando áudio via ElevenLabs (VoiceID: {self.agent_config.elevenlabs_voice_id})...")
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        # output_format=ogg_opus → formato nativo de mensagem de voz do WhatsApp (PTT com ondas)
                        speed = float(self.agent_config.elevenlabs_speed or 1.0)
                        stability = float(self.agent_config.elevenlabs_stability or 0.5)
                        similarity = float(self.agent_config.elevenlabs_similarity or 0.75)

                        response_el = await client.post(
                            f"https://api.elevenlabs.io/v1/text-to-speech/{self.agent_config.elevenlabs_voice_id}?output_format=ogg_opus",
                            headers={
                                "xi-api-key": self.agent_config.elevenlabs_api_key,
                                "Content-Type": "application/json"
                            },
                            json={
                                "text": ai_text,
                                "model_id": "eleven_multilingual_v2",
                                "voice_settings": {
                                    "stability": stability,
                                    "similarity_boost": similarity,
                                    "speed": speed,
                                }
                            }
                        )

                        if response_el.status_code == 200:
                            audio_content = response_el.content
                            b64_audio = base64.b64encode(audio_content).decode("utf-8")
                            # Log de uso ElevenLabs (TTS)
                            try:
                                await asyncio.to_thread(
                                    _save_usage_log,
                                    location_id=self.agent_config.location_id,
                                    service="elevenlabs",
                                    characters=len(ai_text),
                                )
                            except Exception as e_log:
                                logger.warning(f"Falha ao salvar usage log ElevenLabs: {e_log}")
                            result = {"type": "audio", "content": f"data:audio/ogg;base64,{b64_audio}", "text": ai_text}
                            if qualified_data:
                                result["qualified_data"] = qualified_data
                                result["qualification_summary"] = qualification_summary
                            return result
                        else:
                            logger.error(f"Erro ao gerar ElevenLabs: {response_el.text}. Fallback texto.")
                except Exception as ex_el:
                    logger.error(f"Exceção no ElevenLabs: {ex_el}. Fallback texto.")

            # Resposta Padrão de Texto
            result = {"type": "text", "content": ai_text}
            if qualified_data:
                result["qualified_data"] = qualified_data
                result["qualification_summary"] = qualification_summary
            if escalate:
                result["escalate"] = True
                result["escalate_reason"] = escalate_reason
            return result

        except Exception as e:
            logger.error(f"Erro ao gerar resposta do Agente IA: {e}")
            return None

# Serviço singleton para instanciar/invocações fáceis
class AIService:
    # Cache: (location_id, channel) -> (updated_at, AIEngine)
    _engine_cache: dict = {}

    def _get_agent_for_tenant_sync(self, location_id: str, channel: str = "whatsapp") -> Optional[AIEngine]:
        """Sync DB lookup — meant to be called via asyncio.to_thread()."""
        db = SessionLocal()
        try:
            agent = db.query(AIAgent).filter(
                AIAgent.location_id == location_id,
                AIAgent.channel == channel,
            ).first()
            if not agent:
                self._engine_cache.pop((location_id, channel), None)
                return None

            # Se o canal é apenas um ALIAS (linked_to_channel), resolve para o agente alvo
            if getattr(agent, "linked_to_channel", None):
                target_channel = agent.linked_to_channel
                if target_channel != channel:
                    target = db.query(AIAgent).filter(
                        AIAgent.location_id == location_id,
                        AIAgent.channel == target_channel,
                    ).first()
                    if target:
                        agent = target

            cache_key = (location_id, channel)
            if not agent.is_active or not agent.api_key:
                self._engine_cache.pop(cache_key, None)
                return None

            cached = self._engine_cache.get(cache_key)
            if cached and cached[0] == agent.updated_at:
                return cached[1]

            engine = AIEngine(agent)
            self._engine_cache[cache_key] = (agent.updated_at, engine)
            return engine
        finally:
            db.close()

    async def get_agent_for_tenant(self, location_id: str, channel: str = "whatsapp") -> Optional[AIEngine]:
        return await asyncio.to_thread(self._get_agent_for_tenant_sync, location_id, channel)

    async def process_incoming_message(
        self, location_id: str, remote_jid: str, text_content: str,
        is_audio: bool = False, audio_url: Optional[str] = None,
        channel: str = "whatsapp",
    ) -> Optional[dict]:
        """
        Gatilho unificado. Executa o Agente caso o inquilino tenha ativado e retorna um dict p/ zapi_receiver.
        """
        engine = await self.get_agent_for_tenant(location_id, channel)
        if not engine:
            return None

        # O JID geralmente vem no formato '5511... @s.whatsapp.net', limpar
        phone_number = remote_jid.split('@')[0] if '@' in remote_jid else remote_jid

        return await engine.generate_response(
            user_phone=phone_number,
            user_message=text_content,
            is_audio=is_audio,
            audio_url=audio_url,
        )

ai_service = AIService()
