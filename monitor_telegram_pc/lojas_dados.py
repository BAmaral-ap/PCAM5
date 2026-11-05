"""Histórico e fila de alertas das lojas no SQLite existente."""
from __future__ import annotations

import hashlib
import json
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
    old = _price_fields(previous or {})
    new = _price_fields(current)
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
    if significant:
        return 'QUEDA DE PREÇO DETECTADA'
    if below_target and (not previous or not prior_below_target or decreased):
        return 'PREÇO DENTRO DA META'
    if below_target and seller_changed:
        return 'VENDEDOR ALTERADO'
    if (below_target and previous and previous.get('frete_centavos') is not None
            and current.get('frete_centavos') is not None
            and abs(previous['frete_centavos'] - current['frete_centavos']) >= 1000):
        return 'FRETE ALTERADO'
    return None


def _message(item, current, previous, event, observed, hint, cfg):
    fields = [('Preço público', 'preco_publico_centavos'), ('Preço PIX', 'preco_pix_centavos'),
              ('Preço cartão', 'preco_cartao_centavos')]
    lines = [f"🔥 LOJA — {event}", f"Produto: {item['nome']}", f"Loja: {item['loja']}"]
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
        lines.append('Faixa preliminar: ' + tier(min(known), cfg))
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
        if previous_for_comparison and (previous_for_comparison.get('estoque') == 'esgotado'
                                        or previous_for_comparison.get('status') == 'preco_nao_encontrado'):
            for field, value in known_prices.items():
                if previous_for_comparison.get(field) is None:
                    previous_for_comparison[field] = value
        event = _event(previous_for_comparison, current, item, reduction)
        hint = row['coupon_hint'] if row['coupon_hint'] and row['coupon_time'] and now-row['coupon_time'] <= 86400 else None
        if not hint and item.get('cupons'):
            hint = item['cupons'][0]
        coupon_new = hint and (row['last_coupon_alert'] != hint or not row['last_coupon_alert_time']
                               or now - row['last_coupon_alert_time'] >= 86400)
        if not event and coupon_new and current['status'] != 'produto_esgotado' and any(_price_fields(current).values()):
            event = 'POSSÍVEL PROMOÇÃO COM CUPOM'
        # Suppress only a confirmed sent Telegram alert for the same direct
        # product and exact price. Pending/uncertain sends must not hide a sale.
        if (event in {'PREÇO DENTRO DA META', 'QUEDA DE PREÇO DETECTADA', 'POSSÍVEL PROMOÇÃO COM CUPOM'}
                and row['telegram_time'] is not None and 0 <= now - row['telegram_time'] < 86400
                and row['telegram_price'] in _price_fields(current).values() and row['telegram_key']):
            sent = self.db.execute('SELECT state FROM notifications WHERE dedupe_key=?', (row['telegram_key'],)).fetchone()
            if sent and sent['state'] == 'sent':
                event = None
        payload = _message(item, current, previous_for_comparison, event, _local(now), hint, cfg) if event else None
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
