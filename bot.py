"""
ClockBot — Real-time soat bio boti
Render da ishlaydi, minimal xotira sarflaydi.
"""

import asyncio
import logging
import os
from datetime import datetime

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

BOT_TOKEN  = os.getenv("BOT_TOKEN", "")
API_ID     = int(os.getenv("API_ID", "0"))
API_HASH   = os.getenv("API_HASH", "")
TIMEZONE   = int(os.getenv("TIMEZONE", "5"))  # UTC+5 Toshkent

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())

# Foydalanuvchilar: {user_id: {"client": TelegramClient, "task": Task, "shablon": int}}
foydalanuvchilar: dict[int, dict] = {}

# ═══════════════════════════════════════════════════════════════════════════════
# 10 ta soat shabloni
# ═══════════════════════════════════════════════════════════════════════════════

SHABLON_NOMLARI = [
    "🕐 Oddiy",
    "🌙 Kecha/Kunduz",
    "⏰ Analog",
    "🔢 Raqamli",
    "🌸 Gul",
    "⚡ Zamonaviy",
    "🎯 Minimal",
    "💎 Premium",
    "🌊 To'lqin",
    "🔥 Olov",
]


def soat_formatlash(shablon: int) -> str:
    """Tanlangan shablon bo'yicha hozirgi vaqtni qaytaradi."""
    from datetime import timezone, timedelta
    tz  = timezone(timedelta(hours=TIMEZONE))
    now = datetime.now(tz)
    h   = now.hour
    m   = now.minute
    s   = now.second
    hh  = f"{h:02d}"
    mm  = f"{m:02d}"
    ss  = f"{s:02d}"

    # 12 soatlik format
    ampm = "AM" if h < 12 else "PM"
    h12  = h % 12 or 12

    # Analog soat strelkasi
    soat_belgi  = ["🕛","🕐","🕑","🕒","🕓","🕔","🕕","🕖","🕗","🕘","🕙","🕚"]
    daqiqa_belgi = "▓" * (m // 6) + "░" * (10 - m // 6)

    # Kecha/Kunduz belgisi
    if 6 <= h < 12:
        vaqt_belgisi = "🌅 Erta"
    elif 12 <= h < 17:
        vaqt_belgisi = "☀️ Kunduz"
    elif 17 <= h < 21:
        vaqt_belgisi = "🌇 Kech"
    else:
        vaqt_belgisi = "🌙 Kecha"

    shablonlar = {
        1:  f"🕐 {hh}:{mm}:{ss}",
        2:  f"{vaqt_belgisi} | {hh}:{mm}",
        3:  f"{soat_belgi[h % 12]} {hh}:{mm}",
        4:  f"⏱ {hh}:{mm}:{ss} | UTC+{TIMEZONE}",
        5:  f"🌸 {hh}:{mm} 🌸",
        6:  f"⚡{hh}:{mm}:{ss}⚡",
        7:  f"{hh}:{mm}",
        8:  f"💎 {h12}:{mm} {ampm} 💎",
        9:  f"🌊 {daqiqa_belgi} {hh}:{mm}",
        10: f"🔥{hh}:{mm}:{ss}🔥",
    }
    return shablonlar.get(shablon, f"{hh}:{mm}:{ss}")


# ═══════════════════════════════════════════════════════════════════════════════
# FSM
# ═══════════════════════════════════════════════════════════════════════════════

class Login(StatesGroup):
    telefon = State()
    kod     = State()
    parol   = State()


# ═══════════════════════════════════════════════════════════════════════════════
# Yordamchi
# ═══════════════════════════════════════════════════════════════════════════════

def ikb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
        for row in rows
    ])

def asosiy_menyu(user_id: int) -> InlineKeyboardMarkup:
    aktiv = user_id in foydalanuvchilar and foydalanuvchilar[user_id].get("task")
    if aktiv and not foydalanuvchilar[user_id]["task"].done():
        return ikb([
            [("⏹ To'xtatish", "toxt")],
            [("🎨 Shablon o'zgartir", "shablon")],
        ])
    return ikb([
        [("▶️ Boshlash", "boshlash")],
        [("🎨 Shablon tanlash", "shablon")],
    ])


# ═══════════════════════════════════════════════════════════════════════════════
# /start
# ═══════════════════════════════════════════════════════════════════════════════

@dp.message(Command("start"))
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    uid    = msg.from_user.id
    aktiv  = uid in foydalanuvchilar

    matn = (
        "⏰ <b>ClockBot</b> — real-time soat bio\n\n"
        "Bio'ingiz har soniyada yangilanib turadi!\n\n"
    )
    if aktiv and not foydalanuvchilar[uid].get("task", asyncio.Future()).done():
        shablon = foydalanuvchilar[uid].get("shablon", 1)
        matn   += f"✅ Hozir aktiv | Shablon: {SHABLON_NOMLARI[shablon-1]}\n"
        matn   += f"Namuna: <code>{soat_formatlash(shablon)}</code>"
    else:
        matn += "Boshlash uchun Telegram akkauntingizni ulang."

    await msg.answer(matn, reply_markup=asosiy_menyu(uid), parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════════════════
# Login
# ═══════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data == "boshlash")
async def boshlash(cb: CallbackQuery, state: FSMContext):
    uid = cb.from_user.id
    if uid in foydalanuvchilar and foydalanuvchilar[uid].get("client"):
        # Allaqachon ulangan — faqat taskni boshlash
        await _task_boshlash(uid)
        await cb.answer("▶️ Boshlandi!")
        try:
            await cb.message.edit_text(
                "✅ Ishlayapti!\n\nBio'ingiz har soniyada yangilanmoqda.",
                reply_markup=asosiy_menyu(uid), parse_mode="HTML"
            )
        except Exception:
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

    client = TelegramClient(StringSession(), API_ID, API_HASH,
                            device_model="iPhone 14 Pro", system_version="iOS 16.5",
                            app_version="9.6.3")
    await client.connect()
    try:
        natija = await client.send_code_request(telefon)
        uid    = msg.from_user.id
        if uid not in foydalanuvchilar:
            foydalanuvchilar[uid] = {"shablon": 1}
        foydalanuvchilar[uid]["client"]  = client
        foydalanuvchilar[uid]["telefon"] = telefon
        foydalanuvchilar[uid]["hash"]    = natija.phone_code_hash
        await state.set_state(Login.kod)
        await msg.answer("📨 SMS kod yuborildi. Kodni kiriting:")
    except FloodWaitError as e:
        await client.disconnect()
        await state.clear()
        await msg.answer(f"⏳ FloodWait: {e.seconds//60} daqiqa kuting.")
    except Exception as e:
        await client.disconnect()
        await state.clear()
        await msg.answer(f"❌ Xato: {e}")


@dp.message(Login.kod)
async def kod_qabul(msg: Message, state: FSMContext):
    uid    = msg.from_user.id
    ma     = foydalanuvchilar.get(uid, {})
    client = ma.get("client")
    if not client:
        await state.clear()
        return await msg.answer("❌ Sessiya topilmadi. /start bosing.")
    try:
        await client.sign_in(ma["telefon"], msg.text.strip(), phone_code_hash=ma["hash"])
        await _login_muvaffaqiyat(msg, state, uid, client)
    except SessionPasswordNeededError:
        await state.set_state(Login.parol)
        await msg.answer("🔐 2FA parolini kiriting:")
    except Exception as e:
        await state.clear()
        await msg.answer(f"❌ Kod xato: {e}")


@dp.message(Login.parol)
async def parol_qabul(msg: Message, state: FSMContext):
    uid    = msg.from_user.id
    client = foydalanuvchilar.get(uid, {}).get("client")
    if not client:
        await state.clear()
        return await msg.answer("❌ Sessiya topilmadi.")
    try:
        await client.sign_in(password=msg.text.strip())
        await _login_muvaffaqiyat(msg, state, uid, client)
    except Exception as e:
        await state.clear()
        await msg.answer(f"❌ Parol xato: {e}")


async def _login_muvaffaqiyat(msg: Message, state: FSMContext, uid: int, client: TelegramClient):
    me = await client.get_me()
    foydalanuvchilar[uid]["client"] = client
    await state.clear()
    # Shablon tanlash menyusi
    await msg.answer(
        f"✅ Ulandi: @{me.username or me.first_name}\n\n"
        f"Soat shablonini tanlang:",
        reply_markup=_shablon_ikb()
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Shablon
# ═══════════════════════════════════════════════════════════════════════════════

def _shablon_ikb() -> InlineKeyboardMarkup:
    qatorlar = []
    for i in range(0, 10, 2):
        namuna1 = soat_formatlash(i+1)
        namuna2 = soat_formatlash(i+2)
        qatorlar.append([
            (f"{SHABLON_NOMLARI[i]}: {namuna1}", f"sh:{i+1}"),
            (f"{SHABLON_NOMLARI[i+1]}: {namuna2}", f"sh:{i+2}"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=qatorlar)


@dp.callback_query(F.data == "shablon")
async def shablon_menu(cb: CallbackQuery):
    try:
        await cb.message.edit_text("🎨 Shablon tanlang:", reply_markup=_shablon_ikb())
    except TelegramBadRequest:
        await cb.message.answer("🎨 Shablon tanlang:", reply_markup=_shablon_ikb())


@dp.callback_query(F.data.startswith("sh:"))
async def shablon_tanlash(cb: CallbackQuery):
    uid     = cb.from_user.id
    shablon = int(cb.data.split(":")[1])

    if uid not in foydalanuvchilar:
        foydalanuvchilar[uid] = {}
    foydalanuvchilar[uid]["shablon"] = shablon

    namuna = soat_formatlash(shablon)
    await cb.answer(f"✅ {SHABLON_NOMLARI[shablon-1]} tanlandi!")

    # Agar client ulangan bo'lsa — darhol boshlash
    client = foydalanuvchilar[uid].get("client")
    if client:
        # Eski taskni bekor qilish
        eski = foydalanuvchilar[uid].get("task")
        if eski and not eski.done():
            eski.cancel()
        await _task_boshlash(uid)
        try:
            await cb.message.edit_text(
                f"✅ <b>{SHABLON_NOMLARI[shablon-1]}</b> tanlandi!\n\n"
                f"Namuna: <code>{namuna}</code>\n\n"
                f"Bio har soniyada yangilanmoqda... 🔄",
                reply_markup=asosiy_menyu(uid), parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass
    else:
        try:
            await cb.message.edit_text(
                f"✅ Shablon tanlandi: <b>{SHABLON_NOMLARI[shablon-1]}</b>\n"
                f"Namuna: <code>{namuna}</code>\n\n"
                f"Boshlash uchun akkaunt ulang:",
                reply_markup=asosiy_menyu(uid), parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# To'xtatish
# ═══════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data == "toxt")
async def toxtatish(cb: CallbackQuery):
    uid  = cb.from_user.id
    ma   = foydalanuvchilar.get(uid, {})
    task = ma.get("task")
    if task and not task.done():
        task.cancel()
        await cb.answer("⏹ To'xtatildi")
    else:
        await cb.answer("Hozir ishlamayapti")
    try:
        await cb.message.edit_text(
            "⏹ To'xtatildi.\n\nDavom ettirish uchun ▶️ bosing.",
            reply_markup=asosiy_menyu(uid)
        )
    except TelegramBadRequest:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# Asosiy tsikl — bio yangilash
# ═══════════════════════════════════════════════════════════════════════════════

async def _task_boshlash(uid: int):
    ma  = foydalanuvchilar.get(uid, {})
    task = asyncio.create_task(_bio_tsikl(uid))
    foydalanuvchilar[uid]["task"] = task
    log.info(f"Bio tsikl boshlandi: uid={uid}")


async def _bio_tsikl(uid: int):
    """Bio ni har soniyada yangilaydi."""
    oxirgi_bio = None
    xato_soni  = 0

    while True:
        try:
            ma      = foydalanuvchilar.get(uid, {})
            client  = ma.get("client")
            shablon = ma.get("shablon", 1)

            if not client or not client.is_connected():
                await asyncio.sleep(5)
                if client:
                    try: await client.connect()
                    except Exception: pass
                continue

            yangi_bio = soat_formatlash(shablon)

            # Faqat o'zgargan bo'lsa yangilaymiz (API limitini tejash)
            if yangi_bio != oxirgi_bio:
                await client(UpdateProfileRequest(about=yangi_bio))
                oxirgi_bio = yangi_bio
                xato_soni  = 0

            await asyncio.sleep(1)

        except asyncio.CancelledError:
            log.info(f"Bio tsikl to'xtatildi: uid={uid}")
            # Bio ni tozalash (ixtiyoriy)
            try:
                client = foydalanuvchilar.get(uid, {}).get("client")
                if client and client.is_connected():
                    await client(UpdateProfileRequest(about=""))
            except Exception:
                pass
            break
        except Exception as e:
            xato_soni += 1
            log.warning(f"Bio yangilash xato uid={uid}: {e}")
            # Ko'p xato bo'lsa — uzoqroq kutish
            kutish = min(xato_soni * 5, 60)
            await asyncio.sleep(kutish)


# ═══════════════════════════════════════════════════════════════════════════════
# Render uchun — HTTP health check (minimal)
# ═══════════════════════════════════════════════════════════════════════════════

async def health_server():
    """Render free tier uchun health check serveri."""
    from aiohttp import web
    async def handle(request):
        aktiv = sum(1 for v in foydalanuvchilar.values()
                    if v.get("task") and not v["task"].done())
        return web.Response(text=f"OK | Aktiv: {aktiv}")
    app    = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "8080"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info(f"Health server: port {port}")


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

async def main():
    await health_server()
    log.info("ClockBot ishga tushdi ✅")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
