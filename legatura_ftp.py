"""FTP connection and verified uploads. Credentials are encrypted by Windows DPAPI."""
import ctypes
import ftplib
import hashlib
import io
import json
import os
from pathlib import Path
import ssl
import socket
import uuid

class SiteFTP(ftplib.FTP_TLS):
    def auth(self):
        # The hosting provider presents its server certificate on the site's FTP alias.
        response = self.voidcmd('AUTH TLS')
        self.sock = self.context.wrap_socket(self.sock, server_hostname='core13.spatiul.ro')
        self.file = self.sock.makefile(mode='r', encoding=self.encoding)
        return response

    def ntransfercmd(self, cmd, rest=None):
        connection, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            connection = self.context.wrap_socket(connection, server_hostname='core13.spatiul.ro', session=self.sock.session)
        return connection, size

CONFIG = Path(os.environ['LOCALAPPDATA']) / 'MiniDreamweaver' / 'ftp.dpapi'

class Blob(ctypes.Structure):
    _fields_ = [('size', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_ubyte))]

def protect(data, decrypt=False):
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    api = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not api(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)

def configure(host, user, password):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_bytes(protect(json.dumps(dict(host=host,user=user,password=password)).encode()))

def connect():
    if not CONFIG.exists():
        raise RuntimeError('Lipsește configurația FTP pentru acest cont Windows.')
    try:
        settings = json.loads(protect(CONFIG.read_bytes(), True))
    except Exception:
        raise RuntimeError('Configurația FTP nu poate fi decriptată în acest cont Windows.') from None
    ftp = SiteFTP(context=ssl.create_default_context(), timeout=60)
    try:
        # IPv6 control works at this host but its passive data ports time out.
        ftp.connect(socket.gethostbyname(settings['host']), 21)
        ftp.login(settings['user'], settings['password'])
        ftp.prot_p()
        ftp.set_pasv(True)
        return ftp
    except ftplib.error_perm as exc:
        ftp.close()
        if str(exc).startswith('530'):
            raise RuntimeError('Autentificare FTP refuzată. Verifică utilizatorul și parola; este posibil să fi fost schimbate.') from None
        raise RuntimeError('FTP a refuzat accesul: ' + str(exc)) from None
    except Exception as exc:
        ftp.close()
        raise RuntimeError('Conexiunea FTP securizată a eșuat: ' + str(exc)) from None

def remote_directory(ftp, language):
    path = '/public_html/en' if language == 'en' else '/public_html'
    ftp.cwd(path)
    return path

def upload_verified(ftp, path):
    """Verify bytes before replacing the public file; failure leaves local output intact."""
    path = Path(path)
    data = path.read_bytes()
    temporary = '.' + path.name + '.' + uuid.uuid4().hex + '.upload'
    try:
        ftp.storbinary('STOR ' + temporary, io.BytesIO(data))
        digest = hashlib.sha256()
        ftp.retrbinary('RETR ' + temporary, digest.update)
        if digest.digest() != hashlib.sha256(data).digest():
            raise RuntimeError('Verificarea FTP a eșuat: ' + path.name)
        ftp.rename(temporary, path.name)
    except Exception:
        try:
            ftp.delete(temporary)
        except Exception:
            pass
        raise

if __name__ == '__main__':
    ftp = connect()
    try:
        for language in ('en', 'ro'):
            remote_directory(ftp, language)
        print('Conexiunea FTP și ambele directoare sunt accesibile.')
    finally:
        ftp.close()
