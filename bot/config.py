import os
import sys
import json
import logging
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler

# --- ПУТИ ---
def get_base_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE_PATH = get_base_path()
SETTINGS_FILE = os.path.join(BASE_PATH, 'settings.json')

LOG_DIR = os.path.join(BASE_PATH, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

USER_DATA_DIR = Path(BASE_PATH) / 'data' / 'user_data'

# --- КОНСТАНТЫ СОСТОЯНИЙ ---
# Добавлено GET_CONFIG_TYPE
GET_CONFIG_NAME, GET_CONFIG_TYPE, SELECT_CONFIG, GET_MANUAL_CONFIG, GET_CURRENT_VERSION = range(5)
GET_REG_TEXT = 5

# --- ЛОГИРОВАНИЕ ---
def setup_logging():
    log_file_path = os.path.join(LOG_DIR, 'bot.log')
    
    rotating_handler = TimedRotatingFileHandler(
        log_file_path, when='midnight', interval=1, backupCount=1, encoding='utf-8'
    )
    rotating_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

    handlers = [rotating_handler]
    
    if sys.stdout:
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level=logging.INFO,
        handlers=handlers
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    
    try:
        sys.stderr = open(os.path.join(LOG_DIR, 'critical_errors.log'), 'a', encoding='utf-8')
    except Exception:
        pass

# --- ЗАГРУЗКА НАСТРОЕК ---
def load_settings():
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        default_settings = {
            'TELEGRAM_BOT_TOKEN': 'ВАШ_ТОКЕН',
            'LOGIN_1C': 'ВАШ_ЛОГИН',
            'PASSWORD_1C': 'ВАШ_ПАРОЛЬ',
            'ADMIN_USER_ID': 0,
            'TIMEZONE': 'Asia/Novosibirsk',
            'SCHEDULE_HOUR': 9,
            'SCHEDULE_MINUTE': 0
        }
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(default_settings, f, ensure_ascii=False, indent=4)
        print(f'Файл настроек создан: {SETTINGS_FILE}')
        sys.exit()

settings = load_settings()
TELEGRAM_TOKEN = settings.get('TELEGRAM_BOT_TOKEN')
LOGIN_1C = settings.get('LOGIN_1C')
PASSWORD_1C = settings.get('PASSWORD_1C')
ADMIN_USER_ID = int(settings.get('ADMIN_USER_ID', 0))
TIMEZONE = settings.get('TIMEZONE', 'Asia/Novosibirsk')
SCHEDULE_HOUR = settings.get('SCHEDULE_HOUR', 9)
SCHEDULE_MINUTE = settings.get('SCHEDULE_MINUTE', 0)