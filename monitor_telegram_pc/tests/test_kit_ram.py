import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from finance import evaluate_offer
from inventario import PURCHASES
from parsing import parse_offers
from runtime import collect_once, make_notifications
from settings import load_config
from storage import Store


class KitRamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'teste.sqlite3')
        self.addCleanup(self.store.close)
        self.cfg = load_config()
        # Preserva a cobertura da política histórica exclusiva de cartão.
        self.cfg['montagem']['RAM'].update(pagamento='cartao', fonte_pagamento='renda_pessoal_separada',
                                           parcelamento='cartao_sem_juros', estimativa_cartao=1500)
        self.now = datetime.now(timezone.utc)
        self.store.set_meta('alerts_started_at', self.now.isoformat())

    def parse(self, text, message_id=1, channel='lapromotion'):
        offers = parse_offers(channel, message_id, self.now, text, self.cfg)
        self.assertTrue(offers, text)
        return offers[0]

    def alerts(self, text, message_id=1, channel='lapromotion'):
        offer = self.parse(text, message_id, channel)
        alerts = make_notifications(self.store, offer, self.cfg, PURCHASES, self.now)
        return offer, alerts

    def test_real_example_with_and_without_a_line_break(self):
        title = ('🔥 Corsair Memória de computador Vengeance Ddr5 Ram 32Gb (2x16GB) '
                 '6000MHz Cl38 Intel Xmp iCUE Preto (Cmk32gx5m2b6000c38)')
        for heading in (title, title.replace('32Gb (2x16GB)', '32Gb\n(2x16GB)')):
            with self.subTest(heading=heading):
                offer, alerts = self.alerts(heading + '\nPreço: R$ 2333\n⭐ 10x sem Juros\nhttps://link.amazon/B07oeTLLV')
                self.assertEqual(offer['peca'], 'DDR5 2x16GB')
                self.assertEqual(offer['anunciado_centavos'], 233300)
                self.assertEqual(offer['parcelas'], 10)
                self.assertIs(offer['parcelamento_sem_juros'], True)
                self.assertTrue(offer['parcelamento_cartao_inferido'])
                self.assertIsNone(offer['total_parcelado_sem_juros_centavos'])
                evaluation = evaluate_offer(offer, self.cfg, PURCHASES)
                self.assertEqual(evaluation['meta_centavos'], 240000)
                self.assertIsNone(evaluation['preco_centavos'])
                self.assertEqual(len(alerts), 1)
                payload = alerts[0]['payload']
                self.assertIn('KIT DDR5 2×16 GB', payload)
                self.assertIn('10x sem juros: identificado como cartão', payload)
                self.assertIn('Preço anunciado: R$ 2.333,00 (confirme o total no cartão)', payload)
                self.assertIn('Meta: R$ 2.400,00', payload)
                self.assertIn('https://link.amazon/B07oeTLLV', payload)
                self.assertIn('Origem: https://t.me/lapromotion/1', payload)
                self.assertNotIn('⚠️ Parcelamento sem juros ainda não confirmado.', payload)

    def test_interest_free_without_card_label_is_card_plan(self):
        for plan in ('10x sem juros', '10X SEM JUROS', '10× s/ juros', '10 parcelas sem juros'):
            with self.subTest(plan=plan):
                offer, alerts = self.alerts('DDR5 2x16GB\nPreço: R$ 2300\n' + plan)
                self.assertIs(offer['parcelamento_sem_juros'], True)
                self.assertTrue(offer['parcelamento_cartao_inferido'])
                self.assertEqual(offer['parcelas'], 10)
                self.assertTrue(alerts)
                self.assertIn('identificado como cartão', alerts[0]['payload'])

    def test_no_interest_free_card_invention_when_other_method_is_explicit(self):
        for plan in ('10x sem juros no PIX', '10x sem juros no boleto', '10x sem juros no carnê',
                     '10x sem juros; cartão não aceito'):
            with self.subTest(plan=plan):
                offer, alerts = self.alerts('DDR5 2x16GB\nPreço: R$ 2300\n' + plan)
                self.assertIsNone(offer['parcelamento_sem_juros'])
                self.assertFalse(offer['parcelamento_cartao_inferido'])
                self.assertTrue(alerts)
                self.assertIn('Parcelamento sem juros ainda não confirmado', alerts[0]['payload'])

    def test_explicit_pix_installments_do_not_become_card_total(self):
        offer, alerts = self.alerts('DDR5 2x16GB\nR$ 1.900 no PIX\n10x de R$ 190 sem juros no PIX')
        self.assertIsNone(offer['parcelamento_sem_juros'])
        self.assertIsNone(offer['cartao_centavos'])
        self.assertIsNone(offer['total_parcelado_sem_juros_centavos'])
        self.assertIsNone(offer['parcelas'])
        self.assertEqual(len(alerts), 1)
        self.assertIn('Cartão total: não informado', alerts[0]['payload'])

    def test_verified_card_total_threshold_and_pix_are_distinct(self):
        for each, expected in [('240,00', True), ('240,01', False), ('233,30', True)]:
            with self.subTest(each=each):
                offer, alerts = self.alerts('DDR5 2x16GB\nR$ 1.900 no PIX\n10x de R$ ' + each + ' sem juros')
                self.assertEqual(offer['pix_centavos'], 190000)
                self.assertTrue(offer['parcelamento_sem_juros'])
                self.assertEqual(bool(alerts), expected)
        _, alerts = self.alerts('DDR5 2x16GB\nR$ 1.900 no PIX\nR$ 2.500 no cartão\n10x sem juros')
        self.assertEqual(alerts, [])

    def test_announced_price_threshold_and_no_interest_uncertainty(self):
        for price, expected in [('2.400,00', True), ('2.400,01', False), ('2.333,00', True)]:
            with self.subTest(price=price):
                offer, alerts = self.alerts('DDR5 2x16GB\nPreço: R$ ' + price + '\n10x sem juros')
                self.assertEqual(bool(alerts), expected)
                self.assertIsNone(offer['total_parcelado_sem_juros_centavos'])
        _, alerts = self.alerts('DDR5 2x16GB\nR$ 2.300 no cartão')
        self.assertEqual(len(alerts), 1)
        self.assertIn('⚠️ Parcelamento sem juros ainda não confirmado.', alerts[0]['payload'])
        _, alerts = self.alerts('DDR5 2x16GB\n10x de R$ 230 com juros no cartão')
        self.assertEqual(alerts, [])

    def test_one_module_policy_and_budget_still_work(self):
        offer, alerts = self.alerts('DDR5 1x16GB\nR$ 1.000 no PIX ou 10x de R$ 120 sem juros')
        self.assertEqual(len(alerts), 1)
        result = evaluate_offer(offer, self.cfg, PURCHASES)
        self.assertEqual(result['meta_centavos'], 150000)
        self.assertEqual(result['preco_centavos'], 120000)
        self.assertEqual(result['pagamento'], 'cartao')
        self.assertEqual(result['orcamento']['projecao_total_avista_centavos'], 740798)
        self.assertEqual(result['orcamento']['projecao_total_cartao_centavos'], 326002)

    def test_other_kits_and_notebook_memory_do_not_alert(self):
        for title in ('DDR5 2x8GB', 'DDR5 4x8GB', 'DDR5 1x32GB', 'DDR5 SODIMM notebook 2x16GB'):
            with self.subTest(title=title):
                _, alerts = self.alerts(title + '\nR$ 1.500\n10x sem juros')
                self.assertEqual(alerts, [])

    def test_configurable_target_without_losing_existing_configuration(self):
        self.cfg['metas']['DDR5 2x16GB'] = 2200
        _, alerts = self.alerts('DDR5 2x16GB\nPreço: R$ 2300\n10x sem juros')
        self.assertEqual(alerts, [])
        _, alerts = self.alerts('DDR5 2x16GB\nPreço: R$ 2200\n10x sem juros')
        self.assertEqual(len(alerts), 1)
        with tempfile.TemporaryDirectory() as directory:
            cfg = deepcopy(self.cfg)
            cfg['metas'].pop('DDR5 2x16GB')
            path = Path(directory) / 'old-config.json'
            raw = json.dumps(cfg, ensure_ascii=False)
            path.write_text(raw, encoding='utf-8')
            self.assertEqual(load_config(path)['metas']['DDR5 2x16GB'], 2400)
            self.assertEqual(path.read_text(encoding='utf-8'), raw)

    def test_exact_custom_model_is_preserved_and_cannot_bypass_kit_cap(self):
        self.cfg['alternativas'] = [{'modelo': 'Corsair Vengeance específica', 'categoria': 'RAM',
                                     'padrao': 'Vengeance', 'meta': 3000}]
        offer, alerts = self.alerts('Corsair Vengeance DDR5 32GB (2x16GB)\nPreço: R$ 2500\n10x sem juros')
        self.assertEqual(offer['peca'], 'Corsair Vengeance específica')
        self.assertEqual(evaluate_offer(offer, self.cfg, PURCHASES)['meta_centavos'], 240000)
        self.assertEqual(alerts, [])
        offer, alerts = self.alerts('Corsair Vengeance DDR5 32GB (2x16GB)\nPreço: R$ 2300\n10x sem juros')
        self.assertTrue(alerts)
        self.assertIn('2×16 GB', alerts[0]['payload'])

    def test_duplicate_is_suppressed_but_new_lower_price_alerts(self):
        title = 'Corsair Vengeance DDR5 32GB (2x16GB)\nPreço: R$ 2333\n10x sem juros\nhttps://example.com/ram'
        offer, notifications = self.alerts(title)
        self.store.record_message('lapromotion', 1, self.now.isoformat(), title, [offer], notifications)
        _, same = self.alerts(title, message_id=2, channel='outrocanal')
        self.assertEqual(same, [])
        _, lower = self.alerts(title.replace('2333', '2200'), message_id=3, channel='outrocanal')
        self.assertEqual(len(lower), 1)

    def test_other_products_can_interpret_interest_free_plan(self):
        offer, alerts = self.alerts('Ryzen 7 7800X3D\nPreço: R$ 1700\n10x sem juros')
        self.assertEqual(offer['categoria'], 'CPU')
        self.assertTrue(offer['parcelamento_sem_juros'])
        self.assertTrue(offer['parcelamento_cartao_inferido'])
        self.assertEqual(offer['parcelas'], 10)
        self.assertIsNone(offer['total_parcelado_sem_juros_centavos'])
        self.assertEqual(alerts, [])  # Processador já comprado, mesmo com parcelamento detectado.

    def test_gpu_alert_also_shows_interest_free_card_plan(self):
        _, alerts = self.alerts('XFX RX 9070 XT 16GB\nPreço: R$ 3.978\n10x sem juros')
        self.assertTrue(alerts)
        self.assertIn('10x sem juros: identificado como cartão', alerts[0]['payload'])


class TelegramKitFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_lapromotion_message_creates_alert_without_sending(self):
        cfg = load_config()
        cfg['canais'] = ['lapromotion']
        now = datetime.now(timezone.utc)
        text = ('🔥 Corsair Vengeance DDR5 32GB\n(2x16GB) 6000MHz CL38\n'
                'Preço: R$ 2333\n⭐ 10x sem juros\nhttps://link.amazon/B07oeTLLV')
        class Client:
            async def get_entity(self, channel):
                return channel
            async def iter_messages(self, entity, limit, min_id=0, reverse=False):
                if min_id < 101:
                    yield SimpleNamespace(id=101, date=now, raw_text=text, entities=[])
        with tempfile.TemporaryDirectory() as directory, Store(Path(directory) / 'state.sqlite3') as store:
            for purchase in PURCHASES:
                store.add_purchase(purchase)
            store.set_meta('alerts_started_at', (now - timedelta(minutes=1)).isoformat())
            store.set_cursor('lapromotion', 100)
            self.assertEqual(await collect_once(Client(), store, cfg), 1)
            self.assertEqual(store.offers()[0]['peca'], 'DDR5 2x16GB')
            queued = store.pending_notifications()
            self.assertEqual(len(queued), 1)
            self.assertIn('R$ 2.333,00', queued[0]['payload'])
            self.assertIn('10x sem juros: identificado como cartão', queued[0]['payload'])
            self.assertEqual(store.notifications('sent'), [])


if __name__ == '__main__':
    unittest.main()
