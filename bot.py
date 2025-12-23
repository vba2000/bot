import os
import asyncio
from datetime import timedelta
from typing import Set

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# =====================
# CONFIG FROM ENV
# =====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")

ADMIN_IDS: Set[int] = {
    int(admin_id)
    for admin_id in os.getenv("ADMIN_IDS", "").split(",")
}

if not BOT_TOKEN or not CHANNEL_ID or not ADMIN_IDS or not WEBHOOK_URL:
    raise RuntimeError("Не заданы обязательные переменные окружения")

# =====================
# INIT
# =====================
bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# =====================
# IN-MEMORY RATE LIMIT
# =====================
active_requests: Set[int] = set()

# =====================
# FSM
# =====================
class Registration(StatesGroup):
    consent = State()
    name = State()
    address = State()
    phone = State()

# =====================
# KEYBOARDS
# =====================
consent_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Согласен")]],
    resize_keyboard=True,
    one_time_keyboard=True,
)

def admin_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Одобрить",
                    callback_data=f"approve:{user_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"reject:{user_id}",
                ),
            ]
        ]
    )

# =====================
# HANDLERS
# =====================
@dp.message(F.text == "/start")
async def start(message: Message, state: FSMContext):
    if message.from_user.id in active_requests:
        await message.answer(
            "Ваша заявка уже находится на рассмотрении администратора."
        )
        return

    await message.answer(
        "Для регистрации необходимо согласие на обработку персональных данных.\n\n"
        "Я даю согласие на обработку моих персональных данных "
        "(имя, адрес, номер телефона) в соответствии с ФЗ-152 "
        "«О персональных данных» исключительно в целях "
        "рассмотрения заявки на вступление в канал.\n\n"
        "Обработка включает сбор и передачу данных администраторам "
        "без хранения.\n\n"
        "Нажмите «Согласен» для продолжения.",
        reply_markup=consent_kb,
    )
    await state.set_state(Registration.consent)


@dp.message(Registration.consent, F.text == "Согласен")
async def consent_given(message: Message, state: FSMContext):
    await message.answer("Введите ваше имя:")
    await state.set_state(Registration.name)


@dp.message(Registration.name)
async def get_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Введите адрес:")
    await state.set_state(Registration.address)


@dp.message(Registration.address)
async def get_address(message: Message, state: FSMContext):
    await state.update_data(address=message.text)
    await message.answer("Введите номер телефона:")
    await state.set_state(Registration.phone)


@dp.message(Registration.phone)
async def get_phone(message: Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id
    active_requests.add(user_id)

    text_for_admin = (
        "Новая заявка на вступление\n\n"
        f"Telegram: @{message.from_user.username}\n"
        f"ID: {user_id}\n\n"
        f"Имя: {data['name']}\n"
        f"Адрес: {data['address']}\n"
        f"Телефон: {message.text}"
    )

    for admin_id in ADMIN_IDS:
        await bot.send_message(
            admin_id,
            text_for_admin,
            reply_markup=admin_kb(user_id),
        )

    await message.answer(
        "Ваша заявка отправлена администраторам.\n"
        "Ожидайте решения."
    )

    await state.clear()


@dp.callback_query(F.data.startswith("approve:"))
async def approve_user(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])

    invite = await bot.create_chat_invite_link(
        chat_id=CHANNEL_ID,
        member_limit=1,
        expire_date=timedelta(hours=24),
    )

    await bot.send_message(
        user_id,
        "Ваша заявка одобрена.\n\n"
        f"Ссылка для вступления (одноразовая, 24 часа):\n"
        f"{invite.invite_link}",
    )

    active_requests.discard(user_id)

    await callback.message.edit_text(
        callback.message.text + "\n\n✅ Заявка одобрена"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("reject:"))
async def reject_user(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])

    await bot.send_message(
        user_id,
        "К сожалению, ваша заявка отклонена администраторами."
    )

    active_requests.discard(user_id)

    await callback.message.edit_text(
        callback.message.text + "\n\n❌ Заявка отклонена"
    )
    await callback.answer()

# =====================
# WEBHOOK ENTRYPOINT
# =====================
async def main():
    await bot.set_webhook(WEBHOOK_URL + WEBHOOK_PATH)

    app = web.Application()
    SimpleRequestHandler(dp, bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(app, "0.0.0.0", 8080)
    await site.start()

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
