# Crypto Trader Bot - Mercado Lateral (AUTO-DETECT 24/7)

Bot especializado em **mercado lateral** com detecção automática e estratégia de **Mean Reversion**.

---

## 🔄 Sistema 24/7 com Estados Automáticos

O bot roda **24/7 sem parar** e detecta automaticamente quando o mercado está em lateral:

```
┌─────────────────────────────────────────────┐
│                                             │
│   📊 MONITORANDO MERCADO 24/7           │
│                                             │
│   ┌─────────────┐    ┌──────────────┐    │
│   │  ESTADOS    │    │   MERCADO    │    │
│   ├─────────────┤    ├──────────────┤    │
│   │ ⏸️ STAND BY│ ←→ │ TENDÊNCIA    │    │
│   │             │    │ (não opera)  │    │
│   └─────┬──────┘    └───────┬──────┘    │
│         │                     │            │
│         ↓                     ↓            │
│   ┌─────────────┐    ┌──────────────┐    │
│   │  ESTADOS    │    │   MERCADO    │    │
│   ├─────────────┤    ├──────────────┤    │
│   │ ▶️ ACTIVE   │ ←→ │  LATERAL     │    │
│   │             │    │ (OPERA!)     │    │
│   └─────────────┘    └──────────────┘    │
│                                             │
└─────────────────────────────────────────────┘
```

### Estados do Bot

| Estado | Descrição | O que faz |
|--------|-----------|-------------|
| **⏸️ STAND BY** | Mercado em tendência | Apenas monitora, NÃO abre posições |
| **▶️ ACTIVE** | Mercado em lateral | Busca e ABRE oportunidades |

---

## 📚 Estratégia: Mean Reversion (Reversão à Média)

### Quando opera (só em ACTIVE)
- Mercado está em **consolidação/lateral**
- BTC oscilando entre suporte e resistência
- ADX baixo (< 25)
- Sem tendência clara

### Lógica de Entrada

```
┌─────────────────────────────────────────┐
│  RESISTÊNCIA ←───┬─────────────  │
│                    │             │
│  ┌─────────────┐  │  ┌────────┐ │
│  │   ZONA     │  │  │        │ │
│  │  LATERAL   │  │  │ ZONA   │ │
│  │             │  │  │ LATERAL│ │
│  └─────────────┘  │  └────────┘ │
│                    │             │
│  SUPORTE ─────────┴─────────────  │
└─────────────────────────────────────────┘
```

| Sinal | Ação | Critérios |
|-------|-------|-----------|
| **LONG** | COMPRAR | BB lower + RSI < 30 + Stoch < 20 |
| **SHORT** | VENDER | BB upper + RSI > 70 + Stoch > 80 |

---

## 🔍 Detecção Automática de Mercado Lateral

O bot analisa o BTC 4h continuamente e determina se está lateral:

### Critérios para LATERAL (score ≥ 6)

| Critério | Peso | Condição |
|----------|-------|-----------|
| **ADX baixo** | +4 | ADX < 20 (bônus) ou < 25 (médio) |
| **BB width adequada** | +3 | 2% ≤ BB width ≤ 6% |
| **Preço no meio da BB** | +3 | 10% ≤ BB_pct ≤ 80% |
| **EMAs entrelaçadas** | +2 | Sem alinhamento claro |

### Critérios para TENDÊNCIA

- ADX ≥ 25 OU
- EMAs alinhadas (BULLISH ou BEARISH) OU
- BB muito estreita/larga OU
- Preço em extremo da BB

---

## 📊 Indicadores

| Indicador | Uso no Lateral |
|-----------|----------------|
| **Bollinger Bands** | Identificar limites da consolidação |
| **RSI** | Detectar sobrecomprado/sobrevendido |
| **Stochastic** | Confirmar reversões |
| **Williams %R** | Sensibilidade adicional |
| **ADX** | Confirmar se está em lateral |
| **EMAs** | Detectar tendência |

---

## ⚙️ Configuração

### Arquivo .env

```env
# API Keys
BINANCEAPIKEY=sua_chave_api
BINANCEAPISECRET=sua_chave_secreta

# Modo
BOT_MODE=live        # live ou paper (comece com paper!)

# Parâmetros de Trading
LEVERAGE=2            # 2x (conservador para lateral)
POSITION_SIZE_PCT=0.05 # 5% por entrada
MIN_BALANCE_USDT=0.60
MAX_OPEN_POSITIONS=3    # Até 3 posições simultâneas

# Parâmetros para Lateral
BB_PERIOD=20           # Período do Bollinger Bands
BB_STD_DEV=2.0         # Desvio padrão das bandas
RSI_PERIOD=14          # Período do RSI
STOCH_K_PERIOD=14      # Período do Stochastic
STOCH_D_PERIOD=3      # Período do Stochastic D
WILLIAMS_PERIOD=14     # Período do Williams %R
SR_LOOKBACK=50         # Lookback para Suporte/Resistência
MIN_BB_PROXIMITY=0.02 # 2% de proximidade da banda

# Stop Loss e Take Profit
STOP_LOSS_PCT=0.005   # 0.5% Stop Loss (estreito)
TAKE_PROFIT_PCT=0.010  # 1.0% Take Profit (curto)

# Intervalos
SCAN_INTERVAL_SECONDS=60  # 1 minuto (análise de mercado)
POSITION_CHECK_INTERVAL_SECONDS=5  # 5 segundos (check posições)

# Logging
LOG_LEVEL=INFO
```

---

## 🚀 Instalação e Execução

```bash
# 1. Instalar dependências
cd mercado_lateral
pip install -r requirements.txt

# 2. Criar arquivo .env
cp .env.example .env
# Editar .env com suas chaves API

# 3. Executar (roda 24/7)
python main.py
```

**Para rodar em background (screen/tmux):**
```bash
# Com screen
screen -S mercado_lateral
cd ~/downloads/sniper/mercado_lateral
python main.py
# Ctrl+A, D (detach do screen)

# Com tmux
tmux new -s mercado_lateral
cd ~/downloads/sniper/mercado_lateral
python main.py
# Ctrl+B, D (detach da sessão)
```

---

## 📊 Como Lucra em Lateral

**Exemplo prático (BTC lateral 65000-66000):**

```
#1 Entrada: LONG @ 65100
   └── BB lower, RSI=28, Stoch=15
   SL: 64775 (-0.5%)
   TP: 65750 (+1.0%)
   └── Lucro após 2h: +$65

#2 Entrada: SHORT @ 65900
   └── BB upper, RSI=72, Stoch=85
   SL: 66229 (-0.5%)
   TP: 65241 (+1.0%)
   └── Lucro após 3h: +$66

#3 Entrada: LONG @ 65250
   └── Suporte, RSI=25, WillR=-75
   SL: 64924 (-0.5%)
   TP: 65703 (+1.0%)
   └── Lucro após 4h: +$50

TOTAL: +$181 em 24h de lateral
```

### Vantagens

- ✅ **24/7 sem parar** - Sempre monitorando
- ✅ **Auto-detecta lateral** - Opera quando deve, espera quando não deve
- ✅ **Múltiplas entradas** - Até 3 simultâneas
- ✅ **Risco limitado** - Stop Loss estreito (0.5%)
- ✅ **Lucros frequentes** - Take Profit curto (1.0%)
- ✅ **Não perde oportunidades** - Detecta lateral automaticamente

---

## ⚠️ Avisos Importantes

- ✅ O bot **NUNCA PARA** - roda 24/7
- ✅ Em **STAND BY** só monitora, não abre posições
- ✅ Em **ACTIVE** busca e abre oportunidades
- ⚠️ Use leverage baixo (2x) para movimentos laterais
- ⚠️ Teste em modo PAPER primeiro
- ⚠️ Stop Loss estreito = pode ser atingido frequentemente

---

## 📈 Logs e Estatísticas

### Arquivos Gerados

| Arquivo | Conteúdo |
|---------|----------|
| `logs/runtime.log` | Log completo de operação |
| `logs/closed_trades.jsonl` | Histórico de trades |
| `logs/active_position.json` | Posições abertas |
| `logs/session_stats.json` | Estatísticas da sessão |

### Estatísticas

```
RESUMO DA SESSÃO
━━━━━━━━━━━━━━━━━━━━━
Duração: X.Xh
Scans totais: XXX
Scans STAND BY: XXX
Scans ACTIVE: XXX
Entradas: XX
Saídas: XX
Wins: X | Losses: X
Win Rate: XX.X%
PnL Total: ±$XXX.XX
Tempo STAND BY: X.XXh
Tempo ACTIVE: X.XXh
━━━━━━━━━━━━━━━━━━━━━
```

---

## 🎯 Fluxo de Operação Completo

```
┌─────────────────────────────────────────────────┐
│                                             │
│   🔁 BOT INICIADO (24/7)                │
│                                             │
│   [LOOP INFINITO - NUNCA PARA]           │
│                                             │
│   1️⃣ Checar posições (sempre)           │
│   2️⃣ Analisar mercado (a cada 60s)       │
│      │                                     │
│      ├─ Se TENDÊNCIA → ⏸️ STAND BY      │
│      │   • Monitora apenas               │
│      │   • Não abre novas               │
│      │   • Aguarda lateral              │
│      │                                     │
│      └─ Se LATERAL → ▶️ ACTIVE          │
│          • Busca oportunidades           │
│          • Abre quando score ≥ 2.5      │
│          • Até 3 posições               │
│                                             │
└─────────────────────────────────────────────────┘
```

---

**BOT 24/7 - AUTO-DETECT MERCADO LATERAL** ✅
