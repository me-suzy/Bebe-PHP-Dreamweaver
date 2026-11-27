"""Audit reciprocal FLAGS links and fill missing EN category entries from RO.
No translation, no index expansion, no deletion of existing entries.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import sys
import tempfile
from html import unescape as html_unescape
from urllib.parse import unquote, urlsplit
from bs4 import BeautifulSoup

BASE = Path(r'E:\Carte\BB\17 - Site Leadership\Principal')
PIPELINE = Path(r'E:\Carte\BB\17 - Site Leadership\alte\Ionel Balauta\Aryeht\Task 1 - Traduce tot site-ul\Doar Google Web\Andreea\Meditatii\2023\Iulia Python')
REPORT = Path(__file__).with_name('category-sync-report.json')
MONTHS = ['ianuarie','februarie','martie','aprilie','mai','iunie','iulie','august','septembrie','octombrie','noiembrie','decembrie']

def read(path):
    return path.read_bytes().decode('utf-8', errors='surrogateescape')

FLAG_LINK_RE = re.compile(r'<a\b([^>]*)>\s*<img\b([^>]*)>', re.I | re.S)
FLAG_SECTION_RE = re.compile(r'<!--\s*FLAGS_1\s*-->(.*?)<!--\s*FLAGS\s*-->', re.S)
_FLAGS_CACHE = {}


def flags(text):
    """Linkurile ro/en din FLAGS. Varianta rapida (regex), cu acelasi rezultat ca flags_bs4."""
    section = FLAG_SECTION_RE.search(text)
    if not section:
        return {}
    key = section[1]
    if key in _FLAGS_CACHE:
        return dict(_FLAGS_CACHE[key])
    result = {}
    for link in FLAG_LINK_RE.finditer(section[1]):
        href = re.search(r'\bhref\s*=\s*["\']([^"\']*)["\']', link[1], re.I)
        src = re.search(r'\bsrc\s*=\s*["\']([^"\']*)["\']', link[2], re.I)
        if not href or not src:
            continue
        for lang in ('ro', 'en'):
            if re.search(r'flag_lang_' + lang + r'\.', src[1], re.I):
                url = urlsplit(html_unescape(href[1]))
                pattern = r'^/en/([^/]+\.html)$' if lang == 'en' else r'^/([^/]+\.html)$'
                match = re.match(pattern, url.path, re.I)
                if url.hostname in ('neculaifantanaru.com', 'www.neculaifantanaru.com') and match:
                    name = unquote(match[1])
                    if '/' not in name and '\\' not in name:
                        result[lang] = name
    _FLAGS_CACHE[key] = dict(result)
    return result


def flags_bs4(text):
    section = re.search(r'<!--\s*FLAGS_1\s*-->(.*?)<!--\s*FLAGS\s*-->', text, re.S)
    if not section:
        return {}
    soup = BeautifulSoup(section[1], 'html.parser')
    result = {}
    for link in soup.select('a[href]'):
        img = link.find('img')
        if not img:
            continue
        for lang in ('ro', 'en'):
            if re.search(r'flag_lang_' + lang + r'\.', img.get('src', ''), re.I):
                from urllib.parse import urlsplit, unquote
                url = urlsplit(link['href'])
                pattern = r'^/en/([^/]+\.html)$' if lang == 'en' else r'^/([^/]+\.html)$'
                match = re.match(pattern, url.path, re.I)
                if url.hostname in ('neculaifantanaru.com', 'www.neculaifantanaru.com') and match:
                    name = unquote(match[1])
                    if '/' not in name and '\\' not in name:
                        result[lang] = name
    return result


def canonical_identity_issue(lang, name, text):
    """Return a reportable issue when an article canonical does not identify its own file."""
    if not re.search(r'<h1\b[^>]*\bclass\s*=\s*["\'][^"\']*\bden_articol\b', text, re.I):
        return None
    tag = re.search(r'<link\b(?=[^>]*\brel\s*=\s*["\'][^"\']*\bcanonical\b[^"\']*["\'])[^>]*>', text, re.I)
    href = re.search(r'\bhref\s*=\s*["\']([^"\']*)["\']', tag[0], re.I) if tag else None
    value = href[1] if href else '(lipsa)'
    expected_path = ('/en/' if lang == 'en' else '/') + name
    expected_url = 'https://neculaifantanaru.com' + expected_path
    if href:
        parsed = urlsplit(html_unescape(value))
        if parsed.hostname and parsed.hostname.lower() in ('neculaifantanaru.com', 'www.neculaifantanaru.com') \
                and unquote(parsed.path) == expected_path and not parsed.query and not parsed.fragment:
            return None
    return f'CANONICAL {lang.upper()} diferit: {lang}/{name} -> {value}; trebuie sa indice {expected_url}'

def flag_pair_issues(maps, exists):
    issues = []
    for lang, entries in maps.items():
        other = 'en' if lang == 'ro' else 'ro'
        for name, pair in entries.items():
            if pair.get(lang) != name:
                issues.append(f'{lang}/{name}: steagul {lang.upper()} indică {pair.get(lang) or "un link lipsă/invalid"}, dar trebuie să indice {name}')
            counterpart = pair.get(other)
            if not counterpart:
                issues.append(f'{lang}/{name}: lipsește linkul FLAGS către {other.upper()}')
            elif not exists(other, counterpart):
                issues.append(f'{lang}/{name}: corespondent lipsă {other}/{counterpart}')
            else:
                target = maps[other].get(counterpart, {})
                for flag in ('ro', 'en'):
                    if pair.get(flag) != target.get(flag):
                        issues.append(f'FLAGS {flag.upper()} diferite: {lang}/{name} → {pair.get(flag) or "lipsă"}; {other}/{counterpart} → {target.get(flag) or "lipsă"}')
    return issues


MONTH_NUM = {m: i + 1 for i, m in enumerate(MONTHS)}
MONTH_NUM.update({m: i + 1 for i, m in enumerate(['january','february','march','april','may','june','july','august','september','october','november','december'])})
BLOCK_RE = re.compile(
    r'<table\s[^>]*\bwidth\s*=\s*["\'][^"\']+["\'][^>]*>(?:(?!</table>).)*?class="den_articol".*?</table>\s*'
    r'<p\s+class\s*=\s*["\']text_obisnuit2["\'][^>]*>.*?</p>\s*'
    r'<table\s[^>]*\bwidth\s*=\s*["\'][^"\']+["\'][^>]*>.*?</table>\s*'
    r'<p\s+class\s*=\s*["\']text_obisnuit["\'][^>]*>\s*</p>', re.S | re.I)
CATEGORY_START = '<!-- ARTICOL CATEGORIE START -->'
CATEGORY_END = '<!-- ARTICOL CATEGORIE FINAL -->'
GRACE_SECONDS = 120
TD_RE = re.compile(r'(<td\b[^>]*class\s*=\s*["\'][^"\']*\btext_dreapta\b[^"\']*["\'][^>]*>)(.*?)(</td>|</tr>)', re.I | re.S)
H1_RE = re.compile(r'<h1\b[^>]*class\s*=\s*["\'][^"\']*\bden_articol\b', re.I)


def base_name(url):
    return unquote(urlsplit(url).path).split('/')[-1].lower()


def block_date(text):
    m = re.search(r'\bOn\s+([^\s\d,<]+)\s+(\d{1,2}),?\s+(\d{4})', re.sub(r'<[^>]+>', ' ', text))
    if not m or m[1].lower() not in MONTH_NUM:
        return None
    try:
        return dt.date(int(m[3]), MONTH_NUM[m[1].lower()], int(m[2]))
    except ValueError:
        return None


def date_cell(text):
    """Continutul celulei text_dreapta cu data si categoria (incepe cu „On ...” si contine un link)."""
    for cell in TD_RE.finditer(text):
        if re.search(r'\bOn\s+', cell[2]) and re.search(r'<a\b', cell[2]):
            return cell
    return None


def article_category(name, text):
    cell = date_cell(text)
    if not cell:
        return None
    for href in re.findall(r'<a\b[^>]*href\s*=\s*["\']([^"\']+)', cell[2], re.I):
        if base_name(href).endswith('.html') and base_name(href) != name.lower():
            return base_name(href)
    return None


def scan_language(folder):
    """Intoarce (articole, categorii, listate): articole/categorii nume -> (path, text); listate nume articol -> {categorii}."""
    articles, categories, listed = {}, {}, {}
    for path in sorted(folder.glob('*.html')):
        if path.name.lower() == 'index.html':
            continue
        text = read(path)
        if H1_RE.search(text):
            articles[path.name] = (path, text)
        elif CATEGORY_START in text:
            start = text.index(CATEGORY_START)
            end = text.find(CATEGORY_END, start)
            categories[path.name] = (path, text)
            for link in re.finditer(r'<a\b[^>]*class\s*=\s*["\'][^"\']*\blinkMare\b[^>]*>', text[start:end if end > 0 else len(text)], re.I):
                href = re.search(r'href\s*=\s*["\']([^"\']+)', link[0])
                if href:
                    listed.setdefault(base_name(href[1]), set()).add(path.name)
    return articles, categories, listed


def teaser_of(text):
    """Textul de prezentare: <em> din primul text_obisnuit2 dupa SASA-1; altfel inceputul primului paragraf de text."""
    zone = re.search(r'<!--\s*SASA-1\s*-->(.*?)(?:<!--\s*SASA-2\s*-->|$)', text, re.I | re.S)
    zone = zone[1] if zone else ''
    plain = lambda s: re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', s)).strip()
    first = re.search(r'<p\b[^>]*class\s*=\s*["\'][^"\']*\btext_obisnuit2\b[^>]*>(.*?)</p>', zone, re.I | re.S)
    if first and plain(first[1]) and re.match(r'\s*<em\b', first[1], re.I):
        return plain(first[1])
    for paragraph in re.finditer(r'<p\b[^>]*class\s*=\s*["\'][^"\']*\btext_obisnuit2?\b[^>]*>(.*?)</p>', zone, re.I | re.S):
        quote = plain(paragraph[1])
        if quote:
            if len(quote) > 220:
                quote = quote[:220].rsplit(' ', 1)[0].rstrip(',.;:') + '…'
            return quote
    return ''


def build_block(name, text):
    """Blocul de categorie al unui articol. Intoarce (block, date) sau (None, motiv)."""
    canonical = re.search(r'<link[^>]*rel\s*=\s*["\']canonical["\'][^>]*href\s*=\s*["\']([^"\']+)', text, re.I)
    if not canonical or base_name(canonical[1]) != name.lower():
        return None, 'canonical diferit de numele fișierului (copie neterminată?)'
    h1 = re.search(r'<h1\b[^>]*class\s*=\s*["\'][^"\']*\bden_articol\b[^>]*>(.*?)</h1>', text, re.I | re.S)
    title = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', h1[1])).strip() if h1 else ''
    cell = date_cell(text)
    date = block_date(cell[2]) if cell else None
    quote = teaser_of(text)
    if not title or not cell or not date or not quote:
        return None, 'nu pot determina titlul, data sau textul de prezentare'
    td = cell[2].strip()
    english = '/en/' in urlsplit(canonical[1]).path.lower()
    home = 'https://neculaifantanaru.com/' + ('en/' if english else '')
    esc = lambda s: s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    block = (
        '    <table width="601" border="0">\n          <tbody><tr>\n'
        f'            <td><span class="den_articol"><a href="{canonical[1]}" class="linkMare">{esc(title)}</a></span></td>\n'
        '          </tr>\n          <tr>\n'
        f'            <td class="text_dreapta">{td}</td>\n'
        '          </tr>\n        </tbody>\n</table>\n'
        f'        <p class="text_obisnuit2"><em>{esc(quote)}</em></p>\n'
        '        <table width="552" border="0">\n          <tbody><tr>\n'
        f'            <td width="552"><div align="right" id="external2"><a href="{canonical[1]}">{"read more " if english else "citește mai departe "}</a>'
        f'<a href="{home}" title=""><img src="Arrow3_black_5x7.gif" alt="" width="5" height="7" class="arrow"></a></div>\n</td>\n'
        '          </tr>\n        </tbody>\n</table>\n\t\t<p class="text_obisnuit"></p>')
    return block, date


def list_region(content):
    start = content.index(CATEGORY_START)
    end = content.find(CATEGORY_END, start)
    end = end if end > 0 else len(content)
    div = re.compile(r'<div\s+align\s*=\s*["\']?justify["\']?\s*>', re.I).search(content, start, end)
    if not div:
        raise ValueError('lipsește <div align="justify"> după marcajul categoriei')
    return div.end(), end


def insert_block(content, block, date):
    pos, end = list_region(content)
    for existing in BLOCK_RE.finditer(content, pos, end):
        existing_date = block_date(existing[0])
        if existing_date is not None and existing_date <= date:
            pos = existing.start()
            break
        pos = existing.end()
    return content[:pos] + block + '\n    ' + content[pos:]


def remove_blocks(content, name):
    pos, end = list_region(content)
    for existing in reversed(list(BLOCK_RE.finditer(content, pos, end))):
        link = re.search(r'<a\b[^>]*class\s*=\s*["\'][^"\']*\blinkMare\b[^>]*>', existing[0], re.I)
        href = re.search(r'href\s*=\s*["\']([^"\']+)', link[0]) if link else None
        if href and base_name(href[1]) == name.lower():
            tail = re.match(r'[ \t]*\r?\n?[ \t]*', content[existing.end():])
            content = content[:existing.start()] + content[existing.end() + (tail.end() if tail else 0):]
            end = list_region(content)[1]
    return content


EN_MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
             'September', 'October', 'November', 'December']
DATE_TEXT_RE = re.compile(r'\bOn\s+([^\s\d,<]+)\s+(\d{1,2}),?\s+(\d{4})')


def en_date_text(ro_cell):
    """Data din celula RO („On Mai 07, 2008”) scrisa in engleza („On May 07, 2008”), cu ziua scrisa la fel."""
    m = DATE_TEXT_RE.search(ro_cell)
    if not m or m[1].lower() not in MONTH_NUM:
        return None
    return f'On {EN_MONTHS[MONTH_NUM[m[1].lower()] - 1]} {m[2]}, {m[3]}'


def set_article_date(text, new_date):
    """Inlocuieste data din celula text_dreapta a articolului."""
    cell = date_cell(text)
    if not cell:
        return text
    inner = DATE_TEXT_RE.sub(new_date, cell[2], count=1)
    return text[:cell.start(2)] + inner + text[cell.end(2):]


def set_list_date(content, name, new_date):
    """Data din blocul articolului `name` (linkul de titlu linkMare) dintr-o lista (ex. index.html)."""
    for link in re.finditer(r'<a\b[^>]*class\s*=\s*["\'][^"\']*\blinkMare\b[^>]*>', content, re.I):
        href = re.search(r'href\s*=\s*["\']([^"\']+)', link[0])
        if not href or base_name(href[1]) != name.lower():
            continue
        cell = TD_RE.search(content, link.end())
        if not cell or cell.start() - link.end() > 6000:
            return content
        inner = DATE_TEXT_RE.sub(new_date, cell[2], count=1)
        return content[:cell.start(2)] + inner + content[cell.end(2):]
    return content


def move_block_by_date(content, name, new_date, date):
    """Blocul existent al articolului `name`: ii schimba data si il muta la locul lui dupa data
    (pastreaza restul blocului neschimbat)."""
    pos, end = list_region(content)
    for existing in BLOCK_RE.finditer(content, pos, end):
        link = re.search(r'<a\b[^>]*class\s*=\s*["\'][^"\']*\blinkMare\b[^>]*>', existing[0], re.I)
        href = re.search(r'href\s*=\s*["\']([^"\']+)', link[0]) if link else None
        if href and base_name(href[1]) == name.lower():
            block = set_list_date(existing[0], name, new_date)
            return insert_block(remove_blocks(content, name), block, date)
    return content


def category_anchor(category_text, category):
    """Linkul spre categorie, exact asa cum apare in blocurile ei (cu title, class si rel)."""
    for anchor in re.finditer(r'<a\b[^>]*class\s*=\s*["\']external["\'][^>]*>.*?</a>', category_text, re.I | re.S):
        href = re.search(r'href\s*=\s*["\']([^"\']+)', anchor[0])
        if href and base_name(href[1]) == category.lower():
            return anchor[0]
    return None


def retarget_category(name, text, anchor):
    """Inlocuieste in text_dreapta linkul de categorie cu `anchor`."""
    cell = date_cell(text)
    if not cell:
        return text
    def swap(match):
        href = re.search(r'href\s*=\s*["\']([^"\']+)', match[0])
        if href and base_name(href[1]).endswith('.html') and base_name(href[1]) != name.lower():
            swap.done = True
            return anchor
        return match[0]
    swap.done = False
    inner = re.sub(r'<a\b[^>]*>.*?</a>', lambda m: swap(m) if not swap.done else m[0], cell[2], flags=re.S | re.I)
    return text[:cell.start(2)] + inner + text[cell.end(2):]


class Store:
    """Fisiere citite o data, modificate in memorie si scrise la final (doar daca nu s-au schimbat intre timp)."""
    def __init__(self):
        self.files = {}

    def get(self, path):
        if path not in self.files:
            original = path.read_bytes()
            self.files[path] = [original, original.decode('utf-8', errors='surrogateescape')]
        return self.files[path][1]

    def set(self, path, text):
        self.get(path)
        self.files[path][1] = text

    def flush(self, dry_run):
        for path, (original, text) in self.files.items():
            data = text.encode('utf-8', errors='surrogateescape')
            if data == original or dry_run:
                continue
            if path.read_bytes() != original:
                raise ValueError('Fișier modificat în timpul verificării; reîncercare la următoarea rulare: ' + path.name)
            temporary = path.with_suffix('.sync.tmp')
            temporary.write_bytes(data)
            os.replace(temporary, path)


def audit_categories(dry_run, excluded, apply_ids=()):
    """Fiecare articol RO/EN trebuie sa fie intr-o singura categorie, cea corecta.
    RO e sursa de adevar: categoria EN este corespondentul (din FLAGS) al categoriei RO a articolului.
    Recuperari sigure se fac automat; restul devin propuneri care se aplica doar dupa OK."""
    scanned = {lang: scan_language(BASE / lang) for lang in ('ro', 'en')}
    pairs = {lang: {n: flags(t) for n, (_, t) in scanned[lang][0].items()} for lang in scanned}
    cat_flags = {lang: {n: flags(t) for n, (_, t) in scanned[lang][1].items()} for lang in scanned}
    store, touched, issues, proposals = Store(), {}, [], []
    now = dt.datetime.now().timestamp()

    def counterpart(lang, name):
        other = 'en' if lang == 'ro' else 'ro'
        found = pairs[lang][name].get(other)
        if found and found in scanned[other][0] and pairs[other][found].get(lang) == name:
            return found
        return None

    def busy(path):
        return str(path.resolve()).lower() in excluded

    def note(path, name):
        touched.setdefault(path, []).append(name)

    recovered_ro = set()
    articles, categories, listed = scanned['ro']
    for name, (path, text) in articles.items():
        where = sorted(listed.get(name.lower(), ()))
        category = article_category(name, text)
        if where and category in categories and where != [category]:
            # RO e sursa de adevar: categoria din linia cu data a articolului decide unde trebuie sa fie blocul
            proposal_id = f'ro/{name}->{category}'
            if proposal_id in apply_ids and not busy(path) and not any(busy(categories[c][0]) for c in set(where) | {category}):
                for old in where:
                    if old != category:
                        old_path = categories[old][0]
                        store.set(old_path, remove_blocks(store.get(old_path), name))
                if category not in where:
                    block, date = build_block(name, text)
                    if block is None:
                        issues.append(f'ro/{name}: nu pot construi blocul pentru categoria {category} — {date}')
                        continue
                    store.set(categories[category][0], insert_block(store.get(categories[category][0]), block, date))
                    note(categories[category][0], name)
                continue
            proposals.append({'id': proposal_id, 'lang': 'ro', 'article': name, 'category': category,
                              'message': f'ro/{name} are linia cu data/categoria spre ro/{category}, dar blocul lui este în: {", ".join(where)}. '
                                         f'La OK: scot blocul din categoria greșită și îl las doar în ro/{category}. După aceea, EN se aliniază singur (propunere nouă pentru corespondentul EN, dacă e nevoie).'})
            continue
        if len(where) > 1:
            issues.append(f'ro/{name}: apare în {len(where)} categorii ({", ".join(where)}); un articol poate fi doar într-una singură')
        elif where and category not in categories:
            issues.append(f'ro/{name}: este în categoria {where[0]}, dar linia cu data din articol indică {category or "(nedeterminată)"}, care nu există în ro/')
        if where:
            continue
        twin = counterpart('ro', name)
        if twin is None:
            issues.append(f'ro/{name}: nu este în nicio categorie și nu are corespondent EN valid în FLAGS')
            continue
        if not scanned['en'][2].get(twin.lower()):
            continue  # nelistat in ambele limbi: pagina de site, nu un bloc pierdut
        if busy(path) or now - path.stat().st_mtime < GRACE_SECONDS:
            continue
        if category not in categories:
            issues.append(f'ro/{name}: lipsește din categorie, iar categoria {category or "(nedeterminată)"} nu există în ro/')
            continue
        if busy(categories[category][0]):
            issues.append(f'Recuperare amânată: ro/{category} are modificări nesalvate în editor')
            continue
        block, date = build_block(name, text)
        if block is None:
            issues.append(f'ro/{name}: lipsește din categoria {category}, dar nu pot recupera blocul — {date}')
            continue
        target = categories[category][0]
        store.set(target, insert_block(store.get(target), block, date))
        note(target, name)
        recovered_ro.add(name)

    articles, categories, listed = scanned['en']
    for name, (path, text) in articles.items():
        where = sorted(listed.get(name.lower(), ()))
        if len(where) > 1:
            issues.append(f'en/{name}: apare în {len(where)} categorii ({", ".join(where)}); un articol poate fi doar într-una singură')
        twin = counterpart('en', name)
        if twin is None:
            if not where:
                issues.append(f'en/{name}: nu este în nicio categorie și nu are corespondent RO valid în FLAGS')
            continue
        ro_text = scanned['ro'][0][twin][1]
        ro_category = article_category(twin, ro_text)
        if ro_category not in scanned['ro'][1]:
            continue
        en_category = cat_flags['ro'].get(ro_category, {}).get('en')
        if en_category not in categories:
            issues.append(f'ro/{ro_category}: FLAGS EN nu duce la o categorie existentă în en/ ({en_category or "lipsă"})')
            continue
        ro_listed = bool(scanned['ro'][2].get(twin.lower())) or twin in recovered_ro
        current = article_category(name, text)
        # Data: RO e sursa de adevar. Daca difera, la OK se ia data din articolul RO, scrisa in engleza.
        en_cell, ro_cell = date_cell(text), date_cell(ro_text)
        en_date = block_date(en_cell[2]) if en_cell else None
        ro_date = block_date(ro_cell[2]) if ro_cell else None
        new_date_text = en_date_text(ro_cell[2]) if (en_date and ro_date and en_date != ro_date) else None
        if where == [en_category] and current == en_category and not new_date_text:
            continue
        if not where and not ro_listed:
            continue  # pagina de site
        anchor = category_anchor(categories[en_category][1], en_category)
        wrong_place = where != [en_category] or current != en_category
        proposal_id = f'en/{name}->{en_category}' + ('+data' if new_date_text else '')
        date_note = (f' Data EN ({en_date:%d.%m.%Y}) diferă de data RO ({ro_date:%d.%m.%Y}): la OK preiau data din RO, '
                     f'în engleză („{new_date_text}”), în articol, în blocul din categorie și în index.') if new_date_text else ''
        automatic = not where and current == en_category and not new_date_text
        if not automatic and proposal_id not in apply_ids:
            if where == [en_category] and current != en_category:
                message = (f'en/{name} (corespondent RO: ro/{twin}, din ro/{ro_category}): blocul este corect în en/{en_category}, '
                           f'dar linkul de categorie din articol arată {current}. La OK: corectez linkul de categorie din articol '
                           f'spre en/{en_category}.' + date_note)
            elif wrong_place:
                message = (f'en/{name} (corespondent RO: ro/{twin}, din ro/{ro_category}) trebuie să fie în en/{en_category}, '
                           f'dar acum este în: {", ".join(where) or "nicio categorie"}'
                           + (f'; linkul de categorie din articol arată {current}' if current != en_category else '')
                           + '. La OK: corectez linkul de categorie din articol, scot blocul din categoria greșită și îl adaug în categoria corectă.'
                           + date_note)
            else:
                message = f'en/{name} (corespondent RO: ro/{twin}) este în categoria corectă, en/{en_category}.' + date_note
            # Detalii pentru fereastra editorului: ce e greșit (roșu) și ce va fi pus (verde), luat din RO.
            kind = ('categorie+data' if new_date_text else 'categorie') if wrong_place else 'data'
            en_date_now = DATE_TEXT_RE.search(en_cell[2])[0] if en_cell and DATE_TEXT_RE.search(en_cell[2]) else ''
            proposals.append({'id': proposal_id, 'lang': 'en', 'article': name, 'category': en_category, 'message': message,
                              'kind': kind, 'ro_article': twin, 'ro_category': ro_category,
                              'category_line': current or '', 'category_listed': list(where), 'category_correct': en_category,
                              'date_current': en_date_now if new_date_text else '', 'date_correct': new_date_text or ''})
            continue
        if busy(path) or any(busy(categories[c][0]) for c in list(where) + [en_category]):
            issues.append(f'Corectare amânată: en/{name} sau categoria lui are modificări nesalvate în editor')
            continue
        if not automatic or now - path.stat().st_mtime >= GRACE_SECONDS:
            new_text = text
            if current != en_category:
                new_text = retarget_category(name, new_text, anchor or f'<a href="https://neculaifantanaru.com/en/{en_category}" class="external" rel="category tag">{en_category}</a>')
            if new_date_text:
                new_text = set_article_date(new_text, new_date_text)
            if new_text != text:
                store.set(path, new_text)
            for old in where:
                if old != en_category:
                    old_path = categories[old][0]
                    store.set(old_path, remove_blocks(store.get(old_path), name))
            target = categories[en_category][0]
            if en_category not in where:
                block, date = build_block(name, new_text)
                if block is None:
                    issues.append(f'en/{name}: nu pot construi blocul pentru categoria {en_category} — {date}')
                    continue
                store.set(target, insert_block(store.get(target), block, date))
                note(target, name)
            elif new_date_text:
                # blocul e deja in categoria corecta: ii schimb doar data si il pun la locul lui
                store.set(target, move_block_by_date(store.get(target), name, new_date_text, ro_date))
            if new_date_text:
                index_path = BASE / 'en' / 'index.html'
                if index_path.is_file():
                    store.set(index_path, set_list_date(store.get(index_path), name, new_date_text))
    store.flush(dry_run)
    changes = [{'file': str(path), 'articles': names, 'recovered': True} for path, names in touched.items()]
    return changes, issues, proposals


def listed_names(content):
    """Numele articolelor listate intr-o pagina de categorie (linkurile de titlu a.linkMare)."""
    start = content.find(CATEGORY_START)
    if start < 0:
        return set()
    end = content.find(CATEGORY_END, start)
    names = set()
    for link in re.finditer(r'<a\b[^>]*class\s*=\s*["\'][^"\']*\blinkMare\b[^>]*>', content[start:end if end > 0 else len(content)], re.I):
        href = re.search(r'href\s*=\s*["\']([^"\']+)', link[0])
        if href:
            names.add(href[1].split('/')[-1])
    return names


LOG = Path(__file__).with_name('verificari-log.txt')
SEEN = Path(__file__).with_name('category-sync-seen.json')
KIND_TITLE = {'categorie': 'Categorie greșită', 'categorie+data': 'Categorie și dată greșite', 'data': 'Dată greșită'}


def describe(p):
    """O propunere pe un rand: tipul, perechea, ce e gresit si ce se pune."""
    if not p.get('kind'):
        return p.get('message', p['id'])
    parts = [f"[{KIND_TITLE.get(p['kind'], p['kind'])}] en/{p['article']} ↔ ro/{p.get('ro_article', '?')}"]
    if p['kind'] != 'data':
        now = p.get('category_line') or '?'
        listed = ', '.join(p.get('category_listed') or []) or 'nicio categorie'
        parts.append(f"categorie: linkul din articol {now}, bloc în {listed} → en/{p['category_correct']}")
    if p.get('date_correct'):
        parts.append(f"dată: {p.get('date_current') or '?'} → {p['date_correct']}")
    return '; '.join(parts)


def write_log(result, apply_ids):
    """Jurnalul verificarilor: doar ce s-a schimbat fata de rularea anterioara (nu repeta aceleasi randuri)."""
    try:
        seen = json.loads(SEEN.read_text(encoding='utf-8')) if SEEN.exists() else {}
    except ValueError:
        seen = {}
    old_props, old_issues = seen.get('proposals', {}), set(seen.get('issues', []))
    now_props = {p['id']: describe(p) for p in result.get('proposals', [])}
    now_issues = set(result.get('issues', []))
    stamp = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    lines = []
    for pid, text in now_props.items():
        if pid not in old_props:
            lines.append(f'{stamp}  GĂSIT      {text}')
    for pid, text in old_props.items():
        if pid not in now_props:
            how = 'REPARAT    (OK, aplică)' if pid in apply_ids else 'REZOLVAT   (fără OK: fișier corectat sau înlocuit)'
            lines.append(f'{stamp}  {how}  {text}')
    for change in result.get('changes', []):
        verb = 'RECUPERAT ' if change.get('recovered') else 'ACTUALIZAT'
        lines.append(f"{stamp}  {verb}  {', '.join(change.get('articles', []))} → {change.get('file')}"
                     + (' (linkul și blocul lipseau din categorie)' if change.get('recovered') else ''))
    for issue in sorted(now_issues - old_issues):
        lines.append(f'{stamp}  PROBLEMĂ   {issue}')
    for issue in sorted(old_issues - now_issues):
        lines.append(f'{stamp}  PROBLEMĂ REZOLVATĂ  {issue}')
    if lines:
        with LOG.open('a', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
    SEEN.write_text(json.dumps({'proposals': now_props, 'issues': sorted(now_issues)}, ensure_ascii=False, indent=1), encoding='utf-8')


def sync(dry_run=False, excluded=(), apply_ids=()):
    tools = runpy.run_path(str(PIPELINE / 'Pasul 5 - Duce fiecare articol in fisierul categorii din care face parte si apoi in index FINAL.py'))
    tools['merge_listing'].__globals__['DEBUG'] = False
    issues, changes = [], []
    maps = {'ro': {}, 'en': {}}
    categories = {}
    excluded = {str(Path(p).resolve()).lower() for p in excluded}
    for lang in maps:
        folder = BASE / lang
        if not folder.is_dir():
            raise ValueError('Folder lipsă: ' + str(folder))
        for path in sorted(folder.glob('*.html')):
            text = read(path)
            canonical_issue = canonical_identity_issue(lang, path.name, text)
            if canonical_issue:
                issues.append(canonical_issue)
            pair = flags(text)
            if pair:
                maps[lang][path.name] = pair
            if lang == 'ro' and '<!-- ARTICOL CATEGORIE START -->' in text and path.name != 'index.html':
                categories[path.name] = pair
        listing = folder / ('lista categorii.txt' if lang == 'ro' else 'lista categorii_en.txt')
        if listing.exists():
            for name in sorted(set(re.findall(r'[\w-]+\.html', read(listing)))):
                if not (folder / name).is_file():
                    issues.append(f'Categorie lipsă: {folder / name}')
    issues.extend(flag_pair_issues(maps, lambda lang, name: (BASE / lang / name).is_file()))
    for ro_name, pair in categories.items():
        en_name = pair.get('en')
        if not en_name or maps['en'].get(en_name, {}).get('ro') != ro_name:
            continue
        target = BASE / 'en' / en_name
        if str(target.resolve()).lower() in excluded:
            issues.append(f'Sincronizare amânată: {en_name} are modificări nesalvate în editor')
            continue
        try:
            original = target.read_bytes()
            content = original.decode('utf-8', errors='surrogateescape')
            existing = listed_names(content)  # = listing_blocks din Pasul 5, dar ~150x mai rapid
            ro_text = read(BASE / 'ro' / ro_name)
            section = re.search(r'<!-- ARTICOL CATEGORIE START -->(.*?)<!-- ARTICOL CATEGORIE FINAL -->', ro_text, re.S)
            if not section:
                continue
            soup = BeautifulSoup(section[1], 'html.parser')
            additions = []
            for link in soup.select('.den_articol a[href]'):
                ro_article = link['href'].split('/')[-1]
                en_article = maps['ro'].get(ro_article, {}).get('en')
                if not en_article or en_article in existing:
                    continue
                if maps['en'].get(en_article, {}).get('ro') != ro_article:
                    continue  # Never substitute an old translation left in copied FLAGS.
                article = tools['extract_article_data'](read(BASE / 'en' / en_article))
                if not article or article['url'].split('/')[-1] != en_article:
                    raise ValueError('Canonical EN neconcordant: ' + en_article)
                if article['category_url'].split('/')[-1] != en_name:
                    issues.append(f'{en_article}: categoria din articol diferă de {en_name}; verificare manuală')
                    continue
                table = link.find_parent('table')
                date = re.search(r'On\s+(\w+)\s+(\d{1,2}),\s*(\d{4})', table.get_text(' '))
                if not date or date[1].lower() not in MONTHS:
                    raise ValueError('Dată RO invalidă: ' + ro_article)
                expected = dt.datetime(int(date[3]), MONTHS.index(date[1].lower()) + 1, int(date[2]))
                if expected != article['date_obj']:
                    issues.append(f'{en_article}: data EN {article["date"]} diferă de RO {expected:%Y-%m-%d}; verificare manuală')
                    continue
                additions.append(article)
                existing.add(en_article)
            if not additions:
                continue
            updated = tools['merge_listing'](content, additions)
            if not dry_run:
                if target.read_bytes() != original:
                    raise ValueError('Fișier modificat în timpul sincronizării; reîncercare la următoarea verificare')
                temporary = target.with_suffix('.sync.tmp')
                temporary.write_bytes(updated.encode('utf-8', errors='surrogateescape'))
                os.replace(temporary, target)
            changes.append({'file': str(target), 'articles': [a['url'].split('/')[-1] for a in additions]})
        except Exception as error:
            issues.append(f'{en_name}: {error}')
    proposals = []
    try:
        recovered, recover_issues, proposals = audit_categories(dry_run, excluded, set(apply_ids))
        changes.extend(recovered)
        issues.extend(recover_issues)
    except Exception as error:
        issues.append(f'Verificarea categoriilor a eșuat: {error}')
    return {'ok': True, 'time': dt.datetime.now().isoformat(), 'dry_run': dry_run,
            'checked': {k: len(v) for k, v in maps.items()}, 'changes': changes, 'issues': sorted(set(issues)), 'proposals': proposals}

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--exclude', default='[]')
    parser.add_argument('--apply', default='[]')
    args = parser.parse_args()
    # One audit at a time across Apache and the scheduled task.
    import msvcrt
    import time
    lock = open(str(REPORT) + '.lock', 'a+b')
    lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
    # O verificare simpla renunta daca alta ruleaza deja (raportul ei e la fel de bun).
    # O cerere de APLICARE (OK din fereastra) asteapta sa termine verificarea curenta (max 3 min),
    # altfel verificarile automate ar putea tine lacatul ocupat si OK-ul n-ar ajunge niciodata.
    wants_apply = bool(json.loads(args.apply))
    deadline = time.time() + (180 if wants_apply else 0)
    locked = False
    while True:
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            locked = True
            break
        except OSError:
            if time.time() >= deadline:
                break
            time.sleep(0.5)
    if not locked:
        result = {'ok': True, 'busy': True}
    else:
        try:
            result = sync(args.dry_run, json.loads(args.exclude), json.loads(args.apply))
            if not args.dry_run:
                try:
                    write_log(result, set(json.loads(args.apply)))
                except Exception as error:  # jurnalul nu are voie sa opreasca verificarea
                    result.setdefault('issues', []).append(f'Jurnalul nu a putut fi scris: {error}')
                temp = REPORT.with_suffix('.tmp')
                temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
                os.replace(temp, REPORT)
        except Exception as error:
            result = {'ok': False, 'error': str(error)}
        finally:
            lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
    lock.close()
    if sys.stdout:
        print(json.dumps(result, ensure_ascii=True))
