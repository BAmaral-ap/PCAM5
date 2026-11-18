import copy
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timezone
from inventario import PURCHASES, LEGACY_PURCHASES, CPU_UPDATE_ID, updated_config, apply_confirmed, reactivate
from finance import budget_snapshot
from settings import load_config
from storage import Store
from parsing import parse_offers
from runtime import make_notifications
from produtos_validacao import valid_ram

class CurrentBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'db');self.addCleanup(self.store.close)
        self.cfg=updated_config(load_config())
        self.now=datetime.now(timezone.utc)
        self.store.set_meta('alerts_started_at',self.now.isoformat())
    def migrate(self):
        apply_confirmed(self.store,self.cfg)
        return budget_snapshot(self.cfg,self.store.purchases())
    def alerts(self,text):
        offers=parse_offers('teste',1,self.now,text,self.cfg)
        self.assertTrue(offers,text)
        return [n for o in offers for n in make_notifications(self.store,o,self.cfg,self.store.purchases(),self.now)]
    def test_six_exact_purchases_only_two_pending(self):
        report=self.migrate()
        self.assertEqual([p['total_centavos'] for p in self.store.purchases()],[21581,31712,39505,206002,71847,81100])
        self.assertEqual(report['total_comprado_centavos'],451747)
        self.assertEqual(report['total_pago_avista_centavos'],245745)
        self.assertEqual(report['cartao_comprometido_centavos'],206002)
        self.assertEqual({p['categoria'] for p in report['faltantes']},{'GPU','RAM'})
        self.assertEqual(report['restante_pix_centavos'],510000)
        self.assertEqual(report['restante_cartao_centavos'],180000)
        self.assertEqual(report['total_projetado_centavos'],1141747)
    def test_prior_records_and_installment_payments_preserved(self):
        for p in LEGACY_PURCHASES:self.store.add_purchase(p)
        self.store.set_meta(CPU_UPDATE_ID,True)
        cpu=next(p for p in self.store.purchases() if p['categoria']=='CPU')
        self.store.record_installment(cpu['id'],34334)
        self.migrate();self.migrate()
        self.assertEqual(len(self.store.purchases()),6)
        self.assertEqual(self.store.purchases()[3]['valores_pagos_centavos'],[34334])
        self.assertEqual(self.store.purchases()[3]['dia_vencimento'],10)
    def test_existing_board_and_ssd_not_duplicated(self):
        for p in PURCHASES[4:]:self.store.add_purchase(p)
        self.migrate();self.migrate()
        self.assertEqual(len(self.store.purchases()),6)
    def test_conflicting_real_price_not_overwritten(self):
        self.store.add_purchase(dict(PURCHASES[4],total_centavos=71848))
        with self.assertRaises(ValueError):self.migrate()
        self.assertEqual(len(self.store.purchases()),1)
        self.assertEqual(self.store.purchases()[0]['total_centavos'],71848)
    def test_simulated_gpu_not_in_total_or_owned(self):
        self.store.add_purchase({'categoria':'GPU','modelo':'RX 9070 XT','pagamento':'pix','total_centavos':593314,'monitorar_novamente':True})
        report=self.migrate()
        self.assertEqual(report['total_comprado_centavos'],451747)
        self.assertNotIn('GPU',report['ja_possui'])
        self.assertEqual(self.store.purchases()[0]['status_compra'],'SIMULACAO')
    def test_only_gpu_and_ram_alert(self):
        self.migrate()
        for title in ['Ryzen 7 9800X3D','Placa mãe MSI B650M DDR5 AM5','SSD NVMe M.2 1TB','Fonte 850W','Gabinete ATX','Air cooler CPU','Water cooler CPU']:
            with self.subTest(title=title):self.assertEqual(self.alerts(title+' R$ 200 no PIX'),[])
        self.assertTrue(self.alerts('RX 9070 XT R$ 4700'))
        self.assertTrue(self.alerts('DDR5 1x16GB 6000 CL38 R$ 1800 em 10x sem juros'))
        self.assertTrue(self.alerts('DDR5 1x16GB R$ 1700'))
        self.assertEqual(self.alerts('DDR5 1x16GB 10x de R$ 170 com juros no cartão'),[])
    def test_ram_preferences_and_no_old_active_sale(self):
        self.migrate()
        ram=self.cfg['montagem']['RAM']
        self.assertEqual(ram['fonte_pagamento'],'renda_pessoal_separada')
        self.assertEqual(self.cfg['metas']['DDR5 1x16GB'],1800)
        self.assertEqual(self.cfg['perfis']['Maxsun Terminator B650M WiFi ICE White']['slots_memoria'],4)
        self.assertEqual(self.store.offers(),[])
        for title in ['DDR5 1x16GB 6000 EXPO','Corsair DDR5 16GB 6000 CL38']:self.assertTrue(valid_ram(title))
        for title in ['DDR4 16GB','DDR5 SODIMM 16GB','DDR5 4x16GB','DDR5 32GB']:self.assertFalse(valid_ram(title))
    def test_reactivation_does_not_delete_expenses(self):
        self.migrate();reactivate(self.store,'PLACA-MAE')
        self.migrate()
        self.assertTrue(self.alerts('Placa mãe MSI B650M DDR5 AM5 R$ 650 no PIX'))
        self.assertEqual(budget_snapshot(self.cfg,self.store.purchases())['total_comprado_centavos'],451747)
    def test_stale_corsair_alert_expires_history_remains(self):
        text='Corsair Vengeance DDR5 16GB 6000 CL38 R$ 1802,85'
        offer=parse_offers('teste',2,self.now,text,self.cfg)[0]
        notification={'key':'old:'+offer['link'],'payload':'AVALIAR\nModelo identificado: '+offer['peca']+'\n','date':self.now.isoformat()}
        self.store.record_message('teste',2,self.now,text,[offer],[notification])
        self.migrate()
        self.assertEqual(self.store.notifications()[0]['state'],'expired')
        self.assertEqual(len(self.store.offers()),1)
