"""Atualização idempotente das compras confirmadas pelo usuário em novembro/2026."""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone

from finance import validate_purchase

UPDATE_ID = 'montagem-confirmada-2026-11-v1'
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
    limits.update(pix=None, total_maximo=9450, previsto_total=9450, entradas_pendentes=pending)
    if confirmed:
        limits['entradas_confirmadas'] = confirmed
    cfg.setdefault('metas', {})['Ryzen 7 7800X3D'] = 1750
    cfg['meta_7800x3d_inclui_frete'] = True
    cfg['alertar_orcamento_incerto'] = True
    cfg['pcam5_atualizacao_montagem'] = UPDATE_ID
    build=cfg.setdefault('montagem', {})
    for category, model in [('COOLER-CPU','ARCTIC Freezer 36 A-RGB Black'),
                            ('GABINETE','Montech XR White'), ('FONTE','Pichau Cluster 850W PG-CL850-BK01')]:
        build.setdefault(category, {})['modelo'] = model
        # Purchase ledger is the single source of truth for acquisition status.
        build[category]['possui'] = False
    if 'CPU' in build: build['CPU']['estimativa_pix'] = 1750
    if 'GPU' in build: build['GPU']['estimativa_pix'] = 5100
    profiles=cfg.setdefault('perfis', {})
    profiles['ARCTIC Freezer 36 A-RGB Black'] = {'altura_mm':159, 'sockets':['AM5'], 'compativel':True}
    profiles['Montech XR White'] = {'gpu_max_mm':420, 'cooler_max_mm':175, 'cor':'branco',
        'tipo':'Mid-Tower', 'ventoinhas_incluidas':3, 'ventoinhas_tipo':'ARGB PWM',
        'formatos_placa_mae':['ATX','MATX','MICRO-ATX','MINI-ITX','ITX'],
        'formato_fonte':'ATX', 'compativel':True}
    profiles['Pichau Cluster 850W PG-CL850-BK01'] = {'potencia_w':850,
        'conectores':{'pcie_6_2':True,'12v_2x6':True}, 'padrao':'ATX 3.1',
        'formato':'ATX', 'certificacao':'Cybenetics Gold', 'modularidade':'Full modular', 'compativel':True}
    cfg['observacao_orcamento'] = ('R$ 9.450 é previsão total condicional incluindo RAM no cartão; '
        'não representa dinheiro disponível. O crédito não é receita. Custos e fretes das peças faltantes são estimativas.')
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


def _expire_obsolete_pending(db):
    """Expire only queued alerts that can be tied unambiguously to a purchased part."""
    categories = {'FONTE', 'GABINETE', 'COOLER-CPU'}
    offers = [json.loads(row['payload']) for row in db.execute('SELECT payload FROM offers')]
    shop_items = []
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='shop_targets'").fetchone():
        shop_items = [json.loads(row['item']) for row in db.execute('SELECT item FROM shop_targets')]
    for row in db.execute("SELECT id,dedupe_key,payload FROM notifications WHERE state='pending'").fetchall():
        payload = row['payload']
        telegram_match = any(
            o.get('categoria') in categories and o.get('link') and
            row['dedupe_key'].endswith(':' + o['link']) and
            ('Modelo identificado: ' + o.get('peca', '') + '\n') in payload
            for o in offers
        )
        shop_match = any(
            item.get('categoria') in categories and item.get('nome') and item.get('url') and
            ('Produto: ' + item['nome'] + '\n') in payload and item['url'] in payload
            for item in shop_items
        )
        if telegram_match or shop_match:
            db.execute("UPDATE notifications SET state='expired',updated_at=? WHERE id=? AND state='pending'",
                       (datetime.now(timezone.utc).isoformat(), row['id']))


def apply_confirmed(store):
    """Atomic ledger migration; conflicts abort without modifying the ledger."""
    db=store.db
    with db:
        db.execute('BEGIN IMMEDIATE')
        existing=store.purchases()
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
        _expire_obsolete_pending(db)
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
