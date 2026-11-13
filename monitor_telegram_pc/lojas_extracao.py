"""Extração conservadora de ofertas públicas em JSON-LD Schema.org.

Não interpreta preços riscados, parcelas isoladas, cupons ou dados de páginas
arbitrárias como preço confirmado. Ausência de dado permanece desconhecida.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import urlsplit
from lojas_rede import compatible_target


class ExtractionError(ValueError):
    pass


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.collecting = False
        self.buffer = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'script':
            attributes = dict(attrs)
            self.collecting = attributes.get('type', '').split(';', 1)[0].strip().lower() == 'application/ld+json'
            self.buffer = []

    def handle_data(self, data):
        if self.collecting:
            self.buffer.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == 'script' and self.collecting:
            self.scripts.append(''.join(self.buffer))
            self.collecting = False


def _nodes(value):
    if isinstance(value, list):
        for item in value:
            yield from _nodes(item)
    elif isinstance(value, dict):
        yield value
        for key in ('@graph', 'mainEntity', 'itemListElement'):
            if key in value:
                yield from _nodes(value[key])


def _type(node, wanted):
    value = node.get('@type', [])
    return wanted in ([value] if isinstance(value, str) else value if isinstance(value, list) else [])


def _amount(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        raw = str(value).strip()
        if not re.fullmatch(r'\d+(?:\.\d{1,2})?', raw):
            return None
        amount = Decimal(raw)
        if not amount.is_finite() or amount <= 0 or amount > 100000:
            return None
        return int(amount * 100)
    except (InvalidOperation, ValueError):
        return None


def _norm(value):
    return re.sub(r'[^a-z0-9]', '', str(value).lower())


def _same_product(node, target):
    name = node.get('name')
    if not isinstance(name, str):
        return False
    if not compatible_target(target['categoria'], name):
        return False
    if target.get('familia') == 'RX 9070 XT':
        from produtos_validacao import valid_gpu
        if not valid_gpu(name):
            return False
    compact = _norm(name)
    if not all(_norm(term) in compact for term in target['termos_identificacao']):
        return False
    if target.get('familia') == 'RX 9070 XT' and not re.search(r'(?i)\b(?:radeon\s+)?(?:rx\s*[- ]*)?9070\W*xt\b', name):
        return False
    # A known wrong SKU must never be substituted for the configured product.
    ids = {_norm(node[k]) for k in ('sku', 'mpn', 'productID') if isinstance(node.get(k), (str, int)) and str(node[k]).strip()}
    wanted = {_norm(v) for v in (target.get('sku'), target.get('sku_loja')) if v}
    if ids and wanted and ids.isdisjoint(wanted):
        return False
    page = node.get('url')
    if isinstance(page, str) and page.startswith('https://'):
        a, b = urlsplit(page), urlsplit(target['url'])
        if a.hostname != b.hostname or a.path.rstrip('/') != b.path.rstrip('/'):
            return False
    return True


def _seller(raw):
    if isinstance(raw, dict):
        raw = raw.get('name')
    if not isinstance(raw, str):
        return None
    clean = re.sub(r'[\x00-\x1f\x7f]+', ' ', raw).strip()
    # The label is untrusted data; don't relay URLs or instructions as a seller.
    if not clean or len(clean) > 100 or re.search(r'https?://', clean, re.I):
        return None
    return clean


def _stock(value):
    if not isinstance(value, str):
        return 'desconhecido'
    name = value.rstrip('/').rsplit('/', 1)[-1].rsplit('#', 1)[-1].lower()
    if name == 'instock':
        return 'em_estoque'
    if name in {'outofstock', 'soldout', 'discontinued'}:
        return 'esgotado'
    return 'desconhecido'


def _prices(offer):
    prices = {'publico': set(), 'pix': set(), 'cartao': set(), 'sem_cupom': set()}
    currency = offer.get('priceCurrency')
    offer_label = ' '.join(str(offer.get(k, '')) for k in ('name', 'description', 'priceType')).lower()
    installment = re.search(r'parcela|mensalidade|por\s+m[eê]s|\b\d+\s*[x×]\b', offer_label)
    price = _amount(offer.get('price')) if currency == 'BRL' and not installment else None
    if price:
        prices['publico'].add(price)
    specs = offer.get('priceSpecification', [])
    if isinstance(specs, dict):
        specs = [specs]
    for spec in specs if isinstance(specs, list) else []:
        if not isinstance(spec, dict):
            continue
        label = ' '.join(str(spec.get(k, '')) for k in ('name', 'description', 'priceType')).lower()
        if re.search(r'listprice|list price|pre[cç]o anterior|hist[oó]ric|parcela|mensalidade|por\s+m[eê]s|\b\d+\s*[x×]\b|frete|original', label):
            continue
        value = _amount(spec.get('price')) if spec.get('priceCurrency', currency) == 'BRL' else None
        if not value:
            continue
        if re.search(r'\bsem\s+cupom\b', label) and not re.search(r'\bpix\b|cart[aã]o', label):
            prices['sem_cupom'].add(value)
        elif re.search(r'coupon|cupom', label):
            # A price labelled with a coupon does not validate that coupon.
            continue
        elif re.search(r'\bpix\b', label) and not re.search(r'cart[aã]o', label):
            prices['pix'].add(value)
        elif re.search(r'cart[aã]o|credit card', label) and not re.search(r'\bpix\b', label):
            prices['cartao'].add(value)
    return prices


def _shipping(offer):
    details = offer.get('shippingDetails', [])
    if isinstance(details, dict):
        details = [details]
    prices = set()
    for detail in details if isinstance(details, list) else []:
        if not isinstance(detail, dict) or any(k in detail for k in ('eligibleTransactionVolume', 'validForMemberTier', 'conditions')):
            continue
        destination, rate = detail.get('shippingDestination'), detail.get('shippingRate')
        if not isinstance(destination, dict) or not isinstance(rate, dict):
            continue
        city = str(destination.get('addressLocality', '')).strip().casefold()
        city = ''.join(c for c in unicodedata.normalize('NFKD', city) if not unicodedata.combining(c))
        if city != 'belem' or destination.get('addressRegion') != 'PA' or destination.get('addressCountry') != 'BR':
            continue
        if rate.get('currency') != 'BRL':
            continue
        # Unlike a product price, explicit zero shipping is meaningful.
        value = rate.get('value')
        if str(value).strip() in {'0', '0.0', '0.00'}:
            prices.add(0)
        else:
            amount = _amount(value)
            if amount is not None:
                prices.add(amount)
    return next(iter(prices)) if len(prices) == 1 else None


def extract_public_offer(html, target):
    """Return structured, selected product data or raise ExtractionError."""
    if not isinstance(html, str) or len(html) > 2_000_000:
        raise ExtractionError('Página ausente ou grande demais')
    if re.search(r'cf-chl-|captcha|verifique se voc[eê] [eé] humano|access denied|acesso negado', html, re.I):
        raise ExtractionError('Página de bloqueio ou desafio anti-bot')
    parser = _Scripts()
    parser.feed(html)
    products = []
    for raw in parser.scripts:
        try:
            parsed = json.loads(raw)
        except (ValueError, RecursionError):
            continue
        for node in _nodes(parsed):
            if _type(node, 'Product') and _same_product(node, target):
                products.append(node)
    if not products:
        raise ExtractionError('Produto exato não encontrado nos dados estruturados da página')
    observations = []
    for product in products:
        offers = product.get('offers', [])
        if isinstance(offers, dict):
            offers = [offers]
        for offer in offers if isinstance(offers, list) else []:
            if not isinstance(offer, dict) or not _type(offer, 'Offer'):
                continue
            if offer.get('priceValidUntil'):
                try:
                    if date.fromisoformat(str(offer['priceValidUntil'])[:10]) < date.today():
                        continue
                except ValueError:
                    continue
            if target.get('familia') == 'RX 9070 XT':
                conditions = [product.get('itemCondition'), offer.get('itemCondition')]
                if any(c and not str(c).endswith('/NewCondition') for c in conditions):
                    continue
            price_sets = _prices(offer)
            if any(len(v) > 1 for v in price_sets.values()):
                raise ExtractionError('Preços conflitantes nos dados estruturados')
            prices = {key: next(iter(values), None) for key, values in price_sets.items()}
            stock = _stock(offer.get('availability'))
            observations.append((prices, stock, _seller(offer.get('seller')), _shipping(offer)))
    unique = []
    for item in observations:
        if item not in unique:
            unique.append(item)
    if len(unique) != 1:
        raise ExtractionError('Preço ausente ou múltiplas ofertas/vendedores ambíguos')
    prices, stock, seller, shipping = unique[0]
    condition_values = [p.get('itemCondition') for p in products]
    for p in products:
        offers = p.get('offers', [])
        if isinstance(offers, dict): offers = [offers]
        condition_values.extend(o.get('itemCondition') for o in offers if isinstance(o, dict))
    return {
        'condicao': 'novo' if any(str(c).endswith('/NewCondition') for c in condition_values if c) else 'não informada',
        'status': ('produto_esgotado' if stock == 'esgotado' else
                   'preco_publico' if any(prices.values()) else 'preco_nao_encontrado'),
        'preco_publico_centavos': prices['publico'],
        'preco_pix_centavos': prices['pix'],
        'preco_cartao_centavos': prices['cartao'],
        'preco_sem_cupom_centavos': prices['sem_cupom'],
        'preco_com_cupom_centavos': None,
        'cupom_validado': False,
        'frete_centavos': shipping,
        'preco_final_centavos': None,
        'estoque': stock,
        'vendedor': seller,
        'fonte_dados': 'schema.org JSON-LD público',
    }
