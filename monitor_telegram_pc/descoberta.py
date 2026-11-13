"""Fila persistente de descoberta integrada ao monitor e SQLite existentes."""
import asyncio
import hashlib
import ipaddress
import json
import logging
import re
from urllib.parse import urlsplit, unquote

from descoberta_fontes import QUERIES, search, xml_links
from lojas_rede import DOMAINS, FetchError, product_url, robots_allows, requires_terms_review
from lojas_extracao import ExtractionError, extract_public_offer, _Scripts, _nodes, _type

LOG = logging.getLogger('pcam5.descoberta')
from produtos_validacao import RX, valid_gpu


def public_host(url):
    p = urlsplit(url)
    host = (p.hostname or '').lower()
    if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443):
        raise ValueError('URL pública HTTPS necessária')
    if not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+', host) or host.endswith(('.local', '.internal', '.localhost')):
        raise ValueError('Domínio público necessário')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    raise ValueError('Endereço IP não permitido')


def register_domains(catalog):
    for store, policy in catalog.get('lojas', {}).items():
        domains = policy.get('dominios', [])
        for domain in domains:
            if public_host('https://' + domain) != domain:
                raise ValueError('Domínio inválido')
        if domains and policy.get('confiavel') is True:
            DOMAINS[store] = set(domains)


class Discovery:
    def __init__(self, monitor, api_search=search):
        self.monitor = monitor
        self.store = monitor.tracking.store
        self.db = self.store.db
        self.cfg = monitor.config.get('descoberta', {})
        self.api_search = api_search
        register_domains(monitor.catalog)
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS discovery_urls (
            url TEXT PRIMARY KEY, loja TEXT, estado TEXT NOT NULL, origem TEXT,
            criado REAL, visto REAL, proxima REAL DEFAULT 0, tentativas INTEGER DEFAULT 0,
            detalhe TEXT, item TEXT);
          CREATE TABLE IF NOT EXISTS discovery_sources (
            id TEXT PRIMARY KEY, proxima REAL DEFAULT 0, tentativas INTEGER DEFAULT 0,
            estado TEXT, ultima REAL, detalhe TEXT, cursor INTEGER DEFAULT 0);
        ''')
        if not monitor.catalog.get('exigir_termos_verificados', False):
            with self.db:
                self.db.execute("UPDATE discovery_sources SET proxima=0 WHERE estado='termos_nao_verificados'")
                self.db.execute("UPDATE discovery_urls SET proxima=0 WHERE estado='termos_nao_verificados'")
        self.reload()

    def reload(self):
        urls = {i['url'] for i in self.monitor.items}
        for row in self.db.execute("SELECT item FROM discovery_urls WHERE estado='monitorando' AND item IS NOT NULL"):
            item = json.loads(row['item'])
            if (item['url'] not in urls and item['loja'] in DOMAINS
                    and item['url'] not in self.cfg.get('urls_desativadas', [])):
                self.monitor.items.append(item)
                self.monitor.tracking.sync([item])
                urls.add(item['url'])

    def ingest(self, url, origin):
        try:
            host = public_host(url)
            store = next((key for key, domains in DOMAINS.items() if host in domains), None)
            if not store:
                # Save only safe, stripped URLs; never visit or trust a new domain automatically.
                url = urlsplit(url)._replace(query='', fragment='').geturl()
                state = 'loja_pendente'
            else:
                url = product_url(url, store)
                state = 'candidata'
            equivalent = lambda value: value.replace('https://www.', 'https://', 1)
            manual = next((i for i in self.monitor.items if equivalent(i['url']) == equivalent(url) and not i.get('descoberto')), None)
            if manual:
                return
            if url in self.cfg.get('urls_desativadas', []):
                return
            # Keep origins even when the product is already monitored.
            if url in {item['url'] for item in self.monitor.items} and not self.db.execute('SELECT 1 FROM discovery_urls WHERE url=?', (url,)).fetchone():
                return
            now = self.monitor.now()
            alternate = url.replace('https://www.', 'https://', 1) if host.startswith('www.') else url.replace('https://', 'https://www.', 1)
            alias = self.db.execute('SELECT url FROM discovery_urls WHERE url=?', (alternate,)).fetchone()
            if alias:
                url = alias['url']
            old = self.db.execute('SELECT origem FROM discovery_urls WHERE url=?', (url,)).fetchone()
            origins = set(json.loads(old['origem'])) if old else set()
            origins.add(str(origin)[:100])
            if not old and self.db.execute('SELECT COUNT(*) FROM discovery_urls').fetchone()[0] >= 5000:
                return
            with self.db:
                self.db.execute('''INSERT INTO discovery_urls(url,loja,estado,origem,criado,visto)
                    VALUES (?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET visto=excluded.visto,origem=excluded.origem''',
                    (url, store, state, json.dumps(sorted(origins)), now, now))
        except (ValueError, TypeError):
            return

    def telegram(self, text):
        if not self.cfg.get('ativado', False) or not valid_gpu(text.split('\n')[0]):
            return
        for url in re.findall(r'https://[^\s<>]+', text):
            self.ingest(url.rstrip('.,;)]}>\"\''), 'telegram')

    async def public_read(self, url, store):
        m = self.monitor
        if requires_terms_review(m.catalog, store):
            raise FetchError('termos_nao_verificados', 'Política de acesso ainda não verificada')
        policy = m.catalog.get('lojas', {}).get(store, {})
        if policy.get('dominios') and policy.get('confiavel') is not True:
            raise FetchError('loja_pendente', 'Domínio personalizado sem confiança verificada')
        url = product_url(url, store)
        origin = 'https://' + urlsplit(url).netloc
        item = {'loja': store}
        async with m.locks.setdefault(store, asyncio.Lock()):
            robot = m.tracking.host('robots:' + origin)
            if not robot['robots_text'] or robot['robots_until'] <= m.now():
                response = await m._request(item, origin + '/robots.txt', 10)
                if response.status != 200 or response.content_type not in {'text/plain', 'application/octet-stream'}:
                    raise FetchError('robots_indisponivel', 'robots.txt indisponível')
                robots_allows(response.body, url)
                m.tracking.robots('robots:' + origin, response.body, m.now()+86400)
                robot = m.tracking.host('robots:' + origin)
            delay = robots_allows(robot['robots_text'], url)
            response = await m._request(item, url, delay)
            if response.status != 200:
                raise FetchError('http_erro', 'Resposta HTTP inválida')
            return response.body

    def sources(self):
        sources = list(self.cfg.get('fontes', []))
        for store, domains in DOMAINS.items():
            policy = self.monitor.catalog.get('lojas', {}).get(store, {})
            if policy.get('descobrir', True):
                urls = policy.get('sitemaps', [])
                if not urls:
                    domain = next((d for d in sorted(domains) if d.startswith('www.')), sorted(domains)[0])
                    sources.append({'id': 'robots-'+store, 'tipo': 'robots_sitemaps',
                                    'url': 'https://'+domain+'/robots.txt', 'loja': store, 'ativado': True,
                                    'intervalo_segundos': 7200})
                for index, url in enumerate(urls):
                    sources.append({'id': f'sitemap-{store}-{index}', 'tipo': 'sitemap', 'url': url, 'loja': store, 'ativado': True})
        return sources

    async def scan(self, source):
        ident = source['id']
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO discovery_sources(id) VALUES (?)', (ident,))
        row = self.db.execute('SELECT * FROM discovery_sources WHERE id=?', (ident,)).fetchone()
        if row['proxima'] > self.monitor.now() or source.get('ativado') is not True:
            return
        interval = max(3600, int(source.get('intervalo_segundos', 7200)))
        # Persist before requesting, including across crashes/restarts.
        with self.db:
            self.db.execute('UPDATE discovery_sources SET proxima=? WHERE id=?', (self.monitor.now()+interval, ident))
        try:
            if source['tipo'] in {'brave', 'google_cse'}:
                queries = self.cfg.get('consultas') or QUERIES
                links = await asyncio.to_thread(self.api_search, source, queries[row['cursor'] % len(queries)])
            elif source['tipo'] in {'sitemap', 'feed', 'robots_sitemaps'}:
                body = await self.public_read(source['url'], source['loja'])
                if source['tipo'] == 'robots_sitemaps':
                    maps = re.findall(r'(?im)^sitemap:\s*(https://\S+)', body)
                    if not maps:
                        raise FetchError('sitemap_ausente', 'Nenhum sitemap declarado em robots.txt')
                    body = await self.public_read(maps[row['cursor'] % len(maps)], source['loja'])
                kind, links = xml_links(body)
                if kind == 'sitemapindex':
                    # Rotate a bounded batch so large indexes eventually get covered.
                    children = links
                    links = []
                    for offset in range(min(3, len(children))):
                        child = children[(row['cursor'] + offset) % len(children)]
                        _, found = xml_links(await self.public_read(child, source['loja']))
                        links.extend(found)
                if source['tipo'] in {'sitemap', 'robots_sitemaps'}:
                    links = [link for link in links if RX.search(unquote(link).replace('/', ' '))]
            else:
                raise ValueError('Tipo de fonte desconhecido')
            for url in links[:500]:
                self.ingest(url, ident)
            with self.db:
                self.db.execute("UPDATE discovery_sources SET estado='ok',ultima=?,tentativas=0,detalhe=?,cursor=cursor+1 WHERE id=?",
                                (self.monitor.now(), f'{len(links)} URLs candidatas; não são ofertas confirmadas', ident))
        except Exception as exc:
            status = exc.status if isinstance(exc, FetchError) else 'fonte_falhou'
            delay = max(interval, min(86400, 3600 * 2**min(row['tentativas'], 5)), getattr(exc, 'retry_seconds', 0) or 0)
            if '403' in status or status == 'bloqueado': delay = max(delay, 86400)
            with self.db:
                self.db.execute('UPDATE discovery_sources SET estado=?,ultima=?,proxima=?,tentativas=tentativas+1,detalhe=? WHERE id=?',
                    (status, self.monitor.now(), self.monitor.now()+delay, type(exc).__name__, ident))
            if source.get('loja') and status in {'bloqueado', 'limite_429'}:
                self.monitor.tracking.pause_host('loja:'+source['loja'], self.monitor.now()+delay, status)
            LOG.warning('Fonte %s: %s', ident, status)

    async def validate(self, row):
        url, store = row['url'], row['loja']
        try:
            host = public_host(url)
            store = next((key for key, domains in DOMAINS.items() if host in domains), None)
            if store is None:
                raise FetchError('loja_pendente', 'Domínio necessita avaliação de confiança e política')
            html = await self.public_read(url, store)
            parser = _Scripts(); parser.feed(html)
            matches = []
            for script in parser.scripts:
                try:
                    nodes = list(_nodes(json.loads(script)))
                except (ValueError, RecursionError):
                    continue
                for product in nodes:
                    if not _type(product, 'Product') or not valid_gpu(product.get('name')):
                        continue
                    name = product['name']
                    if re.search(r'(?i)\b(?:usado|usada|recondicionado|refurbished|open box)\b', str(product.get('description', ''))):
                        continue
                    offers = product.get('offers', [])
                    if isinstance(offers, dict): offers = [offers]
                    conditions = [product.get('itemCondition')] + [o.get('itemCondition') for o in offers if isinstance(o, dict)]
                    if any(c and not str(c).endswith('/NewCondition') for c in conditions):
                        continue
                    item = {'id': 'auto-'+hashlib.sha256(url.encode()).hexdigest()[:20], 'nome': name[:160],
                            'url': url, 'loja': store, 'categoria': 'GPU', 'familia': 'RX 9070 XT',
                            'termos_identificacao': ['9070', 'XT'], 'meta_centavos': 530000,
                            'intervalo_segundos': max(900, int(self.cfg.get('intervalo_produto_segundos', 1200))),
                            'descoberto': True, 'condicao': 'novo' if any(conditions) else 'não informada'}
                    sku = product.get('sku') or product.get('mpn')
                    if isinstance(sku, str) and re.fullmatch(r'[A-Za-z0-9._-]{1,80}', sku):
                        item['sku'] = sku
                    observation = extract_public_offer(html, item)
                    # Marketplace needs an identified seller. Unknown condition remains provisional.
                    if store in {'amazon', 'mercadolivre', 'magalu', 'casasbahia'} and not observation.get('vendedor'):
                        continue
                    matches.append(item)
            if len(matches) != 1:
                raise ExtractionError('Produto único RX 9070 XT não validado')
            item = matches[0]
            self.monitor.tracking.sync([item])
            self.monitor.tracking.started(item, self.monitor.now())
            owned = any(p.get('categoria') == 'GPU' and not p.get('monitorar_novamente') for p in self.store.purchases())
            owned = owned or self.monitor.config.get('montagem', {}).get('GPU', {}).get('possui', False)
            self.monitor.tracking.success(item, self.monitor.now(), observation, self.monitor.config,
                notify=self.monitor.notifications_enabled and not owned, reduction=self.monitor.catalog.get('queda_minima_percentual', 5))
            with self.db:
                self.db.execute("UPDATE discovery_urls SET estado='monitorando',loja=?,item=?,detalhe=NULL WHERE url=?", (store, json.dumps(item), url))
            self.reload()
        except Exception as exc:
            status = exc.status if isinstance(exc, FetchError) else 'validacao_pendente'
            delay = max(86400, getattr(exc, 'retry_seconds', 0) or 0)
            with self.db:
                self.db.execute('UPDATE discovery_urls SET estado=?,detalhe=?,proxima=?,tentativas=tentativas+1 WHERE url=?',
                                (status, type(exc).__name__, self.monitor.now()+delay, url))
            if store and status in {'bloqueado', 'limite_429'}:
                self.monitor.tracking.pause_host('loja:'+store, self.monitor.now()+delay, status)

    async def tick(self):
        if self.cfg.get('ativado', False) is False:
            return
        for source in self.sources():
            try:
                await self.scan(source)
            except Exception as exc:
                LOG.warning('Configuração de fonte inválida: %s', type(exc).__name__)
        rows = self.db.execute("SELECT * FROM discovery_urls WHERE estado!='monitorando' AND proxima<=? ORDER BY proxima,criado LIMIT 10", (self.monitor.now(),)).fetchall()
        for row in rows:
            with self.db:
                self.db.execute('UPDATE discovery_urls SET proxima=? WHERE url=?', (self.monitor.now()+86400, row['url']))
            await self.validate(row)

    def report(self):
        return {'fontes': [dict(r) for r in self.db.execute('SELECT * FROM discovery_sources')],
                'anuncios': [dict(r) for r in self.db.execute('SELECT url,loja,estado,origem,detalhe,proxima FROM discovery_urls')],
                'configuracao_fontes': self.sources()}
