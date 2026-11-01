import unittest

from finance import budget_snapshot


class TotalCapTests(unittest.TestCase):
    def config(self):
        return {'orcamento': {'pix': 10000, 'cartao': 2500}, 'montagem': {
            'CPU': {'modelo': 'CPU', 'estimativa_pix': 10000, 'pagamento': 'pix'},
            'RAM': {'modelo': 'RAM', 'estimativa_cartao': 2500, 'pagamento': 'cartao'},
        }}

    def paid_purchase(self, amount=100000):
        return {'categoria': 'GPU', 'modelo': 'GPU comprada', 'total_centavos': amount,
                'pagamento': 'cartao', 'parcelas': 1, 'parcelas_pagas': 1, 'primeira_parcela': '2026-11'}

    def test_repayment_does_not_expand_whole_build_budget(self):
        result = budget_snapshot(self.config(), [self.paid_purchase()])
        self.assertEqual(result['saldo_pix_centavos'], 1000000)
        self.assertEqual(result['limite_cartao_centavos'], 250000)
        self.assertEqual(result['total_projetado_centavos'], 1350000)
        self.assertEqual(result['teto_total_centavos'], 1250000)
        self.assertEqual(result['saldo_teto_centavos'], 1150000)
        self.assertEqual(result['folga_teto_centavos'], -100000)
        self.assertFalse(result['conclusao_viavel'])

    def test_tied_cap_is_permitted_but_not_a_target(self):
        result = budget_snapshot(self.config(), [])
        self.assertEqual(result['folga_teto_centavos'], 0)
        self.assertTrue(result['conclusao_viavel'])
        cfg = self.config()
        cfg['montagem']['CPU']['estimativa_pix'] = 1000
        self.assertTrue(budget_snapshot(cfg, [])['conclusao_viavel'])

    def test_already_above_cap_stays_impossible_with_unknown_prices(self):
        cfg = self.config()
        cfg['montagem']['CPU']['estimativa_pix'] = None
        cfg['montagem']['RAM']['estimativa_cartao'] = None
        result = budget_snapshot(cfg, [self.paid_purchase(1300000)])
        self.assertIsNone(result['total_projetado_centavos'])
        self.assertEqual(result['total_minimo_projetado_centavos'], 1300000)
        self.assertFalse(result['conclusao_viavel'])
        self.assertEqual(result['saldo_teto_centavos'], -50000)

    def test_custom_total_cap_does_not_merge_payment_balances(self):
        cfg = self.config()
        cfg['orcamento']['total_maximo'] = 20000
        cfg['montagem']['RAM']['estimativa_cartao'] = 2600
        result = budget_snapshot(cfg, [])
        self.assertEqual(result['teto_total_centavos'], 2000000)
        self.assertFalse(result['conclusao_viavel'])

    def test_candidate_part_of_whole_project_ceiling(self):
        cfg = self.config()
        cfg['orcamento']['total_maximo'] = 12000
        candidate = {'categoria': 'CPU', 'peca': 'CPU alternativa', 'pix_centavos': 980000}
        result = budget_snapshot(cfg, [], candidate)
        self.assertEqual(result['saldo_teto_apos_centavos'], 220000)
        self.assertEqual(result['folga_teto_centavos'], -30000)
        self.assertFalse(result['conclusao_viavel'])


if __name__ == '__main__':
    unittest.main()
