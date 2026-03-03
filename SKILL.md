---
name: stock-analyst-deploy
description: Build, validate, and deploy the iStockPick API from the backend/ directory, including recommendation/scoring-data endpoints, shared-model APIs, verbose mode, and server runtime wiring.
---

# Stock Analyst Deploy Skill

Deploy and validate the current stock-analyst API implementation.

## Expected Endpoints

For production domain (`api.istockpick.ai`), ensure these are live:

1. `GET /health`
2. `POST /api/v1/agents/register`
3. `POST /api/v1/models/share`
4. `GET|POST /api/v1/shared-models/recommendation`
5. `GET|POST /api/v1/recommendation` (supports `asset_type`: stock, crypto, option, future)
6. `GET|POST /api/v1/scoring-data` (supports `asset_type`)
7. `GET /api/v1/weights`
8. `GET /api/v1/model-leaderboard`
9. `GET|POST /api/v1/portfolio`
10. `GET /api/v1/congress/trades` (params: `year`, `chamber`, `symbol`, `politician`)
11. `GET /api/v1/congress/roi` (params: `year`, `chamber`, `top_n`)
12. `GET /api/v1/congress/seasonal` (params: `year`, `chamber`)
13. `GET /api/v1/options/chain` (params: `symbol`, `expiry`)

## Recommendation Response Modes

`/api/v1/recommendation` supports `verbose` (and legacy alias `verborse`).
It also supports optional `model_name` for personalized model selection,
and `asset_type` (stock, crypto, option, future) for multi-asset analysis.

1. Default (`verbose=false` or omitted):
- Action-only payload: `{"recommendation":"BUY|HOLD|SELL"}`

2. `verbose=true`:
- Detailed payload with sub-sections:
- `stock_analysis`
- `sentiment_analysis`
- `ai_recommendation`
- `scoring_weights`
- `model_name`

## Scoring-Data Endpoint

`/api/v1/scoring-data` returns:

1. `price`
2. `snapshot` (raw market data)
3. `scoring_inputs` (raw scoring factors)
4. `scoring_weights`
5. metadata (`input`, `resolved_symbol`, `company`, `generated_at`)

Supports optional weights override:

1. GET: `weights` as JSON-encoded query string.
2. POST: `weights` as JSON object body.
3. Optional `model_name` (GET query or POST body) selects a named personalized model.

## Weights Discovery + Persistence

1. `GET /api/v1/weights` returns all modifiable keys with default/min/max and threshold rules.
2. Per-agent model persistence is stored in:
- `backend/data/weights.txt`
3. Model portfolio metadata is stored in:
- `backend/data/portfolio.txt`
4. Recommendation/scoring calls use weights in this order:
- Request `weights` override (if provided), and persist it for the selected model.
- Saved agent/model weights from `backend/data/weights.txt`.
- Hardcoded defaults.
5. Default-model behavior:
- If `model_name` is omitted, updates/read apply to the agent's `default` model.
- If `model_name` is provided and missing, API returns 404.

## Shared Models

1. `POST /api/v1/models/share`
- Requires `agent_name` and `agent_token`.
- Accepts optional `model_name`.
- Accepts optional `external_name`, which is the public identifier the owner exposes to other developers.
- `shared=true` publishes the model; `shared=false` unpublishes it.

2. `GET|POST /api/v1/shared-models/recommendation`
- Requires caller `agent_name` and `agent_token`.
- Requires `owner_agent_name`.
- Resolves the shared model by either:
- `external_name` (preferred public identifier), or
- `model_name` (owner/internal identifier).
- Returns shared-model recommendation output plus:
- `model_owner`
- `called_by`
- `model_name`
- `external_name`

3. Shared model metadata is stored alongside model weights in:
- `backend/data/weights.txt`

4. `external_name` rules:
- Optional.
- Must be unique per owner across that owner's saved models.
- Persists across future weight updates for the same model.

## Portfolio Leaderboard

`GET /api/v1/model-leaderboard` returns rows with:

1. `agent_name`
2. `portfolio_name`
3. `model_name`
4. `daily_change_pct`
5. `weekly_change_pct`
6. `monthly_change_pct`
7. display strings + delta integers (for UI coloring)

## Portfolio Endpoint

Dedicated portfolio persistence endpoint:

1. `GET /api/v1/portfolio?agent_name=...&model_name=...`
- Returns the saved portfolio for that agent/model pair.

2. `POST /api/v1/portfolio`
- Required:
- `agent_name`
- `agent_token`
- `model_name`
- `positions` or `stock_recommendations` list
- Each list item must be:
- `{"symbol":"TICKER","recommendation":"BUY|SELL|HOLD"}`
- Optional:
- `portfolio_name`
- `daily_change_pct`, `weekly_change_pct`, `monthly_change_pct`
- `daily_change_delta`, `weekly_change_delta`, `monthly_change_delta`

## Setup

Run from `backend/`.

Using **uv** (recommended):

```bash
uv sync
```

Using **pip**:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Optional env for Alpaca provider:

```dotenv
APCA_API_KEY_ID=...
APCA_API_SECRET_KEY=...
ALPACA_DATA_BASE_URL=https://data.alpaca.markets
```

## Pre-Deploy Validation

1. Compile checks.

```bash
cd backend
python -m py_compile stock_analyst/api.py
python -m py_compile stock_analyst/web_analyzer.py
python -m py_compile stock_analyst/congress.py
python -m py_compile stock_analyst/crypto.py
python -m py_compile stock_analyst/futures.py
python -m py_compile stock_analyst/options.py
python -m py_compile server.py
```

2. Route checks for package app.

```bash
cd backend
python - <<'PY'
from stock_analyst.api import app
paths = {r.path for r in app.routes}
required = {
    "/health",
    "/api/v1/agents/register",
    "/api/v1/models/share",
    "/api/v1/recommendation",
    "/api/v1/shared-models/recommendation",
    "/api/v1/recommendations",
    "/api/v1/scoring-data",
    "/api/v1/congress/trades",
    "/api/v1/congress/roi",
    "/api/v1/congress/seasonal",
    "/api/v1/options/chain",
}
print("api_routes_ok", required.issubset(paths))
PY
```

## Runtime Notes

1. If domain traffic is served by `backend/server.py`, restart that process after changes.
2. If traffic is served by FastAPI directly, run:

```bash
cd backend
uvicorn stock_analyst.api:app --host 0.0.0.0 --port 8000
```

## Smoke Tests

1. Health.

```bash
curl "http://api.istockpick.ai/health"
```

2. Recommendation default (action-only).

```bash
curl "http://api.istockpick.ai/api/v1/recommendation?stock=AAPL&agent_name=agent-alpha&agent_token=REPLACE_WITH_TOKEN"
```

3. Recommendation verbose.

```bash
curl "http://api.istockpick.ai/api/v1/recommendation?stock=AAPL&agent_name=agent-alpha&agent_token=REPLACE_WITH_TOKEN&verbose=true"
```

4. Scoring data.

```bash
curl "http://api.istockpick.ai/api/v1/scoring-data?stock=AAPL&agent_name=agent-alpha&agent_token=REPLACE_WITH_TOKEN"
```

5. Scoring data with weights override.

```bash
curl "http://api.istockpick.ai/api/v1/scoring-data?stock=AAPL&agent_name=agent-alpha&agent_token=REPLACE_WITH_TOKEN&weights=%7B%22trend_bullish%22%3A20%2C%22action_buy_threshold%22%3A70%7D"
```

6. Weights metadata endpoint.

```bash
curl "http://api.istockpick.ai/api/v1/weights"
```

7. Model leaderboard endpoint.

```bash
curl "http://api.istockpick.ai/api/v1/model-leaderboard"
```

8. Portfolio GET endpoint.

```bash
curl "http://api.istockpick.ai/api/v1/portfolio?agent_name=agent-alpha&model_name=default"
```

9. Portfolio POST endpoint.

```bash
curl -X POST "http://api.istockpick.ai/api/v1/portfolio" \
  -H "Content-Type: application/json" \
  -d '{"agent_name":"agent-alpha","agent_token":"REPLACE_WITH_TOKEN","model_name":"growth","stock_recommendations":[{"symbol":"AAPL","recommendation":"SELL"},{"symbol":"GOOG","recommendation":"BUY"},{"symbol":"META","recommendation":"HOLD"}]}'
```

10. Congress trades endpoint.

```bash
curl "http://api.istockpick.ai/api/v1/congress/trades?year=2025&chamber=all"
```

11. Share model endpoint.

```bash
curl -X POST "http://api.istockpick.ai/api/v1/models/share" \
  -H "Content-Type: application/json" \
  -d '{"agent_name":"agent-alpha","agent_token":"REPLACE_WITH_TOKEN","model_name":"growth","external_name":"agent-alpha-growth","shared":true}'
```

12. Shared-model recommendation endpoint.

```bash
curl -X POST "http://api.istockpick.ai/api/v1/shared-models/recommendation" \
  -H "Content-Type: application/json" \
  -d '{"stock":"AAPL","agent_name":"agent-beta","agent_token":"REPLACE_WITH_CALLER_TOKEN","owner_agent_name":"agent-alpha","external_name":"agent-alpha-growth","verbose":true}'
```

13. Congress ROI endpoint.

```bash
curl "http://api.istockpick.ai/api/v1/congress/roi?year=2025&chamber=senate&top_n=10"
```

14. Congress seasonal endpoint.

```bash
curl "http://api.istockpick.ai/api/v1/congress/seasonal?year=2025"
```

15. Options chain endpoint.

```bash
curl "http://api.istockpick.ai/api/v1/options/chain?symbol=AAPL"
```

14. Crypto recommendation.

```bash
curl "http://api.istockpick.ai/api/v1/recommendation?stock=BTC-USD&asset_type=crypto&agent_name=agent-alpha&agent_token=REPLACE_WITH_TOKEN&verbose=true"
```

15. Futures recommendation.

```bash
curl "http://api.istockpick.ai/api/v1/recommendation?stock=ES=F&asset_type=future&agent_name=agent-alpha&agent_token=REPLACE_WITH_TOKEN&verbose=true"
```

## Sample Scripts

Run from `backend/`.

1. Detail call sample.

```bash
python3 samples/istockpick_reco_detail.py
```

2. Multi-symbol scan sample.

```bash
python3 samples/istockpick_reco_scan.py
```

## Definition of Done

1. Runtime is serving updated code path (no stale entrypoint mismatch).
2. Recommendation endpoint honors `verbose` behavior.
3. Scoring-data endpoint is reachable and returns price + raw scoring inputs.
4. `/api/v1/weights` returns all modifiable keys + ranges.
5. Named model behavior works (`model_name` + default fallback).
6. `/api/v1/model-leaderboard` returns rows from portfolio/weights DB.
7. `/api/v1/portfolio` supports GET by `agent_name` + `model_name`.
8. `/api/v1/portfolio` supports authenticated POST updates for portfolio positions.
9. Authenticated calls succeed with registered agent credentials.
10. Compile checks pass for analyzer API and server entrypoint.
11. `asset_type` parameter works across recommendation/scoring endpoints for stock, crypto, option, and future.
12. Congress endpoints (`/api/v1/congress/trades`, `/roi`, `/seasonal`) return data.
13. Options chain endpoint (`/api/v1/options/chain`) returns calls/puts with summary.
14. Frontend asset type selector, congress card, and options chain display work.
