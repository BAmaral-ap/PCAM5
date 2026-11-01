#!/usr/bin/env python3
"""Monitor AM5: Telethon, CSV, histórico SQLite e alertas privados opcionais."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv
from telethon import TelegramClient

from finance import budget_snapshot, evaluate_offer, validate_purchase
from notifications import BotNotifier, DeliveryError, format_alert
# Legacy import names remain available to callers of the original script.
from parsing import (RULES, brl, brl_number, detect_product, extract_prices,
                     normalize_channel, parse_offers)
from runtime import LOG, collect_once, deliver_pending, setup_logging
from settings import HERE, load_config
from storage import Store


@contextmanager
def instance_lock(path):
    """OS lock released even after crashes; one process per SQLite database."""
    path = Path(str(path) + '.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('Já existe um processo usando este banco. Encerre-o antes de iniciar outro comando.') from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def notifier_from_env():
    return BotNotifier(os.getenv('TELEGRAM_BOT_TOKEN', '').strip(), os.getenv('TELEGRAM_CHAT_ID', '').strip())


def demo(cfg):
    offer = parse_offers('pcbuildwizard', 1, datetime.now(timezone.utc),
                         'Ryzen 7 7800X3D\nR$ 1.500,00 no PIX\nLoja: Demonstração\nhttps://example.com/oferta-ficticia', cfg)[0]
    return '[TESTE — OFERTA FICTÍCIA]\n' + format_alert(offer, evaluate_offer(offer, cfg, []))


def migrate_legacy(store):
    # Preserve source CSVs byte-for-byte. New exports use a different filename.
    for path in sorted((HERE / 'dados').glob('ofertas*.csv')):
        if path.name == 'ofertas_monitor.csv':
            continue
        store.import_csv(path)
    state = HERE / 'dados' / 'estado.json'
    if state.exists() and not store.get_meta('legacy_state_imported'):
        old = json.loads(state.read_text(encoding='utf-8'))
        for channel, cursor in old.items():
            if isinstance(cursor, int) and cursor >= 0:
                store.set_cursor(normalize_channel(channel), cursor)
        store.set_meta('legacy_state_imported', True)


async def async_main(args, cfg, store):
    api_id = os.getenv('TELEGRAM_API_ID', '').strip()
    api_hash = os.getenv('TELEGRAM_API_HASH', '').strip()
    if not api_id.isdigit() or not api_hash:
        raise ValueError('Configure TELEGRAM_API_ID e TELEGRAM_API_HASH somente no .env local.')
    notifier = None if args.no_notify or args.login else notifier_from_env()
    session = Path(os.getenv('TELEGRAM_SESSION', 'sessao_telegram'))
    if not session.is_absolute():
        session = HERE / session
    client = TelegramClient(str(session), int(api_id), api_hash, auto_reconnect=True,
                            connection_retries=5, request_retries=3, retry_delay=2,
                            flood_sleep_threshold=0)
    try:
        while True:
            try:
                await client.connect()
                break
            except (ConnectionError, OSError, asyncio.TimeoutError):
                if not args.watch:
                    raise
                LOG.warning('Conexão inicial indisponível; nova tentativa em 30 segundos.')
                await asyncio.sleep(30)
        if not await client.is_user_authorized():
            if args.login:
                await client.start()
            else:
                raise ValueError('Sessão ausente ou expirada. Execute python monitor.py --login no terminal local.')
        if args.login:
            print('Sessão autenticada disponível; nenhum canal foi consultado.')
            return
        migrated = store.recover_notifications()
        if migrated:
            LOG.warning('%s envios interrompidos marcados como incertos; consulte --notifications-status.', migrated)
        migrate_legacy(store)
        while True:
            try:
                if not client.is_connected():
                    await client.connect()
                await collect_once(client, store, cfg, args.limit, args.days, notifier is not None)
                if notifier:
                    await deliver_pending(store, notifier, cfg.get('idade_maxima_alerta_minutos', 30), cfg.get('gemini'))
                store.export_csv(HERE / 'dados' / 'ofertas_monitor.csv')
            except (ConnectionError, OSError, asyncio.TimeoutError):
                LOG.warning('Falha de conexão ou arquivo; estado persistido será reutilizado na próxima consulta.')
            if not args.watch:
                break
            interval = args.interval or cfg.get('intervalo_minutos', 2)
            LOG.info('Próxima consulta em %s minutos.', interval)
            await asyncio.sleep(interval * 60)
    finally:
        await client.disconnect()


def print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv=None):
    load_dotenv(HERE / '.env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=HERE / 'config.json')
    parser.add_argument('--db', type=Path, default=HERE / 'dados' / 'monitor.sqlite3')
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--limit', type=int, default=150, help='Tamanho da página de mensagens (1 a 500)')
    parser.add_argument('--days', type=int, default=7, help='Janela do histórico inicial; sem alertas retroativos')
    parser.add_argument('--interval', type=float, help='Minutos entre consultas; padrão config: 2')
    parser.add_argument('--no-notify', action='store_true', help='Coleta sem envio ou fila de alertas')
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--login', action='store_true', help='Autenticação interativa somente se necessária')
    actions.add_argument('--dry-run', action='store_true', help='Exibir alerta fictício offline')
    actions.add_argument('--test-alert', action='store_true', help='Enviar um alerta fictício ao chat privado')
    actions.add_argument('--test-gemini', action='store_true', help='Testar Gemini com oferta fictícia, sem enviar ao Telegram')
    actions.add_argument('--bot-chat-id', action='store_true', help='Mostrar IDs privados após /start no bot, sem exibir tokens')
    actions.add_argument('--budget', action='store_true')
    actions.add_argument('--history', metavar='MODELO')
    actions.add_argument('--offers', action='store_true', help='Análise das 20 ofertas recentes, incluindo comparação de perfis')
    parser.add_argument('--history-days', type=int)
    actions.add_argument('--import-csv', type=Path)
    actions.add_argument('--export-csv', type=Path)
    actions.add_argument('--reprocess', action='store_true')
    actions.add_argument('--purchase', type=Path, help='JSON com compra já realizada; exige --confirmar')
    actions.add_argument('--pay-installment', type=int, metavar='ID', help='Registrar parcela já paga; exige --confirmar')
    actions.add_argument('--notifications-status', action='store_true')
    parser.add_argument('--confirmar', action='store_true', help='Confirma manualmente registro financeiro local')
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 500 or args.days < 1 or (args.interval is not None and args.interval < 1):
        parser.error('Use --limit entre 1 e 500; --days >= 1; --interval >= 1.')
    if args.history_days is not None and args.history_days < 1:
        parser.error('--history-days deve ser positivo.')
    if (args.purchase or args.pay_installment is not None) and not args.confirmar:
        parser.error('Registro financeiro requer confirmação explícita: --confirmar. Nenhuma compra foi registrada.')
    try:
        cfg = load_config(args.config)
        if args.dry_run:
            print(demo(cfg))
            return
        if args.test_gemini:
            from gemini import GeminiError, explain
            key = os.getenv('GEMINI_API_KEY', '').strip()
            if not key:
                raise ValueError('Configure GEMINI_API_KEY no .env local.')
            try:
                print('Análise Gemini de oferta fictícia:')
                print(explain(demo(cfg), key, cfg.get('gemini', {}).get('modelo', 'gemini-2.5-flash-lite')))
            except GeminiError as exc:
                print(f'Gemini indisponível: {exc}', file=sys.stderr)
                return 1
            except Exception:
                print('Gemini indisponível: confira conexão, chave, modelo e cota.', file=sys.stderr)
                return 1
            return 0
        if args.bot_chat_id:
            token = os.getenv('TELEGRAM_BOT_TOKEN', '')
            if not token:
                raise ValueError('Configure TELEGRAM_BOT_TOKEN no .env local.')
            bot = BotNotifier(token, '1')
            updates = bot._request('getUpdates', {'timeout': 0, 'allowed_updates': ['message']})
            ids = {str(u['message']['chat']['id']) for u in updates if u.get('message', {}).get('chat', {}).get('type') == 'private'}
            print('IDs de chats privados: ' + (', '.join(sorted(ids)) or 'nenhum; envie /start ao bot e repita.'))
            return
        if args.test_alert:
            notifier_from_env().send(demo(cfg))
            print('Alerta de teste enviado ao chat privado.')
            return
        with instance_lock(args.db), Store(args.db) as store:
            if args.purchase:
                item = validate_purchase(json.loads(args.purchase.read_text(encoding='utf-8-sig')))
                if item['categoria'] not in cfg['montagem']:
                    raise ValueError('Categoria da compra não existe em montagem; confira config.json.')
                # Repeated execution of the same input must not spend the budget twice.
                identity = lambda p: {k: v for k, v in p.items() if k not in ('id', 'parcelas_pagas')}
                if any(identity(p) == identity(item) for p in store.purchases()):
                    raise ValueError('Compra idêntica já registrada. Confira --budget antes de repetir.')
                print_json({'compra_registrada': store.add_purchase(item), 'registro': item})
            elif args.pay_installment is not None:
                store.record_installment(args.pay_installment)
                print('Parcela marcada como paga por confirmação manual.')
            elif args.budget:
                print_json({'orcamento': budget_snapshot(cfg, store.purchases()), 'compras': store.purchases()})
            elif args.history:
                migrate_legacy(store)
                print_json(store.history(args.history, args.history_days))
            elif args.offers:
                recent = sorted(store.offers(), key=lambda o: o['data'], reverse=True)[:20]
                print_json([{'oferta': o, 'analise': evaluate_offer(o, cfg, store.purchases())} for o in recent])
            elif args.import_csv:
                print_json(store.import_csv(args.import_csv))
            elif args.reprocess:
                paths = sorted((HERE / 'dados').glob('ofertas*.csv'))
                print_json({p.name: store.import_csv(p, reprocess=True) for p in paths if p.name != 'ofertas_monitor.csv'})
            elif args.export_csv:
                migrate_legacy(store)
                store.export_csv(args.export_csv)
                print('CSV exportado; arquivos de origem preservados.')
            elif args.notifications_status:
                print_json({'contagens': dict(Counter(n['state'] for n in store.notifications())),
                            'envios': [{'id': n['id'], 'estado': n['state'], 'data': n['source_date']} for n in store.notifications()]})
            else:
                setup_logging(HERE / 'logs')
                asyncio.run(async_main(args, cfg, store))
    except KeyboardInterrupt:
        print('Monitor encerrado; estado salvo.')
    except (ValueError, DeliveryError) as exc:
        # All ValueErrors emitted by our validation contain no credentials.
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        # Telegram/HTTP exception strings can expose request details: type only.
        print(f'Falha {type(exc).__name__}. Confira configuração e conectividade; credenciais não foram exibidas.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
