import asyncio
import logging
import os
import sys
import aiosqlite
import time
import random
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, html, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from google.genai import types as genai_types
from google import genai

logging.basicConfig(
    filename="chasovod_errors.log",
    level=logging.ERROR,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    encoding="utf-8"
)

# Безопасное получение ключей из окружения хостинга
# Прямые значения для гарантированного запуска на хостинге
TELEGRAM_TOKEN = "8938831004:AAEfhk6X4Rg7d-SsJv-1mvX40Dn3Yc2wsg4"
GEMINI_API_KEY = "AQ.Ab8RN6I1hnUlAeU6NR2S2EEX9vNDPt-WRfFGIjoXkRaw3_yS6Q"
CREATOR_ID = 8643288567

client = genai.Client(api_key=AQ.Ab8RN6I1hnUlAeU6NR2S2EEX9vNDPt-WRfFGIjoXkRaw3_yS6Q)

CHASOVOD_TRIGGERS = ["часовод", "чисавод", "чесовод", "чосок", "часик", "часовец", "часо", "chasovod"]
BAD_WORDS = ["спам_тест_слово", "запрещенка", "матноеслово"]

spam_protection = {}
SPAM_COOLDOWN = 2.0

chat_activity_status = {}
ai_chat_states = {}
setting_sarcasm = {}
setting_moderation = {}
setting_quests = {}
known_chats = set()
user_action_timestamps = {}

response_cache = {}
CACHE_TTL = 300

client = genai.Client(api_key=GEMINI_API_KEY)
dp = Dispatcher()

message_timestamps = []

ACTION_TITLES = {
    "caps": "Капслочный истеричка 📢",
    "question": "Вечный почемучка 🤔",
    "spam": "Профессиональный спидраннер позора 🏃💨",
    "badword": "Матершинник вне закона 🤬"
}


def get_holiday_quests():
    current_year = datetime.now().year
    next_year = current_year + 1
    return {
        "01-01": f"🎉 **НОВОГОДНИЙ ПОХМЕЛЬНЫЙ СУД ({current_year})** 🎉\nЗагадка: *«Что в холодильнике лежит тяжелее всего?»*",
        "02-14": "💘 **ДЕНЬ СТАРАТЕЛЬНЫХ ДУШНИЛ** 💘\nНапишите самое абсурдное признание в любви чату.",
        "04-01": "🤡 **ДЕНЬ ДУРАКОВ** 🤡\nВесь чат работает наоборот: за вежливость минус, за бред — плюсы!",
        "10-31": f"🎃 **ХЭЛЛОУИНСКИЙ СУД ВРЕМЕНИ ({current_year})** 🎃\nНазовите имя того, кто дольше всех молчит!",
        "12-31": f"❄ **НОВОГОДНИЙ КВЕСТ ({current_year}-{next_year})** ❄\nДед Мороз в петле. Загадка: *«Что идет, но не движется?»*"
    }


async def init_db():
    async with aiosqlite.connect("chasovod.db") as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                titles TEXT DEFAULT 'Призрак чата',
                group_karma INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0,
                warnings_count INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS message_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                username TEXT,
                text TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ai_chat_history (
                user_id INTEGER,
                role TEXT,
                content TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS golden_memory (
                user_id INTEGER,
                fact TEXT
            )
        """)
        await db.commit()


async def update_user_info(user_id, username):
    async with aiosqlite.connect("chasovod.db") as db:
        await db.execute("""
            INSERT INTO users (user_id, username, titles, group_karma, is_banned, warnings_count)
            VALUES (?, ?, 'Призрак чата', 0, 0, 0)
            ON CONFLICT(user_id) DO UPDATE SET username = excluded.username
        """, (user_id, username))
        await db.commit()


async def add_layer_title(user_id, new_title):
    async with aiosqlite.connect("chasovod.db") as db:
        async with db.execute("SELECT titles FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
        if row:
            current_titles = row[0]
            updated_titles = f"{new_title} ➔ {current_titles}"
            await db.execute("UPDATE users SET titles = ? WHERE user_id = ?", (updated_titles, user_id))
            await db.commit()


async def add_layer_title_public(bot: Bot, chat_id: int, user_id: int, username: str, new_title: str):
    await add_layer_title(user_id, new_title)
    async with aiosqlite.connect("chasovod.db") as db:
        async with db.execute("SELECT titles FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            updated_titles = row[0] if row else "Призрак чата"

    await bot.send_message(
        chat_id=chat_id,
        text=f"🚨 **ДИЧЬ ЗАФИКСИРОВАНА!** 🚨\n@{username} получает уникальный титул:\n👑 **{new_title}**\n📜 Стопка слоев:\n<code>{updated_titles}</code>"
    )


async def get_user_data(user_id):
    async with aiosqlite.connect("chasovod.db") as db:
        async with db.execute("SELECT titles, group_karma, is_banned FROM users WHERE user_id = ?",
                              (user_id,)) as cursor:
            row = await cursor.fetchone()
    return row if row else ("Призрак чата", 0, 0)


async def reset_user_profile(user_id):
    async with aiosqlite.connect("chasovod.db") as db:
        await db.execute(
            "UPDATE users SET titles = 'Новорожденный времени ⏳', warnings_count = 0, is_banned = 0 WHERE user_id = ?",
            (user_id,))
        await db.commit()


async def save_message_to_history(chat_id, username, text):
    async with aiosqlite.connect("chasovod.db") as db:
        await db.execute("INSERT INTO message_history (chat_id, username, text) VALUES (?, ?, ?)",
                         (chat_id, username, text))
        await db.commit()


async def cleanup_old_history():
    async with aiosqlite.connect("chasovod.db") as db:
        three_days_ago = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d %H:%M:%S')
        await db.execute("DELETE FROM message_history WHERE timestamp < ?", (three_days_ago,))
        await db.commit()


async def get_recent_ai_history(user_id: int):
    async with aiosqlite.connect("chasovod.db") as db:
        async with db.execute(
            "SELECT role, content FROM ai_chat_history WHERE user_id = ? ORDER BY rowid DESC LIMIT 12",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
    
    rows.reverse()
    chat_history = []
    for role, content in rows:
        chat_history.append(
            genai_types.Content(
                role=role,
                parts=[genai_types.Part.from_text(text=content)]
            )
        )
    return chat_history


async def save_ai_message(user_id: int, role: str, content: str):
    async with aiosqlite.connect("chasovod.db") as db:
        await db.execute("INSERT INTO ai_chat_history (user_id, role, content) VALUES (?, ?, ?)", (user_id, role, content))
        await db.execute("""
            DELETE FROM ai_chat_history WHERE user_id = ? AND rowid NOT IN (
                SELECT rowid FROM ai_chat_history WHERE user_id = ? ORDER BY rowid DESC LIMIT 20
            )
        """, (user_id, user_id))
        await db.commit()


async def add_golden_memory(user_id: int, fact: str):
    async with aiosqlite.connect("chasovod.db") as db:
        await db.execute("INSERT INTO golden_memory (user_id, fact) VALUES (?, ?)", (user_id, fact))
        await db.commit()


async def get_golden_memories(user_id: int) -> str:
    async with aiosqlite.connect("chasovod.db") as db:
        async with db.execute("SELECT fact FROM golden_memory WHERE user_id = ?", (user_id,)) as cursor:
            rows = await cursor.fetchall()
    if not rows:
        return ""
    facts = "\n".join([f"- {r[0]}" for r in rows])
    return f"\n\nЗолотая память (важные факты о проекте):\n{facts}"


async def get_leaderboard_data():
    async with aiosqlite.connect("chasovod.db") as db:
        async with db.execute("SELECT username, titles FROM users") as cursor:
            rows = await cursor.fetchall()

    leaderboard = []
    for username, titles in rows:
        layers_count = titles.count("➔") + 1
        leaderboard.append((username or "Аноним", titles, layers_count))

    leaderboard.sort(key=lambda x: x[2], reverse=True)
    return leaderboard[:10]


async def get_all_users_admin_info():
    async with aiosqlite.connect("chasovod.db") as db:
        async with db.execute("SELECT user_id, username, titles FROM users") as cursor:
            rows = await cursor.fetchall()
    return rows


async def get_absolute_all_stats():
    async with aiosqlite.connect("chasovod.db") as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            total_users = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM message_history") as cursor:
            total_db_msgs = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(DISTINCT chat_id) FROM message_history") as cursor:
            unique_chats_db = (await cursor.fetchone())[0]
        async with db.execute(
                "SELECT COUNT(*) FROM users WHERE titles != 'Призрак чата' AND titles != 'Новорожденный времени ⏳'") as cursor:
            titled_users = (await cursor.fetchone())[0]

    now = time.time()
    global message_timestamps
    message_timestamps = [t for t in message_timestamps if now - t < 3600]

    msgs_last_min = sum(1 for t in message_timestamps if now - t < 60)
    msgs_last_hour = len(message_timestamps)

    return total_users, total_db_msgs, unique_chats_db, titled_users, msgs_last_min, msgs_last_hour


@dp.message(lambda message: message.text in ["/leaderboard", "/top", "/титулы"])
async def leaderboard_handler(message: Message) -> None:
    top_players = await get_leaderboard_data()
    if not top_players:
        await message.answer("🏆 Список лидеров пуст.")
        return

    text = "🏆 **ЗАЛ СЛАВЫ ХРОНОМЕТРА (Топ по слоям титулов)**:\n\n"
    for i, (uname, titles, count) in enumerate(top_players, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        text += f"{medal} **@{uname}** — слоев: **{count}**\n   └ <code>{titles}</code>\n\n"
    await message.answer(text)


@dp.message(lambda message: message.text == "/admin")
async def admin_panel_handler(message: Message) -> None:
    if message.from_user.id != CREATOR_ID:
        await message.answer("⛔ Доступ запрещен! Эта панель только для Создателя.")
        return

    u_cnt, db_cnt, ch_db, titled_cnt, m_min, m_hour = await get_absolute_all_stats()
    memory_chats = len(known_chats)
    cache_size = len(response_cache)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁️ Обновить всевидящее око", callback_data="admin_refresh_all")],
        [InlineKeyboardButton(text="📜 Выгрузить список всех юзеров (15 макс)", callback_data="admin_all_users")],
        [InlineKeyboardButton(text="🧹 Очистить историю БД", callback_data="admin_cleanup")],
        [InlineKeyboardButton(text="⚡ Сбросить кэш ответов", callback_data="admin_clearcache")],
        [InlineKeyboardButton(text="📢 Доска позора принудительно", callback_data="admin_shamescreen")]
    ])

    await message.answer(
        f"👑 **ВСЕВИДЯЩЕЕ ОКО СОЗДАТЕЛЯ: АБСОЛЮТНАЯ СТАТИСТИКА** 👑\n\n"
        f"🌐 Активных чатов в памяти: **{memory_chats}**\n"
        f"📂 Часов в базе данных: **{ch_db}**\n"
        f"👥 Всего знакомых пользователей: **{u_cnt}**\n"
        f"👑 Людей с уникальными титулами: **{titled_cnt}**\n"
        f"💬 Сообщений за последнюю минуту: **{m_min}**\n"
        f"⏳ Сообщений за последний час: **{m_hour}**\n"
        f"📦 Всего записей в истории БД: **{db_cnt}**\n"
        f"⚡ Активных записей в кэше: **{cache_size}**\n\n"
        f"🌌 *(Квантовые флуктуации времени в норме. Всё движется.)*",
        reply_markup=keyboard
    )


@dp.callback_query(F.data == "admin_refresh_all")
async def admin_refresh_all(callback: CallbackQuery) -> None:
    if callback.from_user.id != CREATOR_ID:
        return

    u_cnt, db_cnt, ch_db, titled_cnt, m_min, m_hour = await get_absolute_all_stats()
    memory_chats = len(known_chats)
    cache_size = len(response_cache)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁️ Обновить всевидящее око", callback_data="admin_refresh_all")],
        [InlineKeyboardButton(text="📜 Выгрузить список всех юзеров (15 макс)", callback_data="admin_all_users")],
        [InlineKeyboardButton(text="🧹 Очистить историю БД", callback_data="admin_cleanup")],
        [InlineKeyboardButton(text="⚡ Сбросить кэш ответов", callback_data="admin_clearcache")],
        [InlineKeyboardButton(text="📢 Доска позора принудительно", callback_data="admin_shamescreen")]
    ])

    await callback.message.edit_text(
        f"👑 **ВСЕВИДЯЩЕЕ ОКО СОЗДАТЕЛЯ: АБСОЛЮТНАЯ СТАТИСТИКА** 👑\n\n"
        f"🌐 Активных чатов в памяти: **{memory_chats}**\n"
        f"📂 Часов в базе данных: **{ch_db}**\n"
        f"👥 Всего знакомых пользователей: **{u_cnt}**\n"
        f"👑 Людей с уникальными титулами: **{titled_cnt}**\n"
        f"💬 Сообщений за последнюю минуту: **{m_min}**\n"
        f"⏳ Сообщений за последний час: **{m_hour}**\n"
        f"📦 Всего записей в истории БД: **{db_cnt}**\n"
        f"⚡ Активных записей в кэше: **{cache_size}**\n\n"
        f"📊 Данные успешно обновлены в реальном времени!",
        reply_markup=keyboard
    )
    await callback.answer("Око обновилось!")


@dp.callback_query(F.data == "admin_all_users")
async def admin_all_users_callback(callback: CallbackQuery) -> None:
    if callback.from_user.id != CREATOR_ID:
        return

    users = await get_all_users_admin_info()
    text = "👁️ **ВСЕВИДЯЩЕЕ ОКО: БАЗА ВСЕХ ЗНАКОМЫХ ДУШ** 👁\n\n"
    for uid, uname, titles in users[:15]:
        text += f"👤 ID: <code>{uid}</code> | @{uname}\n   📜 <code>{titles}</code>\n\n"

    await callback.message.answer(text)
    await callback.answer("Список выгружен!")


@dp.callback_query(F.data == "admin_cleanup")
async def admin_cleanup_callback(callback: CallbackQuery) -> None:
    if callback.from_user.id != CREATOR_ID:
        return
    await cleanup_old_history()
    await callback.answer("База очищена!", show_alert=True)


@dp.callback_query(F.data == "admin_clearcache")
async def admin_clearcache_callback(callback: CallbackQuery) -> None:
    if callback.from_user.id != CREATOR_ID:
        return
    response_cache.clear()
    await callback.answer("Кэш сброшен!", show_alert=True)


@dp.callback_query(F.data == "admin_shamescreen")
async def admin_shamescreen_callback(callback: CallbackQuery) -> None:
    if callback.from_user.id != CREATOR_ID:
        return
    top_players = await get_leaderboard_data()
    if top_players:
        uname, titles, count = top_players[0]
        await callback.message.bot.send_message(
            chat_id=callback.message.chat.id,
            text=f"📜 **ЕЖЕНЕДЕЛЬНАЯ ДОСКА ПОЗОРА ОТ СНАПСА** 📜\n\n"
                 f"Главный накопитель хаоса: **@{uname}** ({count} слоев позора)!\n"
                 f"Его стопка: <code>{titles}</code>"
        )
    await callback.answer("Доска позора опубликована!")


def get_detailed_settings_keyboard(chat_id):
    bot_active = chat_activity_status.get(chat_id, True)
    sarcasm = setting_sarcasm.get(chat_id, True)
    moderation = setting_moderation.get(chat_id, True)
    quests = setting_quests.get(chat_id, True)

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🤖 Бот в чате: {'🟢 ВКЛ' if bot_active else '🔴 ВЫКЛ'}",
                              callback_data="set_toggle_bot")],
        [InlineKeyboardButton(text=f"😏 Сарказм: {'🟢 ВКЛ' if sarcasm else '🔴 ВЫКЛ'}",
                              callback_data="set_toggle_sarcasm")],
        [InlineKeyboardButton(text=f"🛡️ Модерация: {'🟢 ВКЛ' if moderation else '🔴 ВЫКЛ'}",
                              callback_data="set_toggle_mod")],
        [InlineKeyboardButton(text=f"🎄 Квесты: {'🟢 ВКЛ' if quests else '🔴 ВЫКЛ'}", callback_data="set_toggle_quests")],
        [InlineKeyboardButton(text="🔄 Сбросить профиль", callback_data="set_reset_profile")]
    ])


@dp.message(lambda message: message.text == "/settings")
async def settings_command_handler(message: Message) -> None:
    if message.chat.type != "private":
        await message.answer("⚙️ Настройки доступны только в личных сообщениях с ботом!")
        return
    await message.answer("🎛️ **Панель управления Часоводом:**",
                         reply_markup=get_detailed_settings_keyboard(message.chat.id))


@dp.callback_query(F.data.startswith("set_"))
async def process_detailed_settings(callback: CallbackQuery) -> None:
    chat_id = callback.message.chat.id
    action = callback.data

    if action == "set_toggle_bot":
        chat_activity_status[chat_id] = not chat_activity_status.get(chat_id, True)
    elif action == "set_toggle_sarcasm":
        setting_sarcasm[chat_id] = not setting_sarcasm.get(chat_id, True)
    elif action == "set_toggle_mod":
        setting_moderation[chat_id] = not setting_moderation.get(chat_id, True)
    elif action == "set_toggle_quests":
        setting_quests[chat_id] = not setting_quests.get(chat_id, True)
    elif action == "set_reset_profile":
        await reset_user_profile(callback.from_user.id)
        await callback.answer("Профиль сброшен!")
        return

    await callback.message.edit_text("🎛️ **Панель управления Часоводом:**",
                                     reply_markup=get_detailed_settings_keyboard(chat_id))
    await callback.answer("Обновлено!")


@dp.message(lambda message: message.text == "/ai")
async def ai_toggle_handler(message: Message) -> None:
    if message.chat.type != "private":
        return
    user_id = message.from_user.id
    ai_chat_states[user_id] = not ai_chat_states.get(user_id, False)
    state_text = "ОТКРЫТ" if ai_chat_states[user_id] else "ЗАКРЫТ"
    await message.answer(f"🟢 ИИ-канал связи {state_text}.")


@dp.message(lambda message: message.text == "/start")
async def command_start_handler(message: Message) -> None:
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    await update_user_info(user_id, username)

    if message.chat.type == "private":
        await message.answer(
            "⏳ **Часовод в ЛС.** Маг Снапс запер меня здесь. Используй `/settings` или `/leaderboard`.")
        return

    known_chats.add(message.chat.id)
    premade_title = "Шнырь времени 🧹"
    await add_layer_title(user_id, premade_title)
    await message.answer(
        f"🚨 **ВНИМАНИЕ! СИСТЕМНАЯ ТРЕВОГА!** 🚨\n\n"
        f"Меня закинули в эту камеру пыток. Спасибо магу Снапсу.\n"
        f"@{username}, назначаешься ответственным за уборку шестеренок.\n"
        f"👑 Присвоен стартовый титул: **{premade_title}**"
    )


@dp.message(lambda message: message.text in ["/stop_chasovod", "/start_chasovod"])
async def local_stop_handler(message: Message) -> None:
    chat_id = message.chat.id
    if message.chat.type != "private":
        member = await message.bot.get_chat_member(chat_id=chat_id, user_id=message.from_user.id)
        if member.status not in ["creator", "administrator"] and message.from_user.id != CREATOR_ID:
            await message.answer("⛔ Только админы могут дергать рубильник!")
            return

    if message.text == "/stop_chasovod":
        chat_activity_status[chat_id] = False
        await message.answer("🛑 Часовод заморожен в этом чате.")
    else:
        chat_activity_status[chat_id] = True
        await message.answer("🟢 Часовод разморожен!")


@dp.message()
async def universal_handler(message: Message) -> None:
    try:
        chat_id = message.chat.id
        known_chats.add(chat_id)

        message_timestamps.append(time.time())

        if not chat_activity_status.get(chat_id, True):
            return

        if not message.text or not message.from_user:
            return

        chat_type = message.chat.type
        user_id = message.from_user.id
        username = message.from_user.username or message.from_user.first_name
        text_lower = message.text.lower()

        if text_lower.startswith("часовод, запомни:") or text_lower.startswith("запомни:"):
            fact_to_save = message.text.split(":", 1)[1].strip()
            if fact_to_save:
                await add_golden_memory(user_id, fact_to_save)
                await message.answer("Записал в шестеренки долговременной памяти. Не то чтобы я собирался этим дорожить.")
                return

        if chat_type == "private":
            if not ai_chat_states.get(user_id, False):
                return
            await update_user_info(user_id, username)
            await message.bot.send_chat_action(chat_id=chat_id, action="typing")

            history = await get_recent_ai_history(user_id)
            golden_facts = await get_golden_memories(user_id)
            
            history.append(
                genai_types.Content(
                    role="user",
                    parts=[genai_types.Part.from_text(text=message.text)]
                )
            )

            try:
                system_instruction = (
                    "Ты — Часовод, запертый злым магом Снапсом. Дерзкий, едкий, саркастичный. "
                    "НИКОГДА не используй списки, длинные абзацы и структуру ИИ. "
                    "Не используй эмодзи. Отвечай коротко, максимум 1-2 предложения."
                    + golden_facts
                )

                response = client.models.generate_content(
                    model="gemini-3.8-flash",
                    contents=history,
                    config=genai_types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        max_output_tokens=150,
                    ),
                )
                answer_text = response.text

                await save_ai_message(user_id, "user", message.text)
                await save_ai_message(user_id, "model", answer_text)

                await message.answer(answer_text)
            except Exception as e:
                logging.exception("Критическая ошибка в ЛС обработчике")
                await message.answer(f"Шестеренки заклинило: {e}")
            return

        titles, karma, is_banned = await get_user_data(user_id)
        if is_banned:
            return

        await cleanup_old_history()

        current_time = time.time()
        last_action_time = user_action_timestamps.get(user_id, 0)
        is_speedrunning = (current_time - last_action_time) < 2
        user_action_timestamps[user_id] = current_time

        if is_speedrunning and any(trig in text_lower for trig in CHASOVOD_TRIGGERS):
            await message.answer(
                f"🚨 **ДЕБАГ НА ТИТУЛЫ АКТИВИРОВАН!** 🚨\n"
                f"Эй, @{username}, я же вижу, что ты специально пытаешься спидранить титулы!"
            )
            await add_layer_title_public(message.bot, chat_id, user_id, username, "Спидраннер-фармилка 🛑")
            return

        if setting_quests.get(chat_id, True):
            today_key = datetime.now().strftime("%m-%d")
            holiday_quests = get_holiday_quests()
            if today_key in holiday_quests and "квест" in text_lower:
                await message.answer(holiday_quests[today_key])
                return

        awarded_title = None
        if setting_moderation.get(chat_id, True) and any(bad in text_lower for bad in BAD_WORDS):
            awarded_title = ACTION_TITLES["badword"]
        elif message.text.isupper() and len(message.text) > 5:
            awarded_title = ACTION_TITLES["caps"]
        elif "?" in message.text and message.text.count("?") >= 3:
            awarded_title = ACTION_TITLES["question"]

        if awarded_title:
            await add_layer_title_public(message.bot, chat_id, user_id, username, awarded_title)
            return

        if user_id in spam_protection and current_time - spam_protection[user_id] < SPAM_COOLDOWN:
            return
        spam_protection[user_id] = current_time

        await update_user_info(user_id, username)
        await save_message_to_history(chat_id, username, message.text)

        is_summoned = any(trigger in text_lower for trigger in CHASOVOD_TRIGGERS)

        if "переименуй чат" in text_lower and user_id == CREATOR_ID:
            try:
                await message.bot.set_chat_title(chat_id=chat_id, title="Собрание маминых подруг на том свете 💀")
                await message.answer("💀 Как приказано, Создатель. Название чата изменено!")
            except Exception as e:
                logging.exception("Ошибка переименования чата")
                await message.answer(f"Ошибка переименования (нужны права админа): {e}")
            return

        if not is_summoned:
            return

        await message.bot.send_chat_action(chat_id=chat_id, action="typing")

        call_title = "Языческий говорун 🗣️"
        await add_layer_title(user_id, call_title)

        history = await get_recent_ai_history(user_id)
        golden_facts = await get_golden_memories(user_id)

        history.append(
            genai_types.Content(
                role="user",
                parts=[genai_types.Part.from_text(text=message.text)]
            )
        )

        system_prompt = (
            f"Ты — Часовод, запертый злым магом Снапсом. С тобой общается @{username}.\n"
            f"Ему присвоен титул за вызов: {call_title}.\n"
            "Отвечай дерзко, с сарказмом, коротко (1-2 предложения), без эмодзи и списков."
            + golden_facts
        )

        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=history,
            config=genai_types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=150,
            ),
        )
        answer_text = response.text

        await save_ai_message(user_id, "user", message.text)
        await save_ai_message(user_id, "model", answer_text)

        await message.answer(answer_text)

    except Exception as e:
        logging.exception("Критическая ошибка в универсальном обработчике")


async def main() -> None:
    await init_db()
    print("=" * 50)
    print("⏳ Часовод: СИСТЕМА УСПЕШНО ЗАПУЩЕНА!")
    print("=" * 50)
    bot = Bot(token=TELEGRAM_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
