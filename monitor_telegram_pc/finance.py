"""Orçamento local: dinheiro, crédito e compromissos permanecem separados.

Todos os cálculos usam centavos. As parcelas são baixadas apenas por confirmação
manual; a passagem do tempo não paga faturas nem libera limite automaticamente.
O caixa PIX é a reserva para peças, não a renda futura usada para pagar o cartão.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date
import re
from typing import Any


DEFAULT_BUILD = {
    "CPU": {"modelo": "Ryzen 7 9800X3D", "estimativa_pix": None, "pagamento": "cartao"},
    "GPU": {"modelo": "RX 9070 XT 16GB", "estimativa_pix": 4700, "pagamento": "pix"},
    "PLACA-MAE": {"modelo": "ASUS B650M-AYW", "estimativa_pix": 680, "pagamento": "pix"},
    # Preço no cartão desconhecido: não reutilizar a meta PIX como promessa.
    "RAM": {"modelo": "DDR5 16GB", "estimativa_pix": 1500,
            "estimativa_cartao": None, "pagamento": "indefinido", "fonte_pagamento": "a_definir",
            "parcelamento": "opcional"},
    "FONTE": {"modelo": "XPG Core Reactor II VE 850W", "estimativa_pix": 490, "pagamento": "pix"},
    "COOLER-CPU": {"modelo": "Cooler AM5", "estimativa_pix": 200, "pagamento": "pix"},
    "GABINETE": {"modelo": "Gabinete", "estimativa_pix": 250, "pagamento": "pix"},
    "SSD": {"modelo": "SSD NVMe", "estimativa_pix": 700, "pagamento": "pix", "possui": False},
}


def cents(value: Any) -> int:
    """Converter reais em centavos sem aritmética binária ou arredondamento oculto."""
    if isinstance(value, bool):
        raise ValueError("Valor monetário inválido.")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("Valor monetário inválido.") from None
    if not number.is_finite() or number < 0:
        raise ValueError("Valor monetário deve ser finito e não negativo.")
    return int((number * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _whole(value: Any, name: str, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} inválido.")
    try:
        parsed = int(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f"{name} inválido.") from None
    if parsed < minimum or str(parsed) != str(value):
        raise ValueError(f"{name} inválido.")
    return parsed


def installments(total_centavos: int, count: int) -> list[int]:
    """Distribuir centavos restantes nas primeiras parcelas, sem perder dinheiro."""
    total = _whole(total_centavos, "Total", 1)
    count = _whole(count, "Número de parcelas", 1)
    if count > 120 or count > total:
        raise ValueError("Número de parcelas inválido.")
    base, remainder = divmod(total, count)
    return [base + (i < remainder) for i in range(count)]


def _month(value: str, offset: int) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", value):
        raise ValueError("Primeira parcela deve usar AAAA-MM.")
    year, month = map(int, value.split("-"))
    if year < 1 or year > 9999:
        raise ValueError("Ano da primeira parcela inválido.")
    serial = year * 12 + month - 1 + offset
    return f"{serial // 12:04d}-{serial % 12 + 1:02d}"


def validate_purchase(purchase: dict[str, Any]) -> dict[str, Any]:
    """Validar um registro manual antes de gravá-lo (não realiza pagamentos)."""
    item = dict(purchase)
    item["categoria"] = str(item.get("categoria", item.get("category", ""))).strip()
    item["modelo"] = str(item.get("modelo", "")).strip()
    if not item["categoria"] or not item["modelo"]:
        raise ValueError("Informe categoria e modelo da compra.")
    item["total_centavos"] = _whole(item.get("total_centavos"), "Total", 1)
    if item.get("pagamento") not in {"pix", "avista", "cartao"}:
        raise ValueError("Pagamento deve ser pix, avista ou cartao.")
    item["parcelas"] = _whole(item.get("parcelas", 1), "Número de parcelas", 1)
    item["parcelas_pagas"] = _whole(item.get("parcelas_pagas", 0), "Parcelas pagas")
    installments(item["total_centavos"], item["parcelas"])
    if item["parcelas_pagas"] > item["parcelas"]:
        raise ValueError("Parcelas pagas excedem o número contratado.")
    if item["pagamento"] in {"pix", "avista"}:
        if item["parcelas"] != 1 or item["parcelas_pagas"]:
            raise ValueError("Compra à vista não tem parcelas de cartão.")
    else:
        _month(item.get("primeira_parcela", ""), 0)
    return item


def budget_snapshot(config: dict, purchases: list[dict], candidate: dict | None = None,
                    payment: str = "pix") -> dict:
    """Projetar a montagem. ``None`` em conclusao_viavel indica custo desconhecido.

    A estimativa de cartão deve ser fornecida separadamente no config; mesmo
    quando conhecida, continua sendo estimativa, não preço confirmado da loja.
    """
    if payment not in {"pix", "cartao"}:
        raise ValueError("Pagamento deve ser pix ou cartao.")
    limits = config.get("orcamento", {})
    cash_raw = limits.get("pix")
    cash = None if cash_raw is None else cents(cash_raw)
    credit_raw = limits.get("cartao")
    credit = None if credit_raw is None else cents(credit_raw)
    build = config.get("montagem", DEFAULT_BUILD)
    # Total project ceiling, not a spending target. Paying a credit-card bill
    # restores the revolving limit but never increases this assembly budget.
    total_cap = cents(limits["total_maximo"]) if limits.get("total_maximo") is not None else None
    bought = [validate_purchase(p) for p in purchases]
    spent_pix = sum(p["total_centavos"] for p in bought if p["pagamento"] == "pix")
    spent_other_cash = sum(p["total_centavos"] for p in bought if p["pagamento"] == "avista")
    spent = spent_pix + spent_other_cash
    all_paid = sum(p["total_centavos"] for p in bought)
    outstanding = 0
    paid_card = 0
    card_amounts_known = True
    payment_warnings: list[str] = []
    schedule: dict[str, int] = {}
    ram_schedule: dict[str, int] = {}
    due_dates: dict[str, int] = {}
    for p in bought:
        if p["pagamento"] != "cartao":
            continue
        pieces = installments(p["total_centavos"], p["parcelas"])
        paid_count = p['parcelas_pagas']
        actual = p.get('valores_pagos_centavos')
        if p.get('rateio_estimado') and paid_count:
            if (not isinstance(actual, list) or len(actual) != paid_count
                    or any(type(value) is not int or value <= 0 for value in actual)
                    or sum(actual) > p['total_centavos']):
                card_amounts_known = False
                payment_warnings.append(f"Valores das parcelas pagas de {p['modelo']} não informados; saldo do cartão é desconhecido.")
                continue
            paid_amount = sum(actual)
            unpaid = p['total_centavos'] - paid_amount
            if (paid_count == p['parcelas'] and unpaid != 0) or (paid_count < p['parcelas'] and unpaid < p['parcelas'] - paid_count):
                raise ValueError('Valores das parcelas pagas não correspondem ao total contratado.')
            remaining_pieces = installments(unpaid, p['parcelas'] - paid_count) if unpaid else []
        else:
            paid_amount = sum(pieces[:paid_count])
            unpaid = sum(pieces[paid_count:])
            remaining_pieces = pieces[paid_count:]
        paid_card += paid_amount
        outstanding += unpaid
        for offset, amount in enumerate(remaining_pieces):
            index = paid_count + offset
            month = _month(p["primeira_parcela"], index)
            schedule[month] = schedule.get(month, 0) + amount
            if p.get('dia_vencimento') is not None:
                day = _whole(p['dia_vencimento'], 'Dia de vencimento', 1)
                try:
                    due = date(int(month[:4]), int(month[5:]), day).isoformat()
                except ValueError:
                    raise ValueError('Dia de vencimento inválido para uma das parcelas.') from None
                due_dates[due] = due_dates.get(due, 0) + amount
            if p['categoria'] == 'RAM':
                ram_schedule[month] = ram_schedule.get(month, 0) + amount

    acquired = {p["categoria"] for p in bought if not p.get("monitorar_novamente", False)}
    already_owned = {cat for cat, spec in build.items() if spec.get("possui", False)}
    acquired |= already_owned
    pending: list[str] = list(payment_warnings)
    remaining: list[dict] = []
    remainder = {"pix": 0, "cartao": 0, "indefinido": 0}
    known = {"pix": True, "cartao": True, "indefinido": True}
    category = candidate.get("categoria") if candidate else None
    if category == 'RAM' and payment != 'cartao' and build.get('RAM', {}).get('parcelamento') == 'cartao_sem_juros':
        raise ValueError('A RAM está configurada para cartão parcelado sem juros, separado da reserva à vista.')
    candidate_price = None
    candidate_redundant = bool(candidate and category in acquired)
    if candidate:
        candidate_price = candidate.get(f"{payment}_centavos")
        if candidate_price is not None:
            candidate_price = _whole(candidate_price, "Preço da oferta", 1)
        else:
            pending.append(f"Preço final {payment} da oferta não informado.")
        if candidate_redundant:
            pending.append("Componente já adquirido; compra adicional não é necessária para a montagem.")
        if category not in build:
            pending.append("Categoria da oferta não está prevista na montagem.")

    # A candidata custa dinheiro inclusive quando é extra; só substitui a
    # estimativa de uma categoria que ainda falta adquirir.
    extra_pix = candidate_price if candidate and payment == "pix" and candidate_price is not None else 0
    extra_card = candidate_price if candidate and payment == "cartao" and candidate_price is not None else 0
    for cat, spec in build.items():
        if cat in acquired:
            continue
        if candidate and cat == category:
            if candidate_price is None:
                known[payment] = False
            remaining.append({"categoria": cat, "modelo": candidate.get("peca", ""),
                              "pagamento": payment, "estimativa_centavos": candidate_price,
                              "oferta_candidata": True})
            continue
        method = spec.get("pagamento", "pix")
        if method not in {"pix", "cartao", "indefinido"}:
            raise ValueError(f"Pagamento inválido na montagem: {cat}.")
        if method == 'indefinido':
            estimate = spec.get('estimativa_total')
            pix_est, card_est = spec.get('estimativa_pix'), spec.get('estimativa_cartao')
            if estimate is None and pix_est is not None and card_est is not None and cents(pix_est) == cents(card_est):
                estimate = pix_est
        else:
            estimate = spec.get(f"estimativa_{method}")
        value = cents(estimate) if estimate is not None else None
        if value is None:
            known[method] = False
            pending.append(f"Falta estimativa {method} de {cat}.")
        else:
            remainder[method] += value
        remaining.append({"categoria": cat, "modelo": spec.get("modelo", cat),
                          "pagamento": method, "estimativa_centavos": value,
                          "oferta_candidata": False})

    cash_after = None if cash is None else cash - spent - extra_pix
    credit_after = None if credit is None or not card_amounts_known else credit - outstanding - extra_card
    unallocated = any(item['pagamento'] == 'indefinido' for item in remaining)
    unknown = cash is None or credit is None or not card_amounts_known or total_cap is None or not all(known.values()) or unallocated or bool(candidate and candidate_price is None)
    finance_items = limits.get('financiamentos', [])
    if not isinstance(finance_items, list):
        raise ValueError('Financiamentos precisam estar em uma lista.')
    finance_cost, finance_paid = 0, 0
    for item in finance_items:
        if not isinstance(item, dict):
            raise ValueError('Financiamento inválido.')
        if item.get('status') == 'contratado' and item.get('confirmado') is True:
            principal = _whole(item.get('valor_centavos'), 'Valor do financiamento', 1)
            total = _whole(item.get('total_centavos'), 'Total do financiamento', 1)
            charges = _whole(item.get('encargos_centavos'), 'Encargos do financiamento')
            if total - principal != charges:
                raise ValueError('Encargos do financiamento não correspondem ao valor contratado.')
            finance_cost += charges
            paid = item.get('encargos_pagos_centavos')
            if paid is None:
                finance_paid = None
            elif finance_paid is not None:
                paid = _whole(paid, 'Encargos pagos')
                if paid > charges:
                    raise ValueError('Encargos pagos excedem o total contratado.')
                finance_paid += paid
    lower_bound = all_paid + finance_cost + (candidate_price or 0) + sum(remainder.values())
    limits_exceeded = (cash_after is not None and cash_after < remainder["pix"]) or (credit_after is not None and credit_after < remainder["cartao"])
    cap_exceeded = total_cap is not None and lower_bound > total_cap
    impossible = limits_exceeded or cap_exceeded
    viable = False if impossible or candidate_redundant else (None if unknown else True)
    if limits_exceeded:
        pending.append("A reserva PIX ou o limite de cartão não cobrem a montagem projetada.")
    if cap_exceeded:
        pending.append("Projeção da montagem, incluindo estimativas, excede o teto total configurado; pagar parcelas não aumenta esse teto.")
    if cash is None:
        pending.append("Saldo disponível no PIX não informado; previsão de receitas não é dinheiro disponível.")
    if credit is None:
        pending.append('Limite do cartão não informado.')
    if unallocated:
        pending.append('Forma de pagamento de peça pendente não definida; não foi debitada automaticamente do PIX ou cartão.')
    if build.get('RAM', {}).get('fonte_pagamento') == 'renda_pessoal_separada':
        pending.append('Parcelas da RAM usam renda pessoal separada; saldo dessa renda não foi informado.')
    if candidate and category not in build:
        viable = False
    projected = None if not all(known.values()) or (candidate and candidate_price is None) else lower_bound
    return {
        "pix_inicial_centavos": cash, "cartao_inicial_centavos": credit,
        "teto_total_centavos": total_cap,
        "saldo_teto_centavos": None if total_cap is None else total_cap - all_paid - finance_cost,
        "saldo_previsto_restante_centavos": None if limits.get('previsto_total') is None else cents(limits['previsto_total']) - all_paid - finance_cost,
        "reserva_avista_prevista_centavos": None if limits.get('previsto_avista') is None else cents(limits['previsto_avista']) - spent,
        "fonte_pagamento_ram": build.get('RAM', {}).get('fonte_pagamento'),
        "entradas_pendentes": limits.get('entradas_pendentes', []),
        "entradas_confirmadas": limits.get('entradas_confirmadas', []),
        "financiamentos": finance_items, "custos_financeiros_contratados_centavos": finance_cost,
        "saldo_teto_apos_centavos": None if total_cap is None or (candidate and candidate_price is None) else total_cap - all_paid - finance_cost - (candidate_price or 0),
        "folga_teto_centavos": None if total_cap is None or not all(known.values()) or (candidate and candidate_price is None) else total_cap - lower_bound,
        "total_minimo_projetado_centavos": lower_bound,
        "gasto_pix_centavos": spent_pix, "gasto_avista_sem_meio_centavos": spent_other_cash,
        "total_pago_avista_centavos": spent, "cartao_comprometido_centavos": outstanding if card_amounts_known else None,
        "cartao_pago_centavos": paid_card if card_amounts_known else None,
        "despesas_pagas_centavos": None if finance_paid is None or not card_amounts_known else spent + paid_card + finance_paid,
        "despesas_contratadas_centavos": all_paid + finance_cost, "total_comprado_centavos": all_paid,
        "total_comprometido_centavos": all_paid + finance_cost,
        "saldo_pix_centavos": None if cash is None or spent_other_cash else cash - spent,
        "saldo_disponivel_avista_centavos": None if cash is None else cash - spent,
        "limite_cartao_centavos": None if credit is None or not card_amounts_known else credit - outstanding,
        "saldo_pix_apos_centavos": None if spent_other_cash or (candidate and payment == "pix" and candidate_price is None) else cash_after,
        "limite_cartao_apos_centavos": None if candidate and payment == "cartao" and candidate_price is None else credit_after,
        "restante_pix_centavos": remainder["pix"] if known["pix"] else None,
        "restante_cartao_centavos": remainder["cartao"] if known["cartao"] else None,
        "restante_pagamento_indefinido_centavos": remainder['indefinido'] if known['indefinido'] else None,
        "total_projetado_centavos": projected,
        "projecao_total_avista_centavos": None if unallocated or not known['pix'] or (candidate and payment == 'pix' and candidate_price is None) else spent + extra_pix + remainder['pix'],
        "projecao_total_cartao_centavos": None if unallocated or not known['cartao'] or (candidate and payment == 'cartao' and candidate_price is None) else sum(p['total_centavos'] for p in bought if p['pagamento'] == 'cartao') + extra_card + remainder['cartao'],
        "folga_pix_centavos": cash_after - remainder["pix"] if not unallocated and not spent_other_cash and cash_after is not None and known["pix"] and not (candidate and payment == "pix" and candidate_price is None) else None,
        "folga_cartao_centavos": credit_after - remainder["cartao"] if not unallocated and credit_after is not None and known["cartao"] and not (candidate and payment == "cartao" and candidate_price is None) else None,
        "compromissos_mensais": dict(sorted(schedule.items())), "parcelas_ram_mensais": dict(sorted(ram_schedule.items())),
        "compromissos_por_vencimento": dict(sorted(due_dates.items())),
        "valores_parcelas_estimados": any(p.get('rateio_estimado') and p['pagamento'] == 'cartao' and p['parcelas_pagas'] < p['parcelas'] for p in bought),
        "compras": bought, "faltantes": remaining, "ja_possui": sorted(acquired),
        "pendencias": pending, "conclusao_viavel": viable,
        "estimativas": True,
    }


def _compatibility(offer: dict, config: dict, purchases: list[dict] | None = None) -> tuple[bool | None, list[str]]:
    """Compare only supplied specifications; missing data requires manual checks.

    The purchased model supersedes a planned model. Manual confirmation does not
    override a contradiction in the supplied dimensions/platform/power data.
    """
    model = offer.get("peca", "")
    profiles = config.get("perfis", {})
    profile = profiles.get(model, {})
    category = offer.get("categoria")
    checks = config.get("compatibilidade", {}).get("confirmacoes", {})
    warnings: list[str] = []
    conflicts: list[str] = []
    selected = {cat: spec.get("modelo", "") for cat, spec in config.get("montagem", DEFAULT_BUILD).items()}
    for purchase in purchases or []:
        if not purchase.get('monitorar_novamente', False):
            selected[purchase.get("categoria", purchase.get("category"))] = purchase.get("modelo", "")
    selected[category] = model
    parts = {cat: profiles.get(name, {}) for cat, name in selected.items()}
    cpu, board, ram = (parts.get(cat, {}) for cat in ("CPU", "PLACA-MAE", "RAM"))
    gpu, psu, cooler, case = (parts.get(cat, {}) for cat in ("GPU", "FONTE", "COOLER-CPU", "GABINETE"))
    if category == 'GPU' and isinstance(offer.get('especificacoes'), dict):
        # Explicit product dimensions and requirements in this announcement
        # take precedence over a generic GPU family profile.
        gpu = dict(gpu, **offer['especificacoes'])

    def label(value):
        return str(value).strip().upper() if value is not None else None

    def numeric(spec, field):
        value = spec.get(field)
        if value is None:
            return None
        try:
            number = Decimal(str(value))
            if number.is_finite() and number > 0:
                return number
        except InvalidOperation:
            pass
        warnings.append(f"Especificação {field} inválida; confirme o perfil do fabricante.")
        return None

    if profile.get("compativel") is False:
        conflicts.append("Modelo marcado como incompatível na configuração.")
    for part, name in ((cpu, "Processador"), (board, "Placa-mãe")):
        if label(part.get("socket")) not in {None, "AM5"}:
            conflicts.append(f"{name} de socket diferente de AM5 na montagem selecionada.")
    for part, name in ((ram, "Memória"), (board, "Placa-mãe")):
        memory = label(part.get("tipo_memoria", part.get("memoria")))
        if memory not in {None, "DDR5"}:
            conflicts.append(f"{name} incompatível com a plataforma DDR5.")
    if cpu.get("socket") and board.get("socket") and label(cpu["socket"]) != label(board["socket"]):
        conflicts.append("Socket do processador difere do socket da placa-mãe selecionada.")
    cooler_sockets = cooler.get("sockets")
    if isinstance(cooler_sockets, list) and "AM5" not in {label(s) for s in cooler_sockets}:
        conflicts.append("Cooler não declara suporte ao socket AM5.")
    formats = case.get("formatos_placa_mae")
    if board.get("formato") and isinstance(formats, list) and label(board["formato"]) not in {label(f) for f in formats}:
        conflicts.append("Formato da placa-mãe não é suportado pelo gabinete selecionado.")
    if case.get('formato_fonte') and psu.get('formato') and label(case['formato_fonte']) != label(psu['formato']):
        conflicts.append('Formato da fonte não é suportado pelo gabinete selecionado.')
    if category == 'PLACA-MAE' and re.search(r'9800\s*x3d', selected.get('CPU', ''), re.I):
        supported = board.get('suporte_9800x3d')
        if supported is False:
            conflicts.append('Placa-mãe marcada como sem suporte ao Ryzen 7 9800X3D.')
        elif supported is not True or not board.get('fonte_suporte_9800x3d'):
            warnings.append('Confirme o suporte oficial ao Ryzen 7 9800X3D na lista do fabricante.')
        minimum = board.get('bios_minima_9800x3d')
        installed = board.get('bios_instalada')
        if not minimum:
            warnings.append('Versão mínima de BIOS para o Ryzen 7 9800X3D não informada; consulte o fabricante.')
        if not installed:
            warnings.append('Versão de BIOS instalada não informada; confirme antes da compra.')
        elif board.get('bios_9800x3d_confirmada') is not True:
            warnings.append('Confirme que a BIOS instalada suporta o Ryzen 7 9800X3D.')
        if board.get('vrm_adequado_9800x3d') is False:
            conflicts.append('VRM da placa-mãe marcado como inadequado ao Ryzen 7 9800X3D.')
        elif board.get('vrm_adequado_9800x3d') is not True:
            warnings.append('Confirme a adequação do VRM ao Ryzen 7 9800X3D.')
        nvme = board.get('slots_m2_nvme')
        if nvme == 0 or nvme is False:
            conflicts.append('Placa-mãe sem slot M.2 NVMe informado.')
        elif type(nvme) is not int or nvme < 1:
            warnings.append('Confirme a existência de pelo menos um slot M.2 NVMe.')
        if not board.get('tipo_memoria', board.get('memoria')):
            warnings.append('Confirme suporte a DDR5 na placa-mãe.')
        if not board.get('formato'):
            warnings.append('Confirme o formato da placa-mãe para o gabinete Montech XR.')
    for part, field, container, limit, message in (
        (gpu, "comprimento_mm", case, "gpu_max_mm", "Comprimento da GPU excede o espaço informado do gabinete."),
        (cooler, "altura_mm", case, "cooler_max_mm", "Altura do cooler excede o espaço informado do gabinete."),
        (gpu, "fonte_minima_w", psu, "potencia_w", "Fonte abaixo da potência mínima recomendada para a GPU."),
    ):
        size, maximum = numeric(part, field), numeric(container, limit)
        if size is not None and maximum is not None and size > maximum:
            conflicts.append(message)
    if conflicts:
        return False, list(dict.fromkeys(conflicts))
    if not offer.get("modelo_exato", False):
        warnings.append("Modelo/variante exata precisa ser verificado.")
    if profile.get("compativel") is not True:
        warnings.append("Compatibilidade deste modelo ainda não cadastrada.")
    if checks.get(model) is not True:
        warnings.append("Confirmar BIOS, dimensões, conectores e compatibilidade da montagem nas especificações do fabricante.")
    if category == 'GPU' and case.get('gpu_max_mm') and not gpu.get('comprimento_mm'):
        warnings.append(f"Comprimento da GPU não informado; confirmar limite de {case['gpu_max_mm']} mm do gabinete.")
    if category == 'GPU' and psu.get('conectores'):
        required = gpu.get('conectores_requeridos')
        if not isinstance(required, dict) or not required:
            warnings.append('Conectores exigidos pela GPU não informados; confirmar com a fonte adquirida.')
        else:
            for kind, amount in required.items():
                available = psu['conectores'].get(kind)
                if available is False or (type(available) is int and type(amount) is int and available < amount):
                    conflicts.append('A fonte não dispõe dos conectores exigidos pela GPU.')
                elif available is None or (available is True and type(amount) is int and amount > 1):
                    warnings.append('Quantidade de conectores PCIe da fonte precisa ser confirmada.')
    if conflicts:
        return False, list(dict.fromkeys(conflicts))
    return (None if warnings else True), warnings


def _comparison(offer: dict, config: dict, price: int | None) -> dict:
    category = offer.get("categoria")
    reference = config.get("montagem", DEFAULT_BUILD).get(category, {}).get("modelo")
    profiles = config.get("perfis", {})
    model = offer.get("peca", "")
    result: dict[str, Any] = {"modelo_referencia": reference, "modelo_oferta": model}
    candidate_profile = profiles.get(model, {})
    baseline = profiles.get(reference, {})
    if not candidate_profile.get("verificado") or not baseline.get("verificado"):
        result["observacao"] = "Comparação de consumo/desempenho/longevidade requer perfis verificados e fontes; nenhuma vantagem foi presumida."
        return result
    allowed = ("consumo_w", "desempenho_indice", "benchmark", "longevidade", "garantia_anos", "fonte")
    result["oferta"] = {key: candidate_profile[key] for key in allowed if key in candidate_profile}
    result["referencia"] = {key: baseline[key] for key in allowed if key in baseline}
    score = candidate_profile.get("desempenho_indice")
    if score is not None and baseline.get("benchmark") == candidate_profile.get("benchmark") and candidate_profile.get("benchmark") and price:
        try:
            score_d = Decimal(str(score))
            if score_d.is_finite() and score_d > 0:
                result["reais_por_ponto"] = round(float(Decimal(price) / 100 / score_d), 2)
        except InvalidOperation:
            pass
    return result


def evaluate_offer(offer: dict, config: dict, purchases: list[dict]) -> dict:
    """Recomendação conservadora; não valida estoque, cupom ou checkout."""
    model = offer.get("peca", "")
    category = offer.get("categoria")
    specs = offer.get('especificacoes') or {}
    kit = category == 'RAM' and (model == 'DDR5 2x16GB' or
        isinstance(specs, dict) and specs.get('modulos') == 2 and specs.get('capacidade_por_modulo_gb') == 16)
    build = config.get("montagem", DEFAULT_BUILD)
    ram_strict = category == 'RAM' and build.get('RAM', {}).get('parcelamento') == 'cartao_sem_juros'
    preferred = ('cartao' if ram_strict else 'pix') if category == 'RAM' else build.get(category, {}).get("pagamento", "pix")
    if preferred not in {"pix", "cartao"}:
        raise ValueError("Forma de pagamento inválida na montagem.")
    target_raw = config.get("metas", {}).get(model, offer.get("meta"))
    if not offer.get('modelo_exato', False) and category in {'PLACA-MAE', 'RAM', 'FONTE', 'COOLER-CPU', 'GABINETE', 'SSD'}:
        reference = build.get(category, {}).get('modelo')
        if category == 'SSD' or kit:
            default = 2400 if category == 'RAM' else config.get('metas', {}).get(reference, target_raw)
            target_raw = config.get('metas', {}).get(model, default)
        else:
            target_raw = config.get('metas', {}).get(reference, target_raw)
    target = cents(target_raw) if target_raw is not None else None
    if kit:
        maximum = cents(config.get('metas', {}).get('DDR5 2x16GB', 2400))
        target = min(target, maximum) if target is not None else maximum
    if category == 'SSD' and config.get('metas', {}).get('SSD NVMe') is not None:
        maximum = cents(config['metas']['SSD NVMe'])
        target = min(target, maximum) if target is not None else maximum
    if target == 0:
        target = None
    settings = config.get("alertas", {})
    exceptional = Decimal(str(settings.get("excepcional_percentual", 10)))
    good = Decimal(str(settings.get("boa_percentual", 0)))
    if not exceptional.is_finite() or not good.is_finite() or not 0 <= exceptional <= 100 or not 0 <= good <= 100:
        raise ValueError("Percentuais de alerta devem estar entre 0 e 100.")
    if good > exceptional:
        raise ValueError("Desconto de boa promoção não pode exceder o excepcional.")
    # The current build allows RAM à vista; older configurations may retain
    # the user's earlier card-only policy until their migration is applied.
    payment = ('cartao' if ram_strict else 'pix') if category == 'RAM' else ('pix' if category == 'SSD' else preferred)
    if category == 'RAM' and not ram_strict and offer.get('pix_centavos') is None:
        if offer.get('total_parcelado_sem_juros_centavos') is not None or offer.get('cartao_centavos') is not None:
            payment = 'cartao'
    if category not in {'RAM', 'SSD'} and offer.get(f"{payment}_centavos") is None:
        other = "pix" if payment == "cartao" else "cartao"
        if offer.get(f"{other}_centavos") is not None:
            payment = other
    if category == 'RAM' and ram_strict:
        price = offer.get('total_parcelado_sem_juros_centavos')
    elif category == 'RAM' and payment == 'cartao':
        price = offer.get('total_parcelado_sem_juros_centavos') or offer.get('cartao_centavos')
    else:
        price = offer.get(f"{payment}_centavos")
    if price is not None:
        price = _whole(price, "Preço da oferta", 1)
    freight = offer.get('frete_centavos') if (model == 'Ryzen 7 7800X3D' and config.get('meta_7800x3d_inclui_frete')) or category == 'SSD' else None
    if freight is not None:
        freight = _whole(freight, 'Frete')
    assessed = price + freight if price is not None and freight is not None else price
    budget_offer = dict(offer)
    if category == 'RAM':
        budget_offer['cartao_centavos'] = None
    if assessed is not None:
        budget_offer[f'{payment}_centavos'] = assessed
    budget = budget_snapshot(config, purchases, budget_offer, payment)
    compatible, compatibility_notes = _compatibility(offer, config, purchases)
    reason: list[str] = []
    verify = list(offer.get("verificar") or [])
    delta = assessed - target if assessed is not None and target is not None else None
    percent = round(float(Decimal(delta) * 100 / target), 2) if delta is not None else None
    classification = "VERIFICAR"
    if category not in build or category in budget["ja_possui"]:
        classification = "NÃO COMPENSA"
        reason.append("Componente fora das necessidades atuais da montagem.")
    elif compatible is False:
        classification = "NÃO COMPENSA"
        reason.extend(compatibility_notes)
    elif budget["conclusao_viavel"] is False:
        classification = "NÃO COMPENSA"
        reason.append("Projeção excede limites configurados; confira estimativas, limite de cartão e recursos disponíveis.")
    elif price is None:
        reason.append("Preço final por forma de pagamento não identificado; confirmar anúncio.")
    elif target is None:
        reason.append("Meta deste modelo ainda não cadastrada.")
    elif Decimal(assessed) * 100 > Decimal(target) * (100 - good):
        classification = "NÃO COMPENSA"
        reason.append("Preço não atende ao desconto configurado para este modelo.")
    elif verify:
        reason.append("Extração ou condições da oferta precisam de verificação.")
    elif Decimal(assessed) * 100 <= Decimal(target) * (100 - exceptional) and compatible is True and budget["conclusao_viavel"] is True:
        classification = "COMPRAR AGORA"
        reason.append("Desconto excepcional frente à meta, compatibilidade confirmada e montagem estimada dentro dos limites.")
    else:
        classification = "BOA PROMOÇÃO"
        reason.append("Preço interessante frente à meta configurada.")
    if compatible is None:
        reason.extend(compatibility_notes)
    if budget["conclusao_viavel"] is None:
        reason.append("Não é possível garantir a conclusão: há custos ainda desconhecidos.")
    if payment != preferred:
        reason.append(f"Avaliado no {payment}; preferência configurada é {preferred}.")
    if model == 'Ryzen 7 7800X3D' and config.get('meta_7800x3d_inclui_frete') and offer.get('frete_centavos') is None:
        reason.append('Meta de R$ 1.750 inclui frete; confirme o total entregue.')
        if classification == 'COMPRAR AGORA':
            classification = 'BOA PROMOÇÃO'
    if category == 'SSD' and offer.get('frete_centavos') is None:
        reason.append('Confirme o valor final com frete para verificar o limite do SSD.')
        if classification == 'COMPRAR AGORA':
            classification = 'BOA PROMOÇÃO'
    reason.extend(str(item) for item in verify)
    # The target is a planning reference, not a guarantee of quality. Generic
    # candidates remain for manual evaluation and can never be "COMPRAR AGORA".
    discovery = config.get('alternativas_gerais', {})
    categories = {'PLACA-MAE', 'RAM', 'FONTE', 'COOLER-CPU', 'GABINETE'}
    generic_only = all(item == 'Modelo exato não identificado; verificar especificações e compatibilidade' for item in verify)
    margin = Decimal(str(discovery.get('margem_percentual', 25)))
    resumed = any(p.get('categoria') == category and p.get('monitorar_novamente', False) for p in purchases)
    if (discovery.get('ativado', False) and category in categories and not offer.get('modelo_exato', False)
            and category in build and category not in budget['ja_possui'] and compatible is not False
            and (budget['conclusao_viavel'] is True or (config.get('alertar_orcamento_incerto') and (category in {'PLACA-MAE', 'RAM'} or resumed)))
            and price is not None and target is not None
            and generic_only and margin.is_finite() and 0 <= margin <= 100
            and Decimal(price) * 100 <= Decimal(target) * (100 + margin)):
        classification = 'AVALIAR ALTERNATIVA'
        reason = ['Alternativa dentro da margem de triagem; modelo, qualidade e compatibilidade precisam de verificação.'] + compatibility_notes
        if budget['conclusao_viavel'] is not True:
            reason.append('Orçamento ainda não confirmado; confira recursos disponíveis e custo total da montagem.')
        if payment != preferred:
            reason.append(f'Avaliado no {payment}; preferência configurada é {preferred}.')
    if category == 'RAM' and category in build and category not in budget['ja_possui'] and compatible is not False:
        interest = offer.get('parcelamento_sem_juros')
        count = offer.get('parcelas')
        if ram_strict and interest is False:
            classification = 'NÃO RECOMENDAR'
            reason.insert(0, 'Parcelamento com juros anunciado; RAM deve ser parcelada sem juros no cartão.')
        elif ram_strict and interest is not True:
            classification = 'VERIFICAR PARCELAMENTO'
            reason.insert(0, '⚠️ Parcelamento sem juros ainda não confirmado.')
        elif ram_strict and price is None:
            classification = 'VERIFICAR PREÇO NO CARTÃO'
            reason.insert(0, 'Parcelamento sem juros identificado; confirme o preço total no cartão.')
        elif ram_strict and (type(count) is not int or count < 2):
            classification = 'VERIFICAR PARCELAMENTO'
            reason.insert(0, 'Parcelamento sem juros identificado; confirme o número de parcelas no cartão.')
        elif not ram_strict and payment == 'cartao' and interest is False:
            classification = 'VERIFICAR PAGAMENTO'
            reason.insert(0, 'Parcelamento com juros anunciado; confira a alternativa à vista antes de decidir.')
        elif not ram_strict and payment == 'cartao' and interest is None and type(count) is int and count > 1:
            classification = 'VERIFICAR PAGAMENTO'
            reason.insert(0, 'Condições e possíveis juros do parcelamento não informados; confira também o preço à vista.')
        if ram_strict and payment == 'cartao':
            reason.append('RAM: parcelas pagas com renda pessoal separada da reserva à vista.')
    return {"classificacao": classification, "justificativa": reason,
            "pagamento": payment, "preco_centavos": price, "total_entregue_centavos": assessed if freight is not None else None, "meta_centavos": target,
            "diferenca_centavos": delta, "diferenca_percentual": percent,
            "orcamento": budget, "compativel": compatible, "politica_ram": 'cartao_sem_juros' if ram_strict else 'flexivel',
            "compatibilidade": compatibility_notes, "comparacao": _comparison(offer, config, price)}
