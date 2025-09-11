const { makeWASocket, DisconnectReason, useMultiFileAuthState } = require('@whiskeysockets/baileys');
const { Boom } = require('@hapi/boom');
const express = require('express');
const cors = require('cors');
const QRCode = require('qrcode');
const fs = require('fs');
const path = require('path');
const { Client } = require('pg');

const app = express();
app.use(cors());
app.use(express.json());

// Railway environment detection
const isRailway = process.env.RAILWAY_ENVIRONMENT_NAME !== undefined;
console.log(`🌍 Environment: ${isRailway ? 'Railway' : 'Local'}`);

if (isRailway) {
    console.log('⚡ Railway environment detected - optimizing for cloud deployment');
}

// Database connection for persistent sessions
let pgClient = null;
const connectToDatabase = async () => {
    if (pgClient) return pgClient;
    
    pgClient = new Client({
        connectionString: process.env.DATABASE_URL || 'postgresql://localhost:5432/telegram_bot',
        ssl: process.env.NODE_ENV === 'production' ? { rejectUnauthorized: false } : false
    });
    
    try {
        await pgClient.connect();
        console.log('🐘 Connected to PostgreSQL for session persistence');
        return pgClient;
    } catch (error) {
        console.error('❌ Failed to connect to PostgreSQL:', error);
        pgClient = null;
        return null;
    }
};

// Save session to database
const saveSessionToDatabase = async (userId, sessionData) => {
    try {
        const client = await connectToDatabase();
        if (!client) return false;

        const sessionJson = JSON.stringify(sessionData);
        
        await client.query(`
            INSERT INTO whatsapp_sessions (user_id, session_data, is_connected, connection_status, updated_at)
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (user_id) 
            DO UPDATE SET 
                session_data = $2,
                is_connected = $3,
                connection_status = $4,
                updated_at = NOW()
        `, [userId, sessionJson, sessionData.connected || false, sessionData.status || 'disconnected']);
        
        console.log(`💾 Session saved to database for user ${userId}`);
        return true;
    } catch (error) {
        console.error('❌ Error saving session to database:', error);
        return false;
    }
};

// Load session from database
const loadSessionFromDatabase = async (userId) => {
    try {
        const client = await connectToDatabase();
        if (!client) return null;

        const result = await client.query(
            'SELECT session_data, is_connected, connection_status FROM whatsapp_sessions WHERE user_id = $1',
            [userId]
        );
        
        if (result.rows.length > 0) {
            const sessionData = JSON.parse(result.rows[0].session_data || '{}');
            sessionData.connected = result.rows[0].is_connected;
            sessionData.status = result.rows[0].connection_status;
            console.log(`📥 Session loaded from database for user ${userId}`);
            return sessionData;
        }
        
        return null;
    } catch (error) {
        console.error('❌ Error loading session from database:', error);
        return null;
    }
};

// Update session status in database
const updateSessionStatus = async (userId, isConnected, status = 'connected') => {
    try {
        const client = await connectToDatabase();
        if (!client) return false;

        await client.query(`
            UPDATE whatsapp_sessions 
            SET is_connected = $2, connection_status = $3, last_activity = NOW(), updated_at = NOW()
            WHERE user_id = $1
        `, [userId, isConnected, status]);
        
        console.log(`🔄 Session status updated for user ${userId}: ${status}`);
        return true;
    } catch (error) {
        console.error('❌ Error updating session status:', error);
        return false;
    }
};

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
        
        // Load session from database on initialization
        this.loadPersistedSession();
    }
    
    async loadPersistedSession() {
        try {
            const sessionData = await loadSessionFromDatabase(this.userId);
            if (sessionData) {
                this.isConnected = sessionData.connected || false;
                this.connectionState = sessionData.status || 'disconnected';
                console.log(`🔄 Restored session state for user ${this.userId}: ${this.connectionState}`);
                
                // If was connected, try to restore connection
                if (this.isConnected && this.connectionState === 'connected') {
                    console.log(`🔄 Attempting to restore active connection for user ${this.userId}...`);
                    // Start connection restoration in background
                    setTimeout(() => this.start(false), 2000);
                }
            }
        } catch (error) {
            console.error(`❌ Error loading persisted session for user ${this.userId}:`, error);
        }
    }
    
    async saveSessionState() {
        try {
            const sessionData = {
                connected: this.isConnected,
                status: this.connectionState,
                timestamp: new Date().toISOString()
            };
            await saveSessionToDatabase(this.userId, sessionData);
            await updateSessionStatus(this.userId, this.isConnected, this.connectionState);
        } catch (error) {
            console.error(`❌ Error saving session state for user ${this.userId}:`, error);
        }
    }
    
    async start(forceNew = false) {
        // Acquire semaphore to limit concurrent connections
        await connectionSemaphore.acquire();
        
        try {
            console.log(`🔄 Starting connection for user ${this.userId}, forceNew: ${forceNew}`);
            
            if (this.reconnectTimeout) {
                clearTimeout(this.reconnectTimeout);
            }
            
            // Force clean start to always generate QR when requested
            if (forceNew) {
                // Backup existing session before cleaning
                const authPath = path.join(__dirname, 'sessions', this.authFolder);
                if (fs.existsSync(authPath)) {
                    const backupPath = path.join(__dirname, 'sessions', `backup_${this.authFolder}_${Date.now()}`);
                    try {
                        fs.cpSync(authPath, backupPath, { recursive: true });
                        console.log(`💾 Backup created for user ${this.userId} at ${backupPath}`);
                    } catch (e) {
                        console.log(`⚠️ Could not create backup for user ${this.userId}: ${e.message}`);
                    }
                    
                    fs.rmSync(authPath, { recursive: true, force: true });
                    console.log(`🧹 Cleaned auth for user ${this.userId} - will generate new QR`);
                }
                this.qrCodeData = null;
                this.connectionState = 'generating_qr';
            }
            
            // Ensure sessions directory exists
            const sessionsDir = path.join(__dirname, 'sessions');
            if (!fs.existsSync(sessionsDir)) {
                fs.mkdirSync(sessionsDir, { recursive: true });
            }
            
            const { state, saveCreds } = await useMultiFileAuthState(path.join(__dirname, 'sessions', this.authFolder));
            
            this.sock = makeWASocket({
                auth: state,
                printQRInTerminal: false,
                defaultQueryTimeoutMs: 300000, // Extended Railway timeout  
                connectTimeoutMs: 300000, // Railway needs longer timeout
                browser: [`User_${this.userId}`, 'Chrome', '22.04.4'], // Unique browser per user
                syncFullHistory: false,
                markOnlineOnConnect: true, // Keep connection visible
                generateHighQualityLinkPreview: false,
                retryRequestDelayMs: 5000, // Railway optimized retry delay
                maxMsgRetryCount: 5, // More retries
                shouldSyncHistoryMessage: () => false,
                keepAliveIntervalMs: 30000, // More frequent keepalive
                emitOwnEvents: false,
                msgRetryCounterCache: new Map(),
                shouldIgnoreJid: () => false,
                // Enhanced connection stability options
                qrTimeout: 180000, // Railway extended QR timeout
                connectCooldownMs: 8000, // Railway longer cooldown
                userDevicesCache: new Map(),
                transactionOpts: {
                    maxCommitRetries: 5, // More retries
                    delayBetweenTriesMs: 2000 // Longer delay
                },
            });

            this.sock.ev.on('connection.update', async (update) => {
                const { connection, lastDisconnect, qr, isNewLogin } = update;
                
                if (qr) {
                    console.log(`✅ QR Code gerado para usuário ${this.userId}`);
                    this.qrCodeData = await QRCode.toDataURL(qr);
                    this.connectionState = 'qr_generated';
                }
                
                // Generate pairing code if it's a new login
                if (isNewLogin && !this.pairingCode && this.phoneNumber) {
                    try {
                        const code = await this.sock.requestPairingCode(this.phoneNumber);
                        this.pairingCode = code;
                        this.connectionState = 'pairing_code_generated';
                        console.log(`🔐 Pairing Code gerado para usuário ${this.userId} (${this.phoneNumber}): ${code}`);
                    } catch (error) {
                        console.log(`⚠️ Could not generate pairing code for user ${this.userId}:`, error.message);
                    }
                }
                
                if (connection === 'close') {
                    const statusCode = lastDisconnect?.error?.output?.statusCode;
                    const errorMessage = lastDisconnect?.error?.message || 'Unknown error';
                    
                    // Enhanced reconnection logic with specific error handling
                    const shouldReconnect = ![
                        DisconnectReason.loggedOut,
                        DisconnectReason.badSession,
                        DisconnectReason.multideviceMismatch
                    ].includes(statusCode);
                    
                    console.log(`❌ Conexão fechada para usuário ${this.userId}, status: ${statusCode}, erro: "${errorMessage}", reconectando: ${shouldReconnect}`);
                    
                    this.isConnected = false;
                    this.connectionState = 'disconnected';
                    
                    // Save disconnection state to database
                    await this.saveSessionState();
                    
                    // Preserve QR data for longer to avoid unnecessary regeneration
                    if (statusCode !== 408 && statusCode !== 428) {
                        this.qrCodeData = null;
                    }
                    
                    // Enhanced error handling with specific recovery strategies
                    if (statusCode === 515 || errorMessage.includes('stream errored')) {
                        // Stream error - gradual reconnection
                        console.log(`🔄 Stream error for user ${this.userId}, attempting gentle reconnection...`);
                        this.reconnectTimeout = setTimeout(() => this.start(false), 8000);
                    } else if (statusCode === 408) {
                        // QR timeout - preserve session and retry
                        console.log(`⏰ QR timeout for user ${this.userId}, preserving session...`);
                        this.reconnectTimeout = setTimeout(() => this.start(false), 3000);
                    } else if (statusCode === 428 || errorMessage.includes('Connection Terminated')) {
                        // Connection terminated by server - wait before retry
                        console.log(`🛑 Connection terminated by server for user ${this.userId}, waiting before retry...`);
                        this.reconnectTimeout = setTimeout(() => this.start(false), 10000);
                    } else if (statusCode === 401) {
                        // Unauthorized - ALWAYS force new QR for auth errors
                        console.log(`🔐 Auth error 401 for user ${this.userId}, forcing clean QR generation...`);
                        // Clean corrupted session
                        try {
                            if (fs.existsSync(this.authPath)) {
                                fs.unlinkSync(this.authPath);
                                console.log(`🗑️ Removed corrupted session file for user ${this.userId}`);
                            }
                        } catch (cleanError) {
                            console.log(`⚠️ Error removing session file: ${cleanError.message}`);
                        }
                        this.reconnectTimeout = setTimeout(() => this.start(true), 2000);
                    } else if (statusCode === 440) {
                        // Conflict error - wait longer to avoid conflicts
                        console.log(`⚡ Conflict error for user ${this.userId}, backing off...`);
                        this.reconnectTimeout = setTimeout(() => this.start(false), 15000);
                    } else if (shouldReconnect) {
                        // Normal reconnection with progressive backoff
                        const delay = Math.min(5000 + (Math.random() * 5000), 20000); // 5-10s with max 20s
                        console.log(`🔄 Auto-reconnecting user ${this.userId} in ${delay}ms...`);
                        this.reconnectTimeout = setTimeout(() => this.start(false), delay);
                    } else {
                        console.log(`❌ User ${this.userId} requires manual reconnection`);
                    }
                } else if (connection === 'connecting') {
                    console.log(`🔄 WhatsApp conectando para usuário ${this.userId}...`);
                    this.connectionState = 'connecting';
                } else if (connection === 'open') {
                    console.log(`✅ WhatsApp conectado com sucesso para usuário ${this.userId}!`);
                    this.isConnected = true;
                    this.connectionState = 'connected';
                    this.qrCodeData = null;
                    this.pairingCode = null; // Clear pairing code when connected
                    this.reconnectAttempts = 0; // Reset reconnect attempts
                    
                    // Save connection state to database
                    await this.saveSessionState();
                    
                    // Clear any reconnection timeouts
                    if (this.reconnectTimeout) {
                        clearTimeout(this.reconnectTimeout);
                        this.reconnectTimeout = null;
                    }
                    
                    // Start heartbeat to maintain connection
                    this.startHeartbeat();
                }
            });

            this.sock.ev.on('creds.update', saveCreds);
            
        } catch (error) {
            console.error(`❌ Erro ao iniciar WhatsApp para usuário ${this.userId}:`, error);
            this.connectionState = 'error';
        } finally {
            // Always release semaphore
            connectionSemaphore.release();
        }
    }
    
    async sendMessage(number, message) {
        // If socket doesn't exist, definitely can't send
        if (!this.sock) {
            throw new Error('WhatsApp não conectado para este usuário');
        }
        
        // If we think we're disconnected but socket exists, try to send anyway
        if (!this.isConnected) {
            console.log(`⚠️ User ${this.userId} marked as disconnected but attempting to send anyway...`);
        }
        
        // Formatar número para WhatsApp
        let formattedNumber = number.replace(/\D/g, '');
        if (!formattedNumber.startsWith('55')) {
            formattedNumber = '55' + formattedNumber;
        }
        formattedNumber += '@s.whatsapp.net';
        
        try {
            const result = await this.sock.sendMessage(formattedNumber, { text: message });
            console.log(`📤 Mensagem enviada pelo usuário ${this.userId} para ${number}: ${message}`);
            
            // If send was successful but we thought we were disconnected, update status
            if (!this.isConnected) {
                console.log(`✅ Message sent successfully for user ${this.userId}, updating connection status`);
                this.isConnected = true;
                this.connectionState = 'connected';
            }
            
            return result;
        } catch (error) {
            console.log(`❌ Erro ao enviar mensagem para usuário ${this.userId}:`, error.message);
            
            // If connection is closed, mark as disconnected
            if (error.message.includes('Connection Closed') || error.message.includes('closed') || error.message.includes('ECONNRESET')) {
                console.log(`🔄 Connection lost during message send for user ${this.userId}, marking as disconnected...`);
                this.isConnected = false;
                this.connectionState = 'disconnected';
                
                // Clear heartbeat to avoid conflicts
                if (this.heartbeatInterval) {
                    clearInterval(this.heartbeatInterval);
                    this.heartbeatInterval = null;
                }
                
                // Try to reconnect automatically
                console.log(`🔄 Attempting automatic reconnection for user ${this.userId}...`);
                setTimeout(async () => {
                    try {
                        await this.start(false); // Try to reconnect without new QR
                        console.log(`✅ Auto-reconnection successful for user ${this.userId}`);
                    } catch (reconnectError) {
                        console.log(`❌ Auto-reconnection failed for user ${this.userId}: ${reconnectError.message}`);
                    }
                }, 3000); // Wait 3 seconds before reconnecting
            }
            
            // Re-throw the error so the caller knows it failed
            throw error;
        }
    }
    
    async disconnect() {
        // Clear all timers
        if (this.reconnectTimeout) {
            clearTimeout(this.reconnectTimeout);
            this.reconnectTimeout = null;
        }
        
        if (this.heartbeatInterval) {
            clearInterval(this.heartbeatInterval);
            this.heartbeatInterval = null;
        }
        
        if (this.sock) {
            await this.sock.logout();
        }
        
        this.isConnected = false;
        this.connectionState = 'disconnected';
        this.qrCodeData = null;
        this.pairingCode = null;
        this.sock = null;
        
        // Save disconnection state to database
        await this.saveSessionState();
    }
    
    startHeartbeat() {
        // Clear any existing heartbeat
        if (this.heartbeatInterval) {
            clearInterval(this.heartbeatInterval);
        }
        
        // Track consecutive heartbeat failures
        this.heartbeatFailures = this.heartbeatFailures || 0;
        
        // Send a heartbeat every 90 seconds to maintain connection
        this.heartbeatInterval = setInterval(async () => {
            if (this.isConnected && this.sock) {
                try {
                    // Simple ping to keep connection alive
                    console.log(`💓 Heartbeat for user ${this.userId} (failures: ${this.heartbeatFailures})`);
                    
                    // Railway-optimized simple heartbeat 
                    await Promise.race([
                        // Use simpler check that's more reliable on cloud
                        this.sock && this.sock.ws && this.sock.ws.readyState === 1 
                            ? Promise.resolve('connected')
                            : Promise.reject(new Error('Socket not ready')),
                        new Promise((_, reject) => 
                            setTimeout(() => reject(new Error('Heartbeat timeout')), 30000) // Railway needs longer timeout
                        )
                    ]);
                    
                    // Heartbeat success - reset failure count
                    this.heartbeatFailures = 0;
                    
                } catch (error) {
                    this.heartbeatFailures++;
                    console.log(`💔 Heartbeat failed for user ${this.userId} (${this.heartbeatFailures}/3): ${error.message}`);
                    
                    // Only disconnect after 3 consecutive failures
                    if (this.heartbeatFailures >= 3) {
                        console.log(`❌ Too many heartbeat failures for user ${this.userId}, marking as disconnected`);
                        
                        // Mark connection as failed
                        this.isConnected = false;
                        this.connectionState = 'disconnected';
                        
                        // Clear heartbeat to avoid conflicts
                        if (this.heartbeatInterval) {
                            clearInterval(this.heartbeatInterval);
                            this.heartbeatInterval = null;
                        }
                        
                        // Reset failure count
                        this.heartbeatFailures = 0;
                        
                        // Auto-reconnect for any heartbeat failure in Railway
                        console.log(`🔄 Heartbeat failed for user ${this.userId}, initiating auto-recovery...`);
                        
                        // Save disconnection state
                        await this.saveSessionState();
                        
                        setTimeout(async () => {
                            try {
                                console.log(`🔄 Auto-reconnecting user ${this.userId} after heartbeat failure...`);
                                await this.start(false); // Reconnect without forcing new QR
                            } catch (reconnectError) {
                                console.log(`❌ Auto-reconnect failed for user ${this.userId}:`, reconnectError.message);
                            }
                        }, 15000); // Wait 15 seconds before reconnecting in Railway
                    }
                }
            }
        }, 120000); // Every 2 minutes for Railway stability
    }
    
    async reconnect() {
        await this.disconnect();
        
        // ALWAYS force new QR on reconnect
        await this.start(true);
    }
    
    // Pairing code functionality removed - conflicts with WhatsApp Web connection
    
    async forceQR() {
        try {
            console.log(`🚀 Force QR requested for user ${this.userId}`);
            
            // Clear any existing timeouts
            if (this.reconnectTimeout) {
                clearTimeout(this.reconnectTimeout);
                this.reconnectTimeout = null;
            }
            
            // Disconnect if connected
            if (this.sock) {
                try {
                    // Properly close WebSocket without triggering error events
                    if (this.sock.ws && this.sock.ws.readyState === 1) {
                        this.sock.ws.close();
                    }
                    await this.sock.end();
                } catch (e) {
                    // Ignore expected WebSocket close errors during forced disconnect
                    console.log(`⚠️ Expected close error for user ${this.userId}: ${e.message}`);
                }
                this.sock = null;
            }
            
            this.isConnected = false;
            this.connectionState = 'generating_qr';
            this.qrCodeData = null;
            this.pairingCode = null;
            
            // Start with force new QR
            await this.start(true);
            
            // Wait for QR generation with timeout
            return new Promise((resolve, reject) => {
                let attempts = 0;
                const maxAttempts = 20; // 10 seconds max
                
                const checkQR = () => {
                    attempts++;
                    if (this.qrCodeData) {
                        resolve({ success: true, qrCode: this.qrCodeData });
                    } else if (attempts >= maxAttempts) {
                        reject(new Error('QR generation timeout'));
                    } else if (this.connectionState === 'error') {
                        reject(new Error('Connection error during QR generation'));
                    } else {
                        setTimeout(checkQR, 500);
                    }
                };
                
                // Start checking immediately
                setTimeout(checkQR, 100);
            });
            
        } catch (error) {
            console.error(`❌ Error in forceQR for user ${this.userId}:`, error);
            return { success: false, error: error.message };
        }
    }
    
    getStatus() {
        // Do a more thorough connection check
        let actuallyConnected = this.isConnected;
        
        // If we think we're connected but sock is null, we're actually disconnected
        if (this.isConnected && !this.sock) {
            console.log(`⚠️ User ${this.userId} marked as connected but socket is null, correcting status...`);
            this.isConnected = false;
            this.connectionState = 'disconnected';
            actuallyConnected = false;
        }
        
        return {
            userId: this.userId,
            connected: actuallyConnected,
            state: this.connectionState,
            qrCode: this.qrCodeData,
            qrCodeExists: !!this.qrCodeData,
            pairingCode: this.pairingCode,
            pairingCodeExists: !!this.pairingCode
        };
    }
}

// Função para obter ou criar sessão de usuário
function getUserSession(userId) {
    if (!userSessions.has(userId)) {
        const session = new UserWhatsAppSession(userId);
        userSessions.set(userId, session);
        
        // Check if session exists before forcing new QR
        const authPath = path.join(__dirname, 'sessions', session.authFolder);
        const hasExistingSession = fs.existsSync(authPath) && fs.existsSync(path.join(authPath, 'creds.json'));
        
        // Add random delay to prevent simultaneous connections
        const initDelay = Math.random() * 1000; // 0-1 second random delay
        
        setTimeout(() => {
            if (hasExistingSession) {
                console.log(`🔄 Found existing session for user ${userId}, attempting restore...`);
                session.start(false); // Try to restore existing session
            } else {
                console.log(`🆕 No existing session for user ${userId}, creating new...`);
                session.start(true); // Force new QR for first time
            }
        }, initDelay);
    }
    return userSessions.get(userId);
}

// API Endpoints
app.get('/status/:userId', async (req, res) => {
    const userId = req.params.userId;
    const session = getUserSession(userId);
    
    // Basic status
    const basicStatus = session.getStatus();
    
    // If claiming to be connected, do a real connection test
    if (basicStatus.connected && session.sock) {
        try {
            // Try a simple query to test if connection is really working
            await session.sock.query({
                tag: 'iq',
                attrs: {
                    type: 'get',
                    xmlns: 'w:profile:picture'
                }
            });
            // Connection test passed
        } catch (error) {
            console.log(`⚠️ Connection test failed for user ${userId}: ${error.message}`);
            // Connection test failed, update status
            session.isConnected = false;
            session.connectionState = 'disconnected';
            basicStatus.connected = false;
            basicStatus.state = 'disconnected';
        }
    }
    
    res.json({
        success: true,
        ...basicStatus
    });
});

app.get('/qr/:userId', async (req, res) => {
    try {
        const userId = req.params.userId;
        const session = getUserSession(userId);
        
        console.log(`📱 QR requested for user ${userId}, state: ${session.connectionState}, hasQR: ${!!session.qrCodeData}`);
        
        // If already connected, don't generate new QR
        if (session.isConnected) {
            return res.json({
                success: false,
                error: 'Already connected',
                connected: true
            });
        }
        
        // If QR exists and is fresh (not expired), return it
        if (session.qrCodeData && session.connectionState === 'qr_generated') {
            return res.json({
                success: true,
                qrCode: session.qrCodeData
            });
        }
        
        // Generate new QR
        try {
            const result = await session.forceQR();
            res.json(result);
        } catch (error) {
            console.error(`❌ QR generation failed for user ${userId}:`, error);
            res.json({
                success: false,
                message: 'Erro ao gerar QR Code',
                error: error.message
            });
        }
        
    } catch (error) {
        console.error('QR endpoint error:', error);
        res.json({
            success: false,
            error: 'Internal server error'
        });
    }
});

app.post('/send/:userId', async (req, res) => {
    try {
        const userId = req.params.userId;
        const { number, message } = req.body;
        
        const session = userSessions.get(userId);
        
        if (!session) {
            return res.json({
                success: false,
                error: 'Sessão não encontrada para este usuário'
            });
        }
        
        // More lenient connection check - if sock exists, try to send
        if (!session.sock) {
            return res.json({
                success: false,
                error: 'WhatsApp não conectado para este usuário'
            });
        }
        
        // If we think we're disconnected but socket exists, try to send anyway
        if (!session.isConnected) {
            console.log(`⚠️ User ${userId} marked as disconnected but socket exists, attempting message send...`);
        }
        
        const result = await session.sendMessage(number, message);
        
        res.json({
            success: true,
            messageId: result.key.id,
            response: result
        });
        
    } catch (error) {
        console.error('Erro ao enviar mensagem:', error);
        res.json({
            success: false,
            error: error.message
        });
    }
});

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
            message: 'WhatsApp desconectado para o usuário'
        });
    } catch (error) {
        res.json({
            success: false,
            error: error.message
        });
    }
});

app.post('/reconnect/:userId', async (req, res) => {
    try {
        const userId = req.params.userId;
        const session = getUserSession(userId);
        
        // Force reconnect always generates new QR
        await session.reconnect();
        
        res.json({
            success: true,
            message: 'Gerando novo QR Code...'
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
        const session = getUserSession(userId);
        
        const result = await session.forceQR();
        res.json(result);
    } catch (error) {
        res.json({
            success: false,
            error: error.message
        });
    }
});

// Endpoint para código de pareamento (POST)
app.post('/pairing-code/:userId', async (req, res) => {
    const userId = req.params.userId;
    const { phoneNumber } = req.body;
    
    if (!phoneNumber) {
        return res.status(400).json({
            success: false,
            error: 'Phone number is required'
        });
    }
    
    try {
        let session = getUserSession(userId);
        
        // Set phone number for pairing
        session.phoneNumber = phoneNumber;
        
        // If already connected, no pairing needed
        if (session.isConnected) {
            return res.json({
                success: false,
                error: 'WhatsApp is already connected for this user'
            });
        }
        
        // If already have a pairing code for this number, return it
        if (session.pairingCode && session.phoneNumber === phoneNumber) {
            return res.json({
                success: true,
                pairingCode: session.pairingCode,
                phoneNumber: phoneNumber
            });
        }
        
        // Start session to generate pairing code
        await session.start();
        
        // Wait for pairing code generation with timeout
        let attempts = 0;
        const maxAttempts = 20; // 10 seconds
        
        return new Promise((resolve) => {
            const checkPairingCode = () => {
                attempts++;
                
                if (session.pairingCode) {
                    resolve(res.json({
                        success: true,
                        pairingCode: session.pairingCode,
                        phoneNumber: phoneNumber
                    }));
                } else if (attempts >= maxAttempts) {
                    resolve(res.status(408).json({
                        success: false,
                        error: 'Timeout waiting for pairing code generation'
                    }));
                } else {
                    setTimeout(checkPairingCode, 500);
                }
            };
            
            checkPairingCode();
        });
        
    } catch (error) {
        console.error('❌ Error generating pairing code:', error);
        res.status(500).json({
            success: false,
            error: 'Failed to generate pairing code',
            details: error.message
        });
    }
});

// Endpoint para buscar código de pareamento existente (GET)
app.get('/pairing-code/:userId', (req, res) => {
    const userId = req.params.userId;
    const session = userSessions.get(userId);
    
    if (!session) {
        return res.status(404).json({
            success: false,
            error: 'User session not found'
        });
    }
    
    if (!session.pairingCode) {
        return res.json({
            success: false,
            error: 'No pairing code available. Generate one first with POST request.'
        });
    }
    
    res.json({
        success: true,
        pairingCode: session.pairingCode,
        phoneNumber: session.phoneNumber
    });
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
        timestamp: new Date().toISOString()
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

// Startup recovery system - restore all persistent sessions from database
const restorePersistedSessions = async () => {
    try {
        const client = await connectToDatabase();
        if (!client) return;

        const result = await client.query(
            'SELECT user_id, is_connected, connection_status FROM whatsapp_sessions WHERE is_connected = true OR connection_status = \'connected\''
        );

        console.log(`🔄 Found ${result.rows.length} sessions to restore from database`);
        
        for (const row of result.rows) {
            const userId = row.user_id.toString();
            
            if (!userSessions.has(userId)) {
                console.log(`🔄 Restoring session for user ${userId}...`);
                const session = new UserWhatsAppSession(userId);
                userSessions.set(userId, session);
                
                // Try to restore connection after a brief delay to avoid overwhelming the system
                setTimeout(() => {
                    session.start(false).catch(error => {
                        console.error(`❌ Failed to restore session for user ${userId}:`, error);
                    });
                }, Math.random() * 5000); // Random delay up to 5 seconds
            }
        }
    } catch (error) {
        console.error('❌ Error restoring persisted sessions:', error);
    }
};

// Enhanced auto-recovery system for Railway - check sessions every 3 minutes
setInterval(async () => {
    console.log(`🔍 Health check: ${userSessions.size} active sessions`);
    
    // Check database for sessions that should be recovered
    try {
        const client = await connectToDatabase();
        if (client) {
            const result = await client.query(
                'SELECT user_id FROM whatsapp_sessions WHERE is_connected = true AND connection_status = \'connected\''
            );
            
            for (const row of result.rows) {
                const userId = row.user_id.toString();
                const session = userSessions.get(userId);
                
                if (!session) {
                    console.log(`🔄 Found orphaned database session for user ${userId}, creating session...`);
                    const newSession = new UserWhatsAppSession(userId);
                    userSessions.set(userId, newSession);
                    
                    setTimeout(() => {
                        newSession.start(false).catch(error => {
                            console.error(`❌ Failed to recover orphaned session for user ${userId}:`, error);
                        });
                    }, Math.random() * 3000);
                } else if (!session.isConnected) {
                    console.log(`🔄 Database shows user ${userId} should be connected, attempting recovery...`);
                    const authPath = path.join(__dirname, 'sessions', session.authFolder);
                    const hasValidSession = fs.existsSync(authPath) && fs.existsSync(path.join(authPath, 'creds.json'));
                    
                    if (hasValidSession && !session.reconnectTimeout) {
                        session.start(false).catch(error => {
                            console.error(`❌ Failed to recover session for user ${userId}:`, error);
                        });
                    }
                }
            }
        }
    } catch (error) {
        console.error('❌ Error in health check:', error);
    }
    
    // Also check existing sessions
    userSessions.forEach((session, userId) => {
        if (!session.isConnected && session.connectionState === 'disconnected') {
            const authPath = path.join(__dirname, 'sessions', session.authFolder);
            const hasValidSession = fs.existsSync(authPath) && fs.existsSync(path.join(authPath, 'creds.json'));
            
            if (hasValidSession && !session.reconnectTimeout) {
                console.log(`🔄 Auto-recovering disconnected session for user ${userId}...`);
                session.start(false).catch(error => {
                    console.error(`❌ Auto-recovery failed for user ${userId}:`, error);
                });
            }
        }
    });
}, 180000); // 3 minutes for Railway

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

const PORT = 3001;
app.listen(PORT, () => {
    console.log(`🚀 Servidor Baileys Multi-User rodando na porta ${PORT}`);
    console.log(`✅ Sistema de recuperação automática ativo`);
    console.log(`💾 Sessões persistentes no PostgreSQL`);
    console.log(`📱 Use /qr/{userId} para gerar QR code`);
    console.log(`💬 Use POST /send/{userId} para enviar mensagens`);
    console.log(`📊 Use /status/{userId} para ver status da conexão`);
    
    // Restore persistent sessions on startup
    console.log('🔄 Restoring persistent sessions from database...');
    setTimeout(restorePersistedSessions, 3000); // Wait 3 seconds before starting recovery
});