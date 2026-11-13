"""Adaptadores de descoberta; resultados nunca são preços confirmados."""
import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from lojas_rede import FetchError, _NoRedirect, _retry_after

QUERIES = ['RX 9070 XT promoção', 'Radeon RX 9070 XT menor preço',
           'RX 9070 XT 16GB comprar', 'RX 9070 XT desconto PIX',
           'RX 9070 XT Black Friday', 'Sapphire RX 9070 XT', 'XFX RX 9070 XT',
           'PowerColor RX 9070 XT', 'ASRock RX 9070 XT']


def xml_links(body):
    if '<!DOCTYPE' in body.upper() or '<!ENTITY' in body.upper():
        raise ValueError('XML com entidades não permitido')
    root = ET.fromstring(body)
    tag = lambda node: node.tag.rsplit('}', 1)[-1]
    links = []
    for node in root.iter():
        if tag(node) in {'loc', 'link'}:
            value = node.get('href') or node.text or ''
            if value.strip().startswith('https://'):
                links.append(value.strip())
    return tag(root), list(dict.fromkeys(links))[:50000]


def search(source, query):
    """APIs oficiais opt-in; custos/cotas dependem do plano do usuário."""
    provider = source['tipo']
    if source.get('custos_autorizados') is not True:
        raise FetchError('configuracao_pendente', 'API requer autorização de uso do plano/cota')
    headers = {'Accept': 'application/json'}
    if provider == 'brave':
        key = os.getenv('BRAVE_SEARCH_API_KEY')
        if not key:
            raise FetchError('chave_ausente', 'Configure BRAVE_SEARCH_API_KEY localmente')
        headers['X-Subscription-Token'] = key
        url = 'https://api.search.brave.com/res/v1/web/search?' + urllib.parse.urlencode({'q': query, 'country': 'BR', 'count': 20})
    elif provider == 'google_cse':
        key, cx = os.getenv('GOOGLE_SEARCH_API_KEY'), os.getenv('GOOGLE_SEARCH_CX')
        if not key or not cx:
            raise FetchError('chave_ausente', 'Configure GOOGLE_SEARCH_API_KEY e GOOGLE_SEARCH_CX localmente')
        url = 'https://www.googleapis.com/customsearch/v1?' + urllib.parse.urlencode({'key': key, 'cx': cx, 'q': query, 'gl': 'br', 'num': 10})
    else:
        raise ValueError('Adaptador desconhecido')
    try:
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(urllib.request.Request(url, headers=headers), timeout=15) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError('Resposta grande demais')
            data = json.loads(raw)
        return ([item['url'] for item in data.get('web', {}).get('results', []) if 'url' in item]
                if provider == 'brave' else [item['link'] for item in data.get('items', []) if 'link' in item])
    except urllib.error.HTTPError as exc:
        raise FetchError('api_http_' + str(exc.code), 'API de busca indisponível', _retry_after(exc.headers)) from None
    except (OSError, ValueError):
        raise FetchError('api_falhou', 'Falha na resposta da API de busca') from None
