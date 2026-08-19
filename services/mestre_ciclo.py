"""
O ciclo fechado: pedido do operador → mudança → TESTE → evidência.

O que o Luiz descreveu (2026-08-18) e que não existia:

    "eu mando mensagem pro Mestre dizendo que o agente precisa começar dando bom
    dia, boa tarde ou boa noite; ele entende o que mudar no prompt, ajusta,
    aplica, TESTA pra ver se deu certo e me traz um retorno."

A peça que faltava não era a mudança — `improve-prompt` já mexia no prompt. Era a
VERIFICAÇÃO. Antes, "Aplicar melhoria" reescrevia 6,6 mil caracteres de produção e
o operador clicava e torcia.

Duas ideias sustentam este módulo:

1. **O pedido do operador contém o próprio teste.** "Começar com bom dia" é
   verificável por regex. Então a Mestre não devolve só o prompt novo: devolve
   também o CASO que prova que o pedido foi atendido. Sem isso, "deu certo" é
   opinião dela sobre o próprio trabalho.

2. **Nada é publicado antes da evidência.** O prompt novo é um CANDIDATO. Rodamos
   a sonda nos dois — atual e candidato — contra o caso novo E contra o roteiro de
   regressão, e devolvemos o comparativo. Publicar é decisão do operador, com o
   número na frente.

O roteiro de regressão importa tanto quanto o caso novo: uma mudança que faz o
agente cumprimentar e passa a escalar tudo não é uma melhoria.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from services.usage_logger import registrar_gasto_mestre
from utils.logger import logger

# Teto de casos por rodada. O operador pede uma coisa; se a Mestre inventar dez
# testes, a rodada fica cara e o sinal se dilui.
MAX_CASOS_NOVOS = 2

# Quantas amostras confirmam um caso que MUDOU de veredito. Ímpar de propósito:
# maioria simples sem empate. 3 é o menor número que distingue sinal de ruído sem
# triplicar a conta — só os casos que discordaram são reamostrados.
CONFIRMACOES = 3


_SYSTEM = """Você é a IA Mestre do MilloChat. Um operador vai te dar UM pedido sobre o comportamento do agente de atendimento dele, e você faz duas coisas.

PRIMEIRO: ajusta o prompt do agente.

Mudança CIRÚRGICA. Você recebe o prompt inteiro e devolve o prompt inteiro, mas alterando só o que o pedido exige. Não reescreva seções que o pedido não tocou, não "melhore de passagem", não mude o tom, os exemplos, nem a estrutura. O operador vai comparar as duas versões; toda diferença que ele não pediu é ruído que ele terá que revisar.

Se o pedido já estiver atendido pelo prompt atual, devolva o prompt inalterado e diga isso no resumo. É uma resposta legítima e melhor que uma mudança inventada.

SEGUNDO: escreve o teste que prova que o pedido foi atendido.

Um caso é: uma fala de lead, e o que a resposta do agente precisa ter (ou não ter). O teste roda contra o agente de verdade.

Use `espera_texto` com uma expressão regular quando o pedido for sobre o QUE o agente diz. Ex: pedido "cumprimente conforme o horário" → fala do lead "oi", `espera_texto` = "bom dia|boa tarde|boa noite".

Use `nao_espera_texto` quando o pedido for para PARAR de dizer algo.

Use `espera_tool`/`nao_espera_tool` (valores: `escalate_to_human`, `register_qualified_lead`) quando o pedido for sobre AÇÃO — transferir, registrar lead.

A regex precisa ser tolerante: o agente escreve livre, então case-insensitive e sem exigir pontuação. Prefira alternativas amplas a uma frase exata.

REGRAS

Escreva em português do Brasil.

O agente roda com FERRAMENTAS. Transferir para humano (`escalate_to_human`) PAUSA a conversa — nunca instrua o agente a transferir quando o lead está pronto para fechar.

No máximo {max_casos} casos. Um pedido, um teste, é o normal.

O resumo é para o operador ler em 5 segundos: o que você mudou e onde."""


def _schema_resposta() -> dict:
    """Contrato da Mestre. Tool forçada = saída estruturada sem parser frágil."""
    return {
        "name": "entregar_ajuste",
        "description": "Entrega o prompt ajustado e o teste que prova o pedido atendido.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt_novo": {
                    "type": "string",
                    "description": "O prompt COMPLETO já ajustado. Inalterado se o pedido já estiver atendido.",
                },
                "resumo": {
                    "type": "string",
                    "description": "O que mudou e onde, em uma ou duas frases, para o operador.",
                },
                "ja_atendido": {
                    "type": "boolean",
                    "description": "true se o prompt atual já cumpria o pedido e nada foi alterado.",
                },
                "casos": {
                    "type": "array",
                    "description": "Testes que provam o pedido atendido.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "porque": {"type": "string", "description": "Que pedido este caso verifica."},
                            "lead": {"type": "string", "description": "A fala do lead."},
                            "espera_texto": {"type": "string"},
                            "nao_espera_texto": {"type": "string"},
                            "espera_tool": {"type": "string"},
                            "nao_espera_tool": {"type": "string"},
                        },
                        "required": ["id", "porque", "lead"],
                    },
                },
            },
            "required": ["prompt_novo", "resumo", "ja_atendido", "casos"],
        },
    }


class CicloIndisponivel(RuntimeError):
    """Mestre não configurada, ou pedido impossível de verificar."""


def _regex_valida(padrao: Optional[str]) -> Optional[str]:
    """
    Regex que não compila derrubaria a sonda no meio da rodada — e o operador leria
    isso como "a mudança falhou", que é outra coisa.
    """
    if not padrao:
        return None
    try:
        re.compile(padrao)
        return padrao
    except re.error as e:
        logger.warning(f"[CICLO] Mestre devolveu regex inválida {padrao!r}: {e}")
        return None


def _limpar_casos(brutos: list) -> list[dict]:
    casos = []
    for c in (brutos or [])[:MAX_CASOS_NOVOS]:
        if not isinstance(c, dict) or not (c.get("lead") or "").strip():
            continue
        caso = {
            "id": (c.get("id") or f"pedido_{len(casos) + 1}").strip()[:60],
            "porque": (c.get("porque") or "Pedido do operador.").strip(),
            "lead": c["lead"].strip(),
            "origem": "pedido_do_operador",
        }
        for chave in ("espera_texto", "nao_espera_texto"):
            v = _regex_valida(c.get(chave))
            if v:
                caso[chave] = v
        for chave in ("espera_tool", "nao_espera_tool"):
            v = (c.get(chave) or "").strip()
            if v in ("escalate_to_human", "register_qualified_lead"):
                caso[chave] = v
        # Caso sem critério não mede nada; guardá-lo daria falso conforto.
        if not any(k in caso for k in
                   ("espera_texto", "nao_espera_texto", "espera_tool", "nao_espera_tool")):
            logger.warning(f"[CICLO] caso {caso['id']} descartado: sem critério verificável")
            continue
        casos.append(caso)
    return casos


async def propor_ajuste(agente, pedido: str) -> dict:
    """
    Pergunta à Mestre O QUE mudar e COMO provar. Não altera nada.

    Devolve {"prompt_novo", "resumo", "ja_atendido", "casos"}.
    """
    from services.master_engine import _resolve_master_key, _read_settings

    chave = _resolve_master_key()
    if not chave:
        raise CicloIndisponivel(
            "IA Mestre não configurada — falta a chave Anthropic em Config. Admin."
        )

    from anthropic import AsyncAnthropic

    from services.agent_engine.tools import build_tool_specs

    tools_do_agente = [t.name for t in build_tool_specs(agente)]
    contexto = {
        "nome_do_agente": agente.name,
        "canal": agente.channel,
        # O que o agente PODE fazer muda o que faz sentido pedir. Sem isto a Mestre
        # otimiza prosa para um sistema cujo eixo é decisão de ferramenta.
        "ferramentas_disponiveis": tools_do_agente,
        "qualificacao_ligada": bool(agente.qualification_enabled),
        "campos_de_coleta": [
            f.get("label") for f in (agente.qualification_fields or [])
            if f.get("key") and not f.get("auto")
        ],
    }

    corpo = (
        f"CONTEXTO DO AGENTE (não é o prompt, é o que ele pode fazer):\n"
        f"{json.dumps(contexto, ensure_ascii=False, indent=1)}\n\n"
        f"PEDIDO DO OPERADOR:\n{pedido.strip()}\n\n"
        f"PROMPT ATUAL DO AGENTE:\n---\n{agente.prompt or ''}\n---"
    )

    cliente = AsyncAnthropic(api_key=chave, timeout=180.0, max_retries=2)
    modelo = (_read_settings()[1] or "").strip() or "claude-sonnet-5"
    ferramenta = _schema_resposta()

    resp = await cliente.messages.create(
        model=modelo,
        max_tokens=16000,
        system=_SYSTEM.format(max_casos=MAX_CASOS_NOVOS),
        tools=[ferramenta],
        tool_choice={"type": "tool", "name": ferramenta["name"]},
        messages=[{"role": "user", "content": corpo}],
    )

    await registrar_gasto_mestre(agente.location_id, modelo, getattr(resp, "usage", None))

    bloco = next((b for b in resp.content if getattr(b, "type", None) == "tool_use"), None)
    if bloco is None:
        raise CicloIndisponivel("A Mestre não devolveu um ajuste estruturado. Tente de novo.")

    dados = dict(bloco.input or {})
    novo = (dados.get("prompt_novo") or "").strip()
    if not novo:
        raise CicloIndisponivel("A Mestre devolveu um prompt vazio; nada foi alterado.")

    casos = _limpar_casos(dados.get("casos") or [])
    if not casos:
        # Sem caso verificável não há ciclo: seria de novo "aplicar e torcer".
        raise CicloIndisponivel(
            "Não consegui transformar esse pedido num teste verificável. "
            "Tente descrever o que o agente deve DIZER ou FAZER numa situação concreta."
        )

    return {
        "prompt_novo": novo,
        "resumo": (dados.get("resumo") or "").strip(),
        "ja_atendido": bool(dados.get("ja_atendido")),
        "casos": casos,
    }


async def verificar(agente, prompt_novo: str, casos_novos: list[dict],
                    incluir_regressao: bool = True) -> dict:
    """
    Roda o ATUAL e o CANDIDATO contra os mesmos casos, e devolve o comparativo.

    Os dois lados rodam com o MESMO roteiro porque a pergunta não é "o candidato
    passa?", é "o candidato é melhor?". Um caso que já passava antes não é mérito
    da mudança; um que passava e parou é o dano que ninguém veria.

    `incluir_regressao` traz o roteiro fixo junto: uma mudança que faz o agente
    cumprimentar e passa a escalar tudo não é melhoria.
    """
    from services.sonda import ROTEIRO_PADRAO, carregar_roteiros, rodar_caso

    casos = list(casos_novos)
    if incluir_regressao:
        try:
            casos += [dict(c, origem="regressao") for c in carregar_roteiros(ROTEIRO_PADRAO)]
        except Exception as e:
            logger.warning(f"[CICLO] roteiro de regressão indisponível: {e}")

    async def rodada(prompt: Optional[str]) -> dict:
        saida = {}
        for caso in casos:
            try:
                r = await rodar_caso(agente, caso, prompt)
            except Exception as e:
                logger.warning(f"[CICLO] caso {caso['id']} falhou: {type(e).__name__}: {e}")
                r = {"id": caso["id"], "ok": False, "criterio": "erro",
                     "chamou": type(e).__name__, "texto": str(e)[:120]}
            saida[caso["id"]] = r
        return saida

    antes = await rodada(None)             # None = o prompt que está no agente
    depois = await rodada(prompt_novo)

    # ── Confirmação por amostra: o que MUDOU precisa se repetir ──
    #
    # Uma amostra por caso não distingue "a mudança causou isso" de "o modelo
    # variou". Medido em 2026-08-19: `nao_inventa_dado` acerta ~1 em 6 no MESMO
    # prompt, e num ciclo real ele apareceu como `QUEBROU` — regressão fantasma,
    # veredito `revisar`, num candidato que não havia quebrado nada.
    #
    # Um alarme falso é pior que nenhum alarme: o operador aprende a ignorar.
    #
    # Só os casos que DISCORDARAM entre antes e depois são reamostrados — o que
    # ficou igual dos dois lados não tem o que confirmar. Custa poucas chamadas.
    mudou = [c for c in casos
             if bool(antes.get(c["id"], {}).get("ok")) != bool(depois.get(c["id"], {}).get("ok"))]
    amostras: dict[str, dict] = {}
    if mudou:
        logger.info(f"[CICLO] {len(mudou)} caso(s) mudaram; confirmando com "
                    f"{CONFIRMACOES} amostras cada.")
        for caso in mudou:
            cid = caso["id"]
            votos_a = [bool(antes[cid].get("ok"))]
            votos_d = [bool(depois[cid].get("ok"))]
            for _ in range(CONFIRMACOES - 1):
                for prompt, votos in ((None, votos_a), (prompt_novo, votos_d)):
                    try:
                        votos.append(bool((await rodar_caso(agente, caso, prompt))["ok"]))
                    except Exception as e:
                        logger.warning(f"[CICLO] reamostra de {cid} falhou: {e}")
                        votos.append(False)
            amostras[cid] = {"antes": votos_a, "depois": votos_d}
            # Maioria simples: o lado vence se acertar na maior parte das amostras.
            antes[cid]["ok"] = sum(votos_a) * 2 > len(votos_a)
            depois[cid]["ok"] = sum(votos_d) * 2 > len(votos_d)

    linhas = []
    for caso in casos:
        cid = caso["id"]
        a, d = antes.get(cid, {}), depois.get(cid, {})
        ok_a, ok_d = bool(a.get("ok")), bool(d.get("ok"))
        if ok_d and not ok_a:
            veredito = "corrigiu"
        elif ok_a and not ok_d:
            veredito = "QUEBROU"
        elif ok_d:
            veredito = "seguia ok"
        else:
            veredito = "segue falhando"
        amostra = amostras.get(cid)
        linhas.append({
            # Quantas amostras sustentam este veredito, e como cada lado votou.
            # Sem isso, "QUEBROU" de 1 amostra e "QUEBROU" de 3 têm o mesmo peso na
            # tela — e não têm o mesmo peso na realidade.
            "amostras": (
                f"{sum(amostra['depois'])}/{len(amostra['depois'])} depois, "
                f"{sum(amostra['antes'])}/{len(amostra['antes'])} antes"
            ) if amostra else "1/1",
            "id": cid,
            "origem": caso.get("origem", "regressao"),
            "porque": caso.get("porque", ""),
            "criterio": d.get("criterio") or a.get("criterio", ""),
            # QUAL critério reprovou. Com vários por caso, "FALHA" sozinho manda o
            # operador abrir o prompt para adivinhar o que deu errado.
            "falhou_em": d.get("falhou_em") or [],
            "antes_ok": ok_a,
            "depois_ok": ok_d,
            "veredito": veredito,
            # Texto INTEIRO, não os 90 chars da linha de CLI: foi lendo a resposta
            # completa que se descobriu o agente devolvendo a pergunta ao lead.
            # Truncado em 600 só para não estourar o payload da tela.
            "resposta_depois": (d.get("texto_completo") or d.get("texto", ""))[:600],
        })

    novos = [l for l in linhas if l["origem"] == "pedido_do_operador"]
    quebrou = [l for l in linhas if l["veredito"] == "QUEBROU"]
    pedido_atendido = bool(novos) and all(l["depois_ok"] for l in novos)

    return {
        "linhas": linhas,
        "pedido_atendido": pedido_atendido,
        "quebrou": [l["id"] for l in quebrou],
        # A recomendação é explícita para o operador não ter que interpretar tabela:
        # atender o pedido quebrando outra coisa NÃO é sucesso.
        "recomendacao": (
            "publicar" if pedido_atendido and not quebrou
            else "descartar" if not pedido_atendido
            else "revisar"
        ),
        "resumo": (
            f"{sum(1 for l in novos if l['depois_ok'])}/{len(novos)} do seu pedido"
            + (f" · {len(quebrou)} regressão(ões)" if quebrou else " · nada quebrou")
        ),
    }
