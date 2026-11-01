import csv
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

try:
    from monitor_telegram_pc.storage import Store, LEGACY_FIELDS
except ImportError:
    from storage import Store, LEGACY_FIELDS


STAMP = "2026-11-01T16:00:00+00:00"


def offer(cents=170000, **changes):
    result = {"peca": "Ryzen 7 7800X3D", "categoria": "CPU", "preco": cents / 100,
              "pix_centavos": cents, "cartao_centavos": None, "anunciado_centavos": cents,
              "modelo_exato": True, "verificar": [], "link": "https://t.me/channel/1",
              "texto": "Ryzen 7 7800X3D R$ 1700 PIX"}
    result.update(changes)
    return result


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "monitor.sqlite3"
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_message_offers_notification_cursor_persist_together(self):
        notification = {"key": "cpu-price", "payload": "Alerta", "date": STAMP}
        self.assertTrue(self.store.record_message("Channel", 1, STAMP, "full\ntext", [offer()], [notification]))
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.get_cursor("CHANNEL"), 1)
        self.assertEqual(len(self.store.offers()), 1)
        self.assertEqual(self.store.db.execute("SELECT text FROM messages").fetchone()[0], "full\ntext")
        self.assertEqual(len(self.store.pending_notifications()), 1)
        self.assertFalse(self.store.record_message("channel", 1, STAMP, "same", [offer()], [notification]))
        self.assertEqual(len(self.store.offers()), 1)

    def test_failed_notification_rolls_back_message_and_cursor(self):
        self.store.set_cursor("channel", 1)
        with self.assertRaises(ValueError):
            self.store.record_message("channel", 2, STAMP, "text", [offer()],
                                      [{"key": "", "payload": "invalid", "date": STAMP}])
        self.assertEqual(self.store.get_cursor("channel"), 1)
        self.assertEqual(self.store.offers(), [])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM messages").fetchone()[0], 0)

    def test_repost_fingerprint_commits_atomically_with_outbox(self):
        notification = {"key": "message-unique", "payload": "offer", "date": STAMP,
                        "fingerprint": "dedupe:cpu-price"}
        self.store.record_message("channel", 1, STAMP, "offer", [offer()], [notification])
        self.assertIsNotNone(self.store.get_meta("dedupe:cpu-price"))
        broken = dict(notification, key="bad", fingerprint="dedupe:bad")
        with self.assertRaises(ValueError):
            self.store.record_message("channel", 2, STAMP, "offer", [offer()],
                                      [broken, {"key": "", "payload": "invalid"}])
        self.assertIsNone(self.store.get_meta("dedupe:bad"))
        self.assertEqual(len(self.store.notifications()), 1)

    def test_no_cursor_regression(self):
        self.store.set_cursor("channel", 10)
        self.store.set_cursor("channel", 4)
        self.assertEqual(self.store.get_cursor("channel"), 10)
        self.assertIsNone(self.store.get_cursor("other"))

    def test_uncertain_delivery_not_retried_after_restart(self):
        nid = self.store.enqueue_notification("unique", "test", STAMP)
        self.assertIsNone(self.store.enqueue_notification("unique", "test", STAMP))
        self.assertTrue(self.store.claim_notification(nid))
        self.assertFalse(self.store.claim_notification(nid))
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.recover_notifications(), 1)
        self.assertEqual(self.store.pending_notifications(), [])
        self.assertEqual(self.store.notifications()[0]["state"], "uncertain")
        self.assertFalse(self.store.claim_notification(nid))

    def test_explicit_retry_only_on_known_failure(self):
        nid = self.store.enqueue_notification("known-failure", "test", STAMP)
        self.store.claim_notification(nid)
        self.store.finish_notification(nid, "pending")
        self.assertTrue(self.store.claim_notification(nid))
        self.store.finish_notification(nid, "sent")
        self.assertEqual(self.store.pending_notifications(), [])
        self.assertEqual(self.store.notifications()[0]["attempts"], 2)
        with self.assertRaises(ValueError):
            self.store.finish_notification(nid, "pending")

    def test_purchase_and_installments_need_manual_confirmation(self):
        pid = self.store.add_purchase({"categoria": "RAM", "modelo": "DDR5 16GB", "total_centavos": 150001,
                                       "pagamento": "cartao", "parcelas": 3, "primeira_parcela": "2026-11"})
        self.assertEqual(self.store.purchases()[0]["parcelas_pagas"], 0)
        self.assertEqual(self.store.record_installment(pid)["parcelas_pagas"], 1)
        self.store.record_installment(pid)
        self.store.record_installment(pid)
        with self.assertRaises(ValueError):
            self.store.record_installment(pid)
        self.assertEqual(self.store.purchases()[0]["parcelas_pagas"], 3)

    def test_invalid_purchase_not_saved(self):
        base = {"categoria": "RAM", "modelo": "DDR5", "total_centavos": 120000,
                "pagamento": "cartao", "parcelas": 10, "primeira_parcela": "2026-11"}
        for changes in ({"total_centavos": -1}, {"total_centavos": 1.5}, {"total_centavos": True},
                        {"parcelas": 0}, {"parcelas_pagas": 11}, {"primeira_parcela": "2026-13"},
                        {"pagamento": "pix"}, {"modelo": ""}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.add_purchase(dict(base, **changes))
        self.assertEqual(self.store.purchases(), [])

    def make_csv(self):
        path = Path(self.tmp.name) / "ofertas.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=LEGACY_FIELDS)
            writer.writeheader()
            row = {"data": "2026-11-01 15:01", "canal": "channel", "peca": "Ryzen 7 7800X3D",
                   "preco": "1700.00", "categoria": "CPU", "link": "https://t.me/channel/1",
                   "texto": "Ryzen 7 7800X3D R$ 1700 menor preço histórico R$ 999"}
            writer.writerow(row)
            writer.writerow(row)
        return path

    def test_import_is_lossless_idempotent_never_alerts(self):
        path = self.make_csv()
        content = path.read_bytes()
        counts = self.store.import_csv(path)
        self.assertEqual(counts, {"lidos": 2, "importados": 2, "reprocessados": 0})
        self.assertEqual(self.store.import_csv(path)["importados"], 0)
        rows = self.store.imported_offers()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["anunciado_centavos"], 170000)
        self.assertIsNone(rows[0]["pix_centavos"])
        self.assertFalse(rows[0]["modelo_exato"])
        self.assertTrue(rows[0]["verificar"])
        self.assertEqual(path.read_bytes(), content)
        self.assertEqual(self.store.notifications(), [])
        self.assertIsNone(self.store.get_cursor("channel"))
        self.assertEqual(self.store.history("Ryzen 7 7800X3D")["ocorrencias"], 0)
        self.assertEqual(self.store.history("Ryzen 7 7800X3D")["importados_nao_verificados"], 2)
        raw = json.loads(self.store.db.execute("SELECT raw_payload FROM imported_rows LIMIT 1").fetchone()[0])
        self.assertEqual(raw["preco"], "1700.00")

    def test_export_preserves_sources_and_prefers_new_observation(self):
        path = self.make_csv()
        self.store.import_csv(path)
        with self.assertRaises(ValueError):
            self.store.export_csv(path)
        self.store.record_message("channel", 1, STAMP, "new", [offer(160000)])
        output = Path(self.tmp.name) / "export.csv"
        self.assertEqual(self.store.export_csv(output), 1)
        with output.open(encoding="utf-8-sig") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(rows[0]["pix_centavos"], "160000")

    def test_reprocessing_never_uses_historical_number_from_flattened_text(self):
        path = self.make_csv()
        original = path.read_bytes()
        self.store.import_csv(path)
        counts = self.store.import_csv(path, reprocess=True)
        self.assertEqual(counts["importados"], 0)
        self.assertEqual(counts["reprocessados"], 2)
        self.assertEqual(self.store.imported_offers()[0]["anunciado_centavos"], 170000)
        self.assertEqual(self.store.imported_offers()[0]["preco"], "1700.00")
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.store.notifications(), [])

    def test_exact_history_separates_payment_and_uncertainty(self):
        self.store.record_message("channel", 1, STAMP, "a", [offer(170000, cartao_centavos=180000)])
        self.store.record_message("channel", 2, STAMP, "b", [offer(150000)])
        self.store.record_message("channel", 3, STAMP, "c", [offer(90000, verificar=["ambíguo"])])
        self.store.record_message("channel", 4, STAMP, "d", [offer(99999, modelo_exato=False)])
        stats = self.store.history("Ryzen 7 7800X3D")
        self.assertEqual(stats["ocorrencias"], 2)
        self.assertEqual(stats["incertos_excluidos"], 2)
        self.assertEqual(stats["pagamentos"]["pix"]["menor_centavos"], 150000)
        self.assertEqual(stats["pagamentos"]["pix"]["media_centavos"], 160000)
        self.assertEqual(stats["pagamentos"]["pix"]["mediana_centavos"], 160000)
        self.assertEqual(stats["pagamentos"]["cartao"]["menor_centavos"], 180000)
        self.assertEqual(stats["pagamentos"]["nao_informado"]["ocorrencias"], 0)
        self.assertEqual(stats["dias_observados"], 0)

    def test_metadata_round_trip_and_aware_date_requirement(self):
        self.store.set_meta("alert_start", STAMP)
        self.assertEqual(self.store.get_meta("alert_start"), STAMP)
        self.assertIsNone(self.store.get_meta("missing"))
        with self.assertRaises(ValueError):
            self.store.record_message("channel", 1, datetime(2026, 11, 1), "text", [])


if __name__ == "__main__":
    unittest.main()
