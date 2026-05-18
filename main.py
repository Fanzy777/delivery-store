# ╔══════════════════════════════════════════════════════════╗
# ║        Delivery Store — bot + server в одном файле      ║
# ║  Запуск: python main.py                                  ║
# ╚══════════════════════════════════════════════════════════╝

import random, time, sqlite3, hashlib, os, base64
import telebot
from telebot import types
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import uvicorn

BOT_TOKEN    = "8733525214:AAHDa4BgY93m8XD-AL49PR164fPstXNLzp0"
ADMIN_ID     = 1387610058
CHANNEL_LINK = "https://t.me/delivstorenews"
MINI_APP_URL = os.getenv("MINI_APP_URL", "https://web-production-9fdbe.up.railway.app")
WEBHOOK_URL  = os.getenv("WEBHOOK_URL",  "https://web-production-9fdbe.up.railway.app")

def hash_pass(p): return hashlib.sha256(p.encode()).hexdigest()

def get_db():
    conn = sqlite3.connect("delivery_store.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    db = get_db()
    # Пользователи telegram (chat_id)
    db.execute("""CREATE TABLE IF NOT EXISTS tg_users (
        username TEXT PRIMARY KEY,
        chat_id  TEXT NOT NULL)""")
    # Аккаунты
    db.execute("""CREATE TABLE IF NOT EXISTS users (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_username  TEXT UNIQUE NOT NULL,
        nick         TEXT UNIQUE NOT NULL,
        password     TEXT NOT NULL,
        role         TEXT NOT NULL DEFAULT 'user',
        created_at   INTEGER NOT NULL)""")
    # Коды подтверждения
    db.execute("""CREATE TABLE IF NOT EXISTS codes (
        tg_username TEXT PRIMARY KEY,
        code        TEXT NOT NULL,
        purpose     TEXT NOT NULL DEFAULT 'register',
        expires_at  INTEGER NOT NULL)""")
    # Ожидание завершения регистрации
    db.execute("""CREATE TABLE IF NOT EXISTS verified_pending (
        tg_username TEXT PRIMARY KEY,
        verified_at INTEGER NOT NULL)""")
    # Приложения (одобренные)
    db.execute("""CREATE TABLE IF NOT EXISTS apps (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL,
        developer   TEXT NOT NULL,
        tg_username TEXT NOT NULL,
        description TEXT NOT NULL,
        version     TEXT NOT NULL,
        category    TEXT NOT NULL DEFAULT 'other',
        apk_data    TEXT,
        rating      REAL NOT NULL DEFAULT 0,
        installs    INTEGER NOT NULL DEFAULT 0,
        created_at  INTEGER NOT NULL)""")
    # Заявки на публикацию (на рассмотрении)
    db.execute("""CREATE TABLE IF NOT EXISTS submissions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL,
        developer   TEXT NOT NULL,
        tg_username TEXT NOT NULL,
        description TEXT NOT NULL,
        version     TEXT NOT NULL,
        category    TEXT NOT NULL DEFAULT 'other',
        apk_data    TEXT,
        status      TEXT NOT NULL DEFAULT 'pending',
        created_at  INTEGER NOT NULL)""")
    # Отзывы
    db.execute("""CREATE TABLE IF NOT EXISTS reviews (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        app_id     INTEGER NOT NULL,
        nick       TEXT NOT NULL,
        stars      INTEGER NOT NULL,
        text       TEXT NOT NULL,
        created_at INTEGER NOT NULL)""")
    db.commit(); db.close()

init_db()

# ── Bot ─────────────────────────────────────────
bot = telebot.TeleBot(BOT_TOKEN)

def start_inline():
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(
        types.InlineKeyboardButton("📢 Наш Телеграмм", url=CHANNEL_LINK),
        types.InlineKeyboardButton("🛍 Открыть магазин", web_app=types.WebAppInfo(url=MINI_APP_URL)),
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
        "👇 Нажми кнопку чтобы открыть магазин:",
        parse_mode="HTML", reply_markup=start_inline())

@bot.message_handler(commands=["help"])
def cmd_help(message):
    bot.send_message(message.chat.id,
        "ℹ️ <b>Помощь</b>\n\n• 📢 Наш канал — новости\n• 🛍 Магазин — открыть приложение\n\n/start /help",
        parse_mode="HTML", reply_markup=start_inline())

# ── FastAPI ─────────────────────────────────────
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/")
def serve_index(): return FileResponse("index.html")

@app.post("/webhook")
async def webhook(request: Request):
    update = telebot.types.Update.de_json(await request.json())
    bot.process_new_updates([update])
    return {"ok": True}

@app.on_event("startup")
def on_startup():
    bot.remove_webhook(); time.sleep(0.5)
    bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
    print(f"🤖 Webhook: {WEBHOOK_URL}/webhook")

# ── Pydantic models ──────────────────────────────
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
class SubmitAppReq(BaseModel):
    nick: str
    name: str
    description: str
    version: str
    category: str
    apk_data: Optional[str] = None  # base64
class ReviewReq(BaseModel):
    app_id: int; nick: str; stars: int; text: str
class ApproveReq(BaseModel):
    submission_id: int
class RejectReq(BaseModel):
    submission_id: int
class SetRoleReq(BaseModel):
    nick: str; role: str

# ── Helpers ──────────────────────────────────────
def bot_send(tg_username, text):
    db = get_db()
    row = db.execute("SELECT chat_id FROM tg_users WHERE username=?", (tg_username,)).fetchone()
    db.close()
    if not row: raise HTTPException(400, "user_not_found")
    try: bot.send_message(int(row["chat_id"]), text, parse_mode="HTML")
    except: raise HTTPException(400, "send_error")

def make_code(tg, purpose):
    code = str(random.randint(100000, 999999))
    db = get_db()
    db.execute("INSERT OR REPLACE INTO codes (tg_username,code,purpose,expires_at) VALUES (?,?,?,?)",
               (tg, code, purpose, int(time.time())+300))
    db.commit(); db.close()
    return code

def get_user_by_nick(nick):
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE nick=?", (nick,)).fetchone()
    db.close()
    return row

# ── Auth endpoints ───────────────────────────────
@app.post("/send-code")
def send_code(req: TgReq):
    tg = req.tg_username.lower().replace("@","")
    db = get_db()
    if db.execute("SELECT id FROM users WHERE tg_username=?", (tg,)).fetchone():
        db.close(); raise HTTPException(400, "already_registered")
    db.close()
    code = make_code(tg, "register")
    bot_send(tg, f"🔐 <b>Delivery Store</b>\n\nКод регистрации:\n\n<code>{code}</code>\n\n⏱ Действует 5 минут")
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
        db.close()
        raise HTTPException(400, "not_verified")

    if db.execute("SELECT 1 FROM users WHERE nick=?", (req.nick,)).fetchone():
        db.close()
        raise HTTPException(400, "nick_taken")

    if len(req.nick) < 3:
        db.close()
        raise HTTPException(422, "nick_too_short")

    if len(req.password) < 6:
        db.close()
        raise HTTPException(422, "pass_too_short")

    # Первый зарегистрированный пользователь становится admin
    count = db.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    role = "admin" if count == 0 else "user"

    db.execute(
        "INSERT INTO users (tg_username,nick,password,role,created_at) VALUES (?,?,?,?,?)",
        (tg, req.nick, hash_pass(req.password), role, int(time.time()))
    )

    db.execute("DELETE FROM verified_pending WHERE tg_username=?", (tg,))
    db.commit()
    db.close()

    bot_send(
        tg,
        "✅ <b>Регистрация успешна!</b>\n\nДобро пожаловать в <b>Delivery Store</b>! 🚀"
    )

    return {"ok": True,"nick": req.nick,"tg_username": tg,"role": role}

@app.post("/login")
def login(req: LoginReq):
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE nick=?", (req.nick,)).fetchone()
    db.close()
    if not row: raise HTTPException(400, "not_found")
    if row["password"] != hash_pass(req.password): raise HTTPException(400, "wrong_password")
    return {"ok": True, "nick": row["nick"], "tg_username": row["tg_username"], "role": row["role"]}

@app.post("/send-reset-code")
def send_reset(req: TgReq):
    tg = req.tg_username.lower().replace("@","")
    db = get_db()
    if not db.execute("SELECT 1 FROM users WHERE tg_username=?", (tg,)).fetchone():
        db.close(); raise HTTPException(400, "not_found")
    db.close()
    code = make_code(tg, "reset")
    bot_send(tg, f"🔑 <b>Сброс пароля</b>\n\nКод:\n\n<code>{code}</code>\n\n⏱ Действует 5 минут")
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
    if len(req.new_password) < 6: raise HTTPException(422, "pass_too_short")
    db.execute("UPDATE users SET password=? WHERE tg_username=?", (hash_pass(req.new_password), tg))
    db.execute("DELETE FROM codes WHERE tg_username=?", (tg,))
    db.commit(); db.close()
    return {"ok": True}

# ── Apps endpoints ───────────────────────────────
@app.get("/apps")
def get_apps():
    db = get_db()
    rows = db.execute("SELECT * FROM apps ORDER BY installs DESC").fetchall()
    db.close()
    return {"apps": [dict(r) for r in rows]}

@app.post("/apps/submit")
def submit_app(req: SubmitAppReq):
    user = get_user_by_nick(req.nick)
    if not user: raise HTTPException(400, "user_not_found")
    if user["role"] not in ("dev", "admin"): raise HTTPException(403, "not_developer")
    if not req.name or not req.description or not req.version:
        raise HTTPException(422, "missing_fields")
    db = get_db()
    db.execute(
        "INSERT INTO submissions (name,developer,tg_username,description,version,category,apk_data,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (req.name, req.nick, user["tg_username"], req.description, req.version, req.category, req.apk_data, "pending", int(time.time()))
    )
    db.commit(); db.close()
    # Уведомляем админа
    try:
        bot.send_message(ADMIN_ID,
            f"📬 <b>Новая заявка на публикацию</b>\n\n"
            f"📦 Название: {req.name}\n"
            f"👤 Разработчик: {req.nick}\n"
            f"📝 Описание: {req.description[:100]}...\n"
            f"🏷 Версия: {req.version}\n\n"
            f"Зайди в админ панель для одобрения.",
            parse_mode="HTML")
    except: pass
    return {"ok": True}

@app.get("/apps/submissions")
def get_submissions():
    db = get_db()
    rows = db.execute("SELECT * FROM submissions ORDER BY created_at DESC").fetchall()
    db.close()
    return {"submissions": [dict(r) for r in rows]}

@app.post("/apps/approve")
def approve_app(req: ApproveReq):
    db = get_db()
    sub = db.execute("SELECT * FROM submissions WHERE id=?", (req.submission_id,)).fetchone()
    if not sub: db.close(); raise HTTPException(404, "not_found")
    db.execute(
        "INSERT INTO apps (name,developer,tg_username,description,version,category,apk_data,rating,installs,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (sub["name"], sub["developer"], sub["tg_username"], sub["description"], sub["version"], sub["category"], sub["apk_data"], 0, 0, int(time.time()))
    )
    db.execute("UPDATE submissions SET status='approved' WHERE id=?", (req.submission_id,))
    db.commit()
    # Уведомляем разработчика
    try:
        bot_send(sub["tg_username"],
            f"✅ <b>Ваше приложение одобрено!</b>\n\n"
            f"📦 <b>{sub['name']}</b> теперь доступно в Delivery Store 🎉")
    except: pass
    db.close()
    return {"ok": True}

@app.post("/apps/reject")
def reject_app(req: RejectReq):
    db = get_db()
    sub = db.execute("SELECT * FROM submissions WHERE id=?", (req.submission_id,)).fetchone()
    if not sub: db.close(); raise HTTPException(404, "not_found")
    db.execute("UPDATE submissions SET status='rejected' WHERE id=?", (req.submission_id,))
    db.commit()
    try:
        bot_send(sub["tg_username"],
            f"❌ <b>Заявка отклонена</b>\n\n"
            f"Приложение <b>{sub['name']}</b> не прошло модерацию.\n"
            f"Свяжитесь с администратором для уточнения причин.")
    except: pass
    db.close()
    return {"ok": True}

@app.delete("/apps/{app_id}")
def delete_app(app_id: int):
    db = get_db()
    db.execute("DELETE FROM apps WHERE id=?", (app_id,))
    db.commit(); db.close()
    return {"ok": True}

# ── Reviews ──────────────────────────────────────
@app.get("/apps/{app_id}/reviews")
def get_reviews(app_id: int):
    db = get_db()
    rows = db.execute("SELECT * FROM reviews WHERE app_id=? ORDER BY created_at DESC", (app_id,)).fetchall()
    db.close()
    return {"reviews": [dict(r) for r in rows]}

@app.post("/apps/review")
def add_review(req: ReviewReq):
    if not 1 <= req.stars <= 5: raise HTTPException(422, "invalid_stars")
    db = get_db()
    db.execute("INSERT INTO reviews (app_id,nick,stars,text,created_at) VALUES (?,?,?,?,?)",
               (req.app_id, req.nick, req.stars, req.text, int(time.time())))
    # Пересчитываем рейтинг
    rows = db.execute("SELECT AVG(stars) as avg FROM reviews WHERE app_id=?", (req.app_id,)).fetchone()
    db.execute("UPDATE apps SET rating=? WHERE id=?", (round(rows["avg"], 1), req.app_id))
    db.commit(); db.close()
    return {"ok": True}

# ── Admin: users ─────────────────────────────────
@app.get("/admin/users")
def admin_get_users():
    db = get_db()
    rows = db.execute("SELECT id,nick,tg_username,role,created_at FROM users ORDER BY created_at DESC").fetchall()
    db.close()
    return {"users": [dict(r) for r in rows]}

@app.post("/admin/set-role")
def admin_set_role(req: SetRoleReq):
    if req.role not in ("user", "dev", "admin"): raise HTTPException(422, "invalid_role")
    db = get_db()
    db.execute("UPDATE users SET role=? WHERE nick=?", (req.role, req.nick))
    db.commit(); db.close()
    return {"ok": True}

# ── Run ──────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    print(f"🌐 Сервер: http://0.0.0.0:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
