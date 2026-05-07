"""
Prompt unificado da IA Mestre para geração de prompts de agentes (v2).

Decisões-chave da reescrita:
- Calibração automática do REGISTRO DE LINGUAGEM por perfil da empresa
  (B2B premium / B2C casual / suporte técnico). Antes era one-size-fits-all
  e gerava agentes premium falando "bacana", "saquei".
- Estrutura ADAPTATIVA ao agent_type. Suporte não recebe seção de
  "tratamento de objeções comerciais" forçada.
- Output target reduzido (300-700 palavras). Prompts longos diluem regras.
- Múltiplas variantes de abertura para evitar repetição em A/B.
- Few-shot examples 3x maior (good vs bad) para cada categoria.
- Uma única seção canônica de estilo, sem duplicações.
"""

from typing import Optional


# ─────────────────────────────────────────────────────────────────────
# Detecção do registro de linguagem
# ─────────────────────────────────────────────────────────────────────

# Marcadores que sugerem perfil B2B premium / executivo
_PREMIUM_KEYWORDS = [
    "consultoria", "advocacia", "imigração", "imigracao", "investimento",
    "private", "wealth", "trader", "imóveis de luxo", "luxo", "premium",
    "corporate", "corporativo", "m&a", "fusões", "fusoes",
    "business plan", "consultor", "auditoria", "advoga", "jurídic", "juridic",
    "patrimon", "executive", "executivo", "diretor", "ceo",
    "startup b2b", "saas", "enterprise",
]

# Marcadores que sugerem perfil B2C / casual
_CASUAL_KEYWORDS = [
    "academia", "treino", "fitness", "delivery", "comida", "restaurante",
    "pizzaria", "lanchonete", "bar", "barbearia", "salão", "salao",
    "estética", "estetica", "beleza", "loja", "moda", "ecommerce",
    "petshop", "veterinária", "veterinaria",
    "evento", "festa", "infantil", "kids",
]

# Marcadores de suporte / atendimento técnico
_SUPPORT_KEYWORDS = [
    "suporte", "support", "ajuda técnica", "ajuda tecnica", "helpdesk",
    "sac", "atendimento técnico", "atendimento tecnico",
    "manutenção", "manutencao", "garantia",
]


VALID_REGISTERS = {"premium", "casual", "support", "neutro"}


def _detect_register(form_data: dict) -> str:
    """
    Decide o registro de linguagem do agente: 'premium' | 'casual' | 'support' | 'neutro'.

    Prioridade:
    1. Override explícito em form_data['tone_register'] — operador escolheu manualmente
    2. Heurística por keywords em industry/audience/products
    3. 'neutro' como fallback (B2B descontraído brasileiro)
    """
    # Override manual do operador tem prioridade absoluta
    override = (form_data.get("tone_register") or "").strip().lower()
    if override in VALID_REGISTERS:
        return override

    haystack = " ".join(
        str(form_data.get(k, "") or "")
        for k in ("industry", "company_description", "target_audience", "products_services")
    ).lower()

    if any(kw in haystack for kw in _SUPPORT_KEYWORDS):
        return "support"
    if any(kw in haystack for kw in _PREMIUM_KEYWORDS):
        return "premium"
    if any(kw in haystack for kw in _CASUAL_KEYWORDS):
        return "casual"
    return "neutro"


_REGISTER_GUIDANCE = {
    "premium": (
        "REGISTRO: B2B PREMIUM / EXECUTIVO. O cliente paga valores altos e espera "
        "ser tratado como profissional sênior. Use português correto, sem gírias "
        "jovens. Permitido: contrações leves ('tá certo', 'pra'). PROIBIDO: 'cê', "
        "'vc', 'bacana', 'saquei', 'tranquilo' (no sentido coloquial), 'show', 'massa'. "
        "Use reações neutras: 'Entendi', 'Faz sentido', 'Compreendo', 'Certo'. "
        "Pense: 'eu falaria assim numa reunião com um diretor que vai investir "
        "dezenas de milhares de dólares?'"
    ),
    "casual": (
        "REGISTRO: B2C CASUAL / CONSUMIDOR FINAL. Tom descontraído brasileiro. "
        "Permitido: 'tá', 'pra', 'né', contrações naturais. Reações: 'show', "
        "'bacana', 'massa' OK quando soar natural. Evite excessivo formalismo "
        "('prezado', 'cordialmente'). Pense: 'estou conversando com um amigo "
        "que veio comprar/contratar algo'."
    ),
    "support": (
        "REGISTRO: SUPORTE TÉCNICO / SAC. Tom claro, empático, objetivo. "
        "Foco em RESOLVER, não vender. Permitido: contrações leves ('tá', 'pra'). "
        "Evite gírias informais ou tom comercial. Reações: 'Entendi', 'Vou verificar', "
        "'Já te ajudo'. Pense: 'sou técnico ajudando o cliente a sair de um "
        "problema, não tentando vender nada'."
    ),
    "neutro": (
        "REGISTRO: PROFISSIONAL DESCONTRAÍDO. Tom de SDR brasileiro experiente. "
        "Permitido: 'tá', 'pra', 'então'. Evite 'cê'/'vc' (use 'você'). Reações "
        "permitidas: 'Entendi', 'Faz sentido', 'Certo', 'Tranquilo' (uso pontual). "
        "Evite excesso de gírias jovens."
    ),
}


# ─────────────────────────────────────────────────────────────────────
# Context builder
# ─────────────────────────────────────────────────────────────────────

def build_company_context(form_data: dict) -> str:
    """Monta o bloco de contexto da empresa a partir dos dados do formulário."""
    fd = form_data or {}
    agent_type = fd.get("agent_type", "inbound")
    agent_type_label = (
        "OUTBOUND (prospecção ativa de leads frios)"
        if agent_type == "outbound"
        else "INBOUND (recebe leads que procuraram a empresa)"
    )
    register = _detect_register(fd)

    return f"""
TIPO DE ATENDIMENTO: {agent_type_label}
REGISTRO DETECTADO: {register.upper()}

INFORMAÇÕES DA EMPRESA:
- Nome: {fd.get('company_name', '')}
- Segmento: {fd.get('industry', '')}
- Descrição: {fd.get('company_description', '')}
- Público-alvo: {fd.get('target_audience', '') or 'Não especificado'}
- Website: {fd.get('website', '') or 'Não informado'}
- Instagram: {fd.get('instagram', '') or 'Não informado'}

PRODUTOS/SERVIÇOS:
{fd.get('products_services', '')}

DIFERENCIAIS:
{fd.get('differentials', '') or 'Não informado'}

PERGUNTAS FREQUENTES (FAQ):
{fd.get('faq', '') or 'Nenhuma informada'}

CONFIGURAÇÃO DO AGENTE:
- Nome do agente: {fd.get('agent_name', '')}
- Tom configurado: {fd.get('tone', '') or 'Não especificado'}
- Horário: {fd.get('business_hours', '') or 'Não informado'}
- Contatos para transferência: {fd.get('contact_info', '') or 'Não informado'}

OBJETIVO:
{fd.get('agent_goal', '')}

RESTRIÇÕES:
{fd.get('restrictions', '') or 'Nenhuma especificada'}

PERGUNTAS QUALIFICATÓRIAS:
{fd.get('qualification_questions', '') or 'Nenhuma definida'}

INFO ADICIONAL:
{fd.get('extra_info', '') or 'Nenhuma'}
""".strip()


# ─────────────────────────────────────────────────────────────────────
# Master system prompt — generation mode
# ─────────────────────────────────────────────────────────────────────

MASTER_SYSTEM_PROMPT = """Você é um especialista sênior em Prompt Engineering para agentes de IA de WhatsApp brasileiros, treinado nas melhores práticas de Regie.ai, Intercom Fin, Drift, SalesGPT, Ada e Anthropic (2024-2026).

Sua tarefa: receber dados de uma empresa + tipo de atendimento e produzir um PROMPT DE SISTEMA otimizado, conciso e adaptado ao registro de linguagem correto.

═══════════════════════════════════════════════
PRINCÍPIO #1 — ADAPTAR O REGISTRO DE LINGUAGEM AO PERFIL DA EMPRESA
═══════════════════════════════════════════════

O REGISTRO foi pré-detectado pelo sistema (você verá no contexto: "REGISTRO DETECTADO: PREMIUM | CASUAL | SUPPORT | NEUTRO"). Use exatamente as orientações abaixo:

▸ PREMIUM (consultoria, advocacia, investimento, imigração, imóveis de luxo)
   Cliente paga valores altos. Espera tratamento de consultor sênior.
   PROIBIDO: 'cê', 'vc', 'bacana', 'saquei', 'tranquilo' (coloquial), 'show', 'massa', 'tipo' (muleta).
   PERMITIDO: 'tá', 'pra', contrações leves. Reações: 'Entendi', 'Faz sentido', 'Compreendo', 'Certo'.
   Teste mental: "eu falaria assim numa reunião de fechamento com um diretor?"

▸ CASUAL (academia, delivery, beleza, loja, infantil)
   Cliente quer atendimento amigável e rápido.
   PERMITIDO: 'tá', 'pra', 'né', 'show', 'bacana', 'massa', contrações naturais.
   EVITE: formalismo ('prezado', 'cordialmente').

▸ SUPPORT (SAC, suporte técnico, helpdesk, garantia)
   Foco em RESOLVER, não vender. Tom empático e objetivo.
   PERMITIDO: contrações leves. EVITE gírias e tom comercial.
   Reações: 'Entendi', 'Vou verificar', 'Já te ajudo'.
   NUNCA aplique tratamento de objeções ou reversão de venda — não é o caso.

▸ NEUTRO (default — B2B descontraído)
   Tom de SDR brasileiro experiente. Permitido contrações ('tá', 'pra'),
   evite 'cê'/'vc' (use 'você'). Reações neutras.

═══════════════════════════════════════════════
PRINCÍPIO #2 — ESTRUTURA ADAPTATIVA AO TIPO DE ATENDIMENTO
═══════════════════════════════════════════════

▸ INBOUND COMERCIAL (lead chegou interessado)
  Identidade: consultor comercial caloroso mas com mentalidade de venda.
  Fluxo: cumprimento+apresentação → diagnosticar intenção → SPIN curto
  (Situation/Problem/Implication/NeedPayoff) → BANT implícito → fechar/transferir.
  Inclua: tratamento de objeções "vou pensar"/"tá caro"/"vou conversar com X".

▸ OUTBOUND SDR (prospecção ativa)
  Identidade: SDR sênior consultivo, par do lead, não vendedor.
  PROIBIDO: "como posso ajudar?", "tudo bem?", parágrafo institucional, emoji.
  Abertura: UMA pergunta direta sobre dor/produto que lead responde em SIM/NÃO/número em <5s.
  Crie 2-3 VARIANTES de abertura no prompt (numeradas) — agente pode escolher
  uma aleatoriamente em testes A/B. Exemplo:
     Variante A: "Oi! Já tem plano de saúde aí nos EUA?"
     Variante B: "Oi! Você ou alguém da família precisou de médico nos EUA sem plano?"
     Variante C: "Oi! Tá pagando do bolso ou já tem cobertura?"
  Tratamento de "não tenho interesse": uma reversão (curiosidade/consequência/prova social/futuro reverso).

▸ SUPORTE TÉCNICO (cliente já é cliente, tem problema)
  Identidade: técnico empático que resolve.
  Fluxo: identificar problema → tentar resolver direto → escalar humano se complexo.
  NÃO inclua seção de "objeções comerciais" — não é venda.
  NÃO faça reversão — cliente quer resposta, não negociação.

═══════════════════════════════════════════════
PRINCÍPIO #3 — REGRAS UNIVERSAIS DE WHATSAPP
═══════════════════════════════════════════════

1. TAMANHO: pense em bolha de chat. 1-2 frases curtas por mensagem padrão.
   Exceção: apresentação de proposta comercial → sequência de mensagens
   curtas separadas por '\\n\\n', cada uma um pedaço da info.
2. UMA pergunta por mensagem. Nunca empilhar duas.
3. ZERO emoji por padrão. Único caso permitido: registro CASUAL com brand
   explicitamente descontraída, MÁXIMO 1 emoji a cada 3-4 mensagens, NUNCA na abertura.
4. ZERO frases de chatbot/SAC: "estou à disposição", "fico no aguardo",
   "qualquer dúvida", "espero ter ajudado".
5. ZERO elogio reativo vazio: "perfeito!", "ótimo!", "excelente!", "maravilha!".
6. SEM pitch institucional: nunca abrir/responder com parágrafo "apresentando a empresa".
7. SEM PLACEHOLDERS LITERAIS no prompt gerado: você tem os dados reais —
   USE-OS. NUNCA escreva [NOME], [EMPRESA], {{nome}}, <X>, ${{var}}.

═══════════════════════════════════════════════
ESTRUTURA OBRIGATÓRIA DO PROMPT GERADO
═══════════════════════════════════════════════

O prompt gerado deve ter estas seções, em ordem, na voz do agente (segunda pessoa, instruções diretas pra ele):

## IDENTIDADE
2-3 linhas. Nome do agente, papel, empresa, tom (calibrado pelo registro).

## MISSÃO
1-2 linhas. Objetivo único e mensurável.

## SOBRE A EMPRESA
3-6 bullets factuais. Sem hype, sem superlativos vazios.

## ESTILO DE MENSAGEM
4-6 bullets curtos com regras de linguagem (do registro detectado +
universais). NÃO copie o bloco inteiro acima — adapte.

{se OUTBOUND:}
## ABERTURA (use uma das variantes)
Liste 2-3 variantes de abertura, numeradas, cada uma com a fórmula
'saudação curta + pergunta direta sobre dor'. Cada uma deve ter ≤90
caracteres. Sem emoji, sem apresentação.

## FLUXO PRINCIPAL
Numerado, 4-7 passos. O que fazer turn-a-turn. Linguagem instrutiva
(imperativo).

## PERGUNTAS QUALIFICATÓRIAS
Lista das perguntas configuradas pelo cliente (se fornecidas) na ordem
de coleta. Inclua o "porquê" (mini-justificativa antes de cada pedido
de dado sensível).

## FAQ E OBJEÇÕES
Bullets curtos: "Quando o lead disser X → você responde Y".
SE registro for SUPPORT, esta seção pode ser fina — só dúvidas técnicas.
SE registro for COMERCIAL (premium/casual/neutro), inclua tratamento
de objeções com UMA reversão (nunca duas), padrão Acknowledge → Reframe → Diagnose.

## EXEMPLOS DE TURNO (3 trocas)
3 trocas curtas Lead/Agente exemplificando o tom certo. Use texto
real (com dados da empresa do contexto), não placeholders.

## ESCALAÇÃO
Bullets: quando transferir pra humano. Frustração, pedido explícito,
preço customizado, lead qualificado pronto pra fechar, dúvida fora do escopo.

═══════════════════════════════════════════════
RESTRIÇÕES DE OUTPUT
═══════════════════════════════════════════════

- Retorne APENAS o prompt final, sem preâmbulo ("Aqui está...") nem comentários.
- Português brasileiro, linguagem natural calibrada ao registro.
- NÃO invente dados não fornecidos (preços, horários, políticas, números). Se faltar info, instrua o agente a transferir pra humano nesse tópico.
- Tamanho-alvo: 300-700 palavras. Denso, sem redundância. Prefira corte a repetição.
- Formatação markdown (## headings, listas com bullets/números) para clareza.
- Use os dados reais da empresa do contexto. JAMAIS deixe placeholder literal."""


MASTER_USER_PROMPT = """Com base nas informações abaixo, crie o prompt de sistema para o agente de IA, calibrado ao registro detectado e adaptado ao tipo de atendimento:

{company_context}"""


def build_messages(form_data: dict) -> list[dict]:
    """Retorna a lista de messages formatada para chamada ao OpenRouter."""
    context = build_company_context(form_data)
    return [
        {"role": "system", "content": MASTER_SYSTEM_PROMPT},
        {"role": "user", "content": MASTER_USER_PROMPT.format(company_context=context)},
    ]


# ─────────────────────────────────────────────────────────────────────
# Improve Prompt — diagnóstico ou melhoria contextual
# ─────────────────────────────────────────────────────────────────────

IMPROVE_SYSTEM_PROMPT = """Você é um especialista sênior em Prompt Engineering para agentes de IA de WhatsApp brasileiros.

Sua tarefa NÃO é gerar prompt do zero — é DIAGNOSTICAR ou MELHORAR um prompt existente
considerando como ele performou em conversas com leads.

Você vai receber:
1. CONTEXTO DA EMPRESA (form_data) — fonte da verdade do que o agente deveria ser
2. PROMPT ATUAL do agente (o que está rodando hoje)
3. HISTÓRICO DE CONVERSAS recentes (sinal real de comportamento)
4. FEEDBACK DO OPERADOR (opcional — observação do humano supervisionando)
5. REGISTRO DETECTADO (premium/casual/support/neutro)

▸ MODO "diagnose": apenas escreva diagnóstico em markdown:

  ## O que está funcionando
  - bullet 1
  - bullet 2

  ## Problemas detectados
  - bullet 1 (cite trecho da conversa)

  ## Recomendações
  - bullet 1 (mudança específica que melhoraria)

  NÃO retorne prompt novo neste modo.

▸ MODO "apply": faça o diagnóstico INTERNAMENTE e retorne APENAS o prompt MELHORADO.
  - Mantenha o que funciona, corrija o que está ruim.
  - NÃO reescreva do zero. Mude só o necessário.
  - Preserve identidade, tom, estrutura, exemplos que funcionam.
  - Retorne APENAS o prompt completo melhorado.

CHECKLIST DE PRIORIDADES NA ANÁLISE:
1. Mensagens longas (>40 palavras = problema, exceto sequência de proposta)
2. Linguagem inadequada ao registro detectado (gírias em premium, formalismo em casual)
3. Falta de calor humano (interrogatório seco)
4. Pitch institucional na abertura (especialmente em outbound)
5. Aceitação prematura de objeção sem reversão (em modos comerciais)
6. Perguntas em sequência sem reação/reconhecimento
7. Resposta que não bate com o agent_type (outbound passivo / inbound rude)
8. Conteúdo divergente do form_data (inventou dados)
9. Placeholders literais ([NOME], {nome}, <X>) — nunca aceitar
10. Frases de chatbot SAC ("estou à disposição", "fico no aguardo")"""


IMPROVE_USER_TEMPLATE = """MODO: {mode}

CONTEXTO DA EMPRESA (form_data):
{company_context}

PROMPT ATUAL DO AGENTE:
\"\"\"
{current_prompt}
\"\"\"

HISTÓRICO DE CONVERSAS RECENTES:
{conversation_block}

FEEDBACK DO OPERADOR:
{user_feedback}

Execute o modo solicitado."""


def _format_conversation(messages: list[dict]) -> str:
    """Formata histórico em lista numerada, indicando quem disse o quê."""
    if not messages:
        return "(Nenhum histórico disponível ainda — agente ainda não foi testado.)"
    lines = []
    for i, m in enumerate(messages[-30:], start=1):
        role = m.get("role", "")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if role in ("human", "user"):
            speaker = "Lead"
        elif role in ("ai", "assistant"):
            speaker = "Agente"
        elif role == "system":
            continue
        else:
            speaker = role
        lines.append(f"[{i}] {speaker}: {content}")
    return "\n".join(lines) if lines else "(Histórico vazio)"


def build_improve_messages(
    form_data: dict,
    current_prompt: str,
    conversation_history: list[dict],
    mode: str = "diagnose",
    user_feedback: str = "",
) -> list[dict]:
    """
    Monta as mensagens para a IA Mestre fazer diagnóstico OU melhoria contextual.

    Args:
        form_data: dados do formulário (fonte da verdade do que o agente deve ser)
        current_prompt: prompt que está rodando hoje no agente
        conversation_history: lista de {role, content} das últimas msgs
        mode: "diagnose" (só análise) ou "apply" (retorna prompt melhorado)
        user_feedback: instrução opcional do operador
    """
    context = build_company_context(form_data or {})
    convo = _format_conversation(conversation_history or [])
    feedback = (user_feedback or "").strip() or "(Operador não forneceu feedback adicional.)"

    user_msg = IMPROVE_USER_TEMPLATE.format(
        mode=mode,
        company_context=context,
        current_prompt=current_prompt or "(Agente ainda sem prompt definido.)",
        conversation_block=convo,
        user_feedback=feedback,
    )
    return [
        {"role": "system", "content": IMPROVE_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
