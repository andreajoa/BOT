# Adaptive Binance Futures Executor — Runbook

## Estado do sistema

O caminho principal agora é `main.py -> adaptive_runtime.py`.
O antigo bot de Mean Reversion permanece apenas como legado/referência e não é o cérebro do runtime adaptativo.

## Princípios

- O cérebro pode responder `WAIT`, `OPEN_POSITION`, `MODIFY_POSITION` ou `CLOSE_POSITION`.
- Estratégia, direção, margem, leverage, SL, TP e trailing são dinâmicos.
- O executor não escolhe estratégia.
- Toda nova ordem real exige aprovação específica para o `command_id` correspondente.
- Chaves Binance/OpenAI ficam apenas no `.env` local e nunca no GitHub.
- Toda nova entrada força margem `ISOLATED` antes de configurar leverage.
- O Risk Governor limita a perda estimada até o stop, incluindo uma estimativa de taxas.
- SL/TP/trailing são instalados e geridos pelo executor após confirmação de fill.
- Uma entrada LIMIT só recebe proteção depois do fill real.
- Entradas pendentes sobrevivem a restart e são reconciliadas por `newClientOrderId` determinístico.
- IDs de entrada, SL, TP e trailing são deterministicamente diferentes mesmo quando o `command_id` real tem 32 caracteres.

## 1. Atualizar o código local

```bash
cd ~/Downloads/Organizados/Pessoal-Andre/coder/mercado_lateral || exit 1
git fetch origin
git checkout main
git pull --ff-only origin main
python3 -m pip install -r requirements.txt
```

## 2. Variáveis obrigatórias no `.env`

```dotenv
BINANCEAPIKEY=...
BINANCEAPISECRET=...
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.6-sol
BOT_MODE=live
```

Configurações de proteção recomendadas para o estágio inicial:

```dotenv
MAX_LEVERAGE_HARD=20
MAX_MARGIN_USAGE_PCT=0.95
SINGLE_POSITION_BELOW_USDT=5.0
MAX_OPEN_POSITIONS_HARD=3
MAX_LOSS_PCT_BALANCE_HARD=0.35
ESTIMATED_TAKER_FEE_RATE=0.0005
SCANNER_MAX_SYMBOLS=15
BRAIN_DECISION_INTERVAL_SECONDS=20
BRAIN_COMMAND_TTL_SECONDS=90
UNIVERSE_REFRESH_SECONDS=300
DERIVATIVES_INTERVAL_SECONDS=60
STRUCTURE_INTERVAL_SECONDS=30
STATUS_INTERVAL_SECONDS=2
```

## 3. Preflight live — não envia ordens

```bash
python3 scripts/live_preflight.py --brain
```

O JSON final precisa mostrar:

```json
{
  "ok": true,
  "orders_sent": 0
}
```

Se `ok` for `false`, não iniciar o runtime até corrigir o check que falhou.

## 4. Iniciar o runtime

```bash
python3 main.py
```

O runtime inicia:

- scanner de contratos executáveis para o saldo atual;
- WebSocket público;
- User Data Stream privado;
- Open Interest e long/short ratios;
- estrutura 5m/15m/1h/4h;
- Brain Client;
- Risk Governor;
- approval gateway;
- executor;
- Position Manager/trailing;
- journal/status.

## 5. Ver proposta pendente

Em outro Terminal:

```bash
python3 control_cli.py status
```

A proposta contém o `command_id` e todos os parâmetros concretos da operação.

## 6. Aprovar exatamente uma proposta

```bash
python3 control_cli.py approve <COMMAND_ID>
```

A aprovação é válida apenas para esse `command_id` e expira junto com o comando.

Para rejeitar:

```bash
python3 control_cli.py reject <COMMAND_ID>
```

## 7. Conferir execução

```bash
cat logs/runtime_status.json
```

E para o histórico completo:

```bash
tail -n 100 logs/execution_journal.jsonl
```

A trilha deve permitir identificar:

- comando recebido;
- preflight;
- aprovação consumida;
- margem isolada;
- leverage;
- ordem de entrada;
- fill/rejeição;
- stop;
- take profits;
- trailing;
- atualização de posição;
- comissão;
- PnL realizado;
- fechamento/erro.

## 8. Auditoria final de 100% — somente leitura

Depois do fill real e da instalação de SL/TP, rode em outro Terminal:

```bash
python3 scripts/final_acceptance.py --command-id <COMMAND_ID>
```

Esse comando não cria, altera, cancela nem fecha ordens. Ele apenas lê os arquivos locais e consulta a Binance para provar:

- `main` local alinhada com `origin/main`;
- runtime ativo e recente;
- WebSocket público conectado;
- User Data Stream conectado;
- `decision_ready=true`;
- aprovação específica do `command_id` registrada;
- entrada realmente `FILLED` na Binance;
- margem `ISOLATED`;
- SL existente e, se a posição ainda estiver aberta, proteção de stop realmente ativa (inclusive trailing substituto);
- TP(s) existentes e, se a posição ainda estiver aberta, pelo menos um TP ainda ativo;
- lifecycle registrado pelo Position Manager/journal;
- ausência de erro fatal ligado àquele comando.

A validação só está concluída quando a saída trouxer:

```json
{
  "hundred_percent": true,
  "completion_percent": 100.0,
  "orders_sent": 0
}
```

## 9. Restart/recovery

Se o processo cair:

```bash
python3 main.py
```

O runtime recarrega posições geridas e entradas LIMIT pendentes. O executor consulta a Binance usando `newClientOrderId` determinístico antes de reenviar entrada ou proteção, reduzindo risco de duplicidade.

## 10. Kill switch manual

Para interromper o runtime:

```text
Ctrl+C
```

Stops/TPs já enviados à Binance permanecem no exchange. O shutdown do processo não deve remover proteção da posição.

## Arquivos operacionais locais

Todos ficam ignorados pelo Git:

- `logs/pending_command.json`
- `logs/trade_approval.json`
- `logs/pending_entries.json`
- `logs/managed_positions.json`
- `logs/runtime_status.json`
- `logs/execution_journal.jsonl`

## Regra para a primeira operação real

1. CI final verde na `main`.
2. `live_preflight.py --brain` com `ok=true` e `orders_sent=0`.
3. Runtime com `market_stream_connected=true` e `user_stream_connected=true`.
4. `decision_ready=true`.
5. Proposta concreta passando o hard limit de perda até o stop.
6. Proposta revisada e aprovada pelo `command_id` exato.
7. Confirmar fill + `ISOLATED` + SL + TP.
8. Rodar `final_acceptance.py --command-id <COMMAND_ID>` e exigir `hundred_percent=true`.
