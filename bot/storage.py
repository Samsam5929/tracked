import json
import os
import logging
import shutil
from pathlib import Path
from .config import USER_DATA_DIR

logger = logging.getLogger(__name__)

def get_user_file_path(user_id: int, filename: str) -> Path:
    user_dir = USER_DATA_DIR / str(user_id)
    user_dir.mkdir(exist_ok=True, parents=True)
    return user_dir / filename

def _load_json(user_id: int, filename: str, default=None):
    if default is None: default = {}
    fpath = get_user_file_path(user_id, filename)
    
    if not fpath.exists():
        return default

    try:
        with open(fpath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError:
        # !!! ЗАЩИТА ОТ ПОТЕРИ ДАННЫХ !!!
        # Если файл есть, но он битый (ошибка чтения), делаем его бэкап
        # и только потом возвращаем пустой список.
        backup_path = fpath.with_suffix('.json.bak')
        try:
            shutil.copy(fpath, backup_path)
            logger.error(f"Файл {filename} для пользователя {user_id} поврежден. Создана копия: {backup_path}")
        except Exception as e:
            logger.error(f"Не удалось создать бэкап поврежденного файла {filename}: {e}")
        return default
    except Exception as e:
        logger.error(f"Ошибка чтения файла {filename}: {e}")
        return default

def _save_json(user_id: int, filename: str, data):
    fpath = get_user_file_path(user_id, filename)
    # Создаем временный файл, пишем в него, потом переименовываем.
    # Это предотвращает потерю данных, если бот упадет прямо во время записи.
    temp_path = fpath.with_suffix('.tmp')
    
    try:
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        
        # Атомарная замена файла (безопасно)
        os.replace(temp_path, fpath)
    except Exception as e:
        logger.error(f"Ошибка записи файла {filename}: {e}")
        if temp_path.exists():
            try: os.remove(temp_path)
            except: pass

def load_configs(user_id: int):
    data = _load_json(user_id, 'configs.json', [])
    
    # Миграция данных (на случай старых файлов)
    is_changed = False
    for config in data:
        if 'track_type' not in config:
            config['track_type'] = 'latest'
            is_changed = True
            
    if is_changed:
        save_configs(user_id, data)
    
    return data

def save_configs(user_id: int, data): _save_json(user_id, 'configs.json', data)

def load_bot_state(user_id: int): return _load_json(user_id, 'state.json', {})
def save_bot_state(user_id: int, data): _save_json(user_id, 'state.json', data)

def load_mappings(user_id: int): return _load_json(user_id, 'mappings.json', {})
def save_mappings(user_id: int, data): _save_json(user_id, 'mappings.json', data)