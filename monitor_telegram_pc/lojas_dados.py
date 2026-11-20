"""Histórico e fila de alertas das lojas no SQLite existente."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from notifications import money
from rx9070 import tier


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _utc(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _local(timestamp):
    return datetime.fromtimestamp(timestamp).astimezone().strftime('%d/%m/%Y %H:%M:%S %z')


def key_for(item):
    identity = [item['url'], item.get('sku'), item.get('sku_loja'), item['termos_identificacao']]
    return hashlib.sha256(_dump(identity).encode()).hexdigest()


def _price_fields(offer):
    return {k: offer.get(k) for k in ('preco_publico_centavos', 'preco_sem_cupom_centavos', 'preco_pix_centavos', 'preco_cartao_centavos')}


def _event(previous, current, target, reduction):
    if current['status'] == 'produto_esgotado':
        return None
    def effective(data):
        shipping = data.get('frete_centavos')
        return {key: value + shipping if value is not None and type(shipping) is int else value
                for key, value in _price_fields(data).items()}
    old = effective(previous or {})
    new = effective(current)
    below_target = any(value is not None and value <= target['meta_centavos'] for value in new.values())
    prior_below_target = any(value is not None and value <= target['meta_centavos'] for value in old.values())
    decreased = any(old[k] is not None and new[k] is not None and new[k] < old[k] for k in new)
    significant = any(old[k] is not None and new[k] is not None and
                      new[k] * 100 <= old[k] * (100 - reduction) and new[k] < old[k] for k in new)
    restocked = (previous is not None and previous.get('estoque') == 'esgotado'
                 and current.get('estoque') == 'em_estoque')
    seller_changed = (previous and previous.get('vendedor') and current.get('vendedor') and
                      previous['vendedor'] != current['vendedor'])
    if restocked and (below_target or not any(value is not None for value in new.values())):
        return 'PRODUTO VOLTOU AO ESTOQUE'
    if target.get('descoberto') and prior_below_target and not below_target and any(v is not None for v in new.values()):
        return 'PROMOÇÃO ENCERRADA'
    if significant:
        return 'QUEDA DE PREÇO DETECTADA'
    if below_target and (not previous or not prior_below_target or (decreased and not target.get('descoberto'))):
        return 'PREÇO DENTRO DA META'
    if below_target and seller_changed:
        return 'VENDEDOR ALTERADO'
    if (below_target and previous and previous.get('frete_centavos') is not None
            and current.get('frete_centavos') is not None
            and abs(previous['frete_centavos'] - current['frete_centavos']) >= 1000):
        return 'FRETE ALTERADO'
    return None


def _special(item, current, previous):
    """Simulação condicional sobre o preço público, sem supor acúmulo no Pix."""
    rule = item.get('monitoramento_especial')
    if not rule or current.get('status') == 'produto_esgotado':
        return None, None
    base = current.get('preco_publico_centavos')
    percent = rule.get('desconto_percentual')
    if type(base) is not int or base <= 0 or type(percent) is not int or not 0 <= percent <= 100:
        return None, None
    discounted = (base * (100 - percent) + 50) // 100
    freight = current.get('frete_centavos')
    total = discounted + freight if type(freight) is int and freight >= 0 else None
    old = (previous or {}).get('preco_publico_centavos')
    def level(value, shipping):
        if type(value) is not int: return 0
        estimate = (value * (100 - percent) + 50) // 100
        if type(shipping) is int and shipping >= 0 and estimate + shipping <= item['meta_centavos']: return 3
        if value <= rule['prioridade_centavos']: return 2
        if value <= rule['aproximacao_centavos']: return 1
        return 0
    now_level = level(base, freight)
    old_level = level(old, (previous or {}).get('frete_centavos'))
    event = None
    if now_level > old_level:
        event = {1:'APROXIMAÇÃO DA META', 2:'ALTA PRIORIDADE', 3:'META ESTIMADA ATINGIDA'}[now_level]
    elif type(old) is int and old - base >= rule['queda_centavos']:
        event = 'QUEDA DE R$ 100 OU MAIS'
    elif previous and previous.get('estoque') == 'esgotado' and current.get('estoque') == 'em_estoque':
        event = 'PRODUTO VOLTOU AO ESTOQUE'
    elif previous and previous.get('vendedor') and current.get('vendedor') and previous['vendedor'] != current['vendedor']:
        event = 'VENDEDOR ALTERADO'
    return event, (base, discounted, freight, total)


def _special_message(item, current, event, values, observed):
    base, discounted, freight, total = values
    rule = item['monitoramento_especial']
    lines = [f'🔥 RX 9070 XT — {event} — KABUM!', f"Modelo: {item['nome']}",
             f'Preço público base: {money(base)} (confirme a modalidade e se é preço normal)',
             f"Preço Pix: {money(current.get('preco_pix_centavos'))}",
             f"Cupom: {rule.get('cupom') or 'não informado'} — {rule['desconto_percentual']}% simulado, não validado",
             f'Preço estimado com cupom: {money(discounted)}',
             f'Frete: {money(freight)}', f'Preço final estimado: {money(total)}',
             f"Meta com frete: {money(item['meta_centavos'])}"]
    if total is not None:
        lines.append(f"Diferença para a meta: {money(abs(total-item['meta_centavos']))} " + ('abaixo' if total <= item['meta_centavos'] else 'acima'))
    else:
        lines.append('Frete não verificado: não é possível confirmar que o total atende à meta.')
    lines += [f"Estoque: {current.get('estoque', 'desconhecido')}", f"Vendedor: {current.get('vendedor') or 'não informado; confirme se é KaBuM!'}",
              'Simulação condicionada: confirme validade, limites e acúmulo no carrinho; não somamos desconto Pix.',
              f'Verificado em: {observed}', item['url']]
    return '\n'.join(lines)

def _asus_event(item, current, previous):
    rule = item['monitoramento_asus']
    old = previous or {}
    if current.get('status') == 'produto_esgotado' or current.get('estoque') == 'esgotado':
        return 'PRODUTO ESGOTADO' if previous and old.get('estoque') != 'esgotado' else None
    if previous and old.get('estoque') == 'esgotado' and current.get('estoque') == 'em_estoque':
        return 'PRODUTO VOLTOU AO ESTOQUE'
    if old.get('vendedor') and current.get('vendedor') and old['vendedor'] != current['vendedor']:
        return 'VENDEDOR ALTERADO'
    if previous and old.get('_asus_regra') and old['_asus_regra'] != rule:
        return 'CUPOM OU CONDIÇÕES ALTERADAS — CONFIRA'
    base = current.get('preco_publico_centavos')
    prev = old.get('preco_publico_centavos')
    percent = rule.get('desconto_percentual')
    if type(base) is not int or base <= 0 or type(percent) is not int or not 0 <= percent <= 100:
        return None
    discounted = (base * (100 - percent) + 50) // 100
    freight = current.get('frete_centavos')
    if type(freight) is not int or freight < 0:
        freight = rule.get('frete_referencia_centavos')
    total = discounted + freight if type(freight) is int and freight >= 0 else None
    # First valid observation establishes the baseline; an already attractive
    # price still deserves an alert without claiming a previous reduction.
    if previous is None:
        if total is not None and total <= item['meta_centavos']:
            return 'META ESTIMADA ATINGIDA'
        if discounted < rule['limite_interessante_centavos']:
            return 'OFERTA INTERESSANTE'
    # Alerts for a lower price also carry the best available priority label.
    if type(prev) is int and prev - base >= rule.get('queda_centavos', 100):
        if total is not None and total <= item['meta_centavos']:
            return 'QUEDA — META ESTIMADA ATINGIDA'
        if discounted < rule['limite_interessante_centavos']:
            return 'QUEDA — OFERTA INTERESSANTE'
        return 'PREÇO CAIU'
    if previous and type(prev) is int and prev == base:
        old_freight = old.get('frete_centavos')
        if type(old_freight) is not int or old_freight < 0:
            old_freight = rule.get('frete_referencia_centavos')
        if type(old_freight) is int and type(freight) is int and discounted + old_freight > item['meta_centavos'] >= discounted + freight:
            return 'META ESTIMADA ATINGIDA'
    return None


def _asus_message(item, current, previous, event, observed):
    rule = item['monitoramento_asus']
    base = current.get('preco_publico_centavos')
    old = (previous or {}).get('preco_publico_centavos')
    percent = rule.get('desconto_percentual')
    lines = [f'📉 ASUS PRIME RX 9070 XT — {event}', 'Loja: KaBuM!',
             f'Preço público anterior: {money(old)}', f'Preço público atual: {money(base)}']
    if type(old) is int and type(base) is int and old > base:
        lines.append(f'Queda: {money(old-base)}')
    lines += [f"Preço Pix: {money(current.get('preco_pix_centavos'))}",
              f"Preço no cartão: {money(current.get('preco_cartao_centavos'))}",
              f"Cupom: {rule.get('cupom') or 'não informado'} — validade e aplicação não confirmadas",
              f'Desconto anunciado configurado: {percent}%']
    if type(base) is int and base > 0 and type(percent) is int and 0 <= percent <= 100:
        discounted = (base * (100-percent)+50)//100
        freight = current.get('frete_centavos')
        reference = not (type(freight) is int and freight >= 0)
        if reference: freight = rule.get('frete_referencia_centavos')
        total = discounted + freight if type(freight) is int and freight >= 0 else None
        lines += [f'Preço simulado com cupom: {money(discounted)}',
                  f"Frete: {money(freight)}" + (' (referência anterior; não confirmado)' if reference else ' (observado)'),
                  f'Total simulado: {money(total)}']
    lines += [f"Meta com frete: {money(item['meta_centavos'])}",
              f"Estoque: {current.get('estoque', 'desconhecido')}",
              f"Vendedor: {current.get('vendedor') or 'não informado'}",
              'Simulação sujeita à validade, limites e acumulação do cupom; confirme preço e frete no carrinho.',
              f'Verificado em: {observed}', item['url']]
    return '\n'.join(lines)

def _message(item, current, previous, event, observed, hint, cfg, purchases=()):
    fields = [('Preço público', 'preco_publico_centavos'), ('Preço PIX', 'preco_pix_centavos'),
              ('Preço cartão', 'preco_cartao_centavos')]
    ram_pix = cfg.get('montagem', {}).get('RAM', {}).get('parcelamento') == 'avista'
    display_event = ('RAM — VERIFICAR PREÇO NO PIX' if ram_pix else 'RAM — VERIFICAR PREÇO E PARCELAMENTO') if item.get('categoria') == 'RAM' else event
    lines = [f"🔥 LOJA — {display_event}", f"Produto: {item['nome']}", f"Loja: {item['loja']}"]
    if item.get('descoberto'):
        lines.append('Oferta preliminar: condição '+current.get('condicao', 'não informada')+'; confira vendedor, garantia e reputação antes de comprar.')
    if item.get('categoria') == 'RAM':
        kit = re.search(r'\b2\s*[x×]\s*16\s*gb\b', item['nome'], re.I)
        desired = '2×16 GB' if kit else '1×16 GB'
        if cfg.get('montagem', {}).get('RAM', {}).get('parcelamento') == 'cartao_sem_juros':
            lines.append(f'⚠️ Parcelamento sem juros ainda não confirmado. Confirme o total no cartão e {desired}.')
            lines.append('As parcelas da RAM serão pagas com renda pessoal separada da reserva à vista.')
        elif ram_pix:
            lines.append(f'RAM planejada no Pix; confirme o preço à vista, frete e se a memória é {desired}.')
        else:
            lines.append(f'Confira o preço à vista, as condições do cartão e se a memória é {desired}.')
    for label, key in fields:
        current_price = current.get(key)
        old = (previous or {}).get(key)
        if current_price is not None:
            if old is not None:
                lines.append(f'{label} anterior: {money(old)}')
            lines.append(f'{label} atual: {money(current_price)}')
    if not any(value is not None for value in _price_fields(current).values()):
        lines.append('Preço atual: não encontrado')
    if current.get('preco_sem_cupom_centavos') is not None and (previous or {}).get('preco_sem_cupom_centavos') is not None:
        lines.append(f"Preço sem cupom anterior: {money(previous['preco_sem_cupom_centavos'])}")
    lines += [
        f"Preço sem cupom: {money(current.get('preco_sem_cupom_centavos'))}",
        f"Preço com cupom validado: {money(current.get('preco_com_cupom_centavos'))}",
        f"Cupom indicado: {hint or 'não informado'}" + (' (não validado)' if hint else ''),
        f"Frete anterior para Belém: {money((previous or {}).get('frete_centavos'))}",
        f"Frete para Belém: {money(current.get('frete_centavos'))}",
        f"Preço final com frete: {money(current.get('preco_final_centavos'))}",
        f"Estoque: {current.get('estoque', 'desconhecido')}",
        f"Vendedor: {current.get('vendedor') or 'não informado'}",
        f"Meta: {money(item['meta_centavos'])}",
    ]
    known = [v for v in _price_fields(current).values() if v is not None]
    if known and item.get('familia') == 'RX 9070 XT':
        shipping = current.get('frete_centavos')
        value = min(known) + shipping if type(shipping) is int and shipping >= 0 else min(known)
        lines.append('Faixa preliminar: ' + tier(value, cfg))
        profiles = cfg.get('perfis', {})
        bought = {p.get('categoria'): profiles.get(p.get('modelo'), {}) for p in purchases
                  if not p.get('monitorar_novamente', False)}
        case, psu = bought.get('GABINETE', {}), bought.get('FONTE', {})
        references = []
        if case.get('gpu_max_mm'):
            references.append(f"gabinete até {case['gpu_max_mm']} mm")
        if psu.get('potencia_w'):
            references.append(f"fonte {psu['potencia_w']} W")
        if references:
            from finance import _compatibility
            model = item['nome'] if item['nome'] in profiles else cfg.get('montagem', {}).get('GPU', {}).get('modelo', '')
            compatible, notes = _compatibility({'categoria': 'GPU', 'peca': model, 'modelo_exato': True}, cfg, list(purchases))
            status = 'INCOMPATIBILIDADE A VERIFICAR' if compatible is False else 'compatibilidade a confirmar'
            lines.append(f"Compatibilidade: {status} ({', '.join(references)}); confira dimensões, potência e conectores.")
            if compatible is False:
                lines.extend(notes)
    lines += [f"Verificado em: {observed}", 'Fonte: dados públicos estruturados da página; confira preço, frete, cupom, vendedor e estoque no checkout.', item['url']]
    return '\n'.join(lines)


class StoreTracking:
    def __init__(self, store):
        self.store = store
        self.db = store.db
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS shop_targets (
            key TEXT PRIMARY KEY, item TEXT NOT NULL, last_attempt REAL,
            next_attempt REAL NOT NULL DEFAULT 0, last_status TEXT,
            last_success REAL, last_data TEXT, failures INTEGER NOT NULL DEFAULT 0,
            coupon_hint TEXT, coupon_time REAL, last_coupon_alert TEXT, last_coupon_alert_time REAL,
            last_known_prices TEXT, telegram_price INTEGER, telegram_key TEXT, telegram_time REAL
        );
        CREATE TABLE IF NOT EXISTS shop_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, target_key TEXT NOT NULL,
            checked_at REAL NOT NULL, status TEXT NOT NULL, detail TEXT, data TEXT,
            FOREIGN KEY(target_key) REFERENCES shop_targets(key)
        );
        CREATE INDEX IF NOT EXISTS shop_checks_target ON shop_checks(target_key,checked_at);
        CREATE TABLE IF NOT EXISTS shop_hosts (
            origin TEXT PRIMARY KEY, last_request REAL, paused_until REAL NOT NULL DEFAULT 0,
            robots_text TEXT, robots_until REAL NOT NULL DEFAULT 0, paused_reason TEXT
        );
        ''')
        # Existing databases from an earlier development snapshot remain usable.
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(shop_targets)')}
        for name, kind in (('last_coupon_alert', 'TEXT'), ('last_coupon_alert_time', 'REAL'), ('last_known_prices', 'TEXT'),
                           ('telegram_price', 'INTEGER'), ('telegram_key', 'TEXT'), ('telegram_time', 'REAL')):
            if name not in columns:
                self.db.execute(f'ALTER TABLE shop_targets ADD COLUMN {name} {kind}')

    def sync(self, items):
        with self.db:
            for item in items:
                self.db.execute('''INSERT INTO shop_targets(key,item) VALUES (?,?)
                    ON CONFLICT(key) DO UPDATE SET item=excluded.item''', (key_for(item), _dump(item)))

    def target(self, item):
        return self.db.execute('SELECT * FROM shop_targets WHERE key=?', (key_for(item),)).fetchone()

    def started(self, item, now):
        # Persist before network access, preventing a restart from bypassing interval.
        with self.db:
            self.db.execute('UPDATE shop_targets SET last_attempt=?, next_attempt=? WHERE key=?',
                            (now, now + item['intervalo_segundos'], key_for(item)))

    def prioritize(self, item, now, coupon=None):
        with self.db:
            row = self.target(item)
            if row is None:
                return
            eligible = max(now, (row['last_attempt'] or 0) + item['intervalo_segundos'])
            # An incoming Telegram post may not override a product's failure
            # backoff, even before considering the separate host-wide pause.
            if row['last_status'] and row['last_status'] not in {'preco_publico', 'produto_esgotado'}:
                eligible = max(eligible, row['next_attempt'])
            coupon = coupon if isinstance(coupon, str) and 4 <= len(coupon) <= 40 and all(c.isalnum() or c in '_-' for c in coupon) else None
            if coupon:
                self.db.execute('UPDATE shop_targets SET next_attempt=MIN(next_attempt,?), coupon_hint=?, coupon_time=? WHERE key=?',
                                (eligible, coupon, now, key_for(item)))
            else:
                self.db.execute('UPDATE shop_targets SET next_attempt=MIN(next_attempt,?) WHERE key=?', (eligible, key_for(item)))

    def host(self, origin):
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO shop_hosts(origin) VALUES (?)', (origin,))
        return self.db.execute('SELECT * FROM shop_hosts WHERE origin=?', (origin,)).fetchone()

    def remember_telegram_alert(self, item, now, price, notification_key):
        with self.db:
            self.db.execute('UPDATE shop_targets SET telegram_price=?,telegram_key=?,telegram_time=? WHERE key=?',
                            (price, notification_key, now, key_for(item)))

    def host_requested(self, origin, now):
        with self.db:
            self.db.execute('UPDATE shop_hosts SET last_request=? WHERE origin=?', (now, origin))

    def robots(self, origin, text, until):
        with self.db:
            self.db.execute('UPDATE shop_hosts SET robots_text=?,robots_until=? WHERE origin=?', (text, until, origin))

    def pause_host(self, origin, until, reason):
        with self.db:
            self.db.execute('UPDATE shop_hosts SET paused_until=MAX(paused_until,?),paused_reason=? WHERE origin=?', (until, reason, origin))

    def failure(self, item, now, status, detail, retry_at=None):
        with self.db:
            self.db.execute('INSERT INTO shop_checks(target_key,checked_at,status,detail) VALUES (?,?,?,?)',
                            (key_for(item), now, status, detail[:200]))
            self.db.execute('''UPDATE shop_targets SET last_status=?, failures=failures+1,
                next_attempt=MAX(next_attempt,?) WHERE key=?''', (status, retry_at or 0, key_for(item)))

    def success(self, item, now, current, cfg, notify=True, reduction=5):
        row = self.target(item)
        previous = json.loads(row['last_data']) if row['last_data'] else None
        known_prices = json.loads(row['last_known_prices']) if row['last_known_prices'] else {}
        previous_for_comparison = dict(previous) if previous else None
        if previous_for_comparison and (item.get('monitoramento_asus') or previous_for_comparison.get('estoque') == 'esgotado'
                                        or previous_for_comparison.get('status') == 'preco_nao_encontrado'):
            for field, value in known_prices.items():
                if previous_for_comparison.get(field) is None:
                    previous_for_comparison[field] = value
        event = _event(previous_for_comparison, current, item, reduction)
        if item.get('monitoramento_asus'):
            current = dict(current, _asus_regra=dict(item['monitoramento_asus']))
        special_event, special_values = _special(item, current, previous_for_comparison)
        if item.get('monitoramento_especial'):
            event = special_event
        if item.get('monitoramento_asus'):
            event = _asus_event(item, current, previous_for_comparison)
        hint = row['coupon_hint'] if row['coupon_hint'] and row['coupon_time'] and now-row['coupon_time'] <= 86400 else None
        if not hint and item.get('cupons'):
            hint = item['cupons'][0]
        coupon_new = hint and (row['last_coupon_alert'] != hint or not row['last_coupon_alert_time']
                               or now - row['last_coupon_alert_time'] >= 86400)
        if not event and not item.get('monitoramento_especial') and not item.get('monitoramento_asus') and coupon_new and current['status'] != 'produto_esgotado' and any(_price_fields(current).values()):
            event = 'POSSÍVEL PROMOÇÃO COM CUPOM'
        # Suppress only a confirmed sent Telegram alert for the same direct
        # product and exact price. Pending/uncertain sends must not hide a sale.
        if (event in {'PREÇO DENTRO DA META', 'QUEDA DE PREÇO DETECTADA', 'POSSÍVEL PROMOÇÃO COM CUPOM'}
                and row['telegram_time'] is not None and 0 <= now - row['telegram_time'] < 86400
                and row['telegram_price'] in _price_fields(current).values() and row['telegram_key']):
            sent = self.db.execute('SELECT state FROM notifications WHERE dedupe_key=?', (row['telegram_key'],)).fetchone()
            if sent and sent['state'] == 'sent':
                event = None
        payload = (_special_message(item, current, event, special_values, _local(now)) if special_values and item.get('monitoramento_especial') else _message(item, current, previous_for_comparison, event, _local(now), hint, cfg, self.store.purchases())) if event else None
        if event and item.get('monitoramento_asus'):
            payload = _asus_message(item, current, previous_for_comparison, event, _local(now))
        if current['status'] != 'produto_esgotado':
            known_prices.update({k: v for k, v in _price_fields(current).items() if v is not None})
        # State, observation and notification are committed atomically.
        with self.db:
            self.db.execute('INSERT INTO shop_checks(target_key,checked_at,status,data) VALUES (?,?,?,?)',
                            (key_for(item), now, current['status'], _dump(current)))
            self.db.execute('''UPDATE shop_targets SET last_status=?,last_success=?,last_data=?,
                               last_known_prices=?,failures=0 WHERE key=?''',
                            (current['status'], now, _dump(current), _dump(known_prices), key_for(item)))
            if payload and notify:
                dedupe = hashlib.sha256((key_for(item) + '|' + _utc(now) + '|' + event).encode()).hexdigest()
                self.store._enqueue('loja:' + dedupe, payload, _utc(now))
                if hint:
                    self.db.execute('UPDATE shop_targets SET last_coupon_alert=?,last_coupon_alert_time=? WHERE key=?',
                                    (hint, now, key_for(item)))
        return event

    def status(self, items):
        result = []
        for item in items:
            row = self.target(item)
            if row:
                result.append({'id': item['id'], 'nome': item['nome'], 'url': item['url'],
                               'categoria': item['categoria'],
                               'monitoramento': 'pausado_comprado' if any(p.get('categoria') == item['categoria'] and not p.get('monitorar_novamente') for p in self.store.purchases()) else 'ativo',
                               'sku': item.get('sku'), 'sku_loja': item.get('sku_loja'),
                               'intervalo_segundos': item['intervalo_segundos'],
                               'meta_centavos': item['meta_centavos'],
                               'ultima_tentativa': _utc(row['last_attempt']) if row['last_attempt'] else None,
                               'ultima_verificacao': _utc(row['last_success']) if row['last_success'] else None,
                               'proxima_verificacao': _utc(row['next_attempt']) if row['next_attempt'] else None,
                               'ultimo_status': row['last_status'],
                               'ultimo_sucesso': _utc(row['last_success']) if row['last_success'] else None,
                               'ultimos_precos_conhecidos': json.loads(row['last_known_prices']) if row['last_known_prices'] else {},
                               'ultima_observacao': json.loads(row['last_data']) if row['last_data'] else None})
        return result

    def history(self, item, limit=100):
        rows = self.db.execute('''SELECT checked_at,status,detail,data FROM shop_checks
            WHERE target_key=? ORDER BY checked_at DESC,id DESC LIMIT ?''', (key_for(item), limit))
        return [{'verificado_em': _utc(row['checked_at']), 'status': row['status'],
                 'detalhe': row['detail'], 'observacao': json.loads(row['data']) if row['data'] else None} for row in rows]

    def statistics(self, item, now):
        rows = self.db.execute("SELECT checked_at,data FROM shop_checks WHERE target_key=? AND data IS NOT NULL ORDER BY checked_at", (key_for(item),)).fetchall()
        result = {}
        for field in ('preco_publico_centavos', 'preco_pix_centavos', 'preco_cartao_centavos'):
            samples = [(row['checked_at'], json.loads(row['data']).get(field)) for row in rows]
            samples = [(t, value) for t, value in samples if type(value) is int and value > 0]
            entry = {'menor_observado_centavos': min((v for _, v in samples), default=None)}
            for days in (7, 30):
                window = [(t, v) for t, v in samples if now-days*86400 <= t <= now]
                entry[str(days)+'_dias'] = {'amostras': len(window),
                    'cobertura_segundos': window[-1][0]-window[0][0] if window else 0,
                    'media_centavos': round(sum(v for _, v in window)/len(window)) if len({int(t//86400) for t, _ in window}) >= 2 else None}
            result[field] = entry
        return result
