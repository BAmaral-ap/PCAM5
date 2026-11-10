"""Agendador para observações públicas autorizadas de produtos específicos."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from lojas_dados import StoreTracking
from lojas_extracao import ExtractionError, extract_public_offer
from lojas_rede import FetchError, fetch_public, product_url, robots_allows, validate_catalog
from parsing import extract_coupon, parse_offers

LOG = logging.getLogger('pcam5.lojas')
URL_RE = re.compile(r'https?://[^\s<>]+', re.I)


def load_catalog(path):
    path = Path(path)
    if not path.exists():
        raise ValueError('Arquivo de produtos das lojas não encontrado.')
    try:
        catalog = json.loads(path.read_text(encoding='utf-8-sig'))
    except (ValueError, OSError):
        raise ValueError('Arquivo de produtos das lojas inválido.') from None
    items = [item for item in validate_catalog(catalog) if item.get('ativo', True)]
    policies = catalog.get('lojas', {})
    if not isinstance(policies, dict):
        raise ValueError('Políticas das lojas devem ser um objeto.')
    return catalog, items


class ShopMonitor:
    def __init__(self, store, catalog, items, config, *, fetch=fetch_public, now=time.time,
                 sleep=asyncio.sleep, jitter=None, notifications_enabled=True):
        self.tracking = StoreTracking(store)
        self.catalog = catalog
        self.items = items
        self.config = config
        self.fetch = fetch
        self.now = now
        self.sleep = sleep
        self.jitter = jitter or (lambda: random.uniform(0, 3))
        self.notifications_enabled = notifications_enabled
        self.locks = {}
        self.event = asyncio.Event()
        self.tracking.sync(items)
        # A reviewed policy is a meaningful state change: do not leave the
        # product waiting an hour from the earlier "unverified terms" result.
        for item in items:
            policy = catalog.get('lojas', {}).get(item['loja'], {})
            row = self.tracking.target(item)
            if isinstance(policy, dict) and policy.get('termos_verificados') is True and row and row['last_status'] == 'termos_nao_verificados':
                with self.tracking.db:
                    self.tracking.db.execute('UPDATE shop_targets SET next_attempt=0 WHERE key=?', (row['key'],))

    def prioritize_message(self, text):
        """Only exact configured direct links; never resolve untrusted short URLs."""
        urls = URL_RE.findall(text or '')
        coupon = extract_coupon(text or '')
        current = self.now()
        for item in self.items:
            for candidate in urls:
                try:
                    normalized = product_url(candidate.rstrip('.,;)]}>\"\''), item['loja'])
                except ValueError:
                    continue
                if normalized == item['url']:
                    self.tracking.prioritize(item, current, coupon)
                    self.event.set()
                    break

    def remember_telegram_alert(self, text, notifications):
        """Correlate only unambiguous, exact direct product URLs and prices."""
        if len(notifications) != 1:
            return
        from datetime import datetime, timezone
        from rx9070 import price_candidates
        offers = parse_offers('telegram', 1, datetime.now(timezone.utc), text or '', self.config)
        if len(offers) != 1:
            return
        offer = offers[0]
        title = re.sub(r'[^a-z0-9]', '', (offer.get('nome_completo') or '').casefold())
        amounts = {offer[k] for k in ('pix_centavos', 'cartao_centavos', 'anunciado_centavos') if type(offer.get(k)) is int}
        if offer.get('peca') == 'RX 9070 XT 16GB':
            amounts = set(price_candidates(text))
        if len(amounts) != 1:
            return
        for item in self.items:
            if offer['categoria'] != item['categoria'] or not all(re.sub(r'[^a-z0-9]', '', t.casefold()) in title for t in item['termos_identificacao']):
                continue
            for candidate in URL_RE.findall(text or ''):
                try:
                    url = product_url(candidate.rstrip('.,;)]}>\"\''), item['loja'])
                except ValueError:
                    continue
                if url == item['url']:
                    self.tracking.remember_telegram_alert(item, self.now(), next(iter(amounts)), notifications[0]['key'])
                    break

    async def _request(self, item, url, minimum_delay):
        host_key = 'loja:' + item['loja']
        row = self.tracking.host(host_key)
        now = self.now()
        if row['paused_until'] > now:
            raise FetchError('pausa_loja', 'Loja em período de backoff', int(row['paused_until'] - now))
        earliest = (row['last_request'] or 0) + max(10, minimum_delay)
        delay = max(0, earliest-now) + max(0, min(3, self.jitter()))
        if delay:
            await self.sleep(delay)
        self.tracking.host_requested(host_key, self.now())
        return await asyncio.to_thread(self.fetch, url)

    async def check(self, item):
        key = item['loja']
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            row = self.tracking.target(item)
            if not row or row['next_attempt'] > self.now():
                return False
            now = self.now()
            self.tracking.started(item, now)
            policies = self.catalog.get('lojas', {})
            policy = policies.get(key, {}) if isinstance(policies, dict) else {}
            if not isinstance(policy, dict) or policy.get('termos_verificados') is not True:
                self.tracking.failure(item, now, 'termos_nao_verificados',
                    'Confirme que a política da loja permite esta consulta pública', now + 3600)
                LOG.warning('%s: consulta não iniciada; termos ainda não verificados.', key)
                return False
            parsed = urlsplit(item['url'])
            origin = f'{parsed.scheme}://{parsed.netloc}'
            host_key = 'loja:' + key
            try:
                state = self.tracking.host(host_key)
                if state['paused_until'] > now:
                    raise FetchError('pausa_loja', 'Loja em período de backoff', int(state['paused_until']-now))
                robot_key = 'robots:' + origin
                robot = self.tracking.host(robot_key)
                if not robot['robots_text'] or robot['robots_until'] <= now:
                    try:
                        response = await self._request(item, origin + '/robots.txt', 5)
                    except FetchError as exc:
                        if exc.status in {'http_erro', 'redirecionamento'}:
                            raise FetchError('robots_indisponivel', 'robots.txt indisponível ou redirecionado', exc.retry_seconds) from None
                        raise
                    if response.status != 200:
                        raise FetchError('robots_indisponivel', 'robots.txt indisponível')
                    if re.search(r'captcha|cf-chl-|<!doctype\s+html|<html\b', response.body, re.I):
                        raise FetchError('bloqueado', 'robots.txt retornou uma página de desafio ou bloqueio')
                    if response.content_type not in {'text/plain', 'application/octet-stream'}:
                        raise FetchError('robots_indisponivel', 'robots.txt não retornou texto simples')
                    # Verify before caching; blocked policies are cached too to avoid frequent checks.
                    self.tracking.robots(robot_key, response.body, self.now()+86400)
                    robot = self.tracking.host(robot_key)
                delay = robots_allows(robot['robots_text'], item['url'])
                response = await self._request(item, item['url'], delay)
                if response.status != 200:
                    raise FetchError('http_erro', f'HTTP {response.status}')
                if response.content_type not in {'text/html', 'application/xhtml+xml'}:
                    raise FetchError('resposta_invalida', 'Página não retornou HTML público')
                try:
                    observation = extract_public_offer(response.body, item)
                except ExtractionError as exc:
                    if 'bloqueio' in str(exc):
                        raise FetchError('bloqueado', 'Página de bloqueio ou desafio anti-bot') from None
                    raise FetchError('extracao_falhou', str(exc)) from None
                threshold = self.catalog.get('queda_minima_percentual', 5)
                if type(threshold) is not int or not 1 <= threshold <= 100:
                    raise ValueError('queda_minima_percentual deve estar entre 1 e 100')
                bought = {p.get('categoria') for p in self.tracking.store.purchases() if not p.get('monitorar_novamente', False)}
                owned = self.config.get('montagem', {}).get(item['categoria'], {}).get('possui', False)
                can_notify = self.notifications_enabled and item['categoria'] not in bought and not owned
                target_item = item
                if item['categoria'] == 'RAM':
                    title = item['nome']
                    kit = re.search(r'\b(\d+)\s*[x×]\s*(\d+)\s*gb\b', title, re.I)
                    shape = (int(kit[1]), int(kit[2])) if kit else None
                    if (re.search(r'\b(?:so[ -]?dimm|notebook)\b', title, re.I)
                            or (shape and shape not in {(1, 16), (2, 16)})
                            or (shape is None and re.search(r'\b32\s*gb\b', title, re.I))):
                        can_notify = False
                    if shape == (2, 16):
                        from finance import cents
                        cap = cents(self.config.get('metas', {}).get('DDR5 2x16GB', 2400))
                        target_item = dict(item, meta_centavos=min(item['meta_centavos'], cap))
                        values = [observation.get(field) for field in ('preco_publico_centavos', 'preco_sem_cupom_centavos',
                                                                        'preco_pix_centavos', 'preco_cartao_centavos')]
                        if not any(type(value) is int and 0 < value <= cap for value in values):
                            can_notify = False
                if item['categoria'] == 'SSD' and self.config.get('metas', {}).get('SSD NVMe') is not None:
                    from finance import cents
                    cap = cents(self.config['metas']['SSD NVMe'])
                    target_item = dict(item, meta_centavos=min(item['meta_centavos'], cap))
                    values = [observation.get(field) for field in ('preco_publico_centavos', 'preco_sem_cupom_centavos',
                                                                    'preco_pix_centavos', 'preco_cartao_centavos')]
                    shipping = observation.get('frete_centavos') or 0
                    if not any(type(value) is int and value + shipping <= cap for value in values):
                        can_notify = False
                event = self.tracking.success(target_item, self.now(), observation, self.config,
                                              notify=can_notify, reduction=threshold)
                LOG.info('%s: consulta %s%s.', item['id'], observation['status'], '; alerta enfileirado' if event and can_notify else '')
                return True
            except FetchError as exc:
                current = self.now()
                failures = row['failures'] + 1
                wait = max(item['intervalo_segundos'], min(86400, 300 * (2 ** min(failures-1, 8))))
                if exc.retry_seconds is not None:
                    wait = max(wait, exc.retry_seconds)
                if exc.status == 'bloqueado':
                    wait = max(wait, 86400)
                if exc.status.startswith('robots_') or exc.status == 'redirecionamento':
                    wait = max(wait, 86400)
                if exc.status in {'bloqueado', 'limite_429', 'pausa_loja'}:
                    self.tracking.pause_host(host_key, current + wait, exc.status)
                self.tracking.failure(item, current, exc.status, exc.detail, current + wait)
                LOG.warning('%s: %s; próxima tentativa após o período de espera.', item['id'], exc.detail)
                return False
            except Exception as exc:
                self.tracking.failure(item, self.now(), 'erro_interno', type(exc).__name__, self.now()+900)
                LOG.error('%s: falha %s; última observação preservada.', item['id'], type(exc).__name__)
                return False

    async def check_due(self):
        # Each store gets a single lock; independent stores may run concurrently.
        await asyncio.gather(*(self.check(item) for item in self.items))

    async def run(self, watch=False, flush=None):
        while True:
            try:
                await self.check_due()
                if flush:
                    await flush()
            except Exception as exc:
                LOG.error('Monitor de lojas falhou (%s); nova tentativa em 60 segundos.', type(exc).__name__)
                if not watch:
                    raise
                await self.sleep(60)
                continue
            if not watch:
                return
            due = [self.tracking.target(item)['next_attempt'] for item in self.items]
            seconds = max(1, min(60, min(due, default=self.now()+60)-self.now()))
            try:
                await asyncio.wait_for(self.event.wait(), timeout=seconds)
                self.event.clear()
            except asyncio.TimeoutError:
                pass
