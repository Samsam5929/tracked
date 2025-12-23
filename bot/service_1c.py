import requests
import re
from bs4 import BeautifulSoup
from .config import LOGIN_1C, PASSWORD_1C
from .utils import normalize_text, escape_markdown
import logging

logger = logging.getLogger(__name__)

def login_to_1c():
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
    try:
        LOGIN_URL = 'https://login.1c.ru/login'
        r = session.get(LOGIN_URL)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, 'html.parser')
        
        execution = soup.find('input', {'name': 'execution'})
        if not execution: return None, 'Ошибка: Не найден токен входа.'
            
        payload = {
            'username': LOGIN_1C, 'password': PASSWORD_1C, 
            'execution': execution.get('value'), '_eventId': 'submit', 'rememberMe': 'on'
        }
        post = session.post(LOGIN_URL, data=payload)
        post.raise_for_status()
        
        if 'Неверный логин или пароль' in post.text:
            return None, 'Ошибка: Неверный логин или пароль.'
        return session, None
    except Exception as e:
        return None, f'Сетевая ошибка: {e}'

def get_releases_soup(session):
    try:
        r = session.get('https://releases.1c.ru/total')
        r.raise_for_status()
        return BeautifulSoup(r.content, 'html.parser'), None
    except Exception as e:
        return None, f'Ошибка получения релизов: {e}'

def parse_versions_from_soup(soup, configs_data: list):
    results_text = []
    updated_configs = configs_data.copy()
    table = soup.find('table', id='actualTable')
    if not table: return ('Ошибка таблицы.', updated_configs)

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
            # Нечеткий поиск
            for k, v in site_configs.items():
                if norm_name in k and len(k) - len(norm_name) < 5:
                    found_row = v; break
        
        safe_name = escape_markdown(config['name'])
        if not found_row:
            results_text.append(f'❌ *{safe_name}*\n   └ Не найдено\\!')
            continue

        ver_cell = found_row.find('td', class_='versionColumn')
        date_cell = ver_cell.find_next_sibling('td')
        
        # Логика извлечения версии
        all_a = ver_cell.find_all('a')
        all_dates = list(date_cell.stripped_strings)
        
        curr_ver, rel_date = "", ""
        if not all_a:
            curr_ver = ver_cell.get_text(strip=True)
            rel_date = date_cell.get_text(strip=True)
        else:
            # Поиск ДП
            idx = 0
            for j, a in enumerate(all_a):
                nxt = a.find_next_sibling()
                if nxt and nxt.name == 'sup' and nxt.find('abbr', title=re.compile('Длительная')):
                    idx = j; break
            curr_ver = all_a[idx].get_text(strip=True)
            rel_date = all_dates[idx] if idx < len(all_dates) else all_dates[0]

        updated_configs[i]['last_date'] = rel_date
        last_ver = config.get('last_version', '')
        
        # --- ВОТ ЗДЕСЬ БЫЛА ОШИБКА, ТЕПЕРЬ ИСПРАВЛЕНО ---
        status = ''
        if not last_ver:
            updated_configs[i]['last_version'] = curr_ver
            status = '🔹 \\(Первая проверка\\)'
        elif curr_ver != last_ver:
            updated_configs[i]['last_version'] = curr_ver
            updated_configs[i]['is_new'] = True
            status = '💥 *НОВАЯ ВЕРСИЯ\\!*'
        else:
            # Если версия не изменилась
            if config.get('is_new', False):
                status = '💥 *НОВАЯ ВЕРСИЯ\\!* \\(ждет\\)'
            else:
                status = '✅ \\(Без изменений\\)'
        # ------------------------------------------------
            
        results_text.append(f'*{safe_name}*\n   └ Версия: `{escape_markdown(curr_ver)}`, Дата: `{escape_markdown(rel_date)}`\n   └ Статус: {status}')

    return ('\n\n'.join(results_text), updated_configs)

def get_target_versions(session: requests.Session, config_name: str) -> tuple:
    try:
        RELEASES_URL = 'https://releases.1c.ru/total'
        releases_response = session.get(RELEASES_URL)
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
                
        def version_key(v):
            try: return [int(p) for p in v.split('.')]
            except ValueError: return [0]

        latest_dp = max(dp_versions, key=version_key) if dp_versions else None
        latest_non_dp = max(non_dp_versions, key=version_key) if non_dp_versions else None
        
        if not latest_dp and not latest_non_dp:
            return (None, 'Не удалось определить ни одной актуальной версии.')
            
        if not latest_dp: latest_dp = latest_non_dp
        if not latest_non_dp: latest_non_dp = latest_dp
            
        return ({'dp': latest_dp, 'non_dp': latest_non_dp}, None)

    except Exception as e:
        logger.error(f'Ошибка при получении целевых версий для \'{config_name}\': {e}', exc_info=True)
        return (None, f'Произошла ошибка при получении актуальных версий: {escape_markdown(str(e))}')

def find_update_path(session: requests.Session, config_name: str, start_version: str, dp_target: str, non_dp_target: str) -> str:
    try:
        RELEASES_URL = 'https://releases.1c.ru/total'
        releases_response = session.get(RELEASES_URL)
        releases_response.raise_for_status()
        releases_soup = BeautifulSoup(releases_response.content, 'html.parser')
        
        config_link_tag = releases_soup.find('a', string=re.compile(re.escape(config_name), re.IGNORECASE))
        if not config_link_tag or not config_link_tag.has_attr('href'):
            return f'Не удалось найти конфигурацию с названием "{escape_markdown(config_name)}" на сайте 1С. Проверьте точность названия.'

        config_page_url = 'https://releases.1c.ru' + config_link_tag['href']
        config_page_response = session.get(config_page_url)
        config_page_response.raise_for_status()
        
        initial_soup = BeautifulSoup(config_page_response.content, 'html.parser')
        updates_soup = initial_soup
        
        all_updates_link_tag = initial_soup.find('a', href=re.compile(r'\?allUpdates=true'))
        if all_updates_link_tag:
            base_url = 'https://releases.1c.ru'
            relative_url = config_link_tag['href'].split('?')[0] + all_updates_link_tag['href']
            all_updates_url = base_url + relative_url
            updates_response = session.get(all_updates_url)
            updates_response.raise_for_status()
            updates_soup = BeautifulSoup(updates_response.content, 'html.parser')

        updates_table = updates_soup.find('table', id='versionsTable')
        if not updates_table:
            return 'Не удалось найти таблицу с историей обновлений на странице конфигурации.'

        rows = updates_table.find_all('tr')[1:]
        current_version = start_version.strip()
        actual_target = dp_target
        message_prefix = ''

        def is_version_greater(v1, v2):
            try:
                return [int(p) for p in v1.split('.')] > [int(p) for p in v2.split('.')]
            except: return v1 > v2

        if is_version_greater(current_version, dp_target):
            actual_target = non_dp_target
            message_prefix = f'Ваша версия `{escape_markdown(current_version)}` новее версии на ДП `{escape_markdown(dp_target)}`\\. Расчет выполняется до версии не на длительной поддержке\\.\n\n'

        if current_version == actual_target:
            return message_prefix + f'Ваша версия `{escape_markdown(start_version)}` уже является целевой (`{escape_markdown(actual_target)}`).'

        predecessors = {}
        for row in rows:
            cols = row.find_all('td')
            if len(cols) < 3: continue
            to_version = cols[0].get_text(strip=True)
            from_versions = [v.strip() for v in cols[2].get_text(strip=True).split(',')]
            predecessors[to_version] = from_versions

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
        
        while current_version != actual_target and count < max_steps:
            possible_next_steps = []
            for row in rows:
                cols = row.find_all('td')
                if len(cols) < 3: continue
                to_ver = cols[0].get_text(strip=True)
                from_vers = [v.strip() for v in cols[2].get_text(strip=True).split(',')]
                
                if current_version in from_vers:
                    if to_ver in reachable_versions:
                        is_next_dp = bool(row.find('small', string='ДП'))
                        possible_next_steps.append({'version': to_ver, 'is_dp': is_next_dp})

            if not possible_next_steps:
                if count > 0:
                    return message_prefix + f'Пройдено *{count}* обновлений до версии `{escape_markdown(current_version)}`\\. Дальнейший шаг обновления не найден\\.'
                return message_prefix + f'Не удалось найти ни одного шага обновления с версии `{escape_markdown(start_version)}`\\.'

            chosen_step = None
            for step in possible_next_steps:
                if not step['is_dp']:
                    chosen_step = step
                    break
            if not chosen_step: chosen_step = possible_next_steps[0]

            current_version = chosen_step['version']
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