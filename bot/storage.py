import json
from pathlib import Path
from .config import USER_DATA_DIR

def get_user_file_path(user_id: int, filename: str) -> Path:
    user_dir = USER_DATA_DIR / str(user_id)
    user_dir.mkdir(exist_ok=True, parents=True)
    return user_dir / filename

def _load_json(user_id: int, filename: str, default=None):
    if default is None: default = {}
    fpath = get_user_file_path(user_id, filename)
    try:
        with open(fpath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

def _save_json(user_id: int, filename: str, data):
    fpath = get_user_file_path(user_id, filename)
    with open(fpath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def load_configs(user_id: int): return _load_json(user_id, 'configs.json', [])
def save_configs(user_id: int, data): _save_json(user_id, 'configs.json', data)

def load_bot_state(user_id: int): return _load_json(user_id, 'state.json', {})
def save_bot_state(user_id: int, data): _save_json(user_id, 'state.json', data)

def load_mappings(user_id: int): return _load_json(user_id, 'mappings.json', {})
def save_mappings(user_id: int, data): _save_json(user_id, 'mappings.json', data)