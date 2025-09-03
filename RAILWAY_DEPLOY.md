# 🚀 Deploy no Railway - Telegram Client Management Bot

## 📋 Pré-requisitos

1. **Conta Railway**: Acesse [railway.app](https://railway.app) e crie uma conta
2. **Token do Bot Telegram**: Criado via [@BotFather](https://t.me/BotFather)
3. **Credenciais MercadoPago**: Access Token e Public Key

## 🔧 Passo a Passo para Deploy

### 1. **Preparar o Repositório**

```bash
# Clone ou faça fork do repositório
git clone <seu-repositorio>
cd <nome-do-projeto>

# Verifique se todos os arquivos necessários estão presentes:
# ✅ railway.toml
# ✅ requirements.txt  
# ✅ package.json
# ✅ Procfile
# ✅ start_railway.py
# ✅ .env.example
```

### 2. **Criar Projeto no Railway**

1. Acesse [railway.app](https://railway.app)
2. Clique em **"New Project"**
3. Selecione **"Deploy from GitHub repo"**
4. Conecte seu repositório GitHub
5. Selecione o repositório do bot

### 3. **Adicionar PostgreSQL Database**

1. No dashboard do projeto, clique em **"New"**
2. Selecione **"Database"** > **"PostgreSQL"**
3. Aguarde a criação (DATABASE_URL será automaticamente configurado)

### 4. **Configurar Variáveis de Ambiente**

No dashboard do Railway, vá em **"Variables"** e adicione:

#### **Obrigatórias:**
```
TELEGRAM_BOT_TOKEN=1234567890:ABC-DEF-GHI_seu_token_aqui
MERCADO_PAGO_ACCESS_TOKEN=APP_USR-seu_access_token_aqui
MERCADO_PAGO_PUBLIC_KEY=APP_USR-seu_public_key_aqui
```

#### **Automáticas (Railway configura):**
```
DATABASE_URL=postgresql://... (automático)
PORT=8080 (automático)
NODE_ENV=production (automático)
```

#### **Opcionais:**
```
ADMIN_USER_ID=seu_telegram_user_id_para_admin
```

### 5. **Deploy**

1. **Primeiro Deploy**: O Railway fará automaticamente após conectar o repo
2. **Deploy Manual**: Clique em **"Deploy"** no dashboard
3. **Deploy Automático**: Configurado por push no GitHub

### 6. **Verificar Deploy**

#### **Logs em Tempo Real:**
1. No dashboard, clique em **"View Logs"**
2. Procure por:
   ```
   ✅ WhatsApp Baileys service started
   ✅ Telegram Bot service started  
   ✅ All services started successfully
   ```

#### **Health Check:**
- URL: `https://seu-projeto.railway.app/health`
- Deve retornar JSON com status dos serviços

### 7. **Testar o Bot**

1. **No Telegram**: Envie `/start` para seu bot
2. **Verificar Logs**: Monitore atividade no Railway
3. **WhatsApp**: Teste conectividade no bot (🔗 WhatsApp)

## 🔧 Arquivos de Configuração

### **railway.toml**
```toml
[build]
builder = "nixpacks"
nixpacksConfigPath = "nixpacks.toml"

[deploy]  
startCommand = "python start_railway.py"
restartPolicyType = "always"
restartPolicyMaxRetries = 5

[env]
NODE_ENV = "production"
PYTHONUNBUFFERED = "1"
```

### **Procfile**
```
web: python start_railway.py
```

### **start_railway.py**
- Gerencia ambos os serviços (Telegram Bot + WhatsApp Baileys)
- Health check endpoint
- Restart automático de serviços
- Logging detalhado

## 📊 Monitoramento

### **Dashboard Railway:**
- **Metrics**: CPU, RAM, Network
- **Logs**: Output em tempo real
- **Health**: Status dos serviços

### **Logs Importantes:**
```
🚀 Starting Railway deployment...
✅ WhatsApp Baileys service started  
✅ Telegram Bot service started
🏥 Health check endpoint started on port 8080
```

## 🛠️ Resolução de Problemas

### **Bot não responde:**
1. Verificar `TELEGRAM_BOT_TOKEN` 
2. Checar logs por erros
3. Confirmar health check ativo

### **WhatsApp não conecta:**
1. Verificar logs do WhatsApp service
2. Tentar reconectar via bot
3. Verificar porta 3001 disponível

### **Pagamentos não funcionam:**
1. Verificar credenciais MercadoPago
2. Testar tokens em ambiente de produção
3. Confirmar webhook configurado

### **Database errors:**
1. Confirmar PostgreSQL service ativo
2. Verificar DATABASE_URL 
3. Checar migrations executadas

## 📱 Uso Após Deploy

1. **Primeiro Uso**: 
   - Envie `/start` no bot
   - Complete registro/trial
   - Conecte WhatsApp via QR Code

2. **Recursos Disponíveis**:
   - ✅ Gestão completa de clientes
   - ✅ Lembretes automáticos  
   - ✅ Pagamentos via PIX
   - ✅ WhatsApp multi-usuário
   - ✅ Relatórios e analytics

## 🔄 Atualizações

Para atualizar o bot:

1. **Push no GitHub**: Deploy automático
2. **Manual**: Botão "Deploy" no Railway  
3. **Rollback**: Disponível no dashboard

## 💰 Custos Railway

- **Starter Plan**: $5/mês (recomendado)
- **PostgreSQL**: Incluído no plano
- **Bandwidth**: Generoso limite gratuito

---

## ✅ Checklist Final

- [ ] Repositório no GitHub configurado
- [ ] Projeto Railway criado  
- [ ] PostgreSQL database adicionado
- [ ] Variáveis de ambiente configuradas
- [ ] Deploy realizado com sucesso
- [ ] Health check funcionando
- [ ] Bot responde no Telegram
- [ ] Logs sem erros críticos
- [ ] WhatsApp conectável via QR
- [ ] Pagamentos testados

**🎉 Parabéns! Seu bot está rodando no Railway!**