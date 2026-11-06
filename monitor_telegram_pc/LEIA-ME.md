# Monitor de promoções para PC AM5

O monitor consulta canais públicos com a sessão existente do **Telethon**, extrai ofertas, conserva o histórico em SQLite e envia recomendações ao seu chat privado por um **bot do Telegram**. A coleta e a exportação CSV do projeto foram mantidas e ampliadas. Nenhuma compra, pagamento, entrada em grupo ou registro de compra acontece automaticamente.

Para o monitoramento direto de produtos nas lojas, consulte [LOJAS.md](LOJAS.md).

## 1. Instalar no Windows

Instale Python **3.11 ou superior**. Abra o PowerShell na pasta `monitor_telegram_pc`:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.exemplo .env }
notepad .env
```

Os exemplos usam o executável do ambiente virtual diretamente; não é necessário ativá-lo nem alterar a política de execução do PowerShell para executar Python. Recrie `.venv` ao mover o projeto para outro computador. O comando acima preserva um `.env` existente.

Preencha apenas localmente:

| Variável | Origem e finalidade |
|---|---|
| `TELEGRAM_API_ID` | Aplicativo pessoal em `https://my.telegram.org`; leitura dos canais. |
| `TELEGRAM_API_HASH` | Segredo do mesmo aplicativo; autenticação Telethon. |
| `TELEGRAM_BOT_TOKEN` | Token fornecido pelo BotFather; envio das notificações. |
| `TELEGRAM_CHAT_ID` | ID numérico positivo do seu chat privado com o bot. |
| `TELEGRAM_SESSION` | Nome/caminho da sessão, inicialmente `sessao_telegram`. Caminhos relativos usam a pasta do programa. |

Não envie essas informações em chats, screenshots, relatórios ou commits. Não cole tokens em comandos com URLs. `.env.exemplo` contém campos vazios. `.env`, sessões, banco e logs locais são ignorados pelo Git. Proteja o acesso à conta do Windows e aos backups.

### Reutilizar a sessão Telethon

Com uma sessão válida e as credenciais do mesmo aplicativo, o monitor a reutiliza sem pedir novo código. Autentique interativamente apenas se necessário:

```powershell
.\.venv\Scripts\python.exe monitor.py --login
```

Informe telefone, código e eventual senha 2FA somente no terminal local. A tarefa automática não inicia autenticação interativa. O monitor não entra em grupos: se necessário, faça isso manualmente no aplicativo oficial.

**Sessão anteriormente versionada:** o repositório original continha `sessao_telegram.session`. Ela foi retirada do índice do Git e o arquivo local foi preservado, mas continua no histórico anterior. Trate-a como exposta: no Telegram → Configurações → Dispositivos, encerre a sessão correspondente. Depois configure outro nome local, como `TELEGRAM_SESSION=sessao_telegram_local`, e execute `--login`. Adicionar um arquivo ao `.gitignore` não apaga versões anteriores.

## 2. Criar o bot e testar o primeiro alerta

1. Abra a conta oficial **@BotFather** no Telegram e envie `/newbot`.
2. Escolha nome/usuário do bot. Copie o token somente para `TELEGRAM_BOT_TOKEN` no `.env` local.
3. Abra seu novo bot em uma conversa privada e envie `/start`.
4. Consulte os IDs de chats privados que enviaram mensagens ao bot:

   ```powershell
   .\.venv\Scripts\python.exe monitor.py --bot-chat-id
   ```

   Esse comando imprime IDs, sem o token. Identifique o seu ID antes de preencher `TELEGRAM_CHAT_ID`; não escolha um desconhecido. Se não aparecer resultado, envie outra mensagem ao bot. Não use simultaneamente outro consumidor de `getUpdates` ou webhook para esse bot.

5. Veja a demonstração offline, sem credenciais nem envio:

   ```powershell
   .\.venv\Scripts\python.exe monitor.py --dry-run
   ```

6. Envie a demonstração real ao chat privado configurado:

   ```powershell
   .\.venv\Scripts\python.exe monitor.py --test-alert
   ```

O teste envia uma mensagem fictícia e não registra compra. Ele testa o bot independentemente da coleta Telethon e verifica que o destinatário é privado. Os testes automatizados usam simulações; sua conta, permissões dos canais e entrega real só estarão validadas depois dos testes locais com suas credenciais.

## 3. Consultar e executar continuamente

Consulta única, compatível com o fluxo anterior:

```powershell
.\.venv\Scripts\python.exe monitor.py
```

Execução contínua, por padrão a cada **2 minutos**:

```powershell
.\.venv\Scripts\python.exe monitor.py --watch
```

Com parâmetros explícitos:

```powershell
.\.venv\Scripts\python.exe monitor.py --watch --interval 2 --limit 150 --days 7
```

`--limit` controla o tamanho do lote; o cursor incremental evita pular o meio da fila. `--days` limita o histórico inicial. A primeira consulta de cada canal estabelece uma base **sem alertas retroativos**. Os ciclos seguintes acompanham mensagens posteriores. Pare com `Ctrl+C`.

Use `--watch --no-notify` para coletar sem envio. As mensagens processadas continuam registradas e não são notificadas retroativamente quando o envio for reativado. `--config CAMINHO` e `--db CAMINHO` selecionam arquivos próprios; use caminhos absolutos em tarefas agendadas.

### Iniciar automaticamente com o Windows

Após instalar, autenticar e confirmar `--test-alert`, registre a tarefa do usuário atual:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\registrar-tarefa.ps1
```

Revise o script antes de executá-lo. `Bypass` vale apenas para essa execução local. O script registra uma tarefa no **Agendador de Tarefas** com caminhos absolutos de `.venv\Scripts\python.exe` e `monitor.py --watch`, além do diretório de trabalho correto. Ela inicia no login do usuário atual, usa a mesma sessão local e não armazena senha de administrador.

No Agendador, localize **PCAM5 Monitor** e use **Executar**. Também pode iniciar pelo PowerShell:

```powershell
Start-ScheduledTask -TaskName 'PCAM5 Monitor'
```

Confira `logs/monitor.log`. Desabilite a tarefa para suspender a automação. Se mover a pasta, edite os caminhos no Agendador ou remova explicitamente a tarefa antiga antes de registrá-la novamente: o script recusa sobrescrever uma tarefa existente. Evite rodar cópias simultâneas sobre o mesmo banco.

**O notebook precisa estar ligado, conectado e sem suspensão.** O usuário precisa entrar no Windows para disparar a tarefa. Fechar a tampa pode suspender o notebook, conforme as opções de energia. Um VPS poderá ser preparado futuramente para execução 24 horas; nenhum processo atual é transferido automaticamente.

## 4. Metas, canais e recomendações

Alertas de placas-mãe explicitamente identificadas como AM4 ou por chipsets AM4 são descartados. Fontes só geram alerta quando o título identifica 850 W sem potência conflitante; se a potência não estiver clara no título, não há alerta. Essas regras não comprovam qualidade nem compatibilidade das demais peças.

A RX 9070 XT tem triagem preliminar separada: até R$ 4.700 IMPERDÍVEL; até R$ 4.900 EXCELENTE; até R$ 5.100 BOA COMPRA; até R$ 5.300 AVALIAR; acima disso PREÇO ALTO. Configure `rx9070xt_faixas` em `config.json`. O alerta preliminar não depende de pagamento, frete ou análise Gemini e não comprova preço ou estoque. Mensagens com vários preços são sinalizadas para conferência. Alertas preliminares da GPU não aguardam o Gemini.

Edite `config.json` e reinicie o monitor para aplicar mudanças. Os canais iniciais são:

```json
"canais": ["https://t.me/pcbuildwizard", "https://t.me/LaPromotion"]
```

Adicione ou remova entradas nessa lista. Use canais públicos ou nomes `@usuario`, sem convites privados. A conta deve ter autorização para ler o histórico.

| Configuração | Função |
|---|---|
| `intervalo_minutos` | Intervalo padrão da consulta contínua, inicialmente 2. |
| `idade_maxima_alerta_minutos` | Idade máxima para alertar, inicialmente 30 minutos. |
| `deduplicacao_horas` | Janela de supressão de republicações, inicialmente 24 horas. |
| `metas` | Preço-alvo em reais por modelo reconhecido. |
| `montagem` | Peças necessárias, estimativas por pagamento e itens já possuídos. |
| `orcamento` | Reserva PIX e limite de cartão, separados. |
| `perfis` | Características e compatibilidade conhecidas por modelo. |
| `compatibilidade.confirmacoes` | Confirmações manuais por modelo para a montagem atual. |
| `alertas` | Descontos mínimos e classes notificadas. |

As metas são referências, não garantia de preço/estoque. RX 9070 XT, RX 9070 e RX 9070 GRE são distintas. “DDR5” sozinho não identifica capacidade, quantidade de módulos ou modelo comercial. Core Reactor II VE também é diferenciada das outras versões.

Inicialmente, `excepcional_percentual: 10` exige 10% ou mais abaixo da meta; `boa_percentual: 0` aceita a meta ou menos. A comparação usa sua meta, não o preço anterior anunciado pela loja.

- **COMPRAR AGORA:** desconto excepcional, modelo exato, compatibilidade confirmada, sem ambiguidades e projeção dentro dos limites. Continua sendo recomendação, nunca execução de compra.
- **BOA PROMOÇÃO:** preço interessante; confira eventuais pendências de compatibilidade e custos desconhecidos antes de decidir.
- **NÃO COMPENSA:** preço fora do critério, incompatibilidade, peça já comprada/fora da montagem ou comprometimento do orçamento.
- **VERIFICAR:** preço, pagamento, meta ou extração ambíguos; não há certeza suficiente para recomendar.
- **AVALIAR ALTERNATIVA:** anúncio sem modelo cadastrado dentro da margem de triagem. Confira modelo, qualidade, compatibilidade e orçamento; a etiqueta não comprova que o produto seja bom.

As duas primeiras classes e `AVALIAR ALTERNATIVA` notificam por padrão. A triagem ampliada aceita até 25% acima da meta da peça de referência; ajuste `alternativas_gerais.margem_percentual` ou desative `alternativas_gerais.ativado` em `config.json`. Com orçamento pendente, CPU, placa-mãe e RAM ainda podem gerar alerta provisório pedindo verificação do orçamento. Preços ou condições ambíguos não entram na triagem de alternativas. Ventoinhas não fazem parte dessa ampliação. Ofertas rejeitadas permanecem no banco.

### Compatibilidade e alternativas

Os perfis iniciais indicam a plataforma pretendida; não atestam dimensões de uma variante de GPU, BIOS, conectores ou gabinete ainda desconhecidos. Consulte as especificações do fabricante antes de habilitar urgência para um modelo:

```json
"compatibilidade": {
  "confirmacoes": {
    "Ryzen 7 7800X3D": true
  }
}
```

Esse exemplo é uma confirmação manual. Não marque todos os modelos sem conferir. Revise-a quando mudar a montagem. `perfis[modelo].compativel: false` rejeita um modelo. As verificações cruzam as peças compradas e planejadas; conflitos conhecidos prevalecem sobre a confirmação manual. Campos opcionais dos perfis:

| Componente | Campos de especificação |
|---|---|
| CPU / placa-mãe | `socket` (ex.: `AM5`) |
| RAM / placa-mãe | `tipo_memoria` (ex.: `DDR5`) |
| GPU | `comprimento_mm`, `fonte_minima_w`, `conectores_requeridos` |
| Fonte | `potencia_w`, `formato`, `conectores` |
| Cooler | `altura_mm`, `sockets` (lista de sockets suportados) |
| Placa-mãe | `formato` |
| Gabinete | `gpu_max_mm`, `cooler_max_mm`, `formatos_placa_mae` (lista) |

Use valores da variante exata e unidades indicadas. Ausência de especificação não prova encaixe. Uma RX 9070 XT de outro fabricante pode ter dimensões diferentes.

Para uma alternativa, configure sua meta e um perfil com a identificação retornada pelo parser. Perfis com `verificado: true` podem incluir `consumo_w`, `desempenho_indice`, `benchmark`, `longevidade`, `garantia_anos` e `fonte`. Use fontes e medições comparáveis: reais por ponto exige o mesmo benchmark. O sistema não inventa vantagem de consumo, desempenho em 1440p/4K ou longevidade. Consulte as análises e comparações das 20 ofertas novas mais recentes com `python monitor.py --offers`.

Cadastre modelos adicionais em `alternativas` no `config.json`, sem alterar o código. Cada item tem `modelo`, `categoria`, `padrao` (expressão regular específica) e `meta` em reais. Exemplo de estrutura fictícia, a substituir pelo produto real:

```json
"alternativas": [
  {"modelo": "RAM Modelo Exato 16GB DDR5", "categoria": "RAM", "padrao": "MODELO-EXATO.*16\\s*GB.*DDR5", "meta": 1500}
]
```

A identidade específica prevalece sobre a família genérica. Complete `perfis` e verifique a regra com exemplos para não confundir capacidades, kits ou variantes.

## 5. Orçamento e compras manuais

```powershell
.\.venv\Scripts\python.exe monitor.py --budget
```

O relatório separa reserva PIX, crédito, compras, parcelas por mês, peças faltantes e total projetado. Crédito não é dinheiro recebido nem aumenta o saldo PIX.

O orçamento **previsto** é de R$ 9.450,00, condicionado às entradas planejadas e incluindo R$ 5.000 ainda pendentes pelo site. Não representa dinheiro disponível agora; o saldo disponível no PIX aparece como `null` até ser informado. O limite de cartão não é receita. A RAM permanece prevista no cartão e seu custo entra no total da montagem.

As compras confirmadas somam R$ 927,98; o saldo previsto restante é R$ 8.522,02. As estimativas atuais das quatro peças faltantes, somadas às compras, dão R$ 9.957,98, acima da previsão em R$ 507,98, antes de fretes não informados. São referências, não preços finais garantidos. A meta de R$ 1.750 para o 7800X3D inclui frete; se ele não estiver disponível, o alerta pede confirmação do total entregue.

`orcamento.total_maximo` é a referência total da montagem, não dinheiro disponível. Parcelas já pagas continuam consumindo esse teto: liberar crédito não cria orçamento adicional. Inclua frete, juros e acessórios nas estimativas e no total confirmado da compra. Se souber o saldo inicial reservado para a montagem, informe `orcamento.pix` com atenção às compras já registradas.

### Aplicar as compras confirmadas de novembro/2026

Pare o monitor e, no computador com o banco existente, execute:

```powershell
.\.venv\Scripts\python.exe monitor.py --atualizar-montagem --confirmar
.\.venv\Scripts\python.exe monitor.py --budget
```

O comando registra cooler ARCTIC Freezer 36 (R$ 215,81), Montech XR White (R$ 317,12) e Pichau Cluster 850W (R$ 395,05). Ele reconhece o cooler antigo, não duplica os registros e interrompe sem mudar as compras se encontrar conflito de valor ou pagamento. A fonte fica paga e comprada, com entrega em preparação. O gabinete é registrado como `avista` porque a forma específica não foi informada. O comando preserva os canais e demais configurações existentes; uma repetição não desfaz alterações posteriores.

Fonte, gabinete e coolers deixam de gerar alertas de compra, enquanto GPU, CPU, placa-mãe e RAM continuam. Quando a frequência da RAM é identificada, o alerta destaca a faixa preferida de 5600–6000 MHz; uma opção de outra frequência não é descartada automaticamente nem se exige CL30. Para reativar uma categoria sem apagar o histórico nem presumir estorno, pare o monitor e use, por exemplo:

```powershell
.\.venv\Scripts\python.exe monitor.py --reativar-categoria FONTE --confirmar
```

As demais opções são `GABINETE` e `COOLER-CPU`. A reativação adiciona novamente a estimativa da categoria às peças faltantes e mantém o gasto anterior. O gabinete oferece 420 mm para GPU e 175 mm para cooler; o ARCTIC tem 159 mm. A fonte é de 850 W. Dimensões específicas da GPU, potência recomendada e quantidade dos conectores ainda precisam ser confirmadas por modelo.

### Registrar uma compra que você já fez

**Pare o monitor ou a tarefa agendada antes de alterar compras/parcelas**, pois o banco é protegido contra alterações simultâneas durante a execução. Reinicie o monitor após registrar os dados.

O registro exige JSON e `--confirmar`. Exemplo ilustrativo de RAM comprada por R$ 1.800,00 em três parcelas:

```powershell
@'
{
  "categoria": "RAM",
  "modelo": "Modelo comercial exato da RAM comprada",
  "total_centavos": 180000,
  "pagamento": "cartao",
  "parcelas": 3,
  "primeira_parcela": "2026-11",
  "parcelas_pagas": 0
}
'@ | Set-Content -Encoding UTF8 .\dados\compra-local.json

.\.venv\Scripts\python.exe monitor.py --purchase .\dados\compra-local.json --confirmar
.\.venv\Scripts\python.exe monitor.py --budget
```

Substitua os dados do exemplo antes de confirmar. Use o total efetivamente contratado, incluindo custos que deseja atribuir à peça. O comando apenas registra uma decisão local. A peça deixa de faltar e seu valor real substitui a estimativa; guarde o ID retornado. O comando evita duplicar um registro idêntico. Uma segunda compra na mesma categoria não representa uma nova necessidade prevista; revise seu planejamento antes de ampliar a montagem.

Para PIX, use `"pagamento": "pix"`, `"parcelas": 1`, `"parcelas_pagas": 0`; a primeira parcela é dispensável. Use `"avista"` quando a forma específica do pagamento à vista não for conhecida. Categorias: `CPU`, `GPU`, `PLACA-MAE`, `RAM`, `FONTE`, `COOLER-CPU`, `GABINETE`, `SSD`.

Depois de pagar uma parcela da compra de ID 1:

```powershell
.\.venv\Scripts\python.exe monitor.py --pay-installment 1 --confirmar
```

Cada execução confirma **uma parcela**; não repita para a mesma parcela. O passar do mês não quita fatura nem libera crédito. A reserva PIX é para peças; o sistema não presume que ela paga as faturas, que são compromissos de renda futura. Se usar essa reserva para pagar o cartão, ajuste o planejamento ao dinheiro realmente disponível.

## 6. Histórico e CSVs existentes

SQLite conserva mensagens, ofertas, cursores, notificações e compras. A coleta exporta os resultados para `dados/ofertas_monitor.csv`, preservando o CSV anterior. Textos novos são completos. O extrator diferencia PIX, total no cartão e preço apenas anunciado; uma parcela isolada não vira preço total. Preços históricos, riscados e anúncios com associações ambíguas exigem revisão.

Importe os CSVs anteriores:

```powershell
.\.venv\Scripts\python.exe monitor.py --import-csv .\dados\ofertas.csv
.\.venv\Scripts\python.exe monitor.py --import-csv .\dados\ofertas_backup_antes_v2.csv
.\.venv\Scripts\python.exe monitor.py --reprocess
```

As linhas originais são arquivadas, inclusive as não classificáveis. Importação não envia alertas; reprocessamento não sobrescreve nem elimina os CSVs originais. O texto antigo foi truncado e achatado: seu preço é preservado para auditoria, sem nova extração como se houvesse um anúncio completo. Modelos ambíguos não contaminam estatísticas de modelos exatos.

```powershell
.\.venv\Scripts\python.exe monitor.py --history "RX 9070 XT 16GB"
.\.venv\Scripts\python.exe monitor.py --history "RX 9070 XT 16GB" --history-days 30
.\.venv\Scripts\python.exe monitor.py --export-csv .\dados\ofertas-atualizadas.csv
```

O histórico mostra quantidade observada, mínimo, média, mediana, evolução e melhores ofertas, distinguindo formas de pagamento. “Últimos 30 dias” é um filtro, não prova de 30 dias coletados. O alcance é o das observações disponíveis; não há histórico de 90 dias sem coleta correspondente. Datas novas incluem fuso UTC.

Pare o monitor antes de copiar o banco para backup. Durante a execução, arquivos SQLite `-wal`/`-shm` podem conter transações recentes; copiar somente o banco principal nesse momento pode perder dados. Preserve configurações e CSVs. `.env` e sessões exigem armazenamento separado e protegido.

## 7. Recuperação e limites de entrega

### Análise opcional com Gemini

Adicione `GEMINI_API_KEY` ao `.env` local, sem aspas e sem compartilhar a chave. A configuração `gemini` em `config.json` ativa a análise, seleciona `gemini-3.5-flash-lite` e limita a 20 tentativas automáticas por dia UTC neste banco. O modelo precisa estar disponível para sua chave e seu projeto; a disponibilidade e as cotas gratuitas são definidas pelo Google e podem mudar. Desative com `"ativado": false` ou ajuste o modelo e o limite. Esse limite local não substitui os controles de faturamento e cotas do Google, nem inclui chamadas feitas por outros programas ou bancos.

```powershell
.\.venv\Scripts\python.exe monitor.py --test-gemini
```

O teste envia uma oferta fictícia ao Gemini e mostra a resposta no terminal; não envia ao Telegram e não entra no limite diário automático. Cada execução consome uma solicitação da API. Nas ofertas reais, somente alertas selecionados após deduplicação são analisados. O texto preparado do alerta, incluindo dados de orçamento e condições da oferta, é enviado ao Google; links e dados da sessão não são enviados. A resposta é um comentário adicional, que pode conter erros; a classificação, os preços e o orçamento continuam locais. Em caso de erro, tempo esgotado ou cota local atingida, o alerta local é enviado normalmente. O Gemini não verifica carrinho nem compra produtos. `--test-alert` continua sendo um teste independente do Telegram.

- Cursores persistidos retomam a leitura após falhas sem gerar ofertas duplicadas por reinicialização.
- `FloodWait` e limites da Bot API são respeitados. Falhas transitórias são retomadas; erros de autenticação/permissão exigem correção local.
- Logs rotativos ficam em `logs/monitor.log`, sem impressão intencional de tokens, credenciais ou URLs autenticadas.
- Deduplicação considera identidade da oferta e janela configurada; link, cupom ou condições diferentes podem representar outra oferta.
- Mensagens com mais de 30 minutos, por padrão, ficam apenas no histórico. Depois de indisponibilidade longa, consulte o histórico para ver ofertas não alertadas.

```powershell
.\.venv\Scripts\python.exe monitor.py --notifications-status
```

Estados da fila: `pending` aguarda envio, `sent` tem confirmação, `expired` ficou antiga e `uncertain` perdeu a confirmação de entrega. A Bot API não permite garantir exatamente uma entrega em todos os cenários: o envio pode ocorrer antes de uma queda da conexão, sem resposta recebida. Envios incertos não são repetidos automaticamente para evitar duplicação; **um alerta pode deixar de chegar**. Confira o chat e os registros nesses casos.

Não apague o banco para resolver uma falha comum: ele contém compras e histórico. Não compartilhe credenciais ao pedir ajuda. Um teste simulado aprovado não confirma conexão real com o Telegram.

## 8. Testes e estrutura

Na pasta `monitor_telegram_pc`:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Os testes exercitam filtros, preços, saldos separados, projeções, parcelas, persistência, retomada e notificações simuladas. Não compram produtos nem enviam mensagens reais. Execute-os novamente após alterar extração ou cálculos.

| Arquivo | Responsabilidade |
|---|---|
| `monitor.py` | Comandos, inicialização e operações manuais. |
| `parsing.py` | Modelos, preços, cupons e ambiguidades. |
| `finance.py` | Orçamento, parcelas, compatibilidade e recomendação. |
| `storage.py` | SQLite, CSV e histórico. |
| `runtime.py` | Coleta incremental, fila e logs. |
| `notifications.py` | Formatação e Bot API privada. |
| `settings.py` / `config.json` | Configuração não secreta. |
| `windows/registrar-tarefa.ps1` | Inicialização no login do Windows. |

O parser usa regras e não valida carrinho. Antes de comprar, confira preço final, vendedor, frete, cupom, garantia, estoque e compatibilidade. O programa não afirma “menor preço da história” ou que a oferta nunca voltará. Formatos novos precisam de regras e testes específicos.
