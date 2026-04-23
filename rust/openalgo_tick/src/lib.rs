//! openalgo_tick — Fast JSON tick normalizer
//!
//! Parses broker-specific tick JSON strings from ZeroMQ and normalises them
//! to OpenAlgo's canonical field names.  Uses serde_json for zero-copy
//! parsing, which is significantly faster than Python's built-in json module.
//!
//! Canonical fields: ltp, open, high, low, close, volume, oi, bid, ask,
//!                   bid_qty, ask_qty, timestamp

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use serde_json::Value;

// ---------------------------------------------------------------------------
// Field name aliases (broker → canonical)
// ---------------------------------------------------------------------------

/// Map a raw field name from any broker to the canonical OpenAlgo field name.
/// Returns None if the field is unknown (will be passed through as-is).
fn canonical_field(raw: &str) -> Option<&'static str> {
    match raw {
        // Last traded price
        "ltp" | "last_price" | "last_trade_price" | "lp" | "price" | "tradedPrice"
        | "last_traded_price" | "lastPrice" | "trade_price" => Some("ltp"),

        // OHLC
        "open" | "open_price" | "openPrice" | "op" | "open_interest_day"
            if raw != "open_interest_day" =>
        {
            Some("open")
        }
        "open" | "open_price" | "openPrice" | "op" => Some("open"),
        "high" | "high_price" | "highPrice" | "hp" | "dayHigh" => Some("high"),
        "low" | "low_price" | "lowPrice" | "lp2" | "dayLow" => Some("low"),
        "close" | "close_price" | "closePrice" | "cp" | "prevClose" | "prev_close"
        | "previous_close" => Some("close"),

        // Volume
        "volume" | "vol" | "totalVolume" | "total_volume" | "tradedQty"
        | "total_traded_volume" => Some("volume"),

        // Open interest
        "oi" | "open_interest" | "openInterest" | "open_int" | "OI" => Some("oi"),

        // Bid / Ask
        "bid" | "best_bid" | "bestBid" | "buy_price" | "b" => Some("bid"),
        "ask" | "best_ask" | "bestAsk" | "sell_price" | "a" | "offer" => Some("ask"),
        "bid_qty" | "best_bid_qty" | "buy_qty" | "bq" => Some("bid_qty"),
        "ask_qty" | "best_ask_qty" | "sell_qty" | "aq" | "offer_qty" => Some("ask_qty"),

        // Timestamp
        "timestamp" | "ts" | "time" | "exchange_timestamp" | "exchange_time"
        | "exchangeTimestamp" => Some("timestamp"),

        _ => None,
    }
}

// ---------------------------------------------------------------------------
// Normalise a serde_json::Value into a Python dict
// ---------------------------------------------------------------------------

fn json_value_to_py(py: Python<'_>, v: &Value) -> PyObject {
    match v {
        Value::Null => py.None(),
        Value::Bool(b) => {
            use pyo3::types::PyBool;
            PyBool::new(py, *b).to_owned().into_any().unbind()
        }
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                i.into_pyobject(py).unwrap().into_any().unbind()
            } else if let Some(f) = n.as_f64() {
                f.into_pyobject(py).unwrap().into_any().unbind()
            } else {
                n.to_string()
                    .into_pyobject(py)
                    .unwrap()
                    .into_any()
                    .unbind()
            }
        }
        Value::String(s) => s
            .as_str()
            .into_pyobject(py)
            .unwrap()
            .into_any()
            .unbind(),
        Value::Array(arr) => {
            let list = pyo3::types::PyList::empty(py);
            for item in arr {
                list.append(json_value_to_py(py, item)).ok();
            }
            list.into_any().unbind()
        }
        Value::Object(obj) => {
            let d = PyDict::new(py);
            for (k, val) in obj {
                let key = canonical_field(k.as_str()).unwrap_or(k.as_str());
                d.set_item(key, json_value_to_py(py, val)).ok();
            }
            d.into_any().unbind()
        }
    }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Parse a raw JSON tick string and normalise field names to OpenAlgo canonical
/// format.  Returns a Python dict.
///
/// Args:
///     json_str: Raw JSON string received from broker via ZeroMQ
///     broker:   Broker name (reserved for future broker-specific handling)
///
/// Returns: dict with canonical field names
#[pyfunction]
#[pyo3(name = "normalize_tick")]
fn py_normalize_tick(py: Python<'_>, json_str: &str, _broker: &str) -> PyResult<PyObject> {
    let value: Value =
        serde_json::from_str(json_str).map_err(|e| PyValueError::new_err(e.to_string()))?;

    Ok(json_value_to_py(py, &value))
}

/// Parse a raw JSON string to a Python dict without field-name normalisation.
/// Faster alternative to Python's `json.loads()` for large payloads.
#[pyfunction]
#[pyo3(name = "parse_json")]
fn py_parse_json(py: Python<'_>, json_str: &str) -> PyResult<PyObject> {
    let value: Value =
        serde_json::from_str(json_str).map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(json_value_to_py(py, &value))
}

/// Serialise a Python dict/list/… to a compact JSON string.
/// Thin wrapper around serde_json for cases where `orjson` is not available.
#[pyfunction]
#[pyo3(name = "to_json")]
fn py_to_json(py: Python<'_>, obj: PyObject) -> PyResult<String> {
    // Convert Python object to serde_json::Value via repr is impractical;
    // use Python's built-in __repr__ is wrong too.  Instead we accept only
    // str/bytes/int/float/bool/None/list/dict via downcasting.
    let value = pyobj_to_json_value(py, &obj.bind(py))?;
    serde_json::to_string(&value).map_err(|e| PyValueError::new_err(e.to_string()))
}

fn pyobj_to_json_value(py: Python<'_>, obj: &Bound<'_, PyAny>) -> PyResult<Value> {
    if obj.is_none() {
        return Ok(Value::Null);
    }
    if let Ok(b) = obj.extract::<bool>() {
        return Ok(Value::Bool(b));
    }
    if let Ok(i) = obj.extract::<i64>() {
        return Ok(Value::Number(i.into()));
    }
    if let Ok(f) = obj.extract::<f64>() {
        return Ok(Value::Number(
            serde_json::Number::from_f64(f).unwrap_or(serde_json::Number::from(0)),
        ));
    }
    if let Ok(s) = obj.extract::<String>() {
        return Ok(Value::String(s));
    }
    if let Ok(list) = obj.downcast::<pyo3::types::PyList>() {
        let mut arr = Vec::new();
        for item in list.iter() {
            arr.push(pyobj_to_json_value(py, &item)?);
        }
        return Ok(Value::Array(arr));
    }
    if let Ok(dict) = obj.downcast::<PyDict>() {
        let mut map = serde_json::Map::new();
        for (k, v) in dict.iter() {
            let key: String = k.extract()?;
            map.insert(key, pyobj_to_json_value(py, &v)?);
        }
        return Ok(Value::Object(map));
    }
    // Fall back to str() representation
    Ok(Value::String(obj.str()?.to_string()))
}

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

#[pymodule]
fn openalgo_tick(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(py_normalize_tick, m)?)?;
    m.add_function(wrap_pyfunction!(py_parse_json, m)?)?;
    m.add_function(wrap_pyfunction!(py_to_json, m)?)?;
    Ok(())
}
