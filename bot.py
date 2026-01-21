import os
import logging
import sys
import random
from datetime import datetime, time
import asyncio

import psycopg2
from psycopg2 import pool
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
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Состояния
REGISTRATION, POLL, MAIN_COFFEE, RARE_COFFEE = range(4)

# Переменные окружения
BOT_TOKEN = os.environ.get('BOT_TOKEN')
DATABASE_URL = os.environ.get('DATABASE_URL')

if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не установлен!")
    sys.exit(1)

# Пул соединений
db_pool = None

def init_database():
    """Инициализация базы данных"""
    global db_pool
    
    try:
        # Используем DATABASE_URL или создаем локальную базу
        if DATABASE_URL:
            # Для Render PostgreSQL
            db_pool = psycopg2.pool.SimpleConnectionPool(
                1, 10, DATABASE_URL, sslmode='require'
            )
        else:
            # Для локальной разработки
            db_pool = psycopg2.pool.SimpleConnectionPool(
                1, 10, 
                host='localhost',
                database='coffee_bot',
                user='postgres',
                password=''
            )
        
        conn = db_pool.getconn()
        cursor = conn.cursor()
        
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
        db_pool.putconn(conn)
        logger.info("✅ База данных инициализирована")
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
        # Пробуем использовать SQLite как запасной вариант
        try:
            import sqlite3
            conn = sqlite3.connect('coffee_bot.db', check_same_thread=False)
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
            logger.info("✅ SQLite база данных инициализирована")
            return True
        except Exception as e2:
            logger.error(f"❌ Ошибка SQLite: {e2}")
            sys.exit(1)

# ... остальной код остается таким же ...
