# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

High-frequency arbitrage bot that exploits price delay between Binance spot BTC and Polymarket CLOB for BTC 5-minute UP/DOWN prediction markets. Core thesis: Binance price changes lead Polymarket by 0.3%+, allowing the bot to front-run re-pricing. Written in async Python.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env  # edit API keys; runs in dry-run mode by default

# Run the bot
./run.sh              # sets proxy env vars, then runs python3 src/main.py
python src/main.py    # run directly (without proxy)

# Dashboard
# Available at http://localhost:8080 (note: README says 5000 but code uses 8080)
```

No test suite exists yet. No linter is configured.

## Architecture

**Data flow**: Binance WebSocket → `binance_ws.py` → `delay_detector.py` (spread + signal analysis) → `Scalper.evaluate_and_execute()` → `risk_manager.py` (position sizing + risk checks) → `data_recorder.py` (SQLite) → `dashboard.py` (Flask API) → `dashboard.html` (Chart.js).

**Three concurrent async tasks** via `asyncio.gather()` in `main.py`:
1. `binance_ws.connect()` — streams real-time BTC trades
2. `monitor_polymarket()` — polls Polymarket mid-price every 100ms
3. `check_opportunities()` — every 500ms checks for arbitrage, records data

The Flask dashboard runs in a separate daemon thread.

**Module responsibilities**:
- `main.py` — `PolymarketScalper` orchestrator class. Initializes components, runs async loop. Simulates trades in dry-run mode.
- `binance_ws.py` — `BinanceWebSocket` class. Connects to Binance trade stream + fetches 5min klines via REST.
- `polymarket_clob.py` — `PolymarketCLOB` class. Wraps Polymarket CLOB REST API (uses raw aiohttp, not py_clob_client).
- `delay_detector.py` — `DelayDetector` (spread calculation, SMA/RSI/momentum signals) + `Scalper` (buy UP on BULL+negative spread, buy DOWN on BEAR+positive spread).
- `risk_manager.py` — `RiskManager` class. Kelly-inspired position sizing, daily loss limits, hard stop-loss, max 3 open positions.
- `data_recorder.py` — `DataRecorder` class. SQLite persistence (prices, opportunities, trades, stats tables).
- `dashboard.py` — Flask app with API endpoints (`/api/stats`, `/api/prices`, `/api/opportunities`, `/api/trades`, `/api/pnl_history`, `/api/health`).

## Key Technical Details

- **SSL verification is disabled** (`ssl.CERT_NONE`) in both Binance WS and Polymarket HTTP connections — needed when running through a proxy.
- **SQLite connections are not pooled** — `data_recorder.py` opens/closes a connection per operation.
- **Binance URLs are hardcoded** in `binance_ws.py` despite `BINANCE_WS_URL`/`BINANCE_REST_URL` existing in `.env.example`.
- **Unused dependencies** in requirements.txt: `pyyaml`, `pandas`, `py_clob_client` (code uses raw aiohttp for Polymarket).
- **Dry-run mode** simulates trade outcomes with `random.random()` rather than deterministic modeling.
- **Proxy configuration** is set via environment variables in `run.sh` (http_proxy, https_proxy, ALL_PROXY).
