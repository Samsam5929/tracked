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
            # Извлекаем "грязный" текст
            raw_nom = match.group('nom')
            # Чистим его от переносов строк и двойных пробелов
            cleaned_nom = clean_whitespace(raw_nom)
            
            tenants_data.append({
                'name': name,
                'inn': inn,
                'nom_raw': cleaned_nom, # Сохраняем уже чистую версию
                'reg_num': match.group('reg').strip()
            })
    return tenants_data