import logging
import sqlite3
import asyncio
import re
import os
from typing import Dict, List, Optional
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация
API_TOKEN =os.getenv("TOKEN")
ADMIN_USER_IDS = ['']

# Инициализация бота
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Глобальное хранилище для текущих сессий просмотра ассоциаций
user_sessions = {}


# Состояния для FSM
class StickerStates(StatesGroup):
    waiting_for_associations = State()
    waiting_for_sticker = State()
    editing_associations = State()


# Класс для работы с базой данных
class StickerDatabase:
    def __init__(self, db_path: str = os.getenv("DATABASE_PATH")):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        """Инициализация базы данных"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Таблица для стикеров и ассоциаций
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sticker_associations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                sticker_id TEXT NOT NULL,
                association TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(sticker_id, association)
            )
        ''')

        # Таблица для статистики использования
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS usage_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                sticker_id TEXT NOT NULL,
                association TEXT NOT NULL,
                used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Индексы для быстрого поиска
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_association ON sticker_associations(association)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_sticker ON sticker_associations(sticker_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_user ON sticker_associations(user_id)')

        conn.commit()
        conn.close()

    def add_association(self, user_id: int, sticker_id: str, association: str) -> bool:
        """Добавление новой ассоциации"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                'INSERT OR IGNORE INTO sticker_associations (user_id, sticker_id, association) VALUES (?, ?, ?)',
                (user_id, sticker_id, association.lower().strip())
            )
            conn.commit()
            success = cursor.rowcount > 0
            conn.close()
            return success
        except Exception as e:
            logger.error(f"Error adding association: {e}")
            return False

    def get_sticker_by_association(self, association: str) -> Optional[str]:
        """Поиск стикера по ассоциации"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                'SELECT sticker_id FROM sticker_associations WHERE association LIKE ? ORDER BY id DESC LIMIT 1',
                (f'%{association.lower().strip()}%',)
            )
            result = cursor.fetchone()
            conn.close()
            return result[0] if result else None
        except Exception as e:
            logger.error(f"Error getting sticker: {e}")
            return None

    def get_user_associations(self, user_id: int) -> List[tuple]:
        """Получение всех ассоциаций пользователя"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                'SELECT sticker_id, association, created_at FROM sticker_associations WHERE user_id = ? ORDER BY created_at DESC',
                (user_id,)
            )
            result = cursor.fetchall()
            conn.close()
            return result
        except Exception as e:
            logger.error(f"Error getting user associations: {e}")
            return []

    def delete_association(self, user_id: int, sticker_id: str, association: str) -> bool:
        """Удаление ассоциации"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                'DELETE FROM sticker_associations WHERE user_id = ? AND sticker_id = ? AND association = ?',
                (user_id, sticker_id, association)
            )
            conn.commit()
            success = cursor.rowcount > 0
            conn.close()
            return success
        except Exception as e:
            logger.error(f"Error deleting association: {e}")
            return False

    def log_usage(self, user_id: int, sticker_id: str, association: str):
        """Логирование использования стикера"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO usage_stats (user_id, sticker_id, association) VALUES (?, ?, ?)',
                (user_id, sticker_id, association)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error logging usage: {e}")

    def get_stats(self) -> Dict:
        """Получение статистики"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Общее количество ассоциаций
            cursor.execute('SELECT COUNT(*) FROM sticker_associations')
            total_associations = cursor.fetchone()[0]

            # Количество уникальных стикеров
            cursor.execute('SELECT COUNT(DISTINCT sticker_id) FROM sticker_associations')
            unique_stickers = cursor.fetchone()[0]

            # Количество пользователей
            cursor.execute('SELECT COUNT(DISTINCT user_id) FROM sticker_associations')
            total_users = cursor.fetchone()[0]

            # Топ ассоциаций
            cursor.execute('''
                SELECT association, COUNT(*) as usage_count 
                FROM usage_stats 
                GROUP BY association 
                ORDER BY usage_count DESC 
                LIMIT 10
            ''')
            top_associations = cursor.fetchall()

            conn.close()

            return {
                'total_associations': total_associations,
                'unique_stickers': unique_stickers,
                'total_users': total_users,
                'top_associations': top_associations
            }
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {}


# Инициализация базы данных
db = StickerDatabase()


def create_main_keyboard():
    """Создание основной клавиатуры"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить стикер")],
            [KeyboardButton(text="📋 Мои стикеры"), KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="❓ Помощь")]
        ],
        resize_keyboard=True,
        persistent=True
    )
    return keyboard


def create_inline_keyboard_for_associations(associations: List[tuple], page: int = 0):
    """Создание inline-клавиатуры для просмотра ассоциаций"""
    keyboard = []
    items_per_page = 8  # Уменьшил количество элементов на странице
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page

    page_associations = associations[start_idx:end_idx]

    for i, (sticker_id, association, created_at) in enumerate(page_associations):
        # Ограничиваем длину текста кнопки и callback_data
        display_text = association[:20] + "..." if len(association) > 20 else association

        # Создаем короткий идентификатор для callback_data
        callback_data = f"del_{i + start_idx}_{page}"

        keyboard.append([
            InlineKeyboardButton(
                text=f"🗑 {display_text}",
                callback_data=callback_data
            )
        ])

    # Навигация
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️", callback_data=f"page_{page - 1}"))
    if end_idx < len(associations):
        nav_buttons.append(InlineKeyboardButton(text="➡️", callback_data=f"page_{page + 1}"))

    if nav_buttons:
        keyboard.append(nav_buttons)

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


@dp.message(Command("start"))
async def start_command(message: types.Message):
    if message.chat.type != "private":
        return
    """Обработчик команды /start"""
    welcome_text = """
🤖 <b>Добро пожаловать в бота флубильни безопасников!</b>

Этот бот поможет вам создать собственную коллекцию стикеров с текстовыми ассоциациями.

<b>Как это работает:</b>
• Добавляйте стикеры с текстовыми ассоциациями
• Пишите ключевые слова - получайте стикеры
• Управляйте своей коллекцией

Начните с кнопки "➕ Добавить стикер"!
    """

    await message.answer(
        welcome_text,
        parse_mode="HTML",
        reply_markup=create_main_keyboard()
    )


@dp.message(Command("help"))
async def help_command(message: types.Message):

    """Обработчик команды помощи"""
    if message.chat.type != "private":
        return
    help_text = """
📖 <b>Подробная инструкция</b>

<b>Добавление стикера:</b>
1️⃣ Нажмите "➕ Добавить стикер"
2️⃣ Введите ассоциации через запятую (например: "смех, радость, веселье")
3️⃣ Отправьте стикер

<b>Использование:</b>
• Просто напишите любое слово из ваших ассоциаций
• Бот автоматически отправит соответствующий стикер
• Работает поиск по части слова!

<b>Управление:</b>
• "📋 Мои стикеры" - просмотр и удаление
• "📊 Статистика" - информация о боте
• Можно удалять ненужные ассоциации

<b>Примеры ассоциаций:</b>
• "привет, hello, здарова, йо"
• "грусть, печаль, слезы"
• "кот, котик, мяу"

<b>Советы:</b>
🔹 Используйте синонимы для лучшего поиска
🔹 Добавляйте популярные сокращения
🔹 Не забывайте про эмоции и контекст
    """

    await message.answer(help_text, parse_mode="HTML")


@dp.message(F.text == "❓ Помощь")
async def help_button(message: types.Message):
    if message.chat.type != "private":
        return
    """Обработчик кнопки помощи"""
    await help_command(message)


@dp.message(F.text == "➕ Добавить стикер")
async def add_sticker_start(message: types.Message, state: FSMContext):
    if message.chat.type != "private":
        return
    """Начало процесса добавления стикера"""
    await state.set_state(StickerStates.waiting_for_associations)

    instruction = """
📝 <b>Добавление нового стикера</b>

Введите текстовые ассоциации через запятую.

<b>Примеры:</b>
• <code>привет, hello, здарова, йо</code>
• <code>смех, ржач, лол, ахаха</code>
• <code>грусть, печаль, слезы</code>

<i>После ввода ассоциаций отправьте стикер!</i>
    """

    await message.answer(instruction, parse_mode="HTML")


@dp.message(StateFilter(StickerStates.waiting_for_associations), F.text)
async def process_associations(message: types.Message, state: FSMContext):
    if message.chat.type != "private":

        return
    """Обработка введенных ассоциаций"""
    associations_text = message.text.strip()

    if not associations_text:
        await message.answer("❌ Пожалуйста, введите хотя бы одну ассоциацию!")
        return

    # Парсинг ассоциаций
    associations = [assoc.strip().lower() for assoc in associations_text.split(',') if assoc.strip()]

    if not associations:
        await message.answer("❌ Некорректный формат! Введите ассоциации через запятую.")
        return
    for association in associations:
        if len(association) < 3:
            await message.answer("слишком короткая ассоциация")
            return
    if len(associations) > 20:
        await message.answer("❌ Слишком много ассоциаций! Максимум 20.")
        return

    # Сохранение в состоянии
    await state.update_data(associations=associations)
    await state.set_state(StickerStates.waiting_for_sticker)

    associations_preview = ', '.join(associations[:5])
    if len(associations) > 5:
        associations_preview += "..."

    await message.answer(
        f"✅ Ассоциации сохранены: <code>{associations_preview}</code>\n\n"
        f"📤 Теперь отправьте стикер!",
        parse_mode="HTML"
    )


@dp.message(StateFilter(StickerStates.waiting_for_sticker), F.sticker)
async def process_sticker(message: types.Message, state: FSMContext):
    """Обработка отправленного стикера"""
    if message.chat.type != "private":
        return
    data = await state.get_data()
    associations = data.get('associations', [])

    if not associations:
        await message.answer("❌ Ошибка! Сначала введите ассоциации.")
        await state.clear()
        return

    sticker_id = message.sticker.file_id
    user_id = message.from_user.id

    # Сохранение ассоциаций в базу данных
    success_count = 0
    for association in associations:
        if db.add_association(user_id, sticker_id, association):
            success_count += 1

    await state.clear()

    if success_count > 0:
        await message.answer(
            f"✅ <b>Стикер успешно добавлен!</b>\n\n"
            f"📝 Сохранено ассоциаций: {success_count}/{len(associations)}\n"
            f"🔍 Теперь можете писать любое из слов: <code>{', '.join(associations[:3])}</code>\n\n"
            f"<i>Некоторые ассоциации могли быть пропущены, если уже существуют.</i>",
            parse_mode="HTML",
            reply_markup=create_main_keyboard()
        )
    else:
        await message.answer(
            "⚠️ Стикер не добавлен - все ассоциации уже существуют!",
            reply_markup=create_main_keyboard()
        )


@dp.message(StateFilter(StickerStates.waiting_for_sticker))
async def wrong_content_for_sticker(message: types.Message, state: FSMContext):
    if message.chat.type != "private":
        return
    """Обработка неправильного контента вместо стикера"""
    if message.text:
        await message.answer("❌ Пожалуйста, отправьте именно стикер, а не текст!")
    else:
        await message.answer("❌ Пожалуйста, отправьте именно стикер!")


@dp.message(F.text == "📋 Мои стикеры")
async def show_user_stickers(message: types.Message):
    if message.chat.type != "private":
        return
    """Показать стикеры пользователя"""
    user_id = message.from_user.id
    associations = db.get_user_associations(user_id)

    if not associations:
        await message.answer(
            "📭 У вас пока нет сохраненных стикеров!\n\n"
            "Нажмите \"➕ Добавить стикер\" чтобы начать.",
            reply_markup=create_main_keyboard()
        )
        return

    # Сохраняем ассоциации в сессии пользователя
    user_sessions[user_id] = associations

    # Группировка по стикерам
    sticker_groups = {}
    for sticker_id, association, created_at in associations:
        if sticker_id not in sticker_groups:
            sticker_groups[sticker_id] = []
        sticker_groups[sticker_id].append(association)

    text = f"📋 <b>Ваши стикеры ({len(sticker_groups)} шт.)</b>\n\n"

    for i, (sticker_id, assocs) in enumerate(sticker_groups.items(), 1):
        assocs_text = ', '.join(assocs[:5])
        if len(assocs) > 5:
            assocs_text += f" и еще {len(assocs) - 5}..."
        text += f"{i}. <code>{assocs_text}</code>\n"

    text += "\n💡 Нажмите на ассоциацию ниже, чтобы удалить её:"

    keyboard = create_inline_keyboard_for_associations(associations)

    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@dp.message(F.text == "📊 Статистика")
async def show_stats(message: types.Message):
    if message.chat.type != "private":
        return
    """Показать статистику"""
    stats = db.get_stats()
    user_associations = db.get_user_associations(message.from_user.id)

    if not stats:
        await message.answer("❌ Ошибка получения статистики!")
        return

    text = f"""
📊 <b>Статистика бота</b>

👤 <b>Ваша статистика:</b>
• Стикеров добавлено: {len(set(assoc[0] for assoc in user_associations))}
• Ассоциаций создано: {len(user_associations)}

🌐 <b>Общая статистика:</b>/ы
• Всего пользователей: {stats.get('total_users', 0)}
• Всего стикеров: {stats.get('unique_stickers', 0)}
• Всего ассоциаций: {stats.get('total_associations', 0)}
    """

    top_associations = stats.get('top_associations', [])
    if top_associations:
        text += "\n🔥 <b>Топ ассоциаций:</b>\n"
        for i, (association, count) in enumerate(top_associations[:5], 1):
            text += f"{i}. {association} ({count} раз)\n"

    await message.answer(text, parse_mode="HTML")


@dp.callback_query(F.data.startswith("del_"))
async def delete_association_callback(callback: types.CallbackQuery):
    if callback.chat.type != "private":
        return
    """Обработка удаления ассоциации"""
    try:
        data_parts = callback.data.split("_")
        if len(data_parts) != 3:
            await callback.answer("❌ Ошибка данных!")
            return

        association_index = int(data_parts[1])
        page = int(data_parts[2])
        user_id = callback.from_user.id

        # Получаем актуальные ассоциации пользователя
        if user_id not in user_sessions:
            user_sessions[user_id] = db.get_user_associations(user_id)

        associations = user_sessions[user_id]

        if association_index >= len(associations):
            await callback.answer("❌ Ассоциация не найдена!")
            return

        sticker_id, association, created_at = associations[association_index]

        success = db.delete_association(user_id, sticker_id, association)

        if success:
            await callback.answer(f"✅ Ассоциация '{association}' удалена!")

            # Обновляем сессию
            user_sessions[user_id] = db.get_user_associations(user_id)
            updated_associations = user_sessions[user_id]

            if updated_associations:
                keyboard = create_inline_keyboard_for_associations(updated_associations, page)

                # Обновляем текст с новой статистикой
                sticker_groups = {}
                for sid, assoc, _ in updated_associations:
                    if sid not in sticker_groups:
                        sticker_groups[sid] = []
                    sticker_groups[sid].append(assoc)

                text = f"📋 <b>Ваши стикеры ({len(sticker_groups)} шт.)</b>\n\n"
                for i, (sid, assocs) in enumerate(sticker_groups.items(), 1):
                    assocs_text = ', '.join(assocs[:5])
                    if len(assocs) > 5:
                        assocs_text += f" и еще {len(assocs) - 5}..."
                    text += f"{i}. <code>{assocs_text}</code>\n"
                text += "\n💡 Нажмите на ассоциацию ниже, чтобы удалить её:"

                await callback.message.edit_text(text, parse_mode="HTML")
                await callback.message.edit_reply_markup(reply_markup=keyboard)
            else:
                await callback.message.edit_text(
                    "📭 Все стикеры удалены!\n\n"
                    "Нажмите \"➕ Добавить стикер\" чтобы добавить новые."
                )
        else:
            await callback.answer("❌ Ошибка удаления!")

    except (ValueError, IndexError) as e:
        logger.error(f"Error in delete callback: {e}")
        await callback.answer("❌ Ошибка обработки!")
    except Exception as e:
        logger.error(f"Unexpected error in delete callback: {e}")
        await callback.answer("❌ Произошла ошибка!")


@dp.callback_query(F.data.startswith("page_"))
async def pagination_callback(callback: types.CallbackQuery):
    if callback.chat.type != "private":
        return
    """Обработка пагинации"""
    try:
        page = int(callback.data.split("_")[1])
        user_id = callback.from_user.id

        # Получаем актуальные ассоциации
        if user_id not in user_sessions:
            user_sessions[user_id] = db.get_user_associations(user_id)

        associations = user_sessions[user_id]
        keyboard = create_inline_keyboard_for_associations(associations, page)

        await callback.message.edit_reply_markup(reply_markup=keyboard)
        await callback.answer()

    except (ValueError, IndexError) as e:
        logger.error(f"Error in pagination: {e}")
        await callback.answer("❌ Ошибка навигации!")
    except Exception as e:
        logger.error(f"Unexpected error in pagination: {e}")
        await callback.answer("❌ Произошла ошибка!")


# Основной обработчик текстовых сообщений для поиска стикеров
@dp.message(F.text)
async def search_sticker(message: types.Message, state: FSMContext):
    logger.info(message.chat.id)
    """Поиск и отправка стикера по тексту"""
    # Проверяем, не находимся ли мы в состоянии ввода данных
    current_state = await state.get_state()
    if current_state is not None:
        return  # Если в состоянии, пропускаем поиск стикеров

    text = message.text.lower().strip()

    # Игнорирование команд и кнопок
    if text.startswith('/') or text in ["➕ Добавить стикер", "📋 Мои стикеры", "📊 Статистика", "❓ Помощь"]:
        return

    # Поиск по всему тексту и отдельным словам
    sticker_id = None
    matched_association = None

    # Сначала ищем точное совпадение
    sticker_id = db.get_sticker_by_association(text)
    if sticker_id:
        matched_association = text
    else:
        # Поиск по словам
        words = re.findall(r'\b\w+\b', text)
        for word in words:
            if len(word) >= 2:  # Минимум 2 символа для поиска
                sticker_id = db.get_sticker_by_association(word)
                if sticker_id:
                    matched_association = word
                    break

    if sticker_id:
        try:
            await message.answer_sticker(sticker_id)
            # Логирование использования
            db.log_usage(message.from_user.id, sticker_id, matched_association)
        except Exception as e:
            logger.error(f"Error sending sticker: {e}")
            await message.answer("❌ Ошибка отправки стикера. Возможно, стикер недоступен.")


@dp.message(Command("stats"))
async def stats_command(message: types.Message):
    if message.chat.type != "private":
        return
    """Команда статистики для админов"""
    if message.from_user.id not in ADMIN_USER_IDS:
        await show_stats(message)
        return

    stats = db.get_stats()
    text = f"""
🔧 <b>Административная статистика</b>

📈 <b>Общие показатели:</b>
• Пользователей: {stats.get('total_users', 0)}
• Уникальных стикеров: {stats.get('unique_stickers', 0)}
• Всего ассоциаций: {stats.get('total_associations', 0)}

🔥 <b>Популярные ассоциации:</b>
    """

    for i, (association, count) in enumerate(stats.get('top_associations', [])[:10], 1):
        text += f"{i}. {association} - {count} использований\n"

    await message.answer(text, parse_mode="HTML")


@dp.message(Command("mystickers"))
async def mystickers_command(message: types.Message):

    """Команда для показа стикеров пользователя"""
    if message.chat.type != "private":
        return
    await show_user_stickers(message)


# Обработка ошибок
@dp.error()
async def error_handler(event, exception):
    if event.chat.type != "private":
        return
    """Глобальный обработчик ошибок"""
    logger.error(f"Error occurred: {exception}")

    # Попытка отправить уведомление пользователю
    try:
        if hasattr(event, 'message') and event.message:
            await event.message.answer(
                "⚠️ Произошла ошибка. Попробуйте еще раз или обратитесь к администратору.",
                reply_markup=create_main_keyboard()
            )
    except:
        pass  # Игнорируем ошибки при отправке уведомления

    return True


async def main():
    """Основная функция запуска бота"""
    # Проверка токена
    if API_TOKEN == '123' or len(API_TOKEN) < 10:
        logger.error("❌ ОШИБКА: Не установлен правильный токен бота!")
        logger.error("📝 Получите токен у @BotFather в Telegram")
        logger.error("🔧 Замените API_TOKEN = '123' на ваш реальный токен")
        return

    logger.info("🚀 Запуск StickerBot...")

    try:
        # Проверка токена
        bot_info = await bot.get_me()
        logger.info(f"✅ Бот авторизован: @{bot_info.username}")

        # Установка команд бота
        await bot.set_my_commands([
            types.BotCommand(command="start", description="🚀 Начать работу с ботом"),
            types.BotCommand(command="help", description="❓ Помощь и инструкции"),
            types.BotCommand(command="mystickers", description="📋 Мои стикеры"),
            types.BotCommand(command="stats", description="📊 Статистика")
        ])

        logger.info("🎯 Бот готов к работе!")
        await dp.start_polling(bot, skip_updates=True)

    except Exception as e:
        if "Unauthorized" in str(e):
            logger.error("❌ ОШИБКА АВТОРИЗАЦИИ:")
            logger.error("🔑 Неправильный токен бота!")
            logger.error("📝 Получите новый токен у @BotFather")
            logger.error("🔧 Замените API_TOKEN в коде на правильный")
        else:
            logger.error(f"❌ Ошибка запуска бота: {e}")
    finally:
        await bot.session.close()


if __name__ == '__main__':
    # Инструкция по получению токена
    if API_TOKEN == '123':
        print("=" * 60)
        print("⚠️  ВНИМАНИЕ: Токен бота не настроен!")
        print("=" * 60)
        print("📝 Как получить токен бота:")
        print("1️⃣  Откройте Telegram")
        print("2️⃣  Найдите @BotFather")
        print("3️⃣  Отправьте команду /newbot")
        print("4️⃣  Следуйте инструкциям")
        print("5️⃣  Скопируйте полученный токен")
        print("6️⃣  Замените API_TOKEN = '123' на ваш токен")
        print("=" * 60)
        print("Пример токена: 1234567890:ABCDEF1234567890abcdef1234567890ABC")
        print("=" * 60)

    asyncio.run(main())
