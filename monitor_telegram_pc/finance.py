"""Orçamento local: dinheiro, crédito e compromissos permanecem separados.

Todos os cálculos usam centavos. As parcelas são baixadas apenas por confirmação
manual; a passagem do tempo não paga faturas nem libera limite automaticamente.
O caixa PIX é a reserva para peças, não a renda futura usada para pagar o cartão.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Any


DEFAULT_BUILD = {
    "CPU": {"modelo": "Ryzen 7 7800X3D", "estimativa_pix": 1700, "pagamento": "pix"},
    "GPU": {"modelo": "RX 9070 XT 16GB", "estimativa_pix": 4700, "pagamento": "pix"},
    "PLACA-MAE": {"modelo": "ASUS B650M-AYW", "estimativa_pix": 680, "pagamento": "pix"},
    # Preço no cartão desconhecido: não reutilizar a meta PIX como promessa.
    "RAM": {"modelo": "DDR5 16GB", "estimativa_pix": 1500,
            "estimativa_cartao": None, "pagamento": "cartao"},
    "FONTE": {"modelo": "XPG Core Reactor II VE 850W", "estimativa_pix": 490, "pagamento": "pix"},
    "COOLER-CPU": {"modelo": "Cooler AM5", "estimativa_pix": 200, "pagamento": "pix"},
    "GABINETE": {"modelo": "Gabinete", "estimativa_pix": 250, "pagamento": "pix"},
    "SSD": {"modelo": "SSD existente", "possui": True},
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
    if item.get("pagamento") not in {"pix", "cartao"}:
        raise ValueError("Pagamento deve ser pix ou cartao.")
    item["parcelas"] = _whole(item.get("parcelas", 1), "Número de parcelas", 1)
    item["parcelas_pagas"] = _whole(item.get("parcelas_pagas", 0), "Parcelas pagas")
    installments(item["total_centavos"], item["parcelas"])
    if item["parcelas_pagas"] > item["parcelas"]:
        raise ValueError("Parcelas pagas excedem o número contratado.")
    if item["pagamento"] == "pix":
        if item["parcelas"] != 1 or item["parcelas_pagas"]:
            raise ValueError("Compra PIX não tem parcelas de cartão.")
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
    cash = cents(limits.get("pix", 10000))
    credit = cents(limits.get("cartao", 2500))
    # Total project ceiling, not a spending target. Paying a credit-card bill
    # restores the revolving limit but never increases this assembly budget.
    total_cap = cents(limits["total_maximo"]) if "total_maximo" in limits else cash + credit
    build = config.get("montagem", DEFAULT_BUILD)
    bought = [validate_purchase(p) for p in purchases]
    spent = sum(p["total_centavos"] for p in bought if p["pagamento"] == "pix")
    all_paid = sum(p["total_centavos"] for p in bought)
    outstanding = 0
    paid_card = 0
    schedule: dict[str, int] = {}
    for p in bought:
        if p["pagamento"] != "cartao":
            continue
        pieces = installments(p["total_centavos"], p["parcelas"])
        paid_card += sum(pieces[:p["parcelas_pagas"]])
        outstanding += sum(pieces[p["parcelas_pagas"]:])
        for index in range(p["parcelas_pagas"], p["parcelas"]):
            month = _month(p["primeira_parcela"], index)
            schedule[month] = schedule.get(month, 0) + pieces[index]

    acquired = {p["categoria"] for p in bought}
    already_owned = {cat for cat, spec in build.items() if spec.get("possui", False)}
    acquired |= already_owned
    pending: list[str] = []
    remaining: list[dict] = []
    remainder = {"pix": 0, "cartao": 0}
    known = {"pix": True, "cartao": True}
    category = candidate.get("categoria") if candidate else None
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
            remaining.append({"categoria": cat, "modelo": candidate.get("peca", ""),
                              "pagamento": payment, "estimativa_centavos": candidate_price,
                              "oferta_candidata": True})
            continue
        method = spec.get("pagamento", "pix")
        if method not in {"pix", "cartao"}:
            raise ValueError(f"Pagamento inválido na montagem: {cat}.")
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

    cash_after = cash - spent - extra_pix
    credit_after = credit - outstanding - extra_card
    unknown = not all(known.values()) or bool(candidate and candidate_price is None)
    lower_bound = all_paid + (candidate_price or 0) + sum(remainder.values())
    limits_exceeded = cash_after < remainder["pix"] or credit_after < remainder["cartao"]
    cap_exceeded = lower_bound > total_cap
    impossible = limits_exceeded or cap_exceeded
    viable = False if impossible or candidate_redundant else (None if unknown else True)
    if limits_exceeded:
        pending.append("A reserva PIX ou o limite de cartão não cobrem a montagem projetada.")
    if cap_exceeded:
        pending.append("Custo acumulado da montagem excede o teto total configurado; pagar parcelas não aumenta esse teto.")
    if candidate and category not in build:
        viable = False
    projected = None if unknown else lower_bound
    return {
        "pix_inicial_centavos": cash, "cartao_inicial_centavos": credit,
        "teto_total_centavos": total_cap,
        "saldo_teto_centavos": total_cap - all_paid,
        "saldo_teto_apos_centavos": None if candidate and candidate_price is None else total_cap - all_paid - (candidate_price or 0),
        "folga_teto_centavos": None if unknown else total_cap - lower_bound,
        "total_minimo_projetado_centavos": lower_bound,
        "gasto_pix_centavos": spent, "cartao_comprometido_centavos": outstanding,
        "cartao_pago_centavos": paid_card, "total_comprado_centavos": all_paid,
        "saldo_pix_centavos": cash - spent, "limite_cartao_centavos": credit - outstanding,
        "saldo_pix_apos_centavos": None if candidate and payment == "pix" and candidate_price is None else cash_after,
        "limite_cartao_apos_centavos": None if candidate and payment == "cartao" and candidate_price is None else credit_after,
        "restante_pix_centavos": remainder["pix"] if known["pix"] else None,
        "restante_cartao_centavos": remainder["cartao"] if known["cartao"] else None,
        "total_projetado_centavos": projected,
        "folga_pix_centavos": cash_after - remainder["pix"] if known["pix"] and not (candidate and payment == "pix" and candidate_price is None) else None,
        "folga_cartao_centavos": credit_after - remainder["cartao"] if known["cartao"] and not (candidate and payment == "cartao" and candidate_price is None) else None,
        "compromissos_mensais": dict(sorted(schedule.items())),
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
        selected[purchase.get("categoria", purchase.get("category"))] = purchase.get("modelo", "")
    selected[category] = model
    parts = {cat: profiles.get(name, {}) for cat, name in selected.items()}
    cpu, board, ram = (parts.get(cat, {}) for cat in ("CPU", "PLACA-MAE", "RAM"))
    gpu, psu, cooler, case = (parts.get(cat, {}) for cat in ("GPU", "FONTE", "COOLER-CPU", "GABINETE"))

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
    build = config.get("montagem", DEFAULT_BUILD)
    preferred = build.get(category, {}).get("pagamento", "pix")
    if preferred not in {"pix", "cartao"}:
        raise ValueError("Forma de pagamento inválida na montagem.")
    target_raw = config.get("metas", {}).get(model, offer.get("meta"))
    if not offer.get('modelo_exato', False) and category in {'PLACA-MAE', 'RAM', 'FONTE', 'COOLER-CPU', 'GABINETE'}:
        reference = build.get(category, {}).get('modelo')
        target_raw = config.get('metas', {}).get(reference, target_raw)
    target = cents(target_raw) if target_raw is not None else None
    if target == 0:
        target = None
    settings = config.get("alertas", {})
    exceptional = Decimal(str(settings.get("excepcional_percentual", 10)))
    good = Decimal(str(settings.get("boa_percentual", 0)))
    if not exceptional.is_finite() or not good.is_finite() or not 0 <= exceptional <= 100 or not 0 <= good <= 100:
        raise ValueError("Percentuais de alerta devem estar entre 0 e 100.")
    if good > exceptional:
        raise ValueError("Desconto de boa promoção não pode exceder o excepcional.")
    # A RAM pode merecer uma exceção PIX se o desconto for excepcional e a
    # projeção completa continuar viável. Nunca inferir preço do cartão.
    payment = preferred
    pix = offer.get("pix_centavos")
    pix_exception = (category == "RAM" and preferred == "cartao" and pix is not None
                     and target is not None and Decimal(pix) * 100 <= Decimal(target) * (100 - exceptional))
    if pix_exception and budget_snapshot(config, purchases, offer, "pix")["conclusao_viavel"] is True:
        payment = "pix"
    elif offer.get(f"{payment}_centavos") is None:
        other = "pix" if payment == "cartao" else "cartao"
        if offer.get(f"{other}_centavos") is not None:
            payment = other
    price = offer.get(f"{payment}_centavos")
    if price is not None:
        price = _whole(price, "Preço da oferta", 1)
    budget = budget_snapshot(config, purchases, offer, payment)
    compatible, compatibility_notes = _compatibility(offer, config, purchases)
    reason: list[str] = []
    verify = list(offer.get("verificar") or [])
    delta = price - target if price is not None and target is not None else None
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
        reason.append("Compra compromete a conclusão da montagem dentro dos limites separados.")
    elif price is None:
        reason.append("Preço final por forma de pagamento não identificado; confirmar anúncio.")
    elif target is None:
        reason.append("Meta deste modelo ainda não cadastrada.")
    elif Decimal(price) * 100 > Decimal(target) * (100 - good):
        classification = "NÃO COMPENSA"
        reason.append("Preço não atende ao desconto configurado para este modelo.")
    elif verify:
        reason.append("Extração ou condições da oferta precisam de verificação.")
    elif Decimal(price) * 100 <= Decimal(target) * (100 - exceptional) and compatible is True and budget["conclusao_viavel"] is True:
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
    reason.extend(str(item) for item in verify)
    # The target is a planning reference, not a guarantee of quality. Generic
    # candidates remain for manual evaluation and can never be "COMPRAR AGORA".
    discovery = config.get('alternativas_gerais', {})
    categories = {'PLACA-MAE', 'RAM', 'FONTE', 'COOLER-CPU', 'GABINETE'}
    generic_only = all(item == 'Modelo exato não identificado; verificar especificações e compatibilidade' for item in verify)
    margin = Decimal(str(discovery.get('margem_percentual', 25)))
    if (discovery.get('ativado', False) and category in categories and not offer.get('modelo_exato', False)
            and category in build and category not in budget['ja_possui'] and compatible is not False
            and budget['conclusao_viavel'] is True and price is not None and target is not None
            and generic_only and margin.is_finite() and 0 <= margin <= 100
            and Decimal(price) * 100 <= Decimal(target) * (100 + margin)):
        classification = 'AVALIAR ALTERNATIVA'
        reason = ['Alternativa dentro da margem de triagem; modelo, qualidade e compatibilidade precisam de verificação.'] + compatibility_notes
        if payment != preferred:
            reason.append(f'Avaliado no {payment}; preferência configurada é {preferred}.')
    return {"classificacao": classification, "justificativa": reason,
            "pagamento": payment, "preco_centavos": price, "meta_centavos": target,
            "diferenca_centavos": delta, "diferenca_percentual": percent,
            "orcamento": budget, "compativel": compatible,
            "compatibilidade": compatibility_notes, "comparacao": _comparison(offer, config, price)}
