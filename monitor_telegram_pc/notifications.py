"""Private Telegram Bot API notifications. No tokens in errors or log records."""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal


@dataclass
class DeliveryError(Exception):
    reason: str
    retry_after: int | None = None
    uncertain: bool = False

    def __str__(self):
        return self.reason


def money(cents):
    if cents is None:
        return 'não informado'
    return ('R$ ' + f'{Decimal(cents) / 100:,.2f}').replace(',', 'X').replace('.', ',').replace('X', '.')


class BotNotifier:
    def __init__(self, token: str, chat_id: str):
        if not token or not chat_id.isdigit() or int(chat_id) <= 0:
            raise ValueError('Configure TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID (chat privado numérico positivo) no .env.')
        self._token, self._chat_id = token, chat_id
        self._verified = False

    def _request(self, method, data):
        request = urllib.request.Request(
            f'https://api.telegram.org/bot{self._token}/{method}',
            data=json.dumps(data).encode(), headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            # Never stringify HTTPError: its URL includes the bot token.
            if exc.code == 429:
                try:
                    delay = int(json.loads(exc.read()).get('parameters', {}).get('retry_after', 60))
                except (ValueError, TypeError):
                    delay = 60
                raise DeliveryError('Limite da Bot API; envio adiado.', max(1, delay)) from None
            raise DeliveryError(f'Bot API respondeu HTTP {exc.code}.', uncertain=exc.code >= 500) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
            raise DeliveryError('Falha de rede na Bot API; resultado do envio desconhecido.', uncertain=True) from None
        except (ValueError, UnicodeError):
            raise DeliveryError('Resposta inválida da Bot API; resultado desconhecido.', uncertain=True) from None
        if not result.get('ok'):
            code = result.get('error_code', 0)
            if code == 429:
                raise DeliveryError('Limite da Bot API.', int(result.get('parameters', {}).get('retry_after', 60)))
            raise DeliveryError('Bot API recusou a operação.', uncertain=code >= 500)
        return result['result']

    def verify_private(self):
        if not self._verified:
            chat = self._request('getChat', {'chat_id': self._chat_id})
            if chat.get('type') != 'private':
                raise DeliveryError('O destino deve ser um chat privado.')
            self._verified = True

    def send(self, text):
        self.verify_private()
        return self._request('sendMessage', {'chat_id': self._chat_id, 'text': text[:4096],
                                             'link_preview_options': {'is_disabled': True}})


def format_alert(offer, evaluation):
    budget = evaluation.get('orcamento', {})
    diff = evaluation.get('diferenca_centavos')
    percent = evaluation.get('diferenca_percentual')
    reasons = evaluation.get('justificativa', [])
    if isinstance(reasons, str):
        reasons = [reasons]
    specs = offer.get('especificacoes') or {}
    speed = specs.get('frequencia_mhz') if offer.get('categoria') == 'RAM' and isinstance(specs, dict) else None
    memory_note = []
    if type(speed) is int:
        preference = 'faixa preferida' if 5600 <= speed <= 6000 else 'fora da faixa preferida; compare preço e compatibilidade'
        memory_note = [f'RAM: {speed} MHz ({preference}; referência 5600–6000 MHz)']
    return '\n'.join([
        evaluation['classificacao'],
        offer.get('nome_completo') or offer['peca'],
        f"Modelo identificado: {offer['peca']}",
        *memory_note,
        f"Preço avaliado: {money(evaluation.get('preco_centavos') or offer.get('anunciado_centavos'))}",
        *([f"Total entregue estimado: {money(evaluation['total_entregue_centavos'])}"] if evaluation.get('total_entregue_centavos') is not None else []),
        f"PIX: {money(offer.get('pix_centavos'))} | Cartão total: {money(offer.get('cartao_centavos'))}",
        f"Forma avaliada: {evaluation.get('pagamento', 'indefinida')} | Parcelas: {offer.get('parcelas') or 'não informadas'}",
        f"Meta: {money(evaluation.get('meta_centavos'))}",
        f"Diferença: {money(diff)} ({percent:+.1f}%)" if percent is not None else 'Diferença: requer verificação',
        f"Loja: {offer.get('loja') or 'não identificada'} | Cupom: {offer.get('cupom') or 'não informado'}",
        f"PIX após compra: {money(budget.get('saldo_pix_apos_centavos'))}",
        f"Cartão livre após compra: {money(budget.get('limite_cartao_apos_centavos'))}",
        f"Montagem projetada: {money(budget.get('total_projetado_centavos'))}",
        f"Estimativa das peças restantes: PIX {money(budget.get('restante_pix_centavos'))} | Cartão {money(budget.get('restante_cartao_centavos'))}",
        'Motivo: ' + '; '.join(reasons),
        'Condições: ' + '; '.join(offer.get('condicoes', []) + offer.get('verificar', [])),
        offer.get('link_loja') or offer['link'],
        'Origem: ' + offer['link'],
        'Recomendação automática. Confira preço final, frete, compatibilidade e estoque. Compra somente por sua decisão.',
    ])[:4096]
