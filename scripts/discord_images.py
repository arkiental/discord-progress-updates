"""Image-only Discord outbox; stdlib only, no credential logging."""
import argparse
import ctypes
from ctypes import wintypes
import getpass
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
SECRET = ROOT / '.local' / 'webhook.dpapi'
LIMIT = 20 * 1024 * 1024


class SafeError(Exception):
    pass


def dpapi(data, decrypt=False):
    if os.name != 'nt':
        raise SafeError('Encrypted credentials require Windows; use DISCORD_PROGRESS_WEBHOOK here.')
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, target = Blob(len(data), buf), Blob()
    library = ctypes.WinDLL('crypt32', use_last_error=True)
    fn = library.CryptUnprotectData if decrypt else library.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise SafeError('Credential encryption/decryption failed for this Windows account.')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def validate_url(value):
    if not re.fullmatch(r'https://discord\.com/api(?:/v\d+)?/webhooks/\d+/[A-Za-z0-9_-]+', value):
        raise SafeError('Expected an HTTPS Discord webhook URL without query parameters.')
    return value


def configure():
    value = os.environ.get('DISCORD_PROGRESS_WEBHOOK') or getpass.getpass('Discord webhook URL (hidden): ')
    encrypted = dpapi(validate_url(value.strip()).encode())
    SECRET.parent.mkdir(parents=True, exist_ok=True)
    SECRET.write_bytes(encrypted)
    return {'status': 'configured', 'storage': 'Windows account-encrypted local credential'}


def webhook():
    value = os.environ.get('DISCORD_PROGRESS_WEBHOOK')
    if not value:
        if not SECRET.is_file():
            raise SafeError('Webhook is not configured. Set DISCORD_PROGRESS_WEBHOOK or run --configure.')
        value = dpapi(SECRET.read_bytes(), decrypt=True).decode()
    return validate_url(value.strip())


def image_data(path):
    source = Path(path)
    if not source.is_file():
        raise SafeError('An image file is missing; no update was sent.')
    if source.stat().st_size > LIMIT:
        raise SafeError('Image exceeds the helper upload ceiling; reduce it before sending.')
    with source.open('rb') as stream:
        data = stream.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise SafeError('Image exceeds the helper upload ceiling.')
    extension = source.suffix.lower()
    checks = {
        '.png': ('image/png', data.startswith(b'\x89PNG\r\n\x1a\n')),
        '.jpg': ('image/jpeg', data.startswith(b'\xff\xd8\xff')),
        '.jpeg': ('image/jpeg', data.startswith(b'\xff\xd8\xff')),
        '.gif': ('image/gif', data[:6] in (b'GIF87a', b'GIF89a')),
        '.webp': ('image/webp', data[:4] == b'RIFF' and data[8:12] == b'WEBP'),
    }
    if extension not in checks or not checks[extension][1]:
        raise SafeError('Unsupported image type or mismatched image signature; no update was sent.')
    return extension, checks[extension][0], data



import hashlib
import sqlite3
import subprocess
import math

DEFAULT = Path.home() / 'Documents' / 'Codex' / 'discord-image-outbox'
LIMIT = 8 * 1024 * 1024
EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}


def image_request(path):
    extension, mime, data = image_data(path)
    if len(data) > LIMIT:
        raise SafeError('Image exceeds 8 MiB.')
    payload = {'allowed_mentions': {'parse': []}, 'attachments': [{'id': 0, 'filename': 'image' + extension}]}
    boundary = 'image-' + uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\nContent-Type: application/json\r\n\r\n'.encode()
            + json.dumps(payload).encode()
            + f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="files[0]"; filename="image{extension}"\r\nContent-Type: {mime}\r\n\r\n'.encode()
            + data + f'\r\n--{boundary}--\r\n'.encode())
    return hashlib.sha256(data).hexdigest(), body, 'multipart/form-data; boundary=' + boundary


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def transmit(url, body, content_type):
    request = urllib.request.Request(url + '?wait=true', data=body, method='POST', headers={
        'Content-Type': content_type, 'User-Agent': 'DiscordImageOutbox/1.0'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            result = json.loads(response.read())
        if result.get('id') and len(result.get('attachments', [])) == 1 and not result.get('content'):
            return 'sent', str(result['id']), 0
        return 'held', 'Unconfirmed response; inspect Discord before resubmitting.', 0
    except urllib.error.HTTPError as error:
        code = error.code
        delay = 0
        if code == 429:
            try:
                delay = float(json.loads(error.read()).get('retry_after'))
            except Exception:
                delay = 0
        error.close()
        if code == 429 and math.isfinite(delay) and delay > 0:
            return 'retry', 'Rate limited', delay + 1
        if code in (401, 403, 404):
            return 'auth', 'Webhook rejected; check credential/destination.', 0
        return ('held' if code >= 500 or code == 429 else 'failed'), f'HTTP {code}', 0
    except Exception:
        return 'held', 'Network/response uncertainty; inspect Discord before resubmitting.', 0


def unsafe(path):
    stat = path.lstat()
    return path.is_symlink() or bool(getattr(stat, 'st_file_attributes', 0) & 0x400)


def layout(outbox):
    outbox.mkdir(parents=True, exist_ok=True)
    if unsafe(outbox):
        raise SafeError('Outbox cannot be a symlink/reparse point.')
    for name in ('pending', '.state'):
        folder = outbox / name
        folder.mkdir(exist_ok=True)
        if unsafe(folder):
            raise SafeError('Outbox subfolders cannot be symlinks/reparse points.')


def connect(outbox):
    db = sqlite3.connect(outbox / '.state' / 'ledger.sqlite', timeout=10)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS images (hash TEXT PRIMARY KEY, state TEXT, detail TEXT, attempts INTEGER, due REAL, updated REAL)')
    db.execute('CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, size INTEGER, mtime INTEGER, hash TEXT)')
    db.commit()
    return db


class Lock:
    def __init__(self, outbox):
        self.file = open(outbox / '.state' / 'watcher.lock', 'a+b')
        if self.file.seek(0, 2) == 0:
            self.file.write(b'0')
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise SafeError('Watcher already running.') from None
    def close(self):
        self.file.close()


def active(outbox):
    try:
        lock = Lock(outbox)
    except SafeError:
        return True
    lock.close()
    return False


class Watcher:
    def __init__(self, outbox, sender=transmit, url=None):
        self.outbox, self.sender = outbox, sender
        self.url = url or webhook()
        self.db = connect(outbox)
        self.db.execute("UPDATE images SET state='held', detail='Interrupted send; inspect Discord before resubmitting.' WHERE state='sending'")
        self.db.commit()
        self.seen = {}
        # Delay on restart so crashes/restarts cannot defeat the posting interval.
        retry_due = self.db.execute("SELECT max(due) FROM images WHERE state='retry'").fetchone()[0] or 0
        self.next_send = max(time.time() + 20, retry_due)

    def tick(self, now=None):
        now = time.time() if now is None else now
        current = set()
        for path in sorted((self.outbox / 'pending').iterdir()):
            if path.suffix.lower() not in EXTENSIONS or not path.is_file() or unsafe(path):
                continue
            name = path.name
            current.add(name)
            stat = path.stat()
            signature = (stat.st_size, stat.st_mtime_ns)
            previous = self.seen.get(name)
            if not previous or previous[:2] != signature:
                self.seen[name] = (*signature, now)
                continue
            if now - previous[2] < 3:
                continue
            old = self.db.execute('SELECT size,mtime,hash FROM files WHERE path=?', (name,)).fetchone()
            if old and old[:2] == signature:
                digest = old[2]
                row = self.db.execute('SELECT state,due,attempts FROM images WHERE hash=?', (digest,)).fetchone()
                if not row or row[0] != 'retry' or row[1] > now:
                    continue
            if now < self.next_send:
                continue
            try:
                digest, body, kind = image_request(path)
                after = path.stat()
                if (after.st_size, after.st_mtime_ns) != signature:
                    self.seen.pop(name, None)
                    continue
            except SafeError:
                self.db.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?)', (name, *signature, 'invalid'))
                self.db.execute('INSERT OR REPLACE INTO images VALUES (?,?,?,?,?,?)', ('invalid:' + name, 'failed', 'Invalid image or over 8 MiB.', 0, 0, now))
                self.db.commit()
                continue
            row = self.db.execute('SELECT state,due,attempts FROM images WHERE hash=?', (digest,)).fetchone()
            self.db.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?)', (name, *signature, digest))
            if row and (row[0] != 'retry' or row[1] > now):
                self.db.commit()
                continue
            attempts = (row[2] if row else 0) + 1
            self.db.execute('INSERT OR REPLACE INTO images VALUES (?,?,?,?,?,?)', (digest, 'sending', '', attempts, 0, now))
            self.db.commit()
            state, detail, delay = self.sender(self.url, body, kind)
            if state == 'retry' and attempts >= 5:
                state, detail = 'failed', 'Rate-limit retry budget exhausted.'
            self.db.execute('UPDATE images SET state=?,detail=?,due=?,updated=? WHERE hash=?', (state, detail, now + delay, now, digest))
            self.db.commit()
            self.next_send = max(now + 20, time.time() + 20, now + delay)
            if state == 'auth':
                raise SafeError(detail)
            break
        self.seen = {k: v for k, v in self.seen.items() if k in current}


def watch(outbox):
    lock = Lock(outbox)
    stop = outbox / '.state' / 'stop'
    stop.unlink(missing_ok=True)
    watcher = None
    try:
        watcher = Watcher(outbox)
        while not stop.exists():
            (outbox / '.state' / 'heartbeat').write_text(str(time.time()))
            try:
                watcher.tick()
            except (FileNotFoundError, PermissionError):
                pass  # A producer is still replacing a file; inspect it on the next scan.
            time.sleep(2)
    finally:
        if watcher:
            watcher.db.close()
        lock.close()


def enqueue(outbox, source):
    source = Path(source)
    if unsafe(source):
        raise SafeError('Refusing a symlink/reparse point image.')
    extension, mime, data = image_data(source)
    if len(data) > LIMIT:
        raise SafeError('Image exceeds 8 MiB.')
    target = outbox / 'pending' / (uuid.uuid4().hex + extension)
    temp = target.with_suffix('.part')
    with open(temp, 'xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(target)
    return {'status': 'queued', 'file': str(target)}


def status(outbox):
    db = connect(outbox)
    try:
        return {'running': active(outbox), 'outbox': str(outbox / 'pending'),
                'states': dict(db.execute('SELECT state,count(*) FROM images GROUP BY state')),
                'recent': [{'state': r[0], 'detail': r[1]} for r in db.execute('SELECT state,detail FROM images ORDER BY updated DESC LIMIT 10')]}
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description='Image-only Discord outbox; no model calls or external dependencies.')
    parser.add_argument('--outbox', type=Path, default=DEFAULT)
    parser.add_argument('command', choices=['start', 'watch', 'enqueue', 'status', 'stop', 'configure'])
    parser.add_argument('image', nargs='?')
    args = parser.parse_args()
    try:
        outbox = args.outbox.absolute()
        if args.command == 'configure':
            result = configure()
        else:
            layout(outbox)
            if args.command == 'watch':
                watch(outbox)
                result = {'status': 'stopped'}
            elif args.command == 'start':
                if not active(outbox):
                    webhook()  # Validate configuration before detaching.
                    log = open(outbox / '.state' / 'watcher.log', 'ab')
                    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS} if os.name == 'nt' else {'start_new_session': True}
                    with log:
                        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--outbox', str(outbox), 'watch'], stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True, **kwargs)
                    for _ in range(30):
                        time.sleep(0.1)
                        if active(outbox):
                            break
                result = status(outbox)
                if not result['running']:
                    raise SafeError('Watcher did not start. Inspect the local watcher log.')
            elif args.command == 'enqueue':
                if not args.image:
                    raise SafeError('An image path is required.')
                result = enqueue(outbox, args.image)
            elif args.command == 'stop':
                (outbox / '.state' / 'stop').touch()
                result = {'status': 'stop_requested', 'running': active(outbox)}
            else:
                result = status(outbox)
        print(json.dumps(result))
        return 0
    except SafeError as error:
        print(json.dumps({'status': 'error', 'message': str(error)}))
    except KeyboardInterrupt:
        return 0
    except Exception:
        print(json.dumps({'status': 'error', 'message': 'Local operation failed; check paths, permissions, and configuration.'}))
    return 1


if __name__ == '__main__':
    sys.exit(main())
