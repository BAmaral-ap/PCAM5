import contextlib
import copy
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from finance import _compatibility, budget_snapshot, evaluate_offer
from inventario import PURCHASES, apply_confirmed, reactivate, updated_config
from monitor import main
from parsing import parse_offers
from runtime import make_notifications
from settings import load_config
from storage import Store


class ConfirmedBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.db = self.path / 'state.sqlite3'
        self.config = self.path / 'config.json'
        cfg = json.loads((Path(__file__).resolve().parents[1] / 'config.json').read_text(encoding='utf-8'))
        cfg.pop('pcam5_atualizacao_montagem', None)
        cfg['canais'].append('https://t.me/canal_preservado')
        cfg['config_usuario'] = {'preferencia': 'preservada'}
        self.config.write_text(json.dumps(cfg, ensure_ascii=False), encoding='utf-8')

    def cli(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = main(['--db', str(self.db), '--config', str(self.config), *args])
        return result, output.getvalue()

    def old_cooler(self):
        return {'categoria': 'COOLER-CPU', 'modelo': 'Arctic Freezer 36 ARGB ACFRE00124A',
                'pagamento': 'pix', 'parcelas': 1, 'parcelas_pagas': 0, 'total_centavos': 21581}

    def migrate(self):
        with Store(self.db) as store:
            store.add_purchase(self.old_cooler())
        code, text = self.cli('--atualizar-montagem', '--confirmar')
        self.assertEqual(code, 0, text)
        return json.loads(text)

    def test_migration_is_idempotent_preserves_existing_cooler_and_config(self):
        report = self.migrate()
        items = report['compras']
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]['id'], 1)
        self.assertEqual([p['total_centavos'] for p in items], [21581, 31712, 39505])
        self.assertEqual(items[1]['pagamento'], 'avista')
        self.assertEqual(items[2]['pagamento'], 'pix')
        self.assertEqual(items[2]['status_financeiro'], 'pago')
        self.assertEqual(items[2]['status_compra'], 'COMPRADO')
        self.assertEqual(items[2]['status_entrega'], 'em_preparacao')
        self.assertIn('Arctic Freezer 36 ARGB', items[0]['modelo_antes_atualizacao'])
        cfg = json.loads(self.config.read_text(encoding='utf-8'))
        self.assertIn('https://t.me/canal_preservado', cfg['canais'])
        self.assertEqual(cfg['config_usuario'], {'preferencia': 'preservada'})
        cfg['metas']['RX 9070 XT 16GB'] = 5099
        self.config.write_text(json.dumps(cfg), encoding='utf-8')
        code, text = self.cli('--atualizar-montagem', '--confirmar')
        self.assertEqual(code, 0, text)
        self.assertEqual(json.loads(self.config.read_text(encoding='utf-8'))['metas']['RX 9070 XT 16GB'], 5099)
        with Store(self.db) as store:
            self.assertEqual(len(store.purchases()), 3)
            self.assertEqual(store.notifications(), [])

    def test_budget_tracks_forecast_separately_from_cash_and_future_costs(self):
        report = self.migrate()['orcamento']
        self.assertEqual(report['teto_total_centavos'], 945000)
        self.assertEqual(report['total_comprado_centavos'], 92798)
        self.assertEqual(report['despesas_pagas_centavos'], 92798)
        self.assertEqual(report['despesas_contratadas_centavos'], 92798)
        self.assertEqual(report['saldo_previsto_restante_centavos'], 852202)
        self.assertIsNone(report['saldo_pix_centavos'])
        self.assertEqual(report['gasto_pix_centavos'], 61086)
        self.assertEqual(report['gasto_avista_sem_meio_centavos'], 31712)
        self.assertEqual(report['entradas_pendentes'][0]['valor_centavos'], 500000)
        self.assertEqual(report['entradas_pendentes'][0]['status'], 'pendente')
        self.assertEqual({p['categoria'] for p in report['faltantes']}, {'GPU', 'CPU', 'PLACA-MAE', 'RAM', 'SSD'})
        self.assertEqual(next(p for p in report['faltantes'] if p['categoria'] == 'RAM')['pagamento'], 'cartao')
        self.assertEqual(report['total_projetado_centavos'], 1065798)
        self.assertEqual(report['projecao_total_avista_centavos'], 915798)
        self.assertEqual(report['projecao_total_cartao_centavos'], 150000)
        self.assertEqual(report['folga_teto_centavos'], -120798)

    def test_conflicting_purchase_aborts_atomically(self):
        with Store(self.db) as store:
            store.add_purchase(dict(self.old_cooler(), total_centavos=20000))
            with self.assertRaisesRegex(ValueError, 'Nenhuma compra foi alterada'):
                apply_confirmed(store)
            self.assertEqual(len(store.purchases()), 1)
        cfg_before = self.config.read_bytes()
        result, text = self.cli('--atualizar-montagem', '--confirmar')
        self.assertEqual(result, 1, text)
        self.assertEqual(self.config.read_bytes(), cfg_before)

    def test_repeat_after_reactivation_does_not_reset_it(self):
        self.migrate()
        code, text = self.cli('--reativar-categoria', 'FONTE', '--confirmar')
        self.assertEqual(code, 0, text)
        self.assertEqual(self.cli('--atualizar-montagem', '--confirmar')[0], 0)
        with Store(self.db) as store:
            psu = next(p for p in store.purchases() if p['categoria'] == 'FONTE')
            self.assertTrue(psu['monitorar_novamente'])
            self.assertEqual(sum(p['total_centavos'] for p in store.purchases()), 92798)

    def test_existing_queued_purchased_part_expires_without_deleting_history(self):
        now = datetime.now(timezone.utc)
        cfg = load_config(self.config)
        with Store(self.db) as store:
            offer = parse_offers('teste', 8, now, 'Fonte Gamer 850W R$ 450 no PIX', cfg)[0]
            notification = {'key': 'example:' + offer['link'],
                            'payload': 'AVALIAR\nModelo identificado: ' + offer['peca'] + '\n',
                            'date': now.isoformat()}
            store.record_message('teste', 8, now.isoformat(), offer['texto'], [offer], [notification])
            store.add_purchase(self.old_cooler())
        result, text = self.cli('--atualizar-montagem', '--confirmar')
        self.assertEqual(result, 0, text)
        with Store(self.db) as store:
            self.assertEqual(store.notifications()[0]['state'], 'expired')
            self.assertEqual(len(store.offers()), 1)

    def test_unidentified_existing_case_does_not_create_duplicate(self):
        with Store(self.db) as store:
            store.add_purchase({'categoria': 'GABINETE', 'modelo': 'Montech XR',
                                'pagamento': 'avista', 'total_centavos': 31712})
            with self.assertRaisesRegex(ValueError, 'sem cor identificada'):
                apply_confirmed(store)
            self.assertEqual(len(store.purchases()), 1)

    def test_confirmed_site_income_and_other_pending_entries_are_preserved(self):
        cfg = json.loads(self.config.read_text(encoding='utf-8'))
        cfg['orcamento']['entradas_pendentes'] = [
            {'descricao': 'desenvolvimento de site', 'valor_centavos': 500000, 'status': 'recebido'},
            {'descricao': 'outra entrada', 'valor_centavos': 10000, 'status': 'pendente'},
        ]
        budget = updated_config(cfg)['orcamento']
        self.assertEqual([i['descricao'] for i in budget['entradas_pendentes']], ['outra entrada'])
        self.assertEqual(budget['entradas_confirmadas'][0]['status'], 'recebido')

    def test_alerts_and_reactivation_without_sending(self):
        self.migrate()
        cfg = load_config(self.config)
        now = datetime.now(timezone.utc)
        with Store(self.db) as store:
            store.set_meta('alerts_started_at', now.isoformat())
            def alerts(text):
                offers = parse_offers('teste', 1, now, text, cfg)
                self.assertTrue(offers, text)
                return [n for offer in offers for n in make_notifications(store, offer, cfg, store.purchases(), now)]
            for title in ('Fonte Gamer 850W', 'Gabinete Gamer ATX', 'Air cooler CPU', 'Water cooler CPU'):
                with self.subTest(title=title):
                    self.assertEqual(alerts(title + '\nPreço: R$ 150'), [])
            for text in ('Ryzen 7 7800X3D R$ 1600 no PIX',
                         'Placa mãe MSI B650M DDR5 AM5 R$ 650 no PIX',
                         'Memória DDR5 16GB R$ 1200 no cartão',
                         'XFX RX 9070 XT 16GB\nR$ 3.978\nhttps://amzn.to/exemplo'):
                with self.subTest(text=text):
                    self.assertTrue(alerts(text))
            gpu = alerts('XFX RX 9070 XT 16GB\nR$ 3.978\nhttps://amzn.to/exemplo')[0]['payload']
            self.assertIn('420 mm', gpu)
            self.assertIn('850 W', gpu)
            self.assertIn('compatibilidade a confirmar', gpu)
            memory = alerts('Memória DDR5 16GB 6000 MHz R$ 1200 no cartão')[0]['payload']
            self.assertIn('6000 MHz (faixa preferida', memory)
            memory = alerts('Memória DDR5 16GB 5200 MHz R$ 1100 no cartão')[0]['payload']
            self.assertIn('fora da faixa preferida', memory)
            reactivate(store, 'FONTE')
            self.assertTrue(alerts('Fonte Gamer 850W\nPreço: R$ 450'))
            self.assertEqual(store.notifications(), [])

    def test_purchased_components_drive_compatibility_without_inventing_specs(self):
        self.migrate()
        cfg = load_config(self.config)
        with Store(self.db) as store:
            purchases = store.purchases()
            gpu = {'categoria': 'GPU', 'peca': 'RX 9070 XT 16GB', 'modelo_exato': True}
            state, notes = _compatibility(gpu, cfg, purchases)
            self.assertIsNone(state)
            self.assertTrue(any('420 mm' in n for n in notes))
            self.assertTrue(any('Conectores' in n for n in notes))
            cfg['perfis'][gpu['peca']].update(comprimento_mm=421, fonte_minima_w=750)
            self.assertFalse(_compatibility(gpu, cfg, purchases)[0])
            cfg['perfis'][gpu['peca']].update(comprimento_mm=400, fonte_minima_w=900)
            self.assertFalse(_compatibility(gpu, cfg, purchases)[0])
            cfg['perfis'][gpu['peca']].update(fonte_minima_w=750, conectores_requeridos={'pcie_6_2': 3})
            state, notes = _compatibility(gpu, cfg, purchases)
            self.assertIsNone(state)
            self.assertTrue(any('Quantidade' in n for n in notes))
            board = {'categoria': 'PLACA-MAE', 'peca': 'placa teste', 'modelo_exato': True}
            cfg['perfis']['placa teste'] = {'formato': 'E-ATX'}
            self.assertFalse(_compatibility(board, cfg, purchases)[0])
            cfg['perfis']['Montech XR White']['cooler_max_mm'] = 158
            self.assertFalse(_compatibility(board, cfg, purchases)[0])

    def test_cpu_target_includes_known_shipping(self):
        self.migrate()
        cfg = load_config(self.config)
        with Store(self.db) as store:
            offer = {'categoria': 'CPU', 'peca': 'Ryzen 7 7800X3D', 'modelo_exato': True,
                     'pix_centavos': 165000, 'frete_centavos': 9000, 'verificar': []}
            result = evaluate_offer(offer, cfg, store.purchases())
            self.assertEqual(result['meta_centavos'], 175000)
            self.assertEqual(result['total_entregue_centavos'], 174000)
            self.assertEqual(result['diferenca_centavos'], -1000)
            offer.pop('frete_centavos')
            result = evaluate_offer(offer, cfg, store.purchases())
            self.assertIsNone(result['total_entregue_centavos'])
            self.assertTrue(any('inclui frete' in line for line in result['justificativa']))

    def test_explicit_gpu_and_shipping_specs_in_announcement(self):
        self.migrate()
        cfg = load_config(self.config)
        now = datetime.now(timezone.utc)
        with Store(self.db) as store:
            store.set_meta('alerts_started_at', now.isoformat())
            text = ('XFX RX 9070 XT R$ 4.000\nComprimento da GPU: 430 mm\n'
                    'Fonte recomendada: 850 W\nConectores da GPU: 3 x 8 pinos\nhttps://example.com/gpu')
            gpu = parse_offers('teste', 50, now, text, cfg)[0]
            self.assertEqual(gpu['especificacoes']['comprimento_mm'], 430)
            self.assertEqual(gpu['especificacoes']['fonte_minima_w'], 850)
            self.assertEqual(gpu['especificacoes']['conectores_requeridos'], {'pcie_6_2': 3})
            self.assertFalse(_compatibility(gpu, cfg, store.purchases())[0])
            alert = make_notifications(store, gpu, cfg, store.purchases(), now)[0]['payload']
            self.assertIn('INCOMPATIBILIDADE A VERIFICAR', alert)
            cpu = parse_offers('teste', 51, now, 'Ryzen 7 7800X3D R$ 1650 no PIX\nFrete para Belém: R$ 90,00', cfg)[0]
            self.assertEqual(cpu['frete_centavos'], 9000)
            self.assertEqual(evaluate_offer(cpu, cfg, store.purchases())['total_entregue_centavos'], 174000)


if __name__ == '__main__':
    unittest.main()
