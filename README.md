# Polymarket Arbitrage Bot

Trades the spread between a Binance spot price and its Polymarket binary-outcome
probability, using momentum signals, fractional Kelly sizing, and hard risk limits.

## Architecture

```
Binance WS ──┐
             ├─► signal.py (momentum edge) ──► sizer.py (Kelly) ──► risk.py ──► executor.py
Poly CLOB ───┘                                                                       │
                                                                              py-clob-client
latency.py instruments every stage ──► github_tracker.py (30-min GitHub Issue posts)
```

| Module | Role |
|--------|------|
| `feed.py` | Binance WebSocket + Polymarket CLOB polling |
| `signal.py` | Momentum-filtered edge detection |
| `sizer.py` | Fractional Kelly position sizing |
| `executor.py` | Order submission via py-clob-client |
| `risk.py` | Pre-trade checks, drawdown/daily-loss halts |
| `latency.py` | Rolling p50/p95/p99 instrumentation |
| `github_tracker.py` | Posts stats to a GitHub Issue every 30 min |
| `main.py` | asyncio entry point |

## Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env — set CLOB_API_KEY, POLY_TOKEN_ID, BANKROLL_USD, etc.

# 3. Dry run (no real orders)
DRY_RUN=true python -m src.main

# 4. Live
DRY_RUN=false python -m src.main
```

## Configuration

All tunables live in `.env` — see [`.env.example`](.env.example) for the full list.

Key parameters:

| Variable | Default | Description |
|----------|---------|-------------|
| `POLY_TOKEN_ID` | — | Polymarket outcome token to trade |
| `BINANCE_SYMBOL` | `BTCUSDT` | Binance reference pair |
| `MIN_EDGE` | `0.02` | Minimum prob edge to fire a signal |
| `KELLY_FRACTION` | `0.25` | Fraction of full Kelly (0–1) |
| `MAX_DRAWDOWN_PCT` | `0.10` | Halt threshold from peak bankroll |
| `DRY_RUN` | `true` | Paper-trade mode |

## Price → probability mapping

The default `btc_price_to_prob()` in `main.py` maps a Binance BTC price to an
implied probability using a logistic curve centred at `BTC_TARGET_PRICE`.
Replace this function for any other market.

## Tests

```bash
pytest tests/ -v
```

The CI workflow (`.github/workflows/latency-check.yml`) runs latency unit tests
and a benchmark on every push/PR, and fails if the signal-evaluation overhead
exceeds 1 ms p99.

## GitHub Issue tracker

Set `GITHUB_TOKEN`, `GITHUB_REPO`, and `GITHUB_ISSUE` in `.env`.  The bot
appends a Markdown stats comment (portfolio, fills, latency table) to that issue
every 30 minutes, and once more on clean shutdown.

## Risk controls

- **Drawdown halt**: stops trading if portfolio falls > `MAX_DRAWDOWN_PCT` from peak
- **Daily loss halt**: stops trading if daily P&L < –`MAX_DAILY_LOSS_PCT`
- **Per-trade notional cap**: hard `MAX_NOTIONAL` USD limit per order
- **Spread filter**: rejects orders when Polymarket spread exceeds 10%
- **Cooldown**: enforces minimum time between fills on the same token
