"""Atualização idempotente das compras confirmadas pelo usuário em novembro/2026."""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone

from finance import validate_purchase

OLD_UPDATE_ID = 'montagem-confirmada-2026-11-v1'
UPDATE_ID = 'montagem-confirmada-2026-11-v2-ram-ssd'
PURCHASES = [
    {'ref_confirmacao':'cooler-2026-11', 'categoria':'COOLER-CPU', 'modelo':'ARCTIC Freezer 36 A-RGB Black',
     'total_centavos':21581, 'pagamento':'pix', 'parcelas':1, 'parcelas_pagas':0,
     'status_financeiro':'pago', 'status_compra':'COMPRADO', 'status_entrega':'não informado'},
    {'ref_confirmacao':'gabinete-2026-11-05', 'categoria':'GABINETE', 'modelo':'Montech XR White',
     'total_centavos':31712, 'pagamento':'avista', 'parcelas':1, 'parcelas_pagas':0,
     'data_compra':'2026-11-05', 'status_financeiro':'pago', 'status_compra':'COMPRADO',
     'status_entrega':'não informado', 'frete_incluido':True},
    {'ref_confirmacao':'fonte-2026-11-06', 'categoria':'FONTE', 'modelo':'Pichau Cluster 850W PG-CL850-BK01',
     'total_centavos':39505, 'pagamento':'pix', 'parcelas':1, 'parcelas_pagas':0,
     'data_compra':'2026-11-06', 'status_financeiro':'pago', 'status_compra':'COMPRADO',
     'status_entrega':'em_preparacao', 'frete_incluido':True},
]


def updated_config(config):
    cfg = copy.deepcopy(config)
    previous_update = cfg.get('pcam5_atualizacao_montagem')
    limits = cfg.setdefault('orcamento', {})
    pending = limits.get('entradas_pendentes', [])
    confirmed = limits.get('entradas_confirmadas', [])
    if not isinstance(pending, list) or not isinstance(confirmed, list):
        raise ValueError('Entradas financeiras já cadastradas precisam ser listas; confira o orçamento antes de atualizar.')
    site = lambda entry: (isinstance(entry, dict) and 'site' in str(entry.get('descricao', '')).lower()
                          and entry.get('valor_centavos') in (None, 500000))
    received = lambda entry: site(entry) and str(entry.get('status', '')).lower() in {'pago', 'paga', 'recebido', 'recebida', 'confirmado', 'confirmada'}
    for entry in pending:
        if received(entry) and entry not in confirmed:
            confirmed.append(entry)
    site_confirmed = any(site(entry) for entry in confirmed)
    pending = [entry for entry in pending if not received(entry) and not (site_confirmed and site(entry))]
    if not site_confirmed and not any(site(entry) for entry in pending):
        pending.append({'descricao':'desenvolvimento de site', 'valor_centavos':500000,
                        'status':'pendente', 'incluida_no_previsto':True})
    if previous_update not in {OLD_UPDATE_ID, UPDATE_ID}:
        limits.update(pix=None, total_maximo=9450, previsto_total=9450)
    limits['entradas_pendentes'] = pending
    if confirmed:
        limits['entradas_confirmadas'] = confirmed
    metas = cfg.setdefault('metas', {})
    if previous_update not in {OLD_UPDATE_ID, UPDATE_ID}:
        metas['Ryzen 7 7800X3D'] = 1750
    metas['SSD NVMe'] = 700
    cfg['meta_7800x3d_inclui_frete'] = True
    cfg['alertar_orcamento_incerto'] = True
    cfg['pcam5_atualizacao_montagem'] = UPDATE_ID
    build=cfg.setdefault('montagem', {})
    for category, model in [('COOLER-CPU','ARCTIC Freezer 36 A-RGB Black'),
                            ('GABINETE','Montech XR White'), ('FONTE','Pichau Cluster 850W PG-CL850-BK01')]:
        build.setdefault(category, {})['modelo'] = model
        # Purchase ledger is the single source of truth for acquisition status.
        build[category]['possui'] = False
    if previous_update not in {OLD_UPDATE_ID, UPDATE_ID}:
        if 'CPU' in build: build['CPU']['estimativa_pix'] = 1750
        if 'GPU' in build: build['GPU']['estimativa_pix'] = 5100
    memory = build.setdefault('RAM', {})
    memory.update(pagamento='cartao', fonte_pagamento='renda_pessoal_separada',
                  parcelamento='cartao_sem_juros')
    ssd = build.setdefault('SSD', {})
    if previous_update != UPDATE_ID:
        if ssd.get('possui', False) or not ssd.get('estimativa_pix'):
            ssd['estimativa_pix'] = 700
        if ssd.get('possui', False):
            ssd['estimativa_cartao'] = None
        ssd.update(modelo='SSD NVMe', possui=False, pagamento='pix')
    else:
        ssd.setdefault('estimativa_pix', 700)
    profiles=cfg.setdefault('perfis', {})
    confirmed_profiles = {
        'ARCTIC Freezer 36 A-RGB Black': {'altura_mm':159, 'sockets':['AM5'], 'compativel':True},
        'Montech XR White': {'gpu_max_mm':420, 'cooler_max_mm':175, 'cor':'branco',
            'tipo':'Mid-Tower', 'ventoinhas_incluidas':3, 'ventoinhas_tipo':'ARGB PWM',
            'formatos_placa_mae':['ATX','MATX','MICRO-ATX','MINI-ITX','ITX'], 'formato_fonte':'ATX', 'compativel':True},
        'Pichau Cluster 850W PG-CL850-BK01': {'potencia_w':850, 'conectores':{'pcie_6_2':True,'12v_2x6':True},
            'padrao':'ATX 3.1', 'formato':'ATX', 'certificacao':'Cybenetics Gold', 'modularidade':'Full modular', 'compativel':True},
    }
    for model, facts in confirmed_profiles.items():
        profile = profiles.setdefault(model, {})
        for key, value in facts.items():
            profile.setdefault(key, value)
    cfg['observacao_orcamento'] = ('A previsão total inclui RAM parcelada paga com renda pessoal separada; '
        'não representa saldo à vista disponível. Crédito não é receita. A meta do SSD é R$ 700, não preço confirmado.')
    return cfg


def _norm(value):
    return re.sub(r'[^a-z0-9]', '', str(value).lower())


def _is_same(existing, wanted):
    if existing.get('ref_confirmacao') == wanted['ref_confirmacao']:
        return True
    if existing.get('categoria') != wanted['categoria']:
        return False
    name = _norm(existing.get('modelo',''))
    reference = {'COOLER-CPU':'arcticfreezer36', 'GABINETE':'montechxrwhite', 'FONTE':'pichaucluster850w'}[wanted['categoria']]
    identifiers = _norm(' '.join(str(existing.get(k, '')) for k in ('sku', 'referencia', 'modelo')))
    if wanted['categoria'] == 'COOLER-CPU' and 'acfre00124a' in identifiers:
        return True
    if wanted['categoria'] == 'FONTE' and 'pgcl850bk01' in identifiers:
        return True
    if wanted['categoria'] == 'GABINETE' and 'montechxr' in name:
        colour = _norm(existing.get('cor', ''))
        if 'white' in name or 'branco' in name or colour in {'white', 'branco'}:
            return True
        if not any(c in name or c == colour for c in ('black', 'preto')):
            raise ValueError('Gabinete Montech XR já registrado sem cor identificada; confira --budget antes de atualizar.')
    return reference in name


def _refresh_pending(store, cfg=None):
    """Preserve history and re-evaluate queued RAM alerts under the new policy."""
    db = store.db
    categories = {'FONTE', 'GABINETE', 'COOLER-CPU', 'RAM'}
    offers = [json.loads(row['payload']) for row in db.execute('SELECT payload FROM offers')]
    shop_items = []
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='shop_targets'").fetchone():
        shop_items = [json.loads(row['item']) for row in db.execute('SELECT item FROM shop_targets')]
    for row in db.execute("SELECT id,dedupe_key,payload FROM notifications WHERE state='pending'").fetchall():
        payload = row['payload']
        telegram_match = next((o for o in offers if
            o.get('categoria') in categories and o.get('link') and
            row['dedupe_key'].endswith(':' + o['link']) and
            ('Modelo identificado: ' + o.get('peca', '') + '\n') in payload
        ), None)
        shop_match = next((item for item in shop_items if
            item.get('categoria') in categories and item.get('nome') and item.get('url') and
            ('Produto: ' + item['nome'] + '\n') in payload and item['url'] in payload
        ), None)
        if telegram_match and telegram_match['categoria'] == 'RAM' and cfg is not None:
            try:
                from parsing import parse_offers
                from runtime import make_notifications
                class FreshDedupe:
                    def get_meta(self, key, default=None):
                        return None if key.startswith('dedupe:') else store.get_meta(key, default)
                original = telegram_match
                date = datetime.fromisoformat(original['data'].replace('Z', '+00:00'))
                offers_now = parse_offers(original['canal'], original['message_id'], date, original['texto'], cfg)
                current = next((offer for offer in offers_now if offer['peca'] == original['peca']), None)
                pending = make_notifications(FreshDedupe(), current, cfg, store.purchases(), datetime.now(timezone.utc)) if current else []
                if pending:
                    db.execute("UPDATE notifications SET payload=?,updated_at=? WHERE id=? AND state='pending'",
                               (pending[0]['payload'], datetime.now(timezone.utc).isoformat(), row['id']))
                    continue
            except (ValueError, TypeError, KeyError):
                pass
        if shop_match and shop_match['categoria'] == 'RAM':
            lines = payload.splitlines()
            if lines:
                lines[0] = '🔥 LOJA — RAM — VERIFICAR PREÇO E PARCELAMENTO'
                lines.insert(1, '⚠️ Parcelamento sem juros ainda não confirmado. Confirme o total no cartão e 1×16 GB.')
                db.execute("UPDATE notifications SET payload=?,updated_at=? WHERE id=? AND state='pending'",
                           ('\n'.join(lines), datetime.now(timezone.utc).isoformat(), row['id']))
                continue
        if telegram_match or shop_match:
            db.execute("UPDATE notifications SET state='expired',updated_at=? WHERE id=? AND state='pending'",
                       (datetime.now(timezone.utc).isoformat(), row['id']))


def apply_confirmed(store, config=None):
    """Atomic ledger migration; conflicts abort without modifying the ledger."""
    db=store.db
    with db:
        db.execute('BEGIN IMMEDIATE')
        existing=store.purchases()
        first_update = not store.get_meta(UPDATE_ID)
        ops=[]
        for raw in PURCHASES:
            wanted=validate_purchase(raw)
            matches=[p for p in existing if _is_same(p,wanted)]
            if len(matches)>1:
                raise ValueError('Mais de um registro possível para '+wanted['categoria']+'; confira --budget antes de atualizar.')
            if matches:
                old=matches[0]
                compatible_payment = (old.get('pagamento') == wanted['pagamento'] or
                                      wanted['pagamento'] == 'avista' and old.get('pagamento') == 'pix')
                if old.get('total_centavos') != wanted['total_centavos'] or not compatible_payment:
                    raise ValueError('Compra já registrada com valor ou pagamento diferente: '+wanted['categoria']+'. Nenhuma compra foi alterada.')
                merged=dict(old)
                merged.pop('id',None)
                if old.get('modelo') != wanted['modelo']:
                    merged.setdefault('modelo_antes_atualizacao', old.get('modelo'))
                # Do not undo explicit later reactivation or user-supplied fields.
                for key,value in wanted.items():
                    if key == 'modelo' or key not in merged:
                        merged[key]=value
                if first_update:
                    merged.pop('monitorar_novamente', None)
                ops.append(('update',old['id'],merged))
            else:
                ops.append(('insert',None,wanted))
        now=datetime.now(timezone.utc).isoformat()
        for operation, ident, payload in ops:
            encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))
            if operation=='update':
                db.execute('UPDATE purchases SET payload=? WHERE id=?',(encoded,ident))
            else:
                db.execute('INSERT INTO purchases(payload,created_at) VALUES (?,?)',(encoded,now))
        if first_update:
            for old in existing:
                if old.get('categoria') in {'SSD', 'RAM'} and not old.get('monitorar_novamente', False):
                    ident = old.pop('id')
                    old['monitorar_novamente'] = True
                    db.execute('UPDATE purchases SET payload=? WHERE id=?',
                               (json.dumps(old,ensure_ascii=False,sort_keys=True,separators=(',',':')), ident))
        _refresh_pending(store, config)
        db.execute('INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)',(UPDATE_ID,json.dumps(True)))
    return len(ops)


def reactivate(store, category):
    """Allow upgrades without deleting purchases or assuming a refund."""
    if category not in {'FONTE','GABINETE','COOLER-CPU'}:
        raise ValueError('Categoria não suportada para reativação.')
    db=store.db
    with db:
        db.execute('BEGIN IMMEDIATE')
        rows=[p for p in store.purchases() if p.get('categoria') == category]
        if not rows:
            raise ValueError('Não há compra registrada nessa categoria.')
        for row in rows:
            ident=row.pop('id')
            row['monitorar_novamente']=True
            db.execute('UPDATE purchases SET payload=? WHERE id=?',
                       (json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':')),ident))
    return len(rows)
