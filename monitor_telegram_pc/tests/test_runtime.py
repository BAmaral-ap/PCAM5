"""Offline integration of the real parser/finance/SQLite collection pipeline."""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telethon.errors import FloodWaitError
from telethon.tl.types import MessageEntityStrike, MessageEntityTextUrl

import runtime
from notifications import DeliveryError
from storage import Store


class FakeClient:
    def __init__(self, messages=None):
        self.messages = list(messages or [])
        self.calls = []
        self.fail_after = None

    async def get_entity(self, channel):
        return channel

    async def iter_messages(self, entity, limit, min_id=0, reverse=False):
        self.calls.append((min_id, reverse, limit))
        candidates = sorted((m for m in self.messages if m.id > min_id),
                            key=lambda m: m.id, reverse=not reverse)[:limit]
        for index, message in enumerate(candidates):
            if self.fail_after is not None and index == self.fail_after:
                raise FloodWaitError(request=None, capture=60)
            yield message


class FakeNotifier:
    def __init__(self, error=None, verification_error=None):
        self.error = error
        self.verification_error = verification_error
        self.sent = []

    def verify_private(self):
        if self.verification_error:
            raise self.verification_error

    def send(self, text):
        self.sent.append(text)
        if self.error:
            raise self.error
        return {"message_id": len(self.sent)}


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.sqlite3"
        self.store = Store(self.path)
        self.now = datetime.now(timezone.utc)
        self.clock = patch.object(runtime, "utcnow", return_value=self.now)
        self.clock.start()
        self.cfg = {
            "canais": ["channel"], "orcamento": {"pix": 10000, "cartao": 2500},
            "montagem": {"CPU": {"modelo": "Ryzen 7 7800X3D", "estimativa_pix": 1700,
                                   "pagamento": "pix"}},
            "metas": {"Ryzen 7 7800X3D": 1700}, "alertas": {"classes": ["BOA PROMOÇÃO", "COMPRAR AGORA"]},
            "perfis": {"Ryzen 7 7800X3D": {"compativel": True}},
            "compatibilidade": {"confirmacoes": {"Ryzen 7 7800X3D": True}},
            "idade_maxima_alerta_minutos": 30, "deduplicacao_horas": 24,
        }

    def tearDown(self):
        self.clock.stop()
        self.store.close()
        self.tmp.cleanup()

    def message(self, mid, price=1600, age_seconds=0):
        return SimpleNamespace(id=mid, date=self.now - timedelta(seconds=age_seconds),
                               raw_text=f"Ryzen 7 7800X3D\nR$ {price},00 no PIX\nhttps://loja.example/cpu", entities=[])

    async def initialized(self):
        client = FakeClient([self.message(1, age_seconds=60)])
        await runtime.collect_once(client, self.store, self.cfg, limit=2)
        return client

    async def test_first_baseline_records_history_without_alerts(self):
        client = FakeClient([self.message(i, age_seconds=60 * i) for i in range(1, 4)])
        await runtime.collect_once(client, self.store, self.cfg, limit=3)
        self.assertEqual(self.store.get_cursor("channel"), 3)
        self.assertEqual(len(self.store.offers()), 3)
        self.assertEqual(self.store.notifications(), [])
        self.assertIsNotNone(self.store.get_meta("alerts_started_at"))

    async def test_ascending_multiple_pages_never_skip_middle_messages(self):
        client = await self.initialized()
        client.messages.extend(self.message(i, price=1600 - i) for i in range(2, 9))
        await runtime.collect_once(client, self.store, self.cfg, limit=2)
        self.assertEqual([o["message_id"] for o in self.store.offers()], list(range(1, 9)))
        self.assertEqual(self.store.get_cursor("channel"), 8)
        self.assertEqual(len(self.store.pending_notifications()), 7)
        self.assertTrue(all(reverse for _, reverse, _ in client.calls[1:]))

    async def test_restart_does_not_duplicate_notifications_or_history(self):
        client = await self.initialized()
        client.messages.append(self.message(2))
        await runtime.collect_once(client, self.store, self.cfg)
        self.assertEqual(len(self.store.pending_notifications()), 1)
        self.store.close()
        self.store = Store(self.path)
        await runtime.collect_once(client, self.store, self.cfg)
        self.assertEqual(len(self.store.pending_notifications()), 1)
        self.assertEqual(len(self.store.offers()), 2)

    async def test_repost_suppressed_after_atomic_fingerprint_commit(self):
        client = await self.initialized()
        client.messages.extend([self.message(2), self.message(3)])
        await runtime.collect_once(client, self.store, self.cfg)
        self.assertEqual(len(self.store.offers()), 3)
        self.assertEqual(len(self.store.pending_notifications()), 1)

    async def test_old_backlog_kept_in_history_without_retroactive_alerts(self):
        client = await self.initialized()
        client.messages.append(self.message(2, age_seconds=3600))
        await runtime.collect_once(client, self.store, self.cfg)
        self.assertEqual(self.store.get_cursor("channel"), 2)
        self.assertEqual(len(self.store.offers()), 2)
        self.assertEqual(self.store.notifications(), [])

    async def test_floodwait_keeps_processed_cursor_and_persistent_cooldown(self):
        client = await self.initialized()
        client.messages.extend(self.message(i, price=1600 - i) for i in range(2, 5))
        client.fail_after = 1
        await runtime.collect_once(client, self.store, self.cfg, limit=3)
        self.assertEqual(self.store.get_cursor("channel"), 2)
        self.assertEqual(len(self.store.offers()), 2)
        calls = len(client.calls)
        await runtime.collect_once(client, self.store, self.cfg, limit=3)
        self.assertEqual(len(client.calls), calls)
        client.fail_after = None
        self.store.set_meta("flood:channel", (self.now - timedelta(seconds=1)).isoformat())
        await runtime.collect_once(client, self.store, self.cfg, limit=3)
        self.assertEqual(self.store.get_cursor("channel"), 4)
        self.assertEqual(len(self.store.offers()), 4)

    async def test_delivery_success_and_stale_queue_expiration(self):
        self.store.enqueue_notification("old", "old alert", self.now - timedelta(hours=1))
        self.store.enqueue_notification("new", "new alert", self.now)
        bot = FakeNotifier()
        await runtime.deliver_pending(self.store, bot)
        self.assertEqual(bot.sent, ["new alert"])
        self.assertEqual([n["state"] for n in self.store.notifications()], ["expired", "sent"])
        await runtime.deliver_pending(self.store, bot)
        self.assertEqual(bot.sent, ["new alert"])

    async def test_429_retries_after_persisted_cooldown_only(self):
        self.store.enqueue_notification("new", "alert", self.now)
        bot = FakeNotifier(DeliveryError("rate limited", retry_after=60))
        await runtime.deliver_pending(self.store, bot)
        self.assertEqual(self.store.notifications()[0]["state"], "pending")
        self.store.close()
        self.store = Store(self.path)
        await runtime.deliver_pending(self.store, bot)
        self.assertEqual(len(bot.sent), 1)
        self.store.set_meta("bot_retry_at", (self.now - timedelta(seconds=1)).isoformat())
        bot.error = None
        await runtime.deliver_pending(self.store, bot)
        self.assertEqual(len(bot.sent), 2)
        self.assertEqual(self.store.notifications()[0]["state"], "sent")

    async def test_timeout_uncertain_is_never_automatically_retried(self):
        self.store.enqueue_notification("new", "alert", self.now)
        bot = FakeNotifier(DeliveryError("timeout", uncertain=True))
        await runtime.deliver_pending(self.store, bot)
        self.assertEqual(self.store.notifications()[0]["state"], "uncertain")
        await runtime.deliver_pending(self.store, bot)
        self.assertEqual(len(bot.sent), 1)

    async def test_private_destination_error_preserves_unsent_queue(self):
        self.store.enqueue_notification("new", "alert", self.now)
        bot = FakeNotifier(verification_error=DeliveryError("not private"))
        await runtime.deliver_pending(self.store, bot)
        self.assertEqual(bot.sent, [])
        self.assertEqual(self.store.notifications()[0]["state"], "pending")

    def test_strike_and_hidden_link_use_telegram_utf16_offsets(self):
        raw = "🎮 Ryzen 7 7800X3D R$ 2.000 por R$ 1.600 PIX aqui"
        old_price = "R$ 2.000"
        start = len(raw[:raw.index(old_price)].encode("utf-16-le")) // 2
        link_start = len(raw[:raw.index("aqui")].encode("utf-16-le")) // 2
        message = SimpleNamespace(raw_text=raw, entities=[
            MessageEntityStrike(offset=start, length=len(old_price)),
            MessageEntityTextUrl(offset=link_start, length=4, url="https://loja.example/cpu"),
        ])
        text = runtime.message_text(message)
        self.assertNotIn(old_price, text)
        self.assertIn("🎮", text)
        self.assertIn("R$ 1.600", text)
        self.assertTrue(text.endswith("https://loja.example/cpu"))


if __name__ == "__main__":
    unittest.main()
