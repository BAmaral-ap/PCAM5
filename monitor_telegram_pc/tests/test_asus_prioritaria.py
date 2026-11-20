import copy
import json
import tempfile
import unittest
from pathlib import Path
from lojas import load_catalog
from lojas_dados import StoreTracking, _asus_event
from storage import Store

_, items = load_catalog(Path(__file__).resolve().parents[1] / 'produtos_lojas.json')
ITEM = next(i for i in items if i['id'] == 'kabum-asus-prime-9070xt-690385')
def observation(price, stock='em_estoque', **kw):
    return dict(status='preco_publico' if stock != 'esgotado' else 'produto_esgotado', estoque=stock,
                preco_publico_centavos=price, frete_centavos=None, vendedor='KaBuM!', **kw)

class AsusTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'test.db'); self.addCleanup(self.store.close)
        self.tracking=StoreTracking(self.store); self.tracking.sync([ITEM]); self.now=100000
    def success(self, obs):
        self.now+=300
        return self.tracking.success(ITEM,self.now,obs,{})
    def test_one_real_drop_repeat_rise_and_fall(self):
        self.assertIsNone(self.success(observation(700000)))
        self.assertEqual(self.success(observation(699900)), 'PREÇO CAIU')
        self.assertIsNone(self.success(observation(699900)))
        self.assertIsNone(self.success(observation(700000)))
        self.assertEqual(self.success(observation(690000)), 'PREÇO CAIU')
        payloads=[n['payload'] for n in self.store.pending_notifications()]
        self.assertEqual(len(payloads),2)
        self.assertIn('Queda: R$ 1,00',payloads[0])
        self.assertIn('referência anterior; não confirmado',payloads[0])
        self.assertIn('não confirmadas',payloads[0])
    def test_failure_and_missing_price(self):
        self.success(observation(700000))
        self.tracking.failure(ITEM,self.now+1,'http_erro','simulada')
        self.assertEqual(self.success(observation(699900)),'PREÇO CAIU')
        self.assertIsNone(self.success(observation(None)))
        self.assertEqual(self.success(observation(699800)), 'PREÇO CAIU')
    def test_stock_and_seller(self):
        self.success(observation(700000))
        self.assertEqual(self.success(observation(None,'esgotado')),'PRODUTO ESGOTADO')
        self.assertEqual(self.success(observation(700000)),'PRODUTO VOLTOU AO ESTOQUE')
        other=observation(700000); other['vendedor']='Outro'
        self.assertEqual(self.success(other),'VENDEDOR ALTERADO')
    def test_thresholds(self):
        self.assertEqual(_asus_event(ITEM,observation(620000),None),'META ESTIMADA ATINGIDA')
        self.assertEqual(_asus_event(ITEM,observation(660000),None),'OFERTA INTERESSANTE')
        old=observation(700000)
        self.assertEqual(_asus_event(ITEM,observation(660000),old),'QUEDA — OFERTA INTERESSANTE')
        self.assertEqual(_asus_event(ITEM,observation(620000),old),'QUEDA — META ESTIMADA ATINGIDA')
    def test_coupon_change_and_no_expiry_claim(self):
        self.success(observation(700000))
        edited=copy.deepcopy(ITEM); edited['monitoramento_asus']['desconto_percentual']=10
        edited['monitoramento_asus']['cupom']='EXPIRADO'
        self.tracking.sync([edited]); self.now+=300
        self.assertEqual(self.tracking.success(edited,self.now,observation(700000),{}),'CUPOM OU CONDIÇÕES ALTERADAS — CONFIRA')
        msg=self.store.pending_notifications()[0]['payload']
        self.assertIn('não confirmadas',msg)
        self.now+=300
        self.assertIsNone(self.tracking.success(edited,self.now,observation(700000),{}))
