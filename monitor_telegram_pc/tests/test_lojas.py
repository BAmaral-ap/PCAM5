import asyncio
import io
import json
import tempfile
import unittest
import urllib.error
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from lojas import ShopMonitor, load_catalog
from lojas_dados import StoreTracking, key_for
from lojas_extracao import ExtractionError, extract_public_offer
from lojas_rede import FetchError, Response, fetch_public, product_url, robots_allows, validate_catalog
from storage import Store

ITEM = {'id':'test-gpu', 'nome':'ASRock RX 9070 XT Challenger', 'loja':'kabum', 'categoria':'GPU',
        'familia':'RX 9070 XT', 'url':'https://www.kabum.com.br/produto/123/gpu',
        'sku':'XYZ-123', 'sku_loja':'123', 'termos_identificacao':['ASRock','9070','Challenger'],
        'meta_centavos':510000, 'intervalo_segundos':180, 'cupons':[]}
ROBOTS = 'User-agent: *\nAllow: /\nCrawl-delay: 5\n'


def html(price='6999.99', stock='InStock', seller='Loja A', specs=None, sku='XYZ-123', product_name='ASRock RX 9070 XT Challenger', extra=None):
    offer={'@type':'Offer', 'priceCurrency':'BRL', 'availability':'https://schema.org/'+stock, 'seller':{'name':seller}}
    if price is not None: offer['price']=price
    if specs is not None: offer['priceSpecification']=specs
    if extra: offer.update(extra)
    obj={'@context':'https://schema.org','@type':'Product','name':product_name,'sku':sku,'offers':offer}
    return '<html><script type="application/ld+json">'+json.dumps(obj)+'</script><del>R$ 12.999,00</del></html>'


class ExtractTests(unittest.TestCase):
    def test_public_price_stock_and_seller_without_false_shipping_or_coupon(self):
        data=extract_public_offer(html('5039.99'),ITEM)
        self.assertEqual(data['preco_publico_centavos'],503999)
        self.assertEqual(data['status'],'preco_publico')
        self.assertEqual(data['estoque'],'em_estoque')
        self.assertEqual(data['vendedor'],'Loja A')
        self.assertIsNone(data['frete_centavos'])
        self.assertIsNone(data['preco_final_centavos'])
        self.assertFalse(data['cupom_validado'])
        self.assertIsNone(data['preco_com_cupom_centavos'])
    def test_pix_and_cartao_are_distinct(self):
        specs=[{'@type':'UnitPriceSpecification','name':'PIX','price':'5039.99','priceCurrency':'BRL'},
               {'@type':'UnitPriceSpecification','name':'Cartão total','price':'5500.00','priceCurrency':'BRL'},
               {'@type':'UnitPriceSpecification','name':'preço anterior','price':'6999.99','priceCurrency':'BRL'}]
        data=extract_public_offer(html('5500.00',specs=specs),ITEM)
        self.assertEqual(data['preco_pix_centavos'],503999)
        self.assertEqual(data['preco_cartao_centavos'],550000)
        self.assertEqual(data['preco_publico_centavos'],550000)
    def test_shipping_requires_explicit_belem_destination(self):
        detail={'shippingDestination':{'addressLocality':'Belém','addressRegion':'PA','addressCountry':'BR'},
                'shippingRate':{'value':'25.00','currency':'BRL'}}
        data=extract_public_offer(html('5039.99',extra={'shippingDetails':detail}),ITEM)
        self.assertEqual(data['frete_centavos'],2500)
        self.assertIsNone(data['preco_final_centavos'])
        detail['shippingDestination'].pop('addressLocality')
        self.assertIsNone(extract_public_offer(html('5039.99',extra={'shippingDetails':detail}),ITEM)['frete_centavos'])
    def test_explicit_price_without_coupon_and_unverified_coupon(self):
        specs=[{'name':'Preço sem cupom','price':'5500.00','priceCurrency':'BRL'},
               {'name':'Preço com cupom','price':'5039.99','priceCurrency':'BRL'}]
        data=extract_public_offer(html('5500',specs=specs),ITEM)
        self.assertEqual(data['preco_sem_cupom_centavos'],550000)
        self.assertIsNone(data['preco_com_cupom_centavos'])
        self.assertFalse(data['cupom_validado'])
    def test_installment_amount_is_not_total_price(self):
        installment=extract_public_offer(html('503.99',extra={'name':'10 parcelas de R$ 503,99'}),ITEM)
        self.assertEqual(installment['status'],'preco_nao_encontrado')
        self.assertIsNone(installment['preco_publico_centavos'])
        specs=[{'name':'Cartão em 10x', 'price':'503.99', 'priceCurrency':'BRL'}]
        data=extract_public_offer(html('5039.90',specs=specs),ITEM)
        self.assertIsNone(data['preco_cartao_centavos'])
        self.assertEqual(data['preco_publico_centavos'],503990)
    def test_advertised_valid_or_invalid_coupon_is_never_validated(self):
        for value in (True,False):
            data=extract_public_offer(html('5039.99',extra={'coupon':'SALE','couponValid':value}),ITEM)
            self.assertFalse(data['cupom_validado'])
            self.assertIsNone(data['preco_com_cupom_centavos'])
    def test_out_of_stock_without_price_is_not_extraction_failure(self):
        data=extract_public_offer(html(None,'OutOfStock'),ITEM)
        self.assertEqual(data['status'],'produto_esgotado')
        self.assertIsNone(data['preco_publico_centavos'])
    def test_changed_html_wrong_sku_and_missing_price_fail_closed(self):
        for page in ('<html><span>R$ 5.039,99</span></html>', html('5039.99',sku='wrong'), html('5039.99',product_name='ASRock RX 9070 Challenger')):
            with self.subTest(page=page):
                with self.assertRaises(ExtractionError): extract_public_offer(page,ITEM)
    def test_missing_price_with_explicit_stock_remains_unknown(self):
        data=extract_public_offer(html(None,'InStock'),ITEM)
        self.assertEqual(data['status'],'preco_nao_encontrado')
        self.assertEqual(data['estoque'],'em_estoque')
        self.assertIsNone(data['preco_publico_centavos'])
        unknown=extract_public_offer(html(None,'Unknown'),ITEM)
        self.assertEqual(unknown['status'],'preco_nao_encontrado')
        self.assertEqual(unknown['estoque'],'desconhecido')
    def test_challenge_and_ambiguous_marketplace_offers(self):
        with self.assertRaises(ExtractionError): extract_public_offer('<h1>CAPTCHA</h1>',ITEM)
        product = {'@type': 'Product', 'name': 'ASRock RX 9070 XT Challenger', 'sku': 'XYZ-123',
                   'offers': [{'@type':'Offer', 'price':'5039.99', 'priceCurrency':'BRL', 'seller': {'name':'Loja A'}},
                              {'@type':'Offer', 'price':'4999', 'priceCurrency':'BRL', 'seller': {'name':'Loja B'}}]}
        page='<script type="application/ld+json">'+json.dumps(product)+'</script>'
        with self.assertRaises(ExtractionError): extract_public_offer(page,ITEM)


class PolicyTests(unittest.TestCase):
    def test_robots_longest_rule_wildcard_and_rate(self):
        self.assertEqual(robots_allows('User-agent: *\nDisallow: /\nAllow: /produto/\nCrawl-delay: 9',ITEM['url']),9)
        for txt,url in [('User-agent: *\nDisallow: /produto/*',ITEM['url']),
                        ('User-agent: *\nDisallow: /produto/123$', 'https://www.kabum.com.br/produto/123'),
                        ('User-agent: *\nAllow: /\nDisallow: /produto/', ITEM['url'])]:
            with self.subTest(txt=txt), self.assertRaises(FetchError): robots_allows(txt,url)
        self.assertEqual(robots_allows('User-agent: *\nDisallow: /\nUser-agent: PCAM5Monitor\nAllow: /\nRequest-rate: 2/30',ITEM['url']),15)
    def test_unknown_robots_and_encoded_paths_fail_closed(self):
        for rules in ('', 'Sitemap: /sitemap.xml'):
            with self.assertRaises(FetchError): robots_allows(rules,ITEM['url'])
        with self.assertRaises(FetchError): robots_allows('User-agent: *\nDisallow: /abc%ZZ',ITEM['url'])
    def test_url_allowlist_tracking_and_secret_query(self):
        self.assertEqual(product_url('https://www.pichau.com.br/produto?affiliated_code=ABC&utm_source=xx','pichau'), 'https://www.pichau.com.br/produto')
        for url in ('http://www.kabum.com.br/produto/x','https://www.kabum.com.br.evil.com/produto/x', 'https://u:p@www.kabum.com.br/produto/x','https://www.kabum.com.br/produto/x?token=secret'):
            with self.subTest(url=url), self.assertRaises(ValueError): product_url(url,'kabum')
    def test_429_retry_after_is_respected_and_never_exposes_body(self):
        err=urllib.error.HTTPError(ITEM['url'],429,'',{'Retry-After':'120'},io.BytesIO(b'secret'))
        class Opener:
            def open(self,*args,**kwargs): raise err
        with patch('urllib.request.build_opener', return_value=Opener()):
            with self.assertRaises(FetchError) as e: fetch_public(ITEM['url'])
        self.assertEqual(e.exception.status,'limite_429')
        self.assertEqual(e.exception.retry_seconds,120)
        self.assertNotIn('secret',str(e.exception))
    def test_catalog_preserves_input_and_disallows_short_intervals(self):
        c={'produtos':[ITEM]}
        self.assertEqual(validate_catalog(c)[0]['intervalo_segundos'],180)
        self.assertEqual(c['produtos'][0]['url'],ITEM['url'])
        invalid=deepcopy(ITEM); invalid['intervalo_segundos']=30
        with self.assertRaises(ValueError): validate_catalog({'produtos':[invalid]})
        with self.assertRaises(ValueError): validate_catalog({'produtos':[ITEM,ITEM]})
    def test_catalog_honors_am5_and_850w_preferences(self):
        for category, name in [('PLACA-MAE','Placa mãe B550M AM4'), ('FONTE','Fonte 750W'), ('FONTE','Fonte potência desconhecida')]:
            item=deepcopy(ITEM);item['categoria']=category;item['nome']=name
            with self.subTest(name=name), self.assertRaises(ValueError): validate_catalog({'produtos':[item]})
        for category, name in [('PLACA-MAE','Placa mãe B650M AM5'), ('FONTE','Fonte 850 W')]:
            item=deepcopy(ITEM);item['categoria']=category;item['nome']=name
            self.assertEqual(len(validate_catalog({'produtos':[item]})),1)

class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'db.sqlite'); self.addCleanup(self.store.close)
        self.db=StoreTracking(self.store); self.db.sync([ITEM]); self.now=1_793_900_000
    def test_price_drop_and_repeat_dedup_and_another_drop(self):
        self.db.started(ITEM,self.now)
        a=extract_public_offer(html('6999.99'),ITEM)
        b=extract_public_offer(html('5039.99'),ITEM)
        c=extract_public_offer(html('4999.99'),ITEM)
        self.assertIsNone(self.db.success(ITEM,self.now,a,{}))
        self.assertEqual(self.db.success(ITEM,self.now+180,b,{}),'QUEDA DE PREÇO DETECTADA')
        self.assertEqual(len(self.store.pending_notifications()),1)
        message=self.store.pending_notifications()[0]['payload']
        self.assertIn('R$ 6.999,99', message); self.assertIn('R$ 5.039,99', message)
        self.assertIn('Preço com cupom validado: não informado',message)
        self.assertIsNone(self.db.success(ITEM,self.now+360,b,{}))
        self.assertEqual(len(self.store.pending_notifications()),1)
        self.assertEqual(self.db.success(ITEM,self.now+540,c,{}),'PREÇO DENTRO DA META')
        self.assertEqual(len(self.store.pending_notifications()),2)
    def test_previous_promotion_can_reappear_after_price_rises(self):
        high=extract_public_offer(html('6999.99'),ITEM)
        low=extract_public_offer(html('5039.99'),ITEM)
        self.db.success(ITEM,self.now,high,{})
        self.db.success(ITEM,self.now+180,low,{})
        self.db.success(ITEM,self.now+360,high,{})
        self.db.success(ITEM,self.now+540,low,{})
        self.assertEqual(len(self.store.pending_notifications()),2)
    def test_failure_does_not_erase_last_price_or_fake_outage(self):
        a=extract_public_offer(html('6999.99'),ITEM)
        self.db.success(ITEM,self.now,a,{})
        self.db.failure(ITEM,self.now+180,'limite_429','Limite da loja atingido',self.now+600)
        status=self.db.status([ITEM])[0]
        self.assertEqual(status['ultimo_status'],'limite_429')
        self.assertEqual(status['ultima_observacao']['preco_publico_centavos'],699999)
        self.assertEqual(status['ultima_observacao']['estoque'],'em_estoque')
        self.assertEqual(len(self.store.pending_notifications()),0)
        self.assertEqual(len(self.db.history(ITEM)),2)
    def test_observation_and_alert_are_atomic(self):
        self.db.success(ITEM,self.now,extract_public_offer(html('6999.99'),ITEM),{})
        with patch.object(self.store, '_enqueue', side_effect=ValueError('fail')):
            with self.assertRaises(ValueError):
                self.db.success(ITEM,self.now+180,extract_public_offer(html('5039.99'),ITEM),{})
        status=self.db.status([ITEM])[0]
        self.assertEqual(status['ultima_observacao']['preco_publico_centavos'],699999)
        self.assertEqual(len(self.db.history(ITEM)),1)
        self.assertEqual(self.store.pending_notifications(),[])
    def test_sold_out_then_restock_and_seller_change(self):
        out=extract_public_offer(html(None,'OutOfStock'),ITEM)
        stock=extract_public_offer(html('5000','InStock','Loja A'),ITEM)
        changed=extract_public_offer(html('5000','InStock','Loja B'),ITEM)
        self.assertIsNone(self.db.success(ITEM,self.now,out,{}))
        self.assertEqual(self.db.success(ITEM,self.now+180,stock,{}),'PRODUTO VOLTOU AO ESTOQUE')
        self.assertEqual(self.db.success(ITEM,self.now+360,changed,{}),'VENDEDOR ALTERADO')
    def test_out_of_stock_keeps_last_known_price_and_restock_comparison(self):
        self.db.success(ITEM,self.now,extract_public_offer(html('6999.99'),ITEM),{})
        self.db.success(ITEM,self.now+180,extract_public_offer(html(None,'OutOfStock'),ITEM),{})
        status=self.db.status([ITEM])[0]
        self.assertEqual(status['ultimos_precos_conhecidos']['preco_publico_centavos'],699999)
        self.assertEqual(status['ultima_observacao']['estoque'],'esgotado')
        self.assertEqual(self.db.success(ITEM,self.now+360,extract_public_offer(html('5039.99'),ITEM),{}),'PRODUTO VOLTOU AO ESTOQUE')
        self.assertIn('Preço público anterior: R$ 6.999,99',self.store.pending_notifications()[0]['payload'])
    def test_restock_without_price_reports_only_stock(self):
        self.db.success(ITEM,self.now,extract_public_offer(html(None,'OutOfStock'),ITEM),{})
        self.assertEqual(self.db.success(ITEM,self.now+180,extract_public_offer(html(None,'InStock'),ITEM),{}), 'PRODUTO VOLTOU AO ESTOQUE')
        message=self.store.pending_notifications()[0]['payload']
        self.assertIn('Preço atual: não encontrado',message)
        self.assertNotIn('BOA COMPRA',message)
    def test_missing_price_then_same_price_does_not_duplicate(self):
        self.db.success(ITEM,self.now,extract_public_offer(html('5000'),ITEM),{})
        self.db.success(ITEM,self.now+180,extract_public_offer(html(None,'InStock'),ITEM),{})
        self.assertIsNone(self.db.success(ITEM,self.now+360,extract_public_offer(html('5000'),ITEM),{}))
        self.assertEqual(len(self.store.pending_notifications()),1)
    def test_significant_shipping_change_when_destination_is_verified(self):
        first=extract_public_offer(html('5000'),ITEM)
        second=deepcopy(first);first['frete_centavos']=1000;second['frete_centavos']=2500
        self.db.success(ITEM,self.now,first,{})
        self.assertEqual(self.db.success(ITEM,self.now+180,second,{}),'FRETE ALTERADO')
        self.assertIn('Frete anterior para Belém: R$ 10,00',self.store.pending_notifications()[-1]['payload'])
    def test_coupon_config_or_telegram_never_confirms(self):
        item=deepcopy(ITEM); item['cupons']=['SALE2026']; self.db.sync([item])
        self.db.prioritize(item,self.now,'OUTRO10')
        self.db.success(item,self.now,extract_public_offer(html('5000'),item),{})
        msg=self.store.pending_notifications()[0]['payload']
        self.assertIn('OUTRO10 (não validado)',msg)
        self.assertIn('Preço com cupom validado: não informado',msg)
    def test_new_coupon_without_price_change_is_alerted_only_once(self):
        item=deepcopy(ITEM); self.db.sync([item])
        current=extract_public_offer(html('6000'),item)
        self.db.success(item,self.now,current,{})
        self.db.prioritize(item,self.now+180,'NOVO10')
        self.assertEqual(self.db.success(item,self.now+180,current,{}),'POSSÍVEL PROMOÇÃO COM CUPOM')
        self.assertIn('NOVO10 (não validado)',self.store.pending_notifications()[0]['payload'])
        self.assertIsNone(self.db.success(item,self.now+360,current,{}))
        self.assertEqual(len(self.store.pending_notifications()),1)
    def test_history_has_identity_and_no_cross_contamination(self):
        self.db.success(ITEM,self.now,extract_public_offer(html('6000'),ITEM),{})
        other=deepcopy(ITEM);other['sku']='OUTRO';self.db.sync([other])
        self.assertEqual(self.db.history(other),[])
        self.assertEqual(len(self.db.history(ITEM)),1)

class Clock:
    def __init__(self): self.t=1_793_900_000
    def now(self): return self.t
    async def sleep(self,seconds): self.t+=seconds

class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'db');self.addCleanup(self.store.close)
        self.clock=Clock();self.calls=[]
        self.catalog={'lojas':{'kabum':{'termos_verificados':True}},'produtos':[ITEM]}
    def make(self,fetch):
        return ShopMonitor(self.store,self.catalog,[ITEM],{},fetch=fetch,now=self.clock.now,sleep=self.clock.sleep,jitter=lambda:0)
    async def test_realistic_schedule_robots_then_price_and_restart(self):
        def fetch(url):
            self.calls.append((url,self.clock.now()))
            return Response(200,ROBOTS,'text/plain') if url.endswith('/robots.txt') else Response(200,html('5000'),'text/html')
        monitor=self.make(fetch);await monitor.check_due()
        self.assertEqual(len(self.calls),2)
        self.assertGreaterEqual(self.calls[1][1]-self.calls[0][1],10)
        self.assertEqual(len(self.store.pending_notifications()),1)
        restarted=self.make(fetch);await restarted.check_due()
        self.assertEqual(len(self.calls),2)
        self.clock.t+=180;await restarted.check_due()
        self.assertEqual(len(self.calls),3)
        self.assertEqual(len(self.store.pending_notifications()),1)
    async def test_no_robots_or_no_verified_terms_means_no_product_fetch(self):
        def fetch(url): self.calls.append(url);raise FetchError('bloqueado','403')
        monitor=self.make(fetch);await monitor.check_due()
        self.assertEqual(len(self.calls),1);self.assertTrue(self.calls[0].endswith('/robots.txt'))
        self.assertEqual(monitor.tracking.status([ITEM])[0]['ultimo_status'],'bloqueado')
        self.assertEqual(len(self.store.pending_notifications()),0)
        self.clock.t+=180;await monitor.check_due()
        self.assertEqual(len(self.calls),1)
    async def test_without_reviewed_terms_no_network_and_enabling_resumes(self):
        self.catalog['lojas']['kabum']['termos_verificados']=False
        def fetch(url):
            self.calls.append(url)
            return Response(200,ROBOTS,'text/plain') if url.endswith('/robots.txt') else Response(200,html('5000'),'text/html')
        monitor=self.make(fetch);await monitor.check_due()
        self.assertEqual(self.calls,[])
        self.assertEqual(monitor.tracking.status([ITEM])[0]['ultimo_status'],'termos_nao_verificados')
        self.catalog['lojas']['kabum']['termos_verificados']=True
        monitor=self.make(fetch);await monitor.check_due()
        self.assertEqual(len(self.calls),2)
    async def test_429_blocks_whole_store_and_prioritization_cannot_bypass(self):
        def fetch(url):
            self.calls.append(url)
            if url.endswith('/robots.txt'): return Response(200,ROBOTS,'text/plain')
            raise FetchError('limite_429','limite',1800)
        monitor=self.make(fetch);await monitor.check_due()
        self.assertEqual(monitor.tracking.status([ITEM])[0]['ultimo_status'],'limite_429')
        monitor.prioritize_message('Cupom SALE10 '+ITEM['url'])
        self.clock.t+=180;await monitor.check_due()
        self.assertEqual(len(self.calls),2)
        self.assertEqual(len(self.store.pending_notifications()),0)
    async def test_robots_denial_no_product_and_captcha_never_stock(self):
        def fetch(url):
            self.calls.append(url)
            return Response(200,'User-agent: *\nDisallow: /produto/', 'text/plain')
        monitor=self.make(fetch);await monitor.check_due()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(monitor.tracking.status([ITEM])[0]['ultimo_status'],'robots_proibido')
        self.assertIsNone(monitor.tracking.status([ITEM])[0]['ultima_observacao'])
    async def test_captcha_is_blocked_and_preserves_last_price(self):
        def fetch(url):
            if url.endswith('/robots.txt'): return Response(200,ROBOTS,'text/plain')
            return Response(200,'<html>CAPTCHA</html>','text/html')
        monitor=self.make(fetch)
        before=extract_public_offer(html('5000'),ITEM)
        monitor.tracking.success(ITEM,self.clock.now()-1000,before,{},notify=False)
        await monitor.check_due()
        status=monitor.tracking.status([ITEM])[0]
        self.assertEqual(status['ultimo_status'],'bloqueado')
        self.assertEqual(status['ultima_observacao']['preco_publico_centavos'],500000)
        self.assertEqual(len(self.store.pending_notifications()),0)
    async def test_telegram_hint_does_not_override_interval(self):
        def fetch(url):
            return Response(200,ROBOTS,'text/plain') if url.endswith('/robots.txt') else Response(200,html('5000'),'text/html')
        monitor=self.make(fetch);await monitor.check_due()
        row=monitor.tracking.target(ITEM); before=row['last_attempt']
        monitor.prioritize_message('Cupom SALE10 '+ITEM['url']+'?utm_source=telegram')
        row=monitor.tracking.target(ITEM)
        self.assertGreaterEqual(row['next_attempt'],before+180)
        self.assertEqual(row['coupon_hint'],'SALE10')
    async def test_robots_redirect_is_not_followed_and_pauses(self):
        def fetch(url):
            self.calls.append(url)
            raise FetchError('redirecionamento','redirecionado')
        monitor=self.make(fetch);await monitor.check_due()
        row=monitor.tracking.target(ITEM)
        self.assertEqual(row['last_status'],'robots_indisponivel')
        self.assertGreaterEqual(row['next_attempt'],self.clock.now()+86400)
        self.assertEqual(len(self.calls),1)
    async def test_already_bought_category_does_not_alert_but_keeps_history(self):
        def fetch(url):
            return Response(200,ROBOTS,'text/plain') if url.endswith('/robots.txt') else Response(200,html('5000'),'text/html')
        self.store.add_purchase({'categoria':'GPU','modelo':'RX 9070 XT','total_centavos':500000,'pagamento':'pix','parcelas':1,'parcelas_pagas':0})
        monitor=self.make(fetch);await monitor.check_due()
        self.assertEqual(self.store.pending_notifications(),[])
        self.assertEqual(monitor.tracking.status([ITEM])[0]['ultima_observacao']['preco_publico_centavos'],500000)
    async def test_reactivated_category_can_alert_again(self):
        def fetch(url):
            return Response(200,ROBOTS,'text/plain') if url.endswith('/robots.txt') else Response(200,html('5000'),'text/html')
        self.store.add_purchase({'categoria':'GPU','modelo':'RX 9070 XT','total_centavos':500000,
                                 'pagamento':'pix','parcelas':1,'parcelas_pagas':0,'monitorar_novamente':True})
        monitor=self.make(fetch);await monitor.check_due()
        self.assertEqual(len(self.store.pending_notifications()),1)
    async def test_ssd_store_alert_respects_global_cap_and_keeps_history(self):
        ssd = deepcopy(ITEM)
        ssd.update(id='test-ssd', nome='SSD NVMe 1TB', categoria='SSD', familia='SSD NVMe',
                   termos_identificacao=['SSD', 'NVMe', '1TB'], meta_centavos=100000)
        price = ['850']
        def fetch(url):
            if url.endswith('/robots.txt'):
                return Response(200, ROBOTS, 'text/plain')
            return Response(200, html(price[0], product_name=ssd['nome']), 'text/html')
        catalog = {'lojas': {'kabum': {'termos_verificados': True}}, 'produtos': [ssd]}
        monitor = ShopMonitor(self.store, catalog, [ssd], {'metas': {'SSD NVMe': 700}},
                              fetch=fetch, now=self.clock.now, sleep=self.clock.sleep, jitter=lambda:0)
        await monitor.check_due()
        self.assertEqual(self.store.pending_notifications(), [])
        self.assertEqual(len(monitor.tracking.history(ssd)), 1)
        price[0] = '650'
        self.clock.t += 180
        await monitor.check_due()
        self.assertEqual(len(self.store.pending_notifications()), 1)
        self.assertIn('Meta: R$ 700,00', self.store.pending_notifications()[0]['payload'])
    async def test_ram_kit_respects_cap_and_warns_when_card_plan_unknown(self):
        ram = deepcopy(ITEM)
        ram.update(id='test-ram-kit', nome='Corsair DDR5 32GB (2x16GB)', categoria='RAM', familia='DDR5',
                   termos_identificacao=['Corsair', 'DDR5', '2x16GB'], meta_centavos=300000)
        price = ['2450']
        def fetch(url):
            if url.endswith('/robots.txt'):
                return Response(200, ROBOTS, 'text/plain')
            return Response(200, html(price[0], product_name=ram['nome']), 'text/html')
        catalog = {'lojas': {'kabum': {'termos_verificados': True}}, 'produtos': [ram]}
        monitor = ShopMonitor(self.store, catalog, [ram], {}, fetch=fetch, now=self.clock.now,
                              sleep=self.clock.sleep, jitter=lambda:0)
        await monitor.check_due()
        self.assertEqual(self.store.pending_notifications(), [])
        self.assertEqual(len(monitor.tracking.history(ram)), 1)
        price[0] = '2333'
        self.clock.t += 180
        await monitor.check_due()
        self.assertEqual(len(self.store.pending_notifications()), 1)
        payload = self.store.pending_notifications()[0]['payload']
        self.assertIn('Meta: R$ 2.400,00', payload)
        self.assertIn('2×16 GB', payload)
        self.assertIn('Parcelamento sem juros ainda não confirmado', payload)
    async def test_shop_alert_does_not_wait_for_gemini(self):
        from runtime import deliver_pending
        class Bot:
            def __init__(self): self.sent=[]
            def verify_private(self): pass
            def send(self, text): self.sent.append(text)
        def fetch(url):
            return Response(200,ROBOTS,'text/plain') if url.endswith('/robots.txt') else Response(200,html('5000'),'text/html')
        # Use a realistic source timestamp; delivery expires old notifications.
        self.clock.t=datetime.now(timezone.utc).timestamp()
        monitor=self.make(fetch); await monitor.check_due()
        bot=Bot()
        with patch.dict('os.environ', {'GEMINI_API_KEY':'fake'}), patch('gemini.explain', side_effect=Exception('fake')) as gemini:
            await deliver_pending(self.store,bot,30,{'ativado':True,'limite_diario':20})
        gemini.assert_not_called()
        self.assertEqual(len(bot.sent),1)
        self.assertIn('PREÇO DENTRO DA META',bot.sent[0])
        self.assertEqual(len(self.store.notifications('sent')),1)

class EndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_supplied_gpu_urls_with_independent_simulated_stores(self):
        catalog, items = load_catalog(Path(__file__).resolve().parents[1] / 'produtos_lojas.json')
        self.assertEqual(len(items), 2)
        for key in ('kabum', 'pichau'):
            catalog['lojas'][key]['termos_verificados'] = True
        clock = Clock()
        def fetch(url):
            if url.endswith('/robots.txt'):
                return Response(200, ROBOTS, 'text/plain')
            item = next(p for p in items if p['url'] == url)
            product = {'@type':'Product', 'name':item['nome'], 'sku':item['sku'], 'url':item['url'],
                       'offers':{'@type':'Offer','price':'5039.99','priceCurrency':'BRL','availability':'https://schema.org/InStock'}}
            return Response(200,'<script type="application/ld+json">'+json.dumps(product)+'</script>','text/html')
        with tempfile.TemporaryDirectory() as directory, Store(Path(directory)/'db') as store:
            monitor=ShopMonitor(store,catalog,items,{},fetch=fetch,now=clock.now,sleep=clock.sleep,jitter=lambda:0)
            await monitor.check_due()
            self.assertEqual(len(store.pending_notifications()),2)
            self.assertTrue(all(s['ultimo_status']=='preco_publico' for s in monitor.tracking.status(items)))

    async def test_same_sent_telegram_price_not_duplicated_by_store(self):
        clock=Clock(); catalog={'lojas':{'kabum':{'termos_verificados':True}},'produtos':[ITEM]}
        text='ASRock RX 9070 XT Challenger\nR$ 5.000\n'+ITEM['url']
        with tempfile.TemporaryDirectory() as directory, Store(Path(directory)/'db') as store:
            monitor=ShopMonitor(store,catalog,[ITEM],{},now=clock.now,sleep=clock.sleep)
            ident=store.enqueue_notification('telegram:one','texto',datetime.fromtimestamp(clock.now(),timezone.utc))
            store.claim_notification(ident);store.finish_notification(ident,'sent')
            monitor.remember_telegram_alert(text,[{'key':'telegram:one'}])
            event=monitor.tracking.success(ITEM,clock.now()+10,extract_public_offer(html('5000'),ITEM),{})
            self.assertIsNone(event)
            self.assertEqual(store.pending_notifications(),[])
            self.assertEqual(len(monitor.tracking.history(ITEM)),1)
            event=monitor.tracking.success(ITEM,clock.now()+190,extract_public_offer(html('4900'),ITEM),{})
            self.assertIsNotNone(event)
            self.assertEqual(len(store.pending_notifications()),1)

    async def test_pending_telegram_alert_does_not_hide_store_alert(self):
        clock=Clock();catalog={'lojas':{'kabum':{'termos_verificados':True}},'produtos':[ITEM]}
        with tempfile.TemporaryDirectory() as directory, Store(Path(directory)/'db') as store:
            monitor=ShopMonitor(store,catalog,[ITEM],{},now=clock.now,sleep=clock.sleep)
            store.enqueue_notification('telegram:pending','texto',datetime.fromtimestamp(clock.now(),timezone.utc))
            monitor.remember_telegram_alert('ASRock RX 9070 XT Challenger\nR$ 5.000\n'+ITEM['url'],[{'key':'telegram:pending'}])
            self.assertEqual(monitor.tracking.success(ITEM,clock.now()+10,extract_public_offer(html('5000'),ITEM),{}),'PREÇO DENTRO DA META')
            self.assertEqual(len(store.pending_notifications()),2)

    async def test_new_telegram_message_prioritizes_matching_store_url(self):
        from types import SimpleNamespace
        from runtime import collect_once
        clock=Clock(); cfg={'canais':['canal'], 'metas':{}, 'orcamento':{'pix':10000,'cartao':2500}}
        catalog={'lojas':{'kabum':{'termos_verificados':True}},'produtos':[ITEM]}
        class Client:
            async def get_entity(self, channel): return channel
            async def iter_messages(self, entity, limit, min_id=0, reverse=False):
                if min_id < 1:
                    yield SimpleNamespace(id=1,date=datetime.fromtimestamp(clock.now(),timezone.utc),
                        raw_text='Cupom: TESTE10\n'+ITEM['url']+'?utm_source=telegram',entities=[])
        with tempfile.TemporaryDirectory() as directory, Store(Path(directory)/'db') as store:
            monitor=ShopMonitor(store,catalog,[ITEM],cfg,now=clock.now,sleep=clock.sleep)
            monitor.tracking.started(ITEM,clock.now()-170)
            store.set_cursor('canal',0)
            await collect_once(Client(),store,cfg,on_message=monitor.prioritize_message)
            row=monitor.tracking.target(ITEM)
            self.assertEqual(row['coupon_hint'],'TESTE10')
            self.assertGreaterEqual(row['next_attempt'],row['last_attempt']+180)

    async def test_same_store_has_no_concurrent_requests(self):
        import threading
        import time
        clock=Clock(); active=0; peak=0; lock=threading.Lock()
        other=deepcopy(ITEM);other['id']='other';other['url']='https://www.kabum.com.br/produto/456/gpu'
        catalog={'lojas':{'kabum':{'termos_verificados':True}},'produtos':[ITEM,other]}
        def fetch(url):
            nonlocal active,peak
            with lock:
                active+=1;peak=max(peak,active)
            time.sleep(0.005)
            with lock: active-=1
            return Response(200,ROBOTS,'text/plain') if url.endswith('/robots.txt') else Response(200,html('6000'),'text/html')
        with tempfile.TemporaryDirectory() as directory, Store(Path(directory)/'db') as store:
            monitor=ShopMonitor(store,catalog,[ITEM,other],{},fetch=fetch,now=clock.now,sleep=clock.sleep,jitter=lambda:0)
            await monitor.check_due()
            self.assertEqual(peak,1)

class InitialConnectivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_store_task_starts_while_telegram_reconnects(self):
        from types import SimpleNamespace
        from monitor import async_main
        event = asyncio.Event()
        class Client:
            async def connect(self): raise OSError('offline')
            async def disconnect(self): pass
        class Shops:
            async def run(self, **kwargs):
                event.set()
                await asyncio.Event().wait()
        args=SimpleNamespace(no_notify=True,login=False,watch=True)
        with tempfile.TemporaryDirectory() as d, Store(Path(d)/'db') as store:
            with patch.dict('os.environ', {'TELEGRAM_API_ID':'123','TELEGRAM_API_HASH':'fake'}), \
                 patch('monitor.TelegramClient',return_value=Client()), \
                 patch('monitor.shop_monitor',return_value=Shops()), \
                 patch('monitor.migrate_legacy'):
                task=asyncio.create_task(async_main(args,{},store))
                await asyncio.wait_for(event.wait(),timeout=1)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError): await task
