# Monitor de promoções para seu PC AM5

Ferramenta **somente de leitura**, em Python, para monitorar ofertas postadas em canais **públicos** do Telegram. O script não entra em grupos, não envia mensagens e não faz compras.

## 1. Preparar o Windows (PowerShell)

Instale Python 3.11+ caso ainda não tenha. Dentro desta pasta:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Se o PowerShell bloquear a ativação, você pode simplesmente usar `\.venv\Scripts\python.exe` no lugar de `python` em todos os comandos.

## 2. Gerar suas credenciais (não mande para o ChatGPT)

1. Abra **https://my.telegram.org** e faça login com sua própria conta.
2. Clique em **API Development Tools** e crie um aplicativo pessoal.
3. Copie `api_id` e `api_hash` **somente para o seu computador**.
4. Renomeie `.env.exemplo` para `.env` e substitua os valores. Não compartilhe `.env` nem `sessao_telegram.session`.

O Telegram solicitará autenticação na primeira execução. Dependendo da sua conta, pode pedir código e senha da verificação em duas etapas.

## 3. Canais configurados

O arquivo `config.json` **ja vem preenchido** com os dois enderecos informados:

```json
{
  "canais": [
    "https://t.me/pcbuildwizard",
    "https://t.me/LaPromotion"
  ]
}
```

- `@LaPromotion` e um **canal publico** que publica ofertas gerais, inclusive de hardware.
- `@pcbuildwizard` e um **grupo publico** de recomendacoes de hardware. Dependendo das permissoes do Telegram, pode ser necessario que voce participe do grupo no aplicativo para conseguir ler seu historico por API. O script **nao entra em nenhum grupo automaticamente**.

Para adicionar mais canais publicos, basta inserir outros links no vetor `canais` e separar com virgulas.

**Nao envie links privados de convite, tokens, arquivos de sessao ou credenciais ao ChatGPT.**

## 4. Executar

Uma consulta das últimas 150 mensagens por canal, limitadas aos últimos 7 dias:

```powershell
python monitor.py
```

Uma consulta maior:

```powershell
python monitor.py --limit 300 --days 14
```

Monitorar a cada 30 minutos, **enquanto o notebook e o programa estiverem ligados**:

```powershell
python monitor.py --watch --interval 30
```

Para encerrar: `Ctrl+C`.

## 5. Resultado

O script gera `dados/ofertas.csv` que pode ser aberto no Excel, ordenado por preço e enviado ao ChatGPT para a análise final. O script inclui modelo, preço localizado, meta, cupom, URL original, data e texto do anúncio. Evita duplicatas por mensagem e modelo entre execuções. Inclui `link_loja` quando existe URL visível no texto.

**Atenção**: o classificador é heurístico. Mensagens com várias peças sao separadas por linha quando possivel; anúncios com formatos pouco usuais podem exigir verificação manual. Parcelas nao sao confundidas com o preço total. Os preços podem ser de PIX, cupom vencido, região específica ou estoque esgotado. O monitor não confirma o checkout. A etiqueta “POTENCIAL BOA OFERTA” não é recomendação definitiva de compra.

## 6. Segurança

- Nunca compartilhe `api_hash`, código de login, senha 2FA ou arquivo `.session`.
- Faça login apenas na interface oficial do Telegram e no programa local que você decidiu executar.
- A pasta `dados/` contém textos e links das mensagens processadas: revise antes de compartilhar.
- Use apenas canais públicos aos quais você está autorizado a acessar; evite consultas excessivamente frequentes.

## 7. Metas iniciais do projeto

- RX 9070 XT — até R$ 4.700
- RX 9070 — até R$ 4.200
- Ryzen 7 7800X3D — até R$ 1.700
- Ryzen 7 9800X3D — até R$ 2.050
- ASUS B650M-AYW — até R$ 680
- DDR5 1×16 GB — até R$ 1.500
- XPG Core Reactor 850W — até R$ 490

Essas metas são editáveis na constante `RULES` do arquivo `monitor.py`. Não representam garantia de disponibilidade, nem preço histórico comprovado.

## 8. Detalhes importantes sobre os preços

- Este projeto **nao valida cupons e estoque automaticamente no carrinho**, nem afirma que o preco continua ativo.
- A oferta mostrada por um canal/agregador pode estar diferente do preco real do vendedor; **confira o preco final, frete para Belem e garantia antes de comprar**.
- O programa nao compra nada, nao altera sua conta e nao transmite os dados da sessao ao ChatGPT.
- A consulta inicial pode levar alguns instantes; nao execute consultas em frequencia muito alta.
