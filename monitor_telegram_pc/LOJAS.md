# Monitoramento direto de lojas

O monitor de lojas acompanha URLs específicas em paralelo ao Telegram. A versão inicial aceita **dados públicos em JSON-LD Schema.org** que correspondam ao produto configurado. Ela não compra produtos, não entra em contas, não segue redirecionamentos e não contorna CAPTCHA ou bloqueios.

## Antes de ativar

O arquivo `produtos_lojas.json` já contém os dois produtos informados:

| ID | Produto | Intervalo por URL | Meta |
|---|---|---:|---:|
| `kabum-asrock-9070xt-challenger` | ASRock RX 9070 XT Challenger | 180 segundos | R$ 5.100 |
| `pichau-xfx-9070xt-swift-white` | XFX RX 9070 XT Swift White | 180 segundos | R$ 5.100 |

A URL da Pichau foi salva sem o parâmetro de rastreamento; a identificação do produto permanece a mesma. O processador já foi comprado e não gera alertas. A Terabyte é suportada como domínio, mas não há URL cadastrada.

Situação verificada durante o desenvolvimento:

| Loja | Situação e limites |
|---|---|
| KaBuM! | URL da ASRock cadastrada. O proxy do ambiente negou a conexão ao `robots.txt`; os termos e o formato real da página não foram verificados. |
| Pichau | URL da XFX cadastrada. O proxy do ambiente negou a conexão ao `robots.txt`; os termos e o formato real da página não foram verificados. |
| Terabyte | Nenhuma URL cadastrada. O proxy do ambiente negou a conexão ao `robots.txt`; os termos e o formato das páginas não foram verificados. |

Essa resposta veio do proxy da máquina de desenvolvimento, portanto **não demonstra que as lojas bloqueariam seu notebook**. O funcionamento com o HTML real de cada loja ainda precisa ser validado somente depois de confirmadas as condições de acesso.

**As duas lojas começam com `termos_verificados: false`**, pois os termos e o acesso real não puderam ser verificados no ambiente de desenvolvimento. Assim, nenhuma página é consultada automaticamente por padrão. Para cada loja, verifique sua política oficial e confirme que a consulta pública automatizada desse produto é permitida. Somente então, em `produtos_lojas.json`, altere a respectiva entrada para:

```json
"kabum": {"termos_verificados": true}
```

Faça o mesmo para `pichau` apenas se tiver verificado a política dessa loja. Isso não substitui a verificação automática do `robots.txt`: se ele proibir o acesso, estiver indisponível ou retornar um desafio, o produto não é consultado. Em caso de dúvida sobre a política, mantenha `false`. Reinicie o monitor após editar o arquivo.

Se a loja permitir um método oficial de consulta com acesso autorizado, ele é preferível. Esta versão ainda não integra nenhuma API oficial de varejista, pois suas condições e acesso não foram confirmados. Também não utiliza Playwright: ele poderia carregar recursos adicionais cuja permissão ainda não foi verificada.

## Iniciar e consultar estado

Após instalar o projeto e configurar o bot Telegram conforme `LEIA-ME.md`, execute:

```powershell
.\.venv\Scripts\python.exe monitor.py --watch
```

O monitor de lojas roda em paralelo ao Telegram quando o arquivo `produtos_lojas.json` existe. A autenticação e as sessões anteriores do Telegram permanecem as mesmas. Para somente consultar as lojas, sem iniciar Telethon:

```powershell
.\.venv\Scripts\python.exe monitor.py --lojas --watch
```

Para consultar uma vez sem enviar mensagens, usando um banco de teste independente:

```powershell
.\.venv\Scripts\python.exe monitor.py --db .\dados\teste-lojas.sqlite3 --lojas --no-notify
```

`--no-notify` também registra os resultados nesse banco, mas não enfileira alertas. Esse comando só faz conexões se os termos da loja tiverem sido marcados como verificados e o `robots.txt` permitir. Um resultado `termos_nao_verificados` significa que **nenhum pedido à loja foi feito**.

Pare o monitor antes de consultar o banco principal, pois uma única instância o utiliza por vez:

```powershell
.\.venv\Scripts\python.exe monitor.py --lojas-status
.\.venv\Scripts\python.exe monitor.py --lojas-historico kabum-asrock-9070xt-challenger
```

O status mostra SKU, meta, intervalo, última tentativa, último sucesso, estado de estoque e últimos preços conhecidos. O histórico lista todas as tentativas, inclusive falhas. Alterar URL, SKU ou termos de identificação inicia uma identidade separada para evitar misturar modelos: os registros anteriores permanecem no banco, mas `--lojas-historico` consulta a identidade atualmente configurada. `logs/monitor.log` contém um resumo. Não apague `dados/monitor.sqlite3`: ele também contém compras, cursores e alertas do Telegram. Faça backup somente com o monitor parado; não remova arquivos `-wal` ou `-shm` enquanto ele está rodando.

## Cadastrar produtos

Adicione um objeto na lista `produtos` de `produtos_lojas.json`. Por exemplo, preencha os campos entre colchetes com dados de uma página **real** antes de salvar:

```json
{
  "id": "kabum-ssd-exemplo",
  "nome": "SSD NVMe 1TB MODELO-REAL",
  "loja": "kabum",
  "categoria": "SSD",
  "url": "https://www.kabum.com.br/produto/ID-REAL/ENDERECO-REAL",
  "sku": "SKU-REAL",
  "sku_loja": "ID-REAL",
  "termos_identificacao": ["NVMe", "1TB", "MODELO-REAL"],
  "meta_centavos": 70000,
  "intervalo_segundos": 600,
  "cupons": [],
  "ativo": true
}
```

O exemplo é um **molde**, não uma URL de produto verificada. Use SKU e ID da loja reais; se não estiverem disponíveis, omita os campos opcionais `sku` e `sku_loja`. Os termos devem identificar o produto de forma suficientemente específica. A extração exige que o produto no JSON-LD contenha esses termos; um SKU informado pela página que não corresponda a nenhum dos IDs configurados bloqueia a leitura para evitar confundir variantes. Para a RX 9070 XT, a família e o modelo também precisam coincidir.

Aceitamos até 100 URLs HTTPS diretas da KaBuM!, Pichau e Terabyte, sem login, cookies ou parâmetros com credenciais. Encurtadores não são seguidos automaticamente. O mínimo configurável por produto é de 120 segundos; os padrões são 180 segundos para GPU e 600 segundos para outras categorias. A política da loja, o `robots.txt` ou a quantidade de URLs podem exigir uma espera maior. As duas GPUs cadastradas usam 180 segundos. Para suspender um item sem apagá-lo, use `"ativo": false`. Fontes devem indicar 850 W e placas-mãe devem identificar AM5 no cadastro e no nome da página. Peças já registradas como compradas continuam tendo histórico de loja, mas não geram novos alertas.

O arquivo `config.json` aceita `"monitoramento_lojas": {"ativado": false}` para desligar a integração. Também pode selecionar outro catálogo com `"arquivo": "produtos_lojas.local.json"`. A configuração antiga sem esse bloco continua funcionando com o nome padrão `produtos_lojas.json`, se o arquivo existir. Para preservar suas personalizações ao atualizar o projeto, faça uma cópia `produtos_lojas.local.json` e aponte `arquivo` para ela. Arquivos `*.local.json` são ignorados pelo Git.

## O que os alertas significam

- O preço da página só é aceito quando há um `Product` e um `Offer` compatíveis e sem conflito nos dados JSON-LD. Sem evidência de PIX/cartão, o preço permanece apenas como **preço público**, com pagamento não identificado.
- Um preço PIX ou cartão só é separado quando a própria especificação estruturada o identifica. Parcelas, preço anterior e dados históricos não são usados como preço atual. Múltiplos preços ou vendedores ambíguos produzem falha de extração, sem aviso de queda.
- Cupons em `cupons` ou em uma mensagem recente do Telegram que contenha a URL direta configurada são apenas **indicados, não validados**. Eles não são aplicados nem testados automaticamente. Se houver preço público e um novo cupom indicado, o bot pode enviar um alerta de possível promoção para conferir manualmente.
- Frete é registrado apenas se os dados públicos estruturados indicarem expressamente um valor para Belém/PA. CEP e conta não são usados. Caso contrário, ele fica desconhecido. O preço final com frete também fica desconhecido nesta versão.
- A primeira observação dentro da meta, uma queda significativa, retorno de estoque dentro da meta, alteração relevante de vendedor ou frete e um novo cupom indicado podem gerar alerta. O percentual padrão de queda é 5%; ajuste `queda_minima_percentual` no catálogo. Um frete só é considerado alterado quando ambos os valores estão explícitos e diferem pelo menos R$ 10.
- A mesma observação não gera alerta repetido; o produto voltar a ficar caro e depois cair novamente é uma nova oportunidade. O histórico registra cada tentativa independentemente do alerta. Mensagens repetidas do Telegram só podem antecipar a consulta até o próximo horário permitido pelo intervalo e pelo backoff; links encurtados não são resolvidos.
- Quando o Telegram já enviou com confirmação uma oferta do mesmo produto, preço e URL direta, a observação igual da loja não gera um segundo alerta. Em caso de envio pendente ou incerto, a supressão não é aplicada para não esconder uma possível promoção; pode haver duplicação. URLs encurtadas ou modelos não identificados com segurança também não são correlacionados.
- O alerta identifica o horário local da máquina e sua fonte, mas os dados estruturados podem estar desatualizados. Confirme a oferta, o vendedor, o cupom, o frete e o estoque no checkout. O sistema não registra compra automaticamente.

Uma resposta HTTP 403 ou desafio anti-bot pausa consultas à loja por pelo menos 24 horas. HTTP 429 obedece `Retry-After` quando informado e aplica espera crescente. A falha não altera o último preço válido nem é interpretada como estoque esgotado. Há uma consulta de cada vez por loja, intervalo mínimo de 10 segundos entre requisições e pequena variação aleatória; regras `Crawl-delay` e `Request-rate` mais restritivas são respeitadas. O `robots.txt` é atualizado ao menos a cada 24 horas quando necessário.

O Gemini continua disponível para os alertas anteriores do Telegram. Alertas das lojas não aguardam o Gemini, para preservar a rapidez e evitar que texto gerado apresente condições de cupom ou frete como confirmadas.
