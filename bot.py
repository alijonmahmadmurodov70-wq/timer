import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from telethon.tl.functions.account import UpdateProfileRequest

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_ID    = int(os.getenv("API_ID", "0"))
API_HASH  = os.getenv("API_HASH", "")
TZ_OFFSET = int(os.getenv("TIMEZONE", "5"))

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())

# uid -> {
#   "accounts": [{"client": ..., "nom": ..., "telefon": ..., "task": ..., "shablon": 1}],
#   "hash": ..., "telefon": ..., "shablon": 1, "client": ...  (login jarayoni uchun)
# }
users: dict[int, dict] = {}


# ── FSM ───────────────────────────────────────────────────────────────────────

class Login(StatesGroup):
    telefon = State()
    kod     = State()
    parol   = State()


# ── 10 ta shablon ─────────────────────────────────────────────────────────────

SHABLONLAR = [
    ("1", "🕐 Oddiy",       "🕐 {HH}:{MM}:{SS}"),
    ("2", "🌙 Kun vaqti",   "{VAQT} {HH}:{MM}"),
    ("3", "⏰ Analog",      "{ANALOG} {HH}:{MM}"),
    ("4", "🔢 To'liq",      "⏱ {HH}:{MM}:{SS} UTC+{TZ}"),
    ("5", "🌸 Gul",         "🌸 {HH}:{MM} 🌸"),
    ("6", "⚡ Zamonaviy",   "⚡{HH}:{MM}:{SS}⚡"),
    ("7", "🎯 Minimal",     "{HH}:{MM}"),
    ("8", "💎 12 soat",     "💎 {H12}:{MM} {AMPM} 💎"),
    ("9", "🌊 To'lqin",     "🌊 {BAR} {HH}:{MM}"),
    ("10","🔥 Olov",        "🔥{HH}:{MM}:{SS}🔥"),
]


def hozirgi_vaqt() -> datetime:
    tz = timezone(timedelta(hours=TZ_OFFSET))
    return datetime.now(tz)


def soat_formatlash(shablon_idx: int) -> str:
    now  = hozirgi_vaqt()
    h    = now.hour
    m    = now.minute
    s    = now.second
    HH   = f"{h:02d}"
    MM   = f"{m:02d}"
    SS   = f"{s:02d}"
    H12  = str(h % 12 or 12)
    AMPM = "AM" if h < 12 else "PM"

    analog = ["🕛","🕐","🕑","🕒","🕓","🕔","🕕","🕖","🕗","🕘","🕙","🕚"][h % 12]

    if 6 <= h < 12:
        vaqt = "🌅 Erta"
    elif 12 <= h < 17:
        vaqt = "☀️ Kunduz"
    elif 17 <= h < 21:
        vaqt = "🌇 Kech"
    else:
        vaqt = "🌙 Kecha"

    dolgan  = m // 6
    bar     = "▓" * dolgan + "░" * (10 - dolgan)

    template = SHABLONLAR[shablon_idx - 1][2]
    return (template
            .replace("{HH}", HH)
            .replace("{MM}", MM)
            .replace("{SS}", SS)
            .replace("{H12}", H12)
            .replace("{AMPM}", AMPM)
            .replace("{ANALOG}", analog)
            .replace("{VAQT}", vaqt)
            .replace("{BAR}", bar)
            .replace("{TZ}", str(TZ_OFFSET)))


# ── Klaviatura ─────────────────────────────────────────────────────────────────

def ikb(rows):
    keyboard = []
    for row in rows:
        kb_row = []
        for item in row:
            if isinstance(item, tuple):
                kb_row.append(InlineKeyboardButton(text=item[0], callback_data=item[1]))
            else:
                kb_row.append(item)
        keyboard.append(kb_row)
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def bosh_kb(uid: int) -> InlineKeyboardMarkup:
    accounts  = users.get(uid, {}).get("accounts", [])
    aktiv_son = sum(1 for a in accounts if a.get("task") and not a["task"].done())
    rows = [
        [("➕ Akkount qo'shish", "start_vc")],
    ]
    if accounts:
        rows.append([("📋 Akkountlar", "akk_royxat")])
    if aktiv_son > 0:
        rows.append([("⏹ Hammasini to'xtatish", "stop_all")])
        rows.append([("🎨 Shablon o'zgartirish", "shablon_menu")])
    else:
        if accounts:
            rows.append([("▶️ Hammasini boshlash", "start_all")])
        rows.append([("🎨 Shablon tanlash", "shablon_menu")])
    return ikb(rows)


def shablon_kb() -> InlineKeyboardMarkup:
    keyboard = []
    for i in range(0, 10, 2):
        s1 = SHABLONLAR[i]
        s2 = SHABLONLAR[i + 1]
        row = [
            InlineKeyboardButton(
                text=s1[1] + ": " + soat_formatlash(i + 1),
                callback_data="sh:" + s1[0]
            ),
            InlineKeyboardButton(
                text=s2[1] + ": " + soat_formatlash(i + 2),
                callback_data="sh:" + s2[0]
            ),
        ]
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# ── /start ─────────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    uid = msg.from_user.id
    if uid not in users:
        users[uid] = {"accounts": [], "shablon": 1}

    accounts = users[uid].get("accounts", [])
    aktiv_son = sum(1 for a in accounts if a.get("task") and not a["task"].done())

    matn = "⏰ <b>ClockBot</b>\n\n"
    if accounts:
        matn += "<b>Akkountlar ({} ta, {} aktiv):</b>\n".format(len(accounts), aktiv_son)
        for i, a in enumerate(accounts):
            aktiv = a.get("task") and not a["task"].done()
            belgi = "✅" if aktiv else "⏸"
            matn += "{}. {} {}\n".format(i+1, belgi, a.get("nom") or a.get("telefon",""))
        matn += "\n"
    else:
        matn += "Hech qanday akkount ulanmagan.\nBio'ingizga real-time soat qo'ying! 🔄\n\n"

    await msg.answer(matn, reply_markup=bosh_kb(uid), parse_mode="HTML")


@dp.callback_query(F.data == "back")
async def back(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    uid   = cb.from_user.id
    aktiv = (uid in users
             and users[uid].get("task")
             and not users[uid]["task"].done())
    if aktiv:
        shablon = users[uid].get("shablon", 1)
        nom     = SHABLONLAR[shablon - 1][1]
        namuna  = soat_formatlash(shablon)
        matn    = "✅ Ishlayapti!\nShablon: <b>{}</b>\nHozir: <code>{}</code>".format(nom, namuna)
    else:
        matn = "⏰ <b>ClockBot</b>\n\nBoshlash uchun ▶️ bosing."
    try:
        await cb.message.edit_text(matn, reply_markup=bosh_kb(uid), parse_mode="HTML")
    except TelegramBadRequest:
        await cb.message.answer(matn, reply_markup=bosh_kb(uid), parse_mode="HTML")


# ── Login ──────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "start_vc")
async def start_vc(cb: CallbackQuery, state: FSMContext):
    uid = cb.from_user.id
    if uid in users and users[uid].get("client"):
        await _task_boshlash(uid)
        await cb.answer("▶️ Boshlandi!")
        try:
            shablon = users[uid].get("shablon", 1)
            await cb.message.edit_text(
                "✅ Ishlayapti!\nShablon: <b>{}</b>".format(SHABLONLAR[shablon-1][1]),
                reply_markup=bosh_kb(uid), parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass
        return

    try:
        await cb.message.edit_text(
            "📱 Telefon raqamingizni kiriting:\n<code>+998901234567</code>",
            parse_mode="HTML"
        )
    except TelegramBadRequest:
        await cb.message.answer(
            "📱 Telefon raqamingizni kiriting:\n<code>+998901234567</code>",
            parse_mode="HTML"
        )
    await state.set_state(Login.telefon)


@dp.message(Login.telefon)
async def telefon_qabul(msg: Message, state: FSMContext):
    telefon = msg.text.strip()
    if not telefon.startswith("+"):
        return await msg.answer("❌ Format: +998901234567")

    await msg.answer("⏳ Kod yuborilmoqda...")

    # O'zbekiston +998 → DC5, boshqalar → DC1
    dc_id = 5 if telefon.startswith("+998") else 1

    client = TelegramClient(
        StringSession(), API_ID, API_HASH,
        device_model="Samsung Galaxy S23",
        system_version="Android 13",
        app_version="10.0.1",
        connection_retries=5,
        retry_delay=3,
    )
    try:
        await client.connect()
        # To'g'ri DC ga o'tish
        await client._switch_dc(dc_id)
        natija = await client.send_code_request(telefon)

        uid = msg.from_user.id
        if uid not in users:
            users[uid] = {"accounts": [], "shablon": 1}
        # Login jarayoni uchun vaqtincha saqlaymiz
        users[uid]["_client"]  = client
        users[uid]["_telefon"] = telefon
        users[uid]["_hash"]    = natija.phone_code_hash

        await state.set_state(Login.kod)

        tur = natija.type.__class__.__name__
        if "Sms" in tur:
            qaerga = "📱 SMS ga"
        elif "App" in tur:
            qaerga = "📲 Telegram ilovaga"
        else:
            qaerga = "📨"
        await msg.answer("{} kod yuborildi! Kodni kiriting:".format(qaerga))

    except FloodWaitError as e:
        try: await client.disconnect()
        except: pass
        await state.clear()
        d = e.seconds // 60 + 1
        await msg.answer("⏳ FloodWait: {} daqiqa kuting.".format(d))
    except Exception as e:
        try: await client.disconnect()
        except: pass
        await state.clear()
        await msg.answer("❌ Xato: {}".format(str(e)[:200]))


async def kod_qabul(msg: Message, state: FSMContext):
    uid    = msg.from_user.id
    ma     = users.get(uid, {})
    client = ma.get("_client")
    if not client:
        await state.clear()
        return await msg.answer("❌ Sessiya topilmadi. /start bosing.")
    try:
        await client.sign_in(ma["_telefon"], msg.text.strip(), phone_code_hash=ma["_hash"])
        await _login_ok(msg, state, uid, client)
    except SessionPasswordNeededError:
        await state.set_state(Login.parol)
        await msg.answer("🔐 2FA parolini kiriting:")
    except Exception as e:
        await state.clear()
        await msg.answer("❌ Kod xato: {}".format(e))


@dp.message(Login.parol)
async def parol_qabul(msg: Message, state: FSMContext):
    uid    = msg.from_user.id
    client = users.get(uid, {}).get("_client")
    if not client:
        await state.clear()
        return await msg.answer("❌ Sessiya topilmadi.")
    try:
        await client.sign_in(password=msg.text.strip())
        await _login_ok(msg, state, uid, client)
    except Exception as e:
        await state.clear()
        await msg.answer("❌ Parol xato: {}".format(e))


async def _login_ok(msg: Message, state: FSMContext, uid: int, client: TelegramClient):
    me  = await client.get_me()
    nom = me.first_name or me.username or str(me.id)
    if uid not in users:
        users[uid] = {"accounts": [], "shablon": 1}
    if "accounts" not in users[uid]:
        users[uid]["accounts"] = []

    # Yangi akkount qo'shish
    akk = {
        "client":  client,
        "telefon": me.phone or "",
        "nom":     nom,
        "shablon": users[uid].get("shablon", 1),
        "task":    None,
    }
    users[uid]["accounts"].append(akk)

    # Login vaqtinchalik ma'lumotlarini tozalash
    users[uid].pop("_client", None)
    users[uid].pop("_telefon", None)
    users[uid].pop("_hash", None)

    await state.clear()

    # Darhol boshlash
    akk_idx = len(users[uid]["accounts"]) - 1
    await _task_boshlash_akk(uid, akk_idx)

    await msg.answer(
        "✅ @{} ulandi va boshlandi!\n\nShablon tanlang:".format(me.username or nom),
        reply_markup=shablon_kb()
    )


# ── Shablon ────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "shablon_menu")
async def shablon_menu(cb: CallbackQuery):
    try:
        await cb.message.edit_text("🎨 Shablon tanlang:", reply_markup=shablon_kb())
    except TelegramBadRequest:
        await cb.message.answer("🎨 Shablon tanlang:", reply_markup=shablon_kb())


@dp.callback_query(F.data.startswith("sh:"))
async def shablon_tanlash(cb: CallbackQuery):
    uid     = cb.from_user.id
    shablon = int(cb.data.split(":")[1])
    if uid not in users:
        users[uid] = {}
    users[uid]["shablon"] = shablon

    nom    = SHABLONLAR[shablon - 1][1]
    namuna = soat_formatlash(shablon)

    # Eski taskni to'xtatish
    eski = users[uid].get("task")
    if eski and not eski.done():
        eski.cancel()

    # Client bor bo'lsa darhol boshlash
    if users[uid].get("client"):
        await _task_boshlash(uid)
        await cb.answer("✅ {} tanlandi!".format(nom))
        try:
            await cb.message.edit_text(
                "✅ <b>{}</b> tanlandi!\nNamuna: <code>{}</code>\n\nHar soniyada yangilanmoqda 🔄".format(nom, namuna),
                reply_markup=bosh_kb(uid), parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass
    else:
        await cb.answer("✅ Shablon tanlandi!")
        try:
            await cb.message.edit_text(
                "✅ <b>{}</b> tanlandi!\nNamuna: <code>{}</code>\n\nBoshlash uchun ▶️ bosing.".format(nom, namuna),
                reply_markup=bosh_kb(uid), parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass


# ── To'xtatish ─────────────────────────────────────────────────────────────────




# ── Bio tsikl ──────────────────────────────────────────────────────────────────

async def _task_boshlash_akk(uid: int, akk_idx: int):
    """Bitta akkount uchun bio tsikl boshlash."""
    task = asyncio.create_task(_bio_tsikl_akk(uid, akk_idx))
    users[uid]["accounts"][akk_idx]["task"] = task


async def _task_boshlash(uid: int):
    """Barcha akkountlar uchun bio tsikl boshlash."""
    accounts = users.get(uid, {}).get("accounts", [])
    for i, akk in enumerate(accounts):
        old_task = akk.get("task")
        if not old_task or old_task.done():
            await _task_boshlash_akk(uid, i)


async def _bio_tsikl_akk(uid: int, akk_idx: int):
    """Bitta akkount uchun bio yangilash tsikli."""
    oxirgi   = None
    xato_son = 0

    while True:
        try:
            accounts = users.get(uid, {}).get("accounts", [])
            if akk_idx >= len(accounts):
                break
            akk     = accounts[akk_idx]
            client  = akk.get("client")
            shablon = akk.get("shablon", users.get(uid, {}).get("shablon", 1))

            if not client:
                await asyncio.sleep(5)
                continue

            if not client.is_connected():
                try:
                    await client.connect()
                except Exception:
                    await asyncio.sleep(10)
                    continue

            yangi = soat_formatlash(shablon)
            if yangi != oxirgi:
                await client(UpdateProfileRequest(about=yangi))
                oxirgi   = yangi
                xato_son = 0

            await asyncio.sleep(1)

        except asyncio.CancelledError:
            try:
                accounts = users.get(uid, {}).get("accounts", [])
                if akk_idx < len(accounts):
                    client = accounts[akk_idx].get("client")
                    if client and client.is_connected():
                        await client(UpdateProfileRequest(about=""))
            except Exception:
                pass
            nom = users.get(uid,{}).get("accounts",[{}]*max(akk_idx+1,1))[akk_idx].get("nom","")
            log.info("Bio tsikl to'xtatildi: uid={} akk={}({})".format(uid, akk_idx, nom))
            break
        except Exception as e:
            xato_son += 1
            log.warning("Bio xato uid={} akk={}: {}".format(uid, akk_idx, e))
            await asyncio.sleep(min(xato_son * 3, 30))


@dp.callback_query(F.data == "start_all")
async def start_all(cb: CallbackQuery):
    uid      = cb.from_user.id
    accounts = users.get(uid, {}).get("accounts", [])
    if not accounts:
        return await cb.answer("Akkount yo'q!")
    n = 0
    for i, akk in enumerate(accounts):
        t = akk.get("task")
        if not t or t.done():
            await _task_boshlash_akk(uid, i)
            n += 1
    await cb.answer("▶️ {} ta boshlandi!".format(n))
    try:
        await cb.message.edit_text(
            "✅ {} ta akkount ishlayapti!".format(n),
            reply_markup=bosh_kb(uid)
        )
    except Exception:
        pass


@dp.callback_query(F.data == "stop_all")
async def stop_all(cb: CallbackQuery):
    uid      = cb.from_user.id
    accounts = users.get(uid, {}).get("accounts", [])
    n = 0
    for akk in accounts:
        t = akk.get("task")
        if t and not t.done():
            t.cancel()
            n += 1
    await cb.answer("⏹ {} ta to'xtatildi".format(n))
    try:
        await cb.message.edit_text(
            "⏹ {} ta akkount to'xtatildi.".format(n),
            reply_markup=bosh_kb(uid)
        )
    except Exception:
        pass


@dp.callback_query(F.data == "akk_royxat")
async def akk_royxat(cb: CallbackQuery):
    uid      = cb.from_user.id
    accounts = users.get(uid, {}).get("accounts", [])
    if not accounts:
        return await cb.answer("Akkount yo'q!")
    rows = []
    for i, akk in enumerate(accounts):
        aktiv = akk.get("task") and not akk["task"].done()
        belgi = "✅" if aktiv else "⏸"
        nom   = akk.get("nom") or akk.get("telefon", "")
        rows.append([("{} {}. {}".format(belgi, i+1, nom), "akk:{}".format(i))])
    rows.append([("➕ Yangi qo'shish", "start_vc"), ("⬅️ Orqaga", "back")])
    try:
        await cb.message.edit_text("📋 <b>Akkountlar:</b>", reply_markup=ikb(rows), parse_mode="HTML")
    except Exception:
        pass


@dp.callback_query(F.data.startswith("akk:"))
async def akk_detail(cb: CallbackQuery):
    uid     = cb.from_user.id
    idx     = int(cb.data.split(":")[1])
    accounts = users.get(uid, {}).get("accounts", [])
    if idx >= len(accounts):
        return await cb.answer("Topilmadi")
    akk   = accounts[idx]
    aktiv = akk.get("task") and not akk["task"].done()
    nom   = akk.get("nom") or akk.get("telefon", "")
    shablon = akk.get("shablon", 1)
    matn = "{} <b>{}</b>\nShablon: {}\nNamuna: <code>{}</code>".format(
        "✅" if aktiv else "⏸", nom,
        SHABLONLAR[shablon-1][1], soat_formatlash(shablon)
    )
    rows = []
    if aktiv:
        rows.append([("⏹ To'xtatish", "akk_stop:{}".format(idx))])
    else:
        rows.append([("▶️ Boshlash", "akk_start:{}".format(idx))])
    rows.append([("🗑 O'chirish", "akk_del:{}".format(idx))])
    rows.append([("⬅️ Orqaga", "akk_royxat")])
    try:
        await cb.message.edit_text(matn, reply_markup=ikb(rows), parse_mode="HTML")
    except Exception:
        pass


@dp.callback_query(F.data.startswith("akk_start:"))
async def akk_start(cb: CallbackQuery):
    uid = cb.from_user.id
    idx = int(cb.data.split(":")[1])
    await _task_boshlash_akk(uid, idx)
    await cb.answer("▶️ Boshlandi!")
    cb.data = "akk:{}".format(idx)
    await akk_detail(cb)


@dp.callback_query(F.data.startswith("akk_stop:"))
async def akk_stop(cb: CallbackQuery):
    uid      = cb.from_user.id
    idx      = int(cb.data.split(":")[1])
    accounts = users.get(uid, {}).get("accounts", [])
    if idx < len(accounts):
        t = accounts[idx].get("task")
        if t and not t.done():
            t.cancel()
    await cb.answer("⏹ To'xtatildi")
    cb.data = "akk:{}".format(idx)
    await akk_detail(cb)


@dp.callback_query(F.data.startswith("akk_del:"))
async def akk_del(cb: CallbackQuery):
    uid      = cb.from_user.id
    idx      = int(cb.data.split(":")[1])
    accounts = users.get(uid, {}).get("accounts", [])
    if idx < len(accounts):
        t = accounts[idx].get("task")
        if t and not t.done():
            t.cancel()
        c = accounts[idx].get("client")
        if c:
            try: await c.disconnect()
            except: pass
        accounts.pop(idx)
    await cb.answer("🗑 O'chirildi")
    cb.data = "akk_royxat"
    await akk_royxat(cb)


# ── Health server (Render uchun) ───────────────────────────────────────────────

async def health_server():
    from aiohttp import web

    async def handle(request):
        aktiv = sum(
            1 for v in users.values()
            if v.get("task") and not v["task"].done()
        )
        return web.Response(text="OK | Aktiv: {}".format(aktiv))

    app    = web.Application()
    app.router.add_get("/", handle)
    app.router.add_get("/health", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "8080"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("Health server port={}".format(port))


# ── Main ───────────────────────────────────────────────────────────────────────

async def main():
    await health_server()
    log.info("ClockBot ishga tushdi ✅")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
