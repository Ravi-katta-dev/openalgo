"""
AI Hedge Fund Signals API endpoint  (/api/v1/ai/signals)
=========================================================
Generates AI trading signals using the virattt/ai-hedge-fund engine
and optionally places the resulting orders via OpenAlgo's broker layer.

Two sub-resources are exposed:

    POST /api/v1/ai/signals
        Run the AI agents on the supplied symbols and return signals.

    POST /api/v1/ai/execute
        Run the AI agents and immediately place orders (or route to sandbox).

Both endpoints require a valid OpenAlgo API key in the JSON body.

See docs/ai-hedge-fund.md for setup instructions, including installing
the ai-hedge-fund package and configuring your LLM API keys.
"""

from __future__ import annotations

import os

from flask import jsonify, make_response, request
from flask_restx import Namespace, Resource, fields

from database.auth_db import get_auth_token_broker
from limiter import limiter
from utils.logging import get_logger

logger = get_logger(__name__)

AI_RATE_LIMIT = os.getenv("AI_RATE_LIMIT", "5 per minute")

api = Namespace("ai", description="AI Hedge Fund Signal Generation API")

# ---------------------------------------------------------------------------
# Request / response models (used for Swagger docs)
# ---------------------------------------------------------------------------
_position_model = api.model(
    "AIPosition",
    {
        "long": fields.Integer(default=0, description="Long quantity held"),
        "short": fields.Integer(default=0, description="Short quantity held"),
        "long_cost_basis": fields.Float(default=0.0),
        "short_cost_basis": fields.Float(default=0.0),
        "short_margin_used": fields.Float(default=0.0),
    },
)

_portfolio_model = api.model(
    "AIPortfolio",
    {
        "cash": fields.Float(
            default=1000000.0,
            description="Available cash in the portfolio (₹ for Indian markets)",
        ),
        "margin_requirement": fields.Float(default=0.0),
        "margin_used": fields.Float(default=0.0),
        "positions": fields.Raw(description="Positions keyed by symbol"),
    },
)

_signals_request_model = api.model(
    "AISignalsRequest",
    {
        "apikey": fields.String(required=True, description="OpenAlgo API key"),
        "symbols": fields.List(
            fields.String,
            required=True,
            description="Ticker symbols to analyse (e.g. ['RELIANCE', 'INFY'])",
        ),
        "portfolio": fields.Nested(
            _portfolio_model,
            description="Current portfolio state (optional; defaults to ₹10 lakh cash)",
        ),
        "analysts": fields.List(
            fields.String,
            description="Analyst keys to run (optional; defaults to all analysts)",
        ),
        "model_name": fields.String(
            description="LLM model name (auto-detected from env if omitted)",
        ),
        "model_provider": fields.String(
            description="LLM provider: OpenAI | Anthropic | Groq | DeepSeek | Google | Ollama",
        ),
        "lookback_days": fields.Integer(
            default=90, description="Days of historical data to feed to agents"
        ),
        "show_reasoning": fields.Boolean(
            default=False,
            description="Include agent reasoning chains in the response",
        ),
    },
)

_execute_request_model = api.model(
    "AIExecuteRequest",
    {
        "apikey": fields.String(required=True, description="OpenAlgo API key"),
        "symbols": fields.List(fields.String, required=True),
        "portfolio": fields.Nested(_portfolio_model),
        "analysts": fields.List(fields.String),
        "model_name": fields.String(),
        "model_provider": fields.String(),
        "lookback_days": fields.Integer(default=90),
        "show_reasoning": fields.Boolean(default=False),
        "exchange": fields.String(
            default="NSE",
            description="Exchange for order placement (NSE, BSE, NFO, …)",
        ),
        "product": fields.String(
            default="CNC", description="Product type: CNC | MIS | NRML"
        ),
        "price_type": fields.String(
            default="MARKET", description="Price type: MARKET | LIMIT"
        ),
        "dry_run": fields.Boolean(
            default=True,
            description="When true, return orders without placing them (default: true)",
        ),
    },
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _validate_api_key(api_key: str) -> tuple[bool, str | None]:
    """Verify API key and return (ok, broker)."""
    if not api_key:
        return False, None
    try:
        _auth_token, broker = get_auth_token_broker(api_key)
        return bool(_auth_token), broker
    except Exception:
        return False, None


# ---------------------------------------------------------------------------
# /api/v1/ai/signals  – generate signals only
# ---------------------------------------------------------------------------

@api.route("/signals", strict_slashes=False)
class AISignals(Resource):
    @limiter.limit(AI_RATE_LIMIT)
    @api.expect(_signals_request_model)
    def post(self):
        """Generate AI trading signals for the supplied symbols.

        Runs the selected ai-hedge-fund analyst agents (Warren Buffett, Peter
        Lynch, etc.) and the portfolio manager, then returns buy/sell/hold
        signals with confidence scores.

        **This endpoint does NOT place any orders.**  Use ``/api/v1/ai/execute``
        to also route orders to the broker.
        """
        try:
            data = request.json or {}
            api_key = data.get("apikey")

            ok, _broker = _validate_api_key(api_key)
            if not ok:
                return make_response(
                    jsonify({"status": "error", "message": "Invalid API key"}), 403
                )

            symbols = data.get("symbols")
            if not symbols or not isinstance(symbols, list):
                return make_response(
                    jsonify({"status": "error", "message": "symbols must be a non-empty list"}),
                    400,
                )

            from ai_hedge_fund.service import generate_signals  # noqa: PLC0415

            result = generate_signals(
                symbols=symbols,
                portfolio=data.get("portfolio"),
                analysts=data.get("analysts"),
                model_name=data.get("model_name"),
                model_provider=data.get("model_provider"),
                lookback_days=int(data.get("lookback_days", 90)),
                show_reasoning=bool(data.get("show_reasoning", False)),
            )

            return make_response(jsonify({"status": "success", **result}), 200)

        except ImportError as exc:
            logger.warning("ai-hedge-fund not installed: %s", exc)
            return make_response(
                jsonify({"status": "error", "message": str(exc)}), 503
            )
        except EnvironmentError as exc:
            logger.warning("AI Hedge Fund configuration error: %s", exc)
            return make_response(
                jsonify({"status": "error", "message": str(exc)}), 503
            )
        except Exception:
            logger.exception("Unexpected error in AISignals endpoint")
            return make_response(
                jsonify({"status": "error", "message": "Internal server error"}), 500
            )


# ---------------------------------------------------------------------------
# /api/v1/ai/execute  – generate signals and place orders
# ---------------------------------------------------------------------------

@api.route("/execute", strict_slashes=False)
class AIExecute(Resource):
    @limiter.limit(AI_RATE_LIMIT)
    @api.expect(_execute_request_model)
    def post(self):
        """Generate AI trading signals and place the resulting orders.

        Runs the analyst agents, then converts the portfolio-manager decisions
        into OpenAlgo orders.  When ``dry_run`` is ``true`` (default), the
        orders are returned without being sent to the broker — allowing you to
        review them before enabling live execution.

        Set ``dry_run: false`` to place live orders via the configured broker.

        .. warning::
            Live order placement uses real funds.  Always test with
            ``dry_run: true`` first, and consider using the sandbox/analyzer
            mode (set ``ANALYZE_MODE=True`` in Settings) before going live.
        """
        try:
            data = request.json or {}
            api_key = data.get("apikey")

            ok, broker = _validate_api_key(api_key)
            if not ok:
                return make_response(
                    jsonify({"status": "error", "message": "Invalid API key"}), 403
                )

            symbols = data.get("symbols")
            if not symbols or not isinstance(symbols, list):
                return make_response(
                    jsonify({"status": "error", "message": "symbols must be a non-empty list"}),
                    400,
                )

            from ai_hedge_fund.service import build_openalgo_orders, generate_signals  # noqa: PLC0415

            result = generate_signals(
                symbols=symbols,
                portfolio=data.get("portfolio"),
                analysts=data.get("analysts"),
                model_name=data.get("model_name"),
                model_provider=data.get("model_provider"),
                lookback_days=int(data.get("lookback_days", 90)),
                show_reasoning=bool(data.get("show_reasoning", False)),
            )

            decisions = result.get("decisions", {})
            orders = build_openalgo_orders(
                decisions=decisions,
                apikey=api_key,
                exchange=str(data.get("exchange", "NSE")),
                product=str(data.get("product", "CNC")),
                price_type=str(data.get("price_type", "MARKET")),
            )

            dry_run = bool(data.get("dry_run", True))

            if dry_run:
                return make_response(
                    jsonify({
                        "status": "success",
                        "dry_run": True,
                        "message": (
                            "Orders generated but NOT placed (dry_run=true). "
                            "Set dry_run=false to place live orders."
                        ),
                        "orders": orders,
                        **result,
                    }),
                    200,
                )

            # Live execution — place each order via the place_order service
            from services.place_order_service import place_order  # noqa: PLC0415

            placed: list[dict] = []
            errors: list[dict] = []

            for order in orders:
                try:
                    success, response_data, _status = place_order(
                        order_data=order, api_key=api_key
                    )
                    if success:
                        placed.append({"symbol": order["symbol"], "response": response_data})
                    else:
                        errors.append({"symbol": order["symbol"], "error": response_data})
                except Exception as order_exc:
                    logger.exception(
                        "Failed to place order for %s: %s", order.get("symbol"), order_exc
                    )
                    errors.append({"symbol": order.get("symbol"), "error": str(order_exc)})

            status_code = 200 if not errors else 207
            return make_response(
                jsonify({
                    "status": "success" if not errors else "partial",
                    "dry_run": False,
                    "placed": placed,
                    "errors": errors,
                    **result,
                }),
                status_code,
            )

        except ImportError as exc:
            logger.warning("ai-hedge-fund not installed: %s", exc)
            return make_response(
                jsonify({"status": "error", "message": str(exc)}), 503
            )
        except EnvironmentError as exc:
            logger.warning("AI Hedge Fund configuration error: %s", exc)
            return make_response(
                jsonify({"status": "error", "message": str(exc)}), 503
            )
        except Exception:
            logger.exception("Unexpected error in AIExecute endpoint")
            return make_response(
                jsonify({"status": "error", "message": "Internal server error"}), 500
            )


# ---------------------------------------------------------------------------
# /api/v1/ai/analysts  – list available analysts
# ---------------------------------------------------------------------------

@api.route("/analysts", strict_slashes=False)
class AIAnalysts(Resource):
    def get(self):
        """Return the list of available AI analyst keys."""
        from ai_hedge_fund.service import AVAILABLE_ANALYSTS  # noqa: PLC0415

        return make_response(
            jsonify({"status": "success", "analysts": AVAILABLE_ANALYSTS}), 200
        )
