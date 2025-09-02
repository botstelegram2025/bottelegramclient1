const { makeWASocket, DisconnectReason, useMultiFileAuthState } = require('@whiskeysockets/baileys');
const { Boom } = require('@hapi/boom');
const express = require('express');
const cors = require('cors');
const QRCode = require('qrcode');
const fs = require('fs');
const path = require('path');

const app = express();
app.use(cors());
app.use(express.json());

// Environment detection
const isRailway = process.env.RAILWAY_ENVIRONMENT_NAME !== undefined;
const isLocal = !isRailway;

// UNIFIED PORT CONFIGURATION - Solves Railway/Local conflict
const UNIFIED_PORT = parseInt(process.env.PORT) || (isLocal ? 3001 : 8080);

console.log(`🌍 Environment: ${isRailway ? 'Railway' : 'Local'}`);
console.log(`🎯 UNIFIED PORT: ${UNIFIED_PORT}`);

if (isRailway) {
    console.log('⚡ Railway environment - optimized for cloud deployment');
    console.log('🔗 Port will bind to Railway dynamic port for external access');
} else {
    console.log('💻 Local environment - using standard port 3001');
}

// Map para armazenar sessões de cada usuário
const userSessions = new Map();

// Semáforo para controlar conexões simultâneas
class ConnectionSemaphore {
    constructor(maxConcurrent = 3) {
        this.maxConcurrent = maxConcurrent;
        this.current = 0;
        this.queue = [];
    }
    
    async acquire() {
        return new Promise((resolve) => {
            if (this.current < this.maxConcurrent) {
                this.current++;
                resolve();
            } else {
                this.queue.push(resolve);
            }
        });
    }
    
    release() {
        this.current--;
        if (this.queue.length > 0) {
            const next = this.queue.shift();
            this.current++;
            next();
        }
    }
}

const connectionSemaphore = new ConnectionSemaphore(2); // Max 2 conexões simultâneas

// Estrutura de dados para cada sessão de usuário
class UserWhatsAppSession {
    constructor(userId) {
        this.userId = userId;
        this.sock = null;
        this.qrCodeData = null;
        this.isConnected = false;
        this.connectionState = 'disconnected';
        this.authFolder = `auth_info_baileys_user_${userId}`;
        this.reconnectTimeout = null;
        this.heartbeatInterval = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.pairingCode = null; // Store pairing code
        this.phoneNumber = null; // Store phone number for pairing
    }
    
    async start(forceNew = false) {
        // Acquire semaphore to limit concurrent connections
        await connectionSemaphore.acquire();
        
        try {
            console.log(`🚀 Starting WhatsApp session for user ${this.userId}`);
            
            // Random delay to prevent simultaneous connections
            const delay = Math.random() * 2000 + 1000; // 1-3 seconds
            await new Promise(resolve => setTimeout(resolve, delay));
            
            if (this.sock && this.isConnected && !forceNew) {
                console.log(`✅ User ${this.userId} already connected`);
                return;
            }
            
            // Clear existing connection if forcing new
            if (forceNew && this.sock) {
                try {
                    await this.sock.end();
                } catch (error) {
                    console.log(`Warning: Error ending existing connection for user ${this.userId}:`, error.message);
                }
                this.sock = null;
                this.isConnected = false;
            }
            
            // Clear any existing timeout
            if (this.reconnectTimeout) {
                clearTimeout(this.reconnectTimeout);
                this.reconnectTimeout = null;
            }
            
            const authPath = path.join(__dirname, 'sessions', this.authFolder);
            
            // Create sessions directory if it doesn't exist
            const sessionsDir = path.join(__dirname, 'sessions');
            if (!fs.existsSync(sessionsDir)) {
                fs.mkdirSync(sessionsDir, { recursive: true });
            }
            
            // Setup auth state
            const { state, saveCreds } = await useMultiFileAuthState(authPath);
            
            const sock = makeWASocket({
                auth: state,
                browser: [`Baileys-${this.userId}`, 'Chrome', '91.0'],
                connectTimeoutMs: 60000,
                defaultQueryTimeoutMs: 60000,
                keepAliveIntervalMs: 30000,
                logger: {
                    level: 'silent', // Reduce log noise
                    log: () => {} // Disable logs
                },
                shouldIgnoreJid: jid => isJidBroadcast(jid),
                markOnlineOnConnect: false,
                printQRInTerminal: false,
                // Add session-specific identifier
                generateHighQualityLinkPreview: false,
                syncFullHistory: false,
                shouldSyncHistoryMessage: () => false,
                retryRequestDelayMs: 250,
                qrTimeout: 30000,
                version: [2, 2413, 1],
                syncFullHistory: false
            });
            
            this.sock = sock;
            this.connectionState = 'connecting';
            
            // Handle connection updates
            sock.ev.on('connection.update', async (update) => {
                const { connection, lastDisconnect, qr } = update;
                
                if (qr) {
                    try {
                        console.log(`📱 QR Code gerado para usuário ${this.userId}`);
                        this.qrCodeData = await QRCode.toDataURL(qr);
                        this.connectionState = 'qr_ready';
                    } catch (error) {
                        console.error(`Erro gerando QR para usuário ${this.userId}:`, error);
                        this.qrCodeData = null;
                    }
                }
                
                if (connection === 'close') {
                    const shouldReconnect = (lastDisconnect?.error as Boom)?.output?.statusCode !== DisconnectReason.loggedOut;
                    console.log(`🔌 Conexão fechada para usuário ${this.userId}, reconectando:`, shouldReconnect);
                    
                    this.isConnected = false;
                    this.connectionState = 'disconnected';
                    
                    if (shouldReconnect && this.reconnectAttempts < this.maxReconnectAttempts) {
                        this.reconnectAttempts++;
                        this.scheduleReconnect();
                    } else if (this.reconnectAttempts >= this.maxReconnectAttempts) {
                        console.log(`❌ Max reconnect attempts reached for user ${this.userId}`);
                        this.connectionState = 'failed';
                    }
                } else if (connection === 'open') {
                    console.log(`✅ WhatsApp conectado para usuário ${this.userId}`);
                    this.isConnected = true;
                    this.connectionState = 'connected';
                    this.reconnectAttempts = 0;
                    this.qrCodeData = null; // Clear QR code when connected
                    this.pairingCode = null; // Clear pairing code when connected
                }
            });
            
            // Handle credentials update
            sock.ev.on('creds.update', saveCreds);
            
            // Handle pairing code
            sock.ev.on('creds.update', async () => {
                if (this.phoneNumber && sock.authState.creds.registered === false) {
                    try {
                        const code = await sock.requestPairingCode(this.phoneNumber);
                        this.pairingCode = code;
                        console.log(`📱 Pairing code for user ${this.userId}: ${code}`);
                        this.connectionState = 'pairing_code_ready';
                    } catch (error) {
                        console.error(`Error generating pairing code for user ${this.userId}:`, error);
                    }
                }
            });
            
            // Start heartbeat
            this.startHeartbeat();
            
        } catch (error) {
            console.error(`❌ Erro ao iniciar sessão para usuário ${this.userId}:`, error);
            this.connectionState = 'error';
            this.scheduleReconnect();
        } finally {
            // Release semaphore
            connectionSemaphore.release();
        }
    }
    
    scheduleReconnect() {
        if (this.reconnectTimeout) return;
        
        const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 60000); // Exponential backoff, max 60s
        console.log(`⏰ Agendando reconexão para usuário ${this.userId} em ${delay/1000}s`);
        
        this.reconnectTimeout = setTimeout(() => {
            this.reconnectTimeout = null;
            this.start();
        }, delay);
    }
    
    startHeartbeat() {
        if (this.heartbeatInterval) {
            clearInterval(this.heartbeatInterval);
        }
        
        this.heartbeatInterval = setInterval(() => {
            if (this.isConnected && this.sock) {
                // Send heartbeat ping
                this.sock.sendPresenceUpdate('available').catch(() => {
                    // Ignore heartbeat errors
                });
            }
        }, 30000); // Every 30 seconds
    }
    
    async sendMessage(number, message) {
        if (!this.isConnected || !this.sock) {
            throw new Error(`WhatsApp não conectado para usuário ${this.userId}`);
        }
        
        try {
            const jid = `${number}@s.whatsapp.net`;
            const result = await this.sock.sendMessage(jid, { text: message });
            console.log(`✅ Mensagem enviada com sucesso para ${number} pelo usuário ${this.userId}`);
            return result;
        } catch (error) {
            console.error(`❌ Erro ao enviar mensagem para ${number} pelo usuário ${this.userId}:`, error);
            throw error;
        }
    }
    
    getStatus() {
        return {
            userId: this.userId,
            isConnected: this.isConnected,
            connectionState: this.connectionState,
            hasQR: !!this.qrCodeData,
            hasPairingCode: !!this.pairingCode,
            reconnectAttempts: this.reconnectAttempts
        };
    }
    
    async generateQRCode() {
        if (this.qrCodeData) {
            return this.qrCodeData;
        }
        return null;
    }
    
    async generatePairingCode(phoneNumber) {
        try {
            this.phoneNumber = phoneNumber.replace(/\D/g, ''); // Store clean phone number
            
            if (this.sock && this.sock.authState.creds.registered === false) {
                const code = await this.sock.requestPairingCode(this.phoneNumber);
                this.pairingCode = code;
                this.connectionState = 'pairing_code_ready';
                console.log(`📱 Generated pairing code for user ${this.userId}: ${code}`);
                return code;
            } else {
                // Start new session to generate pairing code
                await this.start(true);
                // Wait a bit for the session to initialize
                await new Promise(resolve => setTimeout(resolve, 2000));
                
                if (this.sock && this.sock.authState.creds.registered === false) {
                    const code = await this.sock.requestPairingCode(this.phoneNumber);
                    this.pairingCode = code;
                    this.connectionState = 'pairing_code_ready';
                    console.log(`📱 Generated pairing code for user ${this.userId}: ${code}`);
                    return code;
                } else {
                    throw new Error('Unable to generate pairing code - session may already be registered');
                }
            }
        } catch (error) {
            console.error(`Error generating pairing code for user ${this.userId}:`, error);
            throw error;
        }
    }
    
    async disconnect() {
        try {
            if (this.heartbeatInterval) {
                clearInterval(this.heartbeatInterval);
                this.heartbeatInterval = null;
            }
            
            if (this.reconnectTimeout) {
                clearTimeout(this.reconnectTimeout);
                this.reconnectTimeout = null;
            }
            
            if (this.sock) {
                await this.sock.end();
                this.sock = null;
            }
            
            this.isConnected = false;
            this.connectionState = 'disconnected';
            this.qrCodeData = null;
            this.pairingCode = null;
            
            console.log(`🔌 Usuário ${this.userId} desconectado`);
        } catch (error) {
            console.error(`Erro ao desconectar usuário ${this.userId}:`, error);
        }
    }
}

function isJidBroadcast(jid) {
    return [
        '@broadcast',
        '@newsletter'
    ].some(suffix => jid?.includes?.(suffix));
}

// Get or create user session
function getUserSession(userId) {
    if (!userSessions.has(userId)) {
        userSessions.set(userId, new UserWhatsAppSession(userId));
    }
    return userSessions.get(userId);
}

// API Endpoints

// Send message endpoint
app.post('/send/:userId', async (req, res) => {
    try {
        const userId = req.params.userId;
        const { number, message } = req.body;
        
        if (!number || !message) {
            return res.json({
                success: false,
                error: 'Number and message are required'
            });
        }
        
        const session = getUserSession(userId);
        
        if (!session.isConnected) {
            return res.json({
                success: false,
                error: `WhatsApp não conectado para usuário ${userId}. Por favor, conecte primeiro.`
            });
        }
        
        const result = await session.sendMessage(number, message);
        
        res.json({
            success: true,
            messageId: result.key.id,
            result: result
        });
    } catch (error) {
        console.error('Send message error:', error);
        res.json({
            success: false,
            error: error.message
        });
    }
});

// Generate QR Code endpoint
app.post('/generate-qr/:userId', async (req, res) => {
    try {
        const userId = req.params.userId;
        console.log(`📱 QR code solicitado para usuário ${userId}`);
        
        const session = getUserSession(userId);
        
        // Force new connection to generate fresh QR
        await session.start(true);
        
        // Wait for QR generation
        let attempts = 0;
        const maxAttempts = 15; // 15 seconds max wait
        
        while (!session.qrCodeData && attempts < maxAttempts) {
            await new Promise(resolve => setTimeout(resolve, 1000));
            attempts++;
        }
        
        if (session.qrCodeData) {
            res.json({
                success: true,
                qrCode: session.qrCodeData,
                message: 'QR Code gerado com sucesso'
            });
        } else {
            res.json({
                success: false,
                error: 'Não foi possível gerar o QR Code. Tente novamente.',
                connectionState: session.connectionState
            });
        }
    } catch (error) {
        console.error('Generate QR error:', error);
        res.json({
            success: false,
            error: error.message
        });
    }
});

// Get QR Code endpoint (existing QR)
app.get('/qr/:userId', async (req, res) => {
    try {
        const userId = req.params.userId;
        const session = getUserSession(userId);
        
        if (session.qrCodeData) {
            res.json({
                success: true,
                qrCode: session.qrCodeData,
                connectionState: session.connectionState
            });
        } else {
            res.json({
                success: false,
                message: 'QR Code não disponível',
                connectionState: session.connectionState
            });
        }
    } catch (error) {
        res.json({
            success: false,
            error: error.message
        });
    }
});

// Connection status endpoint
app.get('/status/:userId', (req, res) => {
    try {
        const userId = req.params.userId;
        const session = userSessions.get(userId);
        
        if (!session) {
            return res.json({
                success: true,
                status: 'disconnected',
                message: 'Sessão não encontrada'
            });
        }
        
        res.json({
            success: true,
            ...session.getStatus()
        });
    } catch (error) {
        res.json({
            success: false,
            error: error.message
        });
    }
});

// Connect endpoint
app.post('/connect/:userId', async (req, res) => {
    try {
        const userId = req.params.userId;
        const session = getUserSession(userId);
        
        // Start connection (will check if already connected)
        session.start();
        
        res.json({
            success: true,
            message: `Iniciando conexão para usuário ${userId}`,
            status: session.getStatus()
        });
    } catch (error) {
        res.json({
            success: false,
            error: error.message
        });
    }
});

// Disconnect endpoint
app.post('/disconnect/:userId', async (req, res) => {
    try {
        const userId = req.params.userId;
        const session = userSessions.get(userId);
        
        if (session) {
            await session.disconnect();
            userSessions.delete(userId);
        }
        
        res.json({
            success: true,
            message: `Usuário ${userId} desconectado`
        });
    } catch (error) {
        res.json({
            success: false,
            error: error.message
        });
    }
});

// Reconnect endpoint
app.post('/reconnect/:userId', async (req, res) => {
    try {
        const userId = req.params.userId;
        const session = getUserSession(userId);
        
        // Force reconnection
        await session.start(true);
        
        res.json({
            success: true,
            message: `Reconexão iniciada para usuário ${userId}`,
            status: session.getStatus()
        });
    } catch (error) {
        res.json({
            success: false,
            error: error.message
        });
    }
});

// Force QR endpoint
app.post('/force-qr/:userId', async (req, res) => {
    try {
        const userId = req.params.userId;
        console.log(`🔄 Force QR solicitado para usuário ${userId}`);
        
        const session = getUserSession(userId);
        
        // Disconnect first, then generate new QR
        await session.disconnect();
        
        // Wait a bit
        await new Promise(resolve => setTimeout(resolve, 1000));
        
        // Start fresh connection
        await session.start(true);
        
        // Wait for QR generation
        let attempts = 0;
        const maxAttempts = 15;
        
        while (!session.qrCodeData && session.connectionState !== 'qr_ready' && attempts < maxAttempts) {
            await new Promise(resolve => setTimeout(resolve, 1000));
            attempts++;
        }
        
        if (session.qrCodeData) {
            res.json({
                success: true,
                qrCode: session.qrCodeData,
                message: 'Novo QR Code gerado com sucesso'
            });
        } else {
            res.json({
                success: false,
                error: 'Não foi possível gerar novo QR Code',
                connectionState: session.connectionState
            });
        }
    } catch (error) {
        console.error('Force QR error:', error);
        res.json({
            success: false,
            error: error.message
        });
    }
});

// Pairing code endpoints
app.post('/generate-pairing-code/:userId', async (req, res) => {
    try {
        const userId = req.params.userId;
        const { phoneNumber } = req.body;
        
        if (!phoneNumber) {
            return res.json({
                success: false,
                error: 'Número de telefone é obrigatório'
            });
        }
        
        console.log(`📱 Pairing code solicitado para usuário ${userId} com número ${phoneNumber}`);
        
        const session = getUserSession(userId);
        const code = await session.generatePairingCode(phoneNumber);
        
        res.json({
            success: true,
            pairingCode: code,
            message: 'Código de pareamento gerado com sucesso',
            phoneNumber: phoneNumber
        });
    } catch (error) {
        console.error('Generate pairing code error:', error);
        res.json({
            success: false,
            error: error.message
        });
    }
});

// Endpoint para buscar código de pareamento existente
app.get('/pairing-code/:userId', (req, res) => {
    try {
        const userId = req.params.userId;
        const session = userSessions.get(userId);
        
        if (!session) {
            return res.json({
                success: false,
                error: 'Sessão não encontrada'
            });
        }
        
        if (session.pairingCode) {
            res.json({
                success: true,
                pairingCode: session.pairingCode,
                state: session.connectionState
            });
        } else {
            res.json({
                success: false,
                message: 'Código de pareamento não disponível'
            });
        }
    } catch (error) {
        console.error('Get pairing code error:', error);
        res.json({
            success: false,
            error: 'Internal server error'
        });
    }
});

// Endpoint para listar todos os usuários conectados (admin)
app.get('/sessions', (req, res) => {
    const sessions = Array.from(userSessions.entries()).map(([userId, session]) => ({
        userId,
        ...session.getStatus()
    }));
    
    res.json({
        success: true,
        sessions,
        totalSessions: sessions.length
    });
});

// Health check endpoint
app.get('/health', (req, res) => {
    const connectedSessions = Array.from(userSessions.values()).filter(s => s.isConnected).length;
    const totalSessions = userSessions.size;
    
    res.json({
        success: true,
        status: 'healthy',
        connectedSessions,
        totalSessions,
        uptime: process.uptime(),
        timestamp: new Date().toISOString(),
        port: UNIFIED_PORT,
        environment: isRailway ? 'Railway' : 'Local'
    });
});

// Restore session endpoint (for manual recovery)
app.post('/restore/:userId', async (req, res) => {
    try {
        const userId = req.params.userId;
        const session = getUserSession(userId);
        
        // Check if session exists
        const authPath = path.join(__dirname, 'sessions', session.authFolder);
        const hasValidSession = fs.existsSync(authPath) && fs.existsSync(path.join(authPath, 'creds.json'));
        
        if (!hasValidSession) {
            return res.json({
                success: false,
                error: 'No valid session found for this user'
            });
        }
        
        // Force restart the session without clearing auth
        await session.start(false);
        
        res.json({
            success: true,
            message: 'Session restore initiated',
            hasSession: hasValidSession
        });
    } catch (error) {
        res.json({
            success: false,
            error: error.message
        });
    }
});

// Auto-recovery system - check sessions every 5 minutes
setInterval(() => {
    console.log(`🔍 Health check: ${userSessions.size} active sessions`);
    
    userSessions.forEach((session, userId) => {
        if (!session.isConnected && session.connectionState === 'disconnected') {
            const authPath = path.join(__dirname, 'sessions', session.authFolder);
            const hasValidSession = fs.existsSync(authPath) && fs.existsSync(path.join(authPath, 'creds.json'));
            
            if (hasValidSession && !session.reconnectTimeout) {
                console.log(`🔄 Auto-recovering session for user ${userId}...`);
                session.start(false);
            }
        }
    });
}, 300000); // 5 minutes

// Graceful shutdown
process.on('SIGINT', () => {
    console.log('🛑 Graceful shutdown initiated...');
    
    const promises = Array.from(userSessions.values()).map(session => {
        if (session.reconnectTimeout) {
            clearTimeout(session.reconnectTimeout);
        }
        return session.sock?.end();
    });
    
    Promise.all(promises).finally(() => {
        console.log('✅ All sessions closed');
        process.exit(0);
    });
});

// UNIFIED PORT SERVER START
app.listen(UNIFIED_PORT, '0.0.0.0', () => {
    console.log(`🚀 Servidor Baileys Multi-User rodando na porta ${UNIFIED_PORT}`);
    console.log(`✅ Sistema de recuperação automática ativo`);
    console.log(`💾 Sessões persistentes em ./sessions/`);
    console.log(`🌍 Listening on 0.0.0.0:${UNIFIED_PORT} for all interfaces`);
    
    if (isRailway) {
        console.log(`⚡ Railway deployment mode ACTIVE`);
        console.log(`🔗 External access: Railway domain`);
        console.log(`🔗 Internal access: 127.0.0.1:${UNIFIED_PORT}`);
        console.log(`🎯 UNIFIED PORT solves Railway/Local conflict!`);
    } else {
        console.log(`💻 Local development mode`);
        console.log(`🔗 Local access: http://localhost:${UNIFIED_PORT}`);
    }
});