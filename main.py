import logging
from aiogram import Bot, Dispatcher, types
from aiogram import F
from aiogram.types import FSInputFile
import asyncio
import os

from dotenv import load_dotenv
logging.basicConfig(level=logging.INFO)
load_dotenv()

API_TOKEN =os.getenv("TOKEN") 

STICKER_ID = "CAACAgIAAxkBAAEPbhZo0q3v0Ookf2woRfS7ib-jO06OOAACzXUAAnhRCUlLN_CT6JyFLzYE"

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

@dp.message(F.text.contains('шумахер'))
async def shumacher_handler(message: types.Message):
    await message.answer_sticker(STICKER_ID)

async def main():
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())

