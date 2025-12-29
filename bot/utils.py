import re

def escape_markdown(text: str) -> str:
    escape_chars = '_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', '\\\\\\1', str(text))

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
    """
    Пытается извлечь название конфигурации и версию из строки.
    Пример: "Бухгалтерия 3.0 (3.0.189.29)" -> ("Бухгалтерия 3.0", "3.0.189.29")
    """
    # Ищем паттерн версии: 3 или 4 группы цифр через точку (напр. 3.0.123 или 3.0.123.45)
    version_pattern = r'\b\d+\.\d+\.\d+(\.\d+)?\b'
    match = re.search(version_pattern, text)
    
    if match:
        version = match.group(0)
        # Удаляем версию из текста
        text_without_version = text.replace(version, '')
        # Удаляем скобки () [] и лишние пробелы
        clean_name = re.sub(r'[()\[\]]', '', text_without_version)
        clean_name = re.sub(r'\s+', ' ', clean_name).strip()
        # Удаляем запятые на концах, если есть
        clean_name = clean_name.strip(', ')
        return clean_name, version
    
    return text.strip(), None