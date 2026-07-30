//! openalgo_matcher — Parallel sandbox order matching engine
//!
//! Evaluates MARKET / LIMIT / SL / SL-M order conditions against current
//! market quotes in parallel using Rayon, bypassing the Python GIL.
//!
//! The Python side fetches quotes and holds DB connections; Rust handles only
//! the pure-computation matching step and returns which orders should be
//! executed and at what price.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use rayon::prelude::*;

// ---------------------------------------------------------------------------
// Internal order/quote representations
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
struct Order {
    orderid: String,
    symbol: String,
    exchange: String,
    action: String,   // "BUY" or "SELL"
    price_type: String, // MARKET / LIMIT / SL / SL-M
    price: f64,
    trigger_price: f64,
    quantity: i64,
}

#[derive(Debug, Clone)]
struct Quote {
    ltp: f64,
    bid: f64,
    ask: f64,
}

#[derive(Debug)]
struct MatchResult {
    orderid: String,
    should_execute: bool,
    execution_price: f64,
    reason: String,
}

// ---------------------------------------------------------------------------
// Matching logic (pure Rust, GIL-free)
// ---------------------------------------------------------------------------

fn evaluate_order(order: &Order, quote: &Quote) -> MatchResult {
    let ltp = quote.ltp;
    let bid = quote.bid;
    let ask = quote.ask;

    if ltp <= 0.0 {
        return MatchResult {
            orderid: order.orderid.clone(),
            should_execute: false,
            execution_price: 0.0,
            reason: "invalid_ltp".to_string(),
        };
    }

    let is_buy = order.action.to_uppercase() == "BUY";
    let price_type = order.price_type.to_uppercase();

    let (should_execute, execution_price) = match price_type.as_str() {
        "MARKET" => {
            // BUY at ask (or LTP fallback), SELL at bid (or LTP fallback)
            let exec_price = if is_buy {
                if ask > 0.0 { ask } else { ltp }
            } else {
                if bid > 0.0 { bid } else { ltp }
            };
            (true, exec_price)
        }

        "LIMIT" => {
            let exec_price = order.price;
            if is_buy && ltp <= order.price {
                (true, exec_price)
            } else if !is_buy && ltp >= order.price {
                (true, exec_price)
            } else {
                (false, 0.0)
            }
        }

        "SL" => {
            // Stop-loss limit: activate when LTP crosses trigger, then fill as limit
            if is_buy && ltp >= order.trigger_price && ltp <= order.price {
                (true, ltp)
            } else if !is_buy && ltp <= order.trigger_price && ltp >= order.price {
                (true, ltp)
            } else {
                (false, 0.0)
            }
        }

        "SL-M" => {
            // Stop-loss market: activate when LTP crosses trigger, fill at market
            if is_buy && ltp >= order.trigger_price {
                (true, ltp)
            } else if !is_buy && ltp <= order.trigger_price {
                (true, ltp)
            } else {
                (false, 0.0)
            }
        }

        _ => (false, 0.0),
    };

    MatchResult {
        orderid: order.orderid.clone(),
        should_execute,
        execution_price,
        reason: if should_execute {
            "matched".to_string()
        } else {
            "conditions_not_met".to_string()
        },
    }
}

// ---------------------------------------------------------------------------
// Python extraction helpers
// ---------------------------------------------------------------------------

fn extract_f64(row: &Bound<'_, PyDict>, key: &str) -> f64 {
    row.get_item(key)
        .ok()
        .flatten()
        .and_then(|v| {
            v.extract::<f64>()
                .ok()
                .or_else(|| v.extract::<i64>().ok().map(|i| i as f64))
        })
        .unwrap_or(0.0)
}

fn extract_i64(row: &Bound<'_, PyDict>, key: &str) -> i64 {
    row.get_item(key)
        .ok()
        .flatten()
        .and_then(|v| {
            v.extract::<i64>()
                .ok()
                .or_else(|| v.extract::<f64>().ok().map(|f| f as i64))
        })
        .unwrap_or(0)
}

fn extract_str(row: &Bound<'_, PyDict>, key: &str) -> String {
    row.get_item(key)
        .ok()
        .flatten()
        .and_then(|v| v.extract::<String>().ok())
        .unwrap_or_default()
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Evaluate order matching conditions in parallel.
///
/// Args:
///     orders: list of order dicts with keys:
///             orderid, symbol, exchange, action, price_type,
///             price, trigger_price, quantity
///     quotes: dict mapping (symbol, exchange) -> {ltp, bid, ask}
///             Key format: "SYMBOL:EXCHANGE" (e.g. "SBIN:NSE")
///
/// Returns: list of dicts, each containing:
///     orderid, should_execute (bool), execution_price (float), reason (str)
#[pyfunction]
#[pyo3(name = "match_orders")]
fn py_match_orders(
    py: Python<'_>,
    orders: &Bound<'_, PyList>,
    quotes: &Bound<'_, PyDict>,
) -> PyResult<PyObject> {
    // Build quote lookup (string key "SYMBOL:EXCHANGE")
    let mut quote_map: std::collections::HashMap<String, Quote> =
        std::collections::HashMap::new();

    for (k, v) in quotes.iter() {
        // Key can be a tuple (symbol, exchange) or a colon-separated string
        let key_str: String = if let Ok(s) = k.extract::<String>() {
            s
        } else if let Ok(tup) = k.downcast::<pyo3::types::PyTuple>() {
            let sym: String = tup.get_item(0)?.extract()?;
            let exc: String = tup.get_item(1)?.extract()?;
            format!("{}:{}", sym, exc)
        } else {
            continue;
        };

        let quote_dict: &Bound<'_, PyDict> = v.downcast()?;
        let ltp = extract_f64(quote_dict, "ltp");
        let bid = extract_f64(quote_dict, "bid");
        let ask = extract_f64(quote_dict, "ask");
        quote_map.insert(key_str, Quote { ltp, bid, ask });
    }

    // Collect orders into Rust structs
    let mut rust_orders: Vec<Order> = Vec::with_capacity(orders.len());
    for item in orders.iter() {
        let row: &Bound<'_, PyDict> = item.downcast()?;
        rust_orders.push(Order {
            orderid: extract_str(row, "orderid"),
            symbol: extract_str(row, "symbol"),
            exchange: extract_str(row, "exchange"),
            action: extract_str(row, "action"),
            price_type: extract_str(row, "price_type"),
            price: extract_f64(row, "price"),
            trigger_price: extract_f64(row, "trigger_price"),
            quantity: extract_i64(row, "quantity"),
        });
    }

    // Release the GIL and match in parallel
    let results: Vec<MatchResult> = py.allow_threads(|| {
        rust_orders
            .par_iter()
            .map(|order| {
                let key = format!("{}:{}", order.symbol, order.exchange);
                match quote_map.get(&key) {
                    Some(quote) => evaluate_order(order, quote),
                    None => MatchResult {
                        orderid: order.orderid.clone(),
                        should_execute: false,
                        execution_price: 0.0,
                        reason: "no_quote".to_string(),
                    },
                }
            })
            .collect()
    });

    // Convert back to Python list of dicts
    let py_results = PyList::empty(py);
    for r in &results {
        let d = PyDict::new(py);
        d.set_item("orderid", &r.orderid)?;
        d.set_item("should_execute", r.should_execute)?;
        d.set_item("execution_price", r.execution_price)?;
        d.set_item("reason", &r.reason)?;
        py_results.append(d)?;
    }

    Ok(py_results.into())
}

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

#[pymodule]
fn openalgo_matcher(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(py_match_orders, m)?)?;
    Ok(())
}
