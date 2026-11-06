"""Conservative, offline extraction of hardware offers.

Preserves the original monitor's product rules and legacy CSV fields. Monetary
arithmetic uses integer centavos; payment methods are never inferred from a price
alone. Unknown models and ambiguous announcements remain available for review.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse


# Specific variants precede broader families. Capacity labels for GPUs identify
# the configured family, not a claim about a vendor's exact board dimensions.
RULES = [
    ("GPU", "RX 9070 XT 16GB", r"(?<!\w)(?:(?:amd\s+)?(?:radeon\s+)?(?:rx[\s-]*)?|radeon[\s-]*)9070[\s-]*xt\b", 5100, True),
    ("GPU", "RX 9070 GRE 12GB", r"\b(?:rx|radeon)\s*9070\s*gre\b", 3600, True),
    ("GPU", "RX 9070 16GB", r"\b(?:rx|radeon)\s*9070\b(?!\s*(?:xt|gre))", 4200, True),
    ("GPU", "RX 9060 XT 16GB", r"\b(?:rx|radeon)\s*9060\s*xt\b(?=[^\n]*\b16\s*gb\b)", 2800, True),
    ("GPU", "RTX 5070", r"\brtx\s*5070\b(?!\s*ti)", 4000, True),
    ("CPU", "Ryzen 7 7800X3D", r"\b7800\s*x3d\b", 1700, True),
    ("CPU", "Ryzen 7 9800X3D", r"\b9800\s*x3d\b", 2050, True),
    ("CPU", "Ryzen 9 7900", r"\bryzen\s*9\s*7900\b(?!\s*x)", 1600, True),
    ("CPU", "Ryzen 5 7600", r"\bryzen\s*5\s*7600\b(?!\s*x)", 1050, True),
    ("CPU", "Ryzen 5 9600X", r"\b9600\s*x\b", 1250, True),
    ("PLACA-MAE", "ASUS B650M-AYW", r"\bb650m[\s-]*ayw\b", 680, True),
    ("PLACA-MAE", "B650 (modelo não identificado)", r"\bb650(?:m|e)?\b", 680, False),
    ("PLACA-MAE", "B850 (modelo não identificado)", r"\bb850(?:m)?\b", 800, False),
    ("PLACA-MAE", "B840 (modelo não identificado)", r"\bb840(?:m)?\b", 650, False),
    ("PLACA-MAE", "Placa-mãe (modelo não identificado)", r"\b(?:placa[\s-]*m[aã]e|motherboard)\b", 680, False),
    ("FONTE", "XPG Core Reactor II VE 850W", r"\bcore\s*reactor\s*ii\s*ve\b(?=[^\n]*\b850\s*w\b)", 490, True),
    ("FONTE", "XPG Core Reactor II 850W", r"\bcore\s*reactor\s*ii\b(?!\s*ve)(?=[^\n]*\b850\s*w\b)", 490, True),
    ("FONTE", "XPG Core Reactor 850W", r"\bcore\s*reactor\b(?!\s*ii)(?=[^\n]*\b850\s*w\b)", 490, True),
    ("FONTE", "XPG Kyber 850W", r"\bkyber\b(?=[^\n]*\b850\s*w\b)", 400, True),
    ("FONTE", "Fonte (modelo não identificado)", r"\b(?:fonte|psu|power supply)\b", 500, False),
    ("COOLER-CPU", "Cooler CPU (modelo não identificado)", r"\b(?:cooler\s+para\s+processador|air[ -]?cooler|cooler\s+de\s+cpu|water[ -]?cooler)\b", 200, False),
    ("VENTOINHA", "Kit de fans gabinete (modelo não identificado)", r"\b(?:kit\s+(?:de\s+)?(?:ventoinhas|fans?)|ventoinhas\s+de\s+gabinete|cooler\s+de\s+gabinete)\b", 100, False),
    ("GABINETE", "Gabinete (modelo não identificado)", r"\b(?:gabinete|case gamer)\b", 250, False),
]
MIN_PRICE = {"GPU": 1000, "CPU": 450, "PLACA-MAE": 280, "RAM": 100,
             "FONTE": 150, "GABINETE": 70, "COOLER-CPU": 35, "VENTOINHA": 15}
MONEY = r"R\$\s*(\d[\d.,]*)"
PRICE_RE = re.compile(MONEY, re.I)
INSTALLMENT_RE = re.compile(r"\b(\d{1,2})\s*(?:[x×]|parcelas?)\s*(?:de\s*)?" + MONEY, re.I)
URL_RE = re.compile(r"https?://[^\s<>]+", re.I)
HISTORY_RE = re.compile(r"menor\s+pre[cç]o|mediana|hist[oó]rico|m[eé]dia\s+(?:dos\s+)?pre[cç]os|pre[cç]o\s+antigo", re.I)


def normalize_channel(value: str) -> str:
    value = re.sub(r"^https?://(?:www\.)?t\.me/(?:s/)?", "", value.strip().rstrip("/"), flags=re.I).lstrip("@")
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{4,31}", value):
        raise ValueError("Canal público inválido; use @usuario ou https://t.me/usuario")
    return value.lower()


def cents(value: str) -> int | None:
    value = value.strip().rstrip(".,")
    if not re.fullmatch(r"\d[\d.,]*", value):
        return None
    if "," in value:
        if not re.fullmatch(r"(?:\d+|\d{1,3}(?:\.\d{3})+),\d{1,2}", value):
            return None
        value = value.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", value):
        value = value.replace(".", "")
    elif not re.fullmatch(r"\d+(?:\.\d{1,2})?", value):
        return None
    try:
        amount = Decimal(value) * 100
        return int(amount) if amount.is_finite() and amount > 0 else None
    except InvalidOperation:
        return None


def brl_number(text: str) -> float | None:
    value = cents(text)
    return value / 100 if value is not None else None


def brl(value: float) -> str:
    return "R$ " + f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _products(text: str, config: dict | None = None) -> list[tuple[str, str, float, bool]]:
    # Product slugs and accessory mentions in URLs are not product headings.
    text = URL_RE.sub("", text)
    found: list[tuple[str, str, float, bool]] = []
    generic: list[tuple[int, tuple]] = []
    rules = []
    # Optional exact alternative definitions are intentionally explicit. No
    # performance, power or compatibility data is manufactured by this module.
    for item in (config or {}).get("alternativas", []):
        if isinstance(item, dict) and all(k in item for k in ("modelo", "categoria", "padrao", "meta")):
            rules.append((item["categoria"], item["modelo"], item["padrao"], item["meta"], True))
    alternative_count = len(rules)
    rules.extend(RULES)
    matched_alternatives: set[str] = set()
    aliases = {"Gabinete (modelo não identificado)": "Gabinete",
               "Cooler CPU (modelo não identificado)": "Air cooler CPU"}
    for rule_index, (category, model, pattern, target, exact) in enumerate(rules):
        match = re.search(pattern, text, re.I)
        if match:
            if rule_index >= alternative_count and category in matched_alternatives:
                continue
            if rule_index < alternative_count:
                matched_alternatives.add(category)
            targets = (config or {}).get("metas", {})
            target = targets.get(model, targets.get(aliases.get(model, ""), target))
            if not exact:
                generic.append((match.start(), (category, model, float(target), exact)))
                continue
            if category in {"VENTOINHA", "GABINETE"} and any(p[0] == "GPU" for p in found):
                continue
            if category == "GABINETE" and any(p[0] == "VENTOINHA" for p in found):
                continue
            found.append((category, model, float(target), exact))
    if not found and generic:
        # e.g. "Gabinete ... sem fonte" describes one case, not a PSU combo.
        found.append(min(generic, key=lambda item: item[0])[1])
        if found[0][0] == 'PLACA-MAE' and re.search(r'\b(?:intel|lga\s*\d+|b760|b660|h610|z790|z690)\b', text, re.I):
            return []
    if not found and re.search(r"\bddr5\b", text, re.I) and not re.search(r"placa[\s-]*m[aã]e|motherboard|\blga\b", text, re.I):
        kit = re.search(r"\b([124])\s*[x×]\s*(\d+)\s*gb\b", text, re.I)
        capacity = re.search(r"\b(\d+)\s*gb\b", text, re.I)
        if kit:
            label = f"{kit[1]}x{kit[2]}GB"
        elif capacity:
            label = f"1x{capacity[1]}GB"
        else:
            label = "capacidade desconhecida"
        model = "DDR5 " + label
        target = (config or {}).get("metas", {}).get(model, 1500)
        found.append(("RAM", model, float(target), False))
    return list(dict.fromkeys(found))


def detect_product(text: str) -> tuple[str, str, float] | None:
    products = _products(text)
    return products[0][:3] if products else None


def extract_coupon(text: str) -> str:
    pattern = re.compile(r"\bcupom\b(?:\s+(?:de\s+desconto|no\s+app|no\s+site|da\s+loja))?\s*(?::|=|\-)?\s*[`\"']?([A-Za-z0-9][A-Za-z0-9_-]{3,})", re.I)
    ignore = {"PARA", "COMO", "NESTE", "DESCONTO", "VALIDO", "APLICAR", "RESGATE", "PRODUTO", "EXCLUSIVO"}
    return next((m[1].upper() for m in pattern.finditer(text) if m[1].upper() not in ignore), "")


def store_link(text: str) -> str:
    for match in URL_RE.finditer(text):
        url = match[0].rstrip(".,;)]}>\"'")
        if (urlparse(url).hostname or "").lower() not in {"t.me", "telegram.me", "www.t.me"}:
            return url
    return ""


def _prices(text: str) -> dict[str, Any]:
    values: dict[str, list[int]] = {"pix": [], "cartao": [], "anunciado": []}
    counts: list[int] = []
    verify: list[str] = []
    conditions: list[str] = []
    clean = re.sub(r"~~.*?~~|<s>.*?</s>|<del>.*?</del>", "", text, flags=re.S | re.I)
    for original in clean.splitlines():
        # History labels usually introduce a separate clause, even in flattened
        # legacy CSV text. Keep current amounts before that clause, not after.
        line = HISTORY_RE.split(original, maxsplit=1)[0]
        if not line.strip():
            continue
        # A parenthesized coupon amount qualifies the immediately preceding PIX
        # total. Only this explicit structure supplies payment context; a coupon
        # price elsewhere in the message still has an unknown payment method.
        coupon_pix = re.compile(
            r"R\$\s*(?P<base>\d[\d.,]*)\s+(?:no|via|por|em)\s+PIX\s*"
            r"\(\s*R\$\s*(?P<final>\d[\d.,]*)\s+com\s+(?:o\s+)?cupom\s+"
            r"[`\"']?(?P<code>[A-Za-z0-9][A-Za-z0-9_-]{3,})[`\"']?\s*\)", re.I)

        def coupon_total(match):
            base, final = cents(match['base']), cents(match['final'])
            if base is None or final is None or final > base:
                verify.append("Preço com cupom inconsistente; conferir valor final")
                return match[0]
            conditions.append(
                f"PIX anunciado com cupom {match['code'].upper()}: {brl(final / 100)}; "
                f"sem cupom: {brl(base / 100)}. Aplicação do cupom não verificada.")
            return f"R$ {match['final']} no PIX"

        line = coupon_pix.sub(coupon_total, line)
        for match in INSTALLMENT_RE.finditer(line):
            unit = cents(match[2])
            count = int(match[1])
            if unit and count > 0:
                values["cartao"].append(unit * count)
                counts.append(count)
                conditions.append(f"{count} parcelas de {brl(unit / 100)}; total calculado pela soma das parcelas")
        line = INSTALLMENT_RE.sub(lambda m: " " * len(m[0]), line)
        matches = list(PRICE_RE.finditer(line))
        for index, match in enumerate(matches):
            amount = cents(match[1])
            if amount is None:
                verify.append("Valor monetário inválido")
                continue
            before = line[matches[index - 1].end() if index else 0:match.start()]
            after = line[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(line)]
            if re.search(r"(?:\bde|antes|era|pre[cç]o\s+(?:original|normal)|frete|cashback|desconto(?:\s+de)?)\s*[:\-]?\s*$", before, re.I):
                continue
            # An installment amount without an explicit count is never a total.
            if re.search(r"parcela(?:s)?\s*(?:de)?\s*[:\-]?\s*$", before, re.I) or re.match(r"\s*(?:por\s+(?:parcela|m[eê]s)|/\s*m[eê]s)", after, re.I):
                verify.append("Parcela sem quantidade; total desconhecido")
                continue
            # Prefer adjacent payment labels; do not let an earlier amount's
            # suffix label bleed into the next amount's payment method.
            suffix = re.match(r"\s*(?:(?:[àa]\s+vista\s+)?(?:no|via|por|em)\s+)?(pix|cart[aã]o|parcelad[oa])\b", after, re.I)
            installments_suffix = re.match(r"\s*em\s+(\d{1,2})\s*parcelas?\s+(?:no\s+)?cart[aã]o", after, re.I)
            prefix = re.search(r"\b(pix|cart[aã]o|parcelad[oa])\s*[:\-]?\s*$", before, re.I)
            # "R$ X no PIX R$ Y à vista no cartão": PIX belongs to X.
            if index and re.fullmatch(r"\s*(?:no|via|por|em)?\s*(?:pix|cart[aã]o|parcelad[oa])\s*", before, re.I):
                prefix = None
            method = (prefix or suffix)
            bucket = "pix" if method and method[1].lower() == "pix" else "cartao" if method else "anunciado"
            if installments_suffix:
                bucket = "cartao"
                counts.append(int(installments_suffix[1]))
            values[bucket].append(amount)
    result: dict[str, Any] = {"parcelas": counts[0] if counts and len(set(counts)) == 1 else None}
    for bucket, amounts in values.items():
        distinct = set(amounts)
        if len(distinct) > 1:
            result[bucket + "_centavos"] = None
            verify.append(f"Mais de um preço {bucket}; conferir condição e valor final")
        else:
            result[bucket + "_centavos"] = next(iter(distinct), None)
    if len(set(counts)) > 1:
        verify.append("Mais de um plano de parcelamento")
    if result["anunciado_centavos"] is not None:
        verify.append("Forma de pagamento do preço anunciado não informada")
    if re.search(r"cupom|cashback|clube|prime|primeira\s+compra|apenas\s+no\s+app|frete", text, re.I):
        conditions.extend(line.strip() for line in text.splitlines() if re.search(r"cupom|cashback|clube|prime|primeira\s+compra|apenas\s+no\s+app|frete", line, re.I))
    if re.search(r"cashback|clube|primeira\s+compra|exclusivo", text, re.I):
        verify.append("Condições de elegibilidade/desconto exigem conferência")
    if not any(result[k + "_centavos"] is not None for k in values):
        verify.append("Preço final não identificado com segurança")
    result["verificar"] = list(dict.fromkeys(verify))
    result["condicoes"] = list(dict.fromkeys(conditions))
    return result


def extract_prices(text: str, category: str) -> list[float]:
    """Compatibility helper; returns only unambiguous current total amounts."""
    result = _prices(text)
    return [result[k] / 100 for k in ("pix_centavos", "cartao_centavos", "anunciado_centavos") if result[k] is not None]


def _explicit_specs(text: str, category: str) -> dict[str, Any]:
    """Use only explicit, unambiguous product specifications from its block."""
    if category != 'GPU':
        return {}
    result = {}
    patterns = {
        'comprimento_mm': r'\bcomprimento\s*(?:(?:da|do)\s+(?:gpu|placa(?:\s+de\s+v[ií]deo)?))?\s*[:=]\s*(\d{2,4})\s*mm\b',
        'fonte_minima_w': r'\bfonte\s+(?:m[ií]nima|recomendada)\s*(?::|=|de)?\s*(\d{3,4})\s*w\b',
    }
    for key, pattern in patterns.items():
        values = {int(value) for value in re.findall(pattern, text, re.I)}
        if len(values) == 1:
            result[key] = values.pop()
    connector = re.findall(
        r'\bconectores?\s+(?:de\s+alimenta[cç][aã]o\s+da\s+(?:gpu|placa)|da\s+(?:gpu|placa))\s*[:=]\s*'
        r'([1-9])\s*[x×]\s*(?:pcie\s*)?(?:8\s*pinos?|6\s*\+\s*2)\b', text, re.I)
    if len(set(connector)) == 1:
        result['conectores_requeridos'] = {'pcie_6_2': int(connector[0])}
    return result


def _shipping_belem(text: str) -> int | None:
    pattern = r'\bfrete\s+para\s+bel[eé]m\s*[:=]\s*R\$\s*(\d[\d.,]*)'
    values = {cents(value) for value in re.findall(pattern, text, re.I)}
    values.discard(None)
    if re.search(r'\bfrete\s+(?:gr[aá]tis|gratuito)\s+para\s+bel[eé]m\b', text, re.I):
        values.add(0)
    return values.pop() if len(values) == 1 else None


def _offer(channel: str, msg_id: int, date: datetime, text: str, product: tuple,
           original: str, extra_verify: list[str] | None = None) -> dict[str, Any]:
    category, model, target, exact = product
    details = _prices(text)
    verify = details["verificar"]
    verify.extend(extra_verify or [])
    if not exact:
        verify.append("Modelo exato não identificado; verificar especificações e compatibilidade")
    if category == "RAM" and re.search(r"so[ -]?dimm|notebook", text, re.I):
        verify.append("Memória de notebook: incompatível com placa-mãe desktop DIMM")
    if re.search(r"kit\s+(?:upgrade|ryzen)|combo", text, re.I):
        verify.append("Possível combo; conferir itens incluídos e preço por conjunto")
    prices = [details[k] for k in ("pix_centavos", "cartao_centavos", "anunciado_centavos") if details[k] is not None]
    price = min(prices) / 100 if prices else None
    if price is not None and (price < MIN_PRICE.get(category, 0) or price > 30000):
        verify.append("Preço fora da faixa plausível; verificar erro ou produto diferente")
    seller = store_link(text)
    store = re.search(r"\bloja\s*:\s*([^\n|]+)", text, re.I)
    store_name = store[1].strip() if store else (urlparse(seller).hostname or "")
    store_name = re.split(r"https?://|menor\s+pre[cç]o|mediana|avalia[cç][aã]o\s*:|frete", store_name, maxsplit=1, flags=re.I)[0].strip()
    if seller and ((category == "GABINETE" and re.search(r"/(?:kit-ventoinhas|memoria-|placa-de-video)", seller, re.I)) or (category == "GPU" and re.search(r"/(?:kit-ventoinhas|gabinete-gamer|fonte-)", seller, re.I))):
        verify.append("Link divergente do produto anunciado")
    if date.tzinfo is None:
        raise ValueError("A data da mensagem deve conter fuso horário")
    title = next((line.strip() for line in text.splitlines() if detect_product(line)), model)
    title = re.split(r"R\$|https?://|\bcupom\b|\bloja\s*:", title, maxsplit=1, flags=re.I)[0].strip()[:240] or model
    return {
        "data": date.astimezone(timezone.utc).isoformat(), "canal": channel,
        "message_id": msg_id, "peca": model, "categoria": category,
        "nome_completo": title, "preco": price, "meta": target,
        "diferenca_meta": round(price - target, 2) if price is not None else None,
        "avaliacao": "VERIFICAR" if verify else "OFERTA OBSERVADA",
        "cupom": extract_coupon(text), "link": f"https://t.me/{channel}/{msg_id}",
        "link_loja": seller, "texto": original, "loja": store_name,
        "modelo_exato": exact, **details,
        "especificacoes": _explicit_specs(text, category),
        "frete_centavos": _shipping_belem(text),
    }


def parse_offers(channel: str, msg_id: int, date: datetime, text: str,
                 config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    # Split only where actual product headings occur; keep multiline pricing,
    # links and coupon conditions with their respective product block.
    lines = text.splitlines()
    starts = [(i, _products(line, config)) for i, line in enumerate(lines)
              if not re.match(r'^\s*fonte\s+(?:m[ií]nima|recomendada)\b', line, re.I)]
    starts = [(i, products) for i, products in starts if products]
    if not starts:
        products = _products(text, config)
        return [_offer(channel, msg_id, date, text, p, text) for p in products]
    models = {p[1] for _, products in starts for p in products}
    if len(models) == 1:
        return [_offer(channel, msg_id, date, text, starts[0][1][0], text)]
    offers = []
    for index, (start, products) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(lines)
        block = "\n".join(lines[start:end])
        ambiguous = len(products) > 1
        for product in products:
            offer = _offer(channel, msg_id, date, block, product, text,
                           ["Vários produtos no mesmo trecho; associação de preço ambígua"] if ambiguous else [])
            if ambiguous:
                for key in ("preco", "pix_centavos", "cartao_centavos", "anunciado_centavos", "diferenca_meta"):
                    offer[key] = None
            offers.append(offer)
    return offers


def parse_offer(channel: str, msg_id: int, date: datetime, text: str,
                config: dict | None = None) -> dict | None:
    offers = parse_offers(channel, msg_id, date, text, config)
    return offers[0] if offers else None
