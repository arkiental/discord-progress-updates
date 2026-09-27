"""Offline behavior tests. Run: python -m unittest discover -s <skill>/scripts"""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from email.parser import BytesParser
from email.policy import default
from urllib.error import HTTPError
import post_update as p


URL = 'https://discord.com/api/webhooks/123/fake-secret?wait=true'


def response(payload):
    return io.BytesIO(json.dumps(payload).encode())


def rate_limit(delay):
    return HTTPError(URL, 429, 'hidden', {}, response({'retry_after': delay}))


class PostingTests(unittest.TestCase):
    def test_text_only_and_mentions(self):
        body, kind, payload, count = p.build_request('Testing', '@everyone tests passed', [])
        self.assertEqual(kind, 'application/json')
        self.assertEqual(json.loads(body), payload)
        self.assertEqual(payload['allowed_mentions'], {'parse': []})
        self.assertNotIn('attachments', payload)
        self.assertEqual(count, 0)

    def test_multipart_preserves_image_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'private filename.png'
            data = b'\x89PNG\r\n\x1a\n' + b'fixture-bytes'
            path.write_bytes(data)
            body, kind, payload, count = p.build_request('Reviewing', 'Actual image', [path, path])
        parsed = BytesParser(policy=default).parsebytes(f'Content-Type: {kind}\r\nMIME-Version: 1.0\r\n\r\n'.encode() + body)
        parts = list(parsed.iter_parts())
        self.assertEqual(len(parts), 2)
        self.assertEqual(json.loads(parts[0].get_payload(decode=True)), payload)
        self.assertEqual(parts[1].get_payload(decode=True), data)
        self.assertEqual(parts[1].get_filename(), 'evidence-1.png')
        self.assertEqual(count, 1)

    def test_missing_image_blocks_request(self):
        with self.assertRaises(p.SafeError):
            p.build_request('Building', 'Progress', ['no-such-test-image.png'])

    def test_wrong_image_signature(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.png'
            path.write_text('not an image')
            with self.assertRaises(p.SafeError):
                p.build_request('Testing', 'Progress', [path])

    def test_image_size_ceiling(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'large.png'
            path.write_bytes(b'\x89PNG\r\n\x1a\n' + b'x' * 32)
            with patch.object(p, 'LIMIT', 16), self.assertRaises(p.SafeError):
                p.build_request('Reviewing', 'Progress', [path])

    def test_message_limits(self):
        for stage, description in [('', 'x'), ('Bad\nStage', 'x'), ('Testing', ''), ('Testing', '😀' * 1000)]:
            with self.subTest(stage=stage), self.assertRaises(p.SafeError):
                p.build_request(stage, description, [])

    def test_webhook_host_validation(self):
        for url in ['https://evil.test/api/webhooks/123/secret', 'http://discord.com/api/webhooks/123/secret',
                    'https://discord.com.evil.test/api/webhooks/123/secret']:
            with self.assertRaises(p.SafeError) as caught:
                p.validate_url(url)
            self.assertNotIn(url, str(caught.exception))

    def test_success_confirmation(self):
        opener = Mock()
        opener.open.return_value = response({'id': '42', 'attachments': [{'id': '1'}]})
        result = p.send(URL, b'{}', 'application/json', opener)
        self.assertEqual(result, {'status': 'sent', 'message_id': '42', 'attachment_count': 1})
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 30)

    def test_one_retry_after_rate_limit(self):
        opener, sleep = Mock(), Mock()
        opener.open.side_effect = [rate_limit(0.2), response({'id': '42'})]
        self.assertEqual(p.send(URL, b'{}', 'application/json', opener, sleep)['status'], 'sent')
        self.assertEqual(opener.open.call_count, 2)
        sleep.assert_called_once()
        self.assertGreaterEqual(sleep.call_args.args[0], 0.2)

    def test_repeated_rate_limit_stops(self):
        opener, sleep = Mock(), Mock()
        opener.open.side_effect = [rate_limit(0), rate_limit(1)]
        with self.assertRaises(p.SafeError):
            p.send(URL, b'{}', 'application/json', opener, sleep)
        self.assertEqual(opener.open.call_count, 2)
        sleep.assert_called_once()

    def test_long_rate_limit_does_not_wait(self):
        opener, sleep = Mock(), Mock()
        opener.open.side_effect = rate_limit(120)
        with self.assertRaises(p.SafeError):
            p.send(URL, b'{}', 'application/json', opener, sleep)
        sleep.assert_not_called()
        self.assertEqual(opener.open.call_count, 1)

    def test_ambiguous_failure_does_not_retry_or_leak_url(self):
        for failure in [TimeoutError(URL), HTTPError(URL, 500, URL, {}, io.BytesIO(b'bad'))]:
            opener = Mock()
            opener.open.side_effect = failure
            with self.assertRaises(p.SafeError) as caught:
                p.send(URL, b'{}', 'application/json', opener)
            self.assertIn('uncertain', str(caught.exception))
            self.assertNotIn('fake-secret', str(caught.exception))
            self.assertEqual(opener.open.call_count, 1)

    def test_missing_confirmation_is_uncertain(self):
        opener = Mock()
        opener.open.return_value = response({})
        with self.assertRaises(p.SafeError):
            p.send(URL, b'{}', 'application/json', opener)
        self.assertEqual(opener.open.call_count, 1)

    def test_redirect_disabled(self):
        self.assertIsNone(p.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.test'))

    @unittest.skipUnless(os.name == 'nt', 'Windows-only credential store')
    def test_credential_encryption_roundtrip(self):
        plain = b'fixture-secret'
        encrypted = p.dpapi(plain)
        self.assertNotIn(plain, encrypted)
        self.assertEqual(p.dpapi(encrypted, decrypt=True), plain)

    def test_dry_run_needs_no_credentials_or_network(self):
        with patch.object(p, 'webhook', side_effect=AssertionError('credential read')), \
             patch.object(p, 'send', side_effect=AssertionError('network')), \
             patch('sys.argv', ['post_update.py', '--stage', 'Testing', '--description', 'Done', '--dry-run']), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(p.main(), 0)
            self.assertEqual(json.loads(output.getvalue())['attachment_count'], 0)


if __name__ == '__main__':
    unittest.main()
