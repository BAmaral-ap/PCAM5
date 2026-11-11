"""Atualização idempotente das compras confirmadas pelo usuário em novembro/2026."""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone

from finance import validate_purchase

OLD_UPDATE_ID = 'montagem-confirmada-2026-11-v1'
PREVIOUS_UPDATE_ID = 'montagem-confirmada-2026-11-v2-ram-ssd'
UPDATE_ID = 'montagem-confirmada-2026-11-v3-9800x3d'
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
    {'ref_confirmacao':'cpu-9800x3d-2026-11-11', 'categoria':'CPU', 'modelo':'Ryzen 7 9800X3D',
     'sku':'100-100001084WOF', 'fabricante':'AMD', 'arquitetura':'Zen 5', 'socket':'AM5',
     'nucleos':8, 'threads':16, 'total_centavos':206002, 'pagamento':'cartao', 'parcelas':6,
     'parcelas_pagas':0, 'primeira_parcela':'2026-12', 'dia_vencimento':10,
     'rateio_estimado':True, 'valores_pagos_centavos':[],
     'juros':'sem_juros', 'data_compra':'2026-11-11', 'frete_incluido':True,
     'status_financeiro':'parcelado', 'status_compra':'COMPRADO', 'status_entrega':'não informado'},
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
    if previous_update not in {OLD_UPDATE_ID, PREVIOUS_UPDATE_ID, UPDATE_ID}:
        limits.update(pix=None, total_maximo=9450, previsto_total=9450)
    limits['entradas_pendentes'] = pending
    if confirmed:
        limits['entradas_confirmadas'] = confirmed
    metas = cfg.setdefault('metas', {})
    metas.setdefault('DDR5 2x16GB', 2400)
    if previous_update not in {OLD_UPDATE_ID, PREVIOUS_UPDATE_ID, UPDATE_ID}:
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
    if previous_update not in {OLD_UPDATE_ID, PREVIOUS_UPDATE_ID, UPDATE_ID}:
        if 'GPU' in build: build['GPU']['estimativa_pix'] = 5100
    cpu = build.setdefault('CPU', {})
    cpu.update(modelo='Ryzen 7 9800X3D', possui=False, pagamento='cartao',
               estimativa_pix=None, estimativa_cartao=None)
    memory = build.setdefault('RAM', {})
    memory.update(pagamento='indefinido', fonte_pagamento='a_definir', parcelamento='opcional')
    ssd = build.setdefault('SSD', {})
    if previous_update not in {PREVIOUS_UPDATE_ID, UPDATE_ID}:
        if ssd.get('possui', False) or not ssd.get('estimativa_pix'):
            ssd['estimativa_pix'] = 700
        if ssd.get('possui', False):
            ssd['estimativa_cartao'] = None
        ssd.update(modelo='SSD NVMe', possui=False, pagamento='pix')
    else:
        ssd.setdefault('estimativa_pix', 700)
    profiles=cfg.setdefault('perfis', {})
    confirmed_profiles = {
        'Ryzen 7 9800X3D': {'socket':'AM5', 'arquitetura':'Zen 5', 'nucleos':8,
                            'threads':16, 'sku':'100-100001084WOF', 'compativel':True},
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
    cfg['observacao_orcamento'] = ('CPU adquirida em 6 parcelas; nenhuma parcela é dada como paga automaticamente. '
        'RAM, placa-mãe e SSD podem ser avaliados à vista; empréstimo apenas simulado não é saldo disponível.')
    finance = limits.setdefault('financiamentos', [])
    if not isinstance(finance, list):
        raise ValueError('Financiamentos precisam estar em uma lista; confira o orçamento.')
    if not any(isinstance(item, dict) and item.get('id') == 'simulacao-ram-placa-ssd' for item in finance):
        finance.append({'id':'simulacao-ram-placa-ssd', 'status':'simulado', 'valor_centavos':268000,
                        'total_centavos':333960, 'parcelas':4, 'parcela_centavos':83490,
                        'encargos_centavos':65960})
    return cfg


def _norm(value):
    return re.sub(r'[^a-z0-9]', '', str(value).lower())


def _is_same(existing, wanted):
    if existing.get('ref_confirmacao') == wanted['ref_confirmacao']:
        return True
    if existing.get('categoria') != wanted['categoria']:
        return False
    name = _norm(existing.get('modelo',''))
    reference = {'COOLER-CPU':'arcticfreezer36', 'GABINETE':'montechxrwhite',
                 'FONTE':'pichaucluster850w', 'CPU':'9800x3d'}[wanted['categoria']]
    identifiers = _norm(' '.join(str(existing.get(k, '')) for k in ('sku', 'referencia', 'modelo')))
    if wanted['categoria'] == 'COOLER-CPU' and 'acfre00124a' in identifiers:
        return True
    if wanted['categoria'] == 'FONTE' and 'pgcl850bk01' in identifiers:
        return True
    if wanted['categoria'] == 'CPU' and '100100001084wof' in identifiers:
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
    # Categorias que o usuário reativou continuam aptas a receber alertas.
    owned = {p.get('categoria') for p in store.purchases() if not p.get('monitorar_novamente', False)}
    categories = (owned & {'CPU', 'FONTE', 'GABINETE', 'COOLER-CPU'}) | {'RAM'}
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
                kit = re.search(r'\b2\s*[x×]\s*16\s*gb\b', shop_match['nome'], re.I)
                desired = '2×16 GB' if kit else '1×16 GB'
                strict = (cfg or {}).get('montagem', {}).get('RAM', {}).get('parcelamento') == 'cartao_sem_juros'
                if not strict:
                    lines = [line for line in lines if not line.startswith((
                        '⚠️ Parcelamento sem juros ainda não confirmado.',
                        'As parcelas da RAM serão pagas com renda pessoal separada',
                        'Confira o preço à vista, as condições do cartão'))]
                lines[0] = '🔥 LOJA — RAM — VERIFICAR PREÇO E PARCELAMENTO'
                note = (f'⚠️ Parcelamento sem juros ainda não confirmado. Confirme o total no cartão e {desired}.'
                        if strict else f'Confira o preço à vista, as condições do cartão e se a memória é {desired}.')
                lines.insert(1, note)
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
        first_legacy_update = not store.get_meta(PREVIOUS_UPDATE_ID) and first_update
        ops=[]
        for raw in PURCHASES:
            wanted=validate_purchase(raw)
            matches=[p for p in existing if _is_same(p,wanted)]
            if len(matches)>1:
                raise ValueError('Mais de um registro possível para '+wanted['categoria']+'; confira --budget antes de atualizar.')
            if matches:
                old=matches[0]
                if wanted['categoria'] == 'CPU':
                    old_paid = old.get('parcelas_pagas', 0)
                    if old_paid and (old.get('pagamento') != 'cartao' or old.get('parcelas') != 6 or old.get('total_centavos') != wanted['total_centavos']):
                        raise ValueError('Registro anterior da CPU tem parcelas pagas incompatíveis; confira --budget antes de atualizar.')
                    merged = dict(old)
                    merged.pop('id', None)
                    if any(old.get(k) not in (None, wanted[k]) for k in ('total_centavos', 'pagamento', 'parcelas')):
                        merged.setdefault('dados_antes_atualizacao', {k: old.get(k) for k in ('total_centavos', 'pagamento', 'parcelas', 'parcelas_pagas')})
                    merged.update(wanted)
                    merged['parcelas_pagas'] = old_paid if old_paid else 0
                    if old_paid:
                        merged['valores_pagos_centavos'] = old.get('valores_pagos_centavos')
                    if old_paid == wanted['parcelas']:
                        merged['status_financeiro'] = 'pago'
                    if first_update:
                        merged.pop('monitorar_novamente', None)
                    ops.append(('update', old['id'], validate_purchase(merged)))
                    continue
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
                if first_legacy_update:
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
        if first_legacy_update:
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
    if category not in {'CPU','FONTE','GABINETE','COOLER-CPU'}:
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
