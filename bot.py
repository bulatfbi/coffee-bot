import os
import logging
import sys
import random
from datetime import datetime, time
import sqlite3
import threading
import time as tm

# Для python-telegram-bot 13.x
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater, CommandHandler, CallbackQueryHandler,
    MessageHandler, Filters, ConversationHandler, JobQueue
)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Состояния
REGISTRATION, POLL, MAIN_COFFEE, RARE_COFFEE = range(4)

# Переменные окружения
BOT_TOKEN = os.environ.get('BOT_TOKEN')

if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не установлен!")
    sys.exit(1)

# База данных SQLite
DB_FILE = 'coffee_bot.db'

def init_database():
    """Инициализация базы данных SQLite"""
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        cursor = conn.cursor()
        
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
        
        conn.commit()
        conn.close()
        logger.info("✅ База данных SQLite инициализирована")
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
        sys.exit(1)

def execute_query(query, params=(), fetchone=False, fetchall=False, commit=False):
    """Универсальная функция выполнения запросов"""
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
        logger.error(f"❌ Ошибка выполнения запроса: {e}")
        return None

# Функции БД
def get_user_data(user_id):
    return execute_query(
        'SELECT * FROM users WHERE user_id = ?',
        (user_id,),
        fetchone=True
    )

def update_user(user_id, **kwargs):
    for key, value in kwargs.items():
        execute_query(
            f'UPDATE users SET {key} = ? WHERE user_id = ?',
            (value, user_id),
            commit=True
        )

def delete_user(user_id):
    execute_query(
        'DELETE FROM users WHERE user_id = ?',
        (user_id,),
        commit=True
    )

def create_user(user_id):
    if not get_user_data(user_id):
        execute_query(
            '''INSERT INTO users (user_id, count_1, count_2, wait_1, wait_2)
               VALUES (?, 0, 0, 0, 0)''',
            (user_id,),
            commit=True
        )

def get_all_users():
    return execute_query('SELECT * FROM users', fetchall=True)

# СКРИПТЫ
def script_1():
    """Прирост кофе"""
    execute_query(
        '''UPDATE users 
           SET count_1 = count_1 + 1 
           WHERE chastota = 'Каждый день' AND wait_1 = 0''',
        commit=True
    )
    logger.info("✅ Скрипт_1: прирост кофе")

def script_2():
    """Поиск дежурного"""
    result = execute_query(
        '''SELECT MAX(count_1) FROM users 
           WHERE wait_1 = 0 AND wait_2 = 0''',
        fetchone=True
    )
    max_count = result[0] if result else None
    
    if max_count:
        candidates = execute_query(
            '''SELECT user_id FROM users 
               WHERE count_1 = ? AND wait_1 = 0 AND wait_2 = 0''',
            (max_count,),
            fetchall=True
        )
        
        if candidates:
            chosen_user = random.choice(candidates)[0]
            execute_query(
                'UPDATE users SET count_2 = 1 WHERE user_id = ?',
                (chosen_user,),
                commit=True
            )
            logger.info(f"✅ Скрипт_2: выбран дежурный {chosen_user}")

def script_3():
    """Обнуление Печальки"""
    execute_query(
        'UPDATE users SET wait_2 = 0 WHERE wait_2 = 1',
        commit=True
    )
    logger.info("✅ Скрипт_3: обнуление Печальки")

def script_4():
    """Погашение дежурства"""
    execute_query(
        'UPDATE users SET count_2 = 0, count_1 = 0 WHERE count_2 = 1',
        commit=True
    )
    logger.info("✅ Скрипт_4: погашение дежурства")

def script_5():
    """Уход домой неполнозанятых"""
    execute_query(
        '''UPDATE users SET wait_1 = 1 
           WHERE chastota = 'Я тут не каждый день' AND wait_1 = 0''',
        commit=True
    )
    logger.info("✅ Скрипт_5: уход домой неполнозанятых")

def script_6(bot):
    """Информирование"""
    duty = execute_query(
        'SELECT name FROM users WHERE count_2 = 1',
        fetchone=True
    )
    
    if duty:
        duty_name = duty[0] or "Неизвестный"
        active_users = execute_query(
            'SELECT user_id FROM users WHERE wait_1 = 0 AND wait_2 = 0',
            fetchall=True
        )
        
        for user in active_users or []:
            try:
                bot.send_message(
                    chat_id=user[0],
                    text=f"☕ Сегодня дежурный: {duty_name}"
                )
            except Exception as e:
                logger.error(f"❌ Не удалось отправить сообщение: {e}")

# Функции для работы с JobQueue (расписанием)
def daily_14_job(context):
    """Выполняется в 14:00 по UTC"""
    script_1()
    script_2()
    script_6(context.bot)
    logger.info("✅ Выполнены скрипты 14:00")

def daily_21_job(context):
    """Выполняется в 21:00 по UTC"""
    script_3()
    script_4()
    script_5()
    logger.info("✅ Выполнены скрипты 21:00")

# Ручной запуск скриптов для отладки
def run_scripts(bot, script_name):
    """Ручной запуск скриптов для отладки"""
    if script_name == 'script_1':
        script_1()
    elif script_name == 'script_2':
        script_2()
    elif script_name == 'script_3':
        script_3()
    elif script_name == 'script_4':
        script_4()
    elif script_name == 'script_5':
        script_5()
    elif script_name == 'script_6':
        script_6(bot)

# ОБРАБОТЧИКИ КОМАНД
def start(update: Update, context):
    create_user(update.effective_user.id)
    update.message.reply_text(
        "👋 Введите ваше имя, оно будет видно всем пользователям:"
    )
    return REGISTRATION

def registration(update: Update, context):
    update_user(update.effective_user.id, name=update.message.text)
    
    keyboard = [
        [InlineKeyboardButton("Каждый день", callback_data='daily')],
        [InlineKeyboardButton("Я тут не каждый день", callback_data='rarely')],
        [InlineKeyboardButton("Я теперь НЕ пью кофе", callback_data='no_coffee')]
    ]
    
    update.message.reply_text(
        "☕ Как часто вы пьете кофе?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return POLL

def poll_handler(update: Update, context):
    query = update.callback_query
    query.answer()
    user_id = update.effective_user.id
    data = query.data
    
    if data == 'no_coffee':
        delete_user(user_id)
        query.edit_message_text("🗑️ Данные удалены. Нажмите /start")
        return ConversationHandler.END
    
    if data == 'daily':
        update_user(user_id, chastota="Каждый день")
        keyboard = [
            [InlineKeyboardButton("Я некоторое время не пью кофе", callback_data='temp_no_coffee')],
            [InlineKeyboardButton("Я дежурный, но не смогу вымыть кофемашинку", callback_data='cant_duty')],
            [InlineKeyboardButton("Я Вернулся", callback_data='returned')],
            [InlineKeyboardButton("Я теперь пью кофе по другому", callback_data='change_habit')]
        ]
        query.edit_message_text(
            "✅ Теперь вам будут приходить уведомления о дежурных",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return MAIN_COFFEE
    
    elif data == 'rarely':
        update_user(user_id, chastota="Я тут не каждый день")
        keyboard = [
            [InlineKeyboardButton("Я сегодня пью кофе", callback_data='today_coffee')],
            [InlineKeyboardButton("Я дежурный, но не смогу вымыть кофемашинку", callback_data='cant_duty_rare')],
            [InlineKeyboardButton("Я теперь пью кофе по другому", callback_data='change_habit_rare')]
        ]
        query.edit_message_text(
            "⏰ Когда вы придете, отметьтесь:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return RARE_COFFEE

def main_coffee_handler(update: Update, context):
    query = update.callback_query
    query.answer()
    user_id = update.effective_user.id
    
    if query.data == 'temp_no_coffee':
        update_user(user_id, wait_1=1)
        context.bot.send_message(user_id, "⏸️ Когда вернетесь, отметьтесь")
        query.edit_message_text("✅ Отметили временное отсутствие")
        
    elif query.data == 'cant_duty':
        update_user(user_id, wait_2=1, count_2=0)
        context.bot.send_message(user_id, "😔 Печалька")
        script_2()
        script_6(context.bot)
        query.edit_message_text("✅ Отказ от дежурства учтен")
        
    elif query.data == 'returned':
        update_user(user_id, wait_1=0)
        context.bot.send_message(user_id, "🎉 Ура! С возвращением!")
        query.edit_message_text("✅ Вы вернулись!")
        
    elif query.data == 'change_habit':
        return poll_handler(update, context)
    
    return MAIN_COFFEE

def rare_coffee_handler(update: Update, context):
    query = update.callback_query
    query.answer()
    user_id = update.effective_user.id
    
    if query.data == 'today_coffee':
        user = get_user_data(user_id)
        if user:
            current_count = user[3] or 0
            update_user(user_id, count_1=current_count + 1, wait_1=0)
        context.bot.send_message(user_id, "✅ Спасибо!")
        query.edit_message_text("✅ Присутствие отмечено")
        
    elif query.data == 'cant_duty_rare':
        update_user(user_id, wait_2=1, count_2=0)
        context.bot.send_message(user_id, "😔 Печалька")
        script_2()
        script_6(context.bot)
        query.edit_message_text("✅ Отказ от дежурства учтен")
        
    elif query.data == 'change_habit_rare':
        return poll_handler(update, context)
    
    return RARE_COFFEE

def cancel(update: Update, context):
    update.message.reply_text("❌ Действие отменено. /start")
    return ConversationHandler.END

# Команда для отладки
def debug(update: Update, context):
    """Команда для отладки скриптов"""
    user_id = update.effective_user.id
    if user_id == 123456789:  # Замените на ваш ID в Telegram
        if context.args:
            script_name = context.args[0]
            run_scripts(context.bot, script_name)
            update.message.reply_text(f"✅ Скрипт {script_name} выполнен")
        else:
            update.message.reply_text("Использование: /debug <script_name>")
    else:
        update.message.reply_text("❌ Нет доступа")

def status(update: Update, context):
    """Показать статус"""
    user = get_user_data(update.effective_user.id)
    if user:
        user_id, name, chastota, count_1, count_2, wait_1, wait_2, created_at = user
        status_text = f"""
📊 Ваш статус:
👤 Имя: {name or 'Не указано'}
📅 Режим: {chastota or 'Не указан'}
☕ Чашек: {count_1}
🎖️ Дежурств: {count_2}
🚫 Отсутствие: {'Да' if wait_1 else 'Нет'}
😔 Печалька: {'Да' if wait_2 else 'Нет'}
        """
        update.message.reply_text(status_text)
    else:
        update.message.reply_text("❌ Вы не зарегистрированы. Нажмите /start")

def main():
    """Основная функция"""
    # Инициализация базы данных
    init_database()
    
    # Создание Updater и передача токена
    updater = Updater(token=BOT_TOKEN, use_context=True)
    
    # Получаем диспетчер для регистрации обработчиков
    dp = updater.dispatcher
    
    # Получаем JobQueue для планирования задач
    job_queue = updater.job_queue
    
    # Настройка ConversationHandler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            REGISTRATION: [MessageHandler(Filters.text & ~Filters.command, registration)],
            POLL: [CallbackQueryHandler(poll_handler)],
            MAIN_COFFEE: [CallbackQueryHandler(main_coffee_handler)],
            RARE_COFFEE: [CallbackQueryHandler(rare_coffee_handler)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    
    dp.add_handler(conv_handler)
    
    # Добавляем дополнительные команды
    dp.add_handler(CommandHandler("debug", debug))
    dp.add_handler(CommandHandler("status", status))
    
    # Настройка планировщика (только по будням)
    if job_queue:
        # Понедельник-пятница в 14:00 UTC
        job_queue.run_daily(
            daily_14_job,
            time=time(hour=14, minute=0),
            days=(0, 1, 2, 3, 4)  # Пн-Пт
        )
        
        # Понедельник-пятница в 21:00 UTC
        job_queue.run_daily(
            daily_21_job,
            time=time(hour=21, minute=0),
            days=(0, 1, 2, 3, 4)  # Пн-Пт
        )
    
    # Запуск бота
    logger.info("✅ Бот запускается...")
    updater.start_polling()
    
    # Запуск бесконечного цикла
    updater.idle()

if __name__ == '__main__':
    main()
