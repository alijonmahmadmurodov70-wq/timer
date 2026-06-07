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

# uid -> {client, task, shablon, telefon, hash}
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
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
        for row in rows
    ])


def bosh_kb(uid: int) -> InlineKeyboardMarkup:
    aktiv = (uid in users
             and users[uid].get("task")
             and not users[uid]["task"].done())
    if aktiv:
        shablon = users[uid].get("shablon", 1)
        nom     = SHABLONLAR[shablon - 1][1]
        return ikb([
            [("⏹ To'xtatish", "stop")],
            [("🎨 Shablon: " + nom, "shablon_menu")],
        ])
    else:
        return ikb([
            [("▶️ Boshlash", "start_vc")],
            [("🎨 Shablon tanlash", "shablon_menu")],
        ])


def shablon_kb() -> InlineKeyboardMarkup:
    now = hozirgi_vaqt()
    h, m, s = now.hour, now.minute, now.second
    rows = []
    for i in range(0, 10, 2):
        s1 = SHABLONLAR[i]
        s2 = SHABLONLAR[i + 1]
        rows.append([
            (s1[1] + ": " + soat_formatlash(i + 1), "sh:" + s1[0]),
            (s2[1] + ": " + soat_formatlash(i + 2), "sh:" + s2[0]),
        ])
    rows.append([("⬅️ Orqaga", "back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── /start ─────────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    uid   = msg.from_user.id
    aktiv = (uid in users
             and users[uid].get("task")
             and not users[uid]["task"].done())

    if aktiv:
        shablon = users[uid].get("shablon", 1)
        nom     = SHABLONLAR[shablon - 1][1]
        namuna  = soat_formatlash(shablon)
        matn    = (
            "⏰ <b>ClockBot</b>\n\n"
            "✅ Ishlayapti!\n"
            "Shablon: <b>{}</b>\n"
            "Hozir: <code>{}</code>"
        ).format(nom, namuna)
    else:
        matn = (
            "⏰ <b>ClockBot</b>\n\n"
            "Bio'ingizga real-time soat qo'ying!\n"
            "Har soniyada yangilanib turadi 🔄\n\n"
            "Boshlash uchun ▶️ bosing."
        )
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
    # MTProxy — Render serveridan Telegram ga ulanish uchun
    # Bir nechta ishonchli MTProxy serverlar
    PROXIES = [
        ("mtproto", "mtproxy.co", 443, "secret"),
        None,  # Proxy-siz ham urinib ko'ramiz
    ]

    client = None
    for proxy in PROXIES:
        try:
            if proxy:
                from telethon.network.connection import ConnectionTcpMTProxyRandomizedIntermediate
                c = TelegramClient(
                    StringSession(), API_ID, API_HASH,
                    connection=ConnectionTcpMTProxyRandomizedIntermediate,
                    proxy=proxy,
                    device_model="Samsung Galaxy S23",
                    system_version="Android 13",
                    app_version="10.0.1",
                    connection_retries=3,
                )
            else:
                c = TelegramClient(
                    StringSession(), API_ID, API_HASH,
                    device_model="Samsung Galaxy S23",
                    system_version="Android 13",
                    app_version="10.0.1",
                    connection_retries=3,
                )
            await c.connect()
            client = c
            break
        except Exception:
            try: await c.disconnect()
            except: pass
            continue

    if not client:
        await state.clear()
        await msg.answer("❌ Ulanib bo'lmadi. Keyinroq urinib ko'ring.")
        return
    try:
        await client.connect()
        natija = await client.send_code_request(telefon)
        uid = msg.from_user.id
        if uid not in users:
            users[uid] = {"shablon": 1}
        users[uid]["client"]  = client
        users[uid]["telefon"] = telefon
        users[uid]["hash"]    = natija.phone_code_hash
        await state.set_state(Login.kod)
        # Kod qayerga ketishini aniqlaymiz
        tur = natija.type.__class__.__name__
        if "Sms" in tur:
            qaerga = "📱 SMS ga"
        elif "App" in tur:
            qaerga = "📲 Telegram ilovaga"
        else:
            qaerga = "📨"
        await msg.answer("{} kod yuborildi! Kodni kiriting:".format(qaerga))
    except FloodWaitError as e:
        await client.disconnect()
        await state.clear()
        d = e.seconds // 60 + 1
        await msg.answer("⏳ FloodWait: {} daqiqa kuting.".format(d))
    except Exception as e:
        await client.disconnect()
        await state.clear()
        await msg.answer(
            "❌ Xato: {}\n\nAPI_ID va API_HASH to'g'rimi?".format(str(e)[:150])
        )


@dp.message(Login.kod)
async def kod_qabul(msg: Message, state: FSMContext):
    uid    = msg.from_user.id
    ma     = users.get(uid, {})
    client = ma.get("client")
    if not client:
        await state.clear()
        return await msg.answer("❌ Sessiya topilmadi. /start bosing.")
    try:
        await client.sign_in(ma["telefon"], msg.text.strip(), phone_code_hash=ma["hash"])
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
    client = users.get(uid, {}).get("client")
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
    me = await client.get_me()
    users[uid]["client"] = client
    await state.clear()
    await msg.answer(
        "✅ Ulandi: @{}\n\nShablon tanlang:".format(me.username or me.first_name),
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

@dp.callback_query(F.data == "stop")
async def toxtatish(cb: CallbackQuery):
    uid  = cb.from_user.id
    task = users.get(uid, {}).get("task")
    if task and not task.done():
        task.cancel()
        await cb.answer("⏹ To'xtatildi")
    else:
        await cb.answer("Hozir ishlamayapti")
    try:
        await cb.message.edit_text(
            "⏹ To'xtatildi.\n\nDavom ettirish uchun ▶️ bosing.",
            reply_markup=bosh_kb(uid)
        )
    except TelegramBadRequest:
        pass


# ── Bio tsikl ──────────────────────────────────────────────────────────────────

async def _task_boshlash(uid: int):
    task = asyncio.create_task(_bio_tsikl(uid))
    users[uid]["task"] = task


async def _bio_tsikl(uid: int):
    oxirgi   = None
    xato_son = 0

    while True:
        try:
            ma      = users.get(uid, {})
            client  = ma.get("client")
            shablon = ma.get("shablon", 1)

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
            # Bio ni tozalash
            try:
                client = users.get(uid, {}).get("client")
                if client and client.is_connected():
                    await client(UpdateProfileRequest(about=""))
            except Exception:
                pass
            log.info("Bio tsikl to'xtatildi: uid={}".format(uid))
            break
        except Exception as e:
            xato_son += 1
            log.warning("Bio xato uid={}: {}".format(uid, e))
            await asyncio.sleep(min(xato_son * 3, 30))


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
