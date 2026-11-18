import unittest
from lojas_dados import _special, _special_message

ITEM = {'nome':'Gigabyte RX 9070 XT','url':'https://www.kabum.com.br/produto/747554','meta_centavos':550000,
        'monitoramento_especial':{'cupom':'ZAFADINHO13','desconto_percentual':13,'aproximacao_centavos':650000,'prioridade_centavos':640000,'queda_centavos':10000}}
def obs(price, freight=None, **kw):
    return dict(status='preco_publico',preco_publico_centavos=price,frete_centavos=freight,**kw)
class SpecialTests(unittest.TestCase):
    def test_calculation_and_unknown_shipping(self):
        event, values = _special(ITEM, obs(709999), None)
        self.assertIsNone(event)
        self.assertEqual(values[1],617699)
        self.assertIsNone(values[3])
        msg=_special_message(ITEM,obs(632184),'ALTA PRIORIDADE',_special(ITEM,obs(632184),None)[1],'data')
        self.assertIn('não validado',msg)
        self.assertIn('Frete não verificado',msg)
    def test_thresholds_and_rounding(self):
        for price, event in [(650001,None),(650000,'APROXIMAÇÃO DA META'),(640000,'ALTA PRIORIDADE')]:
            self.assertEqual(_special(ITEM,obs(price),None)[0],event)
        self.assertEqual(_special(ITEM,obs(632184,0),None)[0],'META ESTIMADA ATINGIDA')
        self.assertEqual(_special(ITEM,obs(632184,1),None)[0],'ALTA PRIORIDADE')
        self.assertEqual(_special(ITEM,obs(632184),None)[0],'ALTA PRIORIDADE')
    def test_change_and_dedup(self):
        self.assertEqual(_special(ITEM,obs(660000),obs(670000))[0],'QUEDA DE R$ 100 OU MAIS')
        self.assertIsNone(_special(ITEM,obs(640000),obs(640000))[0])
        self.assertIsNone(_special(ITEM,dict(status='produto_esgotado'),obs(640000))[0])
    def test_expired_coupon_never_asserted_valid(self):
        item={**ITEM,'monitoramento_especial':{**ITEM['monitoramento_especial'],'cupom':'EXPIRADO'}}
        event,values=_special(item,obs(630000,0),None)
        self.assertIn('não validado',_special_message(item,obs(630000,0),event,values,'data'))
