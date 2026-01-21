#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
☕ Coffee Duty Bot для Telegram
Работает на Render.com с Python 3.13 и SQLite
Полностью соответствует ТЗ
"""

import os
import sys
import logging
import random
import asyncio
import sqlite3
import threading
from datetime import datetime, time
from typing import Dict, List, Optional, Tuple, Any

# =========== ПАТЧ ДЛЯ ПРОБЛЕМ С IMGHDR В PYTHON 3.13 ===========
try:
    import imghdr
except ImportError:
    import io
    
    class ImghdrCompat:
        @staticmethod
        def what(file, h=None):
            """Простая замена imghdr.what() для Python 3.13"""
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
    import imghdr
# ===========================================

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    JobQueue,
)

# =========== НАСТРОЙКА ЛОГИРОВАНИЯ ===========
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# =========== КОНСТАНТЫ ===========
REGISTRATION, POLL, MAIN_COFFEE, RARE_COFFEE = range(4)

# Переменные окружения
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не установлен! Добавьте его в Environment Variables на Render.")
    sys.exit(1)

# Глобальные флаги
SCRIPTS_ENABLED = True

# База данных SQLite
DB_FILE = 'coffee_bot.db'

# =========== БАЗА ДАННЫХ ===========
def init_database():
    """Инициализация базы данных SQLite"""
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        cursor = conn.cursor()
        
        # Таблица пользователей (точно по ТЗ)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                name TEXT,
                chastota TEXT,
                count_1 INTEGER DEFAULT 0,
                count_2 INTEGER DEFAULT 0,
                wait_1 INTEGER DEFAULT 0,
                wait_2 INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица настроек для хранения состояния скриптов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
        # Инициализация настроек
        cursor.execute('''
            INSERT OR IGNORE INTO settings (key, value) 
            VALUES ('scripts_enabled', '1')
        ''')
        
        conn.commit()
        conn.close()
        logger.info("✅ База данных инициализирована")
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
        sys.exit(1)

def execute_query(query: str, params: Tuple = (), 
                  fetchone: bool = False, fetchall: bool = False, 
                  commit: bool = False):
    """Универсальная функция выполнения SQL-запросов"""
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
        logger.error(f"❌ Ошибка SQL-запроса: {e}")
        return None

# =========== ФУНКЦИИ ДЛЯ РАБОТЫ С БД (по ТЗ) ===========
def get_user_data(user_id: int):
    """Получить данные пользователя по user_id"""
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

def update_user(user_id: int, **kwargs):
    """Обновить данные пользователя"""
    for key, value in kwargs.items():
        execute_query(
            f'UPDATE users SET {key} = ?, last_updated = CURRENT_TIMESTAMP WHERE user_id = ?',
            (value, user_id),
            commit=True
        )

def delete_user(user_id: int):
    """Удалить пользователя из базы"""
    execute_query(
        'DELETE FROM users WHERE user_id = ?',
        (user_id,),
        commit=True
    )

def create_user(user_id: int):
    """Создать новую запись пользователя"""
    if not get_user_data(user_id):
        execute_query(
            '''INSERT INTO users (user_id, count_1, count_2, wait_1, wait_2) 
               VALUES (?, 0, 0, 0, 0)''',
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
    """Получить активных пользователей (wait_1 = 0 AND wait_2 = 0)"""
    results = execute_query(
        'SELECT user_id FROM users WHERE wait_1 = 0 AND wait_2 = 0',
        fetchall=True
    )
    return [row[0] for row in results] if results else []

def get_duty_user():
    """Получить текущего дежурного (count_2 = 1)"""
    result = execute_query(
        'SELECT user_id, name FROM users WHERE count_2 = 1',
        fetchone=True
    )
    return result

def get_scripts_enabled():
    """Получить статус включения скриптов"""
    result = execute_query(
        'SELECT value FROM settings WHERE key = ?',
        ('scripts_enabled',),
        fetchone=True
    )
    return result and result[0] == '1'

def set_scripts_enabled(enabled: bool):
    """Установить статус включения скриптов"""
    value = '1' if enabled else '0'
    execute_query(
        'UPDATE settings SET value = ? WHERE key = ?',
        (value, 'scripts_enabled'),
        commit=True
    )
    global SCRIPTS_ENABLED
    SCRIPTS_ENABLED = enabled
    logger.info(f"✅ Скрипты {'включены' if enabled else 'отключены'}")

# =========== СКРИПТЫ (ТОЧНО ПО ТЗ) ===========
def script_1():
    """Скрипт_1 (прирост кофе)"""
    execute_query(
        '''UPDATE users 
           SET count_1 = count_1 + 1 
           WHERE chastota = 'Каждый день' AND wait_1 = 0''',
        commit=True
    )
    logger.info("✅ Скрипт_1: Прирост кофе выполнен")

def script_2():
    """Скрипт_2 (поиск дежурного)"""
    # Найти максимальное значение count_1 среди активных пользователей
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
    """Скрипт_3 (обнуление Печальки)"""
    execute_query(
        'UPDATE users SET wait_2 = 0 WHERE wait_2 = 1',
        commit=True
    )
    logger.info("✅ Скрипт_3: Обнуление Печальки")

def script_4():
    """Скрипт_4 (погашение дежурства)"""
    execute_query(
        'UPDATE users SET count_2 = 0, count_1 = 0 WHERE count_2 = 1',
        commit=True
    )
    logger.info("✅ Скрипт_4: Погашение дежурства")

def script_5():
    """Скрипт_5 (уход домой неполнозанятых)"""
    execute_query(
        '''UPDATE users SET wait_1 = 1 
           WHERE chastota = 'Я тут не каждый день' AND wait_1 = 0''',
        commit=True
    )
    logger.info("✅ Скрипт_5: Уход домой неполнозанятых")

async def script_6(context: ContextTypes.DEFAULT_TYPE):
    """Скрипт_6 (информирование)"""
    duty = get_duty_user()
    
    if duty:
        duty_user_id, duty_name = duty
        active_users = get_active_users()
        
        for user_id in active_users:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"☕ Сегодня дежурный: {duty_name if duty_name else f'Пользователь {duty_user_id}'}"
                )
            except Exception as e:
                logger.error(f"❌ Не удалось отправить сообщение {user_id}: {e}")
        
        logger.info(f"✅ Скрипт_6: Уведомления отправлены {len(active_users)} пользователям")

# =========== ОБРАБОТЧИКИ КОМАНД И ДИАЛОГОВ ===========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Экран 'Стартовый': создает запись в БД"""
    user_id = update.effective_user.id
    
    # СОЗДАЕТ НОВУЮ СТРОКУ В ТАБЛИЦЕ И ПРИСВАИВАЕТ ЗНАЧЕНИЕ user_id
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
    name = update.message.text.strip()
    
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
    """Экран 'Опрос': выбор частоты употребления кофе"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    data = query.data
    
    if data == 'no_coffee':
        # УДАЛЯЕТ ВСЕ ДАННЫЕ ИЗ ТАБЛИЦЫ
        delete_user(user_id)
        await query.edit_message_text(
            "🗑️ Ваши данные удалены.\n\n"
            "Чтобы начать заново, нажмите /start"
        )
        return ConversationHandler.END
    
    if data == 'daily':
        # Сохраняем "Каждый день"
        update_user(user_id, chastota='Каждый день')
        
        # Переход на экран "Главные кофеманы"
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
        # Сохраняем "Я тут не каждый день"
        update_user(user_id, chastota='Я тут не каждый день')
        
        # Переход на экран "Редкие кофеманы"
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
        # Присваивает 1 в wait_1
        update_user(user_id, wait_1=1)
        await context.bot.send_message(
            chat_id=user_id,
            text="⏸️ Когда вы вернетесь отметьте это"
        )
        await query.edit_message_text("✅ Вы отметили временное отсутствие")
        
    elif data == 'cant_duty':
        # Присваивает 1 в wait_2 и 0 в count_2
        update_user(user_id, wait_2=1, count_2=0)
        await context.bot.send_message(
            chat_id=user_id,
            text="😔 Печалька"
        )
        # Запускаем Скрипт_2 и скрипт_6
        script_2()
        await script_6(context)
        await query.edit_message_text("✅ Отказ от дежурства учтен")
        
    elif data == 'returned':
        # Присваивает 0 в wait_1
        update_user(user_id, wait_1=0)
        await context.bot.send_message(
            chat_id=user_id,
            text="🎉 Ура!"
        )
        await query.edit_message_text("✅ Вы вернулись!")
        
    elif data == 'change_habit':
        # Переход на экран "Опрос"
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
        # Добавляет 1 в count_1, присваивает 0 в wait_1
        user = get_user_data(user_id)
        current_count = user['count_1'] if user else 0
        update_user(user_id, count_1=current_count + 1, wait_1=0)
        
        await context.bot.send_message(
            chat_id=user_id,
            text="✅ Спасибо"
        )
        await query.edit_message_text("✅ Ваше присутствие отмечено")
        
    elif data == 'cant_duty_rare':
        # Присваивает 1 в wait_2 и 0 в count_2
        update_user(user_id, wait_2=1, count_2=0)
        await context.bot.send_message(
            chat_id=user_id,
            text="😔 Печалька"
        )
        # Запускаем Скрипт_2 и скрипт_6
        script_2()
        await script_6(context)
        await query.edit_message_text("✅ Отказ от дежурства учтен")
        
    elif data == 'change_habit_rare':
        # Переход на экран "Опрос"
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
async def hollidaon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скрытая команда: отключить работу скриптов по времени"""
    set_scripts_enabled(False)
    await update.message.reply_text("✅ Работа скриптов по времени ОТКЛЮЧЕНА")

async def hollidayoff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скрытая команда: включить работу скриптов по времени"""
    set_scripts_enabled(True)
    await update.message.reply_text("✅ Работа скриптов по времени ВКЛЮЧЕНА")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать статус пользователя"""
    user = get_user_data(update.effective_user.id)
    
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
👑 Сегодняшний дежурный: {duty_text}
⚙️ Автоскрипты: {'ВКЛЮЧЕНЫ' if SCRIPTS_ENABLED else 'ОТКЛЮЧЕНЫ'}
        """
    else:
        status_msg = "❌ Вы не зарегистрированы. Используйте /start"
    
    await update.message.reply_text(status_msg)

async def run_script(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запустить скрипт вручную (для тестирования)"""
    if context.args:
        script_num = context.args[0]
        if script_num == '1':
            script_1()
            await update.message.reply_text("✅ Скрипт_1 (прирост кофе) выполнен")
        elif script_num == '2':
            script_2()
            await update.message.reply_text("✅ Скрипт_2 (поиск дежурного) выполнен")
        elif script_num == '3':
            script_3()
            await update.message.reply_text("✅ Скрипт_3 (обнуление Печальки) выполнен")
        elif script_num == '4':
            script_4()
            await update.message.reply_text("✅ Скрипт_4 (погашение дежурства) выполнен")
        elif script_num == '5':
            script_5()
            await update.message.reply_text("✅ Скрипт_5 (уход домой) выполнен")
        elif script_num == '6':
            await script_6(context)
            await update.message.reply_text("✅ Скрипт_6 (информирование) выполнен")
        elif script_num == 'all':
            script_1()
            script_2()
            script_3()
            script_4()
            script_5()
            await script_6(context)
            await update.message.reply_text("✅ Все скрипты выполнены")
        else:
            await update.message.reply_text("❌ Неизвестный скрипт. Используйте: /run_script <1-6|all>")
    else:
        await update.message.reply_text("Использование: /run_script <номер_скрипта>\n1-прирост кофе, 2-поиск дежурного, 3-обнуление печальки, 4-погашение дежурства, 5-уход домой, 6-информирование, all-все")

# =========== ФУНКЦИИ ПЛАНИРОВЩИКА ===========
async def daily_13_job(context: ContextTypes.DEFAULT_TYPE):
    """Выполняется в 13:00 с понедельника по пятницу"""
    if not SCRIPTS_ENABLED:
        logger.info("⏸️ Скрипты отключены, пропускаем выполнение в 13:00")
        return
    
    logger.info("⏰ Запуск скриптов 13:00 (UTC)")
    script_1()  # Скрипт_1 (прирост кофе)
    script_2()  # Скрипт_2 (поиск дежурного)
    await script_6(context)  # Скрипт_6 (информирование)

async def daily_21_job(context: ContextTypes.DEFAULT_TYPE):
    """Выполняется в 21:00 с понедельника по пятницу"""
    if not SCRIPTS_ENABLED:
        logger.info("⏸️ Скрипты отключены, пропускаем выполнение в 21:00")
        return
    
    logger.info("⏰ Запуск скриптов 21:00 (UTC)")
    script_3()  # Скрипт_3 (обнуление Печальки)
    script_4()  # Скрипт_4 (погашение дежурства)
    script_5()  # Скрипт_5 (уход домой неполнозанятых)

# =========== ОСНОВНАЯ ФУНКЦИЯ ===========
def main():
    """Основная функция запуска бота"""
    # Инициализация базы данных
    init_database()
    
    # Загрузка статуса скриптов из БД
    global SCRIPTS_ENABLED
    SCRIPTS_ENABLED = get_scripts_enabled()
    logger.info(f"✅ Статус скриптов: {'ВКЛЮЧЕНЫ' if SCRIPTS_ENABLED else 'ОТКЛЮЧЕНЫ'}")
    
    # Создание приложения (версия 20.7)
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
    
    # Добавление обработчиков команд
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('status', status))
    application.add_handler(CommandHandler('hollidaon', hollidaon))
    application.add_handler(CommandHandler('hollidayoff', hollidayoff))
    application.add_handler(CommandHandler('run_script', run_script))
    
    # Настройка планировщика задач
    job_queue = application.job_queue
    
    if job_queue:
        # Понедельник-пятница в 13:00 UTC
        job_queue.run_daily(
            daily_13_job,
            time=time(hour=13, minute=0, second=0),
            days=(0, 1, 2, 3, 4),  # Пн=0, Пт=4
            name="daily_13_job"
        )
        
        # Понедельник-пятница в 21:00 UTC
        job_queue.run_daily(
            daily_21_job,
            time=time(hour=21, minute=0, second=0),
            days=(0, 1, 2, 3, 4),
            name="daily_21_job"
        )
        
        logger.info("✅ Планировщик скриптов настроен")
        logger.info("⏰ Расписание (UTC): Пн-Пт 13:00 (скрипты 1,2,6) и 21:00 (скрипты 3,4,5)")
    
    # Запуск бота
    logger.info("✅ Бот запущен и готов к работе!")
    logger.info("📋 Доступные команды:")
    logger.info("  /start - начать работу")
    logger.info("  /status - показать статус")
    logger.info("  /hollidaon - отключить автоскрипты")
    logger.info("  /hollidayoff - включить автоскрипты")
    logger.info("  /run_script <номер> - запустить скрипт вручную")
    
    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )

if __name__ == '__main__':
    main()
