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
        is_new = config_obj.get('is_new', False)
        
        # Определяем общий значок статуса для меню
        status_mark = "⚡️" if is_new else "✅"
        
        display_lines = []
        
        if not last_version or not last_date:
            display_lines.append('   └ Данных пока нет ⏳')
        else:
            # Проверяем, есть ли разделитель (режим Both)
            if '|' in last_version:
                ver_parts = last_version.split('|')
                date_parts = last_date.split('|') if '|' in last_date else [last_date, '-']
                
                v_new = ver_parts[0]
                d_new = date_parts[0]
                v_dp = ver_parts[1] if len(ver_parts) > 1 else "Нет"
                d_dp = date_parts[1] if len(date_parts) > 1 else "-"
                
                display_lines.append(f"🔥 `{escape_markdown(v_new)}` • `{escape_markdown(d_new)}` {status_mark}")
                display_lines.append(f"🛡 `{escape_markdown(v_dp)}` • `{escape_markdown(d_dp)}` {status_mark}")
            else:
                # Обычный режим
                icon = "🛡" if track_type == 'dp' else "🔥"
                display_lines.append(f"{icon} `{escape_markdown(last_version)}` • `{escape_markdown(last_date)}` {status_mark}")

        # Заголовок БЕЗ иконок
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
    await send_or_edit_message(context, user_id, full_text, get_main_keyboard(user_id, configs))

async def daily_version_check(context: ContextTypes.DEFAULT_TYPE):
    logger.info('ЗАПУСК ежедневной проверки...')
    if not USER_DATA_DIR.exists():
        return

    session, error = await asyncio.to_thread(service_1c.login_to_1c)
    if error or not session:
        logger.error(f"Ежедневная проверка пропущена: {error}")
        return

    soup, soup_error = await asyncio.to_thread(service_1c.get_releases_soup, session)
    if soup_error or not soup:
        logger.error(f"Ежедневная проверка пропущена (ошибка получения таблицы): {soup_error}")
        return

    user_ids = [int(p.name) for p in USER_DATA_DIR.iterdir() if p.is_dir() and p.name.isdigit()]
    
    for user_id in user_ids:
        try:
            user_configs = load_configs(user_id)
            if not user_configs: continue
            
            result_text, updated_configs = service_1c.parse_versions_from_soup(soup, user_configs)
            save_configs(user_id, updated_configs)
            
            full_text = escape_markdown('🗓️ *Ежедневная проверка:*\n\n') + result_text
            await send_or_edit_message(context, user_id, full_text, get_main_keyboard(user_id, updated_configs))
            
        except Forbidden:
            logger.warning(f'Пользователь {user_id} заблокировал бота. Пропускаем.')
        except Exception as e:
            logger.error(f'Ошибка проверки для {user_id}: {e}')

async def get_versions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if update.callback_query:
        await update.callback_query.answer()
        try: await update.callback_query.message.delete()
        except: pass
        
        msg = await context.bot.send_message(chat_id=user_id, text='⏳ Идет проверка, пожалуйста, подождите...')
        bot_state = load_bot_state(user_id)
        bot_state['main_menu_message_id'] = msg.message_id
        save_bot_state(user_id, bot_state)
    
    session, error = await asyncio.to_thread(service_1c.login_to_1c)
    if error:
        await send_or_edit_message(context, user_id, f"Ошибка: {escape_markdown(error)}", get_main_keyboard(user_id))
        return ConversationHandler.END

    soup, soup_error = await asyncio.to_thread(service_1c.get_releases_soup, session)
    if soup_error:
        await send_or_edit_message(context, user_id, f"Ошибка: {escape_markdown(soup_error)}", get_main_keyboard(user_id))
        return ConversationHandler.END

    header = escape_markdown('🔍 *Результаты проверки:*\n\n')
    result_text, updated_configs = service_1c.parse_versions_from_soup(soup, load_configs(user_id))
    save_configs(user_id, updated_configs)
    
    full_text = header + result_text
    await send_or_edit_message(context, user_id, full_text, get_main_keyboard(user_id, updated_configs))
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
    await send_or_edit_message(context, user_id, 'Управление списком конфигураций:', get_manage_keyboard())

async def add_config_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    prompt_message = await query.edit_message_text(text='Пришлите мне полное название конфигурации для отслеживания.')
    context.user_data['prompt_message_id'] = prompt_message.message_id
    return GET_CONFIG_NAME

async def handle_new_config_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    config_name = update.message.text
    context.user_data['new_config_name'] = config_name

    try: await context.bot.delete_message(chat_id=user_id, message_id=update.message.message_id)
    except: pass
    
    if 'prompt_message_id' in context.user_data:
        try: await context.bot.delete_message(chat_id=user_id, message_id=context.user_data['prompt_message_id'])
        except: pass

    msg = await context.bot.send_message(
        chat_id=user_id,
        text=f'Вы ввели: *{escape_markdown(config_name)}*\n\nКакую версию отслеживать?',
        parse_mode='MarkdownV2',
        reply_markup=get_type_selection_keyboard()
    )
    context.user_data['prompt_message_id'] = msg.message_id
    return GET_CONFIG_TYPE

async def handle_new_config_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    await query.answer()
    
    # --- ИСПРАВЛЕНИЕ: Удаляем сообщение с кнопками выбора типа ---
    try:
        await query.message.delete()
    except Exception:
        pass
    # -------------------------------------------------------------
    
    track_type = query.data.split('_')[1]
    config_name = context.user_data.get('new_config_name')
    
    if not config_name:
        # Если имя потерялось, отправляем новое сообщение, так как старое мы только что удалили
        await context.bot.send_message(chat_id=user_id, text="Ошибка: имя конфигурации потеряно. Попробуйте снова.")
        return ConversationHandler.END

    configs = load_configs(user_id)
    configs.append({
        'name': config_name,
        'track_type': track_type,
        'last_version': '',
        'last_date': '',
        'is_new': False
    })
    save_configs(user_id, configs)
    
    context.user_data.pop('new_config_name', None)
    context.user_data.pop('prompt_message_id', None)

    type_desc = {'latest': 'Самая новая', 'dp': 'Только ДП', 'both': 'ДП + Новая'}.get(track_type, track_type)
    
    # Экранируем текст, чтобы не было ошибки с плюсом (+)
    success_text = f'✅ Конфигурация *{escape_markdown(config_name)}* добавлена\\!\nТип: {escape_markdown(type_desc)}'
    
    bot_state = load_bot_state(user_id)
    main_menu_id = bot_state.get('main_menu_message_id')
    
    # Обновляем главное меню
    if main_menu_id:
        try:
            await context.bot.edit_message_text(
                chat_id=user_id, 
                message_id=main_menu_id, 
                text=success_text, 
                parse_mode='MarkdownV2', 
                reply_markup=get_main_keyboard(user_id, configs)
            )
        except Exception:
            # Если не получилось отредактировать (например, старое меню слишком далеко), шлем новое
            await send_or_edit_message(context, user_id, success_text, get_main_keyboard(user_id, configs))
    else:
        await send_or_edit_message(context, user_id, success_text, get_main_keyboard(user_id, configs))

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
        icon = {'latest': '🔥', 'dp': '🛡', 'both': '👀'}.get(c.get('track_type'), '🔥')
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
    new_type = query.data.split('_')[1]
    index = context.user_data.get('edit_config_index')
    
    if index is None:
        await change_type_menu(update, context)
        return

    configs = load_configs(user_id)
    if 0 <= index < len(configs):
        configs[index]['track_type'] = new_type
        configs[index]['last_version'] = '' 
        configs[index]['last_date'] = ''
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
    keyboard.append([InlineKeyboardButton('⬅️ Отмена', callback_data='cancel_update_check')])
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
    await query.edit_message_text(text=f'Выбрана конфигурация: *{escape_markdown(selected_config_name)}*\n\n' + 'Теперь, пожалуйста, пришлите номер вашей текущей версии \\(например, `3\\.0\\.123\\.45`\\)\\.', parse_mode='MarkdownV2')
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
    text = f'Выбрана конфигурация: *{escape_markdown(config_name)}*\n\nТеперь пришлите номер вашей текущей версии \\(например, `3\\.0\\.123\\.45`\\)\\.'
    
    if main_menu_id:
        try: await context.bot.edit_message_text(chat_id=user_id, message_id=main_menu_id, text=text, parse_mode='MarkdownV2')
        except: await context.bot.send_message(chat_id=user_id, text=text, parse_mode='MarkdownV2')
    return GET_CURRENT_VERSION

async def check_updates_calculate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_version = update.message.text.strip()
    config_name = context.user_data.get('selected_config')
    
    if not config_name:
        await update.message.reply_text('Произошла ошибка: конфигурация не была выбрана. Попробуйте снова.')
        return ConversationHandler.END
    
    try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.id)
    except: pass
        
    session, error = await asyncio.to_thread(service_1c.login_to_1c)
    if error:
        await send_or_edit_message(context, update.effective_chat.id, text=error, reply_markup=get_main_keyboard(update.effective_user.id))
        context.user_data.clear()
        return ConversationHandler.END
    
    targets, error = await asyncio.to_thread(service_1c.get_target_versions, session, config_name)
    if error:
        await send_or_edit_message(context, update.effective_chat.id, text=error, reply_markup=get_main_keyboard(update.effective_user.id))
        context.user_data.clear()
        return ConversationHandler.END
    
    dp_target = targets['dp']
    non_dp_target = targets['non_dp']
    status_text = f'✅ Версия на ДП: `{escape_markdown(dp_target)}`'
    if dp_target != non_dp_target:
        status_text += f'\n✅ Версия не на ДП: `{escape_markdown(non_dp_target)}`'
        
    await send_or_edit_message(context, update.effective_chat.id, text=f'{status_text}\n\n⏳ Рассчитываю путь обновления от `{escape_markdown(user_version)}`\\. Это может занять некоторое время\\.\\.\\.', reply_markup=None)
    
    result_text = await asyncio.to_thread(service_1c.find_update_path, session, config_name, user_version, dp_target, non_dp_target)
    header = escape_markdown('📊 *Результат подсчета обновлений:*\n\n')
    full_text = header + result_text
    
    await send_or_edit_message(context, update.effective_chat.id, text=full_text, reply_markup=get_main_keyboard(update.effective_user.id))
    context.user_data.clear()
    return ConversationHandler.END

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
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Отмена', callback_data='cancel_reg')]])
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

    new_extra_ids = []
    for i, page_blocks in enumerate(pages):
        text_content = "\n\n".join(page_blocks)
        if i == 0: text_content = header + text_content
        
        if i == len(pages) - 1:
            sent_msg = await context.bot.send_message(chat_id=user_id, text=text_content, parse_mode='HTML', reply_markup=get_main_keyboard(user_id))
            bot_state['main_menu_message_id'] = sent_msg.message_id
        else:
            sent_msg = await context.bot.send_message(chat_id=user_id, text=text_content, parse_mode='HTML')
            new_extra_ids.append(sent_msg.message_id)
    
    bot_state['extra_message_ids'] = new_extra_ids
    save_bot_state(user_id, bot_state)

async def manage_mappings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    await query.answer()
    mappings = load_mappings(user_id)
    if not mappings:
        try: await query.edit_message_text(text='Словарь замен пуст.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Назад', callback_data='manage_list_menu')]]))
        except BadRequest: pass
        return

    keyboard = []
    for raw, fixed in mappings.items():
        btn_text = f"❌ {raw[:15]}.. -> {fixed[:15]}.."
        raw_hash = hashlib.md5(raw.encode()).hexdigest()
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f'del_map_{raw_hash}')])
    keyboard.append([InlineKeyboardButton('⬅️ Назад', callback_data='manage_list_menu')])
    try: await query.edit_message_text(text='Нажмите на замену, чтобы удалить её из словаря:', reply_markup=InlineKeyboardMarkup(keyboard))
    except BadRequest: pass

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
    
# 1. Добавьте импорт новой функции
from .utils import escape_markdown, normalize_text, version_tuple, is_valid_version

# 2. Добавьте функцию help_command (где-то в начале обработчиков)
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 *Справка по боту*\n\n"
        "Этот бот помогает отслеживать обновления конфигураций 1С.\n\n"
        "*Основные команды:*\n"
        "/start — Запуск и главное меню\n"
        "/help — Эта справка\n\n"
        "*Функции:*\n"
        "🔄 *Проверить версии* — Сверяет ваши конфигурации с сайтом releases.1c.ru\n"
        "📈 *Кол-во обновлений* — Рассчитывает цепочку обновлений (cfu) от вашей версии до актуальной\n"
        "📝 *Регистрация* — Форматирует данные арендаторов для подачи заявки\n"
        "⚙️ *Управление* — Добавление и удаление конфигураций из списка отслеживания\n\n"
        "_Бот проверяет обновления автоматически раз в сутки._"
    )
    await send_or_edit_message(context, update.effective_chat.id, escape_markdown(text), get_main_keyboard(update.effective_user.id))

# 3. Обновите функцию check_updates_calculate
async def check_updates_calculate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_version = update.message.text.strip()
    config_name = context.user_data.get('selected_config')
    chat_id = update.effective_chat.id
    
    # 1. Валидация (если добавили ранее)
    # if not is_valid_version(user_version): ...

    if not config_name:
        await update.message.reply_text('Произошла ошибка: конфигурация не была выбрана. Попробуйте снова.')
        return ConversationHandler.END
    
    # 2. Удаляем сообщение пользователя
    try: 
        await context.bot.delete_message(chat_id=chat_id, message_id=update.message.id)
    except: 
        pass
        
    # --- ИСПРАВЛЕНИЕ: Сразу даем обратную связь ---
    # Сообщаем, что процесс пошел, ДО начала сетевых запросов
    await send_or_edit_message(
        context, 
        chat_id, 
        text='⏳ *Подключаюсь к порталу 1С\\.\\.\\.*', 
        reply_markup=None
    )
    await context.bot.send_chat_action(chat_id=chat_id, action='typing')
    # ----------------------------------------------

    # 3. Авторизация (может занять время)
    session, error = await asyncio.to_thread(service_1c.login_to_1c)
    if error:
        await send_or_edit_message(context, chat_id, text=f"❌ {escape_markdown(error)}", reply_markup=get_main_keyboard(update.effective_user.id))
        context.user_data.clear()
        return ConversationHandler.END
    
    # --- Обновляем статус ---
    await send_or_edit_message(
        context, 
        chat_id, 
        text=f'⏳ *Ищу актуальные версии для {escape_markdown(config_name)}\\.\\.\\.*', 
        reply_markup=None
    )
    # ------------------------
    
    # 4. Получение целевых версий
    targets, error = await asyncio.to_thread(service_1c.get_target_versions, session, config_name)
    if error:
        await send_or_edit_message(context, chat_id, text=f"❌ {error}", reply_markup=get_main_keyboard(update.effective_user.id))
        context.user_data.clear()
        return ConversationHandler.END
    
    dp_target = targets['dp']
    non_dp_target = targets['non_dp']
    
    status_text = f'✅ Версия на ДП: `{escape_markdown(dp_target)}`'
    if dp_target != non_dp_target:
        status_text += f'\n✅ Версия не на ДП: `{escape_markdown(non_dp_target)}`'
        
    # --- Финальный статус перед долгим расчетом ---
    await send_or_edit_message(
        context, 
        chat_id, 
        text=f'{status_text}\n\n⏳ *Рассчитываю цепочку обновлений от* `{escape_markdown(user_version)}`*\\.\\.\\.*', 
        reply_markup=None
    )
    # ----------------------------------------------
    
    # 5. Расчет пути (самая долгая операция)
    result_text = await asyncio.to_thread(service_1c.find_update_path, session, config_name, user_version, dp_target, non_dp_target)
    
    header = escape_markdown('📊 *Результат подсчета обновлений:*\n\n')
    full_text = header + result_text
    
    await send_or_edit_message(context, chat_id, text=full_text, reply_markup=get_main_keyboard(update.effective_user.id))
    context.user_data.clear()
    return ConversationHandler.END