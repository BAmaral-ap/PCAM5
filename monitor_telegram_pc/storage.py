"""Persistência local: mensagens, ofertas, fila de alertas e compras manuais.

Valores financeiros são inteiros em centavos. CSVs legados são arquivados como
dados de origem, sem presumir pagamento, precisão do modelo ou preço confirmado.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any


LEGACY_FIELDS = ["data", "canal", "peca", "categoria", "preco", "meta",
                 "diferenca_meta", "avaliacao", "cupom", "link", "link_loja", "texto"]
EXTRA_FIELDS = ["message_id", "pix_centavos", "cartao_centavos", "anunciado_centavos",
                "parcelas", "parcelamento_sem_juros", "total_parcelado_sem_juros_centavos",
                "loja", "condicoes", "verificar", "modelo_exato", "origem"]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("Datas de mensagens novas precisam informar o fuso horário.")
        return value.astimezone(timezone.utc).isoformat()
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Datas de mensagens novas precisam informar o fuso horário.")
    return parsed.astimezone(timezone.utc).isoformat()


def _legacy_cents(value: Any) -> int | None:
    # Legacy export used machine decimal values, occasionally BR comma decimals.
    s = str(value or "").strip().replace("R$", "").strip()
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        d = Decimal(s)
        if not d.is_finite() or d <= 0:
            return None
        return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return None


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                channel TEXT NOT NULL, message_id INTEGER NOT NULL,
                date TEXT NOT NULL, text TEXT NOT NULL,
                PRIMARY KEY(channel, message_id)
            );
            CREATE TABLE IF NOT EXISTS offers (
                channel TEXT NOT NULL, message_id INTEGER NOT NULL,
                model TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(channel, message_id, model),
                FOREIGN KEY(channel,message_id) REFERENCES messages(channel,message_id)
            );
            CREATE TABLE IF NOT EXISTS cursors (
                channel TEXT PRIMARY KEY, last_id INTEGER NOT NULL,
                initialized_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dedupe_key TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
                source_date TEXT NOT NULL, created_at TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending'
                    CHECK(state IN ('pending','sending','sent','uncertain','expired')),
                attempts INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS imported_rows (
                id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
                row_number INTEGER NOT NULL, row_hash TEXT NOT NULL,
                raw_payload TEXT NOT NULL, processed_payload TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                UNIQUE(source,row_number,row_hash)
            );
        """)

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        self.db.close()

    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key: str, value: Any) -> None:
        with self.db:
            self.db.execute("INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, _json(value)))

    def get_cursor(self, channel: str) -> int | None:
        row = self.db.execute("SELECT last_id FROM cursors WHERE channel=?", (channel.lower(),)).fetchone()
        return row[0] if row else None

    def _set_cursor(self, channel: str, message_id: int) -> None:
        if not isinstance(message_id, int) or message_id < 0:
            raise ValueError("Cursor precisa ser um inteiro não negativo.")
        self.db.execute("""INSERT INTO cursors VALUES (?,?,?)
            ON CONFLICT(channel) DO UPDATE SET last_id=MAX(cursors.last_id,excluded.last_id)""",
                        (channel.lower(), message_id, _now()))

    def set_cursor(self, channel: str, message_id: int) -> None:
        with self.db:
            self._set_cursor(channel, message_id)

    def record_message(self, channel: str, msg_id: int, date: Any, text: str,
                       offers: list[dict], notifications: list[dict] | None = None) -> bool:
        """Commit da mensagem, ofertas, notificações e cursor, em uma transação.

        Retorna False se a mensagem já foi registrada. O consumidor deve consultar
        mensagens em ordem crescente; um cursor não representa lacunas recuperadas.
        """
        channel = channel.lower()
        timestamp = _date(date)
        with self.db:
            inserted = self.db.execute("INSERT OR IGNORE INTO messages VALUES (?,?,?,?)",
                                       (channel, msg_id, timestamp, text)).rowcount
            if not inserted:
                return False
            for offer in offers:
                payload = dict(offer, canal=channel, message_id=msg_id, data=timestamp)
                self.db.execute("INSERT INTO offers VALUES (?,?,?,?)",
                                (channel, msg_id, offer["peca"], _json(payload)))
            for notification in notifications or []:
                self._enqueue(notification["key"], notification["payload"], notification.get("date", timestamp))
                if notification.get("fingerprint"):
                    self.db.execute("""INSERT INTO metadata VALUES (?,?)
                        ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                                    (notification["fingerprint"], _json(_now())))
            self._set_cursor(channel, msg_id)
        return True

    def offers(self, include_imported: bool = False) -> list[dict]:
        result = [json.loads(r[0]) for r in self.db.execute(
            "SELECT payload FROM offers ORDER BY channel,message_id,model")]
        if include_imported:
            result.extend(self.imported_offers())
        return result

    def imported_offers(self) -> list[dict]:
        # Keep archive rows individually; deduplicate observations only in exports.
        return [json.loads(r[0]) for r in self.db.execute(
            "SELECT processed_payload FROM imported_rows ORDER BY id")]

    def _enqueue(self, key: str, payload: str, source_date: Any) -> int | None:
        if not key or not isinstance(payload, str) or not payload:
            raise ValueError("Notificação precisa de chave e texto.")
        cur = self.db.execute("""INSERT OR IGNORE INTO notifications
            (dedupe_key,payload,source_date,created_at,updated_at) VALUES (?,?,?,?,?)""",
                              (key, payload, _date(source_date), _now(), _now()))
        return cur.lastrowid if cur.rowcount else None

    def enqueue_notification(self, dedupe_key: str, payload: str, source_date: Any) -> int | None:
        with self.db:
            return self._enqueue(dedupe_key, payload, source_date)

    def pending_notifications(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM notifications WHERE state='pending' ORDER BY id")]

    def notifications(self, state: str | None = None) -> list[dict]:
        if state is None:
            rows = self.db.execute("SELECT * FROM notifications ORDER BY id")
        else:
            rows = self.db.execute("SELECT * FROM notifications WHERE state=? ORDER BY id", (state,))
        return [dict(r) for r in rows]

    def claim_notification(self, notification_id: int) -> bool:
        with self.db:
            return bool(self.db.execute("""UPDATE notifications SET state='sending',
                attempts=attempts+1,updated_at=? WHERE id=? AND state='pending'""",
                                        (_now(), notification_id)).rowcount)

    def finish_notification(self, notification_id: int, state: str) -> None:
        if state not in {"sent", "pending", "uncertain", "expired"}:
            raise ValueError("Estado de notificação inválido.")
        with self.db:
            result = self.db.execute("UPDATE notifications SET state=?,updated_at=? WHERE id=? AND state='sending'",
                                    (state, _now(), notification_id))
            if result.rowcount != 1:
                raise ValueError("A notificação precisa estar reservada antes de finalizar.")

    def recover_notifications(self) -> int:
        """Não reenviar após falha entre enviar e confirmar: entrega é incerta."""
        with self.db:
            return self.db.execute("UPDATE notifications SET state='uncertain',updated_at=? WHERE state='sending'",
                                   (_now(),)).rowcount

    def add_purchase(self, purchase: dict) -> int:
        item = dict(purchase)
        category = item.get("categoria", item.get("category"))
        if not category or not item.get("modelo"):
            raise ValueError("Informe categoria e modelo da compra.")
        item["categoria"] = category
        total = item.get("total_centavos")
        if type(total) is not int or total <= 0:
            raise ValueError("Valor efetivamente pago precisa ser positivo, em centavos inteiros.")
        payment = item.get("pagamento")
        if payment not in {"pix", "avista", "cartao"}:
            raise ValueError("Pagamento precisa ser pix, avista ou cartao.")
        count = item.setdefault("parcelas", 1)
        paid = item.setdefault("parcelas_pagas", 0)
        if type(count) is not int or not 1 <= count <= min(120, total) or type(paid) is not int or not 0 <= paid <= count:
            raise ValueError("Número de parcelas ou parcelas pagas inválido.")
        if payment in {"pix", "avista"} and (count != 1 or paid != 0):
            raise ValueError("Compra à vista não possui parcelamento nem parcelas de cartão pagas.")
        if payment == "cartao":
            month = item.get("primeira_parcela", "")
            if not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", month):
                raise ValueError("Informe primeira_parcela no formato AAAA-MM.")
            if not 1 <= int(month[:4]) <= 9999:
                raise ValueError("Ano da primeira parcela inválido.")
        item.pop("id", None)
        with self.db:
            cur = self.db.execute("INSERT INTO purchases(payload,created_at) VALUES (?,?)", (_json(item), _now()))
            return int(cur.lastrowid)

    def purchases(self) -> list[dict]:
        return [dict(json.loads(r["payload"]), id=r["id"]) for r in self.db.execute("SELECT * FROM purchases ORDER BY id")]

    def record_installment(self, purchase_id: int) -> dict:
        """Confirma manualmente uma parcela; passagem do tempo não libera limite."""
        with self.db:
            # Acquire the writer lock before reading so concurrent confirmations
            # cannot overwrite one another.
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT payload FROM purchases WHERE id=?", (purchase_id,)).fetchone()
            if row is None:
                raise ValueError("Compra não encontrada.")
            item = json.loads(row[0])
            if item["pagamento"] != "cartao" or item["parcelas_pagas"] >= item["parcelas"]:
                raise ValueError("Esta compra não possui parcela de cartão pendente.")
            item["parcelas_pagas"] += 1
            self.db.execute("UPDATE purchases SET payload=? WHERE id=?", (_json(item), purchase_id))
        return dict(item, id=purchase_id)

    def import_csv(self, path: str | Path, reprocess: bool = False) -> dict:
        """Importação sem alertas/cursor. Guarda todos os campos e duplicatas originais.

        Reprocessar revisa somente classificação. Textos antigos foram truncados e
        perderam quebras de linha: nunca se recalcula seu preço automaticamente.
        """
        path = Path(path).resolve()
        counts = {"lidos": 0, "importados": 0, "reprocessados": 0}
        with path.open(encoding="utf-8-sig", newline="") as file, self.db:
            reader = csv.DictReader(file)
            if not reader.fieldnames or not {"peca", "preco", "texto"}.issubset(reader.fieldnames):
                raise ValueError("CSV precisa conter as colunas peca, preco e texto.")
            for index, raw in enumerate(reader, 2):
                counts["lidos"] += 1
                original = _json(raw)
                digest = hashlib.sha256(original.encode()).hexdigest()
                payload = dict(raw)
                payload.update(origem="csv_legado", modelo_exato=False,
                               anunciado_centavos=_legacy_cents(raw.get("preco")),
                               pix_centavos=None, cartao_centavos=None, parcelas=None,
                               verificar=["CSV legado: preço, pagamento, modelo e fuso precisam de verificação; texto pode estar truncado."],
                               condicoes=[], arquivo_origem=str(path), linha_origem=index)
                if reprocess:
                    try:
                        from .parsing import detect_product
                    except ImportError:
                        from parsing import detect_product
                    product = detect_product(raw.get("texto", ""))
                    if product:
                        category, model, target = product
                        payload.update(categoria=category, peca=model, meta=target)
                        cents = payload["anunciado_centavos"]
                        payload["diferenca_meta"] = cents / 100 - float(target) if cents is not None and target is not None else ""
                    payload["verificar"].append("Classificação revista; preço original preservado.")
                cur = self.db.execute("""INSERT OR IGNORE INTO imported_rows
                    (source,row_number,row_hash,raw_payload,processed_payload,imported_at)
                    VALUES (?,?,?,?,?,?)""", (str(path), index, digest, original, _json(payload), _now()))
                counts["importados"] += cur.rowcount
                if reprocess:
                    self.db.execute("""UPDATE imported_rows SET processed_payload=?
                        WHERE source=? AND row_number=? AND row_hash=?""", (_json(payload), str(path), index, digest))
                    counts["reprocessados"] += 1
        return counts

    def export_csv(self, path: str | Path) -> int:
        """Exportação derivada; nunca sobrescreve uma origem CSV importada."""
        path = Path(path).resolve()
        if self.db.execute("SELECT 1 FROM imported_rows WHERE source=? LIMIT 1", (str(path),)).fetchone():
            raise ValueError("Escolha um CSV de saída diferente dos arquivos originais importados.")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Prefer live observations over historical CSV copies of the same offer.
        unique = {}
        for offer in self.imported_offers() + self.offers():
            key = (offer.get("link"), offer.get("peca"))
            if not key[0]:
                key = (offer.get("arquivo_origem"), offer.get("linha_origem"), offer.get("peca"))
            unique[key] = offer
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=LEGACY_FIELDS + EXTRA_FIELDS, extrasaction="ignore")
            writer.writeheader()
            for offer in sorted(unique.values(), key=lambda r: str(r.get("data", "")), reverse=True):
                row = {k: _json(v) if isinstance(v, (list, dict)) else v for k, v in offer.items()}
                # Spreadsheet-safe output; raw text is retained unchanged in SQLite.
                row = {k: "'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v
                       for k, v in row.items()}
                writer.writerow(row)
        temporary.replace(path)
        return len(unique)

    def history(self, model: str, days: int | None = None) -> dict:
        if days is not None and (type(days) is not int or days <= 0):
            raise ValueError("Janela do histórico precisa ser positiva.")
        cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days else None
        points: dict[str, list] = {"pix": [], "cartao": [], "nao_informado": []}
        dates = []
        uncertain = 0
        occurrences = 0
        for offer in self.offers():
            if offer.get("peca") != model:
                continue
            date = datetime.fromisoformat(offer["data"])
            if cutoff and date < cutoff:
                continue
            if not offer.get("modelo_exato", False) or offer.get("verificar"):
                uncertain += 1
                continue
            valid = False
            for payment, key in (("pix", "pix_centavos"), ("cartao", "cartao_centavos"),
                                 ("nao_informado", "anunciado_centavos")):
                if payment == "nao_informado" and (offer.get("pix_centavos") or offer.get("cartao_centavos")):
                    continue
                cents = offer.get(key)
                if type(cents) is int and cents > 0:
                    points[payment].append({"data": offer["data"], "centavos": cents,
                                            "link": offer.get("link", ""), "canal": offer["canal"]})
                    valid = True
            if valid:
                dates.append(date)
                occurrences += 1
        result = {"modelo": model, "ocorrencias": occurrences, "incertos_excluidos": uncertain,
                  "importados_nao_verificados": sum(r.get("peca") == model for r in self.imported_offers()),
                  "inicio": min(dates).isoformat() if dates else None,
                  "fim": max(dates).isoformat() if dates else None,
                  "dias_observados": (max(dates) - min(dates)).total_seconds() / 86400 if dates else 0,
                  "pagamentos": {}}
        for payment, observations in points.items():
            values = [p["centavos"] for p in observations]
            result["pagamentos"][payment] = {
                "ocorrencias": len(values), "menor_centavos": min(values) if values else None,
                "media_centavos": statistics.mean(values) if values else None,
                "mediana_centavos": statistics.median(values) if values else None,
                "observacoes": sorted(observations, key=lambda p: p["data"]),
                "melhores": sorted(observations, key=lambda p: (p["centavos"], p["data"]))[:10],
            }
        return result
