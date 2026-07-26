"""Currency conversion for XOF (CFA franc), EUR and USD.

The EUR/XOF rate is a fixed peg (1 EUR = 655.957 XOF) so it is always
exact and offline. USD uses a static reference rate; live rates can be
requested (httpx, lazy import) and automatically fall back to the static
table when offline.
"""

from __future__ import annotations

from typing import Optional

TOOL_SPEC = {
    "name": "currency",
    "description": "Convert amounts between XOF, EUR and USD (EUR/XOF is the fixed 655.957 peg).",
    "parameters": {
        "type": "object",
        "properties": {
            "amount": {"type": "number", "description": "Amount to convert."},
            "from_currency": {"type": "string", "enum": ["XOF", "EUR", "USD"]},
            "to_currency": {"type": "string", "enum": ["XOF", "EUR", "USD"]},
            "live": {"type": "boolean", "description": "Try live rates first (falls back offline).", "default": False},
        },
        "required": ["amount", "from_currency", "to_currency"],
    },
}

# Static XOF-per-unit table. EUR is the legal peg; USD is a reference value.
STATIC_RATES_XOF = {"XOF": 1.0, "EUR": 655.957, "USD": 600.0}

_LIVE_URL = "https://open.er-api.com/v6/latest/EUR"


async def _live_rates_xof() -> Optional[dict[str, float]]:
    """Fetch live rates (EUR base) and convert to XOF-per-unit. None on failure."""
    try:
        import httpx  # lazy: optional network dependency
        from backend.core.config import get_settings

        async with httpx.AsyncClient(timeout=get_settings().currency_tool_timeout_seconds) as client:
            resp = await client.get(_LIVE_URL)
            data = resp.json()
        rates = data.get("rates", {})
        eur_xof = float(rates["XOF"])
        usd_per_eur = float(rates["USD"])
        return {"XOF": 1.0, "EUR": eur_xof, "USD": eur_xof / usd_per_eur}
    except Exception:
        return None


async def run(amount: float, from_currency: str, to_currency: str, live: bool = False) -> dict:
    """TOOL entrypoint: convert ``amount`` between supported currencies."""
    src = from_currency.upper()
    dst = to_currency.upper()
    supported = set(STATIC_RATES_XOF)
    if src not in supported or dst not in supported:
        return {"success": False, "error": f"unsupported currency (supported: {sorted(supported)})"}

    rates = STATIC_RATES_XOF
    source = "static"
    if live:
        live_rates = await _live_rates_xof()
        if live_rates:
            rates, source = live_rates, "live"

    amount_xof = float(amount) * rates[src]
    converted = amount_xof / rates[dst]
    return {
        "success": True,
        "amount": float(amount),
        "from_currency": src,
        "to_currency": dst,
        "result": round(converted, 2),
        "rate_source": source,
        "rates_xof_per_unit": rates,
    }
