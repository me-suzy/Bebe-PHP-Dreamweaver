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
from bs4 import BeautifulSoup

BASE = Path(r'E:\Carte\BB\17 - Site Leadership\Principal')
PIPELINE = Path(r'E:\Carte\BB\17 - Site Leadership\alte\Ionel Balauta\Aryeht\Task 1 - Traduce tot site-ul\Doar Google Web\Andreea\Meditatii\2023\Iulia Python')
REPORT = Path(__file__).with_name('category-sync-report.json')
MONTHS = ['ianuarie','februarie','martie','aprilie','mai','iunie','iulie','august','septembrie','octombrie','noiembrie','decembrie']

def read(path):
    return path.read_bytes().decode('utf-8', errors='surrogateescape')

def flags(text):
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


def sync(dry_run=False, excluded=()):
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
            existing = {b['url'].split('/')[-1] for b in tools['listing_blocks'](content)}
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
    return {'ok': True, 'time': dt.datetime.now().isoformat(), 'dry_run': dry_run,
            'checked': {k: len(v) for k, v in maps.items()}, 'changes': changes, 'issues': sorted(set(issues))}

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--exclude', default='[]')
    args = parser.parse_args()
    # One audit at a time across Apache and the scheduled task.
    import msvcrt
    lock = open(str(REPORT) + '.lock', 'a+b')
    lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        result = {'ok': True, 'busy': True}
    else:
        try:
            result = sync(args.dry_run, json.loads(args.exclude))
            if not args.dry_run:
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
