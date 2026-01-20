from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from .storage import load_configs

def get_main_keyboard(user_id: int, configs=None, show_ack_button: bool = False):
    if configs is None:
        configs = load_configs(user_id)
    
    keyboard = []
    keyboard.append([InlineKeyboardButton('📋 Показать список', callback_data='main_menu')])
    keyboard.append([InlineKeyboardButton('🔄 Проверить версии (Releases)', callback_data='get_versions')])
    
    keyboard.append([
        InlineKeyboardButton('🧮 Калькулятор', callback_data='check_updates_start'),
        InlineKeyboardButton('📝 Регистрация', callback_data='reg_start')
    ])
    
    keyboard.append([InlineKeyboardButton('🧹 Фильтр баз (saas)', callback_data='cleanup_start')])

    # Кнопка добавляется ТОЛЬКО если есть флаг show_ack_button=True и есть новые версии
    if show_ack_button and any((c.get('is_new', False) for c in configs)):
        keyboard.append([InlineKeyboardButton('✅ Отметить все как просмотренные', callback_data='ack_all')])
        
    keyboard.append([InlineKeyboardButton('⚙️ Управление списком', callback_data='manage_list_menu')])
    
    return InlineKeyboardMarkup(keyboard)

def get_ignore_menu_keyboard(ignore_list):
    keyboard = []
    keyboard.append([InlineKeyboardButton('➕ Добавить исключение', callback_data='add_ignore_start')])
    
    for base_name in ignore_list:
        import hashlib
        name_hash = hashlib.md5(base_name.encode()).hexdigest()
        keyboard.append([InlineKeyboardButton(f'🗑 {base_name}', callback_data=f'del_ign_{name_hash}')])
        
    keyboard.append([InlineKeyboardButton('⬅️ Назад в настройки', callback_data='manage_list_menu')])
    return InlineKeyboardMarkup(keyboard)

def get_manage_keyboard():
    keyboard = [
        [
            InlineKeyboardButton('➕ Добавить базу', callback_data='add_config_start'),
            InlineKeyboardButton('➖ Удалить базу', callback_data='remove_config_menu')
        ],
        [
            InlineKeyboardButton('🛠 Изменить тип', callback_data='change_type_menu'),
            InlineKeyboardButton('↕️ Порядок', callback_data='reorder_config_menu')
        ],
        [
            InlineKeyboardButton('📂 Словарь (Рег.)', callback_data='manage_mappings_menu'),
            InlineKeyboardButton('🚫 Игнор (SaaS)', callback_data='manage_ignore_menu')
        ],
        [InlineKeyboardButton('⬅️ Назад в главное меню', callback_data='main_menu')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_type_selection_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🔥 Только самую новую', callback_data='type_latest')],
        [InlineKeyboardButton('🛡 Только ДП (LTS)', callback_data='type_dp')],
        [InlineKeyboardButton('👀 И то, и другое (New + ДП)', callback_data='type_both')],
        [InlineKeyboardButton('🎯 Конкретная ветка', callback_data='type_specific')],
        [InlineKeyboardButton('🎯 Ветка + 🛡 ДП', callback_data='type_specific_dp')],
        [InlineKeyboardButton('⬅️ Отмена', callback_data='main_menu')]
    ])