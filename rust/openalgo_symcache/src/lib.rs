//! openalgo_symcache — In-memory symbol token cache
//!
//! Loads the full SymToken table into a Rust HashMap at startup and exposes
//! O(1) `lookup(symbol, exchange) -> dict` lookups to Python.  Replaces per-
//! request SQLite queries that add ~2–5 ms per order.
//!
//! Thread-safety: a `Mutex<HashMap>` guards the cache.  All operations are
//! fast enough that contention is negligible compared to the latency being
//! saved.

use pyo3::prelude::*;
use pyo3::types::PyDict;
use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};

// ---------------------------------------------------------------------------
// In-memory cache storage
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
struct SymbolEntry {
    symbol: String,
    brsymbol: String,
    exchange: String,
    brexchange: String,
    token: String,
    expiry: String,
    strike: f64,
    lotsize: i64,
    instrumenttype: String,
    tick_size: f64,
}

type CacheKey = (String, String); // (symbol.upper(), exchange.upper())
type Cache = HashMap<CacheKey, SymbolEntry>;

static CACHE: OnceLock<Mutex<Cache>> = OnceLock::new();

fn get_cache() -> &'static Mutex<Cache> {
    CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn extract_f64(obj: &Bound<'_, PyAny>) -> f64 {
    obj.extract::<f64>()
        .or_else(|_| obj.extract::<i64>().map(|i| i as f64))
        .unwrap_or(0.0)
}

fn extract_i64(obj: &Bound<'_, PyAny>) -> i64 {
    obj.extract::<i64>()
        .or_else(|_| obj.extract::<f64>().map(|f| f as i64))
        .unwrap_or(0)
}

fn extract_str(obj: &Bound<'_, PyAny>) -> String {
    obj.extract::<String>().unwrap_or_default()
}

fn entry_to_py<'py>(py: Python<'py>, e: &SymbolEntry) -> Bound<'py, PyDict> {
    let d = PyDict::new(py);
    d.set_item("symbol", &e.symbol).ok();
    d.set_item("brsymbol", &e.brsymbol).ok();
    d.set_item("exchange", &e.exchange).ok();
    d.set_item("brexchange", &e.brexchange).ok();
    d.set_item("token", &e.token).ok();
    d.set_item("expiry", &e.expiry).ok();
    d.set_item("strike", e.strike).ok();
    d.set_item("lotsize", e.lotsize).ok();
    d.set_item("instrumenttype", &e.instrumenttype).ok();
    d.set_item("tick_size", e.tick_size).ok();
    d
}

// ---------------------------------------------------------------------------
// Public PyO3 functions
// ---------------------------------------------------------------------------

/// Load the symbol table into the Rust cache.
///
/// Args:
///     data: list of dicts, each with keys:
///           symbol, brsymbol, exchange, brexchange, token, expiry,
///           strike, lotsize, instrumenttype, tick_size
#[pyfunction]
#[pyo3(name = "load_symbols")]
fn py_load_symbols(data: &Bound<'_, pyo3::types::PyList>) -> PyResult<()> {
    let mut cache = get_cache().lock().unwrap();
    cache.clear();

    for item in data.iter() {
        let row: &Bound<'_, PyDict> = item.downcast()?;

        let symbol = row
            .get_item("symbol")?
            .map(|v| extract_str(&v))
            .unwrap_or_default()
            .to_uppercase();
        let exchange = row
            .get_item("exchange")?
            .map(|v| extract_str(&v))
            .unwrap_or_default()
            .to_uppercase();

        if symbol.is_empty() || exchange.is_empty() {
            continue;
        }

        let entry = SymbolEntry {
            symbol: symbol.clone(),
            brsymbol: row
                .get_item("brsymbol")?
                .map(|v| extract_str(&v))
                .unwrap_or_default(),
            exchange: exchange.clone(),
            brexchange: row
                .get_item("brexchange")?
                .map(|v| extract_str(&v))
                .unwrap_or_default(),
            token: row
                .get_item("token")?
                .map(|v| extract_str(&v))
                .unwrap_or_default(),
            expiry: row
                .get_item("expiry")?
                .map(|v| extract_str(&v))
                .unwrap_or_default(),
            strike: row
                .get_item("strike")?
                .map(|v| extract_f64(&v))
                .unwrap_or(0.0),
            lotsize: row
                .get_item("lotsize")?
                .map(|v| extract_i64(&v))
                .unwrap_or(1),
            instrumenttype: row
                .get_item("instrumenttype")?
                .map(|v| extract_str(&v))
                .unwrap_or_default(),
            tick_size: row
                .get_item("tick_size")?
                .map(|v| extract_f64(&v))
                .unwrap_or(0.05),
        };

        cache.insert((symbol, exchange), entry);
    }

    Ok(())
}

/// O(1) symbol lookup.
///
/// Returns a Python dict with all symbol fields, or `None` if not found.
#[pyfunction]
#[pyo3(name = "lookup_symbol")]
fn py_lookup_symbol<'py>(
    py: Python<'py>,
    symbol: &str,
    exchange: &str,
) -> Option<Bound<'py, PyDict>> {
    let cache = get_cache().lock().unwrap();
    let key = (symbol.to_uppercase(), exchange.to_uppercase());
    cache.get(&key).map(|e| entry_to_py(py, e))
}

/// Look up just the broker token for a symbol+exchange pair.
/// Returns `None` if not found.
#[pyfunction]
#[pyo3(name = "lookup_token")]
fn py_lookup_token(symbol: &str, exchange: &str) -> Option<String> {
    let cache = get_cache().lock().unwrap();
    let key = (symbol.to_uppercase(), exchange.to_uppercase());
    cache.get(&key).map(|e| e.token.clone())
}

/// Look up the broker exchange code for a symbol.
#[pyfunction]
#[pyo3(name = "lookup_brexchange")]
fn py_lookup_brexchange(symbol: &str, exchange: &str) -> Option<String> {
    let cache = get_cache().lock().unwrap();
    let key = (symbol.to_uppercase(), exchange.to_uppercase());
    cache.get(&key).map(|e| e.brexchange.clone())
}

/// Number of symbols currently loaded in the cache.
#[pyfunction]
#[pyo3(name = "symbol_count")]
fn py_symbol_count() -> usize {
    get_cache().lock().unwrap().len()
}

/// Clear all symbols from the cache.
#[pyfunction]
#[pyo3(name = "clear_symbols")]
fn py_clear_symbols() {
    get_cache().lock().unwrap().clear();
}

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

#[pymodule]
fn openalgo_symcache(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(py_load_symbols, m)?)?;
    m.add_function(wrap_pyfunction!(py_lookup_symbol, m)?)?;
    m.add_function(wrap_pyfunction!(py_lookup_token, m)?)?;
    m.add_function(wrap_pyfunction!(py_lookup_brexchange, m)?)?;
    m.add_function(wrap_pyfunction!(py_symbol_count, m)?)?;
    m.add_function(wrap_pyfunction!(py_clear_symbols, m)?)?;
    Ok(())
}
