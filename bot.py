#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
☕ Coffee Duty Bot для Telegram
Работает на Render.com с Python 3.13 и SQLite
"""

import os
import sys
import logging
import random
import asyncio
import sqlite3
from datetime import datetime, time
from typing import Dict, List, Optional, Tuple

# =========== ПАТЧ ДЛЯ Python 3.13 ===========
# Решение проблемы с отсутствующим imghdr
try:
    import imghdr
except ImportError:
    # Создаем простую реализацию imghdr
    import io
    
    class ImghdrCompat:
        @staticmethod
        def what(file, h=None):
            """Простая замена imghdr.what() для Python 3.13"""
            if hasattr(file, 'read'):
                # Файловый объект
                data = file.read(32)
                file.seek(0)
            else:
                # Путь к файлу
                with open(file, 'rb') as f:
                    data = f.read(32)
            
            # Проверка форматов изображений
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
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('coffee_bot.log')
    ]
)
logger = logging.getLogger(__name__)

# =========== КОНСТАНТЫ И ПЕРЕМЕННЫЕ ===========
REGISTRATION, POLL, MAIN_COFFEE, RARE_COFFEE = range(4)
BOT_TOKEN = os.environ.get('BOT_TOKEN')
DB_FILE = 'coffee_bot.db'

if not BOT_TOKEN:
    logger.error("❌ ОШИБКА: BOT_TOKEN не установлен в переменных окружения!")
    logger.error("✅ Исправление: добавьте BOT_TOKEN в настройках Render")
    sys.exit(1)

# =========== БАЗА ДАННЫХ SQLite ===========
def init_database() -> None:
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Создаем индекс для быстрого поиска
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_active_users 
            ON users(wait_1, wait_2, chastota)
        ''')
        
        conn.commit()
        conn.close()
        logger.info("✅ База данных SQLite успешно инициализирована")
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
        sys.exit(1)

def execute_query(
    query: str, 
    params: Tuple = (), 
    fetchone: bool = False, 
    fetchall: bool = False, 
    commit: bool = False
):
    """Универсальная функция выполнения SQL-запросов"""
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.row_factory = sqlite3.Row  # Возвращать строки как словари
        cursor = conn.cursor()
        
        cursor.execute(query, params)
        
        if commit:
            conn.commit()
        
        if fetchone:
            result = cursor.fetchone()
            if result:
                result = dict(result)
        elif fetchall:
            result = [dict(row) for row in cursor.fetchall()]
        else:
            result = None
        
        conn.close()
        return result
        
    except Exception as e:
        logger.error(f"❌ Ошибка SQL-запроса '{query[:50]}...': {e}")
        return None

# =========== ФУНКЦИИ БАЗЫ ДАННЫХ ===========
def get_user_data(user_id: int) -> Optional[Dict]:
    return execute_query(
        'SELECT * FROM users WHERE user_id = ?',
        (user_id,),
        fetchone=True
    )

def update_user(user_id: int, **kwargs) -> None:
    for key, value in kwargs.items():
        execute_query(
            f'UPDATE users SET {key} = ?, last_updated = CURRENT_TIMESTAMP WHERE user_id = ?',
            (value, user_id),
            commit=True
        )

def delete_user(user_id: int) -> None:
    execute_query(
        'DELETE FROM users WHERE user_id = ?',
        (user_id,),
        commit=True
    )

def create_user(user_id: int) -> None:
    if not get_user_data(user_id):
        execute_query(
            '''INSERT INTO users (user_id, count_1, count_2, wait_1, wait_2) 
               VALUES (?, 0, 0, 0, 0)''',
            (user_id,),
            commit=True
        )

def get_all_users() -> List[Dict]:
    return execute_query('SELECT * FROM users', fetchall=True)

def get_active_users() -> List[Dict]:
    return execute_query(
        'SELECT * FROM users WHERE wait_1 = 0 AND wait_2 = 0',
        fetchall=True
    )

# =========== СКРИПТЫ ===========
def script_1() -> None:
    """Скрипт 1: Прирост кофе (ежедневно для постоянных пользователей)"""
    affected = execute_query(
        '''UPDATE users 
           SET count_1 = count_1 + 1 
           WHERE chastota = 'Каждый день' AND wait_1 = 0''',
        commit=True
    )
    logger.info(f"✅ Скрипт_1: Прирост кофе выполнен (затронуто строк: {affected})")

def script_2() -> None:
    """Скрипт 2: Поиск дежурного"""
    # Находим пользователя с максимальным count_1 среди активных
    result = execute_query(
        '''SELECT MAX(count_1) as max_count FROM users 
           WHERE wait_1 = 0 AND wait_2 = 0''',
        fetchone=True
    )
    
    if result and result.get('max_count') is not None:
        max_count = result['max_count']
        candidates = execute_query(
            '''SELECT user_id FROM users 
               WHERE count_1 = ? AND wait_1 = 0 AND wait_2 = 0''',
            (max_count,),
            fetchall=True
        )
        
        if candidates:
            # Выбираем случайного из кандидатов
            chosen = random.choice(candidates)
            chosen_user = chosen['user_id']
            
            execute_query(
                'UPDATE users SET count_2 = 1 WHERE user_id = ?',
                (chosen_user,),
                commit=True
            )
            logger.info(f"✅ Скрипт_2: Выбран дежурный user_id={chosen_user}")

def script_3() -> None:
    """Скрипт 3: Обнуление Печальки"""
    affected = execute_query(
        'UPDATE users SET wait_2 = 0 WHERE wait_2 = 1',
        commit=True
    )
    logger.info(f"✅ Скрипт_3: Обнуление Печальки (затронуто: {affected})")

def script_4() -> None:
    """Скрипт 4: Погашение дежурства"""
    affected = execute_query(
        'UPDATE users SET count_2 = 0, count_1 = 0 WHERE count_2 = 1',
        commit=True
    )
    logger.info(f"✅ Скрипт_4: Погашение дежурства (затронуто: {affected})")

def script_5() -> None:
    """Скрипт 5: Уход домой неполнозанятых"""
    affected = execute_query(
        '''UPDATE users SET wait_1 = 1 
           WHERE chastota = 'Я тут не каждый день' AND wait_1 = 0''',
        commit=True
    )
    logger.info(f"✅ Скрипт_5: Уход домой неполнозанятых (затронуто: {affected})")

async def script_6(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Скрипт 6: Информирование о дежурном"""
    duty = execute_query(
        'SELECT user_id, name FROM users WHERE count_2 = 1',
        fetchone=True
    )
    
    if duty:
        duty_user_id = duty['user_id']
        duty_name = duty['name'] or f"Пользователь {duty_user_id}"
        
        active_users = get_active_users()
        
        if active_users:
            for user in active_users:
                try:
                    await context.bot.send_message(
                        chat_id=user['user_id'],
                        text=f"☕ <b>Сегодня дежурный:</b> {duty_name}\n\n"
                             f"Не забудьте вымыть кофемашинку после использования!",
                        parse_mode='HTML'
                    )
                except Exception as e:
                    logger.error(f"❌ Ошибка отправки сообщения user_id={user['user_id']}: {e}")
            
            logger.info(f"✅ Скрипт_6: Уведомления отправлены {len(active_users)} пользователям")
        else:
            logger.warning("⚠️  Скрипт_6: Нет активных пользователей для уведомления")
    else:
        logger.warning("⚠️  Скрипт_6: Дежурный не назначен")

# =========== ОБРАБОТЧИКИ КОМАНД ===========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик команды /start"""
    user_id = update.effective_user.id
    create_user(user_id)
    
    await update.message.reply_text(
        "👋 <b>Добро пожаловать в Coffee Duty Bot!</b>\n\n"
        "Введите ваше имя (оно будет видно всем, когда вы будете дежурным):",
        parse_mode='HTML'
    )
    return REGISTRATION

async def registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик ввода имени"""
    user_id = update.effective_user.id
    name = update.message.text.strip()
    
    if not name or len(name) > 50:
        await update.message.reply_text(
            "❌ Имя должно быть от 1 до 50 символов. Попробуйте еще раз:"
        )
        return REGISTRATION
    
    update_user(user_id, name=name)
    
    keyboard = [
        [InlineKeyboardButton("☕ Каждый день", callback_data='daily')],
        [InlineKeyboardButton("⏰ Я тут не каждый день", callback_data='rarely')],
        [InlineKeyboardButton("🚫 Я теперь НЕ пью кофе", callback_data='no_coffee')]
    ]
    
    await update.message.reply_text(
        f"✅ Привет, {name}!\n\n"
        "<b>Как часто вы пьете кофе в офисе?</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )
    return POLL

async def poll_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик выбора частоты кофе"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    data = query.data
    
    if data == 'no_coffee':
        delete_user(user_id)
        await query.edit_message_text(
            "🗑️ <b>Ваши данные удалены.</b>\n\n"
            "Если передумаете - нажмите /start",
            parse_mode='HTML'
        )
        return ConversationHandler.END
    
    if data == 'daily':
        update_user(user_id, chastota="Каждый день")
        
        keyboard = [
            [InlineKeyboardButton("⏸️ Я некоторое время не пью кофе", callback_data='temp_no_coffee')],
            [InlineKeyboardButton("😔 Я дежурный, но не смогу вымыть кофемашинку", callback_data='cant_duty')],
            [InlineKeyboardButton("🎉 Я Вернулся!", callback_data='returned')],
            [InlineKeyboardButton("🔄 Я теперь пью кофе по другому", callback_data='change_habit')]
        ]
        
        await query.edit_message_text(
            "✅ <b>Отлично! Вы теперь 'Главный кофеман' ☕</b>\n\n"
            "Теперь вам будут приходить уведомления о дежурных.\n"
            "Каждый день в 14:00 будет выбираться дежурный.\n\n"
            "<i>Используйте кнопки ниже для изменения статуса:</i>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
        return MAIN_COFFEE
    
    elif data == 'rarely':
        update_user(user_id, chastota="Я тут не каждый день")
        
        keyboard = [
            [InlineKeyboardButton("✅ Я сегодня пью кофе", callback_data='today_coffee')],
            [InlineKeyboardButton("😔 Я дежурный, но не смогу вымыть кофемашинку", callback_data='cant_duty_rare')],
            [InlineKeyboardButton("🔄 Я теперь пью кофе по другому", callback_data='change_habit_rare')]
        ]
        
        await query.edit_message_text(
            "✅ <b>Вы теперь 'Редкий кофеман' ⏰</b>\n\n"
            "Когда вы придете в офис, отметьтесь кнопкой ниже.\n"
            "Также вы можете стать дежурным, если наберете достаточное количество 'кофейных очков'.\n\n"
            "<i>Используйте кнопки ниже:</i>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
        return RARE_COFFEE

async def main_coffee_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик для 'Главных кофеманов'"""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    user = get_user_data(user_id)
    if not user:
        await query.edit_message_text("❌ Пользователь не найден. Нажмите /start")
        return ConversationHandler.END
    
    if query.data == 'temp_no_coffee':
        update_user(user_id, wait_1=1)
        await context.bot.send_message(
            user_id,
            "⏸️ <b>Вы отметили временное отсутствие.</b>\n\n"
            "Когда вернетесь, нажмите 'Я Вернулся!' в меню.",
            parse_mode='HTML'
        )
        await query.edit_message_text(
            "✅ <b>Вы отметили временное отсутствие.</b>\n\n"
            "Уведомления о дежурных приходить не будут.\n"
            "Когда вернетесь, нажмите 'Я Вернулся!'",
            parse_mode='HTML'
        )
        
    elif query.data == 'cant_duty':
        update_user(user_id, wait_2=1, count_2=0)
        await context.bot.send_message(user_id, "😔 <b>Печалька...</b>", parse_mode='HTML')
        
        # Запускаем скрипты для поиска нового дежурного
        script_2()
        await script_6(context)
        
        await query.edit_message_text(
            "✅ <b>Ваш отказ от дежурства учтен.</b>\n\n"
            "Будет выбран новый дежурный, и всем отправлено уведомление.",
            parse_mode='HTML'
        )
        
    elif query.data == 'returned':
        update_user(user_id, wait_1=0)
        await context.bot.send_message(
            user_id,
            "🎉 <b>Ура! С возвращением!</b>\n\n"
            "Теперь вы снова будете получать уведомления о дежурных.",
            parse_mode='HTML'
        )
        await query.edit_message_text("✅ <b>Вы вернулись в строй!</b>", parse_mode='HTML')
        
    elif query.data == 'change_habit':
        return await poll_handler(update, context)
    
    return MAIN_COFFEE

async def rare_coffee_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик для 'Редких кофеманов'"""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    user = get_user_data(user_id)
    if not user:
        await query.edit_message_text("❌ Пользователь не найден. Нажмите /start")
        return ConversationHandler.END
    
    if query.data == 'today_coffee':
        current_count = user['count_1'] or 0
        update_user(user_id, count_1=current_count + 1, wait_1=0)
        
        await context.bot.send_message(
            user_id,
            "✅ <b>Спасибо за отметку!</b>\n\n"
            f"Теперь у вас {current_count + 1} 'кофейных очков'.\n"
            "Чем больше очков, тем выше шанс стать дежурным!",
            parse_mode='HTML'
        )
        await query.edit_message_text(
            "✅ <b>Ваше присутствие отмечено!</b>\n\n"
            f"Кофейных очков: {current_count + 1}",
            parse_mode='HTML'
        )
        
    elif query.data == 'cant_duty_rare':
        update_user(user_id, wait_2=1, count_2=0)
        await context.bot.send_message(user_id, "😔 <b>Печалька...</b>", parse_mode='HTML')
        
        script_2()
        await script_6(context)
        
        await query.edit_message_text(
            "✅ <b>Отказ от дежурства учтен.</b>\n\n"
            "Выбран новый дежурный, всем отправлены уведомления.",
            parse_mode='HTML'
        )
        
    elif query.data == 'change_habit_rare':
        return await poll_handler(update, context)
    
    return RARE_COFFEE

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик отмены"""
    await update.message.reply_text(
        "❌ <b>Действие отменено.</b>\n\n"
        "Используйте /start для начала работы.",
        parse_mode='HTML'
    )
    return ConversationHandler.END

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /status - показать статус пользователя"""
    user_id = update.effective_user.id
    user = get_user_data(user_id)
    
    if user:
        duty = execute_query(
            'SELECT name FROM users WHERE count_2 = 1',
            fetchone=True
        )
        
        duty_text = duty['name'] if duty else "Дежурный еще не выбран"
        
        status_msg = (
            f"📊 <b>Ваш статус:</b>\n"
            f"👤 <b>Имя:</b> {user['name'] or 'Не указано'}\n"
            f"📅 <b>Режим:</b> {user['chastota'] or 'Не указан'}\n"
            f"☕ <b>Кофейных очков:</b> {user['count_1']}\n"
            f"🎖️ <b>Дежурств выполнено:</b> {user['count_2']}\n"
            f"⏸️ <b>Временное отсутствие:</b> {'Да' if user['wait_1'] else 'Нет'}\n"
            f"😔 <b>Отказ от дежурства:</b> {'Да' if user['wait_2'] else 'Нет'}\n\n"
            f"👑 <b>Сегодняшний дежурный:</b> {duty_text}\n\n"
            f"<i>Обновлено: {user['last_updated']}</i>"
        )
    else:
        status_msg = "❌ Вы не зарегистрированы. Используйте /start"
    
    await update.message.reply_text(status_msg, parse_mode='HTML')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /help - помощь"""
    help_text = (
        "🤖 <b>Coffee Duty Bot - помощь</b>\n\n"
        "<b>Основные команды:</b>\n"
        "/start - Начать работу с ботом\n"
        "/status - Показать ваш статус\n"
        "/help - Эта справка\n"
        "/cancel - Отменить текущее действие\n\n"
        
        "<b>Как работает бот:</b>\n"
        "1. Каждый день в 14:00 (UTC) автоматически:\n"
        "   • Прибавляются 'кофейные очки' постоянным пользователям\n"
        "   • Выбирается дежурный на сегодня\n"
        "   • Всем отправляется уведомление\n"
        "2. Каждый день в 21:00 (UTC) автоматически:\n"
        "   • Сбрасываются отказы от дежурства\n"
        "   • Сбрасывается дежурство\n"
        "   • 'Редкие пользователи' отмечаются отсутствующими\n\n"
        
        "<b>Время работы:</b>\n"
        "Автоматические скрипты работают с понедельника по пятницу.\n\n"
        
        "<i>Если что-то не работает, проверьте логи или свяжитесь с администратором.</i>"
    )
    
    await update.message.reply_text(help_text, parse_mode='HTML')

# =========== ФУНКЦИИ ПЛАНИРОВЩИКА ===========
async def daily_14_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Запуск скриптов в 14:00 по UTC (понедельник-пятница)"""
    logger.info("⏰ Запуск ежедневных скриптов 14:00 UTC")
    script_1()  # Прирост кофе
    script_2()  # Поиск дежурного
    await script_6(context)  # Информирование

async def daily_21_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Запуск скриптов в 21:00 по UTC (понедельник-пятница)"""
    logger.info("⏰ Запуск вечерних скриптов 21:00 UTC")
    script_3()  # Обнуление Печальки
    script_4()  # Погашение дежурства
    script_5()  # Уход домой неполнозанятых

# =========== ОСНОВНАЯ ФУНКЦИЯ ===========
def main() -> None:
    """Основная функция запуска бота"""
    logger.info("🚀 Запуск Coffee Duty Bot...")
    
    # Проверяем наличие BOT_TOKEN
    if not BOT_TOKEN or BOT_TOKEN == "ВАШ_TELEGRAM_BOT_TOKEN":
        logger.error("❌ КРИТИЧЕСКАЯ ОШИБКА: BOT_TOKEN не установлен!")
        logger.error("✅ Как исправить:")
        logger.error("1. Зайдите в панель Render")
        logger.error("2. Откройте настройки вашего сервиса")
        logger.error("3. Перейдите в раздел 'Environment'")
        logger.error("4. Добавьте переменную BOT_TOKEN")
        logger.error("5. Вставьте токен из @BotFather")
        logger.error("6. Сохраните и перезапустите сервис")
        sys.exit(1)
    
    # Инициализация базы данных
    init_database()
    
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
    application.add_handler(CommandHandler('status', status_command))
    application.add_handler(CommandHandler('help', help_command))
    
    # Настройка планировщика заданий
    job_queue = application.job_queue
    
    if job_queue:
        # Понедельник-пятница в 14:00 UTC
        job_queue.run_daily(
            daily_14_job,
            time=time(hour=14, minute=0, second=0),
            days=(0, 1, 2, 3, 4),  # Пн-Пт
            name="daily_14_job"
        )
        
        # Понедельник-пятница в 21:00 UTC
        job_queue.run_daily(
            daily_21_job,
            time=time(hour=21, minute=0, second=0),
            days=(0, 1, 2, 3, 4),  # Пн-Пт
            name="daily_21_job"
        )
        
        logger.info("✅ Планировщик заданий настроен")
    
    # Запуск бота
    logger.info("✅ Бот успешно запущен и готов к работе!")
    logger.info(f"👤 Имя бота в Telegram: можно найти по токену {BOT_TOKEN[:10]}...")
    
    # Запускаем поллинг
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == '__main__':
    main()
