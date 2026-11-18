"""Validação compartilhada da identidade do produto."""
import re
RX = re.compile(r'(?i)(?<!\d)(?:rx[\s-]*)?9070[\s-]*xt\b')
EXCLUDED = re.compile(r'(?i)\b(?:usad[oa]|recondicionad[oa]|refurbished|used|open[ -]?box|seminov[oa]|computador|desktop|pc gamer|waterblock|backplate|suporte|cabo|dissipador)\b')


def valid_gpu(name):
    return isinstance(name, str) and bool(RX.search(name)) and not EXCLUDED.search(name) and not re.search(r'(?i)\b(?:8|12|24|32)\s*gb\b', name)



def valid_ram(name):
    if not isinstance(name, str) or not re.search(r'(?i)\bddr5\b', name):
        return False
    if re.search(r'(?i)\b(?:sodimm|so-dimm|notebook|usad[oa]|recondicionad[oa]|computador|pc gamer|placa[- ]m[aã]e)\b', name):
        return False
    kits = re.findall(r'(?i)(\d+)\s*[x×]\s*(\d+)\s*gb', name)
    if kits:
        return all(int(n) in (1, 2) and int(cap) == 16 for n, cap in kits)
    return bool(re.search(r'(?i)\b16\s*gb\b', name)) and not re.search(r'(?i)\b(?:32|64|8)\s*gb\b', name)
