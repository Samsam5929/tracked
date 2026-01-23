import requests
import re
from bs4 import BeautifulSoup, NavigableString
from datetime import datetime
from .config import *
from .utils import normalize_text, escape_markdown, version_tuple
import logging
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

def login_to_1c():
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
    try:
        LOGIN_URL = 'https://login.1c.ru/login'
        r = session.get(LOGIN_URL, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, 'html.parser')
        
        execution = soup.find('input', {'name': 'execution'})
        if not execution: 
            return None, 'Ошибка: Не найден токен входа (возможно, изменилась страница авторизации).'
            
        payload = {
            'username': LOGIN_1C, 'password': PASSWORD_1C, 
            'execution': execution.get('value'), '_eventId': 'submit', 'rememberMe': 'on'
        }
        post = session.post(LOGIN_URL, data=payload, timeout=30)
        post.raise_for_status()
        
        if 'Неверный логин или пароль' in post.text:
            return None, 'Ошибка: Неверный логин или пароль.'
        return session, None
    except Exception as e:
        return None, f'Сетевая ошибка: {e}'

def get_releases_soup(session):
    try:
        r = session.get('https://releases.1c.ru/total', timeout=30)
        r.raise_for_status()
        return BeautifulSoup(r.content, 'html.parser'), None
    except Exception as e:
        return None, f'Ошибка получения релизов: {e}'

def parse_versions_from_soup(soup, configs_data: list, session: requests.Session = None):
    results_text = []
    updated_configs = configs_data.copy()
    
    if not soup:
        return ('Ошибка: пустая страница релизов.', updated_configs)

    table = soup.find('table', id='actualTable')
    if not table: 
        return ('Ошибка: не найдена таблица релизов.', updated_configs)

    site_configs = {}
    for row in table.find_all('tr'):
        name_cell = row.find('td', class_='nameColumn')
        if name_cell:
            raw_name = name_cell.get_text(separator=' ', strip=True)
            site_configs[normalize_text(raw_name)] = row

    for i, config in enumerate(updated_configs):
        norm_name = normalize_text(config['name'])
        found_row = site_configs.get(norm_name)
        
        if not found_row:
            for k, v in site_configs.items():
                if norm_name in k and len(k) - len(norm_name) < 5:
                    found_row = v; break
        
        safe_name = escape_markdown(config['name'])
        if not found_row:
            results_text.append(f'❌ *{safe_name}*\n   └ Не найдено\\!')
            continue

        ver_cell = found_row.find('td', class_='versionColumn')
        
        if not ver_cell:
            results_text.append(f'⚠️ *{safe_name}*\n   └ Ошибка парсинга: не найдена колонка версии')
            logger.warning(f"Не найдена versionColumn для {config['name']}")
            continue

        date_cell = ver_cell.find_next_sibling('td')
        
        if not date_cell:
             results_text.append(f'⚠️ *{safe_name}*\n   └ Ошибка парсинга: не найдена дата')
             continue

        all_a = ver_cell.find_all('a')
        all_dates = list(date_cell.stripped_strings)
        
        found_versions = [] 

        if not all_a:
            v_text = ver_cell.get_text(strip=True)
            d_text = date_cell.get_text(strip=True)
            found_versions.append({'ver': v_text, 'date': d_text, 'is_dp': False})
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
                
                found_versions.append({'ver': v_text, 'date': d_text, 'is_dp': is_dp})

        found_versions.sort(key=lambda x: version_tuple(x['ver']), reverse=True)

        latest_obj = found_versions[0] if found_versions else None
        
        dp_obj = next((v for v in found_versions if v['is_dp']), None)
        
        if not dp_obj:
            dp_obj = latest_obj

        track_type = config.get('track_type', 'latest')
        branch_filter = config.get('branch_filter', '')
        
        last_ver_saved = config.get('last_version', '')
        last_date_saved = config.get('last_date', '')
        was_already_new = config.get('is_new', False)
        
        save_ver = ""
        save_date = ""
        display_lines = []
        detected_change = False

        def format_line(icon, ver, date, mark):
            return f"{mark} {icon} `{escape_markdown(ver)}` {SEPARATOR_SYMBOL} `{escape_markdown(date)}`"

        if track_type == 'specific':
            target_obj = next((v for v in found_versions if v['ver'].startswith(branch_filter)), None)
            
            if not target_obj and session:
                name_cell = found_row.find('td', class_='nameColumn')
                link_tag = name_cell.find('a') if name_cell else None
                if link_tag and link_tag.has_attr('href'):
                    config_url = 'https://releases.1c.ru' + link_tag['href']
                    deep_ver, deep_date = get_version_from_detailed_page(session, config_url, branch_filter)
                    if deep_ver:
                        target_obj = {'ver': deep_ver, 'date': deep_date}

            if not target_obj:
                display_lines.append(f"⚠️ Ветка `{escape_markdown(branch_filter)}` не найдена")
                save_ver = last_ver_saved 
                save_date = last_date_saved
            else:
                curr_ver = target_obj['ver']
                raw_date = target_obj.get('date', '')
                if not raw_date or raw_date == 'н/д':
                    if not last_date_saved:
                        curr_date = datetime.now().strftime('%d.%m.%y')
                    else:
                        curr_date = last_date_saved
                else:
                    curr_date = raw_date

                is_ver_changed = (curr_ver != last_ver_saved) and bool(last_ver_saved)
                if is_ver_changed: detected_change = True
                
                mark = ICON_NEW_VERSION if (is_ver_changed or was_already_new) else ICON_OK
                
                display_lines.append(format_line(ICON_SPECIFIC_TYPE, curr_ver, curr_date, mark))
                save_ver = curr_ver
                save_date = curr_date

        elif track_type == 'specific_dp':
            target_spec = next((v for v in found_versions if v['ver'].startswith(branch_filter)), None)
            
            if not target_spec and session:
                name_cell = found_row.find('td', class_='nameColumn')
                link_tag = name_cell.find('a') if name_cell else None
                if link_tag and link_tag.has_attr('href'):
                    config_url = 'https://releases.1c.ru' + link_tag['href']
                    deep_ver, deep_date = get_version_from_detailed_page(session, config_url, branch_filter)
                    if deep_ver:
                        target_spec = {'ver': deep_ver, 'date': deep_date}

            target_dp = dp_obj 
            
            old_parts = last_ver_saved.split('|') if '|' in last_ver_saved else [last_ver_saved, '']
            old_spec = old_parts[0]
            old_dp = old_parts[1] if len(old_parts) > 1 else ''
            
            old_date_parts = last_date_saved.split('|') if '|' in last_date_saved else [last_date_saved, '']
            old_spec_date = old_date_parts[0]

            if not target_spec:
                display_lines.append(f"⚠️ Ветка `{escape_markdown(branch_filter)}` не найдена")
                curr_spec_ver = old_spec
                curr_spec_date = old_spec_date
            else:
                curr_spec_ver = target_spec['ver']
                raw_spec_date = target_spec.get('date', '')
                if not raw_spec_date or raw_spec_date == 'н/д':
                    if not old_spec_date or old_spec_date == '-':
                        curr_spec_date = datetime.now().strftime('%d.%m.%y')
                    else:
                        curr_spec_date = old_spec_date
                else:
                    curr_spec_date = raw_spec_date
                
                is_spec_changed = (curr_spec_ver != old_spec) and bool(old_spec)
                if is_spec_changed: detected_change = True
                
                mark_spec = ICON_NEW_VERSION if (is_spec_changed or was_already_new) else ICON_OK
                display_lines.append(format_line(ICON_SPECIFIC_TYPE, curr_spec_ver, curr_spec_date, mark_spec))

            curr_dp_ver = target_dp['ver'] if target_dp else "Нет"
            curr_dp_date = target_dp['date'] if target_dp else "-"
            
            is_dp_changed = (curr_dp_ver != old_dp) and bool(old_dp)
            if is_dp_changed: detected_change = True
            
            mark_dp = ICON_NEW_VERSION if (is_dp_changed or was_already_new) else ICON_OK
            display_lines.append(format_line(ICON_LTS_TYPE, curr_dp_ver, curr_dp_date, mark_dp))

            save_ver = f"{curr_spec_ver}|{curr_dp_ver}"
            save_date = f"{curr_spec_date}|{curr_dp_date}"

        elif track_type == 'both':
            old_parts = last_ver_saved.split('|') if '|' in last_ver_saved else [last_ver_saved, '']
            old_new = old_parts[0]
            old_dp = old_parts[1] if len(old_parts) > 1 else ''

            curr_new_ver = latest_obj['ver'] if latest_obj else "Нет"
            curr_new_date = latest_obj['date'] if latest_obj else "-"
            
            is_ver_changed = (curr_new_ver != old_new) and bool(old_new)
            if is_ver_changed: detected_change = True
            
            mark_new = ICON_NEW_VERSION if (is_ver_changed or was_already_new) else ICON_OK
            display_lines.append(format_line(ICON_LATEST_TYPE, curr_new_ver, curr_new_date, mark_new))

            curr_dp_ver = dp_obj['ver'] if dp_obj else "Нет"
            curr_dp_date = dp_obj['date'] if dp_obj else "-"
            
            is_dp_changed = (curr_dp_ver != old_dp) and bool(old_dp)
            if is_dp_changed: detected_change = True
            
            mark_dp = ICON_NEW_VERSION if (is_dp_changed or was_already_new) else ICON_OK
            display_lines.append(format_line(ICON_LTS_TYPE, curr_dp_ver, curr_dp_date, mark_dp))

            save_ver = f"{curr_new_ver}|{curr_dp_ver}"
            save_date = f"{curr_new_date}|{curr_dp_date}"

        else:
            target_obj = None
            icon = ICON_LATEST_TYPE
            
            if track_type == 'dp':
                target_obj = dp_obj 
                icon = ICON_LTS_TYPE
            else:
                target_obj = latest_obj
                icon = ICON_LATEST_TYPE

            curr_ver = target_obj['ver'] if target_obj else "Нет данных"
            curr_date = target_obj['date'] if target_obj else "-"
            
            is_ver_changed = (curr_ver != last_ver_saved) and bool(last_ver_saved)
            if is_ver_changed: detected_change = True
            
            mark = ICON_NEW_VERSION if (is_ver_changed or was_already_new) else ICON_OK
            display_lines.append(format_line(icon, curr_ver, curr_date, mark))
            
            save_ver = curr_ver
            save_date = curr_date

        updated_configs[i]['last_version'] = save_ver
        updated_configs[i]['last_date'] = save_date
        updated_configs[i]['is_new'] = detected_change or was_already_new
        
        block_text = f'*{safe_name}*\n' + '\n'.join(display_lines)
        results_text.append(block_text)

    return ('\n\n'.join(results_text), updated_configs)

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
        logger.error(f"Ошибка парсинга детальной страницы {config_url}: {e}")
        return None, None

def get_target_versions(session: requests.Session, config_name: str) -> tuple:
    try:
        RELEASES_URL = 'https://releases.1c.ru/total'
        # Добавляем timeout
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

def (session: requests.Session, config_name: str, start_version: str, dp_target: str, non_dp_target: str) -> str:
    try:
        RELEASES_URL = 'https://releases.1c.ru/total'
        # 1. Timeout
        releases_response = session.get(RELEASES_URL, timeout=30) 
        releases_response.raise_for_status()
        releases_soup = BeautifulSoup(releases_response.content, 'html.parser')
        
        table = releases_soup.find('table', id='actualTable')
        if not table:
            return 'Не удалось найти таблицу релизов.'

        normalized_name = normalize_text(config_name)
        config_link_tag = None
        
        for row in table.find_all('tr'):
            name_cell = row.find('td', class_='nameColumn')
            if name_cell:
                site_name = normalize_text(name_cell.get_text(separator=' ', strip=True))
                if site_name == normalized_name or (normalized_name in site_name and len(site_name) - len(normalized_name) < 5):
                    config_link_tag = name_cell.find('a')
                    break
        
        if not config_link_tag or not config_link_tag.has_attr('href'):
            return f'Не удалось найти конфигурацию с названием "{escape_markdown(config_name)}" на сайте 1С. Проверьте точность названия.'

        config_page_url = urljoin('https://releases.1c.ru', config_link_tag['href'])
        config_page_response = session.get(config_page_url, timeout=30)
        config_page_response.raise_for_status()
        
        initial_soup = BeautifulSoup(config_page_response.content, 'html.parser')
        updates_soup = initial_soup
        
        all_updates_link_tag = initial_soup.find('a', href=re.compile(r'\?allUpdates=true'))
        if all_updates_link_tag:
            base_url = 'https://releases.1c.ru'
            href = all_updates_link_tag['href']
            all_updates_url = urljoin(config_page_url, all_updates_link_tag['href'])
                
            updates_response = session.get(all_updates_url, timeout=30)
            updates_response.raise_for_status()
            updates_soup = BeautifulSoup(updates_response.content, 'html.parser')

        updates_table = updates_soup.find('table', id='versionsTable')
        if not updates_table:
            return 'Не удалось найти таблицу с историей обновлений на странице конфигурации.'
            
        # ДОБАВИТЬ ПРОВЕРКУ НА НАЛИЧИЕ СТРОК
        all_rows = updates_table.find_all('tr')
        if not all_rows or len(all_rows) < 2:
            return 'Таблица обновлений пуста или имеет неверный формат.'
            
        rows = all_rows[1:]

        current_version = start_version.strip()
        actual_target = dp_target
        message_prefix = ''

        if version_tuple(current_version) > version_tuple(dp_target):
            actual_target = non_dp_target
            message_prefix = f'Ваша версия `{escape_markdown(current_version)}` новее версии на ДП `{escape_markdown(dp_target)}`\\. Расчет выполняется до версии не на длительной поддержке\\.\n\n'

        if current_version == actual_target:
            return message_prefix + rf'Ваша версия `{escape_markdown(start_version)}` уже является целевой \(`{escape_markdown(actual_target)}`\)\.'

        predecessors = {}
        transitions = {} 
        
        found_start_version = False
        
        for row in rows:
            cols = row.find_all('td')
            if len(cols) < 3: continue
            to_version = cols[0].get_text(strip=True)
            from_versions_raw = cols[2].get_text(strip=True)
            from_versions = [v.strip() for v in from_versions_raw.split(',') if v.strip()]
            
            if current_version in from_versions:
                found_start_version = True

            predecessors[to_version] = from_versions
            
            for fv in from_versions:
                if fv not in transitions:
                    transitions[fv] = []
                transitions[fv].append({'version': to_version})

        if not found_start_version:
             return message_prefix + f'⚠️ Версия `{escape_markdown(current_version)}` не найдена в списке обновлений 1С\\. Возможно, она слишком старая или указана с ошибкой\\.'

        reachable_versions = {actual_target}
        queue = [actual_target]
        while queue:
            curr = queue.pop(0)
            if curr in predecessors:
                for prev_ver in predecessors[curr]:
                    if prev_ver not in reachable_versions:
                        reachable_versions.add(prev_ver)
                        queue.append(prev_ver)

        count = 0
        max_steps = 100
        path_log = []
        
        while current_version != actual_target and count < max_steps:
            possible_next_steps = transitions.get(current_version, [])
            valid_steps = [step for step in possible_next_steps if step['version'] in reachable_versions]

            if not valid_steps:
                if count > 0:
                    return message_prefix + f'Пройдено *{count}* обновлений до версии `{escape_markdown(current_version)}`\\. Дальнейший путь прерван (тупик)\\.'
                return message_prefix + f'Не удалось найти путь обновления от `{escape_markdown(start_version)}` до `{escape_markdown(actual_target)}`\\.'

            chosen_step = max(valid_steps, key=lambda x: version_tuple(x['version']))

            current_version = chosen_step['version']
            path_log.append(current_version)
            count += 1

        if current_version != actual_target:
             return message_prefix + f'Не удалось построить полный маршрут. Прервано на версии `{escape_markdown(current_version)}`.'

        return message_prefix + f'От версии `{escape_markdown(start_version)}` до цели `{escape_markdown(actual_target)}` необходимо выполнить *{count}* обновлений\\.'

    except requests.RequestException as e:
        logger.error(f'Сетевая ошибка при подсчете обновлений: {e}')
        return f'Произошла сетевая ошибка: {escape_markdown(str(e))}'
    except Exception as e:
        logger.error(f'Непредвиденная ошибка при подсчете обновлений: {e}', exc_info=True)
        return f'Произошла непредвиденная ошибка: {escape_markdown(str(e))}'