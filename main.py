import logging
import datetime
import html
import json
import traceback
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    ConversationHandler, MessageHandler, filters, ContextTypes
)

from bot.config import (
    setup_logging, TELEGRAM_TOKEN, ADMIN_USER_ID, 
    TIMEZONE, SCHEDULE_HOUR, SCHEDULE_MINUTE, USER_DATA_DIR,
    GET_CONFIG_NAME, GET_CONFIG_TYPE, GET_SPECIFIC_BRANCH, SELECT_CONFIG, 
    GET_MANUAL_CONFIG, GET_CURRENT_VERSION, GET_REG_TEXT, GET_CLEANUP_TEXT, GET_IGNORE_NAME
)
from bot import handlers

setup_logging()
logger = logging.getLogger(__name__)

# --- ГЛОБАЛЬНЫЙ ОБРАБОТЧИК ОШИБОК ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Логирует ошибки, возникающие при обновлении."""
    logger.error("Exception while handling an update:", exc_info=context.error)

    if update and isinstance(update, Update) and ADMIN_USER_ID:
        tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
        tb_string = "".join(tb_list)
        
        message = (
            f"An exception was raised while handling an update\n"
            f"<pre>update = {html.escape(json.dumps(update.to_dict(), indent=2, ensure_ascii=False))}"
            "</pre>\n\n"
            f"<pre>{html.escape(tb_string)}</pre>"
        )
        
        if len(message) > 4000:
            message = message[:4000] + "... (truncated)"

        try:
            await context.bot.send_message(chat_id=ADMIN_USER_ID, text=message, parse_mode=ParseMode.HTML)
        except Exception:
            pass

def main():
    USER_DATA_DIR.mkdir(exist_ok=True, parents=True)
    
    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .read_timeout(60)
        .write_timeout(60)
        .connect_timeout(60)
        .build()
    )
    job_queue = application.job_queue

    application.add_error_handler(error_handler)

    try:
        tz = ZoneInfo(TIMEZONE) 
        target_time = datetime.time(hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE, tzinfo=tz)
        job_queue.run_daily(
            handlers.daily_version_check, 
            target_time, 
            job_kwargs={'misfire_grace_time': 60} 
        )
        
        now = datetime.datetime.now(tz)
        next_run = now.replace(hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE, second=0, microsecond=0)
        if next_run <= now: next_run += datetime.timedelta(days=1)
        
        logger.info(f"⏰ СЕЙЧАС: {now.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"📅 ЗАПУСК ТАЙМЕРА: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
    except Exception as e:
        logger.error(f"Ошибка таймера: {e}")

    # --- HANDLERS ---
    reg_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handlers.reg_start, pattern='^reg_start$')],
        states={
            GET_REG_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.process_registration_text),
                CallbackQueryHandler(handlers.reg_start, pattern='^reg_start$'),
                CallbackQueryHandler(handlers.get_versions_callback, pattern='^get_versions$')
            ]
        },
        fallbacks=[
            CallbackQueryHandler(handlers.cancel_reg, pattern='^cancel_reg$'),
            CallbackQueryHandler(handlers.main_menu_callback, pattern='^main_menu$')
        ],
        per_message=False, allow_reentry=True
    )

    add_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handlers.add_config_start, pattern='^add_config_start$')],
        states={
            GET_CONFIG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_new_config_name)],
            GET_CONFIG_TYPE: [CallbackQueryHandler(handlers.handle_new_config_type, pattern='^type_')],
            GET_SPECIFIC_BRANCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_specific_branch_input)]
        },
        fallbacks=[CallbackQueryHandler(handlers.main_menu_callback, pattern='^main_menu$')]
    )

    update_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handlers.check_updates_start, pattern='^check_updates_start$')],
        states={
            SELECT_CONFIG: [
                CallbackQueryHandler(handlers.check_updates_select_config, pattern='^select_config_'),
                CallbackQueryHandler(handlers.check_updates_manual_config_prompt, pattern='^manual_config$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.check_updates_free_text_input) 
            ],
            GET_MANUAL_CONFIG: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.check_updates_handle_manual_config)],
            GET_CURRENT_VERSION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.check_updates_calculate)]
        },
        fallbacks=[CallbackQueryHandler(handlers.cancel_update_check, pattern='^cancel_update_check$')]
    )
    
    cleanup_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handlers.cleanup_start, pattern='^cleanup_start$')],
        states={
            GET_CLEANUP_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.process_cleanup_text)
            ]
        },
        fallbacks=[
            CallbackQueryHandler(handlers.cancel_cleanup, pattern='^cancel_cleanup$'),
            CallbackQueryHandler(handlers.main_menu_callback, pattern='^main_menu$')
        ]
    )
    
    add_ignore_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handlers.add_ignore_start, pattern='^add_ignore_start$')],
        states={
            GET_IGNORE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.add_ignore_save)]
        },
        fallbacks=[CallbackQueryHandler(handlers.cancel_add_ignore, pattern='^cancel_add_ignore$')]
    )

    application.add_handler(CommandHandler('start', handlers.start))
    application.add_handler(CommandHandler('help', handlers.help_command))
    application.add_handler(add_handler)
    application.add_handler(update_handler)
    application.add_handler(reg_handler)
    application.add_handler(cleanup_handler)
    application.add_handler(add_ignore_handler)
    
    application.add_handler(CallbackQueryHandler(handlers.get_versions_callback, pattern='^get_versions$'))
    application.add_handler(CallbackQueryHandler(handlers.main_menu_callback, pattern='^main_menu$'))
    application.add_handler(CallbackQueryHandler(handlers.acknowledge_all_callback, pattern='^ack_all$'))
    application.add_handler(CallbackQueryHandler(handlers.manage_list_menu_callback, pattern='^manage_list_menu$'))
    application.add_handler(CallbackQueryHandler(handlers.remove_config_menu, pattern='^remove_config_menu$'))
    application.add_handler(CallbackQueryHandler(handlers.remove_config_callback, pattern='^remove_\\d+$'))
    
    
    application.add_handler(CallbackQueryHandler(handlers.change_type_menu, pattern='^change_type_menu$'))
    application.add_handler(CallbackQueryHandler(handlers.change_type_select_callback, pattern='^chtype_sel_\\d+$'))
    
    application.add_handler(CallbackQueryHandler(handlers.manage_ignore_menu, pattern='^manage_ignore_menu$'))
    application.add_handler(CallbackQueryHandler(handlers.delete_ignore_callback, pattern='^del_ign_'))
    
    # ИСПРАВЛЕНО: Добавлены specific и specific_dp в паттерн
    application.add_handler(CallbackQueryHandler(handlers.change_type_save_callback, pattern='^type_(latest|dp|both|specific|specific_dp)$'))
    
    application.add_handler(CallbackQueryHandler(handlers.reorder_config_menu, pattern='^reorder_config_menu$'))
    application.add_handler(CallbackQueryHandler(handlers.move_config_callback, pattern='^(move_up|move_down)_\\d+$'))
    
    application.add_handler(CallbackQueryHandler(handlers.manage_mappings_menu, pattern='^manage_mappings_menu$'))
    application.add_handler(CallbackQueryHandler(handlers.delete_mapping_callback, pattern='^del_map_'))
    
    application.add_handler(CallbackQueryHandler(handlers.noop_callback, pattern='^noop$'))
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.delete_stray_text))

    logger.info('Бот запущен...')
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()