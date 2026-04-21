# AI Hedge Fund Integration

OpenAlgo integrates with [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) to generate AI-powered trading signals using legendary investor frameworks (Warren Buffett, Peter Lynch, etc.) and then execute those signals through OpenAlgo's broker API.

> **Educational purpose**: This integration is for research and paper-trading exploration.  
> Always test in sandbox/analyzer mode before enabling live order execution.

---

## Architecture

```
virattt/ai-hedge-fund              OpenAlgo
─────────────────────              ───────────────────────────────────────
13+ AI Analyst Agents  ──signals──▶  /api/v1/ai/signals  (read only)
  Warren Buffett                      /api/v1/ai/execute  (place orders)
  Peter Lynch                                │
  Rakesh Jhunjhunwala                        │
  ... (17 agents total)                      ▼
  Risk Manager                     place_order_service.py
  Portfolio Manager                          │
                                             ▼
                       Market data ◀──── 24+ Indian Brokers (NSE/BSE/NFO…)
                       (Historify OHLCV)
```

The AI agents analyse the symbols and produce **BUY / SELL / HOLD** decisions with confidence scores and quantities.  OpenAlgo then converts those decisions into broker orders.

---

## Quick Start

### 1 — Install ai-hedge-fund

```bash
# Option A – install directly from GitHub
pip install git+https://github.com/virattt/ai-hedge-fund.git

# Option B – clone and install in editable mode (recommended for customisation)
git clone https://github.com/virattt/ai-hedge-fund.git /opt/ai-hedge-fund
cd /opt/ai-hedge-fund
pip install -e .
```

### 2 — Configure API keys

Add at least one LLM key to your `.env` file:

```env
# OpenAI (recommended)
OPENAI_API_KEY=sk-...

# OR Anthropic
ANTHROPIC_API_KEY=...

# OR Groq (free tier available)
GROQ_API_KEY=...

# OR Google Gemini
GOOGLE_API_KEY=...

# OR run locally with Ollama (no key required)
OLLAMA_BASE_URL=http://localhost:11434
```

For **US stocks** also add:

```env
FINANCIAL_DATASETS_API_KEY=...   # from financialdatasets.ai
```

For **Indian stocks** no additional data key is required — OpenAlgo's Historify
provides the OHLCV data needed for technical analysis.

### 3 — Restart OpenAlgo

```bash
uv run app.py
```

The new endpoints are now available at:

| Endpoint | Method | Description |
|---|---|---|
| `/api/v1/ai/signals` | POST | Generate signals (no orders placed) |
| `/api/v1/ai/execute` | POST | Generate signals and place orders |
| `/api/v1/ai/analysts` | GET | List available analyst keys |

---

## API Reference

### `POST /api/v1/ai/signals`

Runs the selected AI analyst agents and returns trading signals.  
**No orders are placed.**

**Request body:**

```json
{
  "apikey": "your-openalgo-api-key",
  "symbols": ["RELIANCE", "INFY", "TCS"],
  "analysts": ["warren_buffett", "peter_lynch"],
  "lookback_days": 90,
  "show_reasoning": false,
  "model_provider": "OpenAI",
  "model_name": "gpt-4o-mini",
  "portfolio": {
    "cash": 1000000.0,
    "margin_requirement": 0.0,
    "margin_used": 0.0,
    "positions": {}
  }
}
```

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `apikey` | string | ✅ | — | OpenAlgo API key |
| `symbols` | list[str] | ✅ | — | Ticker symbols to analyse |
| `analysts` | list[str] | ❌ | all | Subset of analyst keys (see below) |
| `lookback_days` | int | ❌ | 90 | Days of historical data to use |
| `show_reasoning` | bool | ❌ | false | Include agent reasoning in response |
| `model_provider` | string | ❌ | auto | LLM provider name |
| `model_name` | string | ❌ | auto | LLM model name |
| `portfolio` | object | ❌ | ₹10 lakh cash | Current portfolio state |

**Response:**

```json
{
  "status": "success",
  "decisions": {
    "RELIANCE": {
      "action": "buy",
      "quantity": 10,
      "confidence": 0.78,
      "reasoning": "Strong brand moat, consistent FCF..."
    },
    "INFY": {
      "action": "hold",
      "quantity": 0,
      "confidence": 0.55
    }
  },
  "analyst_signals": { ... },
  "metadata": {
    "symbols": ["RELIANCE", "INFY"],
    "start_date": "2025-01-20",
    "end_date": "2025-04-20",
    "model": "gpt-4o-mini",
    "provider": "OpenAI"
  }
}
```

---

### `POST /api/v1/ai/execute`

Generates signals **and** places orders via the configured broker.

All fields from `/api/v1/ai/signals` are supported, plus:

| Field | Type | Default | Description |
|---|---|---|---|
| `exchange` | string | `NSE` | Exchange code (`NSE`, `BSE`, `NFO`, …) |
| `product` | string | `CNC` | Product type (`CNC`, `MIS`, `NRML`) |
| `price_type` | string | `MARKET` | Price type (`MARKET`, `LIMIT`) |
| `dry_run` | bool | `true` | When `true`, return orders without placing them |

> **Always use `dry_run: true` first** to review the orders before enabling live execution.

**Example (dry run):**

```bash
curl -X POST http://127.0.0.1:5000/api/v1/ai/execute \
  -H "Content-Type: application/json" \
  -d '{
    "apikey": "your-api-key",
    "symbols": ["RELIANCE", "INFY"],
    "dry_run": true
  }'
```

---

### `GET /api/v1/ai/analysts`

Returns the list of all analyst keys that can be passed in the `analysts` field.

```json
{
  "status": "success",
  "analysts": [
    "aswath_damodaran",
    "ben_graham",
    "bill_ackman",
    "cathie_wood",
    "charlie_munger",
    "michael_burry",
    "mohnish_pabrai",
    "nassim_taleb",
    "peter_lynch",
    "phil_fisher",
    "rakesh_jhunjhunwala",
    "stanley_druckenmiller",
    "warren_buffett",
    "fundamentals_agent",
    "sentiment_agent",
    "technicals_agent",
    "valuation_agent"
  ]
}
```

---

## Using with Indian Stocks

The ai-hedge-fund was originally designed for US stocks using the
[financialdatasets.ai](https://financialdatasets.ai) API.  For Indian stocks
(NSE/BSE), technical analysis agents (`technicals_agent`) work out of the box
using OpenAlgo's Historify OHLCV data once you configure the data bridge.

**Agent compatibility for Indian stocks:**

| Agent | Works with Indian stocks? | Notes |
|---|---|---|
| `technicals_agent` | ✅ Yes | Uses OHLCV data from Historify |
| `sentiment_agent` | ⚠️ Partial | Financial news sources may be US-focused |
| `fundamentals_agent` | ⚠️ Partial | Requires Indian fundamental data source |
| `valuation_agent` | ⚠️ Partial | Requires Indian financial statements |
| `warren_buffett` | ⚠️ Partial | Valuation reasoning works; data sourcing needs Indian adaptor |
| `rakesh_jhunjhunwala` | ⚠️ Partial | India-focused logic; needs Indian data |

For full Indian market support:
1. Implement `ai_hedge_fund/tools/openalgo_data.py` — a custom data tool that
   serves OHLCV from Historify and fundamental data from BSE/NSE filings.
2. Monkey-patch the `financial_datasets` tool calls in the ai-hedge-fund
   `src/tools/` module to use your custom tool.

A reference implementation is planned in a future release.

---

## Python Usage

You can also call the service directly from Python strategies:

```python
from ai_hedge_fund.service import generate_signals, build_openalgo_orders
from services.place_order_service import place_order

# Generate signals
result = generate_signals(
    symbols=["RELIANCE", "INFY", "TCS"],
    analysts=["warren_buffett", "peter_lynch"],
    lookback_days=90,
)

print(result["decisions"])
# {
#   "RELIANCE": {"action": "buy", "quantity": 10, "confidence": 0.78},
#   "INFY":     {"action": "hold", "quantity": 0},
# }

# Convert to OpenAlgo orders
orders = build_openalgo_orders(
    decisions=result["decisions"],
    apikey="your-openalgo-api-key",
    exchange="NSE",
    product="CNC",
)

# Place orders (remove dry_run check for live execution)
for order in orders:
    success, response, status = place_order(order_data=order, api_key=order["apikey"])
    print(response)
```

---

## Sandbox / Paper Trading

To test AI signals without risking real money, enable analyzer mode:

1. Go to **Settings → Analyzer Mode → Enable**
2. Call `/api/v1/ai/execute` with `dry_run: false`
3. All orders are routed to the sandbox with ₹1 Crore virtual capital

---

## Rate Limits

AI signal generation calls external LLM APIs and can take 30–120 seconds
depending on the number of analysts selected.  The default rate limit is
**5 requests per minute** per API key.

Override in `.env`:

```env
AI_RATE_LIMIT=2 per minute
```

---

## Troubleshooting

**`ImportError: The ai-hedge-fund package is not installed`**  
→ Run `pip install git+https://github.com/virattt/ai-hedge-fund.git`

**`EnvironmentError: No LLM API key found`**  
→ Add `OPENAI_API_KEY` (or another LLM key) to your `.env` and restart OpenAlgo.

**`503 Service Unavailable`**  
→ Either ai-hedge-fund is not installed or no LLM API key is configured.  
Check `log/errors.jsonl` for the full error message.

**Signals take too long / timeout**  
→ Use fewer analysts (`"analysts": ["warren_buffett"]`) or a faster model
(`"model_name": "gpt-4o-mini"`, `"model_provider": "OpenAI"`).

---

## Related

- [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) — upstream signal engine
- [Replay Mode](replay-mode.md) — replay historical data to backtest AI signals
- [Sandbox / Analyzer](../blueprints/analyzer.py) — paper trading with AI signals
- [API Reference](../restx_api/ai_signals.py) — endpoint implementation
