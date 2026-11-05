"""Acesso HTTP público para lojas, sem sessões, redirects ou bypass de bloqueios."""
from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

USER_AGENT = 'PCAM5Monitor/1.0'
DOMAINS = {
    'kabum': {'kabum.com.br', 'www.kabum.com.br'},
    'pichau': {'pichau.com.br', 'www.pichau.com.br'},
    'terabyte': {'terabyteshop.com.br', 'www.terabyteshop.com.br'},
}
TRACKING = {'affiliated_code', 'aff', 'affiliate', 'tag', 'ref', 'gclid', 'fbclid'}
SENSITIVE = {'token', 'key', 'api_key', 'apikey', 'auth', 'authorization', 'password', 'senha', 'session', 'cookie', 'cpf', 'email', 'cep', 'sid'}


def compatible_target(category, title):
    if category == 'FONTE':
        powers = re.findall(r'\b(\d{3,4})\s*w(?:atts?)?\b', title, re.I)
        return bool(powers) and set(powers) == {'850'}
    if category == 'PLACA-MAE':
        if re.search(r'\b(?:am[\s-]*4|(?:a320|a520|b350|b450|b550|x370|x470|x570)(?:m|e|i|s)?)\b', title, re.I):
            return False
        return bool(re.search(r'\b(?:am[\s-]*5|(?:a620|b650|x670|b840|b850|x870)(?:m|e|i|s)?)\b', title, re.I))
    return True


class FetchError(Exception):
    def __init__(self, status, detail, retry_seconds=None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.retry_seconds = retry_seconds


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def product_url(url, store):
    if not isinstance(url, str) or len(url) > 2048 or store not in DOMAINS:
        raise ValueError('Loja ou URL não suportada')
    try:
        parts = urllib.parse.urlsplit(url.strip())
        host = (parts.hostname or '').lower()
        if (parts.scheme.lower() != 'https' or host not in DOMAINS[store] or parts.username or parts.password
                or parts.port not in (None, 443) or not parts.path or parts.path == '/'):
            raise ValueError('URL deve ser HTTPS direta da loja, sem usuário nem porta personalizada')
    except ValueError:
        raise ValueError('URL deve ser HTTPS direta da loja, sem usuário nem porta personalizada') from None
    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    if any(k.lower() in SENSITIVE or re.search(
        r'(?:^|[_-])(?:access.?token|refresh.?token|token|secret|api.?key|auth(?:orization)?|session(?:id)?|cookie|password|senha|cpf|email|cep)(?:$|[_-])',
        k, re.I) for k, _ in pairs):
        raise ValueError('URL contém parâmetro sensível; use um endereço público sem credenciais')
    pairs = [(k, v) for k, v in pairs if k.lower() not in TRACKING and not k.lower().startswith('utm_')]
    return urllib.parse.urlunsplit(('https', host, parts.path, urllib.parse.urlencode(sorted(pairs)), ''))


def _robot_normalize(value):
    """Normalize unreserved percent escapes; preserve reserved encoded octets."""
    result = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == '%':
            if index + 2 >= len(value) or not re.fullmatch(r'[0-9a-fA-F]{2}', value[index+1:index+3]):
                raise FetchError('robots_ambiguo', 'Codificação inválida no robots.txt ou URL')
            number = int(value[index+1:index+3], 16)
            decoded = chr(number)
            if number < 128 and (decoded.isalnum() or decoded in '-._~'):
                result.append(decoded)
            else:
                result.append(f'%{number:02X}')
            index += 3
        elif ord(char) > 127:
            result.extend(f'%{byte:02X}' for byte in char.encode('utf-8'))
            index += 1
        else:
            result.append(char)
            index += 1
    return ''.join(result)


def robots_allows(content, url):
    """Evaluate matching robots groups and longest allow/disallow rules."""
    if not isinstance(content, str) or not content.strip():
        raise FetchError('robots_indisponivel', 'robots.txt vazio ou inválido')
    groups = []
    current = None
    for raw in content.splitlines():
        line = raw.split('#', 1)[0].strip()
        if ':' not in line:
            continue
        key, value = (part.strip() for part in line.split(':', 1))
        key = key.lower()
        if key == 'user-agent' and value:
            if current is None or current['directives']:
                current = {'agents': [], 'rules': [], 'delays': [], 'directives': False}
                groups.append(current)
            current['agents'].append(value.lower())
        elif current and key in {'allow', 'disallow', 'crawl-delay', 'request-rate'}:
            current['directives'] = True
            if key in {'allow', 'disallow'} and value:
                current['rules'].append((key == 'allow', value))
            elif key == 'crawl-delay' and re.fullmatch(r'\d+(?:\.\d+)?', value):
                current['delays'].append(float(value))
            elif key == 'request-rate' and re.fullmatch(r'\d+/\d+', value):
                count, seconds = map(int, value.split('/'))
                if count:
                    current['delays'].append(seconds / count)
    if not groups:
        raise FetchError('robots_indisponivel', 'robots.txt sem grupo de agente reconhecível')
    agent = USER_AGENT.split('/', 1)[0].lower()
    matches = []
    for group in groups:
        best = max((len(a) for a in group['agents'] if a != '*' and a in agent), default=0)
        if not best and '*' in group['agents']:
            best = 1
        if best:
            matches.append((best, group))
    if not matches:
        return 0
    longest_agent = max(score for score, _ in matches)
    selected = [group for score, group in matches if score == longest_agent]
    parsed = urllib.parse.urlsplit(url)
    path = _robot_normalize(parsed.path + (('?' + parsed.query) if parsed.query else ''))
    matched_rules = []
    for group in selected:
        for allowed, raw_pattern in group['rules']:
            end_anchor = raw_pattern.endswith('$')
            pattern = _robot_normalize(raw_pattern[:-1] if end_anchor else raw_pattern)
            regex = '^' + re.escape(pattern).replace(r'\*', '.*') + ('$' if end_anchor else '')
            if re.search(regex, path):
                matched_rules.append((len(pattern.replace('*', '')), allowed))
    if matched_rules and not max(matched_rules)[1]:
        raise FetchError('robots_proibido', 'A consulta automatizada é proibida por robots.txt')
    return max((delay for group in selected for delay in group['delays']), default=0)


@dataclass(frozen=True)
class Response:
    status: int
    body: str
    content_type: str = ''


def _retry_after(headers):
    value = headers.get('Retry-After', '') if headers else ''
    try:
        return max(0, min(int(value), 2_147_483_647))
    except (TypeError, ValueError):
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0, min(int((dt - datetime.now(timezone.utc)).total_seconds()), 2_147_483_647))
        except (TypeError, ValueError, OverflowError):
            return None


def fetch_public(url, max_bytes=2_000_000):
    """Follow the session proxy and verified TLS, with no cookies or redirects."""
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'text/html,text/plain;q=0.9'}, method='GET')
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=15) as response:
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise FetchError('resposta_grande', 'Resposta ultrapassou o limite de tamanho')
            charset = response.headers.get_content_charset() or 'utf-8'
            try:
                body = raw.decode(charset, errors='strict')
            except (UnicodeError, LookupError):
                raise FetchError('resposta_invalida', 'Não foi possível interpretar a resposta da loja') from None
            return Response(response.status, body, response.headers.get_content_type())
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise FetchError('bloqueado', 'Acesso negado pela loja; consultas pausadas', _retry_after(exc.headers)) from None
        if exc.code == 429:
            raise FetchError('limite_429', 'Limite da loja atingido', _retry_after(exc.headers)) from None
        if 300 <= exc.code < 400:
            raise FetchError('redirecionamento', 'Redirecionamento não seguido; confira a URL pública direta') from None
        raise FetchError('http_erro', f'HTTP {exc.code}', _retry_after(exc.headers)) from None
    except urllib.error.URLError:
        raise FetchError('rede_indisponivel', 'Falha de conexão com a loja') from None
    except TimeoutError:
        raise FetchError('rede_indisponivel', 'Tempo de espera esgotado') from None
    except OSError:
        raise FetchError('rede_indisponivel', 'Falha de conexão com a loja') from None


def validate_catalog(catalog):
    """Validate and normalize public targets; never infer a missing model or URL."""
    if not isinstance(catalog, dict) or not isinstance(catalog.get('produtos'), list):
        raise ValueError('produtos_lojas.json deve conter uma lista produtos')
    if len(catalog['produtos']) > 100:
        raise ValueError('Use no máximo 100 produtos monitorados por catálogo')
    products = []
    seen = set()
    for raw in catalog['produtos']:
        if not isinstance(raw, dict):
            raise ValueError('Produto de loja inválido')
        item = dict(raw)
        for field in ('id', 'nome', 'loja', 'categoria', 'url', 'termos_identificacao'):
            if not item.get(field):
                raise ValueError(f'Produto sem campo obrigatório: {field}')
        if not isinstance(item['id'], str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', item['id']):
            raise ValueError('ID do produto inválido')
        if not isinstance(item['nome'], str) or len(item['nome']) > 160 or re.search(r'https?://|[\x00-\x1f]', item['nome']):
            raise ValueError('Nome de produto inválido')
        terms = item['termos_identificacao']
        if not isinstance(terms, list) or not terms or not all(isinstance(t, str) and re.fullmatch(r'[A-Za-z0-9+ ._-]{2,80}', t) for t in terms):
            raise ValueError('termos_identificacao deve conter termos simples de modelo')
        if item['categoria'] not in {'GPU', 'CPU', 'FONTE', 'PLACA-MAE', 'RAM', 'COOLER-CPU', 'GABINETE'}:
            raise ValueError('Categoria de produto não suportada')
        if not compatible_target(item['categoria'], item['nome']):
            raise ValueError('Cadastre apenas fontes 850 W e placas-mãe identificadas como AM5.')
        if type(item.get('ativo', True)) is not bool:
            raise ValueError('ativo deve ser true ou false')
        item['url'] = product_url(item['url'], item['loja'])
        interval = item.get('intervalo_segundos', 180 if item['categoria'] == 'GPU' else 600)
        if type(interval) is not int or interval < 120 or interval > 86400:
            raise ValueError('Intervalo deve estar entre 120 e 86400 segundos')
        item['intervalo_segundos'] = interval
        for field in ('sku', 'sku_loja'):
            if item.get(field) is not None and (not isinstance(item[field], str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,80}', item[field])):
                raise ValueError(f'{field} inválido')
        price = item.get('meta_centavos')
        if type(price) is not int or price <= 0 or price > 10_000_000:
            raise ValueError('meta_centavos deve ser um valor positivo em centavos')
        coupons = item.get('cupons', [])
        if not isinstance(coupons, list) or len(coupons) > 10 or not all(
            isinstance(c, str) and re.fullmatch(r'[A-Za-z0-9_-]{4,40}', c) for c in coupons
        ):
            raise ValueError('cupons deve conter no máximo 10 códigos simples')
        if item['id'] in seen or item['url'] in seen:
            raise ValueError('ID ou URL de produto duplicado')
        seen.update({item['id'], item['url']})
        products.append(item)
    return products
