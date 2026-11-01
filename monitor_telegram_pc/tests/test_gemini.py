import io
import json
import os
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from gemini import GeminiError, explain
from runtime import deliver_pending
from storage import Store

class GeminiTests(unittest.TestCase):
    def test_request_and_response(self):
        response = io.BytesIO(json.dumps({'candidates':[{'content':{'parts':[{'text':'Confira o cupom.'}]}}]}).encode())
        with patch('urllib.request.urlopen', return_value=response) as mock:
            self.assertEqual(explain('BOA PROMOÇÃO\nR$ 100 no PIX\nhttps://example.com\nOrigem: canal', 'fake-key', 'gemini-2.5-flash-lite'), 'Confira o cupom.')
        request = mock.call_args.args[0]
        self.assertNotIn('fake-key', request.full_url)
        self.assertNotIn('example.com', request.data.decode())
        self.assertNotIn('Origem:', request.data.decode())

    def test_empty_response_and_invalid_model(self):
        with patch('urllib.request.urlopen', return_value=io.BytesIO(b'{"candidates":[]}')):
            with self.assertRaises(GeminiError): explain('oferta', 'fake', 'gemini-2.5-flash-lite')
        with self.assertRaises(ValueError): explain('oferta', 'fake', '../../bad')

    def test_http_errors_are_useful_and_never_expose_response(self):
        for code in (400, 403, 404, 429, 500):
            error = urllib.error.HTTPError('https://example.com/secret', code, 'secret', {}, io.BytesIO(b'secret'))
            with self.subTest(code=code), patch('urllib.request.urlopen', side_effect=error):
                with self.assertRaises(GeminiError) as caught:
                    explain('oferta', 'secret', 'gemini-2.5-flash-lite')
                self.assertIn(str(code), str(caught.exception))
                self.assertNotIn('secret', str(caught.exception))

    def test_token_exhaustion_is_diagnosed(self):
        data = {'candidates': [{'finishReason': 'MAX_TOKENS', 'content': {'parts': []}}]}
        with patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(data).encode())):
            with self.assertRaisesRegex(GeminiError, 'limite de geração'):
                explain('oferta', 'fake', 'gemini-2.5-flash-lite')

class GeminiModelsTests(unittest.TestCase):
    def test_list_models_handles_pagination_and_filters(self):
        from gemini import list_models
        pages = [{'models': [{'name': 'models/gemini-a', 'supportedGenerationMethods': ['generateContent']},
                             {'name': 'models/embed-a', 'supportedGenerationMethods': ['embedContent']}], 'nextPageToken': 'page2'},
                 {'models': [{'name': 'models/gemini-b', 'supportedGenerationMethods': ['generateContent']}]}]
        with patch('urllib.request.urlopen', side_effect=[io.BytesIO(json.dumps(p).encode()) for p in pages]) as calls:
            self.assertEqual(list_models('secret'), ['gemini-a', 'gemini-b'])
        self.assertNotIn('secret', calls.call_args.args[0].full_url)

    def test_list_models_redacts_http_error(self):
        from gemini import list_models
        error = urllib.error.HTTPError('https://example/secret', 403, 'secret', {}, io.BytesIO(b'secret'))
        with patch('urllib.request.urlopen', side_effect=error):
            with self.assertRaises(GeminiError) as caught:
                list_models('secret')
        self.assertIn('403', str(caught.exception))
        self.assertNotIn('secret', str(caught.exception))


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_gemini_failure_keeps_local_alert_and_caps_calls(self):
        class Bot:
            def __init__(self): self.messages = []
            def verify_private(self): pass
            def send(self, message): self.messages.append(message)
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'db')
            self.addCleanup(store.close)
            now = datetime.now(timezone.utc).isoformat()
            store.enqueue_notification('a','alerta local',now)
            store.enqueue_notification('b','outro alerta local',now)
            bot = Bot()
            with patch.dict(os.environ, {'GEMINI_API_KEY':'fake'}), patch('gemini.explain', side_effect=OSError('secret')) as call:
                await deliver_pending(store, bot, 30, {'ativado':True,'limite_diario':1})
            self.assertEqual(call.call_count, 1)
            self.assertEqual(bot.messages, ['alerta local','outro alerta local'])
            self.assertEqual(len(store.notifications('sent')), 2)

    async def test_success_appends_explanation(self):
        class Bot:
            def verify_private(self): pass
            def send(self, message): self.message = message
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'db')
            self.addCleanup(store.close)
            store.enqueue_notification('a','classificação local',datetime.now(timezone.utc).isoformat())
            bot = Bot()
            with patch.dict(os.environ, {'GEMINI_API_KEY':'fake'}), patch('gemini.explain', return_value='Confira o frete.'):
                await deliver_pending(store, bot, 30, {'ativado':True,'limite_diario':1})
            self.assertIn('classificação local', bot.message)
            self.assertIn('Confira o frete.', bot.message)
