# utils/number_formatter.py
"""
Number formatting utilities for Indian numbering system
Formats large numbers in Crores (Cr) and Lakhs (L)
"""

# Try the Rust-native formatter first.  It is significantly faster for
# high-frequency dashboard rendering, especially when many P&L values
# need to be formatted per request.
try:
    import openalgo_utils as _rust_utils

    _RUST_UTILS_AVAILABLE = True
except ImportError:
    _RUST_UTILS_AVAILABLE = False


def format_indian_number(value):
    """
    Format number in Indian format with Cr/L suffixes

    Examples:
        10000000.0 -> 1.00Cr
        9978000.0 -> 99.78L
        10000.0 -> 10000.00
        -5000000.0 -> -50.00L

    Args:
        value: Number to format (int, float, or string)

    Returns:
        Formatted string with Cr/L suffix or decimal format
    """
    try:
        num = float(value)
    except (ValueError, TypeError):
        return str(value)

    if _RUST_UTILS_AVAILABLE:
        return _rust_utils.format_indian_number(num)

    # Python fallback
    is_negative = num < 0
    num = abs(num)

    if num >= 10000000:  # 1 Crore or more
        formatted = f"{num / 10000000:.2f}Cr"
    elif num >= 100000:  # 1 Lakh or more
        formatted = f"{num / 100000:.2f}L"
    else:
        formatted = f"{num:.2f}"

    if is_negative:
        formatted = f"-{formatted}"

    return formatted


def format_indian_currency(value):
    """
    Format number as Indian currency (₹)

    Examples:
        10000000.0 -> ₹1.00Cr
        9978000.0 -> ₹99.78L
        10000.0 -> ₹10000.00

    Args:
        value: Number to format

    Returns:
        Formatted string with ₹ prefix
    """
    formatted = format_indian_number(value)
    return f"₹{formatted}"
