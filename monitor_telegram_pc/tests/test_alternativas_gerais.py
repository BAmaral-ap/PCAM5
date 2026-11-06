import unittest
from datetime import datetime, timezone
from copy import deepcopy
from finance import evaluate_offer
from parsing import parse_offers
from settings import load_config

class AlternativasTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()
        # Este teste cobre a montagem anterior à compra, com orçamento conhecido.
        self.cfg['orcamento'].update(pix=10000, total_maximo=12500)
        self.cfg['alertar_orcamento_incerto'] = False
    def analyze(self, text, cfg=None, purchases=None):
        offer = parse_offers('pcbuildwizard', 1, datetime.now(timezone.utc), text, cfg or self.cfg)[0]
        return offer, evaluate_offer(offer, cfg or self.cfg, purchases or [])
    def test_generic_candidates_become_reviewable(self):
        for title, amount, category in [
            ('Placa mãe MSI B650M Pro DDR5', '700', 'PLACA-MAE'),
            ('Fonte SuperFlower 850W Gold', '500', 'FONTE'),
            ('Gabinete Gamer Airflow ATX', '260', 'GABINETE'),
            ('Air Cooler Arctic Freezer 36 AM5', '199,99', 'COOLER-CPU'),
            ('Memória DDR5 16GB Corsair', '1200', 'RAM'),
        ]:
            with self.subTest(title=title):
                offer, result = self.analyze(f'{title}\nR$ {amount} no PIX')
                self.assertEqual(offer['categoria'], category)
                expected = 'VERIFICAR PARCELAMENTO' if category == 'RAM' else 'AVALIAR ALTERNATIVA'
                self.assertEqual(result['classificacao'], expected)
                if category == 'RAM':
                    self.assertIsNone(result['preco_centavos'])  # preço PIX não é preço no cartão
                else:
                    self.assertIsNotNone(result['preco_centavos'])
    def test_ventoinhas_are_not_alternatives(self):
        _, result = self.analyze('Kit de ventoinhas gabinete\nR$ 90 no PIX')
        self.assertNotEqual(result['classificacao'], 'AVALIAR ALTERNATIVA')
    def test_cpu_gpu_preserve_classification(self):
        for title in ('Ryzen 7 7800X3D', 'RX 9070 XT'):
            cfg = deepcopy(self.cfg)
            _, enabled = self.analyze(title + '\nR$ 1600 no PIX', cfg)
            cfg['alternativas_gerais']['ativado'] = False
            _, disabled = self.analyze(title + '\nR$ 1600 no PIX', cfg)
            self.assertEqual(enabled, disabled)
    def test_ambiguous_payment_or_additional_eligibility_not_recommended(self):
        for text in ('Gabinete ATX R$ 200', 'Gabinete ATX R$ 200 no PIX somente primeira compra', 'Gabinete ATX R$ 200 no PIX e R$ 300 no PIX'):
            _, result = self.analyze(text)
            self.assertNotEqual(result['classificacao'], 'AVALIAR ALTERNATIVA')
    def test_over_margin_and_over_budget_are_not_recommended(self):
        _, result = self.analyze('Gabinete ATX R$ 400 no PIX')
        self.assertNotEqual(result['classificacao'], 'AVALIAR ALTERNATIVA')
        cfg = deepcopy(self.cfg); cfg['orcamento']['pix'] = 100
        _, result = self.analyze('Gabinete ATX R$ 200 no PIX', cfg)
        self.assertNotEqual(result['classificacao'], 'AVALIAR ALTERNATIVA')
    def test_bought_component_not_recommended(self):
        purchase = {'categoria':'FONTE','modelo':'já comprada','total_centavos':49000,'pagamento':'pix','parcelas':1,'parcelas_pagas':0}
        _, result = self.analyze('Fonte 850W R$ 450 no PIX', purchases=[purchase])
        self.assertNotEqual(result['classificacao'], 'AVALIAR ALTERNATIVA')
    def test_unsupported_intel_motherboard_not_identified(self):
        self.assertEqual(parse_offers('canal', 1, datetime.now(timezone.utc), 'Placa mãe Intel B760 R$ 500 no PIX', self.cfg), [])

class AlternativeAlertTests(unittest.TestCase):
    def test_candidate_is_queued_but_distinct_models_do_not_dedupe(self):
        from runtime import make_notifications, notification_key
        from storage import Store
        import tempfile
        from pathlib import Path
        cfg = load_config(); now = datetime.now(timezone.utc)
        cfg['orcamento'].update(pix=10000, total_maximo=12500)
        cfg['alertar_orcamento_incerto'] = False
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'state.sqlite3')
            try:
                store.set_meta('alerts_started_at', now.isoformat())
                a = parse_offers('canal', 1, now, 'Gabinete A Airflow ATX R$ 200 no PIX', cfg)[0]
                b = parse_offers('canal', 2, now, 'Gabinete B Airflow ATX R$ 200 no PIX', cfg)[0]
                result_a = evaluate_offer(a, cfg, []); result_b = evaluate_offer(b, cfg, [])
                self.assertNotEqual(notification_key(a, result_a), notification_key(b, result_b))
                alerts = make_notifications(store, a, cfg, [], now)
                self.assertEqual(len(alerts), 1)
                self.assertIn('AVALIAR ALTERNATIVA', alerts[0]['payload'])
                fan = parse_offers('canal', 3, now, 'Kit de ventoinhas gabinete R$ 90 no PIX', cfg)[0]
                cfg['alertas']['classes'].append('NÃO COMPENSA')
                self.assertEqual(make_notifications(store, fan, cfg, [], now), [])
            finally:
                store.close()
