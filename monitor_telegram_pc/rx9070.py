"""Triagem preliminar da RX 9070 XT; não confirma checkout nem compra."""
import re

RX = re.compile(r'(?<![\w])(?:(?:amd\s+)?(?:radeon\s+)?(?:rx[\s-]*)?|radeon[\s-]*)9070[\s-]*xt\b', re.I)
PRICE = re.compile(r'(?<![\w.,])(?:R\$\s*)?(\d{1,3}(?:[.,]\d{3})+(?:,\d{2})?|\d{4,5}(?:,\d{2})?)(?![\w.,])', re.I)

def price_candidates(text):
    # Remove product model, URLs, strikethrough and history before scanning.
    text = re.sub(r'https?://\S+|~~.*?~~', ' ', text, flags=re.I | re.S)
    result = []
    for line in text.splitlines():
        line = re.split(r'menor\s+pre[cç]o|mediana|hist[oó]rico|pre[cç]o\s+antigo', line, maxsplit=1, flags=re.I)[0]
        line = RX.sub(' ', line)
        for m in PRICE.finditer(line):
            before = line[max(0,m.start()-35):m.start()]
            after = line[m.end():m.end()+25]
            if not (re.match(r'R\$', m.group(0), re.I) or re.search(r'pre[cç]o|valor|por\s*$|[💰💸—]', before, re.I)
                    or re.match(r'\s*(?:no\s+pix|com\s+(?:o\s+)?cupom)', after, re.I)
                    or not (line[:m.start()].strip() or line[m.end():].strip())):
                continue
            if re.search(r'(?:\bde|antes|era|frete|cashback|sku|c[oó]digo|modelo)\s*[:\-]?\s*$', before, re.I): continue
            if re.search(r'\bfrete\s+para\s+[^:]{1,50}:\s*$', before, re.I): continue
            if re.search(r'\d+\s*[x×]\s*(?:de\s*)?(?:R\$\s*)?$', before, re.I): continue
            if re.match(r'\s*(?:gb|mhz|mm|w|parcelas?\b)', after, re.I): continue
            raw=m.group(1)
            if re.fullmatch(r'\d{1,3},\d{3}',raw): raw=raw.replace(',','')
            else: raw=raw.replace('.','').replace(',','.')
            try: value=round(float(raw)*100)
            except ValueError: continue
            if 100000 <= value <= 3000000: result.append(value)
    return sorted(set(result))

def tier(value, config):
    limits = config.get('rx9070xt_faixas', {'imperdivel':4700,'excelente':4900,'boa_compra':5100,'avaliar':5300})
    for key,label in [('imperdivel','IMPERDÍVEL'),('excelente','EXCELENTE'),('boa_compra','BOA COMPRA'),('avaliar','AVALIAR')]:
        if value <= round(float(limits[key])*100): return label
    return 'PREÇO ALTO'
