import copy
import unittest

from finance import (
    DEFAULT_BUILD, budget_snapshot, cents, evaluate_offer, installments,
    validate_purchase,
)


def config():
    build = copy.deepcopy(DEFAULT_BUILD)
    # Demais cálculos deste conjunto usam a situação histórica com SSD existente.
    build['SSD'] = {'modelo': 'SSD existente', 'possui': True}
    # Independent planning assumption supplied by the user, not copied from
    # an advertised PIX price by the implementation.
    build["RAM"]["estimativa_cartao"] = 1800
    return {"orcamento": {"pix": 10000, "cartao": 2500, "total_maximo": 12500}, "montagem": build,
            "metas": {"Ryzen 7 7800X3D": 1700, "DDR5 16GB": 1500},
            "perfis": {"Ryzen 7 7800X3D": {"socket": "AM5", "compativel": True}},
            "compatibilidade": {"confirmacoes": {"Ryzen 7 7800X3D": True}}}


def offer(**overrides):
    result = {"peca": "Ryzen 7 7800X3D", "categoria": "CPU",
              "pix_centavos": 150000, "cartao_centavos": None,
              "anunciado_centavos": 150000, "meta": 1700,
              "modelo_exato": True, "verificar": []}
    result.update(overrides)
    return result


def purchase(**overrides):
    result = {"categoria": "RAM", "modelo": "DDR5 16GB", "pagamento": "cartao",
              "total_centavos": 180001, "parcelas": 3,
              "parcelas_pagas": 0, "primeira_parcela": "2026-11"}
    result.update(overrides)
    return result


class FinanceTests(unittest.TestCase):
    def test_independent_limits_and_projected_total(self):
        result = budget_snapshot(config(), [])
        self.assertEqual(result["saldo_pix_centavos"], 1000000)
        self.assertEqual(result["limite_cartao_centavos"], 250000)
        self.assertEqual(result["restante_pix_centavos"], 802000)
        self.assertEqual(result["restante_cartao_centavos"], 180000)
        self.assertEqual(result["total_projetado_centavos"], 982000)
        self.assertTrue(result["conclusao_viavel"])
        self.assertIn("SSD", result["ja_possui"])

    def test_total_can_fit_but_credit_cannot(self):
        cfg = config()
        cfg["montagem"]["RAM"]["estimativa_cartao"] = 2600
        result = budget_snapshot(cfg, [])
        self.assertLess(result["total_projetado_centavos"], 1250000)
        self.assertFalse(result["conclusao_viavel"])

    def test_unknown_card_price_is_not_pix_price(self):
        result = budget_snapshot({}, [])
        self.assertIsNone(result["restante_cartao_centavos"])
        self.assertIsNone(result["total_projetado_centavos"])
        self.assertIsNone(result["conclusao_viavel"])

    def test_actual_purchase_replaces_estimate(self):
        result = budget_snapshot(config(), [purchase()])
        self.assertEqual(result["restante_cartao_centavos"], 0)
        self.assertEqual(result["limite_cartao_centavos"], 69999)
        self.assertEqual(result["saldo_pix_centavos"], 1000000)
        self.assertEqual(result["total_projetado_centavos"], 982001)
        self.assertNotIn("RAM", [p["categoria"] for p in result["faltantes"]])

    def test_monthly_commitments_rounding_and_year_rollover(self):
        result = budget_snapshot(config(), [purchase()])
        self.assertEqual(result["compromissos_mensais"], {
            "2026-11": 60001, "2026-12": 60000, "2027-01": 60000})
        self.assertEqual(sum(result["compromissos_mensais"].values()), 180001)

    def test_only_manual_payment_frees_credit(self):
        result = budget_snapshot(config(), [purchase(parcelas_pagas=1)])
        self.assertEqual(result["limite_cartao_centavos"], 130000)
        self.assertEqual(result["saldo_pix_centavos"], 1000000)
        self.assertEqual(result["cartao_pago_centavos"], 60001)
        self.assertEqual(result["compromissos_mensais"], {"2026-12": 60000, "2027-01": 60000})

    def test_candidate_replaces_only_one_estimate(self):
        result = budget_snapshot(config(), [], offer())
        self.assertEqual(result["saldo_pix_apos_centavos"], 850000)
        self.assertEqual(result["restante_pix_centavos"], 632000)
        self.assertEqual(result["total_projetado_centavos"], 962000)
        self.assertTrue(result["conclusao_viavel"])

    def test_extra_purchase_does_not_hide_spending(self):
        purchases = [purchase(categoria="CPU", modelo="Ryzen 7 7800X3D",
                              pagamento="pix", total_centavos=170000, parcelas=1)]
        result = budget_snapshot(config(), purchases, offer())
        self.assertFalse(result["conclusao_viavel"])
        self.assertEqual(result["total_projetado_centavos"], 1132000)

    def test_unknown_payment_does_not_become_cash(self):
        result = evaluate_offer(offer(pix_centavos=None), config(), [])
        self.assertEqual(result["classificacao"], "VERIFICAR")
        self.assertIsNone(result["preco_centavos"])
        self.assertIsNone(result["orcamento"]["saldo_pix_apos_centavos"])

    def test_exceptional_offer_and_target_delta(self):
        result = evaluate_offer(offer(), config(), [])
        self.assertEqual(result["classificacao"], "COMPRAR AGORA")
        self.assertEqual(result["diferenca_centavos"], -20000)
        self.assertEqual(result["diferenca_percentual"], -11.76)

    def test_exact_threshold_uses_decimal(self):
        result = evaluate_offer(offer(pix_centavos=153000), config(), [])
        self.assertEqual(result["classificacao"], "COMPRAR AGORA")
        result = evaluate_offer(offer(pix_centavos=153001), config(), [])
        self.assertEqual(result["classificacao"], "BOA PROMOÇÃO")

    def test_unverified_compatibility_prevents_urgent(self):
        cfg = config()
        cfg["compatibilidade"]["confirmacoes"] = {}
        result = evaluate_offer(offer(), cfg, [])
        self.assertEqual(result["classificacao"], "BOA PROMOÇÃO")
        self.assertIsNone(result["compativel"])

    def test_incompatibility_rejects_even_cheap_offer(self):
        cfg = config()
        cfg["perfis"]["Ryzen 7 7800X3D"]["socket"] = "AM4"
        self.assertEqual(evaluate_offer(offer(), cfg, [])["classificacao"], "NÃO COMPENSA")

    def test_ambiguous_price_is_review_only(self):
        self.assertEqual(evaluate_offer(offer(verificar=["Preço condicionado"]), config(), [])[
            "classificacao"], "VERIFICAR")

    def test_budget_unknown_prevents_urgent(self):
        cfg = config()
        cfg["montagem"]["RAM"]["estimativa_cartao"] = None
        result = evaluate_offer(offer(), cfg, [])
        self.assertNotEqual(result["classificacao"], "COMPRAR AGORA")
        self.assertIsNone(result["orcamento"]["conclusao_viavel"])

    def test_ram_prefers_card_at_actual_card_total(self):
        item = offer(peca="DDR5 16GB", categoria="RAM", pix_centavos=140000,
                     cartao_centavos=149000, meta=1500, parcelas=10,
                     parcelamento_sem_juros=True, total_parcelado_sem_juros_centavos=149000)
        result = evaluate_offer(item, config(), [])
        self.assertEqual(result["pagamento"], "cartao")
        self.assertEqual(result["preco_centavos"], 149000)

    def test_exceptional_ram_pix_does_not_override_card_policy(self):
        item = offer(peca="DDR5 16GB", categoria="RAM", pix_centavos=100000,
                     cartao_centavos=149000, meta=1500, parcelas=10,
                     parcelamento_sem_juros=True, total_parcelado_sem_juros_centavos=149000)
        result = evaluate_offer(item, config(), [])
        self.assertEqual(result["pagamento"], "cartao")
        self.assertEqual(result["preco_centavos"], 149000)
        self.assertEqual(result["orcamento"]["total_projetado_centavos"], 951000)
        self.assertNotEqual(result['classificacao'], 'NÃO RECOMENDAR')

    def test_bought_category_is_not_recommended(self):
        result = evaluate_offer(offer(peca="DDR5 16GB", categoria="RAM", meta=1500,
                                     cartao_centavos=100000), config(), [purchase()])
        self.assertEqual(result["classificacao"], "NÃO COMPENSA")

    def test_money_and_manual_input_validation(self):
        self.assertEqual(cents("0.105"), 11)
        self.assertEqual(installments(100, 3), [34, 33, 33])
        for value in ["NaN", "Infinity", -1, True]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                cents(value)
        for overrides in [{"parcelas": 0}, {"parcelas": 3.5}, {"parcelas_pagas": 4},
                          {"primeira_parcela": "2026-13"}, {"primeira_parcela": "2026-1"},
                          {"total_centavos": 10.5}, {"pagamento": "cash"}]:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                validate_purchase(purchase(**overrides))


if __name__ == "__main__":
    unittest.main()
