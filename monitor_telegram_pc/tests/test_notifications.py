import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from notifications import BotNotifier, DeliveryError, format_alert
from monitor import demo
from settings import load_config


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.bot = BotNotifier('123456:SECRET_SENTINEL', '1234567')

    def response(self, result):
        return io.BytesIO(json.dumps({'ok': True, 'result': result}).encode())

    def test_private_destination_and_plain_text_payload(self):
        with patch('notifications.urllib.request.urlopen', side_effect=[self.response({'type': 'private'}), self.response({'message_id': 1})]) as request:
            self.bot.send('Teste <sem HTML>')
        self.assertEqual(request.call_count, 2)
        payload = json.loads(request.call_args.args[0].data)
        self.assertEqual(payload['text'], 'Teste <sem HTML>')
        self.assertNotIn('parse_mode', payload)

    def test_group_is_rejected_without_sending(self):
        with patch('notifications.urllib.request.urlopen', return_value=self.response({'type': 'group'})) as request:
            with self.assertRaises(DeliveryError):
                self.bot.send('Teste')
        self.assertEqual(request.call_count, 1)

    def test_http_429_has_safe_retry(self):
        exc = urllib.error.HTTPError('https://example/bot123456:SECRET_SENTINEL/sendMessage', 429, 'secret', {}, io.BytesIO(b'{"parameters":{"retry_after":42}}'))
        with patch('notifications.urllib.request.urlopen', side_effect=exc):
            with self.assertRaises(DeliveryError) as caught:
                self.bot._request('sendMessage', {})
        self.assertEqual(caught.exception.retry_after, 42)
        self.assertFalse(caught.exception.uncertain)
        self.assertNotIn('SECRET_SENTINEL', str(caught.exception))

    def test_timeout_is_uncertain_and_redacted(self):
        with patch('notifications.urllib.request.urlopen', side_effect=urllib.error.URLError('SECRET_SENTINEL')):
            with self.assertRaises(DeliveryError) as caught:
                self.bot._request('sendMessage', {})
        self.assertTrue(caught.exception.uncertain)
        self.assertNotIn('SECRET_SENTINEL', str(caught.exception))

    def test_server_error_is_uncertain(self):
        exc = urllib.error.HTTPError('https://example/token', 502, 'bad', {}, io.BytesIO())
        with patch('notifications.urllib.request.urlopen', side_effect=exc):
            with self.assertRaises(DeliveryError) as caught:
                self.bot._request('sendMessage', {})
        self.assertTrue(caught.exception.uncertain)

    def test_demo_discloses_estimates_without_historic_claim(self):
        text = demo(load_config())
        self.assertIn('OFERTA FICTÍCIA', text)
        self.assertIn('R$ 1.500,00', text)
        self.assertIn('R$ 10.420,00', text)
        self.assertIn('Cartão total: não informado', text)
        self.assertNotIn('histórico', text.lower())
        self.assertLessEqual(len(text), 4096)

    def test_invalid_or_nonprivate_id_is_rejected(self):
        for chat in ('', '-10012345', '@channel'):
            with self.assertRaises(ValueError):
                BotNotifier('token', chat)
