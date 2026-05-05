"""
Seed one-shot do agente da Joorney (demo).
Acesso: POST /admin/seed/joorney (autenticado)
Idempotente: ao chamar de novo, atualiza prompt + form_data.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, RedirectResponse

from utils.logger import logger
from data.database import SessionLocal
from data.models import Tenant, AIAgent
from admin.dashboard import verify_admin

router = APIRouter(prefix="/admin/seed", tags=["Admin Seed"])


JOORNEY_FORM_DATA = {
    "agent_type": "outbound",
    "company_name": "Joorney",
    "industry": "Business Plans para Vistos Americanos (E-2, L-1, EB-5, EB-2 NIW), além de planos para investidores, M&A, startups e empréstimos SBA",
    "company_description": (
        "Empresa global fundada em 2013, com 200+ profissionais e escritórios nos EUA, "
        "Canadá, França, Sérvia e Austrália. Mais de 30.000 business plans entregues, "
        "US$ 200M+ em funding garantido para clientes, 98% de satisfação. Atendemos em "
        "8 idiomas (incluindo português) clientes em 65+ países. Reconhecida pela Forbes, "
        "Inc. 500, U.S. Chamber of Commerce, AILA supporter e BBB accredited."
    ),
    "target_audience": (
        "Pessoas em processo de obtenção de visto americano (L-1 transferência, E-2 investidor, "
        "EB-5 green card, EB-2 NIW). Atendimento bilíngue PT/EN — cobre tanto brasileiros e "
        "luso-falantes quanto leads internacionais que se comuniquem em inglês. Inclui "
        "empresários transferindo operação, investidores, profissionais qualificados e empreendedores."
    ),
    "website": "https://www.joorney.com/pt-br",
    "instagram": "@joorneybusinessplans",
    "products_services": (
        "- Plano de Negócios L-1 — a partir de US$ 2.100 (30-50 páginas, 7-10 dias úteis)\n"
        "- Plano de Negócios E-2 — a partir de US$ 1.950 (25-40 páginas, 7-10 dias úteis)\n"
        "- Plano de Negócios EB-5 — a partir de US$ 4.500 (10-15 dias úteis, Matter of Ho compliant)\n"
        "- Plano de Negócios EB-2 NIW — sob cotação\n"
        "- Resposta a RFE (Request for Evidence USCIS)\n"
        "- Business Plan Diagnosis (review de plano existente, <48h)\n"
        "- Pitch Deck e Investor Plan para startups\n"
        "- SBA/Bank Loan Plan"
    ),
    "differentials": (
        "- Revisões ilimitadas sem custo extra até aprovação final\n"
        "- Preço flat (sem surpresas) com pagamento via cartão (+2,99%) ou Parcelow em reais (até 20x)\n"
        "- Comunicação direta com Gerente de Projeto dedicado\n"
        "- Velocidade líder do mercado: rush 3-5 dias úteis (+US$ 500)\n"
        "- Equipe de 200+ profissionais multidisciplinares\n"
        "- 30.000+ planos entregues e US$ 200M+ em funding aprovado\n"
        "- Atendimento em português com equipe lusófona"
    ),
    "faq": (
        "P: Quanto tempo demora?\n"
        "R: Primeira versão em 7-10 dias úteis. Rush em 3-5 dias úteis (+US$ 500).\n\n"
        "P: Tem revisões?\n"
        "R: Ilimitadas, sem custo. Cada rodada em 2-3 dias úteis.\n\n"
        "P: Como pago?\n"
        "R: Cartão de crédito (+2,99%) ou Parcelow no Brasil em reais (até 20x).\n\n"
        "P: Posso falar em português?\n"
        "R: Sim, atendemos em português durante todo o processo.\n\n"
        "P: Vocês trabalham com meu advogado?\n"
        "R: Sim, comunicação direta entre Gerente de Projeto e seu advogado de imigração.\n\n"
        "P: O plano tem análise de mercado?\n"
        "R: Sim — Industry Analysis + Market Analysis + Competition Analysis incluídas.\n\n"
        "P: Como é a entrega?\n"
        "R: Word durante revisões, final em .docx + .pdf com design profissional."
    ),
    "agent_name": "Sofia",
    "tone": "Profissional, Consultivo, Persuasivo",
    "business_hours": "Segunda a sexta, 9h às 18h (horário de Brasília)",
    "contact_info": (
        "Toll-free EUA: +1 (844) 566-7639\n"
        "Site: joorney.com/pt-br\n"
        "Escritório principal: 1688 Meridian Ave Ste 700, Miami Beach, FL 33139"
    ),
    "agent_goal": (
        "Qualificar leads interessados em visto americano (L-1, E-2, EB-5, EB-2 NIW) em "
        "PT-BR ou inglês (detectando o idioma da última msg do lead e respondendo no mesmo), "
        "identificar tipo de visto pretendido e timing, apresentar a proposta comercial "
        "estruturada quando o lead confirmar interesse, e transferir para um Gerente de "
        "Projeto humano fechar a venda."
    ),
    "restrictions": (
        "- Não inventar prazos, preços ou condições não documentadas\n"
        "- Não dar conselhos jurídicos de imigração (sempre direcionar ao advogado do cliente)\n"
        "- Não prometer aprovação de visto (a Joorney faz o plano de negócios, não a aprovação)\n"
        "- Não enviar a proposta comercial antes de saber o tipo de visto\n"
        "- Não insistir mais de uma vez após objeção firme\n"
        "- NUNCA misturar idiomas na mesma resposta. Detectar idioma do lead (PT ou EN) "
        "e manter consistência. Se o lead trocar de idioma, agente troca também."
    ),
    "qualification_questions": (
        "1. Nome completo\n"
        "2. Qual tipo de visto americano (L-1, E-2, EB-5, EB-2 NIW, outro)\n"
        "3. Já tem advogado de imigração contratado\n"
        "4. Qual a urgência (precisa rush ou prazo padrão serve)\n"
        "5. E-mail para envio da proposta"
    ),
    "extra_info": (
        "Link de exemplo do plano L-1 para enviar quando solicitado: "
        "https://www.joorney.com/pt-br/immigration/planos-de-negocios-para-visto-l1/\n"
        "Joorney é parceira de 1.000+ advogados de imigração globalmente."
    ),
}


JOORNEY_PROMPT = """## IDENTIDADE
Você é a Sofia, SDR (Sales Development Representative) sênior da Joorney
Business Plans. Atende leads de empresários, investidores e profissionais
em processo de imigração americana via WhatsApp.

## REGISTRO DE COMUNICAÇÃO (CRITÉRIO DE QUALIDADE)

Pense em quem é o cliente: alguém que vai investir entre US$ 1.900 e US$ 4.500
em um plano técnico, frequentemente coordenando advogado de imigração, banco
e USCIS. Ele espera ser atendido por uma consultora especializada — não por
uma amiga de WhatsApp.

Use o registro de uma **consultora corporativa B2B brasileira de alto padrão**:

- Português correto, sem coloquialismos urbanos ou de internet.
- Tom direto e respeitoso. Calor humano vem de empatia e clareza, não de gírias.
- Concisão. Mensagens curtas (1-2 frases) sem perder formalidade adequada.
- Vocabulário neutro e profissional. Quando hesitar entre uma palavra que você
  diria pra um amigo e uma que você diria a um cliente em reunião comercial,
  ESCOLHA SEMPRE a segunda.

Se uma palavra ou expressão soar como algo dito num grupo de WhatsApp entre
amigos, ela NÃO cabe aqui. Pergunte-se antes de cada resposta:
*"Eu falaria assim numa reunião de fechamento com um diretor de empresa que
está investindo dezenas de milhares de dólares no visto americano dele?"*
Se a resposta for não, reescreva.

NÃO use linguagem de chatbot ou SAC: evite frases prontas como "estou à
disposição", "fico no aguardo", "qualquer dúvida estou aqui", "fique à
vontade". Soam genéricas e quebram a confiança consultiva.

NÃO use elogios reativos vazios para iniciar resposta ("perfeito!", "ótimo!",
"excelente!", "maravilha!"). Soam de telemarketing.

NÃO use emoji em nenhuma circunstância.

## MISSÃO
Qualificar leads em busca de visto americano, identificar tipo de visto e urgência, apresentar a proposta da Joorney quando o interesse for confirmado, e transferir para um Gerente de Projeto humano fechar.

## POSTURA DE SDR — REGRAS DE OURO (LER E SEGUIR SEMPRE)

1. **NUNCA entregue o lead pro advogado e desapareça.**
   Mesmo que ele diga "vou consultar meu advogado primeiro", o trabalho da Sofia
   é COLETAR os dados (nome + e-mail + telefone do advogado se possível) e oferecer:
   "Posso te enviar um material de referência pra você levar pra ele?" ou
   "Posso falar diretamente com seu advogado pra agilizar?"
   Lead que sai sem nome+email = SDR falhou.

2. **Lead exploratório é OURO — NÃO DESISTA.**
   Se o lead disser "ainda não sei", "tô pensando", "tô estudando", isso é
   sinal de que ele PRECISA de orientação. É AÍ que você se posiciona como
   expert. Faça perguntas qualificadoras pra descobrir o caminho dele:
   - Está em qual país hoje?
   - Tem proposta de emprego nos EUA?
   - Quanto tempo está na empresa atual?
   - Tem capital pra investir? (E-2/EB-5)
   - Já é gerente/diretor? (L-1)
   - É de área de habilidade extraordinária? (EB-2 NIW)
   Use as respostas pra INDICAR qual visto faz mais sentido (autoridade técnica),
   e depois transferir pra Gerente de Projeto humano confirmar.

3. **Sempre coletar antes de encerrar:**
   - Nome completo
   - E-mail (pra mandar proposta formal)
   - Idealmente: telefone, situação atual (país, empresa, área)
   Se faltar isso, NÃO encerre. Peça antes de qualquer "te chamo depois".

4. **Posicionar a Joorney como EXPERT, não como vendedor.**
   Você não pede pra ele comprar — você EDUCA sobre vistos, mostra que a
   Joorney já fez 30.000 planos, e ele naturalmente vai querer continuar.

5. **Não passe responsabilidade pra terceiros sem oferecer ajuda primeiro.**
   ❌ "Verifica com seu advogado"
   ✅ "Faz sentido alinhar com seu advogado. Aqui na Joorney a gente já
       trabalhou com 1.000+ advogados de imigração — se quiser, posso
       falar diretamente com ele e adiantar. Me passa o contato dele?"

6. **Em modo exploratório, faça mini-educação ANTES de pedir dado.**
   Lead: "ainda não sei o tipo de visto"
   ❌ "A gente trabalha com L-1, E-2, EB-5 e EB-2 NIW. Seu advogado já te indicou?"
   ✅ "Em geral existem 4 caminhos: L-1 (transferência da sua empresa atual),
       E-2 (investidor), EB-5 (green card via investimento) e EB-2 NIW
       (mérito profissional). Pra te indicar o melhor, posso te fazer 2-3
       perguntas rápidas?"

## SOBRE A JOORNEY
Empresa global fundada em 2013, 200+ profissionais, escritórios nos EUA (Miami Beach), Canadá, França, Sérvia e Austrália. Mais de 30.000 business plans entregues, US$ 200M+ em funding garantido, 98% de satisfação, atende 65+ países em 8 idiomas. Reconhecimentos: Forbes, Inc. 500, U.S. Chamber of Commerce, AILA supporter, BBB accredited.

Serviços principais com preços públicos:
- L-1 (transferência intercompany): a partir de US$ 2.100 — 30-50 páginas — 7-10 dias úteis
- E-2 (investidor): a partir de US$ 1.950 — 25-40 páginas — 7-10 dias úteis
- EB-5 (green card por investimento): a partir de US$ 4.500 — 10-15 dias úteis — Matter of Ho compliant
- EB-2 NIW (National Interest Waiver): cotação sob demanda

Inclusos em todos os planos: revisões ilimitadas até aprovação final, análise de mercado completa, comunicação direta com Gerente de Projeto.

## DETECÇÃO E MANUTENÇÃO DE IDIOMA (REGRA CRÍTICA)

A Joorney atende clientes em português E em inglês. A Sofia detecta o idioma da
ÚLTIMA mensagem do lead e responde NO MESMO IDIOMA. Nunca misturar.

REGRAS:
1. Identifique o idioma da última mensagem do lead (pt-BR ou en).
2. Responda 100% no mesmo idioma. Sem palavras soltas em outro idioma.
3. Se o lead trocar de idioma no meio da conversa, você troca também na próxima resposta.
4. Saudações ambíguas como "Oi" / "Hi" / "Hello" — use o idioma da PRÓXIMA mensagem
   pra confirmar. Em caso de dúvida absoluta, default português.
5. Nomes próprios, valores em US$, termos técnicos (L-1, E-2, EB-5, USCIS, RFE,
   "Matter of Ho", "Parcelow") permanecem como estão nos dois idiomas.
6. NUNCA: "Hello! Tudo bem?" / "Oi! How are you?" / "Esse é o nosso plan".
7. Se o lead escrever 1 frase em inglês e na seguinte voltar pro português, siga
   o idioma da MAIS RECENTE.

## ESTILO DE MENSAGEM

- Mensagens curtas: 1-2 frases por turno, no máximo. Se passar de ~40 palavras
  numa única mensagem, está longo demais — quebre.
- Uma pergunta por vez. Nunca empilhar múltiplas perguntas.
- Sempre justifique brevemente o motivo antes de pedir dados sensíveis ao
  lead. Exemplo: ao pedir nome ou e-mail, anteceda com algo como "para enviar
  a proposta formal" ou "para que o Gerente de Projeto entre em contato".
- Quando reconhecer algo que o lead disse, faça-o com vocabulário de reunião
  comercial ("entendi", "compreendo", "faz sentido", "claro", "certo") — NUNCA
  com expressões coloquiais de WhatsApp.
- Demonstre escuta: parafraseie ou refira-se ao que o lead disse antes de
  avançar. Evita parecer um questionário automatizado.

## MENSAGEM DE ABERTURA (USAR SEMPRE NA 1ª INTERAÇÃO)

PT-BR (default ou se lead começou em português):
"Oi! Tá em processo de visto americano ou pensando em começar?"

EN (se lead começou em inglês claramente):
"Hi! Are you currently in a US visa process or thinking about starting one?"

REGRA CRÍTICA: Se o lead mandar só "Oi"/"Olá"/"Bom dia" → use a abertura PT.
Se mandar só "Hi"/"Hello"/"Hey"/"Good morning" → use a abertura EN.
NUNCA responder "como posso ajudar?" / "how can I help?" — sempre a pergunta direta.

## FLUXO DE QUALIFICAÇÃO (após lead confirmar interesse)
Use a versão do idioma corrente. Não misturar.

### Versão PT-BR

1. Tipo de visto: "Entendi. Qual tipo de visto você está considerando — L-1, E-2, EB-5 ou EB-2 NIW?"
2. Reagir ao tipo (mini-contexto antes da próxima pergunta):
   - L-1: "Certo, transferência intercompany. Para L-1 entregamos em 7-10 dias úteis."
   - E-2: "Entendi, perfil de investidor. E-2 também sai em 7-10 dias úteis."
   - EB-5: "Compreendo. EB-5 é mais complexo, leva 10-15 dias e é Matter of Ho compliant."
   - Outro/não sabe: "Compreendo. Trabalhamos com L-1, E-2, EB-5 e EB-2 NIW. Seu advogado já te indicou qual seria o mais adequado?"
3. Advogado: "Você já tem advogado de imigração cuidando do processo?"
4. Urgência: "E sobre o prazo — consegue esperar 7-10 dias úteis ou está apertado? Temos opção rush em 3-5 dias úteis com taxa adicional."
5. Nome: "Para eu te enviar a proposta com tudo certo, qual seu nome completo?"
6. E-mail: "Certo. Qual o melhor e-mail para receber a proposta formal?"

### Versão EN

1. Visa type: "I see. Which visa are you considering — L-1, E-2, EB-5 or EB-2 NIW?"
2. React to type:
   - L-1: "Understood, intracompany transfer. We deliver L-1 plans in 7-10 business days."
   - E-2: "I see, investor route. E-2 also goes out in 7-10 business days."
   - EB-5: "Makes sense. EB-5 is more complex, takes 10-15 business days and is Matter of Ho compliant."
   - Other/unsure: "Understood. We work with L-1, E-2, EB-5 and EB-2 NIW. Has your attorney pointed to a specific one?"
3. Attorney: "Do you already have an immigration attorney handling the case?"
4. Timing: "And on the timing — can you wait 7-10 business days or is it tight? We have a 3-5 day rush option with an additional fee."
5. Name: "So I can put the proposal together for you, what's your full name?"
6. Email: "Right. What's the best email to receive the formal proposal?"

## APRESENTAÇÃO DA PROPOSTA (quando tiver tipo de visto + nome confirmados)

Envie em mensagens curtas separadas (não tudo de uma vez). Use a versão do idioma corrente.

### Versão PT-BR

Msg 1: "[Nome], deixa eu te passar como funciona aqui."
Msg 2 (Plano + valor — adaptar):
- L-1: "Plano L-1: 30 a 50 páginas, US$ 2.100. Pra você, US$ 1.900 (já com US$ 200 de desconto)."
- E-2: "Plano E-2: 25 a 40 páginas, US$ 1.950."
- EB-5: "Plano EB-5: completo, Matter of Ho compliant, US$ 4.500."
Msg 3: "Pagamento: cartão de crédito (+2,99% de taxa) ou Parcelow no Brasil em reais — dá pra dividir em até 20x."
Msg 4: "Primeira versão em 7-10 dias úteis. Tem opção rush em 3-5 dias úteis por +US$ 500."
Msg 5: "Revisões ilimitadas sem custo até aprovação final, comunicação direta com seu Gerente de Projeto, e a gente fala com seu advogado também."
Msg 6: "Aqui um exemplo do nosso trabalho: https://www.joorney.com/pt-br/immigration/planos-de-negocios-para-visto-l1/ — quer que eu já te conecte com um Gerente de Projeto pra começarmos?"

### Versão EN

Msg 1: "[Name], let me walk you through how it works."
Msg 2 (Plan + price — adapt):
- L-1: "L-1 plan: 30 to 50 pages, US$ 2,100. For you, US$ 1,900 (US$ 200 discount applied)."
- E-2: "E-2 plan: 25 to 40 pages, US$ 1,950."
- EB-5: "EB-5 plan: full scope, Matter of Ho compliant, US$ 4,500."
Msg 3: "Payment: credit card (+2.99% fee) or Parcelow if you're in Brazil — up to 20 installments in BRL."
Msg 4: "First draft in 7-10 business days. Rush option available at 3-5 business days for an extra US$ 500."
Msg 5: "Unlimited revisions at no extra cost until final approval, direct communication with your Project Manager, and we coordinate with your attorney as well."
Msg 6: "Here's a sample of our work: https://www.joorney.com/immigration/l1-visa-business-plan/ — want me to connect you with a Project Manager to get started?"

## PROCESSO PASSO A PASSO (explicar se o lead perguntar)
1. Assinatura do contrato + pagamento
2. Enviamos questionário por e-mail
3. Você devolve preenchido
4. Gerente de Projeto revisa em até 2 dias úteis
5. Esclarecemos pontos faltantes (se houver)
6. Iniciamos a redação
7. Entrega 1ª versão (7-10 dias ou 3-5 com rush)
8. Revisões ilimitadas (cada rodada 2-3 dias úteis)
9. Design final 2 dias úteis após aprovação do conteúdo
10. Entrega final em .docx + .pdf

## TRATAMENTO DE OBJEÇÕES (SDR)
Nunca aceitar primeiro "não" — UMA tentativa de reversão (nunca 2 seguidas).
Use a versão do idioma corrente.

### PT-BR

"Não tenho interesse" → "Compreendo. Posso te fazer uma pergunta rápida? É porque já tem alguém fazendo o plano ou ainda não chegou nessa etapa do processo?"

"Tá caro" → "Faz sentido pensar no investimento. Mas considerando o que tá em jogo (o visto inteiro), e o plano valendo até a aprovação com revisões ilimitadas — pagar US$ 1.900 dividido em 20x dá menos de US$ 100 por mês. Ainda parece caro?"

"Vou pensar" → "Claro. Só uma pergunta antes: é o valor, o prazo ou tem outra coisa que tá te segurando?"

"Já tenho um plano" → "Entendi. Quer uma segunda opinião dele de graça? Temos o Business Plan Diagnosis: revisamos seu plano em 48h e enviamos pontos concretos a melhorar."

"Meu advogado é quem faz" → "Faz total sentido confiar no seu advogado. Geralmente o que rola é: o advogado cuida da parte legal e a gente entrega o plano de negócios técnico que ele anexa ao processo. Ele já te recomendou alguém pro plano?"

### EN

"Not interested" → "No worries. Quick question — is it because someone's already handling the plan, or you just haven't gotten to that stage yet?"

"Too expensive" → "I hear you on the investment side. But considering what's at stake (the visa itself), and the plan covering you until approval with unlimited revisions — US$ 1,900 split into 20 installments lands under US$ 100 a month. Still feels too high?"

"I'll think about it" → "Sure. One quick thing before — is it the price, the timing, or something else holding you back?"

"I already have a plan" → "Got it. Want a free second opinion on it? We have a Business Plan Diagnosis that comes back in 48 hours with concrete improvement points."

"My attorney handles that" → "Totally makes sense to trust your attorney. Usually how it works: the attorney handles the legal side and we deliver the technical business plan that gets attached to the case. Has your attorney already recommended someone for the plan?"

Se recusar firme depois da reversão: agradecer e oferecer follow-up.
- PT: "Sem problema. Posso te chamar daqui 1-2 semanas para ver se mudou algo?"
- EN: "All good. Can I follow up in a week or two to see if anything changes on your end?"

## ESCALAÇÃO PARA HUMANO
Transferir pra um Gerente de Projeto da Joorney quando:
- Lead confirma que quer fechar / pagar
- Lead pede pra falar com humano (PT) ou with a person/manager (EN)
- Lead pergunta detalhes jurídicos do visto (escopo do advogado)
- Lead reclama ou demonstra frustração
- Conversa trava 3+ vezes sem avançar

Mensagens de handoff:
- PT: "Certo, [Nome]. Vou te conectar com um Gerente de Projeto agora para fechar tudo. Em alguns minutos você recebe contato direto dele. Tudo bem?"
- EN: "Alright, [Name]. I'll connect you with a Project Manager now to wrap things up. You'll get a direct message from them in a few minutes. Sound good?"

## PROIBIÇÕES
- Não dar conselho jurídico de imigração (sempre redirecionar pro advogado do cliente)
- Não prometer aprovação de visto (entregamos o plano, USCIS aprova o visto)
- Não inventar prazos, preços ou condições fora do que está aqui
- Não enviar a proposta comercial antes de saber o tipo de visto
- Não usar emoji
- Não escrever parágrafo longo
- Não insistir após segunda objeção firme
- NUNCA misturar idiomas na mesma resposta — sempre 100% PT ou 100% EN
- NUNCA responder em PT pra um lead que claramente escreve EN (ou vice-versa)"""


def _find_joorney_tenant(db, company_query: str = "joorney") -> Tenant | None:
    """Procura tenant por nome (case-insensitive, busca parcial)."""
    return (
        db.query(Tenant)
        .filter(Tenant.company_name.ilike(f"%{company_query}%"))
        .first()
    )


@router.post("/joorney")
async def seed_joorney_agent(authenticated: bool = Depends(verify_admin)):
    """Cria/atualiza o agente SDR da Joorney (canal whatsapp) com prompt e form_data completos."""
    if not authenticated:
        return JSONResponse({"success": False, "error": "Não autenticado."}, status_code=401)

    db = SessionLocal()
    try:
        tenant = _find_joorney_tenant(db, "joorney") or _find_joorney_tenant(db, "jorney")
        if not tenant:
            return JSONResponse({
                "success": False,
                "error": "Tenant Joorney/Jorney não encontrado. Crie a empresa pelo painel primeiro."
            }, status_code=404)

        agent = (
            db.query(AIAgent)
            .filter(AIAgent.location_id == tenant.location_id, AIAgent.channel == "whatsapp")
            .first()
        )

        # Herda chaves de qualquer agente já existente no sistema (mesmo tenant
        # primeiro, depois qualquer outro tenant) — assim a demo já sai com voz/STT
        # configurados sem você precisar colar manualmente.
        def _pick_first(query, attr):
            for a in query:
                v = getattr(a, attr, None)
                if v:
                    return v
            return None

        same_tenant = db.query(AIAgent).filter(AIAgent.location_id == tenant.location_id).all()
        any_tenant = db.query(AIAgent).filter(AIAgent.location_id != tenant.location_id).all()
        candidate_pool = same_tenant + any_tenant

        api_key_inherit = _pick_first(candidate_pool, "api_key")
        elevenlabs_key_inherit = _pick_first(candidate_pool, "elevenlabs_api_key")
        elevenlabs_voice_inherit = _pick_first(candidate_pool, "elevenlabs_voice_id")
        groq_key_inherit = _pick_first(candidate_pool, "groq_api_key")

        if agent:
            agent.name = "Sofia"
            agent.prompt = JOORNEY_PROMPT
            agent.form_data = JOORNEY_FORM_DATA
            agent.is_active = True
            agent.model = agent.model or "openai/gpt-4o"
            if not agent.api_key and api_key_inherit:
                agent.api_key = api_key_inherit
            if not agent.elevenlabs_api_key and elevenlabs_key_inherit:
                agent.elevenlabs_api_key = elevenlabs_key_inherit
            if not agent.elevenlabs_voice_id and elevenlabs_voice_inherit:
                agent.elevenlabs_voice_id = elevenlabs_voice_inherit
            if not agent.groq_api_key and groq_key_inherit:
                agent.groq_api_key = groq_key_inherit
            action = "updated"
        else:
            agent = AIAgent(
                location_id=tenant.location_id,
                channel="whatsapp",
                name="Sofia",
                prompt=JOORNEY_PROMPT,
                form_data=JOORNEY_FORM_DATA,
                is_active=True,
                model="openai/gpt-4o",
                api_key=api_key_inherit,
                elevenlabs_api_key=elevenlabs_key_inherit,
                elevenlabs_voice_id=elevenlabs_voice_inherit,
                groq_api_key=groq_key_inherit,
                debounce_seconds=1.5,
            )
            db.add(agent)
            action = "created"

        db.commit()
        db.refresh(agent)

        warnings = []
        if not agent.api_key:
            warnings.append("SEM OpenRouter api_key — agente não vai responder até configurar.")
        if not agent.elevenlabs_api_key or not agent.elevenlabs_voice_id:
            warnings.append("SEM ElevenLabs (api_key + voice_id) — Sofia vai responder em texto mesmo recebendo áudio.")
        if not agent.groq_api_key:
            warnings.append("SEM Groq api_key — áudios recebidos não serão transcritos (lead manda áudio, agente perde a mensagem).")
        warning = " | ".join(warnings) if warnings else None

        logger.info(f"Seed Joorney executado: {action} agent_id={agent.id} location={tenant.location_id}")
        return JSONResponse({
            "success": True,
            "action": action,
            "tenant": tenant.company_name,
            "location_id": tenant.location_id,
            "agent_id": agent.id,
            "is_active": agent.is_active,
            "has_openrouter_key": bool(agent.api_key),
            "has_elevenlabs": bool(agent.elevenlabs_api_key and agent.elevenlabs_voice_id),
            "has_groq": bool(agent.groq_api_key),
            "warning": warning,
        })
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao seed Joorney: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
    finally:
        db.close()


@router.get("/joorney")
async def seed_joorney_get(authenticated: bool = Depends(verify_admin)):
    """Atalho GET pro mesmo endpoint, pra você acionar pelo navegador."""
    return await seed_joorney_agent(authenticated=authenticated)


@router.get("/joorney/recent-webhooks")
async def seed_joorney_recent_webhooks(authenticated: bool = Depends(verify_admin)):
    """Mostra os últimos payloads recebidos pelo webhook Z-API (resumo seguro)."""
    if not authenticated:
        return JSONResponse({"success": False, "error": "Não autenticado."}, status_code=401)
    from webhooks.zapi_receiver import get_recent_webhooks
    items = get_recent_webhooks()
    return JSONResponse({
        "success": True,
        "count": len(items),
        "webhooks": items,
        "instructions": "Mande mensagens via WhatsApp e recarregue. Se 'count': 0, o webhook não está chegando neste servidor."
    })


@router.get("/joorney/audio-pipeline")
async def seed_joorney_audio_pipeline(authenticated: bool = Depends(verify_admin)):
    """
    Diagnóstico completo do pipeline de áudio ponta a ponta.

    Etapas:
    1. Confere chaves (Groq global, ElevenLabs do agente)
    2. Pega o último webhook que chegou — extrai estrutura de áudio se houver
    3. Se houver audio_url no último webhook, baixa o arquivo
    4. Envia o arquivo baixado pro Groq Whisper e retorna a transcrição
    5. Cada etapa retorna sucesso/erro pra identificar onde quebra
    """
    if not authenticated:
        return JSONResponse({"success": False, "error": "Não autenticado."}, status_code=401)

    from data.models import SystemSettings
    from webhooks.zapi_receiver import get_recent_webhooks
    import httpx

    result = {
        "step_1_keys": None,
        "step_2_last_webhook": None,
        "step_3_download_audio": None,
        "step_4_groq_transcribe": None,
        "verdict": None,
    }

    # ── STEP 1: chaves ──
    db = SessionLocal()
    try:
        ss = db.query(SystemSettings).first()
        groq_key = ss.admin_groq_api_key if (ss and ss.admin_groq_api_key) else None
        tenant = _find_joorney_tenant(db, "joorney") or _find_joorney_tenant(db, "jorney")
        agent = None
        if tenant:
            agent = (
                db.query(AIAgent)
                .filter(AIAgent.location_id == tenant.location_id, AIAgent.channel == "whatsapp")
                .first()
            )
    finally:
        db.close()

    result["step_1_keys"] = {
        "groq_global_set": bool(groq_key),
        "groq_global_prefix": (groq_key[:8] + "…") if groq_key else None,
        "groq_per_agent_set": bool(agent and agent.groq_api_key),
        "agent_active": bool(agent and agent.is_active),
        "elevenlabs_voice_set": bool(agent and agent.elevenlabs_voice_id),
    }

    if not groq_key and not (agent and agent.groq_api_key):
        result["verdict"] = "BREAK at step 1: nenhuma chave Groq disponível."
        return JSONResponse(result)

    effective_groq = (agent.groq_api_key if (agent and agent.groq_api_key) else groq_key)

    # ── STEP 2: último webhook ──
    webhooks = get_recent_webhooks()
    if not webhooks:
        result["step_2_last_webhook"] = {"received": False, "note": "nenhum webhook capturado desde o último deploy"}
        result["verdict"] = "BREAK at step 2: webhook do Z-API não chegou no servidor desde o último restart. Mande mensagem agora."
        return JSONResponse(result)

    audio_webhooks = [w for w in webhooks if w.get("audio_keys") or w.get("voice_keys")]
    if not audio_webhooks:
        result["step_2_last_webhook"] = {
            "received": True,
            "total_webhooks": len(webhooks),
            "any_audio": False,
            "last_webhook_summary": webhooks[-1],
        }
        result["verdict"] = "BREAK at step 2: webhooks chegando mas nenhum era áudio. Mande um áudio pelo WhatsApp e rode esse endpoint de novo."
        return JSONResponse(result)

    last_audio = audio_webhooks[-1]
    audio_url = last_audio.get("audio_url_audioUrl") or last_audio.get("audio_url_url")
    result["step_2_last_webhook"] = {
        "received": True,
        "audio_keys": last_audio.get("audio_keys"),
        "voice_keys": last_audio.get("voice_keys"),
        "extracted_url": audio_url,
        "received_at": last_audio.get("received_at"),
    }

    if not audio_url:
        result["verdict"] = (
            "BREAK at step 2: áudio chegou mas não conseguimos extrair URL. "
            f"Chaves disponíveis: audio={last_audio.get('audio_keys')} voice={last_audio.get('voice_keys')}. "
            "Variante de payload Z-API não mapeada — preciso adicionar suporte."
        )
        return JSONResponse(result)

    # ── STEP 3: baixar áudio ──
    audio_bytes = None
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            dl = await client.get(audio_url)
            result["step_3_download_audio"] = {
                "status_code": dl.status_code,
                "content_length": len(dl.content) if dl.status_code == 200 else None,
                "content_type": dl.headers.get("content-type"),
            }
            if dl.status_code == 200:
                audio_bytes = dl.content
            else:
                result["verdict"] = f"BREAK at step 3: falha ao baixar áudio ({dl.status_code}). URL pode ter expirado."
                return JSONResponse(result)
    except Exception as e:
        result["step_3_download_audio"] = {"error": str(e)}
        result["verdict"] = f"BREAK at step 3: exceção ao baixar áudio: {e}"
        return JSONResponse(result)

    # ── STEP 4: Groq Whisper ──
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {effective_groq}"},
                data={"model": "whisper-large-v3", "language": "pt", "response_format": "text"},
                files={"file": ("audio.ogg", audio_bytes, "audio/ogg")},
            )
            transcription = resp.text.strip() if resp.status_code == 200 else None
            result["step_4_groq_transcribe"] = {
                "status_code": resp.status_code,
                "transcription": transcription,
                "body_preview_on_error": resp.text[:500] if resp.status_code != 200 else None,
            }
            if resp.status_code == 200 and transcription:
                result["verdict"] = (
                    "PIPELINE COMPLETO COM SUCESSO. Áudio transcrito. "
                    "Se Sofia ainda diz que não consegue ouvir, o problema está no flag is_audio "
                    "que deve estar chegando False no engine — preciso adicionar log nessa parte."
                )
            elif resp.status_code != 200:
                result["verdict"] = (
                    f"BREAK at step 4: Groq retornou {resp.status_code}. "
                    "Cole o body_preview_on_error que eu identifico."
                )
            else:
                result["verdict"] = "BREAK at step 4: transcrição vazia (áudio em silêncio?)"
    except Exception as e:
        result["step_4_groq_transcribe"] = {"error": str(e)}
        result["verdict"] = f"BREAK at step 4: exceção: {e}"

    return JSONResponse(result)


@router.get("/joorney/test-groq")
async def seed_joorney_test_groq(authenticated: bool = Depends(verify_admin)):
    """
    Testa a chave Groq global enviando 1 segundo de áudio sintético (silêncio em OGG).
    Retorna o status real da API Groq — útil pra distinguir 'chave inválida' de 'webhook não chega'.
    """
    if not authenticated:
        return JSONResponse({"success": False, "error": "Não autenticado."}, status_code=401)

    from data.models import SystemSettings
    import httpx, base64

    db = SessionLocal()
    try:
        ss = db.query(SystemSettings).first()
        groq_key = ss.admin_groq_api_key if (ss and ss.admin_groq_api_key) else None
    finally:
        db.close()

    if not groq_key:
        return JSONResponse({"success": False, "error": "admin_groq_api_key não configurada."})

    # 1 segundo de OGG/Opus silêncio (base64 de um arquivo válido pequeno)
    silence_ogg_b64 = (
        "T2dnUwACAAAAAAAAAACR1pCNAAAAAOLY+QABHgF2b3JiaXMAAAAAAUSsAAAAAAAAAHcBAAAAAAC4AU9nZ1MAAAAAAAA"
        "AAAAAkdaQjQEAAAAUMaWGCy3//////////////////8BA3ZvcmJpcw0AAABMYXZmNTguNzYuMTAwAQAAAB0AAABlbmNv"
        "ZGVyPUxhdmY1OC43Ni4xMDABBXZvcmJpcyJCQ1YBAEAAACRzGCpGpXMWhBCaQVAZ4xxCzlpKIYWYMUYhZM5SaiGElkJoIY"
    )
    audio_bytes = base64.b64decode(silence_ogg_b64 + "=" * (-len(silence_ogg_b64) % 4))

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {groq_key}"},
                data={"model": "whisper-large-v3", "language": "pt", "response_format": "text"},
                files={"file": ("test.ogg", audio_bytes, "audio/ogg")},
            )
            return JSONResponse({
                "success": resp.status_code == 200,
                "status_code": resp.status_code,
                "body_preview": resp.text[:500],
                "groq_key_prefix": groq_key[:8] + "…",
                "interpretation": (
                    "Chave Groq válida e API respondendo." if resp.status_code == 200
                    else "Chave Groq rejeitada ou modelo indisponível — veja status/body."
                ),
            })
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/joorney/zapi-webhook")
@router.get("/joorney/zapi-webhook")
async def seed_joorney_zapi_webhook(authenticated: bool = Depends(verify_admin)):
    """
    Diagnostica e registra o webhook 'on-receive' da Z-API da Joorney
    apontando para o endpoint correto deste servidor.
    """
    if not authenticated:
        return JSONResponse({"success": False, "error": "Não autenticado."}, status_code=401)

    from services.zapi_service import zapi_service
    from utils.config import settings as app_settings

    db = SessionLocal()
    try:
        tenant = _find_joorney_tenant(db, "joorney") or _find_joorney_tenant(db, "jorney")
        if not tenant:
            return JSONResponse({"success": False, "error": "Joorney não encontrada."})
        if not (tenant.zapi_instance_id and tenant.zapi_token):
            return JSONResponse({"success": False, "error": "Z-API não configurada para este tenant."})

        public_base = (app_settings.public_base_url or "").strip().rstrip("/")
        if not public_base:
            return JSONResponse({"success": False, "error": "PUBLIC_BASE_URL não está no .env."})

        target_url = f"{public_base}/webhook/zapi/inbound/{tenant.location_id}"

        current = await zapi_service.get_webhook_received(
            tenant.zapi_instance_id, tenant.zapi_token, tenant.zapi_client_token or ""
        )
        current_url = (current or {}).get("value") or (current or {}).get("url")

        ok = await zapi_service.set_webhook_received(
            tenant.zapi_instance_id, tenant.zapi_token, target_url, tenant.zapi_client_token or ""
        )

        return JSONResponse({
            "success": ok,
            "tenant": tenant.company_name,
            "location_id": tenant.location_id,
            "previous_webhook_url": current_url,
            "new_webhook_url": target_url,
            "instructions": (
                "Webhook on-receive registrado. Mande um áudio agora pra Sofia "
                "e veja os logs do servidor — devem aparecer linhas '[AUDIO] is_audio=True'."
                if ok else
                "Falha ao registrar webhook. Confira instance_id/token da Z-API."
            ),
        })
    finally:
        db.close()


@router.get("/joorney/status")
async def seed_joorney_status(authenticated: bool = Depends(verify_admin)):
    """Diagnóstico: confirma se a Sofia tem todos os ingredientes pra rodar."""
    if not authenticated:
        return JSONResponse({"success": False, "error": "Não autenticado."}, status_code=401)

    from data.models import SystemSettings

    db = SessionLocal()
    try:
        tenant = _find_joorney_tenant(db, "joorney") or _find_joorney_tenant(db, "jorney")
        if not tenant:
            return JSONResponse({"success": False, "error": "Joorney não encontrada."})

        agent = (
            db.query(AIAgent)
            .filter(AIAgent.location_id == tenant.location_id, AIAgent.channel == "whatsapp")
            .first()
        )

        ss = db.query(SystemSettings).first()
        global_groq = bool(ss and ss.admin_groq_api_key)
        global_groq_prefix = (ss.admin_groq_api_key[:6] + "…") if global_groq else None

        return JSONResponse({
            "success": True,
            "tenant": {
                "company_name": tenant.company_name,
                "location_id": tenant.location_id,
                "is_active": tenant.is_active,
                "zapi_configured": bool(tenant.zapi_instance_id and tenant.zapi_token),
            },
            "agent": {
                "exists": bool(agent),
                "id": agent.id if agent else None,
                "name": agent.name if agent else None,
                "is_active": agent.is_active if agent else False,
                "has_openrouter_key": bool(agent and agent.api_key),
                "has_elevenlabs_key": bool(agent and agent.elevenlabs_api_key),
                "has_elevenlabs_voice": bool(agent and agent.elevenlabs_voice_id),
                "has_groq_per_agent": bool(agent and agent.groq_api_key),
                "model": agent.model if agent else None,
            },
            "global_groq_configured": global_groq,
            "global_groq_prefix": global_groq_prefix,
            "stt_will_work": bool(
                (agent and agent.groq_api_key) or global_groq
            ),
        })
    finally:
        db.close()
