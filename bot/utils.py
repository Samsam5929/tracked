import re
import html

def escape_markdown(text: str) -> str:
    """
    Полное экранирование для MarkdownV2 согласно документации Telegram.
    """
    if not text: return ""
    # Полный список спецсимволов MarkdownV2
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', str(text))

def normalize_text(text):
    """Для поиска конфигураций на сайте (приводит к нижнему регистру)."""
    return re.sub(r'\s+', ' ', text).strip().lower()

def clean_whitespace(text):
    """Удаляет переносы строк и лишние пробелы, сохраняя регистр."""
    return re.sub(r'\s+', ' ', text).strip()

def version_tuple(v):
    """Преобразует строку версии '3.0.123.45' в кортеж (3, 0, 123, 45)."""
    try:
        # Удаляем все нецифровые символы кроме точек
        clean_v = re.sub(r'[^\d.]', '', str(v))
        return tuple(map(int, clean_v.split('.')))
    except ValueError:
        return (0,)

def parse_registration_text(text):
    tenants_data = []
    chunks = re.split(r'(?=Арендатор:)', text)
    
    for chunk in chunks:
        if not chunk.strip(): continue
            
        tenant_match = re.search(r'Арендатор:\s*(?P<name>.*?)\s+Арендатор ИНН:\s*(?P<inn>\d+)', chunk, re.DOTALL)
        if not tenant_match: continue
            
        name = tenant_match.group('name').strip()
        inn = tenant_match.group('inn').strip()
        
        nom_matches = re.finditer(r'Номенклатура:\s*(?P<nom>.*?)\s+Регистрационный номер:\s*(?P<reg>\d+)', chunk, re.DOTALL)
        
        for match in nom_matches:
            raw_nom = match.group('nom')
            cleaned_nom = clean_whitespace(raw_nom)
            
            tenants_data.append({
                'name': name,
                'inn': inn,
                'nom_raw': cleaned_nom,
                'reg_num': match.group('reg').strip()
            })
    return tenants_data
    
def is_valid_version(version_str: str) -> bool:
    """Проверяет, похоже ли строка на версию 1С (например, 3.0.123.45)."""
    # Разрешаем от 2 до 5 групп цифр через точку
    pattern = r'^\d+(\.\d+){1,4}$'
    return bool(re.match(pattern, version_str.strip()))
    
def parse_config_and_version(text: str):
    
    # Разрешаем от 2 до 5 групп цифр (например, 8.3, 3.0.100, 3.0.123.45)
    version_pattern = r'\b\d+\.\d+(\.\d+){0,3}\b' 
    match = re.search(version_pattern, text)
    
    if match:
        version = match.group(0)
        text_without_version = text.replace(version, '')
        clean_name = re.sub(r'[()\[\]]', '', text_without_version)
        clean_name = re.sub(r'\s+', ' ', clean_name).strip()
        clean_name = clean_name.strip(', ')
        return clean_name, version
    
    return text.strip(), None

def parse_saas_bases(text: str, ignore_list: list = None) -> list:
    """
    Парсит список баз (saas_2641, формат Буквы_Цифры или Буквы_Цифры_Цифры).
    ignore_list: список имен баз, которые нужно пропустить.
    """
    if ignore_list is None: ignore_list = []
    ignore_set = set(ignore_list)
    
    results = []
    lines = text.strip().split('\n')
    
    for line in lines:
        line = line.strip()
        if not line: continue
            
        if 'saas_2641' not in line:
            continue
            
        base_match = re.search(r'База:\s*(\S+)', line)
        if not base_match:
            continue
            
        base_name = base_match.group(1)
        
        # ПРОВЕРКА НА ИСКЛЮЧЕНИЕ
        if base_name in ignore_set:
            continue
        
        # ИСПРАВЛЕНО: Разрешаем формат Base_123_45 (добавлено (?:_\d+)*)
        if re.match(r'^[a-zA-Z]+_\d+(?:_\d+)*$', base_name):
            results.append(base_name)
            
    return results

def split_long_text(text: str, max_length: int = 4000) -> list[str]:
    """
    Разбивает длинный текст на части по переносам строк, 
    чтобы не превысить лимит Telegram (4096).
    """
    if len(text) <= max_length:
        return [text]
    
    parts = []
    while len(text) > max_length:
        # Ищем ближайший перенос строки перед лимитом
        split_idx = text.rfind('\n', 0, max_length)
        if split_idx == -1:
            # Если переносов нет, режем жестко
            split_idx = max_length
        
        parts.append(text[:split_idx])
        text = text[split_idx:].lstrip() # Удаляем ведущий перенос
    
    if text:
        parts.append(text)
        
    return parts