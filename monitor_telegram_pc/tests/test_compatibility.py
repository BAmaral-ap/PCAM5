import copy
import unittest

from finance import _compatibility, evaluate_offer


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            'montagem': {cat: {'modelo': cat, 'estimativa_pix': 100, 'pagamento': 'pix'}
                         for cat in ('CPU', 'GPU', 'RAM', 'PLACA-MAE', 'FONTE', 'COOLER-CPU', 'GABINETE')},
            'perfis': {
                'CPU': {'socket': 'AM5'}, 'PLACA-MAE': {'socket': 'AM5', 'tipo_memoria': 'DDR5', 'formato': 'mATX'},
                'RAM': {'tipo_memoria': 'DDR5'}, 'GPU': {'comprimento_mm': 320, 'fonte_minima_w': 750},
                'FONTE': {'potencia_w': 850}, 'COOLER-CPU': {'altura_mm': 155, 'sockets': ['AM4', 'AM5']},
                'GABINETE': {'gpu_max_mm': 330, 'cooler_max_mm': 160, 'formatos_placa_mae': ['ATX', 'mATX']},
            },
            'compatibilidade': {'confirmacoes': {'GPU': True}},
        }
        self.cfg['perfis']['GPU']['compativel'] = True
        self.offer = {'categoria': 'GPU', 'peca': 'GPU', 'modelo_exato': True,
                      'pix_centavos': 10000, 'meta': 200, 'verificar': []}

    def check_conflict(self, expected):
        compatible, reasons = _compatibility(self.offer, self.cfg)
        self.assertFalse(compatible)
        self.assertTrue(any(expected in r for r in reasons), reasons)
        result = evaluate_offer(self.offer, self.cfg, [])
        self.assertEqual(result['classificacao'], 'NÃO COMPENSA')

    def test_known_fit_still_requires_manual_confirmation(self):
        self.assertTrue(_compatibility(self.offer, self.cfg)[0])
        self.cfg['compatibilidade']['confirmacoes'].clear()
        self.assertIsNone(_compatibility(self.offer, self.cfg)[0])

    def test_conflicting_socket_in_other_component_overrides_confirmation(self):
        self.cfg['perfis']['PLACA-MAE']['socket'] = 'AM4'
        self.check_conflict('socket')

    def test_memory_generation_conflict(self):
        self.cfg['perfis']['RAM']['tipo_memoria'] = 'DDR4'
        self.check_conflict('DDR5')

    def test_gpu_case_length_conflict(self):
        self.cfg['perfis']['GPU']['comprimento_mm'] = 340
        self.check_conflict('GPU excede')

    def test_cpu_cooler_height_conflict(self):
        self.cfg['perfis']['COOLER-CPU']['altura_mm'] = 165
        self.check_conflict('cooler excede')

    def test_cooler_mount_conflict(self):
        self.cfg['perfis']['COOLER-CPU']['sockets'] = ['LGA1700']
        self.check_conflict('suporte ao socket')

    def test_psu_minimum_conflict(self):
        self.cfg['perfis']['FONTE']['potencia_w'] = 650
        self.check_conflict('potência mínima')

    def test_motherboard_case_format_conflict(self):
        self.cfg['perfis']['GABINETE']['formatos_placa_mae'] = ['ITX']
        self.check_conflict('Formato da placa-mãe')

    def test_purchased_part_overrides_plan(self):
        self.cfg['perfis']['Case comprado'] = {'gpu_max_mm': 300}
        purchases = [{'categoria': 'GABINETE', 'modelo': 'Case comprado'}]
        self.assertFalse(_compatibility(self.offer, self.cfg, purchases)[0])
        self.assertTrue(_compatibility(self.offer, self.cfg, [dict(purchases[0], modelo='Sem dados')])[0])

    def test_unknown_specs_without_manual_confirmation_never_pass(self):
        self.cfg['perfis'] = {'GPU': {'compativel': True}}
        self.cfg['compatibilidade']['confirmacoes'].clear()
        self.assertIsNone(_compatibility(self.offer, self.cfg)[0])

    def test_invalid_numeric_spec_does_not_claim_fit(self):
        self.cfg['perfis']['GPU']['comprimento_mm'] = 'NaN'
        self.assertIsNone(_compatibility(self.offer, self.cfg)[0])

    def test_candidate_replaces_planned_part(self):
        self.cfg['perfis']['GPU']['comprimento_mm'] = 400
        self.cfg['perfis']['GPU nova'] = {'compativel': True, 'comprimento_mm': 300}
        self.cfg['compatibilidade']['confirmacoes']['GPU nova'] = True
        self.assertTrue(_compatibility(dict(self.offer, peca='GPU nova'), self.cfg)[0])


if __name__ == '__main__':
    unittest.main()
