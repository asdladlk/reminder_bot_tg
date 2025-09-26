import asyncio
import sqlite3
import logging
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import re
import schedule
import time
from threading import Thread
import json
import pytz

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class ReminderBot:
    def __init__(self, token: str):
        self.token = token
        self.db_path = "reminders.db"
        self.init_database()
    
    def get_user_timezone(self, user_id: int) -> str:
        """Получить часовой пояс пользователя"""
        # Захардкодим московское время для всех пользователей
        return 'Europe/Moscow'
    
    def set_user_timezone(self, user_id: int, timezone: str):
        """Установить часовой пояс пользователя"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                timezone TEXT DEFAULT 'Europe/Moscow',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            INSERT OR REPLACE INTO user_settings (user_id, timezone)
            VALUES (?, ?)
        ''', (user_id, timezone))
        
        conn.commit()
        conn.close()
    
    def get_local_time(self, user_id: int) -> datetime:
        """Получить локальное время пользователя"""
        # Захардкодим московское время
        moscow_tz = pytz.timezone('Europe/Moscow')
        return datetime.now(moscow_tz)
        
    def init_database(self):
        """Инициализация базы данных"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                reminder_time TEXT NOT NULL,
                frequency TEXT NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_sent TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                timezone TEXT DEFAULT 'Europe/Moscow',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def add_reminder(self, user_id: int, message: str, reminder_time: str, frequency: str) -> int:
        """Добавить новое напоминание"""
        logger.info(f"💾 Сохраняем напоминание: user_id={user_id}, message='{message}', time='{reminder_time}', frequency='{frequency}'")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO reminders (user_id, message, reminder_time, frequency)
            VALUES (?, ?, ?, ?)
        ''', (user_id, message, reminder_time, frequency))
        
        reminder_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        logger.info(f"✅ Напоминание сохранено с ID: {reminder_id}")
        return reminder_id
    
    def get_user_reminders(self, user_id: int) -> List[Dict]:
        """Получить все напоминания пользователя"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, message, reminder_time, frequency, is_active, created_at
            FROM reminders 
            WHERE user_id = ? AND is_active = 1
            ORDER BY created_at DESC
        ''', (user_id,))
        
        reminders = []
        for row in cursor.fetchall():
            reminders.append({
                'id': row[0],
                'message': row[1],
                'reminder_time': row[2],
                'frequency': row[3],
                'is_active': row[4],
                'created_at': row[5]
            })
        
        conn.close()
        return reminders
    
    def delete_reminder(self, reminder_id: int, user_id: int) -> bool:
        """Удалить напоминание"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            DELETE FROM reminders 
            WHERE id = ? AND user_id = ?
        ''', (reminder_id, user_id))
        
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        
        return deleted
    
    def parse_time_input(self, time_str: str) -> Optional[Dict]:
        """Парсинг времени и периодичности из пользовательского ввода"""
        time_str = time_str.strip().lower()
        logger.info(f"🔍 Парсинг времени: '{time_str}'")
        
        # Паттерны для разового напоминания (более специфичные идут первыми)
        once_patterns = [
            r'через (\d+) (минут|час|часа|часов|день|дня|дней)',
            r'(\d{1,2})\.(\d{1,2})\.(\d{4}) в (\d{1,2}):(\d{2})',  # дата с годом
            r'(\d{1,2})\.(\d{1,2}) в (\d{1,2}):(\d{2})',  # дата без года (текущий год)
            r'(\d{1,2})/(\d{1,2})/(\d{4}) в (\d{1,2}):(\d{2})',  # формат дд/мм/гггг
            r'(\d{1,2})/(\d{1,2}) в (\d{1,2}):(\d{2})',  # формат дд/мм (текущий год)
            r'завтра в (\d{1,2}):(\d{2})',
            r'в (\d{1,2}):(\d{2})'  # общий паттерн времени идет последним
        ]
        
        # Паттерны для периодических напоминаний
        periodic_patterns = [
            r'каждый день в (\d{1,2}):(\d{2})',
            r'(\d+) раз в день',
            r'(\d+) раз в неделю в (\d{1,2}):(\d{2})',
            r'по будням в (\d{1,2}):(\d{2})',
            r'по выходным в (\d{1,2}):(\d{2})',
            r'по (понедельник|вторник|среда|четверг|пятница|суббота|воскресенье) в (\d{1,2}):(\d{2})',
            r'по (пн|вт|ср|чт|пт|сб|вс) в (\d{1,2}):(\d{2})',
            r'каждый (понедельник|вторник|среда|четверг|пятница|суббота|воскресенье) в (\d{1,2}):(\d{2})',
            r'каждый (пн|вт|ср|чт|пт|сб|вс) в (\d{1,2}):(\d{2})'
        ]
        
        # Проверяем периодические напоминания СНАЧАЛА (они более специфичные)
        for i, pattern in enumerate(periodic_patterns):
            match = re.search(pattern, time_str)
            if match:
                logger.info(f"✅ Найден периодический паттерн {i+1}: {pattern}, группы: {match.groups()}")
                result = self._parse_periodic_reminder(match, pattern)
                logger.info(f"📅 Результат парсинга: {result}")
                return result
        
        # Проверяем разовые напоминания
        for i, pattern in enumerate(once_patterns):
            match = re.search(pattern, time_str)
            if match:
                logger.info(f"✅ Найден разовый паттерн {i+1}: {pattern}, группы: {match.groups()}")
                result = self._parse_once_reminder(match, pattern)
                logger.info(f"📅 Результат парсинга: {result}")
                return result
        
        logger.info("❌ Не найдено подходящих паттернов")
        return None
    
    def _parse_once_reminder(self, match, pattern):
        """Парсинг разового напоминания"""
        if 'через' in pattern:
            amount = int(match.group(1))
            unit = match.group(2)
            
            # Используем московское время
            moscow_tz = pytz.timezone('Europe/Moscow')
            now = datetime.now(moscow_tz)
            if 'минут' in unit:
                reminder_time = now + timedelta(minutes=amount)
            elif 'час' in unit:
                reminder_time = now + timedelta(hours=amount)
            elif 'день' in unit:
                reminder_time = now + timedelta(days=amount)
            
            return {
                'type': 'once',
                'time': reminder_time.strftime('%Y-%m-%d %H:%M'),
                'frequency': 'once'
            }
        
        elif 'завтра' in pattern:
            hour = int(match.group(1))
            minute = int(match.group(2))
            # Используем московское время
            moscow_tz = pytz.timezone('Europe/Moscow')
            tomorrow = datetime.now(moscow_tz) + timedelta(days=1)
            reminder_time = tomorrow.replace(hour=hour, minute=minute, second=0, microsecond=0)
            
            return {
                'type': 'once',
                'time': reminder_time.strftime('%Y-%m-%d %H:%M'),
                'frequency': 'once'
            }
        
        elif 'в' in pattern and len(match.groups()) == 2:
            hour = int(match.group(1))
            minute = int(match.group(2))
            # Используем московское время
            moscow_tz = pytz.timezone('Europe/Moscow')
            today = datetime.now(moscow_tz)
            reminder_time = today.replace(hour=hour, minute=minute, second=0, microsecond=0)
            
            # Если время уже прошло сегодня, переносим на завтра
            if reminder_time <= today:
                reminder_time += timedelta(days=1)
            
            return {
                'type': 'once',
                'time': reminder_time.strftime('%Y-%m-%d %H:%M'),
                'frequency': 'once'
            }
        
        elif len(match.groups()) == 5:  # формат дд.мм.гггг в чч:мм
            day = int(match.group(1))
            month = int(match.group(2))
            year = int(match.group(3))
            hour = int(match.group(4))
            minute = int(match.group(5))
            
            try:
                # Используем московское время
                moscow_tz = pytz.timezone('Europe/Moscow')
                reminder_time = datetime(year, month, day, hour, minute)
                reminder_time = moscow_tz.localize(reminder_time)
                
                return {
                    'type': 'once',
                    'time': reminder_time.strftime('%Y-%m-%d %H:%M'),
                    'frequency': 'once'
                }
            except ValueError:
                return None
        
        elif len(match.groups()) == 4:  # формат дд.мм в чч:мм (текущий год)
            day = int(match.group(1))
            month = int(match.group(2))
            hour = int(match.group(3))
            minute = int(match.group(4))
            
            try:
                # Используем московское время
                moscow_tz = pytz.timezone('Europe/Moscow')
                current_year = datetime.now(moscow_tz).year
                reminder_time = datetime(current_year, month, day, hour, minute)
                reminder_time = moscow_tz.localize(reminder_time)
                
                # Если дата уже прошла в этом году, переносим на следующий год
                if reminder_time < datetime.now(moscow_tz):
                    reminder_time = reminder_time.replace(year=current_year + 1)
                
                return {
                    'type': 'once',
                    'time': reminder_time.strftime('%Y-%m-%d %H:%M'),
                    'frequency': 'once'
                }
            except ValueError:
                return None
        
        return None
    
    def _parse_periodic_reminder(self, match, pattern):
        """Парсинг периодического напоминания"""
        logger.info(f"🔍 Парсинг периодического напоминания: pattern='{pattern}', groups={match.groups()}")
        
        if 'каждый день' in pattern:
            hour = int(match.group(1))
            minute = int(match.group(2))
            
            return {
                'type': 'periodic',
                'time': f"{hour:02d}:{minute:02d}",
                'frequency': 'daily'
            }
        
        elif 'раз в день' in pattern:
            times_per_day = int(match.group(1))
            
            return {
                'type': 'periodic',
                'time': '09:00',  # По умолчанию в 9 утра
                'frequency': f'{times_per_day}_times_daily'
            }
        
        elif 'раз в неделю' in pattern:
            times_per_week = int(match.group(1))
            hour = int(match.group(2))
            minute = int(match.group(3))
            
            return {
                'type': 'periodic',
                'time': f"{hour:02d}:{minute:02d}",
                'frequency': f'{times_per_week}_times_weekly'
            }
        
        elif 'будням' in pattern:
            hour = int(match.group(1))
            minute = int(match.group(2))
            
            return {
                'type': 'periodic',
                'time': f"{hour:02d}:{minute:02d}",
                'frequency': 'weekdays'
            }
        
        elif 'выходным' in pattern:
            hour = int(match.group(1))
            minute = int(match.group(2))
            
            return {
                'type': 'periodic',
                'time': f"{hour:02d}:{minute:02d}",
                'frequency': 'weekends'
            }
        
        # Проверяем дни недели по захваченной группе, а не по паттерну
        day_name = match.group(1).lower()
        hour = int(match.group(2))
        minute = int(match.group(3))
        
        day_mapping = {
            'понедельник': 'monday',
            'пн': 'monday',
            'вторник': 'tuesday', 
            'вт': 'tuesday',
            'среда': 'wednesday',
            'ср': 'wednesday',
            'четверг': 'thursday',
            'чт': 'thursday',
            'пятница': 'friday',
            'пт': 'friday',
            'суббота': 'saturday',
            'сб': 'saturday',
            'воскресенье': 'sunday',
            'вс': 'sunday'
        }
        
        if day_name in day_mapping:
            frequency = day_mapping[day_name]
            result = {
                'type': 'periodic',
                'time': f"{hour:02d}:{minute:02d}",
                'frequency': frequency
            }
            logger.info(f"✅ Распознан день недели '{day_name}' -> '{frequency}': {result}")
            return result
        
        return None

# Создаем экземпляр бота
bot = ReminderBot("YOUR_BOT_TOKEN_HERE")  # Замените на ваш токен

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    welcome_text = """
🤖 Добро пожаловать в бота напоминаний!

Я помогу вам не забывать важные дела. Вот что я умею:

📝 **Создать напоминание:**
Просто напишите мне сообщение в формате:
"Напомни мне [текст] [время]"

**Примеры времени:**
• Разово: "в 15:30", "завтра в 10:00", "через 2 часа"
• Конкретная дата: "9.10.2025 в 12:00", "15.03 в 14:30", "25/12/2024 в 18:00"
• Ежедневно: "каждый день в 09:00"
• Несколько раз в день: "3 раза в день"
• По будням: "по будням в 18:00"
• По выходным: "по выходным в 10:00"
• По дням недели: "по понедельник в 14:00", "каждый пт в 16:30"

**Команды:**
/list - показать все напоминания
/help - помощь
/delete [номер] - удалить напоминание
/timezone - настроить часовой пояс
/test - создать тестовое напоминание
/debug - отладка базы данных

Начните с создания первого напоминания! 🚀
    """
    await update.message.reply_text(welcome_text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = """
📚 **Справка по использованию бота**

**Создание напоминаний:**
Напишите сообщение в формате: "Напомни мне [текст] [время]"

**Примеры:**
• "Напомни мне позвонить маме в 19:00"
• "Напомни мне принять лекарство каждый день в 08:00"
• "Напомни мне встречу завтра в 14:30"
• "Напомни мне съодить к врачу 9.10.2025 в 12:00"
• "Напомни мне день рождения 15.03 в 10:00"
• "Напомни мне пить воду 5 раз в день"
• "Напомни мне тренировку по понедельник в 18:00"
• "Напомни мне звонок каждый пт в 16:00"

**Команды:**
/start - начать работу с ботом
/list - показать все ваши напоминания
/delete [номер] - удалить напоминание по номеру
/timezone - настроить часовой пояс
/test - создать тестовое напоминание
/debug - отладка базы данных
/help - показать эту справку

**Типы напоминаний:**
• Разовые - срабатывают один раз
• Ежедневные - каждый день в указанное время
• Периодические - несколько раз в день/неделю
• По дням недели - только в будни или выходные
• По конкретным дням - понедельник, вторник, среда, четверг, пятница, суббота, воскресенье
    """
    await update.message.reply_text(help_text)

async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /list"""
    user_id = update.effective_user.id
    reminders = bot.get_user_reminders(user_id)
    
    if not reminders:
        await update.message.reply_text("📭 У вас пока нет активных напоминаний.")
        return
    
    text = "📋 **Ваши напоминания:**\n\n"
    for i, reminder in enumerate(reminders, 1):
        text += f"{i}. {reminder['message']}\n"
        text += f"   ⏰ {reminder['reminder_time']}\n"
        text += f"   🔄 {reminder['frequency']}\n"
        text += f"   📅 Создано: {reminder['created_at']}\n\n"
    
    await update.message.reply_text(text)

async def delete_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /delete"""
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text("❌ Укажите номер напоминания для удаления.\nПример: /delete 1")
        return
    
    try:
        reminder_num = int(context.args[0])
        reminders = bot.get_user_reminders(user_id)
        
        if reminder_num < 1 or reminder_num > len(reminders):
            await update.message.reply_text("❌ Неверный номер напоминания.")
            return
        
        reminder_id = reminders[reminder_num - 1]['id']
        success = bot.delete_reminder(reminder_id, user_id)
        
        if success:
            await update.message.reply_text(f"✅ Напоминание #{reminder_num} удалено.")
        else:
            await update.message.reply_text("❌ Ошибка при удалении напоминания.")
            
    except ValueError:
        await update.message.reply_text("❌ Номер напоминания должен быть числом.")

async def timezone_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /timezone"""
    user_id = update.effective_user.id
    
    if not context.args:
        current_tz = bot.get_user_timezone(user_id)
        local_time = bot.get_local_time(user_id)
        
        text = f"🕐 **Ваш часовой пояс:** {current_tz}\n"
        text += f"🕐 **Текущее время:** {local_time.strftime('%H:%M:%S %d.%m.%Y')}\n\n"
        text += "**Доступные часовые пояса:**\n"
        text += "• `/timezone Europe/Moscow` - Москва\n"
        text += "• `/timezone Europe/Kiev` - Киев\n"
        text += "• `/timezone Europe/Minsk` - Минск\n"
        text += "• `/timezone Europe/London` - Лондон\n"
        text += "• `/timezone America/New_York` - Нью-Йорк\n"
        text += "• `/timezone Asia/Tokyo` - Токио\n"
        text += "• `/timezone Asia/Shanghai` - Пекин\n"
        text += "• `/timezone Australia/Sydney` - Сидней\n\n"
        text += "**Пример:** `/timezone Europe/Moscow`"
        
        await update.message.reply_text(text)
        return
    
    timezone = context.args[0]
    
    # Проверяем, что часовой пояс существует
    try:
        pytz.timezone(timezone)
        bot.set_user_timezone(user_id, timezone)
        local_time = bot.get_local_time(user_id)
        
        text = f"✅ Часовой пояс установлен: {timezone}\n"
        text += f"🕐 Текущее время: {local_time.strftime('%H:%M:%S %d.%m.%Y')}"
        
        await update.message.reply_text(text)
    except pytz.exceptions.UnknownTimeZoneError:
        await update.message.reply_text("❌ Неизвестный часовой пояс. Используйте команду `/timezone` для просмотра доступных вариантов.")

async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /test для тестирования отправки сообщений"""
    user_id = update.effective_user.id
    
    try:
        # Создаем тестовое напоминание на 1 минуту вперед (в московском времени)
        moscow_tz = pytz.timezone('Europe/Moscow')
        test_time = datetime.now(moscow_tz) + timedelta(minutes=1)
        reminder_id = bot.add_reminder(
            user_id, 
            "🧪 Тестовое напоминание", 
            test_time.strftime('%Y-%m-%d %H:%M'), 
            'once'
        )
        
        await update.message.reply_text(
            f"✅ Тестовое напоминание создано!\n"
            f"🆔 ID: {reminder_id}\n"
            f"⏰ Время: {test_time.strftime('%H:%M:%S %d.%m.%Y')}\n"
            f"📝 Сообщение: 🧪 Тестовое напоминание\n\n"
            f"Ожидайте сообщение через 1 минуту..."
        )
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при создании тестового напоминания: {e}")

async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /debug для отладки базы данных"""
    user_id = update.effective_user.id
    
    try:
        conn = sqlite3.connect(bot.db_path)
        cursor = conn.cursor()
        
        # Получаем все напоминания пользователя
        cursor.execute('''
            SELECT id, message, reminder_time, frequency, is_active, created_at, last_sent
            FROM reminders 
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 10
        ''', (user_id,))
        
        reminders = cursor.fetchall()
        conn.close()
        
        if not reminders:
            await update.message.reply_text("📭 У вас нет напоминаний в базе данных.")
            return
        
        text = "🔍 **Отладка базы данных:**\n\n"
        for reminder in reminders:
            reminder_id, message, reminder_time, frequency, is_active, created_at, last_sent = reminder
            text += f"🆔 ID: {reminder_id}\n"
            text += f"📝 Сообщение: {message}\n"
            text += f"⏰ Время: {reminder_time}\n"
            text += f"🔄 Частота: {frequency}\n"
            text += f"✅ Активно: {bool(is_active)}\n"
            text += f"📅 Создано: {created_at}\n"
            text += f"📤 Последняя отправка: {last_sent or 'Никогда'}\n\n"
        
        await update.message.reply_text(text)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при отладке: {e}")

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админская команда для просмотра всех напоминаний в боте"""
    user_id = update.effective_user.id
    
    # Проверяем, что команда вызвана с правильным паролем
    if not context.args or context.args[0] != "TheRules":
        await update.message.reply_text("❌ Неверная команда.")
        return
    
    try:
        conn = sqlite3.connect(bot.db_path)
        cursor = conn.cursor()
        
        # Получаем все напоминания всех пользователей
        cursor.execute('''
            SELECT id, user_id, message, reminder_time, frequency, is_active, created_at, last_sent
            FROM reminders 
            ORDER BY created_at DESC
            LIMIT 50
        ''')
        
        reminders = cursor.fetchall()
        conn.close()
        
        if not reminders:
            await update.message.reply_text("📭 В боте нет напоминаний.")
            return
        
        text = "🔐 **Админская панель - Все напоминания:**\n\n"
        for reminder in reminders:
            reminder_id, user_id, message, reminder_time, frequency, is_active, created_at, last_sent = reminder
            text += f"🆔 ID: {reminder_id}\n"
            text += f"👤 Пользователь: {user_id}\n"
            text += f"📝 Сообщение: {message}\n"
            text += f"⏰ Время: {reminder_time}\n"
            text += f"🔄 Частота: {frequency}\n"
            text += f"✅ Активно: {bool(is_active)}\n"
            text += f"📅 Создано: {created_at}\n"
            text += f"📤 Последняя отправка: {last_sent or 'Никогда'}\n\n"
            
            # Ограничиваем длину сообщения
            if len(text) > 3500:
                text += "... (показаны первые 50 напоминаний)"
                break
        
        await update.message.reply_text(text)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при получении данных: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик обычных сообщений"""
    user_id = update.effective_user.id
    message_text = update.message.text
    
    # Проверяем, является ли сообщение запросом на напоминание
    if message_text.lower().startswith('напомни мне'):
        reminder_text = message_text[12:].strip()  
        
        # Пытаемся найти время в тексте
        time_info = bot.parse_time_input(reminder_text)
        
        if time_info:
            # Извлекаем текст без времени
            text_without_time = reminder_text
            logger.info(f"🔍 Исходный текст: '{reminder_text}'")
            
            for pattern in [
                r'\s+через\s+\d+\s+(минут|час|часа|часов|день|дня|дней)',
                r'\s+в\s+\d{1,2}:\d{2}',
                r'\s+завтра\s+в\s+\d{1,2}:\d{2}',
                r'\s+\d{1,2}\.\d{1,2}\.\d{4}\s+в\s+\d{1,2}:\d{2}',
                r'\s+\d{1,2}\.\d{1,2}\s+в\s+\d{1,2}:\d{2}',
                r'\s+\d{1,2}/\d{1,2}/\d{4}\s+в\s+\d{1,2}:\d{2}',
                r'\s+\d{1,2}/\d{1,2}\s+в\s+\d{1,2}:\d{2}',
                r'\s+каждый\s+день\s+в\s+\d{1,2}:\d{2}',
                r'\s+\d+\s+раз\s+в\s+(день|неделю)',
                r'\s+по\s+(будням|выходным)\s+в\s+\d{1,2}:\d{2}'
            ]:
                old_text = text_without_time
                text_without_time = re.sub(pattern, '', text_without_time, flags=re.IGNORECASE)
                if old_text != text_without_time:
                    logger.info(f"🔄 Удален паттерн '{pattern}': '{old_text}' -> '{text_without_time}'")
            
            reminder_message = text_without_time.strip()
            logger.info(f"📝 Финальный текст напоминания: '{reminder_message}'")
            
            if reminder_message:
                # Добавляем напоминание в базу данных
                reminder_id = bot.add_reminder(
                    user_id, 
                    reminder_message, 
                    time_info['time'], 
                    time_info['frequency']
                )
                
                response = f"✅ Напоминание создано!\n\n"
                response += f"📝 Текст: {reminder_message}\n"
                response += f"⏰ Время: {time_info['time']}\n"
                response += f"🔄 Периодичность: {time_info['frequency']}\n"
                response += f"🆔 ID: {reminder_id}"
                
                await update.message.reply_text(response)
            else:
                await update.message.reply_text("❌ Не удалось определить текст напоминания.")
        else:
            await update.message.reply_text("❌ Не удалось распознать время напоминания.\n\nПримеры:\n• в 15:30\n• завтра в 10:00\n• 9.10.2025 в 12:00\n• 15.03 в 14:30\n• каждый день в 09:00\n• через 2 часа")
    else:
        await update.message.reply_text("🤖 Для создания напоминания используйте формат:\n\"Напомни мне [текст] [время]\"\n\nИли используйте команду /help для получения справки.")

class SchedulerManager:
    """Менеджер планировщика напоминаний"""
    
    def __init__(self, bot_instance, application):
        self.bot_instance = bot_instance
        self.application = application
        self.running = False
        
    def start_scheduler(self):
        """Запуск планировщика"""
        self.running = True
        scheduler_thread = Thread(target=self._run_scheduler, daemon=True)
        scheduler_thread.start()
        logger.info("Планировщик запущен")
    
    def _run_scheduler(self):
        """Основной цикл планировщика"""
        while self.running:
            try:
                self._check_and_send_reminders()
                time.sleep(30)  # Проверяем каждые 30 секунд
            except Exception as e:
                logger.error(f"Ошибка в планировщике: {e}")
                time.sleep(60)
    
    def _check_and_send_reminders(self):
        """Проверка и отправка напоминаний"""
        conn = sqlite3.connect(self.bot_instance.db_path)
        cursor = conn.cursor()
        
        # Получаем все активные напоминания
        cursor.execute('''
            SELECT id, user_id, message, reminder_time, frequency, last_sent
            FROM reminders 
            WHERE is_active = 1
        ''')
        
        reminders = cursor.fetchall()
        logger.info(f"🔍 Найдено {len(reminders)} активных напоминаний для проверки")
        
        for reminder in reminders:
            reminder_id, user_id, message, reminder_time, frequency, last_sent = reminder
            
            try:
                # Получаем локальное время пользователя
                user_tz = self.bot_instance.get_user_timezone(user_id)
                tz = pytz.timezone(user_tz)
                current_time = datetime.now(tz)
                
                logger.info(f"🔍 Напоминание {reminder_id}: время_из_БД='{reminder_time}', частота='{frequency}', текущее_время={current_time}")
                
                should_send = self._should_send_reminder(reminder_time, frequency, last_sent, current_time, user_id)
                logger.info(f"Проверка напоминания {reminder_id}: время={reminder_time}, частота={frequency}, последняя_отправка={last_sent}, отправить={should_send}")
                
                if should_send:
                    # Отправляем напоминание
                    logger.info(f"🚀 Отправляем напоминание {reminder_id} пользователю {user_id}")
                    
                    # Используем простой подход - создаем новый event loop
                    import asyncio
                    try:
                        # Создаем новый event loop для этого потока
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        
                        # Запускаем корутину
                        loop.run_until_complete(self._send_reminder(user_id, message, reminder_id, frequency))
                        
                        # Закрываем loop
                        loop.close()
                    except Exception as e:
                        logger.error(f"Ошибка при отправке напоминания {reminder_id}: {e}")
                        # Пытаемся закрыть loop в случае ошибки
                        try:
                            if 'loop' in locals():
                                loop.close()
                        except:
                            pass
                    
                    # Для разовых напоминаний удаляем их после отправки
                    if frequency == 'once':
                        cursor.execute('''
                            DELETE FROM reminders 
                            WHERE id = ?
                        ''', (reminder_id,))
                        logger.info(f"🗑️ Разовое напоминание {reminder_id} удалено после отправки")
                    else:
                        # Для периодических напоминаний обновляем время последней отправки
                        cursor.execute('''
                            UPDATE reminders 
                            SET last_sent = ? 
                            WHERE id = ?
                        ''', (current_time.strftime('%Y-%m-%d %H:%M:%S'), reminder_id))
                    
            except Exception as e:
                logger.error(f"Ошибка при обработке напоминания {reminder_id}: {e}")
        
        conn.commit()
        conn.close()
    
    def _should_send_reminder(self, reminder_time: str, frequency: str, last_sent: str, current_time: datetime, user_id: int) -> bool:
        """Определяет, нужно ли отправить напоминание"""
        
        if frequency == 'once':
            # Разовое напоминание
            try:
                # Время в базе хранится в московском времени
                moscow_tz = pytz.timezone('Europe/Moscow')
                target_time = datetime.strptime(reminder_time, '%Y-%m-%d %H:%M')
                target_time = moscow_tz.localize(target_time)
                
                # Отправляем если время пришло и еще не отправляли
                if current_time >= target_time and not last_sent:
                    logger.info(f"Разовое напоминание: текущее время {current_time} >= время напоминания {target_time}, не отправляли")
                    return True
                else:
                    logger.info(f"Разовое напоминание: текущее время {current_time} < время напоминания {target_time} или уже отправляли")
                    return False
            except Exception as e:
                logger.error(f"Ошибка парсинга времени разового напоминания: {e}")
                return False
        
        elif frequency == 'daily':
            # Ежедневное напоминание
            try:
                target_time = datetime.strptime(reminder_time, '%H:%M').time()
                current_time_only = current_time.time()
                
                # Проверяем, что текущее время больше или равно времени напоминания
                if current_time_only >= target_time:
                    if not last_sent:
                        return True
                    
                    last_sent_dt = datetime.strptime(last_sent, '%Y-%m-%d %H:%M:%S')
                    # Отправляем, если последняя отправка была не сегодня
                    return last_sent_dt.date() < current_time.date()
                
                return False
            except:
                return False
        
        elif frequency == 'weekdays':
            # По будням (понедельник-пятница)
            if current_time.weekday() < 5:  # 0-4 это понедельник-пятница
                try:
                    target_time = datetime.strptime(reminder_time, '%H:%M').time()
                    current_time_only = current_time.time()
                    
                    if current_time_only >= target_time:
                        if not last_sent:
                            return True
                        
                        last_sent_dt = datetime.strptime(last_sent, '%Y-%m-%d %H:%M:%S')
                        return last_sent_dt.date() < current_time.date()
                    
                    return False
                except:
                    return False
            return False
        
        elif frequency == 'weekends':
            # По выходным (суббота-воскресенье)
            if current_time.weekday() >= 5:  # 5-6 это суббота-воскресенье
                try:
                    target_time = datetime.strptime(reminder_time, '%H:%M').time()
                    current_time_only = current_time.time()
                    
                    if current_time_only >= target_time:
                        if not last_sent:
                            return True
                        
                        last_sent_dt = datetime.strptime(last_sent, '%Y-%m-%d %H:%M:%S')
                        return last_sent_dt.date() < current_time.date()
                    
                    return False
                except:
                    return False
            return False
        
        elif 'times_daily' in frequency:
            # Несколько раз в день
            times_per_day = int(frequency.split('_')[0])
            try:
                if not last_sent:
                    return True
                
                last_sent_dt = datetime.strptime(last_sent, '%Y-%m-%d %H:%M:%S')
                
                # Если последняя отправка была не сегодня, отправляем
                if last_sent_dt.date() < current_time.date():
                    return True
                
                # Подсчитываем, сколько раз уже отправляли сегодня
                conn = sqlite3.connect(self.bot_instance.db_path)
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT COUNT(*) FROM reminders 
                    WHERE user_id = ? 
                    AND DATE(last_sent) = DATE(?)
                    AND frequency = ?
                ''', (user_id, current_time.strftime('%Y-%m-%d'), frequency))
                
                sent_today = cursor.fetchone()[0]
                conn.close()
                
                # Отправляем, если еще не достигли лимита
                return sent_today < times_per_day
                
            except:
                return False
        
        elif frequency in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']:
            # Напоминания по конкретным дням недели
            day_mapping = {
                'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
                'friday': 4, 'saturday': 5, 'sunday': 6
            }
            
            target_day = day_mapping[frequency]
            current_day = current_time.weekday()
            
            # Проверяем, что сегодня нужный день недели
            if current_day == target_day:
                try:
                    target_time = datetime.strptime(reminder_time, '%H:%M').time()
                    current_time_only = current_time.time()
                    
                    if current_time_only >= target_time:
                        if not last_sent:
                            return True
                        
                        last_sent_dt = datetime.strptime(last_sent, '%Y-%m-%d %H:%M:%S')
                        # Отправляем, если последняя отправка была не сегодня
                        return last_sent_dt.date() < current_time.date()
                    
                    return False
                except:
                    return False
            
            return False
        
        return False
    
    async def _send_reminder(self, user_id: int, message: str, reminder_id: int, frequency: str = None):
        """Отправка напоминания пользователю"""
        try:
            if frequency == 'once':
                reminder_text = f"🔔 Напоминание!\n\n{message}\n\n✅ Разовое напоминание выполнено и удалено."
            else:
                reminder_text = f"🔔 Напоминание!\n\n{message}"
            
            # Проверяем, что бот доступен
            if not self.application.bot:
                logger.error(f"❌ Бот не инициализирован для отправки напоминания {reminder_id}")
                return
            
            # Отправляем сообщение
            await self.application.bot.send_message(chat_id=user_id, text=reminder_text)
            logger.info(f"✅ Напоминание {reminder_id} отправлено пользователю {user_id}: {message}")
            
        except Exception as e:
            error_msg = str(e).lower()
            if "bot was blocked by the user" in error_msg or "chat not found" in error_msg:
                logger.warning(f"⚠️ Пользователь {user_id} заблокировал бота или чат не найден. Деактивируем напоминание {reminder_id}")
                # Деактивируем напоминание для заблокированного пользователя
                try:
                    conn = sqlite3.connect(self.bot_instance.db_path)
                    cursor = conn.cursor()
                    cursor.execute('''
                        UPDATE reminders 
                        SET is_active = 0 
                        WHERE id = ?
                    ''', (reminder_id,))
                    conn.commit()
                    conn.close()
                except Exception as db_error:
                    logger.error(f"Ошибка при деактивации напоминания {reminder_id}: {db_error}")
            else:
                logger.error(f"❌ Ошибка при отправке напоминания {reminder_id} пользователю {user_id}: {e}")
                # Логируем дополнительную информацию для отладки
                logger.error(f"Детали ошибки: тип={type(e).__name__}, сообщение={str(e)}")

def main():
    """Основная функция запуска бота"""
    # Получаем токен из переменных окружения (для облачного развертывания)
    BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
    
    if BOT_TOKEN == 'YOUR_BOT_TOKEN_HERE':
        print("❌ ОШИБКА: Установите переменную окружения BOT_TOKEN!")
        print("Для локального запуска замените YOUR_BOT_TOKEN_HERE на токен вашего бота")
        print("Для облачного развертывания установите переменную BOT_TOKEN")
        return
    
    # Создаем приложение
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("list", list_reminders))
    application.add_handler(CommandHandler("delete", delete_reminder))
    application.add_handler(CommandHandler("timezone", timezone_command))
    application.add_handler(CommandHandler("test", test_command))
    application.add_handler(CommandHandler("debug", debug_command))
    application.add_handler(CommandHandler("admin", admin_command))
    
    # Добавляем обработчик обычных сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Создаем и запускаем планировщик
    scheduler = SchedulerManager(bot, application)
    scheduler.start_scheduler()
    
    # Запускаем бота
    print("🤖 Бот запущен! Нажмите Ctrl+C для остановки.")
    print("📝 Токен получен из переменных окружения")
    print("🌍 Поддержка часовых поясов включена")
    application.run_polling()

if __name__ == '__main__':
    main()
