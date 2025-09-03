// whatsapp_baileys_multi.mjs — ESM forçado por extensão .mjs
import express from "express";
import cors from "cors";
import morgan from "morgan";
import fs from "fs";
import fsp from "fs/promises";
import path from "path";
import { fileURLToPath } from "url";
import qrcode from "qrcode";
import P from "pino";
import {
  makeWASocket,
  DisconnectReason,
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  Browsers,
} from "@whiskeysockets/baileys";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const PORT = Number(process.env.WA_PORT || process.env.PORT || 3001);
const SESSIONS_DIR =
  process.env.SESSIONS_DIR || path.join(__dirname, "sessions");

if (!fs.existsSync(SESSIONS_DIR)) {
  fs.mkdirSync(SESSIONS_DIR, { recursive: true });
}

const app = express();
app.use(cors());
app.use(express.json({ limit: "1mb" }));
app.use(morgan("tiny"));

const instances = new Map(); // id -> { sock, qr, lastQrAt, connected, state, saveCreds }

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const j = (...xs) => path.join(...xs);
const jidFromNumber = (n) => `${(n || "").replace(/\D/g, "")}@s.whatsapp.net`;

async function destroyAuth(id) {
  const p = j(SESSIONS_DIR, String(id));
  if (fs.existsSync(p)) {
    await fsp.rm(p, { recursive: true, force: true });
  }
}

async function ensureInstance(id, { forceNew = false } = {}) {
  id = String(id);

  if (forceNew) {
    await destroyAuth(id);
    if (instances.has(id)) {
      try {
        await instances.get(id).sock?.logout?.();
      } catch {}
      instances.delete(id);
    }
  }

  if (instances.has(id)) return instances.get(id);

  const authPath = j(SESSIONS_DIR, id);
  const { state, saveCreds } = await useMultiFileAuthState(authPath);
  const { version } = await fetchLatestBaileysVersion();

  const logger = P({ level: process.env.WA_LOG_LEVEL || "warn" });

  const sock = makeWASocket({
    version,
    logger,
    auth: state,
    printQRInTerminal: false, // vamos expor via HTTP
    browser: Browsers.appropriate("Chrome"),
    syncFullHistory: false,
  });

  const data = {
    sock,
    qr: null,
    lastQrAt: null,
    connected: false,
    state: "starting",
    saveCreds,
  };
  instances.set(id, data);

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", async ({ connection, lastDisconnect, qr }) => {
    if (qr) {
      try {
        data.qr = await qrcode.toDataURL(qr, { margin: 1, scale: 6 });
        data.lastQrAt = Date.now();
        data.state = "qr";
      } catch {
        data.qr = null;
      }
    }

    if (connection === "open") {
      data.connected = true;
      data.state = "open";
      data.qr = null;
    }

    if (connection === "close") {
      data.connected = false;
      data.state = "close";
      const reason =
        lastDisconnect?.error?.output?.statusCode ||
        lastDisconnect?.error?.code;

      if (reason !== DisconnectReason.loggedOut) {
        await wait(800);
        try {
          ensureInstance(id);
        } catch {}
      } else {
        try {
          await destroyAuth(id);
        } catch {}
      }
    }
  });

  return data;
}

/* ============== ROTAS HTTP ============== */

app.get("/health", (_req, res) => {
  res.json({
    success: true,
    service: "whatsapp-baileys",
    port: PORT,
    sessionsDir: SESSIONS_DIR,
    instances: [...instances.keys()],
  });
});

app.get("/status/:id", (req, res) => {
  const { id } = req.params;
  const inst = instances.get(String(id));
  res.json({
    success: true,
    exists: !!inst,
    connected: !!inst?.connected,
    state: inst?.state || "none",
    qrCode: inst?.qr || null,
    lastQrAt: inst?.lastQrAt || null,
  });
});

app.get("/qr/:id", (req, res) => {
  const { id } = req.params;
  const inst = instances.get(String(id));
  if (!inst?.qr) return res.json({ success: false, error: "QR not available" });
  res.json({ success: true, qrCode: inst.qr, lastQrAt: inst.lastQrAt });
});

app.post("/reconnect/:id", async (req, res) => {
  const { id } = req.params;
  try {
    await ensureInstance(id);
    res.json({ success: true, message: "Reconnecting/connecting…" });
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

app.post("/force-qr/:id", async (req, res) => {
  const { id } = req.params;
  res.json({ success: true, message: "Forcing new QR in background" });
  try {
    await ensureInstance(id, { forceNew: true });
  } catch {}
});

app.post("/pairing-code/:id", async (req, res) => {
  const { id } = req.params;
  const phone = (req.body?.phoneNumber || "").replace(/\D/g, "");
  if (!phone)
    return res.status(400).json({ success: false, error: "phoneNumber required" });

  try {
    const inst = await ensureInstance(id);
    if (typeof inst.sock?.requestPairingCode !== "function") {
      return res.json({
        success: false,
        error: "pairing-code not supported in this version",
      });
    }
    const code = await inst.sock.requestPairingCode(phone);
    res.json({ success: true, pairingCode: code });
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

app.post("/send/:id", async (req, res) => {
  const { id } = req.params;
  const { number, message } = req.body || {};
  if (!number || !message) {
    return res
      .status(400)
      .json({ success: false, error: "number and message required" });
  }
  try {
    const inst = await ensureInstance(id);
    const jid = jidFromNumber(number);
    const sent = await inst.sock.sendMessage(jid, { text: message });
    res.json({ success: true, messageId: sent?.key?.id || null });
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

app.post("/disconnect/:id", async (req, res) => {
  const { id } = req.params;
  try {
    const inst = instances.get(String(id));
    if (inst?.sock?.logout) await inst.sock.logout();
    instances.delete(String(id));
    await destroyAuth(id);
    res.json({ success: true });
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

app.get("/", (_req, res) => {
  res.json({ ok: true, service: "whatsapp-baileys", port: PORT });
});

process.on("unhandledRejection", (err) => console.error("UnhandledRejection:", err));
process.on("uncaughtException", (err) => console.error("UncaughtException:", err));

app.listen(PORT, () => {
  console.log(`WhatsApp service listening on :${PORT}`);
});
