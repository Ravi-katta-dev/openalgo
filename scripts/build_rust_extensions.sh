#!/usr/bin/env bash
# Build and install all OpenAlgo Rust extension modules.
#
# Prerequisites:
#   - Rust stable toolchain  (https://rustup.rs)
#   - maturin >= 1.0          (pip install maturin)
#   - Python >= 3.12          (the active virtual-env / system interpreter)
#
# Usage:
#   bash scripts/build_rust_extensions.sh          # build + install into current venv
#   bash scripts/build_rust_extensions.sh --check  # cargo check only (fast syntax check)
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUST_DIR="$REPO_ROOT/rust"
WHEEL_DIR="$RUST_DIR/target/wheels"

MODULES=(
    openalgo_greeks
    openalgo_tick
    openalgo_symcache
    openalgo_security
    openalgo_matcher
    openalgo_utils
)

# ── helpers ─────────────────────────────────────────────────────────────────

log()  { echo "[build_rust] $*"; }
fail() { echo "[build_rust] ERROR: $*" >&2; exit 1; }

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || fail "$1 not found. $2"
}

# ── argument parsing ─────────────────────────────────────────────────────────

CHECK_ONLY=false
for arg in "$@"; do
    case "$arg" in
        --check) CHECK_ONLY=true ;;
        *) fail "unknown argument: $arg" ;;
    esac
done

# ── pre-flight checks ────────────────────────────────────────────────────────

require_cmd cargo  "Install Rust: curl https://sh.rustup.rs -sSf | sh"
require_cmd maturin "Install maturin: pip install maturin"

if $CHECK_ONLY; then
    log "Running cargo check …"
    cd "$RUST_DIR"
    cargo check
    log "cargo check OK"
    exit 0
fi

# ── build ────────────────────────────────────────────────────────────────────

log "Building ${#MODULES[@]} Rust extension modules (release profile) …"

for mod in "${MODULES[@]}"; do
    mod_dir="$RUST_DIR/$mod"
    [ -d "$mod_dir" ] || fail "module directory not found: $mod_dir"
    log "  Building $mod …"
    (cd "$mod_dir" && maturin build --release)
    log "  $mod built"
done

log "Build complete. Wheels are in $WHEEL_DIR"

# ── install ───────────────────────────────────────────────────────────────────

log "Installing wheels …"
pip install --force-reinstall "$WHEEL_DIR"/*.whl
log "All Rust extensions installed successfully."
log ""
log "Verify with:"
log "  python -c 'import openalgo_greeks, openalgo_security, openalgo_matcher; print(\"OK\")'"
