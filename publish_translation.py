"""Resumeable Translate -> RO design/publication -> EN design/publication.

RO se publica PRIMUL: scripturile de design EN (imaginea articolului si a cardului din
categorie) citesc imaginea din fisierul RO echivalent din Principal 2022/ro, iar acel fisier
are imaginea abia dupa ce a trecut prin designul RO. Fisierele EN asteapta intre timp intr-un
folder de rezerva, ca sa nu se amestece cu cele RO in c:/Folder1/fisiere_html.
"""
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import unquote, urlparse
from bs4 import BeautifulSoup
from legatura_ftp import connect, remote_directory, upload_verified

APP = Path(__file__).resolve().parent
BASE = Path(r'E:\Carte\BB\17 - Site Leadership\alte\Ionel Balauta\Aryeht\Task 1 - Traduce tot site-ul\Doar Google Web\Andreea\Meditatii')
PIPE = BASE / '2023/Iulia Python'
SITE = Path(r'E:\Carte\BB\17 - Site Leadership\Principal')
NEW_SITE = SITE.with_name('Principal 2022')
INPUT = Path(r'C:\Folder1\fisiere_html')
OUTPUT = Path(r'C:\Folder1\fisiere_gata')
PENDING = APP / 'translation-publication.json'
HOLD_ROOT = APP / 'translation-hold'

def log(message):
    print(message, flush=True)

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def save(state):
    temporary = PENDING.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(PENDING)

def html_files(folder):
    return {p.name:p for p in folder.glob('*.html')}

def safe_name(url):
    name = unquote(urlparse(url).path).split('/')[-1]
    if not name or Path(name).name != name or not name.lower().endswith('.html') or any(c in name for c in '\\:\r\n'):
        raise RuntimeError('Nume HTML invalid: ' + name)
    return name

def category(path):
    text = path.read_text(encoding='utf-8-sig')
    soup = BeautifulSoup(text, 'html.parser')
    node = soup.select_one('td.text_dreapta')
    anchor = node.find('a', href=True) if node else None
    if not anchor:
        dates = re.search(r'<!--STARTDATES-->(.*?)<!--FINNISHDATES-->', text, re.S|re.I)
        if dates:
            anchor = BeautifulSoup(dates[1], 'html.parser').select_one('a.color-green[href], a[rel~=category][href]')
    if not anchor:
        raise RuntimeError('Nu pot identifica categoria articolului: ' + str(path))
    return safe_name(anchor['href'])

def initialize(run):
    status = json.loads((run/'stare.json').read_text(encoding='utf-8'))
    save(dict(run=str(run), phase='prepare', articles=status['fisiere'], complete=False))
    en = set(status['fisiere_en'])
    en.update(category(SITE/'en'/name) for name in status['fisiere_en'])
    actual = html_files(INPUT)
    if set(actual) - en - {'index.html'} or not en.issubset(actual):
        raise RuntimeError('Fișierele EN de export nu corespund rulării. Nu public nimic; verifică fisiere_html.')
    ro = set(status['fisiere'])
    ro.update(category(SITE/'ro'/name) for name in status['fisiere'])
    for name in ro:
        if not (SITE/'ro'/name).is_file():
            raise RuntimeError('Lipsește corespondentul RO: ' + name)
    state = dict(run=str(run), order='ro-first', phase='hold-en', design_done=False,
                 en_files=sorted(actual), files=sorted(actual), ro_files=sorted(ro),
                 articles=status['fisiere'], published=[], complete=False)
    save(state)
    return state

def hold_en(state):
    """Muta fisierele EN din fisiere_html intr-un folder de rezerva, ca sa se poata proiecta intai RO."""
    expected = set(state['en_files'])
    hold = Path(state.get('hold') or HOLD_ROOT / Path(state['run']).name)
    hold.mkdir(parents=True, exist_ok=True)
    state['hold'] = str(hold)
    if set(html_files(hold)) - expected:
        raise RuntimeError('Folderul de rezerva EN contine fisiere straine: ' + str(hold))
    if html_files(OUTPUT):
        raise RuntimeError('fisiere_gata conține HTML-uri din altă lucrare. Mută-le înainte de publicare.')
    hashes = state.get('en_hashes', {})
    for name in sorted(expected):
        src, dest = INPUT/name, hold/name
        if src.exists():
            if dest.exists() and digest(dest) != digest(src):
                raise RuntimeError('Fișier EN diferit în folderul de rezervă: ' + name)
            if not dest.exists():
                shutil.copyfile(src, dest)
            if digest(dest) != digest(src):
                raise RuntimeError('Copiere locală incompletă (rezervă EN): ' + name)
            hashes[name] = digest(dest)
            state['en_hashes'] = hashes
            save(state)
            src.unlink()
        elif not dest.exists():
            raise RuntimeError('Lipsește fișierul EN de export: ' + name)
    state['en_hashes'] = {name: digest(hold/name) for name in expected}
    state['phase'] = 'stage-ro'
    save(state)
    log('Fișierele EN au fost puse deoparte; proiectez și public întâi RO, apoi EN.')

def restore_en(state):
    """Aduce fisierele EN inapoi in fisiere_html dupa ce RO a fost publicat."""
    expected = set(state['en_files'])
    hold = Path(state['hold'])
    hashes = state['en_hashes']
    if set(html_files(INPUT)) - expected or html_files(OUTPUT):
        raise RuntimeError('Folderele de lucru conțin fișiere străine. Le păstrez.')
    for name in sorted(expected):
        src, dest = hold/name, INPUT/name
        if src.exists():
            if digest(src) != hashes[name]:
                raise RuntimeError('Fișier EN modificat în folderul de rezervă: ' + name)
            if not dest.exists():
                shutil.copyfile(src, dest)
        if not dest.exists() or digest(dest) != hashes[name]:
            raise RuntimeError('Nu pot readuce fișierul EN în fisiere_html: ' + name)
    state.update(phase='en', files=sorted(expected), design_done=False, design_started=False,
                 cleanup=False, published=[])
    save(state)
    for name in expected:
        (hold/name).unlink(missing_ok=True)
    try:
        hold.rmdir()
    except OSError:
        pass
    log('RO este publicat; proiectez și public acum EN.')

def validate_outputs(state):
    expected = set(state['files'])
    actual = html_files(OUTPUT)
    if set(actual) != expected:
        raise RuntimeError('Design incomplet sau fișiere străine în fisiere_gata. Lipsesc: ' + ', '.join(sorted(expected-set(actual))) + '; în plus: ' + ', '.join(sorted(set(actual)-expected)))
    for name, path in actual.items():
        source = BeautifulSoup(path.read_text(encoding='utf-8-sig'), 'html.parser')
        if not source.html or not source.body or not source.title or not source.get_text(strip=True):
            raise RuntimeError('HTML rezultat incomplet: ' + name)
        canonical = source.select_one('link[rel~=canonical][href]')
        if not canonical or safe_name(canonical['href']) != name:
            raise RuntimeError('Canonical incorect în rezultatul designului: ' + name)
        is_en = urlparse(canonical['href']).path.startswith('/en/')
        if is_en != (state['phase']=='en'):
            raise RuntimeError('Limba canonical nu corespunde: ' + name)
    return actual

def run_design(state):
    if state.get('design_started'):
        # Retry only the outputs belonging to this failed phase; preserve unrelated files.
        for name, path in html_files(OUTPUT).items():
            if name not in state['files']:
                raise RuntimeError('Fișier străin în fisiere_gata: ' + name)
            path.unlink()
    elif html_files(OUTPUT):
        raise RuntimeError('fisiere_gata conține HTML-uri din altă lucrare. Mută-le înainte de publicare.')
    if set(html_files(INPUT)) != set(state['files']):
        raise RuntimeError('Lista din fisiere_html s-a schimbat. Publicarea a fost oprită.')
    state['input_hashes'] = {name:digest(INPUT/name) for name in state['files']}
    state['design_started'] = True
    save(state)
    language = state['phase']
    bat = BASE/'Sedinta 18/ENGLEZA'/f'bebe-metoda-1-{language}-NOU.bat'
    log(f'\nDesign {language.upper()}: rulez toate scripturile din BAT…')
    env = dict(os.environ, EDITOR_AUTOMATION='1', PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
    result = subprocess.run(['cmd.exe','/d','/c',str(bat)], cwd=bat.parent, env=env, stdin=subprocess.DEVNULL)
    if result.returncode:
        raise RuntimeError(f'Design {language.upper()} a eșuat (cod {result.returncode}). Fișierele sunt păstrate.')
    actual = validate_outputs(state)
    state['output_hashes'] = {name:digest(path) for name,path in actual.items()}
    state['design_done'] = True
    save(state)

def publish_phase(state):
    if not state['design_done']:
        run_design(state)
    phase = state['phase']
    target = NEW_SITE/phase
    if not target.is_dir():
        raise RuntimeError('Lipsește folderul destinație: ' + str(target))
    ftp = connect()
    try:
        remote_directory(ftp, phase)
        for name in state['files']:
            path = OUTPUT/name
            if not path.exists() or digest(path) != state['output_hashes'][name]:
                raise RuntimeError('Fișierul rezultat a fost modificat sau lipsește: ' + name)
            if name in state['published']:
                continue
            log(f'Public {phase.upper()}: {name}')
            tmp = target/(name+'.editor-publishing')
            shutil.copyfile(path, tmp)
            if digest(tmp) != digest(path):
                raise RuntimeError('Copiere locală incompletă: ' + name)
            tmp.replace(target/name)
            upload_verified(ftp, path)
            state['published'].append(name)
            save(state)
    finally:
        ftp.close()
    state['cleanup'] = True
    save(state)

def cleanup(state):
    # Delete only the exact files whose successful publication was recorded.
    if set(state['published']) != set(state['files']):
        raise RuntimeError('Refuz curățarea: publicarea nu este completă.')
    for folder, key in [(INPUT,'input_hashes'), (OUTPUT,'output_hashes')]:
        for name in state['files']:
            path = folder/name
            if path.exists() and digest(path) != state[key][name]:
                raise RuntimeError('Fișier modificat după procesare; îl păstrez: ' + str(path))
    for folder in [INPUT,OUTPUT]:
        for name in state['files']:
            path = folder/name
            if path.exists():
                path.unlink()

def publish(state):
    if state['phase']=='prepare':
        state=initialize(Path(state['run']))
    while not state.get('complete'):
        if state.get('phase') == 'hold-en':
            hold_en(state)
        if state.get('phase') == 'restore-en':
            restore_en(state)
        if state.get('phase') == 'stage-ro':
            expected = set(state['ro_files'])
            if set(html_files(INPUT))-expected or html_files(OUTPUT):
                raise RuntimeError('Folderele de lucru conțin fișiere străine. Le păstrez.')
            for name in expected:
                src = SITE/'ro'/name
                dest = INPUT/name
                if dest.exists() and digest(src)!=digest(dest):
                    raise RuntimeError('Fișier RO diferit în folderul de lucru: ' + name)
                shutil.copyfile(src,dest)
            state.update(phase='ro',files=sorted(expected),design_done=False,design_started=False,cleanup=False,published=[])
            save(state)
        if not state.get('cleanup'):
            publish_phase(state)
        cleanup(state)
        if state.get('order') == 'ro-first':
            if state['phase'] == 'ro':
                state.update(phase='restore-en', cleanup=False)
            else:
                state['complete'] = True
        elif state['phase']=='en':
            state.update(phase='stage-ro',cleanup=False)
        else:
            state['complete']=True
        save(state)
    log('GATA: designul nou și publicarea RO + EN s-au încheiat. Copiile locale și FTP au fost verificate; fișierele procesate din folderele de lucru au fost șterse.')

def main():
    OUTPUT.mkdir(parents=True,exist_ok=True)
    INPUT.mkdir(parents=True,exist_ok=True)
    with (APP/'translation-publication.lock').open('a+b') as lock:
        lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            raise RuntimeError('O traducere/publicare rulează deja.') from None
        state = json.loads(PENDING.read_text(encoding='utf-8')) if PENDING.exists() else None
        if state and not state.get('complete'):
            log('Reiau publicarea neterminată, fără o nouă traducere.')
            publish(state)
            return
        if html_files(INPUT) or html_files(OUTPUT):
            raise RuntimeError('Folderele fisiere_html/fisiere_gata conțin HTML-uri dintr-o lucrare anterioară. Mută-le înainte de o traducere nouă; nu le șterg automat.')
        ftp=connect()
        try:
            for language in ('en','ro'): remote_directory(ftp,language)
        finally: ftp.close()
        before=set((PIPE/'_traducere_claude').glob('run_*'))
        env=dict(os.environ,TRADUCE_TOT_MARCAJ='',PYTHONIOENCODING='utf-8')
        result=subprocess.run([sys.executable,'-u',str(PIPE/'TRADUCE TOT - automat.py')],cwd=PIPE,env=env,stdin=subprocess.DEVNULL)
        if result.returncode:
            raise RuntimeError('Traducerea a eșuat. Publicarea nu a fost pornită.')
        runs=set((PIPE/'_traducere_claude').glob('run_*'))-before
        if len(runs)!=1:
            raise RuntimeError('Nu pot identifica fără ambiguitate rularea de publicat.')
        publish(initialize(runs.pop()))

if __name__=='__main__':
    code=0
    try: main()
    except Exception as exc:
        log('EROARE: '+str(exc)); code=1
        log('Publicarea s-a oprit. Fișierele nepublicate sunt păstrate. După remediere, apasă Translate pentru reluare.')
    print(f'[[SFARSIT cod={code}]]',flush=True)
    sys.exit(code)
