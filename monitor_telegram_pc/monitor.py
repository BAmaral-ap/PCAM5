#!/usr/bin/env python3
"""Monitor de ofertas de hardware em canais PUBLICOS do Telegram.

Uso: python monitor.py [--watch] [--limit 150] [--days 7]
Somente LE mensagens. Nao envia mensagens e nao entra em canais.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import FloodWaitError

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")
CONFIG_PATH = HERE / "config.json"
DATA_DIR = HERE / "dados"
DATA_DIR.mkdir(exist_ok=True)
CSV_PATH = DATA_DIR / "ofertas.csv"
STATE_PATH = DATA_DIR / "estado.json"

# Padroes de identificacao: a ordem importa para distinguir RX 9070 e RX 9070 XT.
RULES = [
    # Modelos exatos: GRE (12 GB), XT e 9070 comum sao placas diferentes.
    ("GPU", "RX 9070 XT 16GB", r"\b(?:rx|radeon)\s*9070\s*xt\b", 4700),
    ("GPU", "RX 9070 GRE 12GB", r"\b(?:rx|radeon)\s*9070\s*gre\b", 3600),
    ("GPU", "RX 9070 16GB", r"\b(?:rx|radeon)\s*9070\b(?!\s*(?:xt|gre))", 4200),
    ("GPU", "RX 9060 XT 16GB", r"\b(?:rx|radeon)\s*9060\s*xt\b(?=.*?16\s*gb)", 2800),
    ("GPU", "RTX 5070", r"\brtx\s*5070\b(?!\s*ti)", 4000),
    ("CPU", "Ryzen 7 7800X3D", r"\b7800\s*x3d\b", 1700),
    ("CPU", "Ryzen 7 9800X3D", r"\b9800\s*x3d\b", 2050),
    ("CPU", "Ryzen 9 7900", r"\bryzen\s*9\s*7900\b(?!\s*x|x3d)", 1600),
    ("CPU", "Ryzen 5 7600", r"\bryzen\s*5\s*7600\b(?!\s*x)", 1050),
    ("CPU", "Ryzen 5 9600X", r"\b9600\s*x\b", 1250),
    ("PLACA-MAE", "ASUS B650M-AYW", r"\bb650m[\s-]*ayw\b", 680),
    ("PLACA-MAE", "B650 (outra)", r"\bb650(?:m|e)?\b", 680),
    ("PLACA-MAE", "B850", r"\bb850(?:m)?\b", 800),
    ("PLACA-MAE", "B840", r"\bb840(?:m)?\b", 650),
    ("RAM", "DDR5 16GB", r"\bddr5\b", 1500),
    ("FONTE", "XPG Core Reactor 850W", r"\bcore\s*reactor(?:\s*ii)?(?:\s*ve)?\b(?=.*\b850\s*w\b)", 490),
    ("FONTE", "XPG Kyber 850W", r"\bkyber\b(?=.*\b850\s*w\b)", 400),
    ("FONTE", "Fonte 750W/850W", r"\b(?:fonte|psu|power supply)\b(?=.*\b(?:750|850)\s*w\b)", 500),
    ("GABINETE", "Gabinete", r"\b(?:gabinete|case gamer)\b", 250),
    ("COOLER-CPU", "Air cooler CPU", r"\b(?:cooler\s+para\s+processador|air[ -]?cooler|cooler\s+de\s+cpu|water[ -]?cooler)\b", 200),
    ("VENTOINHA", "Kit de fans gabinete", r"\b(?:kit\s+(?:de\s+)?(?:ventoinhas|fans?)|ventoinhas\s+de\s+gabinete|cooler\s+de\s+gabinete)\b", 100),
]
MIN_PRICE = {"GPU": 1000, "CPU": 450, "PLACA-MAE": 280, "RAM": 300,
             "FONTE": 250, "GABINETE": 70, "COOLER-CPU": 35, "VENTOINHA": 15}
PRICE_RE = re.compile(r"R\$\s*([\d.,]+)", re.I)
COUPON_RE = re.compile(r"\bcupom\s*(?:de\s*)?(?:desconto\s*)?(?:\:|=|\-|\s)*(?:[\"'`])?([A-Z0-9][A-Z0-9_-]{3,})", re.I)
IGNORE_LINE_RE = re.compile(r"(menor\s+pre[cç]o|mediana|hist[oó]rico|m[eé]dia\s+(?:dos\s+)?pre[cç]os|pre[cç]o\s+antigo)", re.I)
FIELDS = ["data", "canal", "peca", "categoria", "preco", "meta", "diferenca_meta", "avaliacao", "cupom", "link", "link_loja", "texto"]
URL_RE = re.compile(r"https?://[^\s<>]+", re.I)
INSTALLMENT_RE = re.compile(r"\b\d{1,2}\s*[x×]\s*(?:de\s*)?R\$\s*[\d.,]+", re.I)


def normalize_channel(value: str) -> str:
    s = value.strip().rstrip("/")
    s = re.sub(r"^https?://(?:www\.)?t\.me/(?:s/)?", "", s, flags=re.I)
    s = s.lstrip("@")
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{4,31}", s):
        raise ValueError(f"Canal publico invalido: {value!r}. Use @usuario ou https://t.me/usuario")
    return s


def brl_number(text: str) -> float | None:
    s = text.strip().rstrip(".,")
    if not re.fullmatch(r"\d[\d.,]*", s):
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif "." in s and len(s.split(".")[-1]) == 3:
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def detect_product(text: str) -> tuple[str, str, float] | None:
    lower = text.lower()
    for category, model, pattern, target in RULES:
        if re.search(pattern, lower, re.I | re.S):
            if category == "RAM":
                # Nao confundir DDR5 citado em especificacoes de placa-mae com RAM.
                if re.search(r"placa[\s-]*m[aã]e|motherboard|\blga\s*1700\b|\bintel\s*b760\b", lower):
                    return None
                # Este projeto quer APENAS um modulo de 16 GB DDR5.
                if not re.search(r"\b16\s*gb\b", lower):
                    return None
                if re.search(r"(?:2\s*[x×]\s*16\s*gb|32\s*gb\s*\(\s*2\s*[x×])", lower):
                    return None
            if category == "PLACA-MAE" and re.search(r"\b(?:lga\s*1700|intel\s*b760)\b", lower):
                return None
            return (category, model, target)
    return None


def extract_prices(text: str, category: str) -> list[float]:
    found = []
    for line in text.splitlines():
        if IGNORE_LINE_RE.search(line):
            continue
        # Parcelas (ex.: 3x de R$ 1.575,88) NAO sao precos finais.
        line = INSTALLMENT_RE.sub("", line)
        for match in PRICE_RE.finditer(line):
            v = brl_number(match.group(1))
            if v is not None and v >= MIN_PRICE[category] and v <= 30000:
                found.append(v)
    return found


def extract_coupon(text: str) -> str:
    # Cupons aparecem como "cupom AURAGAMER", "Cupom: `TECH8DO8`"
    # ou "Cupom no APP `DRAM15OFF`". Nao confundir APP com codigo.
    pattern = re.compile(
        r"\bcupom\b(?:\s+(?:de\s+desconto|no\s+app|no\s+site|da\s+loja))?"
        r"\s*(?::|=|\-)?\s*[`\"']?([A-Za-z0-9][A-Za-z0-9_-]{3,})", re.I
    )
    ignore = {"PARA", "COMO", "NESTE", "DESCONTO", "VALIDO", "APLICAR", "RESGATE", "PRODUTO"}
    for match in pattern.finditer(text):
        candidate = match.group(1).upper()
        if candidate not in ignore:
            return candidate
    return ""


def store_link(text: str) -> str:
    for m in URL_RE.finditer(text):
        url = m.group(0).rstrip(".,;)]}>\"'")
        if "t.me/" not in url.lower() and "telegram.me/" not in url.lower():
            return url
    return ""


def brl(value: float) -> str:
    return "R$ " + f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def parse_offer(channel: str, msg_id: int, date: datetime, text: str) -> dict[str, Any] | None:
    product = detect_product(text)
    if not product:
        return None
    category, model, target = product
    values = extract_prices(text, category)
    if not values:
        return None
    price = min(values)
    # Metas sao indicadores, NAO garantia de preco final/estoque/desconto.
    evaluation = "POTENCIAL BOA OFERTA — conferir carrinho" if price <= target else "ACIMA DA META — comparar"
    seller = store_link(text)
    # Alguns canais chegam a publicar texto de uma peca com URL de outra.
    if seller and ((category == "GABINETE" and re.search(r"/(?:kit-ventoinhas|memoria-|placa-de-video)", seller, re.I))
                   or (category == "GPU" and re.search(r"/(?:kit-ventoinhas|gabinete-gamer|fonte-)", seller, re.I))):
        evaluation += " | LINK DIVERGENTE"
    return {
        "data": date.astimezone().strftime("%Y-%m-%d %H:%M"),
        "canal": channel,
        "peca": model,
        "categoria": category,
        "preco": round(price, 2),
        "meta": round(target, 2),
        "diferenca_meta": round(price-target, 2),
        "avaliacao": evaluation,
        "cupom": extract_coupon(text),
        "link": f"https://t.me/{channel}/{msg_id}",
        "link_loja": seller,
        "texto": re.sub(r"\s+", " ", text).strip()[:550],
    }


def parse_offers(channel: str, msg_id: int, date: datetime, text: str) -> list[dict[str, Any]]:
    """Uma oferta por mensagem normalmente; varios produtos na mesma mensagem
    sao analisados por linha, sem associar preco de outra peca.
    """
    found_models = set()
    for line in text.splitlines():
        product = detect_product(line)
        if product:
            found_models.add(product[1])
    # Ex.: uma lista com processadores Ryzen 5 7600 e Ryzen 7 9800X3D.
    # Nao usar o menor preco de toda a mensagem para todos os produtos.
    if len(found_models) > 1:
        result: dict[str, dict[str, Any]] = {}
        for line in text.splitlines():
            offer = parse_offer(channel, msg_id, date, line)
            if offer:
                model = offer["peca"]
                offer["cupom"] = extract_coupon(text)
                offer["link_loja"] = store_link(line) or store_link(text)
                offer["texto"] = re.sub(r"\s+", " ", text).strip()[:550]
                # Mesma peca repetida no anuncio: guardar menor preco da mesma peca.
                if model not in result or offer["preco"] < result[model]["preco"]:
                    result[model] = offer
        return list(result.values())
    item = parse_offer(channel, msg_id, date, text)
    return [item] if item else []


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise SystemExit("Arquivo config.json nao encontrado.")
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    channels = cfg.get("canais", [])
    if not isinstance(channels, list) or not channels:
        raise SystemExit("Edite config.json e adicione os canais publicos em 'canais'.")
    cfg["canais"] = list(dict.fromkeys(normalize_channel(s) for s in channels))
    return cfg


def load_state() -> dict[str, int]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def load_existing() -> dict[str, dict[str, Any]]:
    rows = {}
    if CSV_PATH.exists():
        with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                rows[r["link"] + "|" + r["peca"]] = r
    return rows


def save_csv(rows: dict[str, dict[str, Any]]) -> None:
    temp = CSV_PATH.with_suffix(".tmp")
    ordered = sorted(rows.values(), key=lambda r: (r["data"], r["link"]), reverse=True)
    seen = set()
    data = []
    for row in ordered:
        # Mesmo anuncio republicado no mesmo canal no mesmo dia: manter 1 entrada.
        signature = (row["canal"].lower(), str(row["data"])[:10],
                     re.sub(r"\s+", " ", row["texto"]).lower().strip())
        if signature in seen:
            continue
        seen.add(signature)
        data.append(row)
    with temp.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(data)
    temp.replace(CSV_PATH)


def reclassify_existing() -> None:
    """Corrige classificacoes do CSV existente, preservando preco ja extraido.

    NAO reextrai preco a partir de texto truncado: isso poderia confundir
    preco promocional e historico de 90 dias que ficaram na mesma linha.
    """
    previous = load_existing()
    fixed = {}
    for row in previous.values():
        prod = detect_product(row["texto"])
        if not prod:
            continue
        cat, name, target = prod
        try:
            price = float(str(row["preco"]).replace(",", "."))
        except ValueError:
            continue
        row = dict(row)
        row.update(categoria=cat, peca=name, meta=round(target, 2),
                   diferenca_meta=round(price - target, 2),
                   avaliacao=("POTENCIAL BOA OFERTA — conferir carrinho" if price <= target
                              else "ACIMA DA META — comparar"))
        url = str(row.get("link_loja", ""))
        if url and cat == "GABINETE" and re.search(r"/(?:kit-ventoinhas|memoria-|placa-de-video)", url, re.I):
            row["avaliacao"] += " | LINK DIVERGENTE"
        fixed[row["link"] + "|" + name] = row
    # Backup anterior preservado para reverter, se necessario.
    if CSV_PATH.exists():
        backup = CSV_PATH.with_name("ofertas_backup_antes_v2.csv")
        if not backup.exists():
            backup.write_bytes(CSV_PATH.read_bytes())
    save_csv(fixed)
    print(f"CSV corrigido: {len(previous)} registros brutos processados -> "
          f"{len(list(csv.DictReader(CSV_PATH.open(encoding='utf-8-sig'))))} registros revisados.")
    print("Backup: dados/ofertas_backup_antes_v2.csv")


async def fetch_once(client: TelegramClient, cfg: dict[str, Any], limit: int, days: int) -> None:
    state = load_state()
    rows = load_existing()
    # Ignora republicacoes identicas no mesmo canal/dia inclusive no terminal.
    signatures = {(r["canal"].lower(), str(r["data"])[:10],
                   re.sub(r"\s+", " ", r["texto"]).lower().strip())
                  for r in rows.values()}
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    found = []
    for channel in cfg["canais"]:
        try:
            # get_entity resolve username publico; nao solicita entrada no grupo.
            entity = await client.get_entity(channel)
            last_id = int(state.get(channel, 0))
            max_seen = last_id
            total = 0
            async for msg in client.iter_messages(entity, limit=limit, min_id=last_id):
                if msg.date and msg.date < cutoff:
                    break
                max_seen = max(max_seen, msg.id)
                total += 1
                for offer in parse_offers(channel, msg.id, msg.date, msg.raw_text or ""):
                    key = offer["link"] + "|" + offer["peca"]
                    signature = (offer["canal"].lower(), str(offer["data"])[:10],
                                 re.sub(r"\s+", " ", offer["texto"]).lower().strip())
                    if key not in rows and signature not in signatures:
                        rows[key] = offer
                        found.append(offer)
                        signatures.add(signature)
            state[channel] = max_seen
            print(f"@{channel}: {total} mensagens novas/consultadas")
            await asyncio.sleep(1.5)
        except FloodWaitError as err:
            print(f"Limite de requisicoes do Telegram: aguarde {err.seconds}s antes de executar de novo.")
            break
        except Exception as err:
            # Alguns grupos publicos exigem associacao para ler o historico.
            # O monitor informa o erro, sem entrar automaticamente.
            print(f"Nao foi possivel ler @{channel}: {err}")
            print("Se for grupo publico, talvez voce precise entrar pelo app Telegram antes da consulta.")
            continue
    save_csv(rows)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"\n{len(found)} ofertas relevantes novas. CSV: {CSV_PATH}")
    for row in sorted(found, key=lambda r: (r["diferenca_meta"], r["data"]))[:20]:
        print(f'[{row["categoria"]}] {row["peca"]} | {brl(row["preco"])} | meta {brl(row["meta"])}')
        print(f'   {row["avaliacao"]} | cupom: {row["cupom"] or "-"} | {row["link"]}')
    if len(found) > 20:
        print(f"... e outras {len(found) - 20}; veja o CSV")


async def async_main(args: argparse.Namespace) -> None:
    cfg = load_config()
    api_id = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    if not api_id.isdigit() or not api_hash:
        raise SystemExit("Configure TELEGRAM_API_ID e TELEGRAM_API_HASH no arquivo .env (veja README).")
    # Telegram salva a sessao localmente; nunca compartilhe o arquivo .session.
    async with TelegramClient(str(HERE / "sessao_telegram"), int(api_id), api_hash) as client:
        while True:
            await fetch_once(client, cfg, args.limit, args.days)
            if not args.watch:
                break
            print(f"\nProxima consulta em {args.interval} minutos (Ctrl+C para encerrar).")
            await asyncio.sleep(args.interval * 60)


def main() -> None:
    p = argparse.ArgumentParser(description="Consulta canais publicos do Telegram por ofertas de PC AM5")
    p.add_argument("--limit", type=int, default=150, help="Maximo de mensagens por canal em cada consulta")
    p.add_argument("--days", type=int, default=7, help="Mensagens dos ultimos N dias")
    p.add_argument("--reprocess", action="store_true", help="Corrige o CSV antigo sem acessar o Telegram")
    p.add_argument("--watch", action="store_true", help="Consulta periodicamente enquanto programa estiver aberto")
    p.add_argument("--interval", type=int, default=30, help="Intervalo em minutos no modo --watch (minimo 10)")
    args = p.parse_args()
    if args.limit < 1 or args.limit > 500 or args.days < 1 or args.interval < 10:
        p.error("Use --limit entre 1 e 500; --days >= 1; --interval >= 10.")
    if args.reprocess:
        reclassify_existing()
        return
    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("\nMonitor encerrado.")


if __name__ == "__main__":
    main()
