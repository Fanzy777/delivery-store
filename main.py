# ╔══════════════════════════════════════════════════════════╗
# ║        Delivery Store — bot + server в одном файле      ║
# ║  Запуск: python main.py                                  ║
# ╚══════════════════════════════════════════════════════════╝

import random, time, sqlite3, threading, hashlib
import telebot
from telebot import types
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn

BOT_TOKEN    = "8733525214:AAHDa4BgY93m8XD-AL49PR164fPstXNLzp0"
ADMIN_ID     = 1387610058
CHANNEL_LINK = "https://t.me/delivstorenews"
MINI_APP_URL = "https://web-production-9fdbe.up.railway.app"

def hash_pass(p):
    return hashlib.sha256(p.encode()).hexdigest()

def get_db():
    conn = sqlite3.connect("delivery_store.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    db = get_db()
    db.execute("""CREATE TABLE IF NOT EXISTS tg_users (
        username TEXT PRIMARY KEY, chat_id TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_username TEXT UNIQUE NOT NULL,
        nick TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        created_at INTEGER NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS codes (
        tg_username TEXT PRIMARY KEY,
        code TEXT NOT NULL,
        purpose TEXT NOT NULL DEFAULT 'register',
        expires_at INTEGER NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS verified_pending (
        tg_username TEXT PRIMARY KEY, verified_at INTEGER NOT NULL)""")
    db.commit(); db.close()

init_db()

bot = telebot.TeleBot(BOT_TOKEN)

def start_inline():
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(
        types.InlineKeyboardButton("📢 Наш Телеграмм", url=CHANNEL_LINK),
        types.InlineKeyboardButton("🛍 Меню", web_app=types.WebAppInfo(url=MINI_APP_URL)),
    )
    return m

@bot.message_handler(commands=["start"])
def cmd_start(message):
    if message.from_user.username:
        db = get_db()
        db.execute("INSERT OR REPLACE INTO tg_users (username, chat_id) VALUES (?, ?)",
                   (message.from_user.username.lower(), str(message.chat.id)))
        db.commit(); db.close()
    bot.send_message(message.chat.id,
        "👋 Добро пожаловать в <b>Delivery Store</b>!\n\n"
        "Магазин Telegram Mini Apps нового поколения.\n\n"
        "👇 Выбери что тебя интересует:",
        parse_mode="HTML", reply_markup=start_inline())

@bot.message_handler(commands=["help"])
def cmd_help(message):
    bot.send_message(message.chat.id,
        "ℹ️ <b>Помощь</b>\n\n• 📢 Наш Телеграмм — новости\n• 🛍 Меню — магазин\n\n/start /help",
        parse_mode="HTML", reply_markup=start_inline())

def run_bot():
    print("🤖 Бот запущен...")
    bot.infinity_polling()

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/")
def serve_index():
    return FileResponse("index.html")

class TgReq(BaseModel):
    tg_username: str
class VerifyReq(BaseModel):
    tg_username: str; code: str
class FinishReq(BaseModel):
    tg_username: str; nick: str; password: str
class LoginReq(BaseModel):
    nick: str; password: str
class ResetReq(BaseModel):
    tg_username: str; code: str; new_password: str

def bot_send(tg_username, text):
    db = get_db()
    row = db.execute("SELECT chat_id FROM tg_users WHERE username = ?", (tg_username,)).fetchone()
    db.close()
    if not row:
        raise HTTPException(400, "user_not_found")
    try:
        bot.send_message(int(row["chat_id"]), text, parse_mode="HTML")
    except:
        raise HTTPException(400, "send_error")

def make_code(tg, purpose):
    code = str(random.randint(100000, 999999))
    db = get_db()
    db.execute("INSERT OR REPLACE INTO codes (tg_username,code,purpose,expires_at) VALUES (?,?,?,?)",
               (tg, code, purpose, int(time.time())+300))
    db.commit(); db.close()
    return code

@app.post("/send-code")
def send_code(req: TgReq):
    tg = req.tg_username.lower().replace("@","")
    db = get_db()
    ex = db.execute("SELECT id FROM users WHERE tg_username=?", (tg,)).fetchone()
    db.close()
    if ex: raise HTTPException(400, "already_registered")
    code = make_code(tg, "register")
    bot_send(tg, f"🔐 <b>Delivery Store</b>\n\nКод регистрации:\n\n<code>{code}</code>\n\n⏱ 5 минут")
    return {"ok": True}

@app.post("/verify-code")
def verify_code(req: VerifyReq):
    tg = req.tg_username.lower().replace("@","")
    db = get_db()
    row = db.execute("SELECT code,expires_at FROM codes WHERE tg_username=? AND purpose='register'", (tg,)).fetchone()
    if not row: db.close(); raise HTTPException(400, "no_code")
    if int(time.time()) > row["expires_at"]:
        db.execute("DELETE FROM codes WHERE tg_username=?", (tg,)); db.commit(); db.close()
        raise HTTPException(400, "expired")
    if row["code"] != req.code.strip(): db.close(); raise HTTPException(400, "wrong_code")
    db.execute("DELETE FROM codes WHERE tg_username=?", (tg,))
    db.execute("INSERT OR REPLACE INTO verified_pending (tg_username,verified_at) VALUES (?,?)", (tg, int(time.time())))
    db.commit(); db.close()
    return {"ok": True}

@app.post("/finish-register")
def finish_register(req: FinishReq):
    tg = req.tg_username.lower().replace("@","")
    db = get_db()
    if not db.execute("SELECT 1 FROM verified_pending WHERE tg_username=?", (tg,)).fetchone():
        db.close(); raise HTTPException(400, "not_verified")
    if db.execute("SELECT 1 FROM users WHERE nick=?", (req.nick,)).fetchone():
        db.close(); raise HTTPException(400, "nick_taken")
    db.execute("INSERT INTO users (tg_username,nick,password,created_at) VALUES (?,?,?,?)",
               (tg, req.nick, hash_pass(req.password), int(time.time())))
    db.execute("DELETE FROM verified_pending WHERE tg_username=?", (tg,))
    db.commit(); db.close()
    bot_send(tg, "✅ <b>Регистрация успешна!</b>\n\nДобро пожаловать в <b>Delivery Store</b>! 🚀")
    return {"ok": True, "nick": req.nick, "tg_username": tg}

@app.post("/login")
def login(req: LoginReq):
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE nick=?", (req.nick,)).fetchone()
    db.close()
    if not row: raise HTTPException(400, "not_found")
    if row["password"] != hash_pass(req.password): raise HTTPException(400, "wrong_password")
    return {"ok": True, "nick": row["nick"], "tg_username": row["tg_username"]}

@app.post("/send-reset-code")
def send_reset(req: TgReq):
    tg = req.tg_username.lower().replace("@","")
    db = get_db()
    if not db.execute("SELECT 1 FROM users WHERE tg_username=?", (tg,)).fetchone():
        db.close(); raise HTTPException(400, "not_found")
    db.close()
    code = make_code(tg, "reset")
    bot_send(tg, f"🔑 <b>Delivery Store — Сброс пароля</b>\n\nКод:\n\n<code>{code}</code>\n\n⏱ 5 минут")
    return {"ok": True}

@app.post("/reset-password")
def reset_password(req: ResetReq):
    tg = req.tg_username.lower().replace("@","")
    db = get_db()
    row = db.execute("SELECT code,expires_at FROM codes WHERE tg_username=? AND purpose='reset'", (tg,)).fetchone()
    if not row: db.close(); raise HTTPException(400, "no_code")
    if int(time.time()) > row["expires_at"]:
        db.execute("DELETE FROM codes WHERE tg_username=?", (tg,)); db.commit(); db.close()
        raise HTTPException(400, "expired")
    if row["code"] != req.code.strip(): db.close(); raise HTTPException(400, "wrong_code")
    db.execute("UPDATE users SET password=? WHERE tg_username=?", (hash_pass(req.new_password), tg))
    db.execute("DELETE FROM codes WHERE tg_username=?", (tg,))
    db.commit(); db.close()
    return {"ok": True}

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    print("🌐 Сервер: http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
