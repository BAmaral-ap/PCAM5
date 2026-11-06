"""Incremental Telethon collection, durable notification queue and safe logging."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from telethon.errors import FloodWaitError

from finance import evaluate_offer, _compatibility
from notifications import DeliveryError, format_alert
from parsing import parse_offers

LOG = logging.getLogger('pcam5')


def utcnow():
    return datetime.now(timezone.utc)


def timestamp(value):
    date = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date.astimezone(timezone.utc)


def setup_logging(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    class Redact(logging.Filter):
        def filter(self, record):
            message = record.getMessage()
            for name in ('TELEGRAM_API_ID', 'TELEGRAM_API_HASH', 'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID', 'GEMINI_API_KEY'):
                value = os.getenv(name)
                if value:
                    message = message.replace(value, '[protegido]')
            record.msg = re.sub(r'bot\d+:[A-Za-z0-9_-]+', 'bot[protegido]', message)
            record.args = ()
            return True
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    for handler in (logging.StreamHandler(), RotatingFileHandler(directory / 'monitor.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')):
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        handler.addFilter(Redact())
        logger.addHandler(handler)
    # Telethon can include server replies; keep routine logs minimal.
    logging.getLogger('telethon').setLevel(logging.CRITICAL)


def message_text(message):
    """Respect Telegram strikethrough and hidden links (UTF-16 entity offsets)."""
    raw = message.raw_text or ''
    encoded = bytearray(raw.encode('utf-16-le'))
    links = []
    for entity in getattr(message, 'entities', None) or []:
        if type(entity).__name__ == 'MessageEntityStrike':
            start, end = entity.offset * 2, (entity.offset + entity.length) * 2
            encoded[start:end] = b' \x00' * entity.length
        elif type(entity).__name__ == 'MessageEntityTextUrl':
            url = getattr(entity, 'url', '')
            if url.startswith(('https://', 'http://')):
                links.append(url)
    return encoded.decode('utf-16-le') + ('\n' + '\n'.join(links) if links else '')


def notification_key(offer, evaluation):
    # Exact commercial identity, independent of reposting channel/message.
    link = urlsplit(offer.get('link_loja') or '')
    tracking = {'tag', 'p', 'affiliated_code', 'aff', 'affiliate', 'ref'}
    query = [(k, v) for k, v in parse_qsl(link.query) if not k.lower().startswith('utm_') and k.lower() not in tracking]
    clean_link = urlunsplit((link.scheme.lower(), link.netloc.lower(), link.path, urlencode(sorted(query)), ''))
    identity = [offer['peca'], evaluation.get('preco_centavos') or offer.get('anunciado_centavos'), evaluation.get('pagamento'),
                offer.get('cupom'), (offer.get('loja') or '').casefold(), clean_link, offer.get('condicoes')]
    if not offer.get('modelo_exato', False):
        identity.append((offer.get('nome_completo') or '').casefold().strip())
    if offer.get('peca') == 'RX 9070 XT 16GB':
        title = offer.get('nome_completo', '')
        variant = re.search(r'\b(?:asrock|xfx|sapphire|powercolor|gigabyte|asus)\b', title, re.I)
        if variant:
            identity.append(variant.group(0).casefold())
    return hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def make_notifications(store, offer, cfg, purchases, now):
    if offer.get('categoria') == 'VENTOINHA':
        return []
    category = offer.get('categoria')
    if category in {'FONTE', 'GABINETE', 'COOLER-CPU'} and (
        cfg.get('montagem', {}).get(category, {}).get('possui', False) or
        any(p.get('categoria') == category and not p.get('monitorar_novamente', False) for p in purchases)
    ):
        return []
    # Match the product heading, rather than accessory specifications elsewhere
    # in a message (e.g. an AM4/AM5 cooler or PSU advice for a GPU).
    title = offer.get('nome_completo') or ''
    if offer.get('categoria') == 'PLACA-MAE' and re.search(
        r'\b(?:am[\s-]*4|(?:a320|a520|b350|b450|b550|x370|x470|x570)(?:m|e|i|s)?)\b', title, re.I
    ):
        return []
    if offer.get('categoria') == 'FONTE':
        watts = re.findall(r'\b(\d{3,4})\s*w(?:atts?)?\b', title, re.I)
        if not watts or set(watts) != {'850'}:
            return []
    start = timestamp(store.get_meta('alerts_started_at'))
    date = timestamp(offer['data'])
    if date < start or date > now + timedelta(minutes=2) or now - date > timedelta(minutes=cfg.get('idade_maxima_alerta_minutos', 30)):
        return []
    if offer.get('peca') == 'RX 9070 XT 16GB':
        from rx9070 import price_candidates, tier
        from notifications import money
        candidates = price_candidates(offer.get('texto', ''))
        offer['precos_candidatos_centavos'] = candidates
        from parsing import detect_product
        models = {product[1] for line in offer.get('texto', '').splitlines()
                  if not re.match(r'^\s*fonte\s+(?:m[ií]nima|recomendada)\b', line, re.I)
                  if (product := detect_product(line))}
        if len(candidates) != 1 or len(models) > 1 or any('associação de preço ambígua' in v for v in offer.get('verificar', [])):
            offer['classificacao_preliminar'] = 'VERIFICAR PREÇO'
            if candidates:
                fingerprint = notification_key(offer, {'preco_centavos': min(candidates), 'pagamento': 'ambíguo'})
                key = 'dedupe:' + fingerprint
                previous = store.get_meta(key)
                if previous and now - timestamp(previous) < timedelta(hours=cfg.get('deduplicacao_horas', 24)):
                    return []
                values = ', '.join(money(v) for v in candidates)
                payload = (f"🔥 RX 9070 XT — VERIFICAR PREÇO\nValores anunciados ambíguos: {values}\n"
                           f"Modelo anunciado: {offer.get('nome_completo') or offer['peca']}\n"
                           f"Origem: @{offer['canal']}\nConfira preço, pagamento e frete no checkout.\n"
                           f"{offer.get('link_loja') or offer['link']}\nOrigem: {offer['link']}")
                return [{'key': fingerprint + ':' + offer['link'], 'payload': payload,
                         'date': offer['data'], 'fingerprint': key}]
            return []
        value = candidates[0]
        label = tier(value, cfg)
        offer['classificacao_preliminar'] = label
        if label == 'PREÇO ALTO':
            return []
        meta = round(float(cfg.get('metas', {}).get('RX 9070 XT 16GB', 5100)) * 100)
        link = offer.get('link_loja') or offer['link']
        compatibility = ''
        profiles = cfg.get('perfis', {})
        owned = {p.get('categoria'): profiles.get(p.get('modelo'), {}) for p in purchases
                 if not p.get('monitorar_novamente', False)}
        case = owned.get('GABINETE', {})
        power = owned.get('FONTE', {})
        if case.get('gpu_max_mm') or power.get('potencia_w'):
            compatible, notes = _compatibility(offer, cfg, purchases)
            references = []
            if case.get('gpu_max_mm'):
                references.append(f"gabinete até {case['gpu_max_mm']} mm")
            if power.get('potencia_w'):
                references.append(f"fonte {power['potencia_w']} W")
            state = 'INCOMPATIBILIDADE A VERIFICAR' if compatible is False else 'compatibilidade a confirmar'
            compatibility = f"Compatibilidade: {state} ({', '.join(references)}); confira dimensões, potência e conectores.\n"
            if compatible is False:
                compatibility += '; '.join(notes) + '\n'
        payload = (f"🔥 RX 9070 XT — {label}\n"
                   f"Modelo anunciado: {offer.get('nome_completo') or offer['peca']}\n"
                   f"Preço anunciado: {money(value)}\n"
                   f"Pagamento e frete: confirme no checkout\n"
                   f"Loja: {offer.get('loja') or 'não informada'}\n"
                   f"Cupom: {offer.get('cupom') or 'não informado'}\n"
                   f"Origem: @{offer['canal']}\nMeta: {money(meta)}\n"
                   f"Diferença da meta: {money(value-meta)}\n"
                   f"{compatibility}"
                   f"⚠️ Preço preliminar; confirme estoque, cupom e frete antes de comprar.\n"
                   f"{link}\nOrigem: {offer['link']}")
        fingerprint = notification_key(offer, {'preco_centavos': value, 'pagamento': 'anunciado'})
        key = 'dedupe:' + fingerprint
        previous = store.get_meta(key)
        if previous and now - timestamp(previous) < timedelta(hours=cfg.get('deduplicacao_horas', 24)):
            return []
        return [{'key': fingerprint + ':' + offer['link'], 'payload': payload,
                 'date': offer['data'], 'fingerprint': key}]
    evaluation = evaluate_offer(offer, cfg, purchases)
    offer['avaliacao'] = evaluation['classificacao']
    # An advertised total with no payment method is a lead, not a PIX or card
    # price. Surface promising leads without changing the financial projection.
    announced = offer.get('anunciado_centavos')
    target = evaluation.get('meta_centavos')
    categories = {'CPU', 'GPU', 'PLACA-MAE', 'RAM', 'FONTE', 'COOLER-CPU', 'GABINETE'}
    broad = {'PLACA-MAE', 'RAM', 'FONTE', 'COOLER-CPU', 'GABINETE'}
    discovery = cfg.get('alternativas_gerais', {})
    margin = discovery.get('margem_percentual', 25) if offer.get('categoria') in broad and discovery.get('ativado') else 0
    budget_lead = (cfg.get('alertar_orcamento_incerto') and evaluation['classificacao'] == 'NÃO COMPENSA'
        and evaluation['orcamento'].get('conclusao_viavel') is False
        and (offer.get('categoria') in {'CPU', 'PLACA-MAE', 'RAM'} or any(
            p.get('categoria') == offer.get('categoria') and p.get('monitorar_novamente', False) for p in purchases))
        and offer.get('categoria') not in evaluation['orcamento'].get('ja_possui', [])
        and evaluation.get('compativel') is not False)
    uncertain_price = ((evaluation['classificacao'] == 'VERIFICAR' or budget_lead) and evaluation.get('preco_centavos') is None
        and offer.get('categoria') in categories
        and offer.get('categoria') not in evaluation['orcamento'].get('ja_possui', [])
        and evaluation.get('compativel') is not False and type(announced) is int and announced > 0
        and type(target) is int and target > 0 and isinstance(margin, (int, float)) and 0 <= margin <= 100
        and announced * 100 <= target * (100 + margin)
        and 'Preço fora da faixa plausível; verificar erro ou produto diferente' not in offer.get('verificar', []))
    if uncertain_price:
        evaluation = dict(evaluation, classificacao='VERIFICAR PREÇO', pagamento='não informado',
                          diferenca_centavos=announced-target,
                          diferenca_percentual=round((announced-target)*100/target, 2),
                          justificativa=['Preço anunciado sem forma de pagamento; confirme PIX, cartão, cupom e orçamento antes de decidir.'] + evaluation['justificativa'])
    known_price = evaluation.get('total_entregue_centavos') or evaluation.get('preco_centavos')
    budget_alert = (budget_lead and type(known_price) is int and known_price > 0 and type(target) is int and target > 0
                    and known_price * 100 <= target * (100 + margin)
                    and 'Preço fora da faixa plausível; verificar erro ou produto diferente' not in offer.get('verificar', []))
    if budget_alert and not uncertain_price:
        evaluation = dict(evaluation, classificacao='VERIFICAR ORÇAMENTO',
                          justificativa=['Preço interessante; saldo disponível e custo total precisam ser confirmados.'] + evaluation['justificativa'])
    if not uncertain_price and not budget_alert and evaluation['classificacao'] not in cfg.get('alertas', {}).get('classes', ['COMPRAR AGORA', 'BOA PROMOÇÃO']):
        return []
    fingerprint = notification_key(offer, evaluation)
    key = 'dedupe:' + fingerprint
    previous = store.get_meta(key)
    if previous and now - timestamp(previous) < timedelta(hours=cfg.get('deduplicacao_horas', 24)):
        return []
    # Fingerprint metadata is committed atomically with the message and outbox.
    return [{'key': fingerprint + ':' + offer['link'], 'payload': format_alert(offer, evaluation),
             'date': offer['data'], 'fingerprint': key}]


async def collect_once(client, store, cfg, limit=150, days=7, notifications_enabled=True, on_message=None, on_alert=None):
    now = utcnow()
    if store.get_meta('alerts_started_at') is None:
        store.set_meta('alerts_started_at', now.isoformat())
    count = 0
    for channel in cfg['canais']:
        cooldown = store.get_meta('flood:' + channel)
        if cooldown and now < timestamp(cooldown):
            continue
        try:
            entity = await client.get_entity(channel)
            cursor = store.get_cursor(channel)
            if cursor is None:
                # Backfill only for history; never alert on first-channel baseline.
                baseline = [m async for m in client.iter_messages(entity, limit=limit)]
                for msg in reversed(baseline):
                    text = message_text(msg)
                    offers = parse_offers(channel, msg.id, msg.date, text, cfg) if msg.date >= now - timedelta(days=days) else []
                    store.record_message(channel, msg.id, msg.date.isoformat(), text, offers)
                if not baseline:
                    store.set_cursor(channel, 0)
                LOG.info('@%s: histórico inicial registrado, sem alertas retroativos.', channel)
                continue
            # Read oldest-first; a bounded page never skips the middle of a backlog.
            # Limit is page size, not an instruction to discard older unread messages.
            for _ in range(20):
                batch_size = 0
                async for msg in client.iter_messages(entity, min_id=store.get_cursor(channel), reverse=True, limit=limit):
                    text = message_text(msg)
                    offers = parse_offers(channel, msg.id, msg.date, text, cfg)
                    notifications = []
                    purchases = store.purchases()
                    for offer in offers:
                        evaluation = evaluate_offer(offer, cfg, purchases)
                        offer['avaliacao'] = evaluation['classificacao']
                        offer['analise'] = evaluation
                        if notifications_enabled:
                            notifications.extend(make_notifications(store, offer, cfg, purchases, utcnow()))
                    inserted = store.record_message(channel, msg.id, msg.date.isoformat(), text, offers, notifications)
                    if on_message:
                        try:
                            on_message(text)
                        except Exception as exc:
                            LOG.warning('Não foi possível priorizar URL de loja: %s.', type(exc).__name__)
                    if inserted and notifications and on_alert:
                        try:
                            on_alert(text, notifications)
                        except Exception as exc:
                            LOG.warning('Não foi possível correlacionar alerta de loja: %s.', type(exc).__name__)
                    count += len(offers)
                    batch_size += 1
                if batch_size < limit:
                    break
            LOG.info('@%s: consulta concluída; cursor %s.', channel, store.get_cursor(channel))
        except FloodWaitError as exc:
            store.set_meta('flood:' + channel, (utcnow() + timedelta(seconds=exc.seconds)).isoformat())
            LOG.warning('@%s: FloodWait de %ss; cursor preservado.', channel, exc.seconds)
        except (OSError, ConnectionError, asyncio.TimeoutError):
            LOG.warning('@%s: conexão interrompida; consulta será retomada pelo cursor.', channel)
        except Exception as exc:
            # No exception text: auth/API errors may contain sensitive input.
            LOG.error('@%s: falha %s; verifique acesso ao canal. Cursor preservado.', channel, type(exc).__name__)
    return count


async def deliver_pending(store, notifier, max_age_minutes=30, gemini_config=None):
    retry_at = store.get_meta('bot_retry_at')
    if retry_at and utcnow() < timestamp(retry_at):
        return
    for item in store.pending_notifications():
        if utcnow() - timestamp(item['source_date']) > timedelta(minutes=max_age_minutes):
            if store.claim_notification(item['id']):
                store.finish_notification(item['id'], 'expired')
            continue
        # Check destination before claiming: no send occurs if configuration is wrong.
        try:
            await asyncio.to_thread(notifier.verify_private)
        except DeliveryError:
            LOG.error('Bot indisponível ou destino não privado; confira .env e /start. Fila preservada.')
            return
        if not store.claim_notification(item['id']):
            continue
        payload = item['payload']
        settings = gemini_config or {}
        key = os.getenv('GEMINI_API_KEY', '').strip()
        limit = settings.get('limite_diario', 20)
        day_key = 'gemini_calls:' + utcnow().date().isoformat()
        used = store.get_meta(day_key, 0)
        if not payload.startswith(('🔥 RX 9070 XT', '🔥 LOJA —')) and settings.get('ativado') and key and isinstance(limit, int) and 0 < limit <= 1000 and used < limit:
            # Count attempts before the request, including failures and restarts.
            store.set_meta(day_key, used + 1)
            try:
                from gemini import explain
                comment = await asyncio.to_thread(explain, payload, key, settings.get('modelo', 'gemini-3.5-flash-lite'))
                payload = payload[:3200] + '\n\nAnálise Gemini (gerada por IA; confira):\n' + comment
            except Exception:
                LOG.warning('Gemini indisponível; enviando análise local. Confira chave, modelo e cota.')
        try:
            await asyncio.to_thread(notifier.send, payload)
        except DeliveryError as exc:
            state = 'uncertain' if exc.uncertain else 'pending'
            store.finish_notification(item['id'], state)
            if exc.retry_after:
                store.set_meta('bot_retry_at', (utcnow() + timedelta(seconds=exc.retry_after)).isoformat())
            LOG.warning('Envio %s: %s', state, exc.reason)
            return
        except Exception:
            store.finish_notification(item['id'], 'uncertain')
            LOG.error('Envio sem confirmação; marcado incerto para evitar duplicação.')
            return
        else:
            store.finish_notification(item['id'], 'sent')
            LOG.info('Alerta enviado ao chat privado.')
