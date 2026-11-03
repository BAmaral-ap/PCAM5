import tempfile, unittest
from datetime import datetime,timezone
from pathlib import Path
from rx9070 import price_candidates,tier
from parsing import parse_offers
from runtime import make_notifications
from settings import load_config
from storage import Store

class RXTests(unittest.TestCase):
 def test_boundaries(self):
  for amount,label in [(3937,'IMPERDÍVEL'),(3978,'IMPERDÍVEL'),(4700,'IMPERDÍVEL'),(4800,'EXCELENTE'),(4999,'BOA COMPRA'),(5100,'BOA COMPRA'),(5200,'AVALIAR'),(5400,'PREÇO ALTO')]:
   self.assertEqual(tier(amount*100,{}),label)
 def test_variants(self):
  for name in ['RX 9070 XT','RX9070XT','9070XT','Radeon 9070 XT','RX-9070-XT','AMD Radeon RX 9070 XT']:
   o=parse_offers('canal',1,datetime.now(timezone.utc),name+' R$ 3.978',load_config())
   self.assertEqual(o[0]['peca'],'RX 9070 XT 16GB',name)
  for name in ['RX 9070','RX 7900 XT','RX 7800 XT']:
   self.assertFalse(any(o['peca']=='RX 9070 XT 16GB' for o in parse_offers('canal',1,datetime.now(timezone.utc),name+' R$ 3.978',load_config())))
 def test_price_formats(self):
  for value in ['R$ 3.937','R$3.937,00','R$ 3,937','3937','3.937','💰 3.937','3937 no Pix','3937 com cupom']:
   self.assertEqual(price_candidates('RX 9070 XT\n'+value),[393700],value)
  self.assertEqual(price_candidates('RX 9070 XT De R$ 5.599 por R$ 4.899\nMenor preço em 90 dias: R$ 3.100'),[489900])
  self.assertEqual(price_candidates('RX 9070 XT 16GB código 12345'),[])
 def test_historical_examples_alert(self):
  cfg=load_config();now=datetime.now(timezone.utc)
  with tempfile.TemporaryDirectory() as d, Store(Path(d)/'db') as store:
   store.set_meta('alerts_started_at',now.isoformat())
   for i,text in enumerate(['ASRock Radeon RX 9070 XT Steel Legend White 16 GB\nR$ 3.937\nhttps://s.shopee.com.br/7puAKVTh7m','XFX Swift RX 9070 XT Triple Fan 16 GB\nR$ 3.978\nhttps://amzn.to/4xZr4DD']):
    offer=parse_offers('poisonpromos',i+1,now,text,cfg)[0]
    result=make_notifications(store,offer,cfg,[],now)
    self.assertEqual(len(result),1)
    self.assertIn('IMPERDÍVEL',result[0]['payload'])
    self.assertIn('checkout',result[0]['payload'])
