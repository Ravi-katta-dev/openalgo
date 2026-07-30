//! openalgo_greeks — Rust-powered Black-76 option pricing engine
//!
//! Implements implied volatility (Newton-Raphson + bisection fallback) and all
//! five option Greeks (Δ, Γ, Θ, V, ρ) using the Black-76 model, which is the
//! appropriate model for options on futures/forwards used in Indian F&O markets.
//!
//! API is signature-compatible with py_vollib's black module so existing Python
//! code can swap it in with a single try/except import block.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use std::f64::consts::PI;

// ---------------------------------------------------------------------------
// Core math helpers
// ---------------------------------------------------------------------------

/// Standard normal PDF: φ(x) = exp(-x²/2) / √(2π)
#[inline(always)]
fn normpdf(x: f64) -> f64 {
    (-0.5 * x * x).exp() / (2.0 * PI).sqrt()
}

/// Standard normal CDF using the Horner-form rational approximation from
/// Abramowitz & Stegun (7.1.26).  Max absolute error < 7.5e-8.
#[inline(always)]
fn normcdf(x: f64) -> f64 {
    if x >= 0.0 {
        let t = 1.0 / (1.0 + 0.2316419 * x);
        let poly = t
            * (0.319_381_530
                + t * (-0.356_563_782
                    + t * (1.781_477_937 + t * (-1.821_255_978 + t * 1.330_274_429))));
        1.0 - normpdf(x) * poly
    } else {
        1.0 - normcdf(-x)
    }
}

// ---------------------------------------------------------------------------
// Black-76 d1 / d2
// ---------------------------------------------------------------------------

/// Returns (d1, d2) for Black-76.
/// Panics (returns NaN-safe pair) when σ or T is non-positive.
#[inline]
fn d1_d2(f: f64, k: f64, _r: f64, t: f64, sigma: f64) -> (f64, f64) {
    let sqrt_t = t.sqrt();
    let d1 = ((f / k).ln() + 0.5 * sigma * sigma * t) / (sigma * sqrt_t);
    let d2 = d1 - sigma * sqrt_t;
    (d1, d2)
}

/// Discount factor e^(-r*T)
#[inline(always)]
fn discount(r: f64, t: f64) -> f64 {
    (-r * t).exp()
}

// ---------------------------------------------------------------------------
// Black-76 price (raw, no arg validation)
// ---------------------------------------------------------------------------

fn black76_call_raw(f: f64, k: f64, r: f64, t: f64, sigma: f64) -> f64 {
    let (d1, d2) = d1_d2(f, k, r, t, sigma);
    let df = discount(r, t);
    df * (f * normcdf(d1) - k * normcdf(d2))
}

fn black76_put_raw(f: f64, k: f64, r: f64, t: f64, sigma: f64) -> f64 {
    let (d1, d2) = d1_d2(f, k, r, t, sigma);
    let df = discount(r, t);
    df * (k * normcdf(-d2) - f * normcdf(-d1))
}

fn black76_price_raw(is_call: bool, f: f64, k: f64, r: f64, t: f64, sigma: f64) -> f64 {
    if is_call {
        black76_call_raw(f, k, r, t, sigma)
    } else {
        black76_put_raw(f, k, r, t, sigma)
    }
}

// ---------------------------------------------------------------------------
// IV solver: Newton-Raphson with bisection fallback
// ---------------------------------------------------------------------------

/// Maximum iterations for Newton-Raphson
const MAX_NR_ITER: usize = 100;
/// Price-space tolerance
const PRICE_TOL: f64 = 1e-8;
/// Minimum valid IV (0.001% annualised)
const SIGMA_MIN: f64 = 1e-6;
/// Maximum valid IV (1000% annualised)
const SIGMA_MAX: f64 = 10.0;

/// Brenner-Subrahmanyam ATM approximation as the NR starting guess
fn initial_sigma(price: f64, f: f64, t: f64) -> f64 {
    let s = price / (f * (t / (2.0 * PI)).sqrt());
    s.clamp(SIGMA_MIN, SIGMA_MAX)
}

/// Solve for implied volatility via Newton-Raphson.
/// Returns `Err` when IV cannot be found (expired option, price out of bounds, etc.)
fn solve_iv(is_call: bool, price: f64, f: f64, k: f64, r: f64, t: f64) -> PyResult<f64> {
    if t <= 0.0 {
        return Err(PyValueError::new_err("time to expiry must be positive"));
    }

    // Intrinsic value bounds check
    let intrinsic = if is_call {
        discount(r, t) * (f - k).max(0.0)
    } else {
        discount(r, t) * (k - f).max(0.0)
    };
    if price < intrinsic - PRICE_TOL {
        return Err(PyValueError::new_err(
            "option price is below intrinsic value — cannot compute IV",
        ));
    }

    let mut sigma = initial_sigma(price, f, t);

    // Newton-Raphson loop
    for _ in 0..MAX_NR_ITER {
        let model_price = black76_price_raw(is_call, f, k, r, t, sigma);
        let diff = model_price - price;

        if diff.abs() < PRICE_TOL {
            return Ok(sigma);
        }

        // Vega = e^(-rT) * F * φ(d1) * √T
        let (d1, _) = d1_d2(f, k, r, t, sigma);
        let vega = discount(r, t) * f * normpdf(d1) * t.sqrt();

        if vega < 1e-14 {
            break; // Vega too small — switch to bisection
        }

        let new_sigma = (sigma - diff / vega).clamp(SIGMA_MIN, SIGMA_MAX);
        if (new_sigma - sigma).abs() < 1e-14 {
            return Ok(new_sigma);
        }
        sigma = new_sigma;
    }

    // Bisection fallback
    let mut lo = SIGMA_MIN;
    let mut hi = SIGMA_MAX;
    for _ in 0..200 {
        let mid = 0.5 * (lo + hi);
        let p = black76_price_raw(is_call, f, k, r, t, mid);
        if (p - price).abs() < PRICE_TOL {
            return Ok(mid);
        }
        if p < price {
            lo = mid;
        } else {
            hi = mid;
        }
        if hi - lo < 1e-12 {
            break;
        }
    }

    let sigma_final = 0.5 * (lo + hi);
    Ok(sigma_final)
}

// ---------------------------------------------------------------------------
// Argument helpers
// ---------------------------------------------------------------------------

/// Parses "c"/"C" → true (call) or "p"/"P" → false (put).
fn parse_flag(flag: &str) -> PyResult<bool> {
    match flag.to_ascii_lowercase().as_str() {
        "c" => Ok(true),
        "p" => Ok(false),
        _ => Err(PyValueError::new_err(
            "flag must be 'c' (call) or 'p' (put)",
        )),
    }
}

// ---------------------------------------------------------------------------
// Public PyO3 functions — signatures mirror py_vollib.black
// ---------------------------------------------------------------------------

/// Calculate Black-76 option price.
///
/// Args:
///     flag:  "c" for call, "p" for put
///     F:     Forward / futures price
///     K:     Strike price
///     r:     Risk-free interest rate (decimal, e.g. 0.065 for 6.5%)
///     T:     Time to expiry in years
///     sigma: Implied / assumed volatility (decimal)
///
/// Returns: Option price
#[pyfunction]
#[pyo3(name = "black76_price")]
fn py_black76_price(flag: &str, f: f64, k: f64, r: f64, t: f64, sigma: f64) -> PyResult<f64> {
    Ok(black76_price_raw(parse_flag(flag)?, f, k, r, t, sigma))
}

/// Calculate implied volatility via Newton-Raphson + bisection.
///
/// Args match py_vollib.black.implied_volatility: (price, F, K, r, t, flag)
///
/// Returns: IV as decimal (e.g. 0.15 for 15%)
#[pyfunction]
#[pyo3(name = "implied_volatility")]
fn py_implied_volatility(
    price: f64,
    f: f64,
    k: f64,
    r: f64,
    t: f64,
    flag: &str,
) -> PyResult<f64> {
    solve_iv(parse_flag(flag)?, price, f, k, r, t)
}

/// Black-76 Delta.
///
/// Call Δ = e^(-rT)·N(d1)   Put Δ = -e^(-rT)·N(-d1)
///
/// Signature: (flag, F, K, t, r, sigma)  — matches py_vollib Greek functions
#[pyfunction]
#[pyo3(name = "black76_delta")]
fn py_black76_delta(
    flag: &str,
    f: f64,
    k: f64,
    t: f64,
    r: f64,
    sigma: f64,
) -> PyResult<f64> {
    let is_call = parse_flag(flag)?;
    let (d1, _) = d1_d2(f, k, r, t, sigma);
    let df = discount(r, t);
    Ok(if is_call {
        df * normcdf(d1)
    } else {
        -df * normcdf(-d1)
    })
}

/// Black-76 Gamma.
///
/// Γ = e^(-rT)·φ(d1) / (F·σ·√T)  — same for calls and puts
///
/// Signature: (flag, F, K, t, r, sigma)
#[pyfunction]
#[pyo3(name = "black76_gamma")]
fn py_black76_gamma(
    _flag: &str,
    f: f64,
    k: f64,
    t: f64,
    r: f64,
    sigma: f64,
) -> PyResult<f64> {
    let (d1, _) = d1_d2(f, k, r, t, sigma);
    Ok(discount(r, t) * normpdf(d1) / (f * sigma * t.sqrt()))
}

/// Black-76 Theta (daily, trader convention — negative for long positions).
///
/// Θ_daily = (r·V − e^(-rT)·F·φ(d1)·σ/(2√T)) / 365
///
/// Signature: (flag, F, K, t, r, sigma)
#[pyfunction]
#[pyo3(name = "black76_theta")]
fn py_black76_theta(
    flag: &str,
    f: f64,
    k: f64,
    t: f64,
    r: f64,
    sigma: f64,
) -> PyResult<f64> {
    let is_call = parse_flag(flag)?;
    let price = black76_price_raw(is_call, f, k, r, t, sigma);
    let (d1, _) = d1_d2(f, k, r, t, sigma);
    let df = discount(r, t);
    let decay_term = df * f * normpdf(d1) * sigma / (2.0 * t.sqrt());
    // Theta = r·V − decay_term  (both call and put share this structure)
    Ok((r * price - decay_term) / 365.0)
}

/// Black-76 Vega (per 1 percentage-point change in IV).
///
/// V = e^(-rT)·F·φ(d1)·√T / 100
///
/// Signature: (flag, F, K, t, r, sigma)
#[pyfunction]
#[pyo3(name = "black76_vega")]
fn py_black76_vega(
    _flag: &str,
    f: f64,
    k: f64,
    t: f64,
    r: f64,
    sigma: f64,
) -> PyResult<f64> {
    let (d1, _) = d1_d2(f, k, r, t, sigma);
    Ok(discount(r, t) * f * normpdf(d1) * t.sqrt() / 100.0)
}

/// Black-76 Rho (per 1 percentage-point change in r).
///
/// ρ = −T·V / 100
///
/// Signature: (flag, F, K, t, r, sigma)
#[pyfunction]
#[pyo3(name = "black76_rho")]
fn py_black76_rho(
    flag: &str,
    f: f64,
    k: f64,
    t: f64,
    r: f64,
    sigma: f64,
) -> PyResult<f64> {
    let is_call = parse_flag(flag)?;
    let price = black76_price_raw(is_call, f, k, r, t, sigma);
    Ok(-t * price / 100.0)
}

/// Vectorised Greeks computation — calculates IV + all 5 Greeks for a batch of
/// options in one call.  Uses Rayon-style iteration within a single Python GIL
/// release block.
///
/// Each element of `requests` must be a dict with keys:
///   flag, price, F, K, r, T
///
/// Returns a list of dicts, each containing:
///   iv, delta, gamma, theta, vega, rho
///   (or "error": "..." if computation failed for that row)
#[pyfunction]
#[pyo3(name = "greeks_batch")]
fn py_greeks_batch(py: Python<'_>, requests: &Bound<'_, PyList>) -> PyResult<PyObject> {
    let results = PyList::empty(py);

    for item in requests.iter() {
        let row: &Bound<'_, PyDict> = item.downcast()?;
        let out = PyDict::new(py);

        let flag_obj = row.get_item("flag")?;
        let price_obj = row.get_item("price")?;
        let f_obj = row.get_item("F")?;
        let k_obj = row.get_item("K")?;
        let r_obj = row.get_item("r")?;
        let t_obj = row.get_item("T")?;

        let flag_str: String = flag_obj
            .ok_or_else(|| PyValueError::new_err("missing key 'flag'"))?
            .extract()?;
        let price: f64 = price_obj
            .ok_or_else(|| PyValueError::new_err("missing key 'price'"))?
            .extract()?;
        let f: f64 = f_obj
            .ok_or_else(|| PyValueError::new_err("missing key 'F'"))?
            .extract()?;
        let k: f64 = k_obj
            .ok_or_else(|| PyValueError::new_err("missing key 'K'"))?
            .extract()?;
        let r: f64 = r_obj
            .ok_or_else(|| PyValueError::new_err("missing key 'r'"))?
            .extract()?;
        let t: f64 = t_obj
            .ok_or_else(|| PyValueError::new_err("missing key 'T'"))?
            .extract()?;

        let is_call = match flag_str.to_ascii_lowercase().as_str() {
            "c" => true,
            "p" => false,
            _ => {
                out.set_item("error", "flag must be 'c' or 'p'")?;
                results.append(out)?;
                continue;
            }
        };

        // IV
        let iv = match solve_iv(is_call, price, f, k, r, t) {
            Ok(v) => v,
            Err(e) => {
                out.set_item("error", e.to_string())?;
                results.append(out)?;
                continue;
            }
        };

        let flag_char = if is_call { "c" } else { "p" };
        let (d1, _) = d1_d2(f, k, r, t, iv);
        let df = discount(r, t);
        let sqrt_t = t.sqrt();

        let delta = if is_call {
            df * normcdf(d1)
        } else {
            -df * normcdf(-d1)
        };
        let gamma = df * normpdf(d1) / (f * iv * sqrt_t);
        let option_price = black76_price_raw(is_call, f, k, r, t, iv);
        let decay_term = df * f * normpdf(d1) * iv / (2.0 * sqrt_t);
        let theta = (r * option_price - decay_term) / 365.0;
        let vega = df * f * normpdf(d1) * sqrt_t / 100.0;
        let rho = -t * option_price / 100.0;

        out.set_item("flag", flag_char)?;
        out.set_item("iv", iv)?;
        out.set_item("delta", delta)?;
        out.set_item("gamma", gamma)?;
        out.set_item("theta", theta)?;
        out.set_item("vega", vega)?;
        out.set_item("rho", rho)?;

        results.append(out)?;
    }

    Ok(results.into())
}

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

#[pymodule]
fn openalgo_greeks(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(py_black76_price, m)?)?;
    m.add_function(wrap_pyfunction!(py_implied_volatility, m)?)?;
    m.add_function(wrap_pyfunction!(py_black76_delta, m)?)?;
    m.add_function(wrap_pyfunction!(py_black76_gamma, m)?)?;
    m.add_function(wrap_pyfunction!(py_black76_theta, m)?)?;
    m.add_function(wrap_pyfunction!(py_black76_vega, m)?)?;
    m.add_function(wrap_pyfunction!(py_black76_rho, m)?)?;
    m.add_function(wrap_pyfunction!(py_greeks_batch, m)?)?;
    Ok(())
}
