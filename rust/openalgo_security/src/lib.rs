//! openalgo_security — High-performance IP ban lookup
//!
//! Maintains a `HashSet<String>` of banned IP addresses in memory.  The
//! `is_banned` check runs in O(1) and costs <1 µs, replacing the per-request
//! SQLite query (NullPool) that adds ~1–3 ms to every HTTP request.
//!
//! Usage pattern:
//!   1. Call `load_banned_ips(list_of_ips)` at startup and whenever the DB
//!      ban list changes.
//!   2. Replace `IPBan.is_ip_banned(ip)` with `openalgo_security.is_banned(ip)`
//!      in SecurityMiddleware.__call__.

use pyo3::prelude::*;
use pyo3::types::PyList;
use std::collections::HashSet;
use std::sync::{Mutex, OnceLock};

// ---------------------------------------------------------------------------
// Global ban set
// ---------------------------------------------------------------------------

static BAN_SET: OnceLock<Mutex<HashSet<String>>> = OnceLock::new();

fn get_ban_set() -> &'static Mutex<HashSet<String>> {
    BAN_SET.get_or_init(|| Mutex::new(HashSet::new()))
}

// ---------------------------------------------------------------------------
// Public PyO3 functions
// ---------------------------------------------------------------------------

/// Replace the current ban set with a new list of IP strings.
///
/// Call this at startup and whenever the DB ban list changes (e.g. after an
/// admin bans a new IP).
///
/// Args:
///     ips: list of IP address strings (IPv4 or IPv6)
#[pyfunction]
#[pyo3(name = "load_banned_ips")]
fn py_load_banned_ips(ips: &Bound<'_, PyList>) -> PyResult<()> {
    let mut set = get_ban_set().lock().unwrap();
    set.clear();
    for item in ips.iter() {
        let ip: String = item.extract()?;
        set.insert(ip);
    }
    Ok(())
}

/// Check if an IP address is in the ban set.
///
/// This is an O(1) HashSet lookup — sub-microsecond on any modern CPU.
///
/// Args:
///     ip: IP address string
///
/// Returns: True if banned, False otherwise
#[pyfunction]
#[pyo3(name = "is_banned")]
fn py_is_banned(ip: &str) -> bool {
    get_ban_set().lock().unwrap().contains(ip)
}

/// Add a single IP to the ban set.
///
/// Args:
///     ip: IP address string to ban
#[pyfunction]
#[pyo3(name = "ban_ip")]
fn py_ban_ip(ip: &str) {
    get_ban_set().lock().unwrap().insert(ip.to_string());
}

/// Remove a single IP from the ban set.
///
/// Args:
///     ip: IP address string to unban
///
/// Returns: True if the IP was in the set (and was removed), False otherwise
#[pyfunction]
#[pyo3(name = "unban_ip")]
fn py_unban_ip(ip: &str) -> bool {
    get_ban_set().lock().unwrap().remove(ip)
}

/// Number of banned IPs currently in the set.
#[pyfunction]
#[pyo3(name = "banned_count")]
fn py_banned_count() -> usize {
    get_ban_set().lock().unwrap().len()
}

/// Clear all banned IPs.
#[pyfunction]
#[pyo3(name = "clear_banned_ips")]
fn py_clear_banned_ips() {
    get_ban_set().lock().unwrap().clear();
}

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

#[pymodule]
fn openalgo_security(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(py_load_banned_ips, m)?)?;
    m.add_function(wrap_pyfunction!(py_is_banned, m)?)?;
    m.add_function(wrap_pyfunction!(py_ban_ip, m)?)?;
    m.add_function(wrap_pyfunction!(py_unban_ip, m)?)?;
    m.add_function(wrap_pyfunction!(py_banned_count, m)?)?;
    m.add_function(wrap_pyfunction!(py_clear_banned_ips, m)?)?;
    Ok(())
}
