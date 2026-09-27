import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import discord_images as d
import unittest
import tempfile
import time
import io
from unittest.mock import patch
from urllib.error import HTTPError

PNG = b'\x89PNG\r\n\x1a\n' + b'test image bytes'

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        d.layout(self.root)
        self.sent = []
        self.w = d.Watcher(self.root, sender=self.send, url='https://discord.com/api/webhooks/1/test')
        self.now = time.time() + 30
    def tearDown(self):
        self.w.db.close()
        self.tmp.cleanup()
    def send(self, url, body, kind):
        self.sent.append(body)
        return 'sent', '123', 0
    def put(self, name='a.png', data=PNG):
        p = self.root / 'pending' / name
        p.write_bytes(data)
        return p
    def ticks(self):
        self.w.tick(self.now)
        self.w.tick(self.now + 4)
    def test_stability_and_image_only_wire(self):
        self.put()
        self.w.tick(self.now)
        self.assertFalse(self.sent)
        self.w.tick(self.now + 4)
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn(b'"content"', self.sent[0])
        self.assertNotIn(b'"embeds"', self.sent[0])
        self.assertIn(b'name="files[0]"', self.sent[0])
    def test_duplicate_and_restart(self):
        self.put(); self.ticks(); self.put('b.png')
        self.w.tick(self.now + 30); self.w.tick(self.now + 34)
        self.assertEqual(len(self.sent), 1)
        self.w.db.close()
        self.w = d.Watcher(self.root, self.send, 'fake')
        self.w.tick(self.now + 60); self.w.tick(self.now + 64)
        self.assertEqual(len(self.sent), 1)
    def test_changing_file_waits(self):
        p = self.put(); self.w.tick(self.now)
        p.write_bytes(PNG + b'changed')
        self.w.tick(self.now + 4)
        self.assertFalse(self.sent)
        self.w.tick(self.now + 8)
        self.assertEqual(len(self.sent), 1)
    def test_invalid_and_partial_ignored(self):
        self.put(data=b'not png'); self.put('b.part'); self.ticks()
        self.assertFalse(self.sent)
        self.assertEqual(d.status(self.root)['states']['failed'], 1)
    def test_held_never_retried(self):
        self.w.sender = lambda *a: ('held', 'uncertain', 0)
        self.put(); self.ticks()
        self.w.sender = self.send
        self.w.tick(self.now + 40)
        self.assertFalse(self.sent)
    def test_crash_claim_held(self):
        self.w.db.execute('INSERT INTO images VALUES (?,?,?,?,?,?)', ('hash', 'sending', '', 1, 0, 0))
        self.w.db.commit(); self.w.db.close()
        self.w = d.Watcher(self.root, self.send, 'fake')
        self.assertEqual(d.status(self.root)['states']['held'], 1)
    def test_single_watcher_lock(self):
        lock = d.Lock(self.root)
        try:
            self.assertTrue(d.active(self.root))
            with self.assertRaises(d.SafeError): d.Lock(self.root)
        finally: lock.close()
        self.assertFalse(d.active(self.root))
    def test_atomic_enqueue(self):
        source = self.root / 'source.png'; source.write_bytes(PNG)
        result = d.enqueue(self.root, source)
        self.assertEqual(Path(result['file']).read_bytes(), PNG)
        self.assertFalse(list((self.root / 'pending').glob('*.part')))
    def test_rate_limit_delays_all_posts(self):
        self.w.sender = lambda *a: ('retry', 'rate limit', 120)
        self.put(); self.ticks(); self.put('b.png', PNG + b'b')
        self.w.sender = self.send
        self.w.tick(self.now + 30); self.w.tick(self.now + 34)
        self.assertFalse(self.sent)
        self.w.tick(self.now + 125)
        self.assertEqual(len(self.sent), 1)
    def test_retry_budget(self):
        self.w.sender = lambda *a: ('retry', 'rate limit', 1)
        self.put(); self.ticks()
        for i in range(1, 5): self.w.tick(self.now + 4 + i * 25)
        self.assertEqual(d.status(self.root)['states']['failed'], 1)
    def test_oversize(self):
        self.put(data=PNG + b'x' * d.LIMIT); self.ticks()
        self.assertFalse(self.sent)
    def test_transport_rate_and_uncertainty(self):
        class Opener:
            def open(self, *a, **k):
                raise HTTPError('secret', 429, '', {}, io.BytesIO(b'{"retry_after": 12}'))
        with patch.object(d.urllib.request, 'build_opener', return_value=Opener()):
            self.assertEqual(d.transmit('https://discord.com/api/webhooks/1/test', b'body', 'kind'), ('retry', 'Rate limited', 13))

if __name__ == '__main__': unittest.main(verbosity=2)


