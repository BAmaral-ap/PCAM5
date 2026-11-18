# Montagem confirmada — 18/11/2026

| Categoria | Compra confirmada | Total com custos informados |
|---|---|---:|
| CPU | AMD Ryzen 7 9800X3D | R$ 2.060,02 |
| Cooler | ARCTIC Freezer 36 A-RGB Black | R$ 215,81 |
| Gabinete | Montech XR White | R$ 317,12 |
| Fonte | Pichau Cluster 850W | R$ 395,05 |
| Placa-mãe | Maxsun Terminator B650M WiFi ICE White | R$ 718,47 |
| SSD | Kingston NV3 1 TB PCIe 4.0 | R$ 811,00 |
| **Total das peças adquiridas** | | **R$ 4.517,47** |

Maxsun e Kingston pagos à vista; seus valores incluem frete. Não foi presumido Pix. O Ryzen permanece no cartão em 6 vezes sem juros, com vencimentos no dia 10, de dezembro/2026 a maio/2027. Pagamentos de parcelas já confirmados no banco são preservados. Sem parcelas confirmadas, o desembolso à vista registrado é R$ 2.457,45 e o compromisso no cartão é R$ 2.060,02. O custo das compras não representa dinheiro restante disponível.

Só faltam **GPU e RAM**. A RX 9070 XT continua prioritária, com as faixas existentes de R$ 4.700, R$ 4.900, R$ 5.100 e R$ 5.300; EXCELENTE e BOA COMPRA abrangem juntas a faixa de oportunidades entre R$ 4.700,01 e R$ 5.100. A simulação de GPU de R$ 5.933,14 não é compra e não compõe o custo. Se esse registro estiver no banco, é conservado como SIMULACAO, sem contabilização financeira.

A RAM tem meta inicial de R$ 1.800 por módulo de 16 GB. Preferências: DDR5-6000, CL30/32/36/38 e AMD EXPO. Começar com 1×16 GB, depois acrescentar outro compatível para dual-channel; quatro slots não são indicação de comprar quatro módulos. A montagem usa a Maxsun como referência DDR5/AM5, sem inventar confirmação de BIOS ou QVL. O parcelamento planejado é sem juros e usa renda pessoal separada da reserva à vista. Pagamento desconhecido gera alerta provisório; juros explícitos não geram recomendação de compra. A regra anterior de kits 2×16 GB até R$ 2.400 continua disponível como alternativa.

A oferta esgotada Corsair de R$ 1.802,85 não foi criada como oportunidade ativa. Eventuais alertas antigos correspondentes na fila são expirados, preservando seu histórico. Uma nova disponibilidade real exige nova observação.

## Aplicar no notebook

Atualize os arquivos do programa com o bot parado, preservando `config.json`, `.env`, sessões e a pasta `dados`. Faça backup da pasta `dados` antes de atualizar. Execute:

```powershell
.\.venv\Scripts\python.exe monitor.py --atualizar-montagem --confirmar
.\.venv\Scripts\python.exe monitor.py --budget
.\.venv\Scripts\python.exe monitor.py --watch --descobrir
```

A migração v4 reconhece as compras anteriores, evita duplicação e recusa valores conflitantes em vez de alterar silenciosamente preços pagos. A primeira aplicação encerra a procura das seis categorias. Uma repetição preserva reativações posteriores. O banco local é a fonte do status comprado; editar apenas o modelo no config não substitui a migração.

Consultas diretas de categorias compradas ficam suspensas, e seus alertas pendentes expiram. O histórico fica preservado. As mensagens dos canais continuam sendo coletadas para encontrar GPU e RAM; referências às outras peças podem permanecer no histórico sem gerar alertas de compra. A descoberta web passou a aceitar RAM DDR5 de 16 GB, além da GPU, usando os mesmos adaptadores e validação da página. Não confirma parcelamento a partir de um preço público isolado.

Para reativar uma categoria após nova decisão, use `--reativar-categoria PLACA-MAE --confirmar` ou `SSD`, além das opções anteriores. Isso mantém a despesa histórica; não presume devolução financeira.

A referência orçamentária prevista anterior de R$ 9.450 continua no cadastro, sem criar receita nem confirmar o recebimento do site ou empréstimo. Com GPU estimada em R$ 5.100 e RAM em R$ 1.800, a projeção é R$ 11.417,47. Revise essa previsão conforme os preços efetivos.
