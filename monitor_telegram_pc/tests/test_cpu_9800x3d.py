"""Regressões da atualização da montagem, sem acesso ao Telegram ou lojas."""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from finance import _compatibility, budget_snapshot
from inventario import PREVIOUS_UPDATE_ID, PURCHASES, UPDATE_ID, apply_confirmed, updated_config
from monitor import main
from parsing import parse_offers
from runtime import make_notifications
from settings import load_config
from storage import Store


class RyzenPurchaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.config_path = self.path / 'config.json'
        self.db = self.path / 'state.sqlite3'
        self.cfg = load_config()
        self.config_path.write_text(json.dumps(self.cfg), encoding='utf-8')
        self.now = datetime.now(timezone.utc)

    def cli(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = main(['--db', str(self.db), '--config', str(self.config_path), *args])
        return code, output.getvalue()

    def populated_store(self):
        store = Store(self.db)
        self.addCleanup(store.close)
        apply_confirmed(store, self.cfg)
        store.set_meta('alerts_started_at', self.now.isoformat())
        return store

    def alerts(self, store, text, ident=1):
        offers = parse_offers('teste', ident, self.now, text, self.cfg)
        self.assertTrue(offers, text)
        return make_notifications(store, offers[0], self.cfg, store.purchases(), self.now)

    def test_migration_preserves_earlier_records_and_is_idempotent(self):
        previous = copy.deepcopy(self.cfg)
        previous['pcam5_atualizacao_montagem'] = PREVIOUS_UPDATE_ID
        previous['orcamento']['pix'] = 6000
        previous['metas']['RX 9070 XT 16GB'] = 5099
        previous['canais'].append('https://t.me/canal_existente')
        self.config_path.write_text(json.dumps(previous), encoding='utf-8')
        with Store(self.db) as store:
            for item in PURCHASES[:3]:
                store.add_purchase(item)
            store.set_meta(PREVIOUS_UPDATE_ID, True)
        code, output = self.cli('--atualizar-montagem', '--confirmar')
        self.assertEqual(code, 0, output)
        data = json.loads(output)
        self.assertEqual(len(data['compras']), 4)
        cpu = next(p for p in data['compras'] if p['categoria'] == 'CPU')
        self.assertEqual((cpu['modelo'], cpu['sku'], cpu['total_centavos']),
                         ('Ryzen 7 9800X3D', '100-100001084WOF', 206002))
        self.assertEqual((cpu['pagamento'], cpu['parcelas'], cpu['juros']), ('cartao', 6, 'sem_juros'))
        self.assertEqual((cpu['primeira_parcela'], cpu['dia_vencimento'], cpu['parcelas_pagas']),
                         ('2026-12', 10, 0))
        saved = json.loads(self.config_path.read_text(encoding='utf-8'))
        self.assertEqual(saved['orcamento']['pix'], 6000)
        self.assertEqual(saved['metas']['RX 9070 XT 16GB'], 5099)
        self.assertIn('https://t.me/canal_existente', saved['canais'])
        self.assertEqual(saved['montagem']['CPU']['modelo'], 'Ryzen 7 9800X3D')
        self.assertEqual(saved['pcam5_atualizacao_montagem'], UPDATE_ID)
        self.assertEqual(self.cli('--atualizar-montagem', '--confirmar')[0], 0)
        with Store(self.db) as store:
            self.assertEqual(len(store.purchases()), 4)

    def test_existing_cpu_is_updated_instead_of_duplicated(self):
        with Store(self.db) as store:
            for item in PURCHASES[:3]:
                store.add_purchase(item)
            ident = store.add_purchase({'categoria': 'CPU', 'modelo': 'AMD 9800X3D',
                'pagamento': 'pix', 'total_centavos': 200000})
            apply_confirmed(store, self.cfg)
            cpus = [p for p in store.purchases() if p['categoria'] == 'CPU']
            self.assertEqual(len(cpus), 1)
            self.assertEqual(cpus[0]['id'], ident)
            self.assertEqual(cpus[0]['dados_antes_atualizacao']['total_centavos'], 200000)
            self.assertEqual(cpus[0]['total_centavos'], 206002)

    def test_budget_and_due_dates_are_not_automatically_marked_paid(self):
        store = self.populated_store()
        report = budget_snapshot(self.cfg, store.purchases())
        self.assertEqual(report['total_comprado_centavos'], 298800)
        self.assertEqual(report['total_comprometido_centavos'], 298800)
        self.assertEqual(report['despesas_pagas_centavos'], 92798)
        self.assertEqual(report['cartao_comprometido_centavos'], 206002)
        self.assertEqual(report['cartao_pago_centavos'], 0)
        self.assertIsNone(report['saldo_disponivel_avista_centavos'])
        self.assertEqual(report['saldo_previsto_restante_centavos'], 646200)
        self.assertEqual(set(p['categoria'] for p in report['faltantes']), {'GPU', 'PLACA-MAE', 'RAM', 'SSD'})
        self.assertEqual(report['restante_pagamento_indefinido_centavos'], 150000)
        dates = report['compromissos_por_vencimento']
        self.assertEqual(list(dates), ['2026-12-10', '2027-01-10', '2027-02-10',
                                      '2027-03-10', '2027-04-10', '2027-05-10'])
        self.assertEqual(sum(dates.values()), 206002)
        self.assertTrue(report['valores_parcelas_estimados'])
        self.assertTrue(all(value in (34333, 34334) for value in dates.values()))

    def test_payment_uses_actual_confirmed_amount_and_keeps_cash_separate(self):
        store = self.populated_store()
        ident = next(p['id'] for p in store.purchases() if p['categoria'] == 'CPU')
        with self.assertRaisesRegex(ValueError, 'valor real'):
            store.record_installment(ident)
        store.record_installment(ident, 34333)
        report = budget_snapshot(self.cfg, store.purchases())
        self.assertEqual(report['cartao_pago_centavos'], 34333)
        self.assertEqual(report['cartao_comprometido_centavos'], 171669)
        self.assertEqual(report['despesas_pagas_centavos'], 127131)
        self.assertEqual(report['total_pago_avista_centavos'], 92798)
        self.assertEqual(sum(report['compromissos_por_vencimento'].values()), 171669)
        self.assertNotIn('2026-12-10', report['compromissos_por_vencimento'])
        apply_confirmed(store, self.cfg)
        self.assertEqual(next(p for p in store.purchases() if p['categoria'] == 'CPU')['valores_pagos_centavos'], [34333])

    def test_unknown_prior_paid_values_do_not_invent_credit_or_expense(self):
        with Store(self.db) as store:
            for item in PURCHASES[:3]:
                store.add_purchase(item)
            old_cpu = dict(PURCHASES[3], parcelas_pagas=1)
            old_cpu.pop('valores_pagos_centavos')
            ident = store.add_purchase(old_cpu)
            apply_confirmed(store, self.cfg)
            report = budget_snapshot(self.cfg, store.purchases())
            self.assertIsNone(report['cartao_pago_centavos'])
            self.assertIsNone(report['cartao_comprometido_centavos'])
            self.assertIsNone(report['limite_cartao_centavos'])
            self.assertIsNone(report['despesas_pagas_centavos'])
            self.assertTrue(any('não informados' in warning for warning in report['pendencias']))
            with self.assertRaisesRegex(ValueError, 'anteriores'):
                store.record_installment(ident, 34334)

    def test_all_six_confirmed_installments_settle_the_purchase(self):
        store = self.populated_store()
        ident = next(p['id'] for p in store.purchases() if p['categoria'] == 'CPU')
        for amount in (34334, 34334, 34334, 34334, 34333, 34333):
            store.record_installment(ident, amount)
        apply_confirmed(store, self.cfg)
        cpu = next(p for p in store.purchases() if p['id'] == ident)
        self.assertEqual(cpu['status_financeiro'], 'pago')
        report = budget_snapshot(self.cfg, store.purchases())
        self.assertEqual(report['cartao_comprometido_centavos'], 0)
        self.assertEqual(report['despesas_pagas_centavos'], 298800)
        self.assertEqual(report['compromissos_por_vencimento'], {})
        with self.assertRaises(ValueError):
            store.record_installment(ident, 1)

    def test_cli_requires_actual_installment_amount(self):
        self.assertEqual(self.cli('--atualizar-montagem', '--confirmar')[0], 0)
        with Store(self.db) as store:
            ident = next(p['id'] for p in store.purchases() if p['categoria'] == 'CPU')
        code, output = self.cli('--pay-installment', str(ident), '--confirmar')
        self.assertEqual(code, 1)
        self.assertIn('valor real', output)
        code, output = self.cli('--pay-installment', str(ident), '--valor-pago-centavos', '34334', '--confirmar')
        self.assertEqual(code, 0, output)
        with Store(self.db) as store:
            self.assertEqual(next(p for p in store.purchases() if p['id'] == ident)['parcelas_pagas'], 1)

    def test_purchased_categories_suppressed_and_four_categories_active(self):
        store = self.populated_store()
        self.cfg['alertas']['classes'].append('NÃO COMPENSA')
        for text in ('Ryzen 7 7800X3D R$ 1500 no PIX', 'Ryzen 7 9800X3D R$ 1500 no PIX',
                     'Fonte Gamer 850W R$ 450', 'Gabinete Gamer ATX R$ 150',
                     'Air cooler CPU R$ 150', 'Water cooler CPU R$ 150'):
            with self.subTest(text=text):
                self.assertEqual(self.alerts(store, text), [])
        for text in ('RX 9070 XT R$ 3978 https://example.com/gpu',
                     'Placa mãe MSI B650M DDR5 AM5 R$ 650 no PIX',
                     'DDR5 16GB R$ 1200 no PIX', 'DDR5 16GB R$ 1200',
                     'DDR5 16GB 10x de R$ 120 com juros no cartão',
                     'SSD NVMe M.2 1TB R$ 650 no PIX'):
            with self.subTest(text=text):
                self.assertTrue(self.alerts(store, text))
        warning = self.alerts(store, 'DDR5 16GB 10x de R$ 120 com juros no cartão')[0]['payload']
        self.assertIn('com juros', warning)
        self.assertNotIn('COMPRAR AGORA', warning)
        kit = self.alerts(store, 'DDR5 2x16GB\nPreço: R$ 2333\n10x sem juros')[0]['payload']
        self.assertIn('KIT DDR5 2×16 GB', kit)
        board = self.alerts(store, 'Placa mãe MSI B650M DDR5 AM5 R$ 650 no PIX')[0]['payload']
        self.assertIn('Peças restantes com pagamento a definir: R$ 1.500,00', board)

    def test_cpu_reactivation_preserves_spending(self):
        self.assertEqual(self.cli('--atualizar-montagem', '--confirmar')[0], 0)
        self.assertEqual(self.cli('--reativar-categoria', 'CPU', '--confirmar')[0], 0)
        self.assertEqual(self.cli('--atualizar-montagem', '--confirmar')[0], 0)
        with Store(self.db) as store:
            self.assertTrue(next(p for p in store.purchases() if p['categoria'] == 'CPU')['monitorar_novamente'])
            self.assertEqual(sum(p['total_centavos'] for p in store.purchases()), 298800)
            store.set_meta('alerts_started_at', self.now.isoformat())
            self.assertTrue(self.alerts(store, 'Ryzen 7 9800X3D R$ 1800 no PIX'))

    def test_migration_expires_queued_cpu_alert_but_keeps_history(self):
        with Store(self.db) as store:
            store.set_meta('alerts_started_at', self.now.isoformat())
            text = 'Ryzen 7 7800X3D R$ 1600 no PIX'
            offer = parse_offers('teste', 1, self.now, text, self.cfg)[0]
            pending = {'key': 'old:' + offer['link'],
                       'payload': 'BOA PROMOÇÃO\nModelo identificado: ' + offer['peca'] + '\n',
                       'date': self.now.isoformat()}
            store.record_message('teste', 1, self.now.isoformat(), text, [offer], [pending])
            apply_confirmed(store, self.cfg)
            self.assertEqual(store.notifications()[0]['state'], 'expired')
            self.assertEqual(len(store.offers()), 1)

    def test_migration_updates_queued_shop_ram_payment_guidance(self):
        from lojas_dados import StoreTracking
        item = {'nome': 'DDR5 1x16GB', 'categoria': 'RAM', 'url': 'https://example.com/ram',
                'sku': 'ram1', 'termos_identificacao': ['DDR5']}
        with Store(self.db) as store:
            StoreTracking(store).sync([item])
            payload = ('🔥 LOJA — RAM\nProduto: DDR5 1x16GB\n'
                       '⚠️ Parcelamento sem juros ainda não confirmado.\n'
                       'As parcelas da RAM serão pagas com renda pessoal separada da reserva à vista.\n'
                       'https://example.com/ram')
            store.enqueue_notification('old-shop-ram', payload, self.now)
            apply_confirmed(store, self.cfg)
            pending = store.pending_notifications()
            self.assertEqual(len(pending), 1)
            self.assertIn('Confira o preço à vista', pending[0]['payload'])
            self.assertNotIn('As parcelas da RAM serão pagas', pending[0]['payload'])
            self.assertNotIn('Parcelamento sem juros ainda não confirmado', pending[0]['payload'])

    def test_motherboard_checks_9800x3d_without_assuming_bios(self):
        store = self.populated_store()
        board = {'categoria': 'PLACA-MAE', 'peca': 'placa desconhecida', 'modelo_exato': True}
        state, warnings = _compatibility(board, self.cfg, store.purchases())
        self.assertIsNone(state)
        for expected in ('9800X3D', 'BIOS', 'VRM', 'DDR5', 'NVMe', 'formato'):
            self.assertTrue(any(expected in note for note in warnings), expected)
        self.cfg['perfis']['placa desconhecida'] = {'socket': 'AM4'}
        self.assertFalse(_compatibility(board, self.cfg, store.purchases())[0])
        self.cfg['perfis']['placa desconhecida'] = {'socket': 'AM5', 'suporte_9800x3d': False}
        self.assertFalse(_compatibility(board, self.cfg, store.purchases())[0])
        self.assertTrue(self.alerts(store, 'Placa mãe MSI B650M DDR5 AM5 R$ 650 no PIX'))

    def test_gpu_classification_includes_explicit_shipping(self):
        store = self.populated_store()
        for price, shipping, label in [('4700', '200', 'EXCELENTE'), ('5100', '100', 'AVALIAR')]:
            text = f'RX 9070 XT R$ {price}\nFrete para Belém: R$ {shipping}\nhttps://example.com/gpu'
            alert = self.alerts(store, text)[0]['payload']
            self.assertIn(label, alert)
            self.assertIn('Total estimado com frete', alert)
            self.assertIn('confirme no checkout', alert)
        self.assertEqual(self.alerts(store, 'RX 9070 XT R$ 5300\nFrete para Belém: R$ 1'), [])

    def test_uncontracted_loan_is_not_cash_and_only_confirmed_charges_count(self):
        store = self.populated_store()
        report = budget_snapshot(self.cfg, store.purchases())
        loan = report['financiamentos'][0]
        self.assertEqual(loan['status'], 'simulado')
        self.assertEqual((loan['valor_centavos'], loan['encargos_centavos']), (268000, 65960))
        self.assertEqual(report['custos_financeiros_contratados_centavos'], 0)
        self.assertIsNone(report['saldo_disponivel_avista_centavos'])
        cfg = copy.deepcopy(self.cfg)
        cfg['orcamento']['financiamentos'][0].update(status='contratado', confirmado=True)
        confirmed = budget_snapshot(cfg, store.purchases())
        self.assertEqual(confirmed['custos_financeiros_contratados_centavos'], 65960)
        self.assertEqual(confirmed['total_comprometido_centavos'], 364760)
        self.assertIsNone(confirmed['saldo_disponivel_avista_centavos'])
        self.assertIsNone(confirmed['despesas_pagas_centavos'])
        cfg['orcamento']['financiamentos'][0]['encargos_centavos'] = 1
        with self.assertRaisesRegex(ValueError, 'Encargos'):
            budget_snapshot(cfg, store.purchases())

    def test_missing_card_limit_and_total_cap_are_not_invented(self):
        store = self.populated_store()
        cfg = copy.deepcopy(self.cfg)
        cfg['orcamento']['cartao'] = None
        cfg['orcamento'].pop('total_maximo')
        report = budget_snapshot(cfg, store.purchases())
        self.assertIsNone(report['limite_cartao_centavos'])
        self.assertIsNone(report['teto_total_centavos'])


if __name__ == '__main__':
    unittest.main()
