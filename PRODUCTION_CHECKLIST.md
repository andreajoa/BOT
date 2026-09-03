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
- [x] Restart reconciliation for pending LIMIT entries
- [x] Dynamic candidate universe with mandatory monitoring of open positions
- [x] Runtime status file
- [x] Unit-test CI
- [x] Non-trading live preflight
- [x] Operations runbook

## Must be verified on the local runtime before first live entry
- [ ] Pull latest `main` after final promotion
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
- [ ] Position Manager reconciles the real open position

## Completion definition

Repository implementation is complete only after the final CI is green and the tested branch is promoted to `main`.
End-to-end live validation reaches 100% only after the local runtime passes every unchecked item above using the real account, because credentials are intentionally not available to GitHub CI.
