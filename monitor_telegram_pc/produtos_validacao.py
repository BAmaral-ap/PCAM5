"""Validação compartilhada da identidade do produto."""
import re
RX = re.compile(r'(?i)(?<!\d)(?:rx[\s-]*)?9070[\s-]*xt\b')
EXCLUDED = re.compile(r'(?i)\b(?:usad[oa]|recondicionad[oa]|refurbished|used|open[ -]?box|seminov[oa]|computador|desktop|pc gamer|waterblock|backplate|suporte|cabo|dissipador)\b')


def valid_gpu(name):
    return isinstance(name, str) and bool(RX.search(name)) and not EXCLUDED.search(name) and not re.search(r'(?i)\b(?:8|12|24|32)\s*gb\b', name)

