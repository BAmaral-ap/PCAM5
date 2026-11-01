import unittest
from datetime import datetime, timezone

from parsing import parse_offers, cents, normalize_channel


DATE = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)


def parse(text, config=None):
    return parse_offers("pcbuildwizard", 42, DATE, text, config)


class ParsingTests(unittest.TestCase):
    def test_exact_gpu_variants_and_triple_fan(self):
        for suffix, model in [("XT", "RX 9070 XT 16GB"), ("GRE", "RX 9070 GRE 12GB"), ("", "RX 9070 16GB")]:
            with self.subTest(suffix=suffix):
                offers = parse(f"Placa de Vídeo RX 9070{suffix} Triple Fan\nR$ 4.000,00 no PIX")
                self.assertEqual(len(offers), 1)
                self.assertEqual(offers[0]["categoria"], "GPU")
                self.assertEqual(offers[0]["peca"], model)
                self.assertEqual(offers[0]["pix_centavos"], 400000)

    def test_price_and_installments_are_separate(self):
        offer = parse("Ryzen 7 7800X3D\nR$ 1.700 no PIX ou 10x de R$ 190,99 no cartão")[0]
        self.assertEqual(offer["pix_centavos"], 170000)
        self.assertEqual(offer["cartao_centavos"], 190990)
        self.assertEqual(offer["parcelas"], 10)

    def test_explicit_card_total(self):
        offer = parse("Ryzen 7 7800X3D\nPIX: R$ 1.700\nCartão: R$ 1.900")[0]
        self.assertEqual(offer["pix_centavos"], 170000)
        self.assertEqual(offer["cartao_centavos"], 190000)

    def test_unknown_payment_is_not_pix(self):
        offer = parse("Ryzen 7 7800X3D R$ 1.700")[0]
        self.assertIsNone(offer["pix_centavos"])
        self.assertIsNone(offer["cartao_centavos"])
        self.assertEqual(offer["anunciado_centavos"], 170000)
        self.assertTrue(offer["verificar"])

    def test_history_strikethrough_and_old_prices_ignored(self):
        offer = parse("RX 9070 XT\nDe R$ 5.000 por R$ 4.500 no PIX\n~~R$ 3.000~~\nMenor preço histórico R$ 2.000")[0]
        self.assertEqual(offer["pix_centavos"], 450000)
        self.assertIsNone(offer["anunciado_centavos"])

    def test_same_line_history_ignored(self):
        offer = parse("RX 9070 XT R$ 4.500 no PIX | menor preço R$ 3.900")[0]
        self.assertEqual(offer["pix_centavos"], 450000)

    def test_real_flattened_csv_payment_and_history(self):
        offer = parse("Gabinete Geometric Future Model 5 Vent White, E-ATX R$ 629,99 no PIX R$ 637,40 à vista no cartão de crédito Frete Grátis: Sul e Sudeste Loja: Pichau https://www.pichau.com.br/gabinete-sem-fonte Menor preço em 90 dias: R$ 621,95 Mediana dos preços de 90 dias: R$ 631,89")[0]
        self.assertEqual(offer["categoria"], "GABINETE")
        self.assertEqual(offer["pix_centavos"], 62999)
        self.assertEqual(offer["cartao_centavos"], 63740)
        self.assertIsNone(offer["anunciado_centavos"])

    def test_case_without_psu_is_not_a_bundle(self):
        offers = parse("Gabinete Lian Li Vector V100 Black, ATX sem fonte R$ 419,99 no PIX https://example.com/gabinete-sem-fonte")
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0]["categoria"], "GABINETE")
        self.assertEqual(offers[0]["pix_centavos"], 41999)

    def test_total_with_installment_count(self):
        offer = parse("Gabinete darkFlash Arc1 White R$ 249,99 em 6 parcelas no cartão de crédito")[0]
        self.assertEqual(offer["cartao_centavos"], 24999)
        self.assertEqual(offer["parcelas"], 6)

    def test_written_installments(self):
        offer = parse("Ryzen 7 7800X3D 10 parcelas de R$ 200")[0]
        self.assertEqual(offer["cartao_centavos"], 200000)
        self.assertEqual(offer["parcelas"], 10)

    def test_discount_and_freight_not_product_prices(self):
        offer = parse("Ryzen 7 7800X3D R$ 1.700 no PIX\nFrete: R$ 30\nDesconto de R$ 100\nCashback R$ 200")[0]
        self.assertEqual(offer["preco"], 1700)
        self.assertIsNone(offer["anunciado_centavos"])

    def test_partial_installment_never_total(self):
        offer = parse("Ryzen 7 7800X3D\nParcela de R$ 500")[0]
        self.assertIsNone(offer["preco"])
        self.assertTrue(offer["verificar"])

    def test_conflicting_values_do_not_choose_minimum(self):
        offer = parse("Ryzen 7 7800X3D\nR$ 1.700 no PIX\nR$ 1.500 no PIX")[0]
        self.assertIsNone(offer["pix_centavos"])
        self.assertIsNone(offer["preco"])

    def test_conflicting_card_arithmetic(self):
        offer = parse("Ryzen 7 7800X3D\n10x R$ 200\nTotal cartão: R$ 1.500")[0]
        self.assertIsNone(offer["cartao_centavos"])
        self.assertTrue(offer["verificar"])

    def test_multiple_products_have_own_prices(self):
        offers = parse("Ryzen 7 7800X3D\nR$ 1.700 no PIX\nRyzen 7 9800X3D\nR$ 2.050 no PIX")
        self.assertEqual([(o["peca"], o["pix_centavos"]) for o in offers], [("Ryzen 7 7800X3D", 170000), ("Ryzen 7 9800X3D", 205000)])

    def test_same_line_combo_is_ambiguous(self):
        offers = parse("Combo Ryzen 7 7800X3D + ASUS B650M-AYW R$ 2.200 no PIX")
        self.assertEqual(len(offers), 2)
        self.assertTrue(all(o["preco"] is None and o["verificar"] for o in offers))

    def test_ram_kits_and_capacity_are_not_conflated(self):
        self.assertEqual(parse("Memória DDR5 16GB R$ 600 no PIX")[0]["peca"], "DDR5 1x16GB")
        self.assertEqual(parse("Kit memória DDR5 2x16GB R$ 1.200 no PIX")[0]["peca"], "DDR5 2x16GB")
        self.assertEqual(parse("Memória DDR5 32GB R$ 900 no PIX")[0]["peca"], "DDR5 1x32GB")
        self.assertTrue(parse("Memória DDR5 SODIMM 16GB R$ 600 no PIX")[0]["verificar"])

    def test_motherboard_ddr5_is_not_memory(self):
        self.assertEqual(parse("Placa mãe ASUS B650M-AYW DDR5 16GB R$ 680 no PIX")[0]["categoria"], "PLACA-MAE")
        self.assertEqual(parse("Placa mãe Intel B760 DDR5 R$ 680"), [])

    def test_psu_variants_remain_separate(self):
        for variant in ("", " II", " II VE"):
            offer = parse(f"Fonte XPG Core Reactor{variant} 850W R$ 490 no PIX")[0]
            self.assertEqual(offer["peca"], f"XPG Core Reactor{variant} 850W")

    def test_coupon_store_link_and_full_source(self):
        text = "RX 9070 XT\nR$ 4.500 no PIX\nCupom no APP `TECH8DO8`\nLoja: Kabum\nhttps://kabum.com.br/produto/123\n" + "Detalhes " * 100
        offer = parse(text)[0]
        self.assertEqual(offer["cupom"], "TECH8DO8")
        self.assertEqual(offer["loja"], "Kabum")
        self.assertEqual(offer["link_loja"], "https://kabum.com.br/produto/123")
        self.assertEqual(offer["texto"], text)
        self.assertTrue(offer["condicoes"])

    def test_conditional_discount_not_assumed_pix(self):
        offer = parse("RX 9070 XT R$ 4.499,99 no PIX\nR$ 4.049,99 com o cupom TESTE")[0]
        self.assertEqual(offer["pix_centavos"], 449999)
        self.assertEqual(offer["anunciado_centavos"], 404999)
        self.assertTrue(offer["verificar"])

    def test_explicit_parenthesized_coupon_uses_pix_payment(self):
        from finance import evaluate_offer
        from settings import load_config
        text = ("Processador AMD Ryzen 7 7800X3D, 8-Core, SMT, AM5\n"
                "R$ 1.849,99 no PIX (R$ 1.683,49 com o cupom THEKING)\n"
                "Loja: KaBuM!\nhttps://www.kabum.com.br/produto/426262\n"
                "Menor preço em 90 dias: R$ 1.699,00\n"
                "Mediana dos preços de 90 dias: R$ 1.978,90")
        offer = parse(text)[0]
        self.assertEqual(offer['pix_centavos'], 168349)
        self.assertIsNone(offer['cartao_centavos'])
        self.assertIsNone(offer['anunciado_centavos'])
        self.assertEqual(offer['cupom'], 'THEKING')
        self.assertFalse(offer['verificar'])
        self.assertEqual(offer['texto'], text)
        self.assertTrue(any('não verificada' in c for c in offer['condicoes']))
        evaluation = evaluate_offer(offer, load_config(), [])
        self.assertEqual(evaluation['classificacao'], 'BOA PROMOÇÃO')
        self.assertEqual(evaluation['diferenca_centavos'], -1651)

    def test_pix_coupon_does_not_change_card_total(self):
        offer = parse('Ryzen 7 7800X3D\nR$ 1.849,99 no PIX (R$ 1.683,49 com o cupom THEKING)\nCartão: R$ 1.999,00')[0]
        self.assertEqual(offer['pix_centavos'], 168349)
        self.assertEqual(offer['cartao_centavos'], 199900)

    def test_coupon_eligibility_still_requires_verification(self):
        offer = parse('Ryzen 7 7800X3D R$ 1.849,99 no PIX (R$ 1.683,49 com o cupom THEKING)\nExclusivo para primeira compra')[0]
        self.assertEqual(offer['pix_centavos'], 168349)
        self.assertTrue(offer['verificar'])

    def test_card_coupon_does_not_inherit_pix(self):
        offer = parse('Ryzen 7 7800X3D R$ 1.849,99 no PIX (R$ 1.683,49 no cartão com o cupom THEKING)')[0]
        self.assertEqual(offer['pix_centavos'], 184999)
        self.assertEqual(offer['cartao_centavos'], 168349)

    def test_inconsistent_coupon_amount_not_accepted_as_pix(self):
        offer = parse('Ryzen 7 7800X3D R$ 1.600,00 no PIX (R$ 1.683,49 com o cupom THEKING)')[0]
        self.assertEqual(offer['pix_centavos'], 160000)
        self.assertTrue(offer['verificar'])

    def test_targets_and_explicit_alternative(self):
        config = {"metas": {"Ryzen 7 7800X3D": 1600}, "alternativas": [{"modelo": "Air Cooler Modelo ABC", "categoria": "COOLER-CPU", "padrao": "Modelo ABC", "meta": 180}]}
        self.assertEqual(parse("Ryzen 7 7800X3D R$ 1.700 no PIX", config)[0]["meta"], 1600)
        self.assertEqual(parse("Air Cooler Modelo ABC R$ 170 no PIX", config)[0]["peca"], "Air Cooler Modelo ABC")

    def test_exact_configured_sku_overrides_family(self):
        config = {"alternativas": [{"modelo": "Sapphire Nitro+ RX 9070 XT 16GB", "categoria": "GPU", "padrao": r"Sapphire\s+Nitro\+.*RX\s*9070\s*XT", "meta": 4800}]}
        offers = parse("Sapphire Nitro+ RX 9070 XT 16GB R$ 4.700 no PIX", config)
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0]["peca"], "Sapphire Nitro+ RX 9070 XT 16GB")
        self.assertTrue(offers[0]["modelo_exato"])
        unknown = parse("Gabinete Marca Desconhecida ABC R$ 200 no PIX")[0]
        self.assertFalse(unknown["modelo_exato"])

    def test_legacy_generic_target_aliases(self):
        cfg = {"metas": {"Gabinete": 220, "Air cooler CPU": 180}}
        self.assertEqual(parse("Gabinete ABC R$ 200 no PIX", cfg)[0]["meta"], 220)
        self.assertEqual(parse("Air cooler ABC R$ 160 no PIX", cfg)[0]["meta"], 180)

    def test_currency_parser_rejects_malformed_values(self):
        self.assertEqual(cents("4.700,99"), 470099)
        self.assertEqual(cents("4700.99"), 470099)
        self.assertEqual(cents("4.700"), 470000)
        self.assertIsNone(cents("1.2.3"))
        self.assertIsNone(cents("1,234,56"))

    def test_channel_normalization_and_timezone(self):
        self.assertEqual(normalize_channel("https://t.me/LaPromotion"), "lapromotion")
        self.assertTrue(parse("RX 9070 XT R$ 4.500 no PIX")[0]["data"].endswith("+00:00"))
        with self.assertRaises(ValueError):
            parse_offers("channel", 1, datetime(2026, 11, 2), "RX 9070 XT R$ 4.500")


if __name__ == "__main__":
    unittest.main()
