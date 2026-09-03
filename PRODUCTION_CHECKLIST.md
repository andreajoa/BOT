# Production Readiness Checklist

## Code / CI
- [x] Strategy-agnostic command protocol
- [x] Per-command approval gateway
- [x] Binance leverage endpoint corrected
- [x] LONG/SHORT close logic corrected
- [x] One-Way/Hedge Mode handling
- [x] New entries force ISOLATED margin before leverage
- [x] Symbol precision / minNotional / stepSize / tickSize
- [x] Balance-aware executable universe
- [x] Public market WebSocket
- [x] Order-book depth / taker flow
- [x] Funding / basis / OI / OI change
- [x] Global/top-trader long-short ratios
- [x] 5m/15m/1h/4h structure
- [x] Private User Data Stream
- [x] Structured OpenAI Brain Client
- [x] Strategy-agnostic Risk Governor
- [x] Hard estimated loss-to-stop cap including estimated round-trip fees
- [x] MARKET/LIMIT execution separation
- [x] Real fill required before LIMIT protection
- [x] SL / TP / partial TP
- [x] Persistent trailing Position Manager
- [x] Fail-safe emergency flatten if critical protection install fails
- [x] Execution journal
- [x] Persistent approvals/pending command state
- [x] Persistent pending LIMIT entry state
- [x] Deterministic newClientOrderId idempotency
- [x] Client IDs preserve explicit order role even with real 32-character command IDs
- [x] Restart reconciliation for pending LIMIT entries
- [x] Idempotent SL/TP protection recovery after crash/restart
- [x] Dynamic candidate universe with mandatory monitoring of open positions
- [x] Runtime status file
- [x] Unit-test CI
- [x] Non-trading live preflight
- [x] Read-only final live acceptance audit
- [x] Operations runbook

## Must be verified on the local runtime before first live entry
- [ ] Pull latest `main` after final CI
- [ ] Install latest requirements
- [ ] `.env` contains Binance credentials, OpenAI key and `BOT_MODE=live`
- [ ] `python3 scripts/live_preflight.py --brain` returns `ok=true`
- [ ] Actual Futures USDT balance is read correctly
- [ ] Executable-symbol scanner returns at least one contract for current balance
- [ ] Public market WebSocket connected
- [ ] Private User Data Stream connected
- [ ] Market context `decision_ready=true`
- [ ] Brain produces a valid proposal or WAIT
- [ ] Proposed operation passes loss-to-stop hard limit
- [ ] User reviews and approves one exact `command_id`
- [ ] Entry symbol is confirmed ISOLATED before execution
- [ ] Binance confirms the entry fill
- [ ] SL is visible/confirmed
- [ ] TP is visible/confirmed
- [ ] Journal records entry + protection
- [ ] Position Manager records/reconciles the lifecycle
- [ ] `python3 scripts/final_acceptance.py --command-id <COMMAND_ID>` returns `hundred_percent=true`

## Completion definition

Repository implementation is complete only after the final CI is green on `main`.
End-to-end live validation reaches 100% only when `scripts/final_acceptance.py` returns `hundred_percent=true` for the exact approved real command. The final audit is read-only and never calls create/change/cancel/close order endpoints.
