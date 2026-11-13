# Descoberta de anúncios RX 9070 XT

A expansão usa o mesmo SQLite, agendador de lojas, controle por domínio e fila Telegram. Não altera compras, sessões, canais ou metas existentes. As faixas anteriores (IMPERDÍVEL, EXCELENTE, BOA COMPRA, AVALIAR) continuam configuráveis; os anúncios descobertos usam R$ 5.300 como limite inicial de triagem. O preço inclui frete quando há indicação pública específica para Belém.

## Executar

Após atualizar os arquivos Python, na pasta `monitor_telegram_pc`:

```powershell
.\.venv\Scripts\python.exe monitor.py --watch --descobrir
```

O parâmetro `--descobrir` funciona com o seu config antigo, sem sobrescrevê-lo. Para somente lojas e descoberta: `--lojas --watch --descobrir`. Para executar um ciclo sem enviar alertas: `--lojas --descobrir --no-notify`. Um banco de testes separado pode ser selecionado com `--db .\dados\teste-descoberta.sqlite3`.

Com o monitor parado, consulte:

```powershell
.\.venv\Scripts\python.exe monitor.py --descoberta-status
.\.venv\Scripts\python.exe monitor.py --lojas-status
.\.venv\Scripts\python.exe monitor.py --lojas-historico ID-DO-ANUNCIO
```

O relatório distingue URLs candidatas, lojas pendentes, falhas de validação e produtos monitorados. `monitorando` significa que a página passou pela validação, e não que seu preço continua atual. Consulte `ultimo_status`, `ultimo_sucesso` e histórico para saber a situação da última consulta. Falhas não viram preços nem estoque esgotado.

## Fontes e acesso

Há três adaptadores independentes: sitemaps públicos, RSS/Atom e APIs oficiais Brave Search / Google Custom Search JSON API. Não há scraping de resultados de buscadores.

| Fonte | Configuração e situação nesta entrega |
|---|---|
| Telegram existente | URLs diretas em anúncios RX 9070 XT alimentam automaticamente a fila. Encurtadores ficam pendentes; não há resolução automática. |
| Sitemaps das lojas | Sem chave/cobrança de API. Descoberta de endereços declarados no robots.txt, somente após revisão das políticas da loja. Nenhum sitemap de produção foi validado neste ambiente. |
| RSS/Atom | Gratuito quando publicado com autorização. É necessário informar uma URL de feed autorizado, não cada URL de produto. Nenhum feed de terceiros foi presumido autorizado. |
| Brave Search | Adaptador implementado; exige chave e disponibilidade de plano/cota. Desativado por padrão; gratuidade não garantida. |
| Google Custom Search | Adaptador implementado; exige chave, identificador do buscador e serviço disponível para a conta. Desativado por padrão; elegibilidade, cotas e cobrança precisam ser verificadas. |

O ambiente de desenvolvimento tem política de rede restrita, sem domínios dessas lojas/buscadores liberados e sem chaves de busca. Por isso a cobertura de produção **não foi comprovada**. Os testes exercitam múltiplas fontes com respostas simuladas. Não há integração paga ativada automaticamente.

KaBuM!, Pichau, Terabyte, GK InfoStore, Patoloco, WAZ, Amazon Brasil, Mercado Livre, Magazine Luiza e Casas Bahia têm domínios reconhecidos. Isso **não comprova acesso, termos, reputação de vendedor ou suporte do HTML real** de nenhuma delas. Todas precisam de acesso permitido e dados estruturados compatíveis. Marketplaces também precisam identificar o vendedor. Não foram implementadas APIs comerciais específicas dessas lojas nem automação de login/carrinho.

Em `produtos_lojas.json`, mantenha as entradas existentes. Para uma loja cuja política você tenha verificado, configure, por exemplo:

```json
"kabum": {"termos_verificados": true, "descobrir": true}
```

Isso permite consultar robots.txt e os sitemaps declarados. O robô ainda respeita as proibições, intervalos e bloqueios. Se a loja publicar outro sitemap autorizado, use `"sitemaps": ["https://DOMINIO/sitemap-publico.xml"]`. Não habilite uma loja sem verificar sua política. Uma loja inacessível não interrompe as demais.

Para um novo domínio confiável, acrescente uma entrada em `lojas` com `dominios`, `confiavel: true` e `termos_verificados: true`. O domínio deve ser público, sem protocolo ou caminho. A descoberta passa a cadastrar seus produtos automaticamente após essa revisão por loja. Domínios desconhecidos são registrados como `loja_pendente`, sem consultas: resultados de busca não estabelecem confiança comercial ou autorização de acesso.

## Configuração opcional em config.json

```json
"descoberta": {
  "ativado": true,
  "intervalo_produto_segundos": 1200,
  "destino": {"cidade": "Belém", "uf": "PA", "cep": null},
  "urls_desativadas": [],
  "fontes": [
    {"id": "brave", "tipo": "brave", "ativado": false, "custos_autorizados": false, "intervalo_segundos": 7200},
    {"id": "google", "tipo": "google_cse", "ativado": false, "custos_autorizados": false, "intervalo_segundos": 7200}
  ]
}
```

As APIs só são chamadas após habilitar a fonte e autorizar seu plano/cota (`custos_autorizados`). Chaves ficam exclusivamente no `.env`: `BRAVE_SEARCH_API_KEY`, ou `GOOGLE_SEARCH_API_KEY` e `GOOGLE_SEARCH_CX`. O código não registra as chaves nem mensagens de erro HTTP com URLs autenticadas. Confira preços e limites nas páginas oficiais: https://api-dashboard.search.brave.com/ e https://developers.google.com/custom-search/v1/overview .

Feeds podem ser adicionados em `fontes` com `id`, `tipo: "feed"`, `url`, `loja`, `ativado: true`; o host precisa constar na política autorizada. Preços de feeds e snippets de busca não geram alertas: o anúncio é consultado e validado primeiro.

O CEP é reservado na configuração para integração futura; esta versão não consulta frete por CEP. Usa apenas dados estruturados que indiquem expressamente Belém/PA. Frete, cupom, condição ou pagamento ausentes permanecem desconhecidos. Cupons continuam como indicações não validadas; não há aplicação automática nem confirmação no checkout.

## Frequências e limites

- Produtos descobertos: 20 minutos por padrão, mínimo de 15 minutos.
- Links prioritários já cadastrados: mantêm os intervalos anteriores (atualmente 3 minutos).
- Sitemaps e busca: 2 horas por padrão. APIs executam uma consulta da lista por ciclo e alternam os termos.
- robots.txt: cache de 24 horas, compartilhado com o monitor de lojas.
- Uma consulta por loja de cada vez, espaçamento mínimo de 10 segundos, jitter e backoff. HTTP 403 pausa pelo menos 24 horas; HTTP 429 respeita Retry-After.
- Fila limitada a 5.000 URLs, até 10 validações por ciclo. Índices de sitemaps são percorridos em lotes rotativos de até três arquivos. XML comprimido e arquivos acima de 2 MB não são processados. URLs de sitemap sem indicação do modelo no caminho podem não ser descobertas; APIs/feeds complementam essa limitação.

Uma URL admitida fica persistida e volta a ser monitorada após reiniciar. Para suspendê-la, inclua a URL em `urls_desativadas` e reinicie. Para pausar apenas a descoberta, configure `ativado: false`; o acompanhamento dos anúncios já admitidos continua.

## Validação, preços e histórico

A página precisa conter um Product/Offer JSON-LD compatível e não ambíguo. São rejeitados RX 9070 sem XT, outras gerações, computadores completos, acessórios e condição explicitamente usada/recondicionada. Condição não informada resulta em alerta provisório. Falta de vendedor em marketplace mantém o anúncio pendente. Fabricante/variante são reproduzidos no nome anunciado; qualidade de refrigeração e reputação não são inferidas do preço.

O histórico existente registra cada consulta. Estatísticas separam preço público, PIX e cartão; médias móveis de 7 e 30 dias só aparecem após amostras em dois dias distintos, com contagem e tempo de cobertura informados. Não representam cobertura integral do período quando o bot começou há menos tempo. Quedas usam observações do bot, nunca o preço riscado da loja. Nova queda relevante (5% por padrão), retorno de estoque e fim de promoção podem gerar alerta; a repetição do mesmo preço não gera outro alerta. Links com parâmetros de rastreamento são normalizados. Não é possível unificar com certeza encurtadores ou URLs comerciais completamente distintas sem identidade adicional.

## Arquivos e testes

`descoberta.py` controla fila/agenda; `descoberta_fontes.py` contém os adaptadores; `produtos_validacao.py` contém a validação de identidade compartilhada. A execução e os alertas permanecem em `lojas.py`, `lojas_dados.py`, `lojas_extracao.py`, `lojas_rede.py` e `monitor.py`.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Os testes usam dados e serviços simulados, sem Telegram de produção. Cobrem descoberta, reinício, isolamento de falhas, bloqueios, identidade, duplicatas, histórico, retorno de estoque e encerramento de promoção. A liberação de rede e a verificação das políticas/fontes reais permanecem necessárias para comprovar cobertura em produção.
