import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from parsing import parse_offers
from runtime import make_notifications
from settings import load_config
from storage import Store

class PrecoAnunciado(unittest.TestCase):
    def test_examples_produce_safe_alerts(self):
        cfg=load_config(); now=datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as d:
            with Store(Path(d)/'db') as store:
                store.set_meta('alerts_started_at', now.isoformat())
                examples=[('CORSAIR Memória Vengeance DDR5 RAM 16GB (1x16GB) 5200MHz\nPreço: R$ 1477', 'RAM'),
                          ('750w Fonte Cooler Master Mwe Gold 750 V3 Atx 3.1 80 Plus Gold\nPreço: R$ 327\nCupom: 0211MELIMAIS (Meli+)', 'FONTE'),
                          ('Processador Amd Ryzen 7 7800X3d\nPreço: R$ 1683\nCupom: THEKING', 'CPU')]
                for i,(text, category) in enumerate(examples):
                    with self.subTest(category=category):
                        offer=parse_offers('lapromotion',i+1,now,text,cfg)[0]
                        self.assertEqual(offer['categoria'], category)
                        self.assertIsNone(offer['pix_centavos']); self.assertIsNone(offer['cartao_centavos'])
                        alerts=make_notifications(store,offer,cfg,[],now)
                        if category == 'FONTE':
                            # A preferência atual exige fontes de 850 W.
                            self.assertEqual(alerts, [])
                            continue
                        self.assertEqual(len(alerts),1)
                        self.assertIn('VERIFICAR PREÇO',alerts[0]['payload'])
                        self.assertIn('Forma avaliada: não informado',alerts[0]['payload'])
                        self.assertIn('PIX: não informado | Cartão total: não informado',alerts[0]['payload'])
                        self.assertNotIn('COMPRAR AGORA',alerts[0]['payload'])
    def test_expensive_or_fan_excluded(self):
        cfg=load_config(); now=datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as d:
            with Store(Path(d)/'db') as store:
                store.set_meta('alerts_started_at',now.isoformat())
                for text in ('Ryzen 7 7800X3D\nPreço: R$ 4000', 'Kit de ventoinhas gabinete\nPreço: R$ 50'):
                    offer=parse_offers('lapromotion',3,now,text,cfg)[0]
                    self.assertEqual(make_notifications(store,offer,cfg,[],now),[])
