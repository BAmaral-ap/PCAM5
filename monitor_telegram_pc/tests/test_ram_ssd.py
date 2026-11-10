import contextlib
import io
import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from finance import budget_snapshot, evaluate_offer
from inventario import OLD_UPDATE_ID, PURCHASES, UPDATE_ID
from lojas_dados import _message
from monitor import main
from parsing import parse_offers
from runtime import deliver_pending, make_notifications
from settings import load_config
from storage import Store


class OffersTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'state.sqlite3')
        self.addCleanup(self.store.close)
        for purchase in PURCHASES:
            self.store.add_purchase(purchase)
        self.cfg = load_config()
        self.now = datetime.now(timezone.utc)
        self.store.set_meta('alerts_started_at', self.now.isoformat())

    def parse(self, text, ident=1):
        offers = parse_offers('canalteste', ident, self.now, text, self.cfg)
        self.assertTrue(offers, text)
        return offers[0]

    def alerts(self, text, ident=1):
        offer = self.parse(text, ident)
        return offer, make_notifications(self.store, offer, self.cfg, self.store.purchases(), self.now)

    def test_ram_interest_free_card_total_and_pix_difference(self):
        offer, alerts = self.alerts('Memória DDR5 16GB 6000 MHz CL36\n'
                                    'R$ 1.000 no PIX ou 10x de R$ 120,00 sem juros no cartão')
        self.assertEqual(offer['parcelas'], 10)
        self.assertIs(offer['parcelamento_sem_juros'], True)
        self.assertEqual(offer['cartao_centavos'], 120000)
        self.assertEqual(offer['total_parcelado_sem_juros_centavos'], 120000)
        self.assertEqual(len(alerts), 1)
        payload = alerts[0]['payload']
        self.assertIn('Cartão total: R$ 1.200,00', payload)
        self.assertIn('Diferença do total no cartão em relação ao PIX: R$ 200,00', payload)
        self.assertIn('sem juros anunciado no cartão', payload)
        self.assertIn('renda pessoal separada', payload)
        self.assertNotIn('COMPRAR AGORA', payload)
        result = evaluate_offer(offer, self.cfg, self.store.purchases())
        self.assertEqual(result['pagamento'], 'cartao')
        self.assertEqual(result['preco_centavos'], 120000)

    def test_explicit_total_with_interest_free_card_installments(self):
        offer, alerts = self.alerts('Memória DDR5 1x16GB 5600 MHz CL36\nR$ 1.100 em 10x sem juros no cartão')
        self.assertTrue(alerts)
        self.assertEqual(offer['cartao_centavos'], 110000)
        self.assertEqual(offer['parcelas'], 10)
        self.assertIs(offer['parcelamento_sem_juros'], True)
        self.assertIn('faixa preferida', alerts[0]['payload'])

    def test_installment_amount_with_card_before_value(self):
        offer, alerts = self.alerts('DDR5 16GB\n10x no cartão de R$ 120,00 sem juros')
        self.assertIs(offer['parcelamento_sem_juros'], True)
        self.assertEqual(offer['total_parcelado_sem_juros_centavos'], 120000)
        self.assertTrue(alerts)

    def test_advertised_transfer_rate_keeps_its_unit(self):
        _, alerts = self.alerts('DDR5 16GB 6000 MT/s\nR$ 1.200 no cartão')
        self.assertIn('6000 MT/s (faixa preferida', alerts[0]['payload'])

    def test_card_cash_price_is_not_invented_as_installment_total(self):
        offer, alerts = self.alerts('DDR5 16GB\nR$ 1.000 no cartão à vista.\n10x sem juros no cartão')
        self.assertIs(offer['parcelamento_sem_juros'], True)
        self.assertIsNone(offer['total_parcelado_sem_juros_centavos'])
        evaluation = evaluate_offer(offer, self.cfg, self.store.purchases())
        self.assertEqual(evaluation['classificacao'], 'VERIFICAR PREÇO NO CARTÃO')
        self.assertIsNone(evaluation['preco_centavos'])
        self.assertTrue(alerts)
        self.assertIn('confirme o preço total no cartão', alerts[0]['payload'])

    def test_ram_unknown_installment_is_provisional_and_pix_is_not_card_price(self):
        for suffix in ('R$ 1.000 no PIX', 'R$ 1.200 no cartão', 'R$ 1.200',
                       '10x de R$ 120 no cartão'):
            with self.subTest(suffix=suffix):
                offer, alerts = self.alerts('DDR5 16GB\n' + suffix)
                self.assertEqual(len(alerts), 1)
                self.assertIn('⚠️ Parcelamento sem juros ainda não confirmado.', alerts[0]['payload'])
                self.assertNotIn('COMPRAR AGORA', alerts[0]['payload'])
                result = evaluate_offer(offer, self.cfg, self.store.purchases())
                self.assertEqual(result['pagamento'], 'cartao')
                if 'PIX' in suffix:
                    self.assertIsNone(result['preco_centavos'])
                    self.assertIsNone(result['orcamento']['restante_cartao_centavos'])
                    self.assertIn('Cartão total: não informado', alerts[0]['payload'])

    def test_with_interest_is_not_recommended_even_if_alert_classes_include_it(self):
        self.cfg['alertas']['classes'].append('NÃO RECOMENDAR')
        offer, alerts = self.alerts('DDR5 16GB\n10x de R$ 120 com juros no cartão')
        self.assertIs(offer['parcelamento_sem_juros'], False)
        self.assertEqual(evaluate_offer(offer, self.cfg, self.store.purchases())['classificacao'], 'NÃO RECOMENDAR')
        self.assertEqual(alerts, [])

    def test_pix_installments_and_mixed_terms_are_not_confirmed_as_card(self):
        for suffix in ('10x sem juros no PIX; cartão não informado',
                       '10x de R$ 120 sem juros no cartão ou 12x de R$ 110 com juros no cartão'):
            with self.subTest(suffix=suffix):
                offer = self.parse('DDR5 16GB\nR$ 1200 no PIX\n' + suffix)
                self.assertIsNone(offer['parcelamento_sem_juros'])
                alerts = make_notifications(self.store, offer, self.cfg, self.store.purchases(), self.now)
                self.assertTrue(alerts)
                self.assertIn('⚠️ Parcelamento sem juros ainda não confirmado.', alerts[0]['payload'])

    def test_no_recommendations_of_other_capacity_or_notebook_modules(self):
        for title in ('DDR5 2x8GB', 'DDR5 32GB', 'DDR5 SODIMM 16GB notebook'):
            with self.subTest(title=title):
                _, alerts = self.alerts(title + '\nR$ 1.200 no cartão')
                self.assertEqual(alerts, [])

    def test_discounted_pix_never_debits_cash_for_ram(self):
        baseline = budget_snapshot(self.cfg, self.store.purchases())
        offer = self.parse('DDR5 16GB\nR$ 900 no PIX ou 10x de R$ 130 sem juros no cartão')
        result = evaluate_offer(offer, self.cfg, self.store.purchases())
        self.assertEqual(result['pagamento'], 'cartao')
        self.assertEqual(result['preco_centavos'], 130000)
        self.assertEqual(result['orcamento']['projecao_total_avista_centavos'], baseline['projecao_total_avista_centavos'])
        self.assertEqual(result['orcamento']['projecao_total_cartao_centavos'], 130000)
        self.assertEqual(result['orcamento']['total_projetado_centavos'], baseline['projecao_total_avista_centavos'] + 130000)
        with self.assertRaisesRegex(ValueError, 'cartão parcelado sem juros'):
            budget_snapshot(self.cfg, self.store.purchases(), offer, 'pix')

    def test_interest_free_ram_above_card_limit_needs_budget_review(self):
        offer, alerts = self.alerts('DDR5 16GB\n10x de R$ 300 sem juros no cartão')
        evaluation = evaluate_offer(offer, self.cfg, self.store.purchases())
        self.assertFalse(evaluation['orcamento']['conclusao_viavel'])
        self.assertTrue(alerts)
        self.assertNotIn('COMPRAR AGORA', alerts[0]['payload'])
        self.assertIn('ORÇAMENTO', alerts[0]['payload'])

    def test_card_limit_is_not_invented_as_total_budget(self):
        cfg = deepcopy(self.cfg)
        cfg['orcamento']['pix'] = 5000
        cfg['orcamento'].pop('total_maximo')
        result = budget_snapshot(cfg, self.store.purchases())
        self.assertIsNone(result['teto_total_centavos'])
        self.assertEqual(result['cartao_inicial_centavos'], 250000)

    def test_ssd_nvme_is_monitored_up_to_seven_hundred(self):
        for title in ('SSD NVMe M.2 1 TB Kingston', 'SSD NVMe M.2 500GB', 'SSD M.2 NVMe 512GB'):
            with self.subTest(title=title):
                offer, alerts = self.alerts(title + '\nR$ 700 no PIX')
                self.assertEqual(offer['categoria'], 'SSD')
                self.assertTrue(alerts)
                self.assertIn('AVALIAR SSD NVMe', alerts[0]['payload'])
                self.assertIn('Meta: R$ 700,00', alerts[0]['payload'])
                self.assertIn('valor final com frete', alerts[0]['payload'])
        _, alerts = self.alerts('SSD NVMe M.2 1TB\nR$ 700,01 no PIX')
        self.assertEqual(alerts, [])
        _, alerts = self.alerts('SSD NVMe M.2 1TB\nR$ 650 no PIX\nFrete para Belém: R$ 60')
        self.assertEqual(alerts, [])
        _, alerts = self.alerts('SSD NVMe M.2 1TB\nR$ 650 no PIX\nFrete para Belém: R$ 40')
        self.assertTrue(alerts)

    def test_ssd_cannot_evade_maximum_using_model_specific_target(self):
        self.cfg['metas']['SSD NVMe 1TB'] = 1000
        _, alerts = self.alerts('SSD NVMe M.2 1TB\nR$ 850 no PIX')
        self.assertEqual(alerts, [])
        self.cfg['metas']['SSD NVMe 1TB'] = 500
        _, alerts = self.alerts('SSD NVMe M.2 1TB\nR$ 550 no PIX')
        self.assertEqual(alerts, [])

    def test_ssd_does_not_identify_sata_or_accessories(self):
        for text in ('SSD SATA M.2 1TB R$ 400', 'Case para SSD NVMe M.2 R$ 80',
                     'Dissipador para SSD NVMe M.2 R$ 80'):
            with self.subTest(text=text):
                offers = parse_offers('canalteste', 1, self.now, text, self.cfg)
                self.assertFalse(any(o['categoria'] == 'SSD' for o in offers))

    def test_unknown_ssd_payment_is_provisional(self):
        offer, alerts = self.alerts('SSD NVMe M.2 1TB\nR$ 650\nhttps://exemplo.com/produto')
        self.assertEqual(len(alerts), 1)
        self.assertIsNone(offer['pix_centavos'])
        self.assertIn('AVALIAR SSD NVMe', alerts[0]['payload'])
        self.assertIn('PIX: não informado', alerts[0]['payload'])
        self.assertNotIn('COMPRAR AGORA', alerts[0]['payload'])

    def test_shop_ram_alert_does_not_treat_pix_as_confirmed_installments(self):
        item = {'categoria':'RAM', 'nome':'DDR5 16GB', 'loja':'loja', 'meta_centavos':150000,
                'url':'https://example.com/ram'}
        current = {'preco_publico_centavos':100000, 'preco_pix_centavos':90000,
                   'preco_cartao_centavos':120000, 'estoque':'em_estoque'}
        payload = _message(item, current, None, 'PREÇO DENTRO DA META', 'agora', None, self.cfg, self.store.purchases())
        self.assertIn('VERIFICAR PREÇO E PARCELAMENTO', payload)
        self.assertIn('⚠️ Parcelamento sem juros ainda não confirmado.', payload)
        self.assertIn('Preço PIX atual: R$ 900,00', payload)
        self.assertIn('Preço cartão atual: R$ 1.200,00', payload)

    def test_ram_price_reductions_are_not_deduplicated(self):
        text = 'DDR5 16GB\nR$ 1000 no PIX\nhttps://example.com/ram'
        offer, notifications = self.alerts(text)
        self.store.record_message('canalteste', 1, self.now.isoformat(), text, [offer], notifications)
        _, same = self.alerts(text, 2)
        self.assertEqual(same, [])
        _, lower = self.alerts(text.replace('1000', '950'), 3)
        self.assertEqual(len(lower), 1)


class MigrationTests(unittest.TestCase):
    def test_historic_ram_purchase_does_not_mark_current_ram_as_owned(self):
        cfg = load_config()
        cfg['pcam5_atualizacao_montagem'] = OLD_UPDATE_ID
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            path, db = directory / 'config.json', directory / 'state.db'
            path.write_text(json.dumps(cfg), encoding='utf-8')
            with Store(db) as store:
                for purchase in PURCHASES:
                    store.add_purchase(purchase)
                store.add_purchase({'categoria':'RAM', 'modelo':'Memória antiga', 'total_centavos':10000,
                                    'pagamento':'pix', 'parcelas':1, 'parcelas_pagas':0})
                store.set_meta(OLD_UPDATE_ID, True)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['--db', str(db), '--config', str(path), '--atualizar-montagem', '--confirmar']), 0)
            with Store(db) as store:
                old = next(p for p in store.purchases() if p['categoria'] == 'RAM')
                self.assertTrue(old['monitorar_novamente'])
                self.assertEqual(old['total_centavos'], 10000)
                report = budget_snapshot(load_config(path), store.purchases())
                self.assertIn('RAM', {item['categoria'] for item in report['faltantes']})

    def test_old_queued_ram_alert_is_reclassified_instead_of_lost(self):
        cfg = load_config()
        cfg['pcam5_atualizacao_montagem'] = OLD_UPDATE_ID
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            path, db = directory / 'config.json', directory / 'state.db'
            path.write_text(json.dumps(cfg), encoding='utf-8')
            with Store(db) as store:
                for purchase in PURCHASES:
                    store.add_purchase(purchase)
                store.set_meta(OLD_UPDATE_ID, True)
                store.set_meta('alerts_started_at', now.isoformat())
                text = 'DDR5 16GB\nR$ 1.000 no PIX\nhttps://example.com/memoria'
                offer = parse_offers('canalteste', 1, now, text, cfg)[0]
                old = {'key': 'old:' + offer['link'], 'payload': 'BOA PROMOÇÃO\nModelo identificado: '+offer['peca']+'\n',
                       'date': now.isoformat()}
                store.record_message('canalteste', 1, now.isoformat(), text, [offer], [old])
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = main(['--db', str(db), '--config', str(path), '--atualizar-montagem', '--confirmar'])
            self.assertEqual(code, 0)
            with Store(db) as store:
                pending = store.pending_notifications()
                self.assertEqual(len(pending), 1)
                self.assertIn('VERIFICAR PARCELAMENTO', pending[0]['payload'])
                self.assertIn('⚠️ Parcelamento sem juros ainda não confirmado.', pending[0]['payload'])

    def test_legacy_ssd_csv_reprocessing_preserves_unknown_target(self):
        with tempfile.TemporaryDirectory() as directory, Store(Path(directory) / 'state.db') as store:
            path = Path(directory) / 'legacy.csv'
            path.write_text('peca,preco,texto\nSSD,650,SSD NVMe M.2 1TB\n', encoding='utf-8')
            self.assertEqual(store.import_csv(path, reprocess=True)['importados'], 1)
            item = store.imported_offers()[0]
            self.assertEqual(item['categoria'], 'SSD')
            self.assertIsNone(item['meta'])
            self.assertEqual(item['diferenca_meta'], '')

    def test_previous_update_keeps_custom_budget_and_purchases_but_marks_ssd_pending(self):
        cfg = load_config()
        cfg['pcam5_atualizacao_montagem'] = OLD_UPDATE_ID
        cfg['orcamento'].update(pix=6000, total_maximo=11000, previsto_total=11000)
        cfg['montagem']['SSD'] = {'modelo': 'SSD antigo', 'possui': True, 'estimativa_pix': 0}
        cfg['metas']['Ryzen 7 7800X3D'] = 1690
        cfg['config_usuario'] = {'preservar': True}
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            path, db = directory / 'config.json', directory / 'state.db'
            path.write_text(json.dumps(cfg), encoding='utf-8')
            with Store(db) as store:
                for purchase in PURCHASES:
                    store.add_purchase(purchase)
                store.add_purchase({'categoria':'SSD', 'modelo':'SSD antigo', 'total_centavos':30000,
                                    'pagamento':'pix', 'parcelas':1, 'parcelas_pagas':0})
                store.set_meta(OLD_UPDATE_ID, True)
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                code = main(['--db', str(db), '--config', str(path), '--atualizar-montagem', '--confirmar'])
            self.assertEqual(code, 0, output.getvalue())
            updated = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(updated['orcamento']['pix'], 6000)
            self.assertEqual(updated['orcamento']['total_maximo'], 11000)
            self.assertEqual(updated['metas']['Ryzen 7 7800X3D'], 1690)
            self.assertEqual(updated['metas']['SSD NVMe'], 700)
            self.assertFalse(updated['montagem']['SSD']['possui'])
            self.assertEqual(updated['montagem']['SSD']['estimativa_pix'], 700)
            self.assertEqual(updated['config_usuario'], {'preservar': True})
            with Store(db) as store:
                self.assertEqual(len(store.purchases()), 4)
                self.assertTrue(next(p for p in store.purchases() if p['categoria'] == 'SSD')['monitorar_novamente'])
                self.assertTrue(store.get_meta(UPDATE_ID))
                report = budget_snapshot(updated, store.purchases())
                self.assertIn('SSD', {item['categoria'] for item in report['faltantes']})
                self.assertEqual(report['total_comprado_centavos'], 122798)
                self.assertEqual(report['fonte_pagamento_ram'], 'renda_pessoal_separada')
                self.assertIsNone(report['reserva_avista_prevista_centavos'])


class GeminiPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_ram_never_sends_ai_comment_that_could_suggest_pix_or_debt(self):
        class FakeBot:
            def __init__(self): self.sent = []
            def verify_private(self): pass
            def send(self, text): self.sent.append(text)
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory, Store(Path(directory) / 'state.db') as store:
            store.enqueue_notification('ram', 'VERIFICAR PARCELAMENTO\nRAM desejada: 1×16 GB; confirme o módulo.', now)
            bot = FakeBot()
            with patch.dict('os.environ', {'GEMINI_API_KEY':'fake'}), patch('gemini.explain') as explain:
                await deliver_pending(store, bot, 30, {'ativado':True, 'limite_diario':20})
            explain.assert_not_called()
            self.assertEqual(len(bot.sent), 1)

    async def test_ram_kit_also_skips_ai_payment_suggestions(self):
        class FakeBot:
            def __init__(self): self.sent = []
            def verify_private(self): pass
            def send(self, text): self.sent.append(text)
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory, Store(Path(directory) / 'state.db') as store:
            store.enqueue_notification('ramkit', 'KIT\nRAM desejada: 2×16 GB (32 GB); confira.', now)
            bot = FakeBot()
            with patch.dict('os.environ', {'GEMINI_API_KEY':'fake'}), patch('gemini.explain') as explain:
                await deliver_pending(store, bot, 30, {'ativado':True, 'limite_diario':20})
            explain.assert_not_called()
            self.assertEqual(len(bot.sent), 1)

    async def test_gemini_credit_suggestion_is_removed_from_other_alerts(self):
        class FakeBot:
            def __init__(self): self.sent = []
            def verify_private(self): pass
            def send(self, text): self.sent.append(text)
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory, Store(Path(directory) / 'state.db') as store:
            store.enqueue_notification('cpu', 'BOA PROMOÇÃO\nProcessador', now)
            bot = FakeBot()
            with patch.dict('os.environ', {'GEMINI_API_KEY':'fake'}), patch('gemini.explain', return_value='Faça um empréstimo para comprar.'):
                await deliver_pending(store, bot, 30, {'ativado':True, 'limite_diario':20})
            self.assertEqual(bot.sent, ['BOA PROMOÇÃO\nProcessador'])


if __name__ == '__main__':
    unittest.main()
