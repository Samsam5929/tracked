from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from .storage import load_configs

def get_main_keyboard(user_id: int, configs=None):
    if configs is None:
        configs = load_configs(user_id)
    
    keyboard = []

    # --- НОВАЯ КНОПКА ---
    # Позволяет вернуться к списку версий с любого экрана (например, после регистрации),
    # не запуская проверку на сайте.
    keyboard.append([InlineKeyboardButton('📋 Показать список', callback_data='main_menu')])
    # --------------------

    keyboard.append([InlineKeyboardButton('🔄 Проверить версии (Releases)', callback_data='get_versions')])
    keyboard.append([InlineKeyboardButton('📈 Узнать кол-во обновлений', callback_data='check_updates_start')])
    keyboard.append([InlineKeyboardButton('📝 Регистрация арендаторов', callback_data='reg_start')])
    
    if any((c.get('is_new', False) for c in configs)):
        keyboard.append([InlineKeyboardButton('✅ Отметить все как просмотренные', callback_data='ack_all')])
        
    keyboard.append([InlineKeyboardButton('⚙️ Управление списком', callback_data='manage_list_menu')])
    
    return InlineKeyboardMarkup(keyboard)

# Остальные функции (get_manage_keyboard, get_type_selection_keyboard) оставляем без изменений,
# так как ты просил не менять расположение кнопок кардинально.
def get_manage_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('➕ Добавить', callback_data='add_config_start')],
        [InlineKeyboardButton('➖ Удалить', callback_data='remove_config_menu')],
        [InlineKeyboardButton('🛠 Изменить тип', callback_data='change_type_menu')],
        [InlineKeyboardButton('↕️ Изменить порядок', callback_data='reorder_config_menu')],
        [InlineKeyboardButton('📂 Словарь замен', callback_data='manage_mappings_menu')],
        [InlineKeyboardButton('⬅️ Назад', callback_data='main_menu')]
    ])

def get_type_selection_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🔥 Только самую новую', callback_data='type_latest')],
        [InlineKeyboardButton('🛡 Только ДП (LTS)', callback_data='type_dp')],
        [InlineKeyboardButton('👀 И то, и другое (New + ДП)', callback_data='type_both')],
        [InlineKeyboardButton('🎯 Конкретная ветка', callback_data='type_specific')],
        [InlineKeyboardButton('🎯 Ветка + 🛡 ДП', callback_data='type_specific_dp')],
        [InlineKeyboardButton('⬅️ Отмена', callback_data='main_menu')]
    ])