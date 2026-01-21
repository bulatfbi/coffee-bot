import os
import logging
import sys
import random
from datetime import datetime, time
import asyncio

# Используем psycopg3 вместо psycopg2
import psycopg
from psycopg_pool import ConnectionPool
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler,
    JobQueue
)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log')
    ]
)
logger = logging.getLogger(__name__)

# Состояния
REGISTRATION, POLL, MAIN_COFFEE, RARE_COFFEE = range(4)

# Получение переменных окружения
BOT_TOKEN = os.environ.get('BOT_TOKEN')
DATABASE_URL = os.environ.get('DATABASE_URL')

if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не установлен!")
    sys.exit(1)

# Пул соединений для psycopg3
db_pool = None

def init_database():
    """Инициализация базы данных"""
    global db_pool
    
    try:
        # Создаем пул соединений для psycopg3
        db_pool = ConnectionPool(
            DATABASE_URL,
            min_size=1,
            max_size=20,
            kwargs={"sslmode": "require"}  # SSL для Render
        )
        
        # Проверяем соединение и создаем таблицу
        with db_pool.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        user_id BIGINT PRIMARY KEY,
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
        
        logger.info("✅ База данных PostgreSQL (psycopg3) инициализирована")
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
        sys.exit(1)

def execute_query(query, params=(), fetchone=False, fetchall=False, commit=False):
    """Универсальная функция выполнения запросов для psycopg3"""
    try:
        with db_pool.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, params)
                
                if commit:
                    conn.commit()
                
                if fetchone:
                    result = cursor.fetchone()
                elif fetchall:
                    result = cursor.fetchall()
                else:
                    result = None
                    
                return result
                
    except Exception as e:
        logger.error(f"❌ Ошибка выполнения запроса: {e}")
        return None

# Функции БД
def get_user_data(user_id: int):
    return execute_query(
        'SELECT * FROM users WHERE user_id = %s',
        (user_id,),
        fetchone=True
    )

def update_user(user_id: int, **kwargs):
    for key, value in kwargs.items():
        execute_query(
            f'UPDATE users SET {key} = %s WHERE user_id = %s',
            (value, user_id),
            commit=True
        )

def delete_user(user_id: int):
    execute_query(
        'DELETE FROM users WHERE user_id = %s',
        (user_id,),
        commit=True
    )

def create_user(user_id: int):
    if not get_user_data(user_id):
        execute_query(
            '''INSERT INTO users (user_id, count_1, count_2, wait_1, wait_2)
               VALUES (%s, 0, 0, 0, 0)''',
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
               WHERE count_1 = %s AND wait_1 = 0 AND wait_2 = 0''',
            (max_count,),
            fetchall=True
        )
        
        if candidates:
            chosen_user = random.choice(candidates)[0]
            execute_query(
                'UPDATE users SET count_2 = 1 WHERE user_id = %s',
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

async def script_6(context: ContextTypes.DEFAULT_TYPE):
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
                await context.bot.send_message(
                    chat_id=user[0],
                    text=f"☕ Сегодня дежурный: {duty_name}"
                )
            except Exception as e:
                logger.error(f"❌ Не удалось отправить сообщение: {e}")

# ОБРАБОТЧИКИ КОМАНД
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    create_user(update.effective_user.id)
    await update.message.reply_text(
        "👋 Введите ваше имя, оно будет видно всем пользователям:"
    )
    return REGISTRATION

async def registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_user(update.effective_user.id, name=update.message.text)
    
    keyboard = [
        [InlineKeyboardButton("Каждый день", callback_data='daily')],
        [InlineKeyboardButton("Я тут не каждый день", callback_data='rarely')],
        [InlineKeyboardButton("Я теперь НЕ пью кофе", callback_data='no_coffee')]
    ]
    
    await update.message.reply_text(
        "☕ Как часто вы пьете кофе?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return POLL

async def poll_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data
    
    if data == 'no_coffee':
        delete_user(user_id)
        await query.edit_message_text("🗑️ Данные удалены. Нажмите /start")
        return ConversationHandler.END
    
    if data == 'daily':
        update_user(user_id, chastota="Каждый день")
        keyboard = [
            [InlineKeyboardButton("Я некоторое время не пью кофе", callback_data='temp_no_coffee')],
            [InlineKeyboardButton("Я дежурный, но не смогу вымыть кофемашинку", callback_data='cant_duty')],
            [InlineKeyboardButton("Я Вернулся", callback_data='returned')],
            [InlineKeyboardButton("Я теперь пью кофе по другому", callback_data='change_habit')]
        ]
        await query.edit_message_text(
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
        await query.edit_message_text(
            "⏰ Когда вы придете, отметьтесь:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return RARE_COFFEE

async def main_coffee_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    if query.data == 'temp_no_coffee':
        update_user(user_id, wait_1=1)
        await context.bot.send_message(user_id, "⏸️ Когда вернетесь, отметьтесь")
        await query.edit_message_text("✅ Отметили временное отсутствие")
        
    elif query.data == 'cant_duty':
        update_user(user_id, wait_2=1, count_2=0)
        await context.bot.send_message(user_id, "😔 Печалька")
        script_2()
        await script_6(context)
        await query.edit_message_text("✅ Отказ от дежурства учтен")
        
    elif query.data == 'returned':
        update_user(user_id, wait_1=0)
        await context.bot.send_message(user_id, "🎉 Ура! С возвращением!")
        await query.edit_message_text("✅ Вы вернулись!")
        
    elif query.data == 'change_habit':
        return await poll_handler(update, context)
    
    return MAIN_COFFEE

async def rare_coffee_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    if query.data == 'today_coffee':
        user = get_user_data(user_id)
        if user:
            current_count = user[3] or 0
            update_user(user_id, count_1=current_count + 1, wait_1=0)
        await context.bot.send_message(user_id, "✅ Спасибо!")
        await query.edit_message_text("✅ Присутствие отмечено")
        
    elif query.data == 'cant_duty_rare':
        update_user(user_id, wait_2=1, count_2=0)
        await context.bot.send_message(user_id, "😔 Печалька")
        script_2()
        await script_6(context)
        await query.edit_message_text("✅ Отказ от дежурства учтен")
        
    elif query.data == 'change_habit_rare':
        return await poll_handler(update, context)
    
    return RARE_COFFEE

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Действие отменено. /start")
    return ConversationHandler.END

# ФУНКЦИИ ПЛАНИРОВЩИКА
async def daily_14_job(context: ContextTypes.DEFAULT_TYPE):
    """Выполняется в 14:00 по UTC"""
    script_1()
    script_2()
    await script_6(context)
    logger.info("✅ Выполнены скрипты 14:00")

async def daily_21_job(context: ContextTypes.DEFAULT_TYPE):
    """Выполняется в 21:00 по UTC"""
    script_3()
    script_4()
    script_5()
    logger.info("✅ Выполнены скрипты 21:00")

def main():
    """Основная функция"""
    # Инициализация базы данных
    init_database()
    
    # Создание приложения
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Настройка ConversationHandler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            REGISTRATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration)],
            POLL: [CallbackQueryHandler(poll_handler)],
            MAIN_COFFEE: [CallbackQueryHandler(main_coffee_handler)],
            RARE_COFFEE: [CallbackQueryHandler(rare_coffee_handler)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    
    application.add_handler(conv_handler)
    
    # Настройка планировщика
    job_queue = application.job_queue
    
    if job_queue:
        # 14:00 UTC (понедельник-пятница)
        job_queue.run_daily(
            daily_14_job, 
            time=time(hour=14, minute=0), 
            days=(0, 1, 2, 3, 4)  # Пн-Пт
        )
        # 21:00 UTC (понедельник-пятница)
        job_queue.run_daily(
            daily_21_job, 
            time=time(hour=21, minute=0), 
            days=(0, 1, 2, 3, 4)  # Пн-Пт
        )
    
    # Запуск бота
    logger.info("✅ Бот запускается...")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
