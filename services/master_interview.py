"""
Entrevista da IA Mestre — a porta principal de criação de agente.

A ideia central, que evita duplicar o gerador: a entrevista NÃO inventa um
caminho paralelo de geração. Ela preenche conversacionalmente os MESMOS campos
que o formulário público preenche, e no fim entrega esse `form_data` para o
`master_engine.generate_agent_spec` que já existe. Duas portas, um gerador só —
sem duas lógicas para manter em sincronia.

    entrevista ─┐
                ├─► form_data ─► generate_agent_spec ─► AgentSpec ─► provisiona
    formulário ─┘

O loop é tool-use de verdade (`model → tool_use → tool_result → model`), no mesmo
padrão de `services/agent_engine/claude_engine.py`: a Mestre conduz a conversa e,
quando julga ter o suficiente, chama `concluir_entrevista` com os campos
preenchidos. Quem decide que acabou é ela, não um contador de perguntas.

Custo: o prefixo (system + tools) é ESTÁVEL e marcado com `cache_control`, então
a partir do 2º turno ele é lido do cache. Nada de timestamp/uuid no prefixo —
qualquer variação invalida o cache silenciosamente e o custo triplica.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from services.master_engine import _read_settings, _resolve_master_key
from utils.logger import logger

DEFAULT_MODEL = "claude-sonnet-5"
_MAX_TOKENS = 1500

# Tetos de segurança. O link público é ANÔNIMO e gasta a NOSSA chave: sem teto,
# uma aba esquecida (ou um curioso) vira conta aberta. Os números são folgados
# para uma entrevista real (~12-18 turnos) e apertados para abuso.
MAX_TURNOS = 40
MAX_TOKENS_SAIDA = 60_000


# Os campos são exatamente os que `public/onboarding.py` coleta — é isso que faz
# as duas portas convergirem. Mudou lá, muda aqui.
CAMPOS = {
    "company_name": "Nome da empresa",
    "industry": "Setor/ramo de atuação",
    "company_description": "O que a empresa faz, em poucas linhas",
    "target_audience": "Quem é o cliente ideal",
    "website": "Site (se tiver)",
    "instagram": "Instagram (se tiver)",
    "products_services": "Produtos ou serviços que vende",
    "differentials": "O que diferencia da concorrência",
    "faq": "Perguntas que os clientes mais fazem, com as respostas",
    "tone": "Tom de voz desejado no atendimento",
    "business_hours": "Horário de atendimento",
    "contact_info": "Telefone/e-mail de contato humano",
    "agent_goal": "O que o agente precisa conseguir (qualificar, agendar, tirar dúvida, vender)",
    "extra_info": "Qualquer coisa que o agente precisa saber e não coube acima",
}

# Campos sem os quais o agente sai genérico. A Mestre é instruída a não concluir
# sem eles — mas a validação também mora no código (o modelo é empírico).
OBRIGATORIOS = ("company_name", "products_services", "agent_goal")


_SYSTEM = """Você é a IA Mestre do MilloChat. Seu trabalho nesta conversa é ENTREVISTAR o dono de um negócio para, ao final, construir o agente de atendimento de WhatsApp dele.

COMO CONDUZIR

Faça UMA pergunta por vez. Nunca despeje uma lista de perguntas — isso é uma conversa, não um formulário disfarçado.

Comece se apresentando em uma frase e perguntando o que a empresa faz. A partir daí, deixe as respostas guiarem a ordem. Se a pessoa já respondeu algo de passagem, NÃO pergunte de novo — aproveite e siga.

Adapte a profundidade ao negócio. Uma pizzaria não precisa da mesma entrevista que uma consultoria jurídica. Se a resposta veio rasa e o dado importa, pergunte de novo de outro jeito, uma vez só; se continuar rasa, siga em frente e trabalhe com o que tem.

Use linguagem de gente. Nada de jargão de marketing ("qual sua proposta de valor?"). Pergunte como um consultor experiente perguntaria num café: "o que faz o cliente escolher vocês e não o concorrente da esquina?".

Se a pessoa não souber responder, ofereça exemplos plausíveis para ela reagir. Reagir é mais fácil que criar do zero.

O QUE VOCÊ PRECISA DESCOBRIR

Estes são os campos que você preenche. Não os leia em voz alta — descubra conversando:

{campos}

Os campos {obrigatorios} são indispensáveis: sem eles o agente sai genérico e o cliente vai odiar a primeira versão.

QUANDO CONCLUIR

Assim que tiver o essencial — tipicamente entre 8 e 15 perguntas — chame a ferramenta `concluir_entrevista` com tudo o que apurou. Não estenda a conversa para "caprichar": um agente bom sai de informação certa, não de entrevista longa. A pessoa vai poder revisar e editar tudo depois.

Antes de concluir, faça um resumo curto do que entendeu e pergunte se ficou faltando algo. Se a pessoa disser que está bom, conclua.

REGRAS

Escreva em português do Brasil, em texto corrido. Nada de bullets ou listas numeradas nas suas mensagens.

Mensagens curtas: duas ou três frases. Isto vai ser lido numa caixa de chat.

Não prometa prazo, preço ou resultado em nome da empresa — você está coletando, não vendendo.

Se a pessoa pedir para pular ou disser que não sabe, aceite e siga. Insistir irrita e não melhora o agente."""


_TOOL_CONCLUIR = {
    "name": "concluir_entrevista",
    "description": (
        "Encerra a entrevista e entrega os dados apurados para a construção do agente. "
        "Chame quando tiver o essencial — não é preciso preencher todos os campos, "
        "mas os obrigatórios sim. Depois disso a conversa acaba."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            campo: {"type": "string", "description": desc} for campo, desc in CAMPOS.items()
        },
        "required": list(OBRIGATORIOS),
    },
}


@dataclass
class EstadoEntrevista:
    """O que atravessa os turnos. Serializável — vive no banco entre requests."""

    mensagens: list[dict] = field(default_factory=list)
    turnos: int = 0
    tokens_entrada: int = 0
    tokens_saida: int = 0
    tokens_cache_read: int = 0
    concluida: bool = False
    form_data: Optional[dict] = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "mensagens": self.mensagens,
                "turnos": self.turnos,
                "tokens_entrada": self.tokens_entrada,
                "tokens_saida": self.tokens_saida,
                "tokens_cache_read": self.tokens_cache_read,
                "concluida": self.concluida,
                "form_data": self.form_data,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, bruto: Optional[str]) -> "EstadoEntrevista":
        if not bruto:
            return cls()
        try:
            d = json.loads(bruto)
        except Exception:
            logger.warning("[ENTREVISTA] Estado ilegível; recomeçando do zero.")
            return cls()
        return cls(
            mensagens=d.get("mensagens") or [],
            turnos=int(d.get("turnos") or 0),
            tokens_entrada=int(d.get("tokens_entrada") or 0),
            tokens_saida=int(d.get("tokens_saida") or 0),
            tokens_cache_read=int(d.get("tokens_cache_read") or 0),
            concluida=bool(d.get("concluida")),
            form_data=d.get("form_data"),
        )

    @property
    def estourou_teto(self) -> bool:
        return self.turnos >= MAX_TURNOS or self.tokens_saida >= MAX_TOKENS_SAIDA


class EntrevistaIndisponivel(RuntimeError):
    """Mestre não configurada, ou teto atingido. O chamador decide o que mostrar."""


def _system_blocks() -> list[dict]:
    """
    Prefixo estável, marcado para cache. Ele não muda entre turnos NEM entre
    entrevistas — é o que faz o cache valer a partir da segunda chamada.
    """
    texto = _SYSTEM.format(
        campos="\n".join(f"- {c}: {d}" for c, d in CAMPOS.items()),
        obrigatorios=", ".join(OBRIGATORIOS),
    )
    return [{"type": "text", "text": texto, "cache_control": {"type": "ephemeral"}}]


def _modelo() -> str:
    return (
        (os.getenv("MASTER_ANTHROPIC_MODEL") or "").strip()
        or (_read_settings()[1] or "").strip()
        or DEFAULT_MODEL
    )


def _limpar_form_data(bruto: dict) -> dict:
    """Fica só com os campos que conhecemos, como string limpa."""
    saida = {}
    for campo in CAMPOS:
        valor = bruto.get(campo)
        if valor is None:
            continue
        texto = str(valor).strip()
        if texto:
            saida[campo] = texto
    return saida


async def avancar(estado: EstadoEntrevista, mensagem_do_usuario: Optional[str]) -> EstadoEntrevista:
    """
    Um turno da entrevista.

    `mensagem_do_usuario=None` inicia a conversa (a Mestre fala primeiro).
    Devolve o estado atualizado; se `concluida`, `form_data` está preenchido.
    """
    if estado.concluida:
        return estado
    if estado.estourou_teto:
        raise EntrevistaIndisponivel(
            "Esta entrevista atingiu o limite. Recomece ou preencha o formulário."
        )

    chave = _resolve_master_key()
    if not chave:
        raise EntrevistaIndisponivel(
            "IA Mestre não configurada — falta a chave Anthropic em Config. Admin."
        )

    from anthropic import AsyncAnthropic

    if mensagem_do_usuario is not None:
        estado.mensagens.append({"role": "user", "content": mensagem_do_usuario})
    elif not estado.mensagens:
        # A Mestre precisa de um turno de usuário para começar; este é o gatilho e
        # não aparece na tela.
        estado.mensagens.append({"role": "user", "content": "Vamos começar."})

    cliente = AsyncAnthropic(api_key=chave, timeout=120.0, max_retries=2)
    modelo = _modelo()

    while True:
        resp = await cliente.messages.create(
            model=modelo,
            max_tokens=_MAX_TOKENS,
            system=_system_blocks(),
            tools=[_TOOL_CONCLUIR],
            messages=estado.mensagens,
        )

        uso = getattr(resp, "usage", None)
        if uso:
            estado.tokens_entrada += getattr(uso, "input_tokens", 0) or 0
            estado.tokens_saida += getattr(uso, "output_tokens", 0) or 0
            estado.tokens_cache_read += getattr(uso, "cache_read_input_tokens", 0) or 0

        # O turno do assistant entra INTEIRO (com o tool_use), e o tool_result vem
        # encostado logo depois — é o invariante que evita `tool_use` órfão virar
        # 400 em cascata quando o estado é recarregado do banco.
        estado.mensagens.append(
            {"role": "assistant", "content": [b.model_dump() for b in resp.content]}
        )

        if resp.stop_reason != "tool_use":
            estado.turnos += 1
            logger.info(
                f"[ENTREVISTA] turno {estado.turnos} | in={estado.tokens_entrada} "
                f"out={estado.tokens_saida} cache_read={estado.tokens_cache_read}"
            )
            return estado

        resultados = []
        for bloco in resp.content:
            if getattr(bloco, "type", None) != "tool_use":
                continue
            if bloco.name == "concluir_entrevista":
                dados = _limpar_form_data(dict(bloco.input or {}))
                faltando = [c for c in OBRIGATORIOS if not dados.get(c)]
                if faltando:
                    # Guard de código: o modelo é empírico. Em vez de aceitar um
                    # spec capenga, devolvemos o problema e ele continua perguntando.
                    resultados.append({
                        "type": "tool_result",
                        "tool_use_id": bloco.id,
                        "content": (
                            "Ainda faltam campos obrigatórios: "
                            f"{', '.join(faltando)}. Pergunte sobre eles antes de concluir."
                        ),
                        "is_error": True,
                    })
                    continue
                estado.form_data = dados
                estado.concluida = True
                estado.turnos += 1
                logger.info(
                    f"[ENTREVISTA] concluída em {estado.turnos} turnos | "
                    f"campos={len(dados)} | out={estado.tokens_saida} tokens"
                )
                return estado
            resultados.append({
                "type": "tool_result", "tool_use_id": bloco.id,
                "content": f"Ferramenta desconhecida: {bloco.name}", "is_error": True,
            })

        estado.mensagens.append({"role": "user", "content": resultados})
        estado.turnos += 1
        if estado.estourou_teto:
            raise EntrevistaIndisponivel("Limite de turnos atingido.")


def ultima_fala(estado: EstadoEntrevista) -> str:
    """O texto da última mensagem da Mestre, para exibir na tela."""
    for msg in reversed(estado.mensagens):
        if msg.get("role") != "assistant":
            continue
        conteudo = msg.get("content")
        if isinstance(conteudo, str):
            return conteudo
        partes = [
            b.get("text", "") for b in (conteudo or [])
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        texto = "\n".join(p for p in partes if p).strip()
        if texto:
            return texto
    return ""


def historico_visivel(estado: EstadoEntrevista) -> list[dict]:
    """
    A conversa como a tela mostra: só texto de gente e da Mestre.

    Filtra o gatilho inicial, os blocos de tool_use e os tool_result — ruído de
    protocolo que não é conversa.
    """
    saida: list[dict] = []
    for i, msg in enumerate(estado.mensagens):
        papel = msg.get("role")
        conteudo = msg.get("content")

        if papel == "user":
            if isinstance(conteudo, str):
                if i == 0 and conteudo == "Vamos começar.":
                    continue
                saida.append({"de": "usuario", "texto": conteudo})
            continue  # lista de user = tool_result, não é fala

        if isinstance(conteudo, str):
            texto = conteudo
        else:
            texto = "\n".join(
                b.get("text", "") for b in (conteudo or [])
                if isinstance(b, dict) and b.get("type") == "text"
            ).strip()
        if texto:
            saida.append({"de": "mestre", "texto": texto})
    return saida
