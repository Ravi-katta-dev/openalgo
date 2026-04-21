"""
AI Hedge Fund Service
=====================
Bridges the virattt/ai-hedge-fund signal engine with OpenAlgo order execution.

Requires the ai-hedge-fund package (install separately):
    pip install git+https://github.com/virattt/ai-hedge-fund.git

Or clone and install in development mode:
    git clone https://github.com/virattt/ai-hedge-fund.git /opt/ai-hedge-fund
    pip install -e /opt/ai-hedge-fund

Required environment variables (add to your .env):
    OPENAI_API_KEY          - OpenAI API key (or GROQ_API_KEY / ANTHROPIC_API_KEY)
    FINANCIAL_DATASETS_API_KEY - financialdatasets.ai key (for US stocks)
                                 Indian stock data comes from OpenAlgo's Historify.

See docs/ai-hedge-fund.md for full setup instructions.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

from utils.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Available analyst keys (mirrors virattt/ai-hedge-fund ANALYST_ORDER)
# ---------------------------------------------------------------------------
AVAILABLE_ANALYSTS = [
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
    "valuation_agent",
]

# Supported LLM providers and their environment variable keys
LLM_PROVIDERS = {
    "OpenAI": "OPENAI_API_KEY",
    "Anthropic": "ANTHROPIC_API_KEY",
    "Groq": "GROQ_API_KEY",
    "DeepSeek": "DEEPSEEK_API_KEY",
    "Google": "GOOGLE_API_KEY",
    "Ollama": None,  # No key required for local Ollama
}


def _import_run_hedge_fund():
    """Lazily import run_hedge_fund from ai-hedge-fund to avoid hard dependency."""
    try:
        from src.main import run_hedge_fund  # noqa: PLC0415 (lazy import by design)

        return run_hedge_fund
    except ImportError as exc:
        raise ImportError(
            "The ai-hedge-fund package is not installed. "
            "Install it with:\n"
            "  pip install git+https://github.com/virattt/ai-hedge-fund.git\n"
            "or clone and install in editable mode:\n"
            "  git clone https://github.com/virattt/ai-hedge-fund.git /opt/ai-hedge-fund\n"
            "  pip install -e /opt/ai-hedge-fund\n"
            "See docs/ai-hedge-fund.md for details."
        ) from exc


def _detect_llm_provider() -> tuple[str, str]:
    """Auto-detect the available LLM provider from environment variables.

    Returns:
        (provider_name, model_name) tuple.

    Raises:
        EnvironmentError: when no supported LLM API key is configured.
    """
    provider_defaults: dict[str, tuple[str, str]] = {
        "OPENAI_API_KEY": ("OpenAI", "gpt-4o-mini"),
        "ANTHROPIC_API_KEY": ("Anthropic", "claude-3-5-haiku-20241022"),
        "GROQ_API_KEY": ("Groq", "llama-3.3-70b-versatile"),
        "DEEPSEEK_API_KEY": ("DeepSeek", "deepseek-chat"),
        "GOOGLE_API_KEY": ("Google", "gemini-2.0-flash"),
    }
    for env_var, (provider, model) in provider_defaults.items():
        if os.getenv(env_var):
            return provider, model

    if os.getenv("OLLAMA_BASE_URL") or os.getenv("OLLAMA_HOST"):
        return "Ollama", "llama3"

    raise EnvironmentError(
        "No LLM API key found. Set at least one of: "
        + ", ".join(provider_defaults.keys())
        + ". See docs/ai-hedge-fund.md for setup instructions."
    )


def generate_signals(
    symbols: list[str],
    portfolio: dict[str, Any] | None = None,
    analysts: list[str] | None = None,
    model_name: str | None = None,
    model_provider: str | None = None,
    lookback_days: int = 90,
    show_reasoning: bool = False,
) -> dict[str, Any]:
    """Run the AI hedge fund workflow and return trading signals.

    Calls ``run_hedge_fund()`` from the virattt/ai-hedge-fund package with the
    supplied symbols and returns normalised signal data ready for use by
    :func:`execute_signals`.

    Args:
        symbols: List of ticker symbols to analyse.
            - For **Indian stocks** use NSE symbols (e.g. ``["RELIANCE", "INFY"]``).
              The ai-hedge-fund agents will use OpenAlgo's Historify data (OHLCV)
              for technical analysis.  Fundamental data requires a separate Indian
              data provider (see docs/ai-hedge-fund.md).
            - For **US stocks** use standard tickers (e.g. ``["AAPL", "MSFT"]``).
              The agents fetch data from financialdatasets.ai automatically.
        portfolio: Current portfolio state.  Defaults to ₹10 lakh virtual cash
            with no open positions.  Format::

                {
                    "cash": 1000000.0,
                    "margin_requirement": 0.0,
                    "margin_used": 0.0,
                    "positions": {
                        "RELIANCE": {
                            "long": 0, "short": 0,
                            "long_cost_basis": 0.0,
                            "short_cost_basis": 0.0,
                            "short_margin_used": 0.0,
                        }
                    },
                }

        analysts: Subset of :data:`AVAILABLE_ANALYSTS` to run.
            Defaults to all analysts.
        model_name: LLM model name (e.g. ``"gpt-4o-mini"``).
            Auto-detected from environment when omitted.
        model_provider: LLM provider (e.g. ``"OpenAI"``).
            Auto-detected from environment when omitted.
        lookback_days: Number of calendar days of historical data to feed to
            the agents (default: 90).
        show_reasoning: When ``True`` the agents' full reasoning chains are
            included in the returned ``analyst_signals`` dict.

    Returns:
        Dict with keys:

        - ``decisions`` – portfolio-manager decisions keyed by symbol::

              {
                  "RELIANCE": {"action": "buy", "quantity": 10,
                               "confidence": 0.72, "reasoning": "..."},
                  "INFY":     {"action": "hold", "quantity": 0, ...},
              }

        - ``analyst_signals`` – raw signal data from each analyst agent.
        - ``metadata`` – run metadata (model, provider, timestamps, symbols).

    Raises:
        ImportError: if ai-hedge-fund is not installed.
        EnvironmentError: if no LLM API key is configured.
    """
    run_hedge_fund = _import_run_hedge_fund()

    # Auto-detect provider/model when not supplied
    if model_provider is None or model_name is None:
        detected_provider, detected_model = _detect_llm_provider()
        model_provider = model_provider or detected_provider
        model_name = model_name or detected_model

    # Build default portfolio if not provided
    if portfolio is None:
        portfolio = {
            "cash": float(os.getenv("AI_HEDGE_FUND_INITIAL_CASH", "1000000")),
            "margin_requirement": 0.0,
            "margin_used": 0.0,
            "positions": {
                sym: {
                    "long": 0,
                    "short": 0,
                    "long_cost_basis": 0.0,
                    "short_cost_basis": 0.0,
                    "short_margin_used": 0.0,
                }
                for sym in symbols
            },
        }

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    logger.info(
        "Starting AI hedge fund analysis: symbols=%s provider=%s model=%s",
        symbols,
        model_provider,
        model_name,
    )

    result = run_hedge_fund(
        tickers=symbols,
        start_date=start_date,
        end_date=end_date,
        portfolio=portfolio,
        show_reasoning=show_reasoning,
        selected_analysts=analysts or [],
        model_name=model_name,
        model_provider=model_provider,
    )

    decisions = result.get("decisions") or {}
    analyst_signals = result.get("analyst_signals") or {}

    logger.info(
        "AI hedge fund analysis complete: %d decisions generated", len(decisions)
    )

    return {
        "decisions": decisions,
        "analyst_signals": analyst_signals,
        "metadata": {
            "symbols": symbols,
            "start_date": start_date,
            "end_date": end_date,
            "model": model_name,
            "provider": model_provider,
            "analysts": analysts or AVAILABLE_ANALYSTS,
        },
    }


def build_openalgo_orders(
    decisions: dict[str, Any],
    apikey: str,
    exchange: str = "NSE",
    product: str = "CNC",
    price_type: str = "MARKET",
) -> list[dict[str, Any]]:
    """Convert AI hedge fund decisions into OpenAlgo order dicts.

    Maps the portfolio-manager output (``action`` / ``quantity``) to the
    standard OpenAlgo order format accepted by ``/api/v1/placeorder``.

    Args:
        decisions: The ``decisions`` dict returned by :func:`generate_signals`.
        apikey: OpenAlgo API key for authentication.
        exchange: Exchange code (``"NSE"``, ``"BSE"``, etc.).
        product: Product type (``"CNC"`` for delivery, ``"MIS"`` for intraday).
        price_type: Price type (``"MARKET"``, ``"LIMIT"``).

    Returns:
        List of order dicts.  Pass each dict to the place_order service or
        ``POST /api/v1/placeorder``.

    Example::

        orders = build_openalgo_orders(signals["decisions"], apikey="abc123")
        for order in orders:
            place_order(order_data=order, api_key=order["apikey"])
    """
    orders: list[dict[str, Any]] = []

    for symbol, decision in decisions.items():
        action = str(decision.get("action", "hold")).lower()
        quantity = int(decision.get("quantity", 0))

        if action == "hold" or quantity <= 0:
            logger.debug("Skipping %s (action=%s quantity=%d)", symbol, action, quantity)
            continue

        openalgo_action = "BUY" if action == "buy" else "SELL"

        orders.append(
            {
                "apikey": apikey,
                "symbol": symbol,
                "exchange": exchange,
                "action": openalgo_action,
                "quantity": str(quantity),
                "product": product,
                "pricetype": price_type,
                "price": "0",
                "trigger_price": "0",
                "disclosed_quantity": "0",
            }
        )
        logger.debug(
            "Built order: %s %s %s qty=%d", openalgo_action, symbol, exchange, quantity
        )

    return orders
