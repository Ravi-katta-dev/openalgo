//! openalgo_utils — Rust-accelerated utility functions
//!
//! Provides:
//!   • format_indian_number(n) — Indian number system (Cr/L suffixes)
//!   • format_indian_currency(n) — same with ₹ prefix
//!   • get_mpp_percentage(price, instrument_type) — MPP slab lookup
//!   • calculate_protected_price(…) — market price protection
//!   • round_to_tick_size(price, tick_size) — tick-aligned rounding
//!   • get_instrument_type_from_symbol(symbol) — CE/PE/FUT/EQ detector

use pyo3::prelude::*;

// ---------------------------------------------------------------------------
// Indian number formatter
// ---------------------------------------------------------------------------

/// Format a number in the Indian numbering system (Cr/L suffixes).
///
/// Examples:
///   10_000_000.0 → "1.00Cr"
///   9_978_000.0  → "99.78L"
///   10_000.0     → "10000.00"
///   -5_000_000.0 → "-50.00L"
#[pyfunction]
#[pyo3(name = "format_indian_number")]
fn py_format_indian_number(value: f64) -> String {
    format_indian_number(value)
}

pub fn format_indian_number(value: f64) -> String {
    if value.is_nan() || value.is_infinite() {
        return value.to_string();
    }

    let is_negative = value < 0.0;
    let abs_val = value.abs();

    let formatted = if abs_val >= 10_000_000.0 {
        format!("{:.2}Cr", abs_val / 10_000_000.0)
    } else if abs_val >= 100_000.0 {
        format!("{:.2}L", abs_val / 100_000.0)
    } else {
        format!("{:.2}", abs_val)
    };

    if is_negative {
        format!("-{}", formatted)
    } else {
        formatted
    }
}

/// Format a number as Indian currency with ₹ prefix.
#[pyfunction]
#[pyo3(name = "format_indian_currency")]
fn py_format_indian_currency(value: f64) -> String {
    format!("₹{}", format_indian_number(value))
}

// ---------------------------------------------------------------------------
// MPP (Market Price Protection) slab calculations
// ---------------------------------------------------------------------------

/// EQ/FUT MPP slabs: (max_price_exclusive, protection_pct)
/// Last entry uses f64::INFINITY as the sentinel for "all remaining prices".
const EQ_FUT_SLABS: &[(f64, f64)] = &[
    (100.0, 2.0),          // price < 100       → 2.0%
    (500.0, 1.0),          // 100 ≤ price < 500 → 1.0%
    (f64::INFINITY, 0.5),  // price ≥ 500       → 0.5%
];

/// Options (CE/PE) MPP slabs
const OPT_SLABS: &[(f64, f64)] = &[
    (10.0, 5.0),           // price < 10        → 5.0%
    (100.0, 3.0),          // 10 ≤ price < 100  → 3.0%
    (500.0, 2.0),          // 100 ≤ price < 500 → 2.0%
    (f64::INFINITY, 1.0),  // price ≥ 500       → 1.0%
];

fn is_option(instrument_type: &str) -> bool {
    let up = instrument_type.to_uppercase();
    up == "CE" || up == "PE"
}

fn mpp_percentage(price: f64, instrument_type: &str) -> f64 {
    let slabs = if is_option(instrument_type) {
        OPT_SLABS
    } else {
        EQ_FUT_SLABS
    };
    for &(max_price, pct) in slabs {
        if price < max_price {
            return pct;
        }
    }
    // Fallback (unreachable in practice: the last slab uses f64::INFINITY so
    // every finite price is handled above).
    slabs.last().map(|&(_, p)| p).unwrap_or(0.5)
}

/// Get the Market Price Protection percentage for a given price and instrument type.
///
/// Args:
///     price:           Current market price (LTP)
///     instrument_type: "EQ", "FUT", "CE", or "PE"
///
/// Returns: protection percentage (e.g. 2.0 for 2%)
#[pyfunction]
#[pyo3(name = "get_mpp_percentage")]
fn py_get_mpp_percentage(price: f64, instrument_type: &str) -> f64 {
    mpp_percentage(price, instrument_type)
}

/// Round a price to the nearest tick size.
///
/// Args:
///     price:     Calculated price
///     tick_size: Tick size (pass None or ≤0 to round to 2 d.p.)
///
/// Returns: price rounded to nearest tick
#[pyfunction]
#[pyo3(name = "round_to_tick_size")]
fn py_round_to_tick_size(price: f64, tick_size: Option<f64>) -> f64 {
    round_to_tick_size(price, tick_size)
}

pub fn round_to_tick_size(price: f64, tick_size: Option<f64>) -> f64 {
    match tick_size {
        Some(ts) if ts > 0.0 => {
            let rounded = (price / ts).round() * ts;
            // Ensure 2 d.p. display precision
            (rounded * 100.0).round() / 100.0
        }
        _ => (price * 100.0).round() / 100.0,
    }
}

/// Calculate the protected limit price for a market order.
///
/// Args:
///     price:              Current market price (LTP)
///     action:             "BUY" or "SELL"
///     instrument_type:    "EQ", "FUT", "CE", or "PE"
///     tick_size:          Tick size for rounding (None → 2 d.p.)
///     custom_percentage:  Override the slab-based percentage
///
/// Returns: adjusted limit price rounded to tick size
#[pyfunction]
#[pyo3(name = "calculate_protected_price")]
fn py_calculate_protected_price(
    price: f64,
    action: &str,
    instrument_type: &str,
    tick_size: Option<f64>,
    custom_percentage: Option<f64>,
) -> f64 {
    let pct = custom_percentage.unwrap_or_else(|| mpp_percentage(price, instrument_type));
    let multiplier = pct / 100.0;

    let raw = if action.to_uppercase() == "BUY" {
        price * (1.0 + multiplier)
    } else {
        price * (1.0 - multiplier)
    };

    round_to_tick_size(raw, tick_size)
}

/// Determine instrument type from symbol suffix.
///
/// Returns "CE", "PE", "FUT", or "EQ".
#[pyfunction]
#[pyo3(name = "get_instrument_type_from_symbol")]
fn py_get_instrument_type_from_symbol(symbol: &str) -> &'static str {
    let up = symbol.to_uppercase();
    if up.ends_with("CE") {
        "CE"
    } else if up.ends_with("PE") {
        "PE"
    } else if up.ends_with("FUT") {
        "FUT"
    } else {
        "EQ"
    }
}

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

#[pymodule]
fn openalgo_utils(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(py_format_indian_number, m)?)?;
    m.add_function(wrap_pyfunction!(py_format_indian_currency, m)?)?;
    m.add_function(wrap_pyfunction!(py_get_mpp_percentage, m)?)?;
    m.add_function(wrap_pyfunction!(py_round_to_tick_size, m)?)?;
    m.add_function(wrap_pyfunction!(py_calculate_protected_price, m)?)?;
    m.add_function(wrap_pyfunction!(py_get_instrument_type_from_symbol, m)?)?;
    Ok(())
}
