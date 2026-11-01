"""Configuration shared by online and offline commands; no credential values."""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_config(path: Path | str | None = None) -> dict:
    from parsing import normalize_channel
    cfg = json.loads(Path(path or HERE / 'config.json').read_text(encoding='utf-8'))
    channels = cfg.get('canais', [])
    if not isinstance(channels, list) or not channels:
        raise ValueError('Configure pelo menos um canal público em canais.')
    cfg['canais'] = list(dict.fromkeys(normalize_channel(c) for c in channels))
    for name in ('pix', 'cartao'):
        amount = cfg['orcamento'][name]
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount < 0:
            raise ValueError('Orçamento deve conter valores não negativos.')
    for name in ('intervalo_minutos', 'idade_maxima_alerta_minutos', 'deduplicacao_horas'):
        value = cfg.get(name, 2 if name == 'intervalo_minutos' else 24)
        if not isinstance(value, (int, float)) or value <= 0:
            raise ValueError(f'{name} deve ser positivo.')
    return cfg
