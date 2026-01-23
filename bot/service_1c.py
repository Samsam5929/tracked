import requests
import re
import asyncio
from bs4 import BeautifulSoup, NavigableString
from datetime import datetime
from .config import *
from .storage import *
from .utils import normalize_text, escape_markdown, version_tuple
import logging
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

# --- SESSION MANAGEMENT ---
_session_store = {
    'session': None,
    'created_at': None
}

def get_cached_session():
    """
    Возвращает активную сессию. Если её нет или она протухла — создает новую.
    """
    global _session_store
    
    # Если сессия есть и ей меньше 30 минут — возвращаем её
    if _session_store['session'] and _session_store['created_at']:
        delta = datetime.now() - _session_store['created_at']
        if delta.total_seconds() < 1800: # 30 минут жизни
            return _session_store['session'], None

    # Иначе создаем новую
    logger.info("♻️ Сессия устарела или отсутствует. Выполняем вход...")
    if _session_store['session']:
        try: _session_store['session'].close()
        except: pass
    
    session, error = _perform_login()
    
    if session:
        _session_store['session'] = session
        _session_store['created_at'] = datetime.now()
    
    return session, error

def _perform_login():
    """Внутренняя функция авторизации с указанием сервиса (Releases)"""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7'
    })
    
    try:
        # 1. Сначала идем на страницу логина с параметром service
        target_service = 'https://releases.1c.ru/public/security_check'
        init_url = 'https://login.1c.ru/login'
        params = {'service': target_service}
        
        r = session.get(init_url, params=params, timeout=30)
        r.raise_for_status()
        
        soup = BeautifulSoup(r.content, 'html.parser')
        
        execution = soup.find('input', {'name': 'execution'})
        if not execution:
            if 'releases.1c.ru' in r.url:
                return session, None
            return None, 'Ошибка парсинга: Не найдено поле execution на странице входа.'
            
        login_form = soup.find('form', id='loginForm')
        action_url = login_form.get('action') if login_form else '/login'
        
        if action_url.startswith('/'):
            post_url = f'https://login.1c.ru{action_url}'
        else:
            post_url = action_url

        payload = {
            'username': LOGIN_1C, 
            'password': PASSWORD_1C, 
            'execution': execution.get('value'), 
            '_eventId': 'submit', 
            'geolocation': '',
            'submit': 'Login'
        }
        
        post = session.post(post_url, data=payload, allow_redirects=True, timeout=45)
        post.raise_for_status()
        
        if 'Неверный логин или пароль' in post.text:
            return None, 'Ошибка: Неверный логин или пароль.'
            
        if 'releases.1c.ru' not in post.url and 'login.1c.ru' in post.url:
            with open('debug_login_fail.html', 'w', encoding='utf-8') as f:
                f.write(post.text)
            return None, 'Вход выполнен, но переадресация не удалась. См. debug_login_fail.html'

        return session, None
        
    except Exception as e:
        logger.error(f"Login exception: {e}", exc_info=True)
        return None, f'Сетевая ошибка при входе: {e}'

def login_to_1c():
    return get_cached_session()

# --- SCRAPING & DATA ---

def get_releases_soup(session):
    try:
        r = session.get('https://releases.1c.ru/total', timeout=30)
        r.raise_for_status()
        return BeautifulSoup(r.content, 'html.parser'), None
    except Exception as e:
        return None, f'Ошибка получения релизов: {e}'

def get_version_from_detailed_page(session, config_url, branch_filter):
    try:
        r = session.get(config_url, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, 'html.parser')
        
        all_updates_link = soup.find('a', href=re.compile(r'\?allUpdates=true'))
        if all_updates_link:
            href = all_updates_link['href']
            url = urljoin(config_url, href)
            r2 = session.get(url, timeout=30)
            if r2.status_code == 200:
                soup = BeautifulSoup(r2.content, 'html.parser')

        table = soup.find('table', id='versionsTable')
        if not table: return None, None
        
        versions = []
        for row in table.find_all('tr')[1:]:
            cols = row.find_all('td')
            if len(cols) >= 2:
                ver_text = cols[0].get_text(strip=True)
                date_text = cols[1].get_text(strip=True)
                if ver_text.startswith(branch_filter):
                    versions.append((ver_text, date_text))
        
        if not versions: return None, None
        versions.sort(key=lambda x: version_tuple(x[0]), reverse=True)
        return versions[0]
    except Exception as e:
        logger.error(f"Ошибка deep fetch: {e}")
        return None, None

def get_target_versions(session: requests.Session, config_name: str) -> tuple:
    """ONLINE версия: берет данные прямо с сайта."""
    try:
        RELEASES_URL = 'https://releases.1c.ru/total'
        releases_response = session.get(RELEASES_URL, timeout=30) 
        releases_response.raise_for_status()
        releases_soup = BeautifulSoup(releases_response.content, 'html.parser')
        
        table = releases_soup.find('table', id='actualTable')
        if not table:
            return (None, 'Не удалось найти таблицу релизов на главной странице\\.')
            
        normalized_user_name = normalize_text(config_name)
        all_rows = table.find_all('tr')
        
        found_row = None
        for row in all_rows:
            name_cell = row.find('td', class_='nameColumn')
            if name_cell:
                site_name = normalize_text(name_cell.get_text(separator=' ', strip=True))
                if site_name == normalized_user_name:
                    found_row = row
                    break
                if normalized_user_name in site_name and len(site_name) - len(normalized_user_name) < 5:
                    found_row = row
                    break
        
        if not found_row:
            return (None, f'Конфигурация \'{escape_markdown(config_name)}\' не найдена на сайте 1С\\. Проверьте название\\.')
            
        version_cell = found_row.find('td', class_='versionColumn')
        if not version_cell:
            return (None, 'Не удалось найти ячейку с версиями для этой конфигурации\\.')
            
        all_a_tags = version_cell.find_all('a')
        
        if not all_a_tags:
            single_version = version_cell.get_text(strip=True)
            if not single_version:
                return (None, 'Не удалось извлечь единственную версию.')
            return ({'dp': single_version, 'non_dp': single_version}, None)
            
        dp_versions = []
        non_dp_versions = []
        
        for a_tag in all_a_tags:
            v_text = a_tag.get_text(strip=True)
            next_sibling = a_tag.find_next_sibling()
            is_dp = False
            if next_sibling and next_sibling.name == 'sup':
                if next_sibling.find('abbr', title=re.compile('Длительная поддержка')):
                    is_dp = True
            
            if is_dp:
                dp_versions.append(v_text)
            else:
                non_dp_versions.append(v_text)
                
        latest_dp = max(dp_versions, key=version_tuple) if dp_versions else None
        latest_non_dp = max(non_dp_versions, key=version_tuple) if non_dp_versions else None
        
        if not latest_dp and not latest_non_dp:
            return (None, 'Не удалось определить ни одной актуальной версии.')
            
        if not latest_dp: latest_dp = latest_non_dp
        if not latest_non_dp: latest_non_dp = latest_dp
            
        return ({'dp': latest_dp, 'non_dp': latest_non_dp}, None)

    except Exception as e:
        logger.error(f'Ошибка при получении целевых версий для \'{config_name}\': {e}', exc_info=True)
        return (None, f'Произошла ошибка при получении актуальных версий: {escape_markdown(str(e))}')

def get_target_versions_from_cache(config_name: str) -> tuple:
    """OFFLINE версия: берет данные из кэша."""
    full_cache = load_global_cache()
    cache_map = full_cache.get('data', {}) if 'data' in full_cache else full_cache

    if not cache_map:
        return None, "Данные отсутствуют. Пожалуйста, нажмите '🔄 Проверить версии', чтобы загрузить актуальные данные."

    norm_name = normalize_text(config_name)
    cached_item = cache_map.get(norm_name)

    if not cached_item:
        for k, v in cache_map.items():
            if norm_name in k and len(k) - len(norm_name) < 5:
                cached_item = v
                break
    
    if not cached_item:
        return None, f"Конфигурация '{escape_markdown(config_name)}' не найдена в кэше. Попробуйте обновить список версий."

    versions = cached_item.get('versions', [])
    if not versions:
        return None, "В кэше нет информации о версиях для этой конфигурации."

    versions.sort(key=lambda x: version_tuple(x['ver']), reverse=True)

    latest_obj = versions[0]
    dp_obj = next((v for v in versions if v['is_dp']), None)
    
    if not dp_obj: dp_obj = latest_obj

    return {'dp': dp_obj['ver'], 'non_dp': latest_obj['ver']}, None

def find_update_path(session: requests.Session, config_name: str, start_version: str, dp_target: str, non_dp_target: str) -> str:
    """
    Рассчитывает путь. Если session=None, работает только в офлайн-режиме.
    """
    norm_name = normalize_text(config_name)
    matrix_cache = load_matrix_cache()
    
    # Если в кэше нет матрицы
    if norm_name not in matrix_cache:
        # Офлайн режим
        if session is None:
            return (
                f"⚠️ Для конфигурации '{escape_markdown(config_name)}' еще не загружена карта обновлений.\n\n"
                "Пожалуйста, выполните **Ручную проверку версий** (кнопка в меню), чтобы бот скачал данные с сайта."
            )
        
        # Онлайн режим - скачиваем
        update_matrices_for_list(session, [config_name])
        matrix_cache = load_matrix_cache() 
    
    entry = matrix_cache.get(norm_name)
    if not entry:
        return f"Не удалось найти матрицу обновлений для '{escape_markdown(config_name)}' в кэше."

    matrix = entry['matrix']
    
    try:
        current_version = start_version.strip()
        actual_target = dp_target
        message_prefix = ''

        if version_tuple(current_version) > version_tuple(dp_target):
            actual_target = non_dp_target
            if actual_target != dp_target:
                message_prefix = f'⚠️ Ваша версия `{escape_markdown(current_version)}` новее LTS `{escape_markdown(dp_target)}`\\. Цель изменена на `{escape_markdown(actual_target)}`\\.\n\n'

        if current_version == actual_target:
            return message_prefix + rf'✅ Ваша версия `{escape_markdown(start_version)}` уже является актуальной \(`{escape_markdown(actual_target)}`\)\.'

        transitions = {}
        found_start_version = False
        
        for to_ver, from_vers in matrix.items():
            for fv in from_vers:
                if fv == current_version: found_start_version = True
                if fv not in transitions: transitions[fv] = []
                if fv != to_ver:
                    transitions[fv].append(to_ver)

        if not found_start_version:
            return message_prefix + f'⛔ Версия `{escape_markdown(current_version)}` не найдена в матрице обновлений 1С\\. Возможно, версия слишком старая или указана неверно\\.'

        queue = [[current_version]]
        visited = {current_version}
        final_path = None
        steps = 0
        
        while queue:
            steps += 1
            if steps > 10000: break
            
            path = queue.pop(0)
            node = path[-1]
            
            if node == actual_target:
                final_path = path
                break
            
            if version_tuple(node) > version_tuple(actual_target):
                continue

            if node in transitions:
                next_nodes = sorted(transitions[node], key=version_tuple, reverse=True)
                
                for next_node in next_nodes:
                    if next_node not in visited:
                        visited.add(next_node)
                        new_path = list(path)
                        new_path.append(next_node)
                        queue.append(new_path)

        if final_path:
            count = len(final_path) - 1
            path_str = ""
            for i, v in enumerate(final_path):
                if i == 0: continue
                prev = final_path[i-1]
                path_str += f"{i}\\. `{escape_markdown(prev)}` ➡️ `{escape_markdown(v)}`\n"

            return (
                message_prefix + 
                f'🚩 Текущая версия: `{escape_markdown(current_version)}`\n' # <--- ДОБАВЛЕНО
                f'🎯 Целевая версия: `{escape_markdown(actual_target)}`\n'
                f'Количество обновлений: *{count}*\n\n'
                f'*Цепочка обновлений:*\n{path_str}'
            )
        else:
            return message_prefix + f'⛔ Не удалось построить маршрут от `{escape_markdown(current_version)}` до `{escape_markdown(actual_target)}`\\. Разрыв в матрице обновлений\\.'

    except Exception as e:
        logger.error(f'Ошибка расчета пути: {e}', exc_info=True)
        return f'Ошибка алгоритма расчета: {escape_markdown(str(e))}'
        
def refresh_global_cache(session: requests.Session, force: bool = False):
    """
    Скачивает таблицу, парсит её и сравнивает с сохраненными ДАННЫМИ.
    Если force=True, сохраняет в любом случае.
    """
    try:
        logger.info("Скачивание таблицы релизов...")
        r = session.get('https://releases.1c.ru/total', timeout=45)
        
        # ПРОВЕРКА НА СЛЕТЕВШУЮ АВТОРИЗАЦИЮ
        if 'login.1c.ru' in r.url or '<form id="loginForm"' in r.text:
            logger.warning("Session appears invalid (redirected to login). Retrying login...")
            # Принудительно сбрасываем сессию и пробуем снова
            global _session_store
            _session_store['session'] = None
            new_session, err = get_cached_session()
            if new_session:
                session = new_session
                r = session.get('https://releases.1c.ru/total', timeout=45)
                if 'login.1c.ru' in r.url:
                    return False, "Ошибка: Повторный вход не удался. Проверьте логин/пароль."
            else:
                return False, f"Ошибка переавторизации: {err}"

        r.raise_for_status()
        soup = BeautifulSoup(r.content, 'html.parser')
        
        table = soup.find('table', id='actualTable')
        
        if not table:
            debug_file = 'debug_1c.html'
            with open(debug_file, 'w', encoding='utf-8') as f:
                f.write(r.text)
            logger.error(f"Таблица не найдена. Сохранен файл отладки: {debug_file}")
            return False, f"Не найдена таблица релизов. См. файл {debug_file} в папке бота."

        current_data_map = {}

        for row in table.find_all('tr'):
            name_cell = row.find('td', class_='nameColumn')
            if not name_cell: continue

            raw_name = name_cell.get_text(separator=' ', strip=True)
            norm_name = normalize_text(raw_name)
            
            link_tag = name_cell.find('a')
            details_url = None
            if link_tag and link_tag.has_attr('href'):
                details_url = urljoin('https://releases.1c.ru', link_tag['href'])

            ver_cell = row.find('td', class_='versionColumn')
            date_cell = ver_cell.find_next_sibling('td') if ver_cell else None
            
            versions_list = []
            
            if ver_cell and date_cell:
                all_a = ver_cell.find_all('a')
                all_dates = list(date_cell.stripped_strings)
                
                if not all_a:
                    v_text = ver_cell.get_text(strip=True)
                    d_text = date_cell.get_text(strip=True)
                    if v_text:
                        versions_list.append({'ver': v_text, 'date': d_text, 'is_dp': False})
                else:
                    for idx, a_tag in enumerate(all_a):
                        v_text = a_tag.get_text(strip=True)
                        d_text = all_dates[idx] if idx < len(all_dates) else "н/д"
                        
                        is_dp = False
                        next_el = a_tag.next_sibling
                        while next_el and (isinstance(next_el, NavigableString) and not next_el.strip()):
                            next_el = next_el.next_sibling
                        
                        if next_el and next_el.name == 'sup' and next_el.find('abbr', title=re.compile('Длительная')):
                            is_dp = True
                        
                        versions_list.append({'ver': v_text, 'date': d_text, 'is_dp': is_dp})
            
            current_data_map[norm_name] = {
                'raw_name': raw_name,
                'url': details_url,
                'versions': versions_list
            }

        old_cache = load_global_cache()
        old_data_map = old_cache.get('data', {})

        if not force and current_data_map == old_data_map:
            logger.info("♻️ Версии конфигураций (данные) не изменились. Пропуск.")
            return False, None

        if not force:
            logger.info("⚡ Обнаружено изменение версий! Обновляем кэш.")
        
        new_cache_obj = {
            'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'data': current_data_map
        }
        save_global_cache(new_cache_obj)
        
        return True, None
        
    except Exception as e:
        logger.error(f"Ошибка обновления кэша: {e}")
        return False, str(e)

def sync_user_configs_with_cache(user_configs: list, session: requests.Session = None) -> tuple:
    full_cache = load_global_cache()
    cache_map = full_cache.get('data', {}) if 'data' in full_cache else full_cache
    
    if not cache_map:
        return ("⚠️ Кэш пуст или еще не создан.", user_configs)

    results_text = []
    updated_configs = user_configs.copy()

    for i, config in enumerate(updated_configs):
        norm_name = normalize_text(config['name'])
        cached_item = cache_map.get(norm_name)
        
        if not cached_item:
            for k, v in cache_map.items():
                if norm_name in k and len(k) - len(norm_name) < 5:
                    cached_item = v
                    break
        
        safe_name = escape_markdown(config['name'])
        if not cached_item:
            results_text.append(f'❌ *{safe_name}*\n   └ Не найдено в базе 1С')
            continue

        versions = cached_item['versions']
        details_url = cached_item.get('url')
        versions.sort(key=lambda x: version_tuple(x['ver']), reverse=True)
        
        latest_obj = versions[0] if versions else None
        dp_obj = next((v for v in versions if v['is_dp']), None)
        if not dp_obj: dp_obj = latest_obj

        track_type = config.get('track_type', 'latest')
        branch_filter = config.get('branch_filter', '')
        last_ver_saved = config.get('last_version', '')
        was_already_new = config.get('is_new', False)
        
        display_lines = []
        detected_change = False
        save_ver = ""
        save_date = ""

        def format_line(icon, ver, date, mark):
            return f"{mark} {icon} `{escape_markdown(ver)}` {SEPARATOR_SYMBOL} `{escape_markdown(date)}`"

        if track_type == 'specific':
            target_obj = next((v for v in versions if v['ver'].startswith(branch_filter)), None)
            if not target_obj and session and details_url:
                 d_ver, d_date = get_version_from_detailed_page(session, details_url, branch_filter)
                 if d_ver: target_obj = {'ver': d_ver, 'date': d_date}
            
            if target_obj:
                is_diff = (target_obj['ver'] != last_ver_saved) and bool(last_ver_saved)
                if is_diff: detected_change = True
                mark = ICON_NEW_VERSION if (is_diff or was_already_new) else ICON_OK
                display_lines.append(format_line(ICON_SPECIFIC_TYPE, target_obj['ver'], target_obj['date'], mark))
                save_ver = target_obj['ver']
                save_date = target_obj['date']
            else:
                 display_lines.append(f"⚠️ Ветка `{escape_markdown(branch_filter)}` не найдена")
                 save_ver = last_ver_saved
                 save_date = config.get('last_date', '')

        elif track_type == 'both':
             old_parts = last_ver_saved.split('|') if '|' in last_ver_saved else [last_ver_saved, '']
             old_new = old_parts[0]
             old_dp = old_parts[1] if len(old_parts) > 1 else ''
             
             curr_new = latest_obj['ver'] if latest_obj else "Нет"
             curr_dp = dp_obj['ver'] if dp_obj else "Нет"
             
             is_new_diff = (curr_new != old_new) and bool(old_new)
             is_dp_diff = (curr_dp != old_dp) and bool(old_dp)
             if is_new_diff or is_dp_diff: detected_change = True
             
             mark_new = ICON_NEW_VERSION if (is_new_diff or was_already_new) else ICON_OK
             mark_dp = ICON_NEW_VERSION if (is_dp_diff or was_already_new) else ICON_OK
             
             display_lines.append(format_line(ICON_LATEST_TYPE, curr_new, latest_obj['date'] if latest_obj else '-', mark_new))
             display_lines.append(format_line(ICON_LTS_TYPE, curr_dp, dp_obj['date'] if dp_obj else '-', mark_dp))
             
             save_ver = f"{curr_new}|{curr_dp}"
             save_date = f"{latest_obj['date'] if latest_obj else '-'}|{dp_obj['date'] if dp_obj else '-'}"
        
        elif track_type == 'specific_dp':
            target_spec = next((v for v in versions if v['ver'].startswith(branch_filter)), None)
            if not target_spec and session and details_url:
                 d_ver, d_date = get_version_from_detailed_page(session, details_url, branch_filter)
                 if d_ver: target_spec = {'ver': d_ver, 'date': d_date}
            
            old_parts = last_ver_saved.split('|') if '|' in last_ver_saved else [last_ver_saved, '']
            old_spec = old_parts[0]
            old_dp = old_parts[1] if len(old_parts) > 1 else ''

            curr_spec = target_spec['ver'] if target_spec else old_spec
            curr_dp = dp_obj['ver'] if dp_obj else "Нет"
            
            is_spec_diff = (curr_spec != old_spec) and bool(old_spec) and target_spec
            is_dp_diff = (curr_dp != old_dp) and bool(old_dp)
            if is_spec_diff or is_dp_diff: detected_change = True
            
            mark_spec = ICON_NEW_VERSION if (is_spec_diff or was_already_new) else ICON_OK
            mark_dp = ICON_NEW_VERSION if (is_dp_diff or was_already_new) else ICON_OK
            
            if target_spec:
                display_lines.append(format_line(ICON_SPECIFIC_TYPE, curr_spec, target_spec['date'], mark_spec))
            else:
                display_lines.append(f"⚠️ Ветка `{escape_markdown(branch_filter)}` не найдена")

            display_lines.append(format_line(ICON_LTS_TYPE, curr_dp, dp_obj['date'] if dp_obj else '-', mark_dp))
            save_ver = f"{curr_spec}|{curr_dp}"
            save_date = f"{target_spec['date'] if target_spec else '-'}|{dp_obj['date'] if dp_obj else '-'}"

        else:
            t_obj = dp_obj if track_type == 'dp' else latest_obj
            icon = ICON_LTS_TYPE if track_type == 'dp' else ICON_LATEST_TYPE
            
            curr = t_obj['ver'] if t_obj else "Нет"
            is_diff = (curr != last_ver_saved) and bool(last_ver_saved)
            if is_diff: detected_change = True
            
            mark = ICON_NEW_VERSION if (is_diff or was_already_new) else ICON_OK
            display_lines.append(format_line(icon, curr, t_obj['date'] if t_obj else '-', mark))
            save_ver = curr
            save_date = t_obj['date'] if t_obj else '-'

        updated_configs[i]['last_version'] = save_ver
        updated_configs[i]['last_date'] = save_date
        updated_configs[i]['is_new'] = detected_change or was_already_new
        
        block_text = f'*{safe_name}*\n' + '\n'.join(display_lines)
        results_text.append(block_text)

    return ('\n\n'.join(results_text), updated_configs)
    
def _scrape_matrix_from_url(session, config_url):
    """Парсит таблицу обновлений по URL конфигурации."""
    try:
        r = session.get(config_url, timeout=45)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, 'html.parser')

        all_updates_link = soup.find('a', href=re.compile(r'\?allUpdates=true'))
        if all_updates_link:
            full_url = urljoin(config_url, all_updates_link['href'])
            r_all = session.get(full_url, timeout=45)
            r_all.raise_for_status()
            soup = BeautifulSoup(r_all.content, 'html.parser')

        table = soup.find('table', id='versionsTable')
        if not table:
            return None

        matrix = {}
        rows = table.find_all('tr')[1:] 
        for row in rows:
            cols = row.find_all('td')
            if len(cols) < 3: continue
            
            to_version = cols[0].get_text(strip=True)
            from_versions_raw = cols[2].get_text(strip=True)
            
            from_versions = [v.strip() for v in from_versions_raw.split(',') if v.strip()]
            matrix[to_version] = from_versions
            
        return matrix
    except Exception as e:
        logger.error(f"Ошибка парсинга матрицы (URL: {config_url}): {e}")
        return None
        
def update_matrices_for_list(session, config_names_list: list):
    """
    Проверяет список конфигураций. Если для конфигурации вышла новая версия,
    которой нет в matrix_cache -> скачивает таблицу обновлений.
    """
    global_cache = load_global_cache()
    g_data = global_cache.get('data', {}) if 'data' in global_cache else global_cache
    
    matrix_cache = load_matrix_cache()
    updated_count = 0
    unique_names = set(config_names_list)
    
    logger.info(f"Проверка актуальности матриц для {len(unique_names)} конфигураций...")

    for name in unique_names:
        norm_name = normalize_text(name)
        config_entry = g_data.get(norm_name)
        if not config_entry:
            for k, v in g_data.items():
                if norm_name in k and len(k) - len(norm_name) < 5:
                    config_entry = v; break
        
        if not config_entry:
            continue

        details_url = config_entry.get('url')
        if not details_url: continue

        versions_list = config_entry.get('versions', [])
        latest_ver_on_site = versions_list[0]['ver'] if versions_list else "0.0.0.0"

        cached_matrix_entry = matrix_cache.get(norm_name)
        
        need_update = True
        if cached_matrix_entry:
            cached_ver = cached_matrix_entry.get('latest_version_at_cache')
            if cached_ver == latest_ver_on_site:
                need_update = False
        
        if need_update:
            logger.info(f"🔄 Обновляем матрицу для '{name}' (v{latest_ver_on_site})...")
            new_matrix = _scrape_matrix_from_url(session, details_url)
            
            if new_matrix:
                matrix_cache[norm_name] = {
                    'latest_version_at_cache': latest_ver_on_site,
                    'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'matrix': new_matrix
                }
                updated_count += 1
                import time
                time.sleep(1.0)
            else:
                logger.warning(f"Не удалось скачать матрицу для {name}")

    if updated_count > 0:
        save_matrix_cache(matrix_cache)
        logger.info(f"✅ Обновлено матриц обновлений: {updated_count}")
    else:
        logger.info("Матрицы обновлений актуальны.")
       
def has_cached_matrix(config_name: str) -> bool:
    """Проверяет, есть ли скачанная матрица обновлений для этой конфигурации."""
    norm_name = normalize_text(config_name)
    matrix_cache = load_matrix_cache()
    return norm_name in matrix_cache
    
def search_config_candidates(query: str, limit: int = 10) -> list:
    """
    Ищет конфигурации в глобальном кэше по частичному совпадению.
    Возвращает список полных названий (raw_name).
    """
    cache = load_global_cache()
    data = cache.get('data', {})
    
    if not data:
        return []

    query_norm = normalize_text(query)
    candidates = []

    for key, item in data.items():
        # item['raw_name'] - это красивое имя, key - нормализованное
        # Ищем по нормализованному ключу или по отображаемому имени
        if query_norm in key or query_norm in normalize_text(item.get('raw_name', '')):
            candidates.append(item.get('raw_name', key))
            
    # Сортируем: сначала те, что начинаются с запроса, потом остальные
    candidates.sort(key=lambda x: 0 if normalize_text(x).startswith(query_norm) else 1)
    
    return candidates[:limit]