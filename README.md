# Axen WP — Integração Z-API & GoHighLevel

Sistema servidor em Python FastAPI para substituir as automações do n8n entre o GoHighLevel e a Z-API. Este sistema é Multi-Tenant e suporta o registro (onboarding) de novas empresas de forma autônoma via **OAuth 2.0**.

## Principais Funcionalidades

- **Multi-Tenant Arquitetura Livre de DB**: Guarda chaves e tokens em `data/tenants/*.json`.
- **Onboarding Automático via OAuth**: Mapeia `locationId` do GHL gerando Access/Refresh tokens perfeitamente isolados por empresa.
- **Outbound Webhook (GHL → Z-API)**: Captura as mensagens enviadas no Axen WP (GHL) enviando via WhatsApp (Z-API). Suporta anexos.
- **Inbound Webhook (Z-API → GHL)**: Recebe retornos e novas conversas do Z-API salvando dentro do CRM GHL na timeline do contato usando endpoints oficias.
- **Automated Token Refresh**: Auto-atualiza os Access Tokens (que duram 24h) proativamente a cada 12h via APScheduler em background.

---

## 🚀 Como Rodar o Servidor

1. Instalar as credenciais ambiente:
```bash
cp .env.example .env
# Edite as variáveis com os dados do Marketplace GHL e Z-API Secret
```

2. Você pode rodar de forma nativa localmente:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Inicia o servidor uvicorn (Porta 8000)
python main.py
```

3. Ou rodar usando Docker na VPS de Produção:
```bash
docker-compose up -d --build
```

---

## 🔗 Fluxo de Onboarding de Novas Empresas

Sempre que a sua agência vender a automação *Axen WP* para uma nova Imobiliária/Empresa, execute os seguintes passos:

1. Gere uma nova **Client Key** e Secret para o app "Axen WP" lá no dashboard de desenvolvedor do GHL (página Marketplace > MyApps). Isso vincula o aplicativo de forma única a essa instalação.
2. Com o seu servidor Python rodando (ex: https://api.sua-agencia.com), vá no navegador:
   `https://api.sua-agencia.com/oauth/install?company=NomedaEmpresaNova`
3. O GHL vai pedir confirmação e solicitar os escopos da Agenda/Contatos. Autorize.
4. O seu servidor vai magicamente trocar os códigos e salvar o novo Json automaticamente em: `/data/tenants/LOCAL_ID.json`.
5. Edite esse arquivo gerado manualmente e só preencha a conexão da **Z-API**:
```json
...
  "zapi_instance_id": "ID_DA_INSTANCIA_DO_CLIENTE_NO_PAINEL",
  "zapi_token": "TOKEN_Z_API",
  "client_id": "COLE O CLIENT ID DO PASSO 1",
  "client_secret": "COLE O SECRET DO PASSO 1"
...
```
✅ **Fim! O webhook já vai estar lendo os retornos do whatsapp dessa empresa para o crm!** 🚀

---

## Configurando Webhooks Z-API (Inbound)

Para a Z-API notificar este servidor toda vez que o cliente final enviar um Whats, vá no painel **Z-API** e ative o `Ao receber`. Insira o Inbound URL com base no Local ID da empresa que foi gerado no seu JSON:

**Link do Inbound Webhook (Z-API Receiver)**
```
https://api.sua-agencia.com/webhook/zapi/inbound/{ID-DA-LOCATION-NO-GHL}
```
Exemplo Real:
`https://axen-server.fly.dev/webhook/zapi/inbound/FkO3l88oXyV3i`
