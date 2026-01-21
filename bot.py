import os
import sys
import logging
import random
import asyncio
import sqlite3
from datetime import datetime, time
from typing import Dict, List, Optional, Tuple

# =========== ПАТЧ ДЛЯ ПРОБЛЕМ С IMGHDR В PYTHON 3.13 ===========
try:
    import imghdr
except ImportError:
    import io
    
    class ImghdrCompat:
        @staticmethod
        def what(file, h=None):
            if hasattr(file, 'read'):
                data = file.read(32)
                file.seek(0)
            else:
                with open(file, 'rb') as f:
                    data = f.read(32)
            
            if data.startswith(b'\xff\xd8\xff'):
                return 'jpeg'
            elif data.startswith(b'\x89PNG\r\n\x1a\n'):
                return 'png'
            elif data.startswith(b'GIF87a') or data.startswith(b'GIF89a'):
                return 'gif'
            elif data.startswith(b'BM'):
                return 'bmp'
            elif data.startswith(b'II*\x00') or data.startswith(b'MM\x00*'):
                return 'tiff'
            elif data.startswith(b'RIFF') and data[8:12] == b'WEBP':
                return 'webp'
            return None
    
    sys.modules['imghdr'] = ImghdrCompat()

# Импорты Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler,
    JobQueue
)

# =========== НАСТРОЙКА ===========
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Состояния для диалогов
REGISTRATION, POLL, MAIN_COFFEE, RARE_COFFEE = range(4)

# Переменные окружения
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не установлен! Добавьте его в Environment Variables на Render.")
    sys.exit(1)

# Флаг для управления скриптами
SCRIPTS_ENABLED = True

# База данных SQLite
DB_FILE = 'coffee_bot.db'

# =========== БАЗА ДАННЫХ ===========
def init_database():
    """Инициализация базы данных"""
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        cursor = conn.cursor()
        
        # Таблица пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                name TEXT,
                chastota TEXT,
                count_1 INTEGER DEFAULT 0,
                count_2 INTEGER DEFAULT 0,
                wait_1 INTEGER DEFAULT 0,
                wait_2 INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица настроек
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
        # Инициализация настроек
        cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', 
                      ('scripts_enabled', '1'))
        
        conn.commit()
        conn.close()
        logger.info("✅ База данных инициализирована")
        
    except Exception as e:
        logger.error(f"❌ Ошибка БД: {e}")
        sys.exit(1)

def execute_query(query, params=(), fetchone=False, fetchall=False, commit=False):
    """Выполнение SQL запросов"""
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        cursor = conn.cursor()
        
        cursor.execute(query, params)
        
        if commit:
            conn.commit()
        
        if fetchone:
            result = cursor.fetchone()
        elif fetchall:
            result = cursor.fetchall()
        else:
            result = None
        
        conn.close()
        return result
        
    except Exception as e:
        logger.error(f"❌ Ошибка SQL: {e}")
        return None

# =========== ФУНКЦИИ ДЛЯ РАБОТЫ С БД ===========
def get_user(user_id):
    """Получить данные пользователя"""
    result = execute_query(
        'SELECT * FROM users WHERE user_id = ?',
        (user_id,),
        fetchone=True
    )
    if result:
        return {
            'user_id': result[0],
            'name': result[1],
            'chastota': result[2],
            'count_1': result[3],
            'count_2': result[4],
            'wait_1': result[5],
            'wait_2': result[6]
        }
    return None

def update_user(user_id, **kwargs):
    """Обновить данные пользователя"""
    for key, value in kwargs.items():
        execute_query(
            f'UPDATE users SET {key} = ? WHERE user_id = ?',
            (value, user_id),
            commit=True
        )

def create_user(user_id):
    """Создать нового пользователя"""
    if not get_user(user_id):
        execute_query(
            'INSERT INTO users (user_id) VALUES (?)',
            (user_id,),
            commit=True
        )

def delete_user(user_id):
    """Удалить пользователя"""
    execute_query(
        'DELETE FROM users WHERE user_id = ?',
        (user_id,),
        commit=True
    )

def get_all_users():
    """Получить всех пользователей"""
    results = execute_query('SELECT * FROM users', fetchall=True)
    users = []
    for row in results or []:
        users.append({
            'user_id': row[0],
            'name': row[1],
            'chastota': row[2],
            'count_1': row[3],
            'count_2': row[4],
            'wait_1': row[5],
            'wait_2': row[6]
        })
    return users

def get_active_users():
    """Получить активных пользователей (wait_1=0, wait_2=0)"""
    results = execute_query(
        'SELECT user_id, name FROM users WHERE wait_1 = 0 AND wait_2 = 0',
        fetchall=True
    )
    return results or []

def get_duty_user():
    """Получить текущего дежурного"""
    result = execute_query(
        'SELECT user_id, name FROM users WHERE count_2 = 1',
        fetchone=True
    )
    return result

def get_scripts_enabled():
    """Получить статус скриптов"""
    result = execute_query(
        'SELECT value FROM settings WHERE key = ?',
        ('scripts_enabled',),
        fetchone=True
    )
    return result and result[0] == '1'

def set_scripts_enabled(enabled):
    """Установить статус скриптов"""
    value = '1' if enabled else '0'
    execute_query(
        'UPDATE settings SET value = ? WHERE key = ?',
        (value, 'scripts_enabled'),
        commit=True
    )
    global SCRIPTS_ENABLED
    SCRIPTS_ENABLED = enabled
    logger.info(f"✅ Скрипты {'включены' if enabled else 'отключены'}")

# =========== СКРИПТЫ ===========
def script_1():
    """Скрипт 1: Прирост кофе"""
    execute_query(
        '''UPDATE users 
           SET count_1 = count_1 + 1 
           WHERE chastota = 'Каждый день' AND wait_1 = 0''',
        commit=True
    )
    logger.info("✅ Скрипт_1: Прирост кофе выполнен")

def script_2():
    """Скрипт 2: Поиск дежурного"""
    # Найти максимальное count_1 среди активных пользователей
    result = execute_query(
        '''SELECT MAX(count_1) FROM users 
           WHERE wait_1 = 0 AND wait_2 = 0''',
        fetchone=True
    )
    
    max_count = result[0] if result and result[0] is not None else 0
    
    if max_count > 0:
        # Найти всех пользователей с максимальным count_1
        candidates = execute_query(
            '''SELECT user_id FROM users 
               WHERE count_1 = ? AND wait_1 = 0 AND wait_2 = 0''',
            (max_count,),
            fetchall=True
        )
        
        if candidates:
            # Выбрать случайного из кандидатов
            chosen_user = random.choice(candidates)[0]
            # Назначить дежурным
            execute_query(
                'UPDATE users SET count_2 = 1 WHERE user_id = ?',
                (chosen_user,),
                commit=True
            )
            logger.info(f"✅ Скрипт_2: Выбран дежурный user_id={chosen_user}")

def script_3():
    """Скрипт 3: Обнуление Печальки"""
    execute_query(
        'UPDATE users SET wait_2 = 0 WHERE wait_2 = 1',
        commit=True
    )
    logger.info("✅ Скрипт_3: Обнуление Печальки")

def script_4():
    """Скрипт 4: Погашение дежурства"""
    execute_query(
        'UPDATE users SET count_2 = 0, count_1 = 0 WHERE count_2 = 1',
        commit=True
    )
    logger.info("✅ Скрипт_4: Погашение дежурства")

def script_5():
    """Скрипт 5: Уход домой неполнозанятых"""
    execute_query(
        '''UPDATE users SET wait_1 = 1 
           WHERE chastota = 'Я тут не каждый день' AND wait_1 = 0''',
        commit=True
    )
    logger.info("✅ Скрипт_5: Уход домой неполнозанятых")

async def script_6(context: ContextTypes.DEFAULT_TYPE):
    """Скрипт 6: Информирование"""
    duty = get_duty_user()
    if duty:
        duty_user_id, duty_name = duty
        active_users = get_active_users()
        
        for user_id, user_name in active_users:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"☕ Сегодня дежурный: {duty_name if duty_name else f'Пользователь {duty_user_id}'}"
                )
            except Exception as e:
                logger.error(f"❌ Не удалось отправить сообщение {user_id}: {e}")
        
        logger.info(f"✅ Скрипт_6: Уведомления отправлены {len(active_users)} пользователям")

# =========== ОБРАБОТЧИКИ КОМАНД ===========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Экран 'Стартовый': команда /start"""
    user_id = update.effective_user.id
    
    # Создаем новую строку в таблице
    create_user(user_id)
    
    # Переход на экран "Регистрация"
    await update.message.reply_text(
        "👋 Добро пожаловать!\n\n"
        "Введите ваше имя, оно будет видно всем пользователям, когда будет назначаться дежурный:"
    )
    return REGISTRATION

async def registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Экран 'Регистрация': ввод имени"""
    user_id = update.effective_user.id
    name = update.message.text
    
    # Сохраняем имя в БД
    update_user(user_id, name=name)
    
    # Переход на экран "Опрос"
    keyboard = [
        [
            InlineKeyboardButton("Каждый день", callback_data='daily'),
            InlineKeyboardButton("Я тут не каждый день", callback_data='rarely')
        ],
        [InlineKeyboardButton("Я теперь НЕ пью кофе", callback_data='no_coffee')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "☕ Как часто вы пьете кофе?",
        reply_markup=reply_markup
    )
    return POLL

async def poll_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Экран 'Опрос': выбор частоты"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    data = query.data
    
    if data == 'no_coffee':
        # Удаляем данные пользователя
        delete_user(user_id)
        await query.edit_message_text(
            "🗑️ Ваши данные удалены.\n\n"
            "Чтобы начать заново, нажмите /start"
        )
        return ConversationHandler.END
    
    if data == 'daily':
        # Сохраняем "Каждый день" и переходим к "Главные кофеманы"
        update_user(user_id, chastota='Каждый день')
        
        keyboard = [
            [InlineKeyboardButton("Я некоторое время не пью кофе", callback_data='temp_no_coffee')],
            [InlineKeyboardButton("Я дежурный, но не смогу вымыть кофемашинку", callback_data='cant_duty')],
            [InlineKeyboardButton("Я Вернулся", callback_data='returned')],
            [InlineKeyboardButton("Я теперь пью кофе по другому", callback_data='change_habit')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "✅ Теперь вам будет приходить уведомления кто сегодня дежурный",
            reply_markup=reply_markup
        )
        return MAIN_COFFEE
    
    elif data == 'rarely':
        # Сохраняем "Я тут не каждый день" и переходим к "Редкие кофеманы"
        update_user(user_id, chastota='Я тут не каждый день')
        
        keyboard = [
            [InlineKeyboardButton("Я сегодня пью кофе", callback_data='today_coffee')],
            [InlineKeyboardButton("Я дежурный, но не смогу вымыть кофемашинку", callback_data='cant_duty_rare')],
            [InlineKeyboardButton("Я теперь пью кофе по другому", callback_data='change_habit_rare')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "⏰ Когда вы придете, отметьтесь",
            reply_markup=reply_markup
        )
        return RARE_COFFEE

async def main_coffee_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Экран 'Главные кофеманы'"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    data = query.data
    
    if data == 'temp_no_coffee':
        update_user(user_id, wait_1=1)
        await context.bot.send_message(
            chat_id=user_id,
            text="⏸️ Когда вы вернетесь отметьте это"
        )
        await query.edit_message_text("✅ Вы отметили временное отсутствие")
        
    elif data == 'cant_duty':
        update_user(user_id, wait_2=1, count_2=0)
        await context.bot.send_message(
            chat_id=user_id,
            text="😔 Печалька"
        )
        # Запускаем скрипты
        script_2()
        await script_6(context)
        await query.edit_message_text("✅ Отказ от дежурства учтен")
        
    elif data == 'returned':
        update_user(user_id, wait_1=0)
        await context.bot.send_message(
            chat_id=user_id,
            text="🎉 Ура!"
        )
        await query.edit_message_text("✅ Вы вернулись!")
        
    elif data == 'change_habit':
        # Возвращаемся к опросу
        keyboard = [
            [
                InlineKeyboardButton("Каждый день", callback_data='daily'),
                InlineKeyboardButton("Я тут не каждый день", callback_data='rarely')
            ],
            [InlineKeyboardButton("Я теперь НЕ пью кофе", callback_data='no_coffee')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "☕ Как часто вы пьете кофе?",
            reply_markup=reply_markup
        )
        return POLL
    
    return MAIN_COFFEE

async def rare_coffee_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Экран 'Редкие кофеманы'"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    data = query.data
    
    if data == 'today_coffee':
        user = get_user(user_id)
        current_count = user['count_1'] if user else 0
        update_user(user_id, count_1=current_count + 1, wait_1=0)
        
        await context.bot.send_message(
            chat_id=user_id,
            text="✅ Спасибо"
        )
        await query.edit_message_text("✅ Ваше присутствие отмечено")
        
    elif data == 'cant_duty_rare':
        update_user(user_id, wait_2=1, count_2=0)
        await context.bot.send_message(
            chat_id=user_id,
            text="😔 Печалька"
        )
        # Запускаем скрипты
        script_2()
        await script_6(context)
        await query.edit_message_text("✅ Отказ от дежурства учтен")
        
    elif data == 'change_habit_rare':
        # Возвращаемся к опросу
        keyboard = [
            [
                InlineKeyboardButton("Каждый день", callback_data='daily'),
                InlineKeyboardButton("Я тут не каждый день", callback_data='rarely')
            ],
            [InlineKeyboardButton("Я теперь НЕ пью кофе", callback_data='no_coffee')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "☕ Как часто вы пьете кофе?",
            reply_markup=reply_markup
        )
        return POLL
    
    return RARE_COFFEE

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена диалога"""
    await update.message.reply_text(
        "❌ Действие отменено. Используйте /start для начала."
    )
    return ConversationHandler.END

# =========== СКРЫТЫЕ КОМАНДЫ ===========
async def hollidaon(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Скрытая команда: отключить скрипты по времени"""
    set_scripts_enabled(False)
    await update.message.reply_text("✅ Скрипты по времени ОТКЛЮЧЕНЫ")

async def hollidayoff(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Скрытая команда: включить скрипты по времени"""
    set_scripts_enabled(True)
    await update.message.reply_text("✅ Скрипты по времени ВКЛЮЧЕНЫ")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать статус пользователя"""
    user = get_user(update.effective_user.id)
    
    if user:
        duty = get_duty_user()
        duty_text = duty[1] if duty else "Дежурный еще не выбран"
        
        status_msg = f"""
📊 Ваш статус:
👤 Имя: {user['name'] or 'Не указано'}
📅 Режим: {user['chastota'] or 'Не указан'}
☕ Чашек: {user['count_1']}
🎖️ Дежурств: {user['count_2']}
🚫 Отсутствие: {'Да' if user['wait_1'] else 'Нет'}
😔 Печалька: {'Да' if user['wait_2'] else 'Нет'}
👑 Дежурный: {duty_text}
⚙️ Скрипты: {'Включены' if SCRIPTS_ENABLED else 'Отключены'}
        """
    else:
        status_msg = "❌ Вы не зарегистрированы. Используйте /start"
    
    await update.message.reply_text(status_msg)

# =========== ФУНКЦИИ ПЛАНИРОВЩИКА ===========
async def daily_14_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Запускается в 14:00 с понедельника по пятницу"""
    if not SCRIPTS_ENABLED:
        logger.info("⏸️ Скрипты отключены, пропускаем выполнение")
        return
    
    logger.info("⏰ Запуск скриптов 14:00")
    script_1()  # Прирост кофе
    script_2()  # Поиск дежурного
    await script_6(context)  # Информирование

async def daily_21_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Запускается в 21:00 с понедельника по пятницу"""
    if not SCRIPTS_ENABLED:
        logger.info("⏸️ Скрипты отключены, пропускаем выполнение")
        return
    
    logger.info("⏰ Запуск скриптов 21:00")
    script_3()  # Обнуление Печальки
    script_4()  # Погашение дежурства
    script_5()  # Уход домой неполнозанятых

# =========== ОСНОВНАЯ ФУНКЦИЯ ===========
def main() -> None:
    """Запуск бота"""
    # Инициализация БД
    init_database()
    
    # Загрузка статуса скриптов из БД
    global SCRIPTS_ENABLED
    SCRIPTS_ENABLED = get_scripts_enabled()
    logger.info(f"✅ Статус скриптов: {'ВКЛЮЧЕНЫ' if SCRIPTS_ENABLED else 'ОТКЛЮЧЕНЫ'}")
    
    # Создание приложения
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Настройка ConversationHandler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            REGISTRATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, registration)
            ],
            POLL: [
                CallbackQueryHandler(poll_handler)
            ],
            MAIN_COFFEE: [
                CallbackQueryHandler(main_coffee_handler)
            ],
            RARE_COFFEE: [
                CallbackQueryHandler(rare_coffee_handler)
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    
    # Добавление обработчиков
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('status', status))
    application.add_handler(CommandHandler('hollidaon', hollidaon))
    application.add_handler(CommandHandler('hollidayoff', hollidayoff))
    
    # Настройка планировщика
    job_queue = application.job_queue
    if job_queue:
        # Понедельник-пятница в 14:00 UTC
        job_queue.run_daily(
            daily_14_job,
            time=time(hour=14, minute=0),
            days=(0, 1, 2, 3, 4)  # 0=Monday, 4=Friday
        )
        
        # Понедельник-пятница в 21:00 UTC
        job_queue.run_daily(
            daily_21_job,
            time=time(hour=21, minute=0),
            days=(0, 1, 2, 3, 4)
        )
        
        logger.info("✅ Планировщик скриптов настроен")
    
    # Запуск бота
    logger.info("✅ Бот запущен и готов к работе")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
