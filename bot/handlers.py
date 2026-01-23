import asyncio
import html
import logging
import hashlib
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, ConversationHandler
from telegram.error import BadRequest, Forbidden
from .config import *
from .storage import *
from .utils import * 
from .keyboards import *
from . import service_1c

logger = logging.getLogger(__name__)

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

async def send_or_edit_message(context, chat_id, text, reply_markup=None):
    bot_state = load_bot_state(chat_id)
    msg_id = bot_state.get('main_menu_message_id')
    try:
        if not msg_id: raise ValueError
        await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, parse_mode='MarkdownV2', reply_markup=reply_markup)
    except Exception:
        if msg_id:
            try: await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except: pass
        sent = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode='MarkdownV2', reply_markup=reply_markup)
        bot_state['main_menu_message_id'] = sent.message_id
        save_bot_state(chat_id, bot_state)

async def delete_extra_messages(context, user_id):
    state = load_bot_state(user_id)
    for mid in state.get('extra_message_ids', []):
        try: await context.bot.delete_message(chat_id=user_id, message_id=mid)
        except: pass
    state['extra_message_ids'] = []
    save_bot_state(user_id, state)

def format_version_list_from_storage(user_id: int):
    configs = load_configs(user_id)
    if not configs:
        return ('Конфигурации для отслеживания не найдены\\.', configs)
    results_text = []
    for config_obj in configs:
        config_name = config_obj.get('name', 'Неизвестная конфигурация')
        safe_config_name = escape_markdown(config_name)
        last_version = config_obj.get('last_version')
        last_date = config_obj.get('last_date')
        track_type = config_obj.get('track_type', 'latest')
        branch_filter = config_obj.get('branch_filter', '')
        is_new = config_obj.get('is_new', False)
        
        status_mark = ICON_NEW_VERSION if is_new else ICON_OK
        
        display_lines = []
        
        def format_line(icon, ver, date, mark):
            # ver и date не экранируем, так как они внутри блока кода `...`
            return f"{mark} {icon} `{ver}` {SEPARATOR_SYMBOL} `{date}`"
        
        if not last_version or not last_date:
            display_lines.append('   └ Данных пока нет ⏳')
        else:
            if (track_type == 'both' or track_type == 'specific_dp') and last_version and '|' in last_version:
                ver_parts = last_version.split('|')
                date_parts = last_date.split('|') if last_date and '|' in last_date else [last_date, '-']
                
                v_primary = ver_parts[0]
                d_primary = date_parts[0]
                v_dp = ver_parts[1] if len(ver_parts) > 1 else "Нет"
                d_dp = date_parts[1] if len(date_parts) > 1 else "-"
                
                icon_primary = ICON_SPECIFIC_TYPE if track_type == 'specific_dp' else ICON_LATEST_TYPE
                
                line1 = format_line(icon_primary, v_primary, d_primary, status_mark)
                if track_type == 'specific_dp' and branch_filter:
                    line1 += f" \\(фильтр: `{escape_markdown(branch_filter)}`\\)"
                
                display_lines.append(line1)
                display_lines.append(format_line(ICON_LTS_TYPE, v_dp, d_dp, status_mark))
            
            else:
                icon = ICON_LATEST_TYPE
                if track_type == 'dp': icon = ICON_LTS_TYPE
                elif track_type == 'specific' or track_type == 'specific_dp': icon = ICON_SPECIFIC_TYPE
                
                line = format_line(icon, last_version, last_date, status_mark)
                
                if (track_type == 'specific' or track_type == 'specific_dp') and branch_filter:
                    line += f" \\(фильтр: `{escape_markdown(branch_filter)}`\\)"
                
                display_lines.append(line)

        block_text = f'*{safe_config_name}*\n' + '\n'.join(display_lines)
        results_text.append(block_text)
        
    return ('\n\n'.join(results_text), configs)

# --- ОБРАБОТЧИКИ (HANDLERS) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f'Пользователь {user_id} запустил бота.')
    await main_menu_callback(update, context)

async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    if query:
        await query.answer()
    
    await delete_extra_messages(context, user_id)
    
    configs = load_configs(user_id)
    if not configs:
        header = escape_markdown('👋 *Добро пожаловать!*\n\nЯ бот для отслеживания версий 1С. Ваш список пока пуст. Добавьте конфигурации через меню "Управление списком".\n\n')
    else:
        header = escape_markdown('📋 *Последние известные данные:*\n\n')
    
    result_text, configs = format_version_list_from_storage(user_id)
    full_text = header + result_text
    
    await send_or_edit_message(context, user_id, full_text, get_main_keyboard(user_id, configs, show_ack_button=True))

async def daily_version_check(context: ContextTypes.DEFAULT_TYPE):
    logger.info('ЗАПУСК ежедневной проверки...')
    if not USER_DATA_DIR.exists(): return

    # Используем кэшированную сессию (она сама перелогинится если надо)
    session, error = await asyncio.to_thread(service_1c.get_cached_session)
    if error or not session:
        logger.error(f"Daily check login failed: {error}")
        return

    try:
        # 1. Обновляем ГЛОБАЛЬНЫЙ СПИСОК (Releases)
        has_updates, cache_error = await asyncio.to_thread(service_1c.refresh_global_cache, session, force=False)
        
        if cache_error:
            logger.error(f"Cache update error: {cache_error}")
            
        # 2. Собираем список отслеживаемых конфигураций
        all_tracked_configs = set()
        user_ids = [int(p.name) for p in USER_DATA_DIR.iterdir() if p.is_dir() and p.name.isdigit()]
        
        for user_id in user_ids:
            configs = load_configs(user_id)
            for c in configs:
                all_tracked_configs.add(c['name'])
        
        # 3. Обновляем МАТРИЦЫ только для нужных баз
        if all_tracked_configs:
            logger.info(f"Запуск обновления матриц для {len(all_tracked_configs)} конфигураций...")
            await asyncio.to_thread(service_1c.update_matrices_for_list, session, list(all_tracked_configs))

        # 4. Рассылка
        if has_updates:
            logger.info("Рассылка уведомлений пользователям...")
            for user_id in user_ids:
                try:
                    user_configs = load_configs(user_id)
                    if not user_configs: continue
                    
                    result_text, updated_configs = await asyncio.to_thread(
                        service_1c.sync_user_configs_with_cache, user_configs, session
                    )
                    save_configs(user_id, updated_configs)
                    
                    if any(c.get('is_new') for c in updated_configs):
                        full_text = escape_markdown('🗓️ *Найдены обновления 1С:*\n\n') + result_text
                        await send_or_edit_message(context, user_id, full_text, get_main_keyboard(user_id, updated_configs, show_ack_button=True))
                except Exception as e:
                    logger.error(f'Error user {user_id}: {e}')
                    
    finally:
        # Сессию не закрываем, она живет в service_1c
        pass

async def get_versions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ручная проверка (онлайн)."""
    user_id = update.effective_user.id
    if update.callback_query:
        await update.callback_query.answer()
        try: await update.callback_query.message.delete()
        except: pass
        
        msg = await context.bot.send_message(chat_id=user_id, text='⏳ Синхронизация с сайтом 1С...')
        bot_state = load_bot_state(user_id)
        bot_state['main_menu_message_id'] = msg.message_id
        save_bot_state(user_id, bot_state)
    
    session, error = await asyncio.to_thread(service_1c.get_cached_session)
    if error:
        await send_or_edit_message(context, user_id, f"Ошибка входа: {escape_markdown(error)}", get_main_keyboard(user_id))
        return ConversationHandler.END

    try:
        # Обновляем глобальный кэш
        has_updates, cache_error = await asyncio.to_thread(service_1c.refresh_global_cache, session, force=False)
        
        if cache_error:
             await send_or_edit_message(context, user_id, f"Ошибка: {escape_markdown(cache_error)}", get_main_keyboard(user_id))
             return ConversationHandler.END

        # Обновляем матрицы для баз пользователя
        configs = load_configs(user_id)
        config_names = [c['name'] for c in configs]
        if config_names:
             await asyncio.to_thread(service_1c.update_matrices_for_list, session, config_names)

        # Формируем результат
        result_text, updated_configs = await asyncio.to_thread(
            service_1c.sync_user_configs_with_cache, configs, session
        )
        save_configs(user_id, updated_configs)
        
        header_text = '🔍 *Результат проверки:*\n\n'
        if not has_updates:
            header_text = '♻️ *Изменений на сайте нет.*\nПоследние данные:\n\n'

        full_text = escape_markdown(header_text) + result_text
        await send_or_edit_message(context, user_id, full_text, get_main_keyboard(user_id, updated_configs, show_ack_button=True))
    
    except Exception as e:
        logger.error(f"Error in manual check: {e}")
        await send_or_edit_message(context, user_id, f"Ошибка: {escape_markdown(str(e))}", get_main_keyboard(user_id))
        
    return ConversationHandler.END

async def acknowledge_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    await query.answer('Все обновления отмечены!')
    
    configs = load_configs(user_id)
    for i in range(len(configs)):
        configs[i]['is_new'] = False
    save_configs(user_id, configs)
    
    await main_menu_callback(update, context)

async def manage_list_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    await query.answer()
    text = (
        r"⚙️ *Настройки списка*" "\n\n"
        r"Здесь вы можете добавлять и удалять базы, менять режим отслеживания \(ЛТС/Обычная\) "
        r"и настраивать словарь для помощника регистрации\."
    )
    await send_or_edit_message(context, user_id, text, get_manage_keyboard())

async def add_config_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Главное меню', callback_data='main_menu')]])
    
    prompt_message = await query.edit_message_text(
        text='Пришлите мне полное название конфигурации для отслеживания.',
        reply_markup=keyboard
    )
    context.user_data['prompt_message_id'] = prompt_message.message_id
    return GET_CONFIG_NAME

async def handle_new_config_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    raw_input = update.message.text.strip()
    
    try: await context.bot.delete_message(chat_id=user_id, message_id=update.message.id)
    except: pass
    if 'prompt_message_id' in context.user_data:
        try: await context.bot.delete_message(chat_id=user_id, message_id=context.user_data['prompt_message_id'])
        except: pass

    # Поиск в кэше
    candidates = await asyncio.to_thread(service_1c.search_config_candidates, raw_input)
    
    # Сохраняем введенное пользователем на случай, если он выберет "Использовать мой вариант"
    context.user_data['manual_name_input'] = raw_input
    
    # Если точное совпадение одно - сразу переходим дальше
    if len(candidates) == 1 and normalize_text(candidates[0]) == normalize_text(raw_input):
        context.user_data['new_config_name'] = candidates[0]
        return await _ask_config_type(context, user_id, candidates[0])

    keyboard = []
    
    # Кнопки с найденными вариантами
    if candidates:
        msg_text = f'🔎 По запросу "*{escape_markdown(raw_input)}*" найдено:'
        # Сохраняем кандидатов в user_data, чтобы в callback передавать только индекс
        context.user_data['search_candidates'] = candidates
        for i, name in enumerate(candidates):
            keyboard.append([InlineKeyboardButton(name, callback_data=f'cand_sel_{i}')])
    else:
        msg_text = f'🔎 По запросу "*{escape_markdown(raw_input)}*" совпадений в кэше не найдено\\.'

    # Всегда даем опцию использовать именно то, что ввел юзер
    keyboard.append([InlineKeyboardButton(f'✍️ Использовать: {raw_input}', callback_data='cand_manual')])
    keyboard.append([InlineKeyboardButton('🔙 Отмена', callback_data='main_menu')])

    msg = await context.bot.send_message(
        chat_id=user_id,
        text=msg_text,
        parse_mode='MarkdownV2',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data['prompt_message_id'] = msg.message_id
    return SELECT_CONFIG_CANDIDATE
    
async def handle_new_config_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    await query.answer()
    
    track_type = query.data.replace('type_', '')
    
    if track_type in ['specific', 'specific_dp']:
        context.user_data['pending_track_type'] = track_type
        await query.edit_message_text(
            text=r"Введите начало версии, которую нужно отслеживать \(например, `3\.0\.12`\):",
            parse_mode='MarkdownV2'
        )
        return GET_SPECIFIC_BRANCH

    try: await query.message.delete()
    except: pass
    
    return await _save_new_config(update, context, track_type, "")

async def handle_specific_branch_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    branch_filter = update.message.text.strip()
    
    track_type = context.user_data.get('pending_track_type', 'specific')
    
    try: await context.bot.delete_message(chat_id=user_id, message_id=update.message.id)
    except: pass
    
    if 'prompt_message_id' in context.user_data:
        try: await context.bot.delete_message(chat_id=user_id, message_id=context.user_data['prompt_message_id'])
        except: pass
        
    return await _save_new_config(update, context, track_type, branch_filter)

async def _save_new_config(update, context, track_type, branch_filter):
    user_id = update.effective_user.id
    config_name = context.user_data.get('new_config_name')
    
    if not config_name:
        await context.bot.send_message(chat_id=user_id, text="Ошибка: имя конфигурации потеряно. Попробуйте снова.")
        return ConversationHandler.END

    configs = load_configs(user_id)
    configs.append({
        'name': config_name,
        'track_type': track_type,
        'branch_filter': branch_filter,
        'last_version': '',
        'last_date': '',
        'is_new': False
    })
    save_configs(user_id, configs)
    
    context.user_data.pop('new_config_name', None)
    context.user_data.pop('prompt_message_id', None)
    context.user_data.pop('pending_track_type', None)

    type_desc = {
        'latest': 'Самая новая', 
        'dp': 'Только ДП', 
        'both': 'ДП + Новая',
        'specific': f'Ветка {branch_filter}',
        'specific_dp': f'Ветка {branch_filter} + ДП'
    }.get(track_type, track_type)
    
    success_text = f'✅ Конфигурация *{escape_markdown(config_name)}* добавлена\\!\nТип: {escape_markdown(type_desc)}'
    await send_or_edit_message(context, user_id, success_text, get_main_keyboard(user_id, configs, show_ack_button=True))
    return ConversationHandler.END
    
async def remove_config_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    await query.answer()
    configs = load_configs(user_id)
    if not configs:
        await query.edit_message_text(text='Список уже пуст.', reply_markup=get_manage_keyboard())
    else:
        keyboard = [[InlineKeyboardButton(f"🗑️ {c['name']}", callback_data=f'remove_{i}')] for i, c in enumerate(configs)]
        keyboard.append([InlineKeyboardButton('⬅️ Назад', callback_data='manage_list_menu')])
        await query.edit_message_text(text='Нажмите на конфигурацию для удаления:', reply_markup=InlineKeyboardMarkup(keyboard))

async def remove_config_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    await query.answer()
    index_to_remove = int(query.data.split('_')[1])
    configs = load_configs(user_id)
    if 0 <= index_to_remove < len(configs):
        configs.pop(index_to_remove)
        save_configs(user_id, configs)
    await remove_config_menu(update, context)

async def change_type_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    await query.answer()
    
    configs = load_configs(user_id)
    if not configs:
        await query.edit_message_text(text='Список пуст.', reply_markup=get_manage_keyboard())
        return

    keyboard = []
    for i, c in enumerate(configs):
        t = c.get('track_type')
        icon = '🔥'
        if t == 'dp': icon = '🛡'
        elif t == 'both': icon = '👀'
        elif t == 'specific': icon = '🎯'
        elif t == 'specific_dp': icon = '🎯🛡'
        
        btn_text = f"{icon} {c['name']}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f'chtype_sel_{i}')])
    
    keyboard.append([InlineKeyboardButton('⬅️ Назад', callback_data='manage_list_menu')])
    await query.edit_message_text(text='Выберите конфигурацию для смены типа отслеживания:', reply_markup=InlineKeyboardMarkup(keyboard))

async def change_type_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    index = int(query.data.split('_')[2])
    context.user_data['edit_config_index'] = index
    configs = load_configs(update.effective_user.id)
    name = configs[index]['name']
    await query.edit_message_text(text=f'Настройка для: *{escape_markdown(name)}*\nВыберите новый режим:', parse_mode='MarkdownV2', reply_markup=get_type_selection_keyboard())

async def change_type_save_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    await query.answer()
    
    new_type = query.data.replace('type_', '')
    index = context.user_data.get('edit_config_index')
    
    if index is None:
        await change_type_menu(update, context)
        return

    if new_type in ['specific', 'specific_dp']:
        await query.edit_message_text(
            text="⚠️ Для смены на этот тип нужно удалить и добавить конфигурацию заново (чтобы задать ветку).",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Назад', callback_data='manage_list_menu')]])
        )
        return

    configs = load_configs(user_id)
    if 0 <= index < len(configs):
        configs[index]['track_type'] = new_type
        configs[index]['last_version'] = '' 
        configs[index]['last_date'] = ''
        configs[index]['branch_filter'] = ''
        save_configs(user_id, configs)
    
    await change_type_menu(update, context)

async def reorder_config_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    await query.answer()
    configs = load_configs(user_id)
    if len(configs) < 2:
        await query.edit_message_text(text='Нужно хотя бы 2 конфигурации для изменения порядка.', reply_markup=get_manage_keyboard())
    else:
        keyboard = []
        for i, config_obj in enumerate(configs):
            row_buttons = []
            if i > 0: row_buttons.append(InlineKeyboardButton('🔼', callback_data=f'move_up_{i}'))
            if i < len(configs) - 1: row_buttons.append(InlineKeyboardButton('🔽', callback_data=f'move_down_{i}'))
            label_button = InlineKeyboardButton(f"{i + 1}. {config_obj['name']}", callback_data='noop')
            full_row = [label_button]
            if i == 0 and len(configs) > 1: full_row.extend([InlineKeyboardButton(' ', callback_data='noop'), row_buttons[0]])
            elif i == len(configs) - 1 and len(configs) > 1: full_row.extend([row_buttons[0], InlineKeyboardButton(' ', callback_data='noop')])
            elif len(configs) > 2: full_row.extend(row_buttons)
            keyboard.append(full_row)
        keyboard.append([InlineKeyboardButton('⬅️ Назад', callback_data='manage_list_menu')])
        await query.edit_message_text(text='Используйте стрелки для изменения порядка:', reply_markup=InlineKeyboardMarkup(keyboard))

async def move_config_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    await query.answer()
    _, direction, index_str = query.data.split('_')
    index = int(index_str)
    configs = load_configs(user_id)
    if direction == 'up' and index > 0:
        configs[index], configs[index - 1] = (configs[index - 1], configs[index])
    elif direction == 'down' and index < len(configs) - 1:
        configs[index], configs[index + 1] = (configs[index + 1], configs[index])
    save_configs(user_id, configs)
    await reorder_config_menu(update, context)

async def noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()

async def check_updates_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    await query.answer()
    configs = load_configs(user_id)
    keyboard = []
    if configs:
        for i, config in enumerate(configs):
            keyboard.append([InlineKeyboardButton(config['name'], callback_data=f'select_config_{i}')])
    keyboard.append([InlineKeyboardButton('⌨️ Ввести вручную', callback_data='manual_config')])
    
    keyboard.append([InlineKeyboardButton('⬅️ Главное меню', callback_data='cancel_update_check')])
    
    await query.edit_message_text(text='Выберите конфигурацию для проверки или введите ее название вручную:', reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_CONFIG

async def check_updates_select_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    await query.answer()
    config_index = int(query.data.split('_')[2])
    configs = load_configs(user_id)
    selected_config_name = configs[config_index]['name']
    context.user_data['selected_config'] = selected_config_name
    
    await query.edit_message_text(
        text=f'Выбрана конфигурация: *{escape_markdown(selected_config_name)}*\n\n' + 
             'Теперь, пожалуйста, пришлите номер вашей текущей версии \\(например, `3.0.123.45`\\)\\.', 
        parse_mode='MarkdownV2'
    )
    return GET_CURRENT_VERSION

async def check_updates_manual_config_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text='Пожалуйста, введите *полное и точное* название конфигурации:')
    return GET_MANUAL_CONFIG

async def check_updates_handle_manual_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    config_name = update.message.text
    context.user_data['selected_config'] = config_name
    try: await context.bot.delete_message(chat_id=user_id, message_id=update.message.id)
    except: pass
    
    bot_state = load_bot_state(user_id)
    main_menu_id = bot_state.get('main_menu_message_id')
    text = f'Выбрана конфигурация: *{escape_markdown(config_name)}*\n\nТеперь пришлите номер вашей текущей версии \\(например, `3.0.123.45`\\)\\.'
    
    if main_menu_id:
        try: await context.bot.edit_message_text(chat_id=user_id, message_id=main_menu_id, text=text, parse_mode='MarkdownV2')
        except: await context.bot.send_message(chat_id=user_id, text=text, parse_mode='MarkdownV2')
    return GET_CURRENT_VERSION

async def _perform_update_check(update: Update, context: ContextTypes.DEFAULT_TYPE, config_name: str, user_version: str):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if not is_valid_version(user_version):
        await context.bot.send_message(
            chat_id=chat_id,
            text='❌ *Некорректный формат версии*\\.\nПример: `3.0.123.45`',
            parse_mode='MarkdownV2'
        )
        return ConversationHandler.END

    await send_or_edit_message(
        context, chat_id, 
        text=f'⏳ *Конфигурация:* {escape_markdown(config_name)}\n*Версия:* `{user_version}`\n\n🔍 *Ищу данные в кэше\\.\\.\\.*', 
        reply_markup=None
    )
    
    targets, error = await asyncio.to_thread(service_1c.get_target_versions_from_cache, config_name)
    
    if error:
        await send_or_edit_message(context, chat_id, text=f"❌ {error}", reply_markup=get_main_keyboard(user_id))
        context.user_data.clear()
        return ConversationHandler.END
    
    dp_target = targets['dp']
    non_dp_target = targets['non_dp']
    
    status_text = f'✅ Версия на ДП: `{escape_markdown(dp_target)}`'
    if dp_target != non_dp_target:
        status_text += f'\n✅ Версия не на ДП: `{escape_markdown(non_dp_target)}`'
        
    await send_or_edit_message(
        context, chat_id, 
        text=f'{status_text}\n\n⏳ *Рассчитываю путь обновления (это может занять время)\\.\\.\\.*', 
        reply_markup=None
    )
    
    # ПОЛУЧАЕМ СЕССИЮ ДЛЯ ОНЛАЙН-ДОКАЧКИ МАТРИЦ
    session = None
    is_cached = await asyncio.to_thread(service_1c.has_cached_matrix, config_name)
    
    if not is_cached:
        await send_or_edit_message(
            context, chat_id, 
            text=f'⏳ *Данных нет в кэше\\. Скачиваю таблицу обновлений с сайта 1С\\.\\.\\.*', 
            reply_markup=None
        )
        session, error = await asyncio.to_thread(service_1c.get_cached_session)
        if error:
            await send_or_edit_message(context, chat_id, text=f"❌ Ошибка входа: {escape_markdown(error)}", reply_markup=get_main_keyboard(user_id))
            return ConversationHandler.END

    result_text = await asyncio.to_thread(
        service_1c.find_update_path, session, config_name, user_version, dp_target, non_dp_target
    )
    
    header = escape_markdown('📊 *Результат:* \n\n')
    full_text = header + result_text
    
    # РАЗБИВКА ДЛИННОГО СООБЩЕНИЯ
    parts = split_long_text(full_text)
    
    # Удаляем сообщение "Рассчитываю..." перед отправкой результата, если возможно,
    # или используем send_or_edit только для первой части.
    bot_state = load_bot_state(user_id)
    main_id = bot_state.get('main_menu_message_id')
    
    if main_id:
        try: await context.bot.delete_message(chat_id=chat_id, message_id=main_id)
        except: pass
        bot_state['main_menu_message_id'] = None
        save_bot_state(user_id, bot_state)

    for i, part in enumerate(parts):
        reply_markup = get_main_keyboard(user_id) if i == len(parts) - 1 else None
        sent = await context.bot.send_message(
            chat_id=chat_id, 
            text=part, 
            parse_mode='MarkdownV2', 
            reply_markup=reply_markup
        )
        if i == len(parts) - 1:
            bot_state['main_menu_message_id'] = sent.message_id
            save_bot_state(user_id, bot_state)

    context.user_data.clear()
    return ConversationHandler.END

async def check_updates_calculate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_version = update.message.text.strip()
    config_name = context.user_data.get('selected_config')
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if not config_name:
        await update.message.reply_text('Произошла ошибка: конфигурация не была выбрана. Попробуйте снова.')
        return ConversationHandler.END
    
    if not is_valid_version(user_version):
        try: await context.bot.delete_message(chat_id=chat_id, message_id=update.message.id)
        except: pass
        await context.bot.send_message(
            chat_id=chat_id,
            text='❌ *Некорректный формат версии*\\.\nПример: `3.0.123.45`',
            parse_mode='MarkdownV2'
        )
        return GET_CURRENT_VERSION
    
    try: await context.bot.delete_message(chat_id=chat_id, message_id=update.message.id)
    except: pass
        
    await send_or_edit_message(
        context, chat_id, 
        text=f'⏳ *Ищу актуальные версии для {escape_markdown(config_name)}\\.\\.\\.*', 
        reply_markup=None
    )
    
    # 1. Получаем целевые версии из кэша
    targets, error = await asyncio.to_thread(service_1c.get_target_versions_from_cache, config_name)
    
    if error:
        await send_or_edit_message(context, chat_id, text=f"❌ {escape_markdown(error)}", reply_markup=get_main_keyboard(user_id))
        context.user_data.clear()
        return ConversationHandler.END
    
    dp_target = targets['dp']
    non_dp_target = targets['non_dp']
    
    # 2. Формируем текст статуса (ЗДЕСЬ БЫЛА ОШИБКА, ЭТОТ БЛОК ДОЛЖЕН БЫТЬ ТУТ)
    status_text = f'✅ Версия на ДП: `{escape_markdown(dp_target)}`'
    if dp_target != non_dp_target:
        status_text += f'\n✅ Версия не на ДП: `{escape_markdown(non_dp_target)}`'
    
    # 3. Сообщаем о начале расчета
    await send_or_edit_message(
        context, chat_id, 
        text=f'{status_text}\n\n⏳ *Рассчитываю цепочку обновлений от* `{user_version}`*\\.\\.\\.*', 
        reply_markup=None
    )
    
    # 4. Проверяем наличие матрицы и при необходимости качаем её (Оптимизация)
    session = None
    is_cached = await asyncio.to_thread(service_1c.has_cached_matrix, config_name)
    
    if not is_cached:
        await send_or_edit_message(
            context, chat_id, 
            text=f'{status_text}\n\n⏳ *Данных нет в кэше\\. Скачиваю таблицу обновлений с сайта 1С\\.\\.\\.*', 
            reply_markup=None
        )
        session, error = await asyncio.to_thread(service_1c.get_cached_session)
        if error:
            await send_or_edit_message(context, chat_id, text=f"❌ Ошибка входа: {escape_markdown(error)}", reply_markup=get_main_keyboard(user_id))
            return ConversationHandler.END

    # 5. Считаем путь
    result_text = await asyncio.to_thread(
        service_1c.find_update_path, session, config_name, user_version, dp_target, non_dp_target
    )
    
    header = escape_markdown('📊 *Результат подсчета обновлений:*\n\n')
    full_text = header + result_text
    
    # 6. Разбиваем длинное сообщение
    parts = split_long_text(full_text)
    
    bot_state = load_bot_state(user_id)
    main_id = bot_state.get('main_menu_message_id')
    if main_id:
        try: await context.bot.delete_message(chat_id=chat_id, message_id=main_id)
        except: pass
        bot_state['main_menu_message_id'] = None
        save_bot_state(user_id, bot_state)

    for i, part in enumerate(parts):
        reply_markup = get_main_keyboard(user_id) if i == len(parts) - 1 else None
        sent = await context.bot.send_message(
            chat_id=chat_id, 
            text=part, 
            parse_mode='MarkdownV2', 
            reply_markup=reply_markup
        )
        if i == len(parts) - 1:
            bot_state['main_menu_message_id'] = sent.message_id
            save_bot_state(user_id, bot_state)

    context.user_data.clear()
    return ConversationHandler.END

async def check_updates_free_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    
    config_name, version = parse_config_and_version(text)
    
    try: await context.bot.delete_message(chat_id=user_id, message_id=update.message.id)
    except: pass

    if config_name and version:
        saved_configs = load_configs(user_id)
        normalized_input = normalize_text(config_name)
        
        for cfg in saved_configs:
            if normalized_input in normalize_text(cfg['name']):
                config_name = cfg['name'] 
                break
        
        return await _perform_update_check(update, context, config_name, version)

    elif config_name and not version:
        context.user_data['selected_config'] = config_name
        
        msg_text = f'Выбрана конфигурация: *{escape_markdown(config_name)}*\nТеперь введите версию:'
        await send_or_edit_message(context, user_id, msg_text)
        return GET_CURRENT_VERSION

    elif not config_name and version:
        await context.bot.send_message(chat_id=user_id, text="Вы ввели версию, но я не понял, для какой она конфигурации. Пожалуйста, введите название и версию вместе.")
        return SELECT_CONFIG
    
    else:
        await context.bot.send_message(chat_id=user_id, text="Не удалось распознать данные. Выберите конфигурацию из списка или введите 'Название Версия'.")
        return SELECT_CONFIG


async def cancel_update_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await main_menu_callback(update, context)
    return ConversationHandler.END

async def reg_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        text='Пожалуйста, отправьте текст с данными арендаторов (можно скопировать сразу несколько блоков).',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Главное меню', callback_data='cancel_reg')]])
    )
    return GET_REG_TEXT

async def cancel_reg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await main_menu_callback(update, context)
    return ConversationHandler.END

async def process_registration_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    if context.user_data.get('awaiting_mapping_name'):
        return await save_mapping_name(update, context)

    try: await context.bot.delete_message(chat_id=user_id, message_id=update.message.id)
    except: pass

    if 'reg_buffer' not in context.user_data: context.user_data['reg_buffer'] = []
    context.user_data['reg_buffer'].append(text)
    
    if 'reg_timer_task' in context.user_data: context.user_data['reg_timer_task'].cancel()
    context.user_data['reg_timer_task'] = asyncio.create_task(finalize_registration_processing(update, context))
    return GET_REG_TEXT

async def finalize_registration_processing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: await asyncio.sleep(1.5)
    except asyncio.CancelledError: return

    full_text = "\n".join(context.user_data.get('reg_buffer', []))
    context.user_data['reg_buffer'] = []
    context.user_data.pop('reg_timer_task', None)
    
    user_id = update.effective_user.id
    parsed_data = parse_registration_text(full_text)
    
    if not parsed_data:
        await context.bot.send_message(chat_id=user_id, text='❌ Не удалось найти данные арендаторов в тексте. Проверьте формат.')
        await main_menu_callback(update, context)
        return

    mappings = load_mappings(user_id)
    unknown_nomenclatures = set()
    
    for item in parsed_data:
        raw = item['nom_raw']
        if raw not in mappings: unknown_nomenclatures.add(raw)
    
    context.user_data['reg_parsed_data'] = parsed_data
    context.user_data['reg_unknowns'] = list(unknown_nomenclatures)
    
    if unknown_nomenclatures: await ask_next_mapping(update, context)
    else: await send_registration_result(update, context)

async def ask_next_mapping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    unknowns = context.user_data.get('reg_unknowns', [])
    if not unknowns:
        context.user_data['awaiting_mapping_name'] = False
        await send_registration_result(update, context)
        return
    
    current_unknown = unknowns[0]
    msg_text = (f'⚠️ Обнаружена неизвестная номенклатура:\n\n`{escape_markdown(current_unknown)}`\n\n'
                f'Пожалуйста, введите правильное название для вывода \\(оно сохранится в словарь\\)\\.')
    
    chat_id = update.effective_chat.id if update.message else update.effective_user.id
    if update.callback_query: sent_msg = await update.callback_query.edit_message_text(text=msg_text, parse_mode='MarkdownV2')
    else: sent_msg = await context.bot.send_message(chat_id=chat_id, text=msg_text, parse_mode='MarkdownV2')
        
    context.user_data['reg_prompt_id'] = sent_msg.message_id
    context.user_data['awaiting_mapping_name'] = True

async def save_mapping_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    new_name = update.message.text.strip()
    
    try: await context.bot.delete_message(chat_id=user_id, message_id=update.message.id)
    except: pass
    
    prompt_id = context.user_data.get('reg_prompt_id')
    if prompt_id:
        try: await context.bot.delete_message(chat_id=user_id, message_id=prompt_id)
        except: pass
    
    unknowns = context.user_data.get('reg_unknowns', [])
    if unknowns:
        current_raw = unknowns.pop(0)
        context.user_data['reg_unknowns'] = unknowns
        mappings = load_mappings(user_id)
        mappings[current_raw] = new_name
        save_mappings(user_id, mappings)
        return await ask_next_mapping(update, context)
    return GET_REG_TEXT

async def send_registration_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await delete_extra_messages(context, user_id)
    
    parsed_data = context.user_data.get('reg_parsed_data', [])
    mappings = load_mappings(user_id)
    blocks = []
    
    for item in parsed_data:
        mapped_nom = mappings.get(item['nom_raw'], item['nom_raw'])
        block = (f"Арендатор: <code>{html.escape(item['name'])}</code>\n"
                 f"ИНН: <code>{html.escape(item['inn'])}</code>\n"
                 f"Номенклатура: <code>{html.escape(mapped_nom)}</code>\n"
                 f"Рег. номер: <code>{html.escape(item['reg_num'])}</code>")
        blocks.append(block)
    
    pages = []
    current_page_blocks = []
    current_length = 0
    header = '<b>📝 Данные для регистрации:</b>\n\n'
    current_length += len(header)

    for block in blocks:
        block_len = len(block) + 2
        if current_length + block_len > 4000:
            pages.append(current_page_blocks)
            current_page_blocks = []
            current_length = 0
        current_page_blocks.append(block)
        current_length += block_len
    if current_page_blocks: pages.append(current_page_blocks)

    bot_state = load_bot_state(user_id)
    old_menu_id = bot_state.get('main_menu_message_id')
    if old_menu_id:
        try: await context.bot.delete_message(chat_id=user_id, message_id=old_menu_id)
        except: pass

    finish_markup = InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Завершить', callback_data='cancel_reg')]])

    new_extra_ids = []
    for i, page_blocks in enumerate(pages):
        text_content = "\n\n".join(page_blocks)
        if i == 0: text_content = header + text_content
        
        if i == len(pages) - 1:
            sent_msg = await context.bot.send_message(
                chat_id=user_id, 
                text=text_content, 
                parse_mode='HTML', 
                reply_markup=finish_markup
            )
            bot_state['main_menu_message_id'] = sent_msg.message_id
        else:
            sent_msg = await context.bot.send_message(chat_id=user_id, text=text_content, parse_mode='HTML')
            new_extra_ids.append(sent_msg.message_id)
    
    bot_state['extra_message_ids'] = new_extra_ids
    save_bot_state(user_id, bot_state)

async def manage_mappings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    if query: await query.answer()
    
    mappings = load_mappings(user_id)
    
    if not mappings:
        empty_text = "📂 *Словарь замен пуст*\\.\n\nЗдесь будут появляться пары, которые вы сохранили при регистрации арендаторов\\."
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Назад', callback_data='manage_list_menu')]])
        
        if query and query.message:
            await query.edit_message_text(text=empty_text, parse_mode='MarkdownV2', reply_markup=keyboard)
        else:
            await send_or_edit_message(context, user_id, empty_text, keyboard)
        return

    text_lines = [r"📂 *Словарь замен:*", r""]
    keyboard_buttons = []
    current_row = []
    
    for i, (raw, fixed) in enumerate(mappings.items(), 1):
        line = f"*{i}\\.* `{escape_markdown(raw)}`\n   ⬇️ `{escape_markdown(fixed)}`"
        text_lines.append(line)
        
        raw_hash = hashlib.md5(raw.encode()).hexdigest()
        btn = InlineKeyboardButton(f"🗑 {i}", callback_data=f'del_map_{raw_hash}')
        current_row.append(btn)
        
        if len(current_row) == 5:
            keyboard_buttons.append(current_row)
            current_row = []
            
    if current_row:
        keyboard_buttons.append(current_row)
        
    keyboard_buttons.append([InlineKeyboardButton('⬅️ Назад', callback_data='manage_list_menu')])
    
    full_text = "\n\n".join(text_lines)
    
    if len(full_text) > 4000:
        full_text = full_text[:4000] + "\n\n_\\.\\.\\. (список слишком длинный, удалите часть записей)_"

    markup = InlineKeyboardMarkup(keyboard_buttons)
    
    if query and query.message:
        try:
            await query.edit_message_text(text=full_text, parse_mode='MarkdownV2', reply_markup=markup)
        except BadRequest:
            pass
    else:
        await send_or_edit_message(context, user_id, full_text, markup)

async def delete_mapping_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    await query.answer()
    target_hash = query.data.split('_')[2]
    mappings = load_mappings(user_id)
    key_to_delete = None
    for key in mappings.keys():
        if hashlib.md5(key.encode()).hexdigest() == target_hash:
            key_to_delete = key; break
    if key_to_delete:
        del mappings[key_to_delete]
        save_mappings(user_id, mappings)
    await manage_mappings_menu(update, context)

async def delete_stray_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: await update.message.delete()
    except: pass
    
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 *Справка по боту*\n\n"
        "Этот бот помогает отслеживать обновления конфигураций 1С\\.\n\n"
        "*Основные команды:*\n"
        "/start — Запуск и главное меню\n"
        "/help — Эта справка\n\n"
        "*Функции:*\n"
        "🔄 *Проверить версии* — Сверяет ваши конфигурации с сайтом releases\\.1c\\.ru\n"
        "📈 *Кол\\-во обновлений* — Рассчитывает цепочку обновлений \\(cfu\\) от вашей версии до актуальной\n"
        "📝 *Регистрация* — Форматирует данные арендаторов для подачи заявки\n"
        "⚙️ *Управление* — Добавление и удаление конфигураций из списка отслеживания\n\n"
        "_Бот проверяет обновления автоматически раз в сутки\\._"
    )
    await send_or_edit_message(context, update.effective_chat.id, text, get_main_keyboard(update.effective_user.id))
    
async def cleanup_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = (
        r"🧹 *Фильтр баз \(saas\_2641\)*" "\n\n"
        r"Пришлите список баз \(текстом\)\." "\n"
        r"Я оставлю только те, что находятся на кластере `saas_2641` "
        r"и имеют имя формата `Буквы_Цифры`\."
    )
    
    await query.edit_message_text(
        text=text,
        parse_mode='MarkdownV2',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Главное меню', callback_data='cancel_cleanup')]])
    )
    return GET_CLEANUP_TEXT

async def process_cleanup_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    try: await context.bot.delete_message(chat_id=user_id, message_id=update.message.id)
    except: pass
    
    if 'cleanup_buffer' not in context.user_data: 
        context.user_data['cleanup_buffer'] = []
    
    context.user_data['cleanup_buffer'].append(text)
    
    if 'cleanup_timer_task' in context.user_data: 
        context.user_data['cleanup_timer_task'].cancel()
    
    context.user_data['cleanup_timer_task'] = asyncio.create_task(finalize_cleanup_processing(update, context))
    return GET_CLEANUP_TEXT

async def finalize_cleanup_processing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: await asyncio.sleep(1.5)
    except asyncio.CancelledError: return

    full_text = "\n".join(context.user_data.get('cleanup_buffer', []))
    context.user_data['cleanup_buffer'] = []
    context.user_data.pop('cleanup_timer_task', None)
    
    user_id = update.effective_user.id
    await delete_extra_messages(context, user_id)

    ignore_list = load_cleanup_ignore(user_id)
    found_bases = parse_saas_bases(full_text, ignore_list)
    
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Завершить', callback_data='cancel_cleanup')]])

    if not found_bases:
        text = "⚠️ В тексте не найдено подходящих баз \\(или все они в списке исключений\\)\\."
        await send_or_edit_message(context, user_id, text, keyboard)
        return

    count = len(found_bases)
    all_candidates = parse_saas_bases(full_text, []) 
    ignored_count = len(all_candidates) - count
    ignore_msg = f" \\(скрыто: {ignored_count}\\)" if ignored_count > 0 else ""

    header = (
        f"✅ Найдено баз: *{count}*{ignore_msg}\n"
        f"Кластер: `saas_2641`\n\n"
    )

    lines = [f"`{base}`" for base in found_bases]
    
    pages = []
    current_page_lines = []
    current_length = len(header)
    
    for line in lines:
        if current_length + len(line) + 1 > 3800: 
            pages.append(current_page_lines)
            current_page_lines = []
            current_length = 0
        current_page_lines.append(line)
        current_length += len(line) + 1
        
    if current_page_lines:
        pages.append(current_page_lines)

    bot_state = load_bot_state(user_id)
    old_menu_id = bot_state.get('main_menu_message_id')
    if old_menu_id:
        try: await context.bot.delete_message(chat_id=user_id, message_id=old_menu_id)
        except: pass

    new_extra_ids = []
    
    for i, page_lines in enumerate(pages):
        page_text = "\n".join(page_lines)
        if i == 0: full_msg_text = header + page_text
        else: full_msg_text = page_text

        if i == len(pages) - 1:
            sent_msg = await context.bot.send_message(
                chat_id=user_id, 
                text=full_msg_text, 
                parse_mode='MarkdownV2', 
                reply_markup=keyboard
            )
            bot_state['main_menu_message_id'] = sent_msg.message_id
        else:
            sent_msg = await context.bot.send_message(
                chat_id=user_id, 
                text=full_msg_text, 
                parse_mode='MarkdownV2'
            )
            new_extra_ids.append(sent_msg.message_id)

    bot_state['extra_message_ids'] = new_extra_ids
    save_bot_state(user_id, bot_state)

    
async def manage_ignore_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    await query.answer()
    
    ignore_list = load_cleanup_ignore(user_id)
    
    text = (
        r"🚫 *Базы\-исключения \(SaaS\)*" "\n\n"
        r"Эти базы будут автоматически удаляться из отчета при фильтрации\." "\n"
        r"Нажмите на базу, чтобы удалить её из списка исключений\."
    )
    
    if not query.message:
         await send_or_edit_message(context, user_id, text, get_ignore_menu_keyboard(ignore_list))
    else:
         await query.edit_message_text(text=text, parse_mode='MarkdownV2', reply_markup=get_ignore_menu_keyboard(ignore_list))

async def add_ignore_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    msg = await query.edit_message_text(
        text="Пришлите название базы, которую нужно игнорировать \\(например, `UNF_12345`\\):",
        parse_mode='MarkdownV2',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Отмена', callback_data='cancel_add_ignore')]])
    )
    context.user_data['ignore_prompt_id'] = msg.message_id
    return GET_IGNORE_NAME

async def add_ignore_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    base_name = update.message.text.strip()
    
    try: await context.bot.delete_message(chat_id=user_id, message_id=update.message.id)
    except: pass
    
    if 'ignore_prompt_id' in context.user_data:
        try: await context.bot.delete_message(chat_id=user_id, message_id=context.user_data['ignore_prompt_id'])
        except: pass
        
    ignore_list = load_cleanup_ignore(user_id)
    sent_msg = None
    
    if base_name not in ignore_list:
        ignore_list.append(base_name)
        ignore_list.sort()
        save_cleanup_ignore(user_id, ignore_list)
        sent_msg = await context.bot.send_message(
            chat_id=user_id, 
            text=f"✅ База `{base_name}` добавлена в исключения\\.", 
            parse_mode='MarkdownV2'
        )
    else:
        sent_msg = await context.bot.send_message(
            chat_id=user_id, 
            text=f"ℹ️ База `{base_name}` уже есть в списке\\.", 
            parse_mode='MarkdownV2'
        )
    
    if sent_msg:
        bot_state = load_bot_state(user_id)
        if 'extra_message_ids' not in bot_state:
            bot_state['extra_message_ids'] = []
        bot_state['extra_message_ids'].append(sent_msg.message_id)
        save_bot_state(user_id, bot_state)
    
    await send_or_edit_message(context, user_id, r"🚫 *Базы\-исключения \(SaaS\)*", get_ignore_menu_keyboard(ignore_list))
    return ConversationHandler.END

async def delete_ignore_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    await query.answer()
    
    target_hash = query.data.split('_')[2]
    ignore_list = load_cleanup_ignore(user_id)
    
    new_list = [name for name in ignore_list if hashlib.md5(name.encode()).hexdigest() != target_hash]
    
    if len(new_list) != len(ignore_list):
        save_cleanup_ignore(user_id, new_list)
    
    await manage_ignore_menu(update, context)

async def cancel_add_ignore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await manage_ignore_menu(update, context)
    return ConversationHandler.END
    
async def cancel_cleanup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await main_menu_callback(update, context)
    return ConversationHandler.END
    
async def handle_config_candidate_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    await query.answer()
    
    data = query.data
    selected_name = ""
    
    if data == 'cand_manual':
        selected_name = context.user_data.get('manual_name_input', 'Unkown')
    elif data.startswith('cand_sel_'):
        idx = int(data.split('_')[2])
        candidates = context.user_data.get('search_candidates', [])
        if 0 <= idx < len(candidates):
            selected_name = candidates[idx]
        else:
            await query.edit_message_text("❌ Ошибка выбора. Попробуйте снова.")
            return ConversationHandler.END

    context.user_data['new_config_name'] = selected_name
    
    # Чистим временные данные
    context.user_data.pop('search_candidates', None)
    context.user_data.pop('manual_name_input', None)

    return await _ask_config_type(context, user_id, selected_name)
    
async def _ask_config_type(context, user_id, config_name):
    """Вспомогательная функция для перехода к выбору типа (Latest/LTS/Specific)"""
    bot_state = load_bot_state(user_id)
    # Удаляем старое сообщение с выбором, если оно было
    if 'prompt_message_id' in context.user_data:
        try: await context.bot.delete_message(chat_id=user_id, message_id=context.user_data['prompt_message_id'])
        except: pass
        
    msg = await context.bot.send_message(
        chat_id=user_id,
        text=f'Выбрана конфигурация: *{escape_markdown(config_name)}*\n\nКакую версию отслеживать?',
        parse_mode='MarkdownV2',
        reply_markup=get_type_selection_keyboard()
    )
    context.user_data['prompt_message_id'] = msg.message_id
    return GET_CONFIG_TYPE