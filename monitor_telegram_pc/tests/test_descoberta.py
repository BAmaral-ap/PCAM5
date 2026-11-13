import json
import tempfile
import unittest
from pathlib import Path
from copy import deepcopy
from descoberta import public_host
from descoberta_fontes import xml_links
from produtos_validacao import valid_gpu
from lojas import ShopMonitor
from lojas_rede import FetchError, Response
from storage import Store
from test_lojas import html, Clock, ROBOTS

URL='https://www.kabum.com.br/produto/999/asrock-rx-9070-xt-challenger'

class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'db'); self.addCleanup(self.store.close)
        self.clock=Clock(); self.calls=[]
        self.page=html('4699', extra={'itemCondition':'https://schema.org/NewCondition'})
        self.catalog={'lojas':{'kabum':{'termos_verificados':True,'descobrir':False}}}
        self.cfg={'descoberta':{'ativado':True,'fontes':[{'id':'busca','tipo':'brave','ativado':True,'custos_autorizados':True}]}}
        def fetch(url):
            self.calls.append(url)
            return Response(200,ROBOTS,'text/plain') if url.endswith('/robots.txt') else Response(200,self.page,'text/html')
        self.monitor=ShopMonitor(self.store,self.catalog,[],self.cfg,fetch=fetch,now=self.clock.now,sleep=self.clock.sleep,jitter=lambda:0)
        self.monitor.discovery.api_search=lambda source,query:[URL,URL+'?utm_source=teste']

    async def test_search_validates_registers_alerts_and_survives_restart(self):
        await self.monitor.check_due()
        self.assertEqual(len(self.monitor.items),1)
        self.assertEqual(len(self.store.pending_notifications()),1)
        self.assertIn('IMPERDÍVEL',self.store.pending_notifications()[0]['payload'])
        self.assertEqual(self.monitor.items[0]['intervalo_segundos'],1200)
        self.assertEqual(self.monitor.items[0]['sku'],'XYZ-123')
        await self.monitor.check_due()
        self.assertEqual(len(self.store.pending_notifications()),1)
        other=ShopMonitor(self.store,self.catalog,[],self.cfg)
        self.assertEqual(len(other.items),1)
        self.assertEqual(len(other.tracking.history(other.items[0])),1)

    async def test_wrong_products_and_used_are_not_admitted(self):
        for name in ['RX 9070 16GB','RX 7900 XT','Computador gamer RX 9070 XT', 'RX 9070 XT usada', 'RX 9070 XT 8GB']:
            self.page=html('4699',product_name=name)
            self.monitor.discovery.ingest(URL,'teste')
            row=self.store.db.execute('SELECT * FROM discovery_urls').fetchone()
            await self.monitor.discovery.validate(row)
            self.assertEqual(self.monitor.items,[],name)
        self.page=html('4699',extra={'itemCondition':'https://schema.org/UsedCondition'})
        await self.monitor.discovery.validate(row)
        self.assertEqual(self.monitor.items,[])
        self.assertEqual(self.store.notifications(),[])

    async def test_unreviewed_store_and_unknown_domains_never_fetched(self):
        self.catalog['exigir_termos_verificados'] = True
        self.catalog['lojas']['kabum']['termos_verificados']=False
        self.monitor.discovery.ingest(URL,'telegram')
        self.monitor.discovery.ingest('https://nova-loja.com.br/rx-9070-xt','busca')
        await self.monitor.discovery.tick()
        self.assertEqual(self.calls,[])
        states={r['estado'] for r in self.store.db.execute('SELECT estado FROM discovery_urls')}
        self.assertIn('termos_nao_verificados',states);self.assertIn('loja_pendente',states)

    async def test_failed_source_does_not_stop_second_source(self):
        self.cfg['descoberta']['fontes'].append({'id':'outra','tipo':'google_cse','ativado':True})
        def search(source,query):
            if source['id']=='busca': raise FetchError('api_http_429','limit',10000)
            return [URL]
        self.monitor.discovery.api_search=search
        await self.monitor.discovery.tick()
        self.assertEqual(len(self.monitor.items),1)
        failed=self.store.db.execute("SELECT * FROM discovery_sources WHERE id='busca'").fetchone()
        self.assertEqual(failed['estado'],'api_http_429')
        self.assertGreater(failed['proxima'],self.clock.now()+9000)

    async def test_telegram_direct_links_and_canonical_duplicates(self):
        self.monitor.prioritize_message('XFX RX 9070 XT\n'+URL+'?utm_source=canal')
        self.monitor.discovery.ingest(URL,'feed')
        rows=list(self.store.db.execute('SELECT * FROM discovery_urls'))
        self.assertEqual(len(rows),1)
        self.assertEqual(set(json.loads(rows[0]['origem'])),{'telegram','feed'})

    async def test_no_notify_records_history(self):
        self.monitor.notifications_enabled=False
        await self.monitor.check_due()
        self.assertEqual(self.store.notifications(),[])
        self.assertEqual(len(self.monitor.tracking.history(self.monitor.items[0])),1)

    async def test_restock_price_loss_and_new_drop(self):
        await self.monitor.check_due()
        self.clock.t+=1300;self.page=html('5500')
        await self.monitor.check_due()
        self.assertIn('PROMOÇÃO ENCERRADA', self.store.notifications()[-1]['payload'])
        self.clock.t+=1300;self.page=html('5000','OutOfStock')
        await self.monitor.check_due()
        count=len(self.store.notifications())
        self.clock.t+=1300;self.page=html('5000')
        await self.monitor.check_due()
        self.assertEqual(len(self.store.notifications()),count+1)
        self.assertIn('VOLTOU AO ESTOQUE',self.store.notifications()[-1]['payload'])

    async def test_history_requires_multiple_days_for_mean(self):
        await self.monitor.check_due();item=self.monitor.items[0]
        stats=self.monitor.tracking.statistics(item,self.clock.now())
        self.assertIsNone(stats['preco_publico_centavos']['7_dias']['media_centavos'])
        self.clock.t+=86400;self.page=html('4499')
        await self.monitor.check_due()
        stats=self.monitor.tracking.statistics(item,self.clock.now())['preco_publico_centavos']
        self.assertEqual(stats['menor_observado_centavos'],449900)
        self.assertEqual(stats['7_dias']['media_centavos'],459900)

    async def test_feed_discovers_urls_without_prices_from_feed(self):
        source={'id':'rss','tipo':'feed','url':'https://www.kabum.com.br/feed','loja':'kabum','ativado':True}
        async def read(url,store): return '<rss><channel><item><link>'+URL+'</link><price>1</price></item></channel></rss>'
        self.monitor.discovery.public_read=read
        await self.monitor.discovery.scan(source)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM discovery_urls').fetchone()[0],1)
        self.assertEqual(self.store.notifications(),[])

class ValidationTests(unittest.TestCase):
    def test_names(self):
        for title in ['RX9070XT', 'RX-9070-XT', 'Sapphire Radeon RX 9070 XT 16GB']:
            self.assertTrue(valid_gpu(title))
        self.assertFalse(valid_gpu('Cabo RX 9070 XT'))
    def test_private_and_credential_urls(self):
        for url in ['http://example.com/a','https://127.0.0.1/a','https://localhost/a','https://x.local/a','https://user:pass@site.com/a']:
            with self.assertRaises(ValueError):public_host(url)
    def test_xml_entity_rejection(self):
        with self.assertRaises(ValueError):xml_links('<!DOCTYPE x><rss/>')

class SitemapTests(unittest.IsolatedAsyncioTestCase):
    setUp = DiscoveryTests.setUp
    async def test_sitemap_index_rotates_and_filters_urls(self):
        source={'id':'map','tipo':'sitemap','url':'https://www.kabum.com.br/sitemap.xml','loja':'kabum','ativado':True}
        visited=[]
        async def read(url,store):
            visited.append(url)
            if url.endswith('sitemap.xml'):
                return '<sitemapindex><sitemap><loc>https://www.kabum.com.br/products.xml</loc></sitemap></sitemapindex>'
            return '<urlset><url><loc>'+URL+'</loc></url><url><loc>https://www.kabum.com.br/rx-7900-xt</loc></url></urlset>'
        self.monitor.discovery.public_read=read
        await self.monitor.discovery.scan(source)
        self.assertEqual(len(visited),2)
        self.assertEqual([r['url'] for r in self.store.db.execute('SELECT url FROM discovery_urls')],[URL])

    async def test_origin_retained_after_admission(self):
        await self.monitor.check_due()
        self.monitor.discovery.ingest(URL,'outro_canal')
        row=self.store.db.execute('SELECT origem FROM discovery_urls').fetchone()
        self.assertIn('outro_canal',json.loads(row['origem']))

    async def test_later_used_condition_does_not_alert_as_new(self):
        await self.monitor.check_due()
        count=len(self.store.notifications())
        self.clock.t+=1300
        self.page=html('3500',extra={'itemCondition':'https://schema.org/UsedCondition'})
        await self.monitor.check_due()
        self.assertEqual(len(self.store.notifications()),count)
        self.assertEqual(self.monitor.tracking.target(self.monitor.items[0])['last_status'],'extracao_falhou')

class APITests(unittest.TestCase):
    def test_disabled_billing_and_missing_key_make_no_network_requests(self):
        from descoberta_fontes import search
        from unittest.mock import patch
        with patch('urllib.request.build_opener') as network, patch.dict('os.environ',{},clear=True):
            with self.assertRaises(FetchError):search({'tipo':'brave'},'RX 9070 XT')
            with self.assertRaises(FetchError):search({'tipo':'brave','custos_autorizados':True},'RX 9070 XT')
            network.assert_not_called()

class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    setUp = DiscoveryTests.setUp
    async def test_two_stores_discovered_without_manual_product_catalog(self):
        self.catalog['lojas']['pichau']={'termos_verificados':True,'descobrir':False}
        self.monitor.discovery.api_search=lambda source,query:[URL,'https://www.pichau.com.br/asrock-rx-9070-xt']
        await self.monitor.check_due()
        self.assertEqual({i['loja'] for i in self.monitor.items},{'kabum','pichau'})
        self.assertEqual(len(self.store.notifications()),2)
    async def test_discovery_respects_purchased_gpu(self):
        self.store.add_purchase({'categoria':'GPU','modelo':'RX 9070 XT','pagamento':'pix','total_centavos':450000})
        await self.monitor.check_due()
        self.assertEqual(len(self.monitor.items),1)
        self.assertEqual(self.store.notifications(),[])

class PublicAccessTests(unittest.IsolatedAsyncioTestCase):
    setUp = DiscoveryTests.setUp
    async def test_known_store_works_without_manual_terms_checkbox(self):
        self.catalog['lojas']['kabum']['termos_verificados']=False
        await self.monitor.check_due()
        self.assertEqual(len(self.monitor.items),1)
        self.assertTrue(self.calls)
    async def test_old_terms_wait_cleared_without_clearing_block_pause(self):
        self.catalog['exigir_termos_verificados']=True
        self.catalog['lojas']['kabum']['termos_verificados']=False
        self.monitor.discovery.ingest(URL,'telegram')
        await self.monitor.discovery.tick()
        self.monitor.tracking.host('loja:kabum')
        self.monitor.tracking.pause_host('loja:kabum',self.clock.now()+10000,'bloqueado')
        self.catalog['exigir_termos_verificados']=False
        from descoberta import Discovery
        new=Discovery(self.monitor)
        row=self.store.db.execute('SELECT * FROM discovery_urls').fetchone()
        self.assertEqual(row['proxima'],0)
        await new.validate(row)
        self.assertEqual(self.calls,[])
        self.assertGreater(self.monitor.tracking.host('loja:kabum')['paused_until'],self.clock.now())
