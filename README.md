# PCAM5 — monitor de promoções para PC AM5

Monitor Python que preserva a coleta de canais públicos pelo Telethon e a exportação CSV, com histórico SQLite, alertas privados pelo Telegram e planejamento de compras separado entre PIX e cartão. Não compra produtos nem registra compras sem confirmação manual.

O código está em [`monitor_telegram_pc/`](monitor_telegram_pc/). Siga o [guia completo para Windows](monitor_telegram_pc/LEIA-ME.md) para reutilizar a sessão, criar o bot, testar o primeiro alerta e iniciar automaticamente.

## Começar no PowerShell

```powershell
cd .\monitor_telegram_pc
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.exemplo .env }
.\.venv\Scripts\python.exe monitor.py --dry-run
```

Preencha `.env` apenas no seu computador, conforme o guia. Depois de autenticar e configurar o bot:

```powershell
.\.venv\Scripts\python.exe monitor.py --test-alert
.\.venv\Scripts\python.exe monitor.py --watch
```

O intervalo padrão é de 2 minutos. O notebook precisa estar ligado, conectado e sem suspensão. Credenciais locais são necessárias para validar coleta e recebimento reais; testes automatizados usam simulações.

## Orçamento, histórico e testes

```powershell
.\.venv\Scripts\python.exe monitor.py --budget
.\.venv\Scripts\python.exe monitor.py --history "RX 9070 XT 16GB"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Metas, canais e estimativas ficam em `config.json`. A montagem atual tem seis peças compradas por R$ 4.517,47; faltam GPU e RAM. Placa-mãe Maxsun e SSD Kingston foram pagos à vista; o Ryzen permanece em seis parcelas sem juros. Consulte [MONTAGEM.md](monitor_telegram_pc/MONTAGEM.md). No notebook com o banco existente, pare o bot e aplique:

```powershell
.\.venv\Scripts\python.exe monitor.py --atualizar-montagem --confirmar
.\.venv\Scripts\python.exe monitor.py --budget
```

O cadastro reconhece as compras anteriores e não as duplica ao ser repetido. Confira o [guia](monitor_telegram_pc/LEIA-ME.md) para registrar parcelas efetivamente pagas, reativar alertas e interpretar as estimativas.

## Segurança da sessão

`.env`, tokens e arquivos `.session` nunca devem ser enviados a chats ou commits. O repositório original continha uma sessão versionada: ela foi retirada do controle do Git, preservando o arquivo local, mas ainda consta no histórico anterior. Revogue-a nas configurações de dispositivos do Telegram e gere outra localmente conforme o guia.

Consulte o [LEIA-ME](monitor_telegram_pc/LEIA-ME.md) para compras confirmadas, parcelas, CSVs legados, agendamento e tratamento de envios incertos.
