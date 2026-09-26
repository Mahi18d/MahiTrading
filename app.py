import csv
from datetime import datetime, timedelta
import gzip
import hashlib
from io import StringIO
import json
import logging
import os
import re
import sys
import threading
import time
import importlib.util
import urllib.request
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from scipy.signal import argrelextrema
import streamlit as st
import yfinance as yf

try:
    import pyotp
    PYOTP_AVAILABLE = True
except ImportError:
    pyotp = None
    PYOTP_AVAILABLE = False

# SmartAPI's import can contact a public-IP service.  Delay that import until
# the user actually connects, so simply opening the dashboard is network-free.
SMARTAPI_AVAILABLE = importlib.util.find_spec("SmartApi") is not None


# Live order support is enabled only after the user arms it in the UI and
# confirms each individual order. Nothing is submitted merely by changing mode.
LIVE_ORDER_EXECUTION_ENABLED = True
# Angel One documents OpenAPIScripMaster.json as its instrument master. The
# first URL is the current domain; the second is an official legacy hostname
# retained only as a resilience fallback.
OPTION_MASTER_URLS = (
    "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json",
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json",
)
OPTION_SNAPSHOT_TTL_SECONDS = 10
OPTION_GREEKS_TTL_SECONDS = 60
OPTION_CHART_TTL_SECONDS = 60
YFINANCE_LOG_LOCK = threading.RLock()
INDEX_CONSTITUENT_CACHE_LOCK = threading.RLock()
IST_TIMEZONE = ZoneInfo("Asia/Kolkata")
INDEX_CONSTITUENT_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "index_constituents_cache.json"
)


def download_public_chart_data(*args, download_fn=None, **kwargs):
    """Load public Yahoo chart data without treating a missing ticker as a price.

    yfinance emits a terminal-level "possibly delisted" error for any temporary
    Yahoo miss.  That text is not a reliable corporate-action signal, so keep
    the terminal quiet and let the UI show an explicit unavailable state instead.
    """
    downloader = download_fn or yf.download
    yf_logger = logging.getLogger("yfinance")
    with YFINANCE_LOG_LOCK:
        previous_disabled = yf_logger.disabled
        try:
            yf_logger.disabled = True
            data = downloader(*args, **kwargs)
        except Exception:
            return pd.DataFrame(), "Yahoo public chart feed request failed. Try refresh later."
        finally:
            yf_logger.disabled = previous_disabled
    if not isinstance(data, pd.DataFrame) or data.empty:
        return pd.DataFrame(), "Yahoo public chart feed returned no data for this request."
    return data, ""


def missing_public_chart_symbols(data, requested_symbols):
    """Return only requested symbols with no usable Yahoo chart rows."""
    symbols = [requested_symbols] if isinstance(requested_symbols, str) else list(requested_symbols or [])
    if not symbols:
        return []
    if not isinstance(data, pd.DataFrame) or data.empty:
        return symbols
    missing = []
    for symbol in symbols:
        try:
            candidate = data[symbol] if isinstance(data.columns, pd.MultiIndex) else data
            required_ohlc = [field for field in ("Open", "High", "Low", "Close") if field in candidate.columns]
            has_usable_ohlc = (
                len(required_ohlc) == 4
                and not candidate[required_ohlc].dropna(how="any").empty
            )
            if not has_usable_ohlc:
                missing.append(symbol)
        except (KeyError, TypeError, AttributeError):
            missing.append(symbol)
    return missing


def ist_now():
    """Return a timezone-aware timestamp for constituent refresh decisions."""
    return datetime.now(IST_TIMEZONE)


def parse_official_index_constituent_csv(payload, yahoo_suffix=".NS"):
    """Parse and validate NSE Indices' public constituent CSV format."""
    if not isinstance(payload, (bytes, bytearray)) or len(payload) < 20:
        raise ValueError("Official constituent file was empty or unreadable.")
    try:
        text = bytes(payload).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Official constituent file was not UTF-8 CSV data.") from exc
    lowered = text.lstrip().lower()
    if lowered.startswith("<!doctype") or lowered.startswith("<html") or "access denied" in lowered[:500]:
        raise ValueError("Official constituent download returned an HTML/error page.")
    reader = csv.DictReader(StringIO(text))
    if not reader.fieldnames:
        raise ValueError("Official constituent file has no CSV header.")
    normalized_headers = {str(header).strip().casefold(): header for header in reader.fieldnames if header}
    symbol_header = normalized_headers.get("symbol")
    if not symbol_header:
        raise ValueError("Official constituent file has no Symbol column.")

    symbols = []
    seen = set()
    for row in reader:
        raw_symbol = str(row.get(symbol_header) or "").strip().upper()
        if not raw_symbol:
            raise ValueError("Official constituent file contains an empty Symbol value.")
        raw_symbol = raw_symbol.removesuffix(".NS")
        if not re.fullmatch(r"[A-Z0-9&.\-]+", raw_symbol):
            raise ValueError(f"Official constituent file contains an invalid Symbol: {raw_symbol!r}.")
        if raw_symbol in seen:
            raise ValueError(f"Official constituent file contains a duplicate Symbol: {raw_symbol}.")
        seen.add(raw_symbol)
        symbols.append(f"{raw_symbol}{yahoo_suffix}")
    if not symbols:
        raise ValueError("Official constituent file contained no symbols.")
    return symbols


def fetch_official_index_constituents(source_config, urlopen_fn=None):
    """Fetch one official public NSE Indices CSV with bounded retry and validation."""
    source_url = str(source_config.get("url") or "")
    checked_at = ist_now().strftime("%Y-%m-%d %H:%M:%S %Z")
    metadata = {
        "source_url": source_url,
        "publisher": "NSE Indices public constituent CSV",
        "checked_at": checked_at,
        "last_modified": "",
        "etag": "",
    }
    if not source_url.startswith("https://www.niftyindices.com/"):
        return [], "Official constituent source URL is not configured safely.", metadata

    opener = urlopen_fn or urllib.request.urlopen
    failures = []
    for _attempt in range(2):
        try:
            request = urllib.request.Request(
                source_url,
                headers={
                    "User-Agent": "MahiTrading/1.0",
                    "Accept": "text/csv,application/vnd.ms-excel,text/plain,*/*",
                    "Referer": "https://www.niftyindices.com/",
                },
            )
            with opener(request, timeout=10) as response:
                status = getattr(response, "status", None)
                if status is None and hasattr(response, "getcode"):
                    status = response.getcode()
                if status is not None and not 200 <= int(status) < 300:
                    raise OSError(f"HTTP {status}")
                payload = response.read()
                headers = getattr(response, "headers", {})
            symbols = parse_official_index_constituent_csv(payload)
            minimum = int(source_config.get("minimum", 1))
            maximum = int(source_config.get("maximum", 10_000))
            if not minimum <= len(symbols) <= maximum:
                raise ValueError(
                    f"Official file returned {len(symbols)} symbols; expected {minimum}-{maximum}."
                )
            metadata.update({
                "last_modified": headers.get("Last-Modified", "") if hasattr(headers, "get") else "",
                "etag": headers.get("ETag", "") if hasattr(headers, "get") else "",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "count": len(symbols),
                "fetched_at": ist_now().strftime("%Y-%m-%d %H:%M:%S %Z"),
            })
            return symbols, "", metadata
        except Exception as exc:
            failures.append(str(exc))
    return [], "Official constituent download failed: " + " | ".join(failures), metadata


def _read_index_constituent_cache():
    """Read only a locally-created, previously validated constituent cache."""
    with INDEX_CONSTITUENT_CACHE_LOCK:
        try:
            with open(INDEX_CONSTITUENT_CACHE_FILE, "r", encoding="utf-8") as handle:
                cached = json.load(handle)
            return cached if isinstance(cached, dict) else {}
        except (OSError, ValueError, json.JSONDecodeError):
            return {}


def _write_index_constituent_cache(cache_data):
    """Atomically persist validated official membership without writing failed data."""
    with INDEX_CONSTITUENT_CACHE_LOCK:
        temporary_path = f"{INDEX_CONSTITUENT_CACHE_FILE}.tmp"
        try:
            with open(temporary_path, "w", encoding="utf-8") as handle:
                json.dump(cache_data, handle, indent=2, sort_keys=True)
            os.replace(temporary_path, INDEX_CONSTITUENT_CACHE_FILE)
            return ""
        except OSError as exc:
            try:
                if os.path.exists(temporary_path):
                    os.remove(temporary_path)
            except OSError:
                pass
            return f"Could not save the local constituent cache: {exc}"


def resolve_active_index_basket(basket_name, ist_date, force_refresh=False):
    """Return verified current membership, a labelled stale cache, or a labelled fallback."""
    fallback_symbols = list(FALLBACK_INDEX_BASKETS.get(basket_name, []))
    source_config = INDEX_BASKET_SOURCES.get(basket_name)
    checked_at = ist_now().strftime("%Y-%m-%d %H:%M:%S %Z")
    if not source_config:
        return {
            "symbols": fallback_symbols,
            "state": "bundled",
            "source": "Bundled watchlist",
            "checked_at": checked_at,
            "last_success_at": "",
            "last_modified": "",
            "error": "This basket has no automatic official constituent source configured.",
        }

    cache = _read_index_constituent_cache()
    records = cache.get("baskets", {}) if isinstance(cache.get("baskets", {}), dict) else {}
    cached = records.get(basket_name, {}) if isinstance(records.get(basket_name, {}), dict) else {}
    cached_symbols = cached.get("symbols", []) if isinstance(cached.get("symbols", []), list) else []
    if (
        not force_refresh
        and cached_symbols
        and cached.get("official_ist_date") == ist_date
        and cached.get("source_url") == source_config["url"]
    ):
        return {
            "symbols": cached_symbols,
            "state": "official",
            "source": "Official NSE Indices public constituent CSV",
            "source_url": source_config["url"],
            "checked_at": cached.get("fetched_at", checked_at),
            "last_success_at": cached.get("fetched_at", ""),
            "last_modified": cached.get("last_modified", ""),
            "count": len(cached_symbols),
            "error": "",
        }

    symbols, error, metadata = fetch_official_index_constituents(source_config)
    if symbols:
        records[basket_name] = {
            "symbols": symbols,
            "official_ist_date": ist_date,
            "source_url": metadata["source_url"],
            "fetched_at": metadata["fetched_at"],
            "last_modified": metadata.get("last_modified", ""),
            "etag": metadata.get("etag", ""),
            "sha256": metadata.get("sha256", ""),
        }
        cache["baskets"] = records
        cache_error = _write_index_constituent_cache(cache)
        return {
            "symbols": symbols,
            "state": "official",
            "source": metadata["publisher"],
            "source_url": metadata["source_url"],
            "checked_at": metadata["fetched_at"],
            "last_success_at": metadata["fetched_at"],
            "last_modified": metadata.get("last_modified", ""),
            "count": len(symbols),
            "error": cache_error,
        }
    if cached_symbols:
        return {
            "symbols": cached_symbols,
            "state": "stale",
            "source": "Previously validated NSE Indices constituent cache",
            "source_url": cached.get("source_url", source_config["url"]),
            "checked_at": checked_at,
            "last_success_at": cached.get("fetched_at", ""),
            "last_modified": cached.get("last_modified", ""),
            "count": len(cached_symbols),
            "error": error,
        }
    return {
        "symbols": fallback_symbols,
        "state": "bundled_fallback" if fallback_symbols else "unavailable",
        "source": "Bundled backup" if fallback_symbols else "No verified constituent list",
        "source_url": source_config["url"],
        "checked_at": checked_at,
        "last_success_at": "",
        "last_modified": "",
        "count": len(fallback_symbols),
        "error": error,
    }


def _load_active_index_basket(basket_name, ist_date, force_refresh):
    """Cache UI work briefly; disk metadata still enforces one official check per IST date."""
    return resolve_active_index_basket(basket_name, ist_date, force_refresh=bool(force_refresh))


# Keep the network-free self-test free of Streamlit's bare-runtime cache warning.
if "--self-test" in sys.argv:
    load_active_index_basket = _load_active_index_basket
else:
    load_active_index_basket = st.cache_data(ttl=900, show_spinner=False)(_load_active_index_basket)


def fetch_angel_instrument_master(urls=OPTION_MASTER_URLS, urlopen_fn=None):
    """Download Angel One's published instrument master with a safe host fallback."""
    opener = urlopen_fn or urllib.request.urlopen
    failures = []
    for url in urls:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "MahiTrading/1.0"})
            with opener(request, timeout=45) as response:
                payload = response.read()
            if payload[:2] == b"\x1f\x8b":
                payload = gzip.decompress(payload)
            rows = json.loads(payload.decode("utf-8-sig"))
            if isinstance(rows, list):
                return rows, "", url
            failures.append(f"{url}: response was not an instrument list")
        except Exception as exc:
            failures.append(f"{url}: {exc}")
    return [], "Could not load Angel One's instrument master. " + " | ".join(failures), ""


def _as_float(value):
    """Return a finite float or None without silently substituting a value."""
    try:
        result = float(str(value).replace(",", "").strip())
        return result if np.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _as_int(value):
    number = _as_float(value)
    return int(number) if number is not None else None


def _option_side(value):
    text = str(value or "").strip().upper()
    if text.endswith("CE") or text == "CE" or "CALL" in text:
        return "CE"
    if text.endswith("PE") or text == "PE" or "PUT" in text:
        return "PE"
    return ""


def _parse_angel_expiry(value):
    """Parse the date formats used by Angel One's instrument master."""
    text = str(value or "").strip().upper()
    for fmt in ("%d%b%Y", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _normalise_angel_strike(value):
    """Angel's master stores strikes in paisa; expose the rupee strike only."""
    raw = _as_float(value)
    if raw is None:
        return None
    return raw / 100.0 if abs(raw) >= 10000 else raw


def _normalise_angel_tick(value):
    raw = _as_float(value)
    if raw is None:
        return None
    return raw / 100.0 if raw >= 1 else raw


def extract_option_contracts(master_rows, underlying):
    """Build option-contract metadata solely from Angel One's master file."""
    contracts = []
    target = str(underlying or "").strip().upper()
    for item in master_rows or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("exch_seg", "")).upper() != "NFO":
            continue
        instrument_type = str(item.get("instrumenttype", "")).upper()
        symbol = str(item.get("symbol", "")).strip().upper()
        side = _option_side(symbol or item.get("optiontype"))
        if instrument_type not in {"OPTIDX", "OPTSTK"} or side not in {"CE", "PE"}:
            continue
        if str(item.get("name", "")).strip().upper() != target:
            continue
        expiry_raw = str(item.get("expiry", "")).strip().upper()
        expiry_date = _parse_angel_expiry(expiry_raw)
        if expiry_date is not None and expiry_date < datetime.now().date():
            continue
        strike = _normalise_angel_strike(item.get("strike"))
        lot_size = _as_int(item.get("lotsize"))
        if not symbol or not item.get("token") or strike is None or lot_size is None or lot_size <= 0:
            continue
        contracts.append({
            "symbol": symbol,
            "token": str(item["token"]),
            "underlying": target,
            "exchange": "NFO",
            "instrument_type": instrument_type,
            "expiry_raw": expiry_raw,
            "expiry_date": expiry_date,
            "expiry_label": expiry_date.strftime("%d %b %Y") if expiry_date else expiry_raw,
            "strike": strike,
            "side": side,
            "lot_size": lot_size,
            "tick_size": _normalise_angel_tick(item.get("tick_size")),
        })
    return sorted(
        contracts,
        key=lambda row: (
            row["expiry_date"] is None,
            row["expiry_date"] or datetime.max.date(),
            row["strike"],
            row["side"],
        ),
    )


def calculate_directional_preview(action, entry_price, stop_points, target_points, quantity):
    """Calculate direction-aware levels and illustrative P&L without trading."""
    side = str(action).upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError("action must be BUY or SELL")
    entry = _as_float(entry_price)
    stop = _as_float(stop_points)
    target = _as_float(target_points)
    qty = _as_int(quantity)
    if entry is None or stop is None or target is None or qty is None:
        raise ValueError("entry, stop, target and quantity must be numeric")
    if entry <= 0 or stop <= 0 or target <= 0 or qty <= 0:
        raise ValueError("entry, stop, target and quantity must be positive")
    if side == "BUY":
        stop_price, target_price = entry - stop, entry + target
    else:
        stop_price, target_price = entry + stop, entry - target
    return {
        "action": side,
        "entry_price": round(entry, 2),
        "stop_price": round(stop_price, 2),
        "target_price": round(target_price, 2),
        "loss_pnl": round(-stop * qty, 2),
        "target_pnl": round(target * qty, 2),
        "gross_notional": round(entry * qty, 2),
        "quantity": qty,
    }


def build_live_order_params(contract, quantity, action, order_kind, limit_price=None, stoploss_points=None, target_points=None):
    """Build a validated Angel One order payload from a broker-resolved contract."""
    side = str(action or "").upper()
    kind = str(order_kind or "").upper()
    qty = _as_int(quantity)
    if side not in {"BUY", "SELL"}:
        raise ValueError("Live order side must be BUY or SELL.")
    if kind not in {"NORMAL", "ROBO"}:
        raise ValueError("Live order type must be NORMAL or ROBO.")
    if not isinstance(contract, dict) or not all(contract.get(key) for key in ("symbol", "token", "exchange")):
        raise ValueError("A broker-resolved symbol, token, and exchange are required.")
    if qty is None or qty <= 0:
        raise ValueError("Live order quantity must be a positive whole number.")

    params = {
        "variety": kind,
        "tradingsymbol": str(contract["symbol"]),
        "symboltoken": str(contract["token"]),
        "transactiontype": side,
        "exchange": str(contract["exchange"]),
        "duration": "DAY",
        "quantity": str(qty),
    }
    if kind == "NORMAL":
        params.update({
            "ordertype": "MARKET",
            "producttype": str(contract.get("product_type", "INTRADAY")),
            "price": "0",
            "squareoff": "0",
            "stoploss": "0",
        })
        return params

    price = _as_float(limit_price)
    stoploss = _as_float(stoploss_points)
    target = _as_float(target_points)
    if price is None or price <= 0:
        raise ValueError("ROBO orders require a positive limit price.")
    if stoploss is None or stoploss <= 0 or target is None or target <= 0:
        raise ValueError("ROBO orders require positive stop-loss and target offsets.")
    params.update({
        "ordertype": "LIMIT",
        "producttype": "BO",
        "price": f"{price:.2f}",
        "squareoff": f"{target:.2f}",
        "stoploss": f"{stoploss:.2f}",
    })
    return params


def fetch_selected_option_snapshot(smart_api, contract):
    """Request the broker LTP for exactly one selected option contract."""
    if smart_api is None:
        return {"ok": False, "state": "disconnected", "message": "Angel One is not connected."}
    if not contract or not contract.get("token") or not contract.get("symbol"):
        return {"ok": False, "state": "invalid_contract", "message": "Select a valid Angel One option contract."}
    try:
        response = smart_api.ltpData(contract.get("exchange", "NFO"), contract["symbol"], str(contract["token"]))
    except Exception as exc:
        return {"ok": False, "state": "error", "message": f"Broker LTP request failed: {exc}"}
    if not isinstance(response, dict) or response.get("status") is False:
        message = response.get("message", "Broker did not return an LTP.") if isinstance(response, dict) else "Invalid broker response."
        return {"ok": False, "state": "error", "message": message}
    data = response.get("data") or {}
    if isinstance(data, list):
        data = data[0] if data else {}
    ltp = _as_float(data.get("ltp") or data.get("last_traded_price")) if isinstance(data, dict) else None
    if ltp is None or ltp <= 0:
        return {"ok": False, "state": "unavailable", "message": "Broker response contains no usable option LTP."}
    return {
        "ok": True,
        "state": "snapshot",
        "ltp": ltp,
        "close": _as_float(data.get("close")),
        "open": _as_float(data.get("open")),
        "high": _as_float(data.get("high")),
        "low": _as_float(data.get("low")),
        "fetched_at": time.time(),
        "source": "Angel One SmartAPI LTP",
    }


def fetch_option_greeks(smart_api, underlying, expiry_raw):
    """Return only raw Greeks supplied by SmartAPI; absence is reported, never filled."""
    if smart_api is None:
        return {"ok": False, "state": "disconnected", "message": "Angel One is not connected.", "rows": []}
    try:
        response = smart_api.optionGreek({"name": str(underlying).upper(), "expirydate": str(expiry_raw).upper()})
    except Exception as exc:
        return {"ok": False, "state": "error", "message": f"Broker Greeks request failed: {exc}", "rows": []}
    if not isinstance(response, dict) or response.get("status") is False:
        message = response.get("message", "Greeks are unavailable for this expiry.") if isinstance(response, dict) else "Invalid broker response."
        return {"ok": False, "state": "unavailable", "message": message, "rows": []}
    data = response.get("data")
    rows = data if isinstance(data, list) else []
    return {
        "ok": bool(rows),
        "state": "snapshot" if rows else "unavailable",
        "message": "" if rows else "Broker returned no Greeks for this expiry.",
        "rows": rows,
        "fetched_at": time.time(),
        "source": "Angel One SmartAPI Option Greek",
    }


def selected_contract_greeks(greek_rows, contract):
    """Find raw broker Greek fields that unambiguously match the selected contract."""
    if not contract:
        return {}
    wanted_symbol = str(contract.get("symbol", "")).upper()
    wanted_side = contract.get("side")
    wanted_strike = contract.get("strike")
    for row in greek_rows or []:
        if not isinstance(row, dict):
            continue
        row_symbol = str(row.get("symbol") or row.get("tradingsymbol") or row.get("tradingSymbol") or "").upper()
        symbol_match = bool(wanted_symbol and row_symbol == wanted_symbol)
        row_side = _option_side(row.get("optionType") or row.get("optiontype") or row_symbol)
        row_strike = _normalise_angel_strike(row.get("strikePrice") or row.get("strikeprice") or row.get("strike"))
        structural_match = row_side == wanted_side and row_strike is not None and wanted_strike is not None and abs(row_strike - wanted_strike) < 0.001
        if not (symbol_match or structural_match):
            continue
        fields = {}
        for label, keys in {
            "Delta": ("delta",), "Gamma": ("gamma",), "Theta": ("theta",),
            "Vega": ("vega",), "IV": ("impliedVolatility", "impliedvolatility", "iv"),
            "OI": ("openInterest", "openinterest", "oi"),
        }.items():
            for key in keys:
                if row.get(key) not in (None, ""):
                    fields[label] = row[key]
                    break
        return fields
    return {}


def fetch_option_candles(smart_api, contract, interval="FIVE_MINUTE"):
    """Fetch a chart only when SmartAPI actually returns candles for the selected token."""
    if smart_api is None:
        return {"ok": False, "state": "disconnected", "message": "Angel One is not connected.", "candles": []}
    if not hasattr(smart_api, "getCandleData"):
        return {"ok": False, "state": "unavailable", "message": "Installed SmartAPI client has no candle-data method.", "candles": []}
    now = datetime.now()
    params = {
        "exchange": contract.get("exchange", "NFO"),
        "symboltoken": str(contract.get("token", "")),
        "interval": interval,
        "fromdate": (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M"),
        "todate": now.strftime("%Y-%m-%d %H:%M"),
    }
    try:
        response = smart_api.getCandleData(params)
    except Exception as exc:
        return {"ok": False, "state": "error", "message": f"Broker candle request failed: {exc}", "candles": []}
    if not isinstance(response, dict) or response.get("status") is False:
        message = response.get("message", "No broker candles were returned.") if isinstance(response, dict) else "Invalid broker response."
        return {"ok": False, "state": "unavailable", "message": message, "candles": []}
    rows = response.get("data") if isinstance(response.get("data"), list) else []
    if not rows:
        return {"ok": False, "state": "unavailable", "message": "Broker returned no candles for this contract.", "candles": []}
    return {"ok": True, "state": "snapshot", "candles": rows, "fetched_at": time.time(), "source": "Angel One SmartAPI candle data"}


def run_self_tests():
    """Network-free regression checks, callable with: python app.py --self-test."""
    assert importlib.util.find_spec("streamlit") is not None, "streamlit is not installed"
    assert importlib.util.find_spec("SmartApi") is not None, "smartapi-python is not installed"
    buy = calculate_directional_preview("BUY", 100, 10, 30, 50)
    sell = calculate_directional_preview("SELL", 100, 10, 30, 50)
    assert (buy["stop_price"], buy["target_price"], buy["loss_pnl"], buy["target_pnl"]) == (90.0, 130.0, -500.0, 1500.0)
    assert (sell["stop_price"], sell["target_price"], sell["loss_pnl"], sell["target_pnl"]) == (110.0, 70.0, -500.0, 1500.0)
    live_contract = {"exchange": "NSE", "symbol": "SBIN-EQ", "token": "3045", "product_type": "INTRADAY"}
    normal_payload = build_live_order_params(live_contract, 5, "SELL", "NORMAL")
    robo_payload = build_live_order_params(live_contract, 5, "BUY", "ROBO", 100.0, 10.0, 30.0)
    assert normal_payload["ordertype"] == "MARKET" and normal_payload["transactiontype"] == "SELL"
    assert robo_payload["producttype"] == "BO" and robo_payload["stoploss"] == "10.00" and robo_payload["squareoff"] == "30.00"
    contract = {"exchange": "NFO", "symbol": "NIFTY26SEP25000CE", "token": "12345"}
    disconnected = fetch_selected_option_snapshot(None, contract)
    assert disconnected["ok"] is False and disconnected["state"] == "disconnected"

    class MockSmartApi:
        def ltpData(self, exchange, symbol, token):
            assert (exchange, symbol, token) == ("NFO", "NIFTY26SEP25000CE", "12345")
            return {"status": True, "data": {"ltp": 123.45, "close": 120.0}}

    connected = fetch_selected_option_snapshot(MockSmartApi(), contract)
    assert connected["ok"] is True and connected["ltp"] == 123.45
    master = [{"exch_seg": "NFO", "instrumenttype": "OPTIDX", "symbol": "NIFTY26SEP25000CE", "name": "NIFTY", "expiry": "26SEP2026", "strike": "2500000.000000", "lotsize": "65", "tick_size": "5.000000", "token": "12345"}]
    parsed = extract_option_contracts(master, "NIFTY")
    assert len(parsed) == 1 and parsed[0]["strike"] == 25000.0 and parsed[0]["lot_size"] == 65 and parsed[0]["tick_size"] == 0.05

    good_chart = pd.DataFrame({
        "Open": [100.0, 101.0], "High": [102.0, 103.0],
        "Low": [99.0, 100.0], "Close": [101.0, 102.0], "Volume": [10, 12],
    })
    bad_chart = pd.DataFrame({column: [np.nan, np.nan] for column in good_chart.columns})
    mock_batch = pd.concat({"GOOD.NS": good_chart, "MISSING.NS": bad_chart}, axis=1)
    public_chart, public_chart_message = download_public_chart_data(
        ["GOOD.NS", "MISSING.NS"], download_fn=lambda *args, **kwargs: mock_batch,
    )
    assert not public_chart_message
    assert missing_public_chart_symbols(public_chart, ["GOOD.NS", "MISSING.NS"]) == ["MISSING.NS"]
    failed_chart, failed_chart_message = download_public_chart_data(
        "OFFLINE.NS", download_fn=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")),
    )
    assert failed_chart.empty and "request failed" in failed_chart_message

    constituent_csv = (
        b"\xef\xbb\xbfCompany Name,Industry,Symbol,Series,ISIN Code\n"
        b"Example One,Services,EXAMPLE1,EQ,INE000A01001\n"
        b"Example Two,Services,EXAMPLE-2,EQ,INE000A01002\n"
    )

    class MockConstituentResponse:
        status = 200
        headers = {"Last-Modified": "Fri, 25 Sep 2026 18:00:00 GMT", "ETag": "test-etag"}

        def read(self):
            return constituent_csv

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    constituent_source = {
        "url": "https://www.niftyindices.com/IndexConstituent/test.csv",
        "minimum": 2,
        "maximum": 2,
    }
    loaded_symbols, constituent_error, constituent_metadata = fetch_official_index_constituents(
        constituent_source, lambda request, timeout: MockConstituentResponse()
    )
    assert loaded_symbols == ["EXAMPLE1.NS", "EXAMPLE-2.NS"] and not constituent_error
    assert constituent_metadata["last_modified"].startswith("Fri, 25 Sep")
    try:
        parse_official_index_constituent_csv(b"Company Name,Industry\nExample,Services\n")
        raise AssertionError("A missing Symbol column must be rejected.")
    except ValueError as exc:
        assert "Symbol column" in str(exc)
    unavailable_symbols, unavailable_error, _ = fetch_official_index_constituents(
        constituent_source,
        lambda request, timeout: (_ for _ in ()).throw(OSError("source offline")),
    )
    assert not unavailable_symbols and "download failed" in unavailable_error

    class MockMasterResponse:
        def __init__(self, payload):
            self.payload = payload

        def read(self):
            return self.payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    attempted_urls = []

    def mock_master_open(request, timeout):
        attempted_urls.append(request.full_url)
        if request.full_url == "https://example.invalid/old.json":
            raise OSError("HTTP Error 404: Not Found")
        return MockMasterResponse(gzip.compress(json.dumps(master).encode("utf-8")))

    rows, master_error, master_source = fetch_angel_instrument_master(
        ("https://example.invalid/old.json", "https://example.valid/OpenAPIScripMaster.json"), mock_master_open
    )
    assert rows == master and not master_error and master_source.endswith("OpenAPIScripMaster.json")
    assert attempted_urls == ["https://example.invalid/old.json", "https://example.valid/OpenAPIScripMaster.json"]
    print("Mahi Trading self-tests passed.")


if "--self-test" in sys.argv:
    run_self_tests()
    raise SystemExit(0)

# Prevent mapbox template crashes
pio.templates.default = "none"

st.set_page_config(
    page_title="Mahi Trading",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# --- Professional Dark Abstract Multi-Color Glassmorphic Theme ---
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700;800&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');

    .stApp {
        background-color: #070913;
        background-image: 
            radial-gradient(at 10% 12%, rgba(0, 242, 254, 0.14) 0px, transparent 45%),
            radial-gradient(at 88% 18%, rgba(139, 92, 246, 0.16) 0px, transparent 50%),
            radial-gradient(at 52% 85%, rgba(244, 63, 94, 0.12) 0px, transparent 55%),
            radial-gradient(at 90% 88%, rgba(16, 185, 129, 0.10) 0px, transparent 45%),
            linear-gradient(180deg, #070913 0%, #0c1022 100%);
        background-attachment: fixed;
        color: #f1f5f9;
        font-family: 'Plus Jakarta Sans', -apple-system, sans-serif;
    }

    .header-box {
        display: flex;
        justify-content: space-between;
        align-items: center;
        background: linear-gradient(135deg, rgba(15, 23, 42, 0.85) 0%, rgba(30, 27, 75, 0.6) 50%, rgba(15, 23, 42, 0.85) 100%);
        backdrop-filter: blur(16px);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 12px;
        padding: 16px 24px;
        margin-bottom: 14px;
        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
    }
    .brand-title {
        font-size: 28px;
        font-weight: 800;
        color: #ffffff;
        letter-spacing: -0.5px;
        line-height: 1.1;
    }
    .brand-accent {
        background: linear-gradient(90deg, #00f2fe, #4facfe, #a855f7);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .brand-sub {
        font-size: 11px;
        color: #94a3b8;
        text-transform: uppercase;
        letter-spacing: 1px;
        font-weight: 600;
        margin-top: 4px;
    }

    .mode-badge-paper {
        background: rgba(245, 158, 11, 0.2);
        color: #fbbf24;
        border: 1px solid rgba(245, 158, 11, 0.4);
        padding: 5px 12px;
        border-radius: 8px;
        font-size: 11px;
        font-weight: 800;
        font-family: 'JetBrains Mono', monospace;
    }
    .mode-badge-live {
        background: rgba(16, 185, 129, 0.2);
        color: #34d399;
        border: 1px solid rgba(52, 211, 153, 0.4);
        padding: 5px 12px;
        border-radius: 8px;
        font-size: 11px;
        font-weight: 800;
        font-family: 'JetBrains Mono', monospace;
    }

    .universe-strip {
        background: linear-gradient(90deg, rgba(15, 23, 42, 0.8) 0%, rgba(20, 20, 45, 0.8) 100%);
        border: 1px solid rgba(0, 242, 254, 0.2);
        border-radius: 10px;
        padding: 12px 18px;
        margin-bottom: 14px;
        box-shadow: 0 4px 20px rgba(0, 242, 254, 0.05);
    }

    .disclaimer-banner {
        border-left: 4px solid #00f2fe;
        background: rgba(15, 23, 42, 0.7);
        border-top: 1px solid rgba(255, 255, 255, 0.05);
        border-right: 1px solid rgba(255, 255, 255, 0.05);
        border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        border-radius: 6px;
        padding: 8px 14px;
        font-size: 12px;
        color: #93c5fd;
        margin-bottom: 14px;
    }

    .inspector-card {
        background: linear-gradient(180deg, rgba(15, 23, 42, 0.85) 0%, rgba(20, 25, 45, 0.85) 100%);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 12px;
        padding: 18px;
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
    }
    .check-item {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 7px 0;
        border-bottom: 1px solid rgba(255, 255, 255, 0.04);
        font-size: 12px;
    }
    .tag-bull { color: #34d399; font-weight: 700; font-family: 'JetBrains Mono', monospace; }
    .tag-bear { color: #fb7185; font-weight: 700; font-family: 'JetBrains Mono', monospace; }

    .calc-box {
        background: linear-gradient(135deg, rgba(15, 23, 42, 0.9) 0%, rgba(30, 27, 75, 0.7) 100%);
        border: 1px solid rgba(99, 102, 241, 0.3);
        border-radius: 8px;
        padding: 12px 16px;
        margin: 10px 0;
        font-size: 12px;
    }
    .calc-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 4px 0;
    }

    .bottom-card {
        background: linear-gradient(135deg, rgba(15, 23, 42, 0.8) 0%, rgba(20, 20, 45, 0.8) 100%);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 10px;
        padding: 18px;
        margin-top: 20px;
    }
    .bottom-title {
        font-size: 15px;
        font-weight: 700;
        color: #f8fafc;
        margin-bottom: 12px;
        display: flex;
        align-items: center;
        gap: 6px;
    }

    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    }
    .stTabs [data-baseweb="tab"] {
        background-color: rgba(15, 23, 42, 0.7);
        border: 1px solid rgba(255, 255, 255, 0.06);
        border-radius: 8px 8px 0 0;
        color: #94a3b8;
        padding: 8px 22px;
        font-weight: 600;
    }
    .stTabs [aria-selected="true"] {
        background: linear-gradient(180deg, rgba(30, 41, 59, 0.9) 0%, rgba(15, 23, 42, 0.9) 100%) !important;
        color: #00f2fe !important;
        font-weight: 800;
        border-bottom: 2px solid #00f2fe;
    }
    div.stButton > button, div[data-testid="stBaseButton-secondary"] {
        background: linear-gradient(135deg, rgba(15, 23, 42, 0.96), rgba(30, 27, 75, 0.92));
        border: 1px solid rgba(56, 189, 248, 0.42);
        color: #e2e8f0;
        border-radius: 8px;
        font-weight: 700;
        box-shadow: 0 5px 16px rgba(0, 0, 0, 0.28);
    }
    div.stButton > button:hover, div[data-testid="stBaseButton-secondary"]:hover {
        border-color: #22d3ee;
        color: #ffffff;
        background: linear-gradient(135deg, rgba(14, 116, 144, 0.55), rgba(91, 33, 182, 0.5));
    }
    div[data-testid="stRadio"] label {
        background: rgba(15, 23, 42, 0.75);
        border: 1px solid rgba(148, 163, 184, 0.20);
        border-radius: 6px;
        padding: 4px 8px;
    }
    div[class*="st-key-btn_buy_"] button,
    div[class*="st-key-mkt_buy_"] button,
    div[class*="st-key-live_option_buy_"] button {
        min-height: 44px;
        background: linear-gradient(135deg, #166534, #22c55e) !important;
        border: 1px solid #4ade80 !important;
        color: #f0fdf4 !important;
        box-shadow: 0 7px 20px rgba(34, 197, 94, 0.25) !important;
    }
    div[class*="st-key-btn_sell_"] button,
    div[class*="st-key-mkt_sell_"] button,
    div[class*="st-key-live_option_sell_"] button {
        min-height: 44px;
        background: linear-gradient(135deg, #991b1b, #ef4444) !important;
        border: 1px solid #fb7185 !important;
        color: #fff1f2 !important;
        box-shadow: 0 7px 20px rgba(239, 68, 68, 0.25) !important;
    }
    div[class*="st-key-btn_buy_"] button:hover:not(:disabled),
    div[class*="st-key-mkt_buy_"] button:hover:not(:disabled),
    div[class*="st-key-live_option_buy_"] button:hover:not(:disabled) {
        background: linear-gradient(135deg, #15803d, #4ade80) !important;
        transform: translateY(-1px);
    }
    div[class*="st-key-btn_sell_"] button:hover:not(:disabled),
    div[class*="st-key-mkt_sell_"] button:hover:not(:disabled),
    div[class*="st-key-live_option_sell_"] button:hover:not(:disabled) {
        background: linear-gradient(135deg, #b91c1c, #fb7185) !important;
        transform: translateY(-1px);
    }
    div[class*="st-key-btn_buy_"] button:disabled,
    div[class*="st-key-btn_sell_"] button:disabled,
    div[class*="st-key-mkt_buy_"] button:disabled,
    div[class*="st-key-mkt_sell_"] button:disabled,
    div[class*="st-key-live_option_buy_"] button:disabled,
    div[class*="st-key-live_option_sell_"] button:disabled {
        opacity: 0.52;
        cursor: not-allowed;
        box-shadow: none !important;
    }
    .live-arm-card {
        background: linear-gradient(135deg, rgba(127, 29, 29, 0.42), rgba(88, 28, 135, 0.35));
        border: 1px solid rgba(251, 113, 133, 0.50);
        border-radius: 10px;
        padding: 12px 15px;
        color: #fecdd3;
        font-size: 12px;
    }
    .live-ready-card {
        background: linear-gradient(135deg, rgba(20, 83, 45, 0.48), rgba(6, 78, 59, 0.38));
        border: 1px solid rgba(74, 222, 128, 0.50);
        border-radius: 10px;
        padding: 12px 15px;
        color: #dcfce7;
        font-size: 12px;
    }
</style>
""", unsafe_allow_html=True)

def apply_chart_style(fig, height=520):
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(15, 23, 42, 0.5)",
        font=dict(color="#94a3b8", family="Plus Jakarta Sans"),
        height=height,
        margin=dict(l=20, r=20, t=30, b=20),
        xaxis=dict(gridcolor="rgba(255,255,255,0.05)", showgrid=True),
        yaxis=dict(gridcolor="rgba(255,255,255,0.05)", showgrid=True),
    )
    return fig

# --- Persistent Paper Trading Engine ---
PAPER_FILE = "paper_trades.json"

def load_paper_account():
    if os.path.exists(PAPER_FILE):
        try:
            with open(PAPER_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "cash": 500000.0,
        "positions": [],
        "closed_trades": []
    }

def save_paper_account(data):
    try:
        with open(PAPER_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

if "paper_data" not in st.session_state:
    st.session_state["paper_data"] = load_paper_account()

def place_paper_order(symbol, action, qty, entry_price, sl_pts, tp_pts, trail_pts=0.0):
    pdata = st.session_state["paper_data"]
    sl_price = round(entry_price - sl_pts if action == "BUY" else entry_price + sl_pts, 2)
    tp_price = round(entry_price + tp_pts if action == "BUY" else entry_price - tp_pts, 2)
    required_capital = qty * entry_price

    if pdata["cash"] < required_capital and action == "BUY":
        return False, "Insufficient virtual balance."

    pdata["cash"] -= required_capital
    position = {
        "id": f"PAPER-{int(datetime.now().timestamp())}",
        "symbol": symbol,
        "action": action,
        "qty": qty,
        "entry_price": entry_price,
        "sl_price": sl_price,
        "tp_price": tp_price,
        "sl_pts": sl_pts,
        "tp_pts": tp_pts,
        "trail_pts": trail_pts,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": "OPEN"
    }
    pdata["positions"].append(position)
    save_paper_account(pdata)
    return True, f"Paper Order Placed! (ID: {position['id']})"

def evaluate_paper_positions(current_prices):
    pdata = st.session_state["paper_data"]
    still_open = []
    closed_any = False

    for pos in pdata["positions"]:
        sym = pos["symbol"]
        if sym not in current_prices:
            still_open.append(pos)
            continue

        ltp = current_prices[sym]
        closed = False
        exit_price = ltp
        reason = ""

        if pos["action"] == "BUY":
            if ltp >= pos["tp_price"]:
                closed = True
                exit_price = pos["tp_price"]
                reason = "Target Hit (1:3)"
            elif ltp <= pos["sl_price"]:
                closed = True
                exit_price = pos["sl_price"]
                reason = "Stop Loss Hit"
        else:
            if ltp <= pos["tp_price"]:
                closed = True
                exit_price = pos["tp_price"]
                reason = "Target Hit (1:3)"
            elif ltp >= pos["sl_price"]:
                closed = True
                exit_price = pos["sl_price"]
                reason = "Stop Loss Hit"

        if closed:
            closed_any = True
            pnl = (exit_price - pos["entry_price"]) * pos["qty"] if pos["action"] == "BUY" else (pos["entry_price"] - exit_price) * pos["qty"]
            pdata["cash"] += (pos["qty"] * pos["entry_price"]) + pnl
            pos["status"] = "CLOSED"
            pos["exit_price"] = exit_price
            pos["pnl"] = round(pnl, 2)
            pos["exit_reason"] = reason
            pos["exit_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            pdata["closed_trades"].append(pos)
        else:
            still_open.append(pos)

    if closed_any:
        pdata["positions"] = still_open
        save_paper_account(pdata)

# --- Safe Secrets Loader ---
try:
    sec_angel = st.secrets.get("angel_one", {})
except Exception:
    sec_angel = {}

def_api_key = sec_angel.get("api_key", "")
def_client_id = sec_angel.get("client_id", "")
def_pin = sec_angel.get("mpin", "")
def_totp = sec_angel.get("totp_secret", "")

# --- Angel One SmartAPI Utilities ---
@st.cache_data(ttl=21600, show_spinner=False)
def load_angel_option_master():
    """Load Angel One's published instrument catalogue, not a user CSV."""
    rows, error, _source_url = fetch_angel_instrument_master()
    return rows, error


@st.cache_data(ttl=21600, show_spinner=False)
def load_angel_equity_contracts():
    """Return only broker-listed cash-equity contracts from Angel One's master."""
    rows, error, _source_url = fetch_angel_instrument_master()
    if error:
        return [], error
    contracts = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        exchange = str(row.get("exch_seg", "")).upper()
        symbol = str(row.get("symbol", "")).upper()
        if exchange not in {"NSE", "BSE"} or not symbol.endswith("-EQ") or not row.get("token"):
            continue
        contracts.append({
            "exchange": exchange,
            "symbol": symbol,
            "token": str(row["token"]),
            "name": str(row.get("name", "")).upper(),
        })
    return contracts, ""


def resolve_angel_equity_contract(clean_symbol, chart_symbol):
    """Resolve a selected screener row to the exact Angel One cash-equity token."""
    contracts, error = load_angel_equity_contracts()
    if error:
        return None, error
    expected_exchange = "BSE" if str(chart_symbol).upper().endswith(".BO") else "NSE"
    expected_symbol = f"{str(clean_symbol).upper()}-EQ"
    for contract in contracts:
        if contract["exchange"] == expected_exchange and contract["symbol"] == expected_symbol:
            return contract, ""
    return None, f"Angel One has no current {expected_exchange} cash contract for {expected_symbol}."

def connect_angel_one(api_key: str, client_code: str, pin: str, totp_secret: str):
    if not SMARTAPI_AVAILABLE:
        return None, "SmartAPI package is unavailable. Install requirements.txt and restart the app."
    if not PYOTP_AVAILABLE:
        return None, "pyotp package is unavailable. Install requirements.txt and restart the app."
    try:
        from SmartApi import SmartConnect
        smart_api = SmartConnect(api_key=api_key)
        totp = pyotp.TOTP(totp_secret).now()
        session_data = smart_api.generateSession(client_code, pin, totp)
        if session_data.get('status'):
            return smart_api, "Connected successfully"
        return None, session_data.get('message', 'Login failed')
    except Exception as e:
        return None, str(e)

if "smart_api" not in st.session_state and all([def_api_key, def_client_id, def_pin, def_totp]):
    api_instance, _ = connect_angel_one(def_api_key, def_client_id, def_pin, def_totp)
    if api_instance:
        st.session_state["smart_api"] = api_instance


def _cached_broker_result(cache_key, ttl_seconds, fetcher):
    now = time.time()
    cached = st.session_state.get(cache_key)
    if cached and now - cached.get("cached_at", 0) < ttl_seconds:
        result = dict(cached["result"])
        result["age_seconds"] = now - cached["cached_at"]
        return result
    result = fetcher()
    st.session_state[cache_key] = {"cached_at": now, "result": result}
    result = dict(result)
    result["age_seconds"] = 0.0
    return result


def get_option_snapshot_for_ui(smart_api, contract):
    if smart_api is None:
        return fetch_selected_option_snapshot(None, contract)
    return _cached_broker_result(
        f"option_snapshot_{contract['token']}",
        OPTION_SNAPSHOT_TTL_SECONDS,
        lambda: fetch_selected_option_snapshot(smart_api, contract),
    )


def get_option_greeks_for_ui(smart_api, underlying, expiry_raw):
    if smart_api is None:
        return fetch_option_greeks(None, underlying, expiry_raw)
    return _cached_broker_result(
        f"option_greeks_{underlying}_{expiry_raw}",
        OPTION_GREEKS_TTL_SECONDS,
        lambda: fetch_option_greeks(smart_api, underlying, expiry_raw),
    )


def get_option_candles_for_ui(smart_api, contract):
    if smart_api is None:
        return fetch_option_candles(None, contract)
    return _cached_broker_result(
        f"option_candles_{contract['token']}",
        OPTION_CHART_TTL_SECONDS,
        lambda: fetch_option_candles(smart_api, contract),
    )


def broker_snapshot_caption(result):
    if not result.get("ok"):
        return result.get("message", "Broker data is unavailable.")
    age = result.get("age_seconds", 0.0)
    fetched_at = datetime.fromtimestamp(result.get("fetched_at", time.time())).strftime("%H:%M:%S")
    status = "STALE" if age > OPTION_SNAPSHOT_TTL_SECONDS else "BROKER SNAPSHOT"
    return f"{status} · fetched {fetched_at} IST · age {age:.0f}s · {result.get('source', 'Angel One')}"

def live_order_is_armed():
    return bool(
        LIVE_ORDER_EXECUTION_ENABLED
        and st.session_state.get("live_orders_armed")
        and st.session_state.get("smart_api") is not None
    )


def verify_live_broker_session(smart_api):
    """Require a successful authenticated broker call immediately before submission."""
    if smart_api is None:
        return False, "Connect Angel One before submitting a live order."
    try:
        response = smart_api.rmsLimit()
    except Exception as exc:
        return False, f"Broker session preflight failed: {exc}"
    if isinstance(response, dict) and response.get("status") is False:
        return False, response.get("message", "Broker rejected the session preflight.")
    if response is None:
        return False, "Broker session preflight returned no response."
    return True, ""


def submit_live_order(smart_api, order_params):
    """Submit an explicitly confirmed order and report it as submitted, never filled."""
    if not LIVE_ORDER_EXECUTION_ENABLED:
        return False, "Live order support is disabled in this build.", None
    session_ok, session_error = verify_live_broker_session(smart_api)
    if not session_ok:
        return False, session_error, None
    try:
        if hasattr(smart_api, "placeOrderFullResponse"):
            response = smart_api.placeOrderFullResponse(order_params)
        else:
            response = smart_api.placeOrder(order_params)
    except Exception as exc:
        return False, f"Broker submission failed: {exc}", None

    if isinstance(response, dict):
        if response.get("status") is False:
            return False, response.get("message", "Broker rejected the order."), response
        response_data = response.get("data") or {}
        if isinstance(response_data, list):
            response_data = response_data[0] if response_data else {}
        order_id = ""
        if isinstance(response_data, dict):
            order_id = str(response_data.get("orderid") or response_data.get("uniqueorderid") or "")
        order_id = order_id or str(response.get("orderid") or response.get("uniqueorderid") or "")
        return True, "Order submitted to Angel One; submission is not a fill confirmation.", {"order_id": order_id, "response": response}
    if response:
        return True, "Order submitted to Angel One; submission is not a fill confirmation.", {"order_id": str(response), "response": response}
    return False, "Broker returned an empty order-submission response.", response


def place_bracket_robo_order(smart_api, contract, qty, limit_price, stoploss_pts, target_pts, action="BUY"):
    try:
        params = build_live_order_params(contract, qty, action, "ROBO", limit_price, stoploss_pts, target_pts)
    except ValueError as exc:
        return False, str(exc), None
    return submit_live_order(smart_api, params)


def place_regular_order(smart_api, contract, qty, action, product_type="INTRADAY"):
    live_contract = dict(contract)
    live_contract["product_type"] = product_type
    try:
        params = build_live_order_params(live_contract, qty, action, "NORMAL")
    except ValueError as exc:
        return False, str(exc), None
    return submit_live_order(smart_api, params)


def submit_live_equity_from_ui(smart_api, clean_symbol, chart_symbol, quantity, action, order_mode, limit_price, stoploss_points, target_points, product_type):
    """Resolve, quote-check, and submit a confirmed cash-equity order."""
    contract, contract_error = resolve_angel_equity_contract(clean_symbol, chart_symbol)
    if contract_error:
        return False, contract_error, None, None
    snapshot = fetch_selected_option_snapshot(smart_api, contract)
    if not snapshot.get("ok"):
        return False, f"Fresh broker LTP is required before submission: {snapshot.get('message', 'unavailable')}", None, snapshot
    if order_mode == "ROBO":
        success, message, details = place_bracket_robo_order(
            smart_api, contract, quantity, limit_price, stoploss_points, target_points, action
        )
    else:
        success, message, details = place_regular_order(
            smart_api, contract, quantity, action, product_type
        )
    return success, message, details, snapshot

# --- Market Universes ---
# These bundled lists are used only when the official daily membership check is
# unavailable. The UI labels that condition clearly; they are not called live.
FALLBACK_INDEX_BASKETS = {
    "NIFTY 50": [
        "ADANIENT.NS", "ADANIPORTS.NS", "APOLLOHOSP.NS", "ASIANPAINT.NS", "AXISBANK.NS",
        "BAJAJ-AUTO.NS", "BAJFINANCE.NS", "BAJAJFINSV.NS", "BEL.NS", "BHARTIARTL.NS",
        "BPCL.NS", "BRITANNIA.NS", "CIPLA.NS", "COALINDIA.NS", "DRREDDY.NS",
        "EICHERMOT.NS", "GRASIM.NS", "HCLTECH.NS", "HDFCBANK.NS", "HDFCLIFE.NS",
        "HEROMOTOCO.NS", "HINDALCO.NS", "HINDUNILVR.NS", "ICICIBANK.NS", "INDUSINDBK.NS",
        "INFY.NS", "ITC.NS", "JSWSTEEL.NS", "KOTAKBANK.NS", "LT.NS",
        "M&M.NS", "MARUTI.NS", "NESTLEIND.NS", "NTPC.NS", "ONGC.NS",
        "POWERGRID.NS", "RELIANCE.NS", "SBILIFE.NS", "SBIN.NS", "SHRIRAMFIN.NS",
        "SUNPHARMA.NS", "TATACONSUM.NS", "TATAMOTORS.NS", "TATASTEEL.NS", "TCS.NS",
        "TECHM.NS", "TITAN.NS", "TRENT.NS", "ULTRACEMCO.NS", "WIPRO.NS"
    ],
    "BANK NIFTY": [
        "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "KOTAKBANK.NS", "AXISBANK.NS",
        "INDUSINDBK.NS", "BANKBARODA.NS", "PNB.NS", "AUBANK.NS", "FEDERALBNK.NS",
        "BANDHANBNK.NS", "IDFCFIRSTB.NS"
    ],
    "SENSEX watchlist (bundled)": [
        "RELIANCE.BO", "TCS.BO", "HDFCBANK.BO", "ICICIBANK.BO", "BHARTIARTL.BO",
        "SBIN.BO", "INFY.BO", "ITC.BO", "LT.BO", "HINDUNILVR.BO", "AXISBANK.BO"
    ],
    "NIFTY MIDCAP 50": [
        "POLYCAB.NS", "PERSISTENT.NS", "DIXON.NS", "COFORGE.NS", "LUPIN.NS",
        "ASTRAL.NS", "CUMMINSIND.NS", "MAXHEALTH.NS", "SUNDARMFIN.NS", "HINDPETRO.NS"
    ]
}

# Official public download links exposed by NSE Indices on each index page.
# The ranges protect the screener from an HTML/error page or truncated CSV.
INDEX_BASKET_SOURCES = {
    "NIFTY 50": {
        "url": "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv",
        "minimum": 45,
        "maximum": 55,
    },
    "BANK NIFTY": {
        "url": "https://www.niftyindices.com/IndexConstituent/ind_niftybanklist.csv",
        "minimum": 8,
        "maximum": 20,
    },
    "NIFTY MIDCAP 50": {
        "url": "https://www.niftyindices.com/IndexConstituent/ind_niftymidcap50list.csv",
        "minimum": 45,
        "maximum": 55,
    },
}

FO_UNIVERSE = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
    "RELIANCE": "RELIANCE.NS",
    "HDFCBANK": "HDFCBANK.NS",
    "ICICIBANK": "ICICIBANK.NS",
    "SBIN": "SBIN.NS",
    "TCS": "TCS.NS",
    "INFY": "INFY.NS",
    "TATAMOTORS": "TATAMOTORS.NS"
}

HORIZON_MAP = {
    "Intraday (15-Min)": ("5d", "15m", 3),
    "BTST (1-Hour)": ("1mo", "60m", 4),
    "Weekly Swing (Daily)": ("6mo", "1d", 5),
    "Mid Term (Daily / 2Y)": ("2y", "1d", 8),
    "Long Term (Weekly)": ("5y", "1wk", 10)
}

# --- Header Box ---
timestamp = ist_now().strftime("%Y-%m-%d %H:%M:%S")

st.markdown(f"""<div class="header-box">
<div>
<div class="brand-title">Mahi <span class="brand-accent">Trading</span></div>
<div class="brand-sub">Multi-Asset Algorithmic Intelligence & Automated Risk Exits</div>
</div>
<div style="text-align:right;">
  <span class="status-badge">● DATA SOURCE CHECK</span>
<div style="font-size:12px; color:#94a3b8; margin-top:4px;">{timestamp} IST</div>
</div>
</div>""", unsafe_allow_html=True)

# --- Execution Mode & Universe Selection Bar ---
st.markdown("""
<div class="universe-strip">
    <div style="display:flex; justify-content:space-between; align-items:center;">
        <span style="font-size:11px; font-weight:800; color:#00f2fe; text-transform:uppercase; letter-spacing:1px;">
            🌐 Active Market Universe & Execution Mode
        </span>
    </div>
</div>
""", unsafe_allow_html=True)

if "force_index_basket_refresh" not in st.session_state:
    st.session_state["force_index_basket_refresh"] = False

u_col1, u_col2, u_col3, u_col4 = st.columns([3.8, 3.8, 2.6, 1.8])
with u_col1:
    selected_basket = st.selectbox("Select Asset Universe", list(FALLBACK_INDEX_BASKETS.keys()), label_visibility="collapsed")
with u_col2:
    selected_horizon = st.selectbox("Select Trading Horizon", list(HORIZON_MAP.keys()), label_visibility="collapsed")
with u_col3:
    exec_env = st.selectbox("Execution Mode", ["📝 Paper Trading", "⚡ Live Broker"], label_visibility="collapsed")
with u_col4:
    if st.button("↻ Refresh list", width="stretch", key="refresh_index_membership"):
        st.session_state["force_index_basket_refresh"] = True
        load_active_index_basket.clear()
        st.rerun()

current_ist = ist_now()
force_index_basket_refresh = st.session_state.pop("force_index_basket_refresh", False)
basket_snapshot = load_active_index_basket(
    selected_basket,
    current_ist.date().isoformat(),
    force_index_basket_refresh,
)
if basket_snapshot["state"] == "official":
    membership_text = (
        f"Membership: {basket_snapshot['source']} · {basket_snapshot['count']} stocks · "
        f"checked {basket_snapshot['checked_at']}"
    )
    if basket_snapshot.get("last_modified"):
        membership_text += f" · publisher file: {basket_snapshot['last_modified']}"
    st.caption(membership_text)
    if basket_snapshot.get("error"):
        st.caption(basket_snapshot["error"])
elif basket_snapshot["state"] == "stale":
    st.warning(
        f"Official membership source could not refresh — using the last validated {basket_snapshot['count']}-stock "
        f"list from {basket_snapshot['last_success_at']}. Membership may be stale. {basket_snapshot['error']}"
    )
elif basket_snapshot["state"] == "bundled_fallback":
    st.warning(
        f"Official membership source unavailable — using a bundled {basket_snapshot['count']}-symbol backup. "
        f"It may be incomplete or stale. {basket_snapshot['error']}"
    )
elif basket_snapshot["state"] == "bundled":
    st.info(
        f"{selected_basket} is a bundled watchlist ({basket_snapshot['count']} symbols), not an automatically updated index list."
    )
else:
    st.error(f"No verified membership list is available for {selected_basket}. {basket_snapshot['error']}")

is_paper_trading = (exec_env == "📝 Paper Trading")
live_orders_armed = live_order_is_armed()
period, interval, extrema_order = HORIZON_MAP[selected_horizon]

# Main Tabs
main_tab_equity, main_tab_fo, tab_paper_ledger, tab_backtest, tab_chart = st.tabs([
    "📈 Equity Intelligence", 
    "🎯 F&O Intelligence (Derivatives)", 
    "📝 Paper Trading Portfolio",
    "📊 Backtest Engine", 
    "📉 Technical S/R Charts"
])

# =========================================================================
# TAB 1: EQUITY INTELLIGENCE (10-POINT STRATEGY + 1:3 RISK-TO-REWARD ENGINE)
# =========================================================================
with main_tab_equity:
    eq_f1, eq_f2, eq_f3 = st.columns([3, 3, 4])
    with eq_f1:
        setup_filter = st.selectbox("Setup Filter", ["All setups", "BUY_SETUP", "BEARISH_SETUP", "REVERSAL_WATCH", "CONSOLIDATION"])
    with eq_f2:
        trend_filter = st.selectbox("Trend Filter", ["All trends", "BULLISH", "BEARISH"])
    with eq_f3:
        search_query = st.text_input("Search Equity Ticker", placeholder="Search ticker (e.g. RELIANCE, TCS)...")

    if is_paper_trading:
        mode_label = '<span class="mode-badge-paper">📝 ACTIVE: PAPER TRADING (SIMULATION)</span>'
    elif live_orders_armed:
        mode_label = '<span class="mode-badge-live">⚡ LIVE ORDERS ARMED · CONFIRM EACH ORDER</span>'
    else:
        mode_label = '<span class="mode-badge-live">⚡ LIVE MODE · ARM AT BROKER GATEWAY</span>'
    st.markdown(f"""
    <div class="disclaimer-banner" style="display:flex; justify-content:space-between; align-items:center;">
        <span>Technical chart feed may be delayed. Live orders require an active Angel One session, gateway arming, and an order-by-order confirmation.</span>
        {mode_label}
    </div>
    """, unsafe_allow_html=True)

    @st.cache_data(ttl=180)
    def fetch_equity_and_daily_data(symbols: tuple[str, ...], period: str, interval: str):
        symbols = list(symbols)
        if not symbols:
            return symbols, pd.DataFrame(), pd.DataFrame(), "No verified constituent list is available.", ""
        intraday_data, intraday_message = download_public_chart_data(
            symbols, period=period, interval=interval, group_by='ticker', progress=False, threads=True,
        )
        daily_data, daily_message = download_public_chart_data(
            symbols, period="1y", interval="1d", group_by='ticker', progress=False, threads=True,
        )
        return symbols, intraday_data, daily_data, intraday_message, daily_message

    symbols, data, daily_data, equity_intraday_message, equity_daily_message = fetch_equity_and_daily_data(
        tuple(basket_snapshot["symbols"]), period, interval,
    )
    unavailable_equity_symbols = missing_public_chart_symbols(data, symbols)
    if equity_intraday_message:
        st.warning(f"Public intraday chart feed unavailable — {equity_intraday_message} No substitute prices are shown.")
    elif unavailable_equity_symbols:
        st.info(
            "Public chart feed did not return usable data for: "
            + ", ".join(unavailable_equity_symbols)
            + ". Those rows are omitted; no price was substituted."
        )
    if equity_daily_message:
        st.caption("Daily public chart data is unavailable; the macro check, where shown, explicitly uses the intraday 50 EMA fallback.")

    rows = []
    checklists = {}
    current_live_prices = {}

    for sym in symbols:
        try:
            df = data[sym].dropna()
            if len(df) < 25:
                continue

            close = df['Close']
            high = df['High']
            low = df['Low']
            volume = df['Volume']
            open_s = df['Open']

            ema20 = close.ewm(span=20, adjust=False).mean()
            ema50 = close.ewm(span=50, adjust=False).mean()

            delta = close.diff()
            gain = (delta.where(delta > 0, 0)).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss.replace(0, np.nan)
            rsi = 100 - (100 / (1 + rs))

            tr1 = high - low
            tr2 = (high - close.shift(1)).abs()
            tr3 = (low - close.shift(1)).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr_val = float(tr.rolling(14).mean().iloc[-1])

            typical_price = (high + low + close) / 3.0
            cum_vol = volume.cumsum()
            vwap_series = (typical_price * volume).cumsum() / cum_vol.replace(0, np.nan)
            c_vwap = float(vwap_series.iloc[-1]) if pd.notna(vwap_series.iloc[-1]) else float(close.iloc[-1])

            orb_high = float(high.iloc[0])
            orb_low = float(low.iloc[0])

            res = float(high.iloc[-extrema_order:].max())
            sup = float(low.iloc[-extrema_order:].min())

            c_ltp = float(close.iloc[-1])
            c_prev = float(close.iloc[-2])
            c_open = float(open_s.iloc[0])
            chg_pct = ((c_ltp - c_prev) / c_prev) * 100
            c_rsi = float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else 50.0
            c_ema20 = float(ema20.iloc[-1])
            c_ema50 = float(ema50.iloc[-1])
            cur_vol = float(volume.iloc[-1])
            avg_vol_20 = float(volume.iloc[-20:].mean()) if len(volume) >= 20 else cur_vol

            clean_sym = sym.replace(".NS", "").replace(".BO", "")
            current_live_prices[clean_sym] = c_ltp

            # Condition 1: MTF Daily 50 EMA Macro Filter
            d_ok = False
            macro_detail = "Daily 50 EMA unavailable"
            try:
                df_day = daily_data[sym].dropna()
                if len(df_day) >= 50:
                    d_ema = df_day['Close'].ewm(span=50, adjust=False).mean()
                    c_daily_ema50 = float(d_ema.iloc[-1])
                    d_ok = c_ltp > c_daily_ema50
                    macro_detail = f"Daily 50 EMA: ₹{c_daily_ema50:.2f}"
                else:
                    d_ok = c_ltp > c_ema50
                    macro_detail = f"Intraday 50 EMA fallback: ₹{c_ema50:.2f}"
            except Exception:
                d_ok = c_ltp > c_ema50
                macro_detail = f"Intraday 50 EMA fallback: ₹{c_ema50:.2f}"

            # 10-Point Strategy Evaluation
            c1_pass = d_ok
            c2_pass = c_ltp > c_vwap
            c3_pass = c_ltp > orb_high
            c4_pass = c_ltp > c_ema20
            c5_pass = c_ltp > c_ema50
            c6_pass = 50.0 <= c_rsi <= 70.0
            c7_pass = cur_vol > (1.25 * avg_vol_20)
            c8_pass = c_ltp > c_open
            c9_pass = (((c_ltp - sup) / c_ltp) <= 0.02) or (c_ltp >= orb_high)
            c10_pass = ((res - c_ltp) / c_ltp) >= 0.02

            checks = [
                ("1. MTF Macro Filter", c1_pass, macro_detail),
                ("2. VWAP Baseline", c2_pass, f"VWAP: ₹{c_vwap:.2f}"),
                ("3. ORB Breakout", c3_pass, f"ORB High: ₹{orb_high:.2f}"),
                ("4. Short-Term Trend", c4_pass, f"20 EMA: ₹{c_ema20:.2f}"),
                ("5. Intermediate Trend", c5_pass, f"50 EMA: ₹{c_ema50:.2f}"),
                ("6. RSI Momentum Corridor", c6_pass, f"RSI @ {c_rsi:.1f} (50-70)"),
                ("7. Volume Expansion", c7_pass, f"{cur_vol:,.0f} vs {1.25*avg_vol_20:,.0f}"),
                ("8. Positive Session Momentum", c8_pass, f"Open: ₹{c_open:.2f} ({chg_pct:+.2f}%)"),
                ("9. Support/Breakout Validation", c9_pass, "Near Support or Confirmed ORB"),
                ("10. Room to Target", c10_pass, f"Res: ₹{res:.2f} (≥2% clearance)")
            ]

            buy_score = sum(1 for _, met, _ in checks if met)
            sell_score = 10 - buy_score

            if c_rsi >= 74 and chg_pct > 0:
                setup = "REVERSAL_WATCH"
            elif buy_score >= 7:
                setup = "BUY_SETUP"
            elif sell_score >= 7:
                setup = "BEARISH_SETUP"
            else:
                setup = "CONSOLIDATION"

            trend_status = "BULLISH (100)" if c_ltp > c_ema50 else "BEARISH (100)"

            checklists[clean_sym] = {
                "sym": sym,
                "ltp": c_ltp,
                "chg": chg_pct,
                "rsi": c_rsi,
                "vwap": c_vwap,
                "orb_high": orb_high,
                "orb_low": orb_low,
                "atr": atr_val if (pd.notna(atr_val) and atr_val > 0) else (c_ltp * 0.015),
                "support": sup,
                "resistance": res,
                "checks": checks,
                "buy_score": buy_score,
                "sell_score": sell_score,
                "setup": setup
            }

            rows.append({
                "Symbol": clean_sym,
                "Price": round(c_ltp, 2),
                "Chg%": round(chg_pct, 2),
                "Trend": trend_status,
                "VWAP": round(c_vwap, 2),
                "RSI": round(c_rsi, 1),
                "Buy": f"{buy_score}/10",
                "Sell": f"{sell_score}/10",
                "Setup": setup
            })
        except Exception:
            continue

    evaluate_paper_positions(current_live_prices)

    df_results = pd.DataFrame(rows)

    filtered_df = df_results.copy()
    if setup_filter != "All setups":
        filtered_df = filtered_df[filtered_df['Setup'] == setup_filter]
    if trend_filter != "All trends":
        filtered_df = filtered_df[filtered_df['Trend'].str.contains(trend_filter)]
    if search_query:
        filtered_df = filtered_df[filtered_df['Symbol'].str.contains(search_query.upper())]

    col_left, col_right = st.columns([6.6, 3.4])

    with col_left:
        grid = st.dataframe(
            filtered_df,
            width="stretch",
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            column_config={
                "Price": st.column_config.NumberColumn(format="₹%.2f"),
                "Chg%": st.column_config.NumberColumn(format="%+.2f%%"),
                "VWAP": st.column_config.NumberColumn(format="₹%.2f"),
                "RSI": st.column_config.NumberColumn(format="%.1f"),
            },
            height=620
        )

    with col_right:
        active_sym = None
        if grid.selection and grid.selection.rows:
            sel_idx = grid.selection.rows[0]
            if sel_idx < len(filtered_df):
                active_sym = filtered_df.iloc[sel_idx]["Symbol"]

        if not active_sym:
            if not filtered_df.empty:
                active_sym = filtered_df.iloc[0]["Symbol"]
            elif list(checklists.keys()):
                active_sym = list(checklists.keys())[0]

        if active_sym and active_sym in checklists:
            stock = checklists[active_sym]
            chg_c = "#34d399" if stock['chg'] >= 0 else "#fb7185"

            atr_val = stock['atr']
            stop_points = round(float(1.5 * atr_val), 1)
            target_points = round(float(4.5 * atr_val), 1)  # 1:3 RRR

            default_preview_idx = 0 if stock['buy_score'] >= stock['sell_score'] else 1
            preview_action = st.radio(
                "Risk Preview Direction",
                ["BUY", "SELL"],
                index=default_preview_idx,
                horizontal=True,
                key=f"equity_preview_side_{active_sym}",
                help="This changes levels and illustrative P&L only. It does not send an order.",
            )
            inspector_preview = calculate_directional_preview(
                preview_action, stock['ltp'], stop_points, target_points, 1
            )

            items_html = "".join([
                f'<div class="check-item"><span>{"✅" if passed else "❌"} {rule}</span><span class="{"tag-bull" if passed else "tag-bear"}">{detail}</span></div>'
                for rule, passed, detail in stock['checks']
            ])

            st.markdown(f"""
            <div class="inspector-card">
                <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                    <div>
                        <div style="font-size:22px; font-weight:800; color:#fff; font-family:'JetBrains Mono', monospace;">{active_sym}</div>
                        <div style="font-size:24px; font-weight:800; color:#fff; margin: 4px 0;">
                            ₹{stock['ltp']:.2f} <span style="font-size:13px; color:{chg_c}">({stock['chg']:+.2f}%)</span>
                        </div>
                    </div>
                    <span style="background:rgba(0, 242, 254, 0.12); border:1px solid rgba(0, 242, 254, 0.3); color:#00f2fe; font-size:11px; padding:3px 8px; border-radius:6px; font-weight:700;">
                        {stock['setup']}
                    </span>
                </div>
                <div style="display:flex; justify-content:space-between; font-size:12px; margin: 10px 0; padding: 8px 12px; background:rgba(30, 41, 59, 0.6); border-radius:6px;">
                    <div>VWAP: <strong style="color:#00f2fe">₹{stock['vwap']:.2f}</strong></div>
                    <div>ORB HIGH: <strong style="color:#fbbf24">₹{stock['orb_high']:.2f}</strong></div>
                    <div>ATR(14): <strong>₹{atr_val:.2f}</strong></div>
                </div>
                <div style="display:flex; justify-content:space-between; font-size:12px; margin-bottom: 12px; padding: 8px 12px; background:rgba(15, 23, 42, 0.7); border:1px solid rgba(255,255,255,0.06); border-radius:6px;">
                    <div>{preview_action} SL PRICE: <strong style="color:#fb7185">₹{inspector_preview['stop_price']:.2f}</strong></div>
                    <div>{preview_action} TARGET (1:3): <strong style="color:#34d399">₹{inspector_preview['target_price']:.2f}</strong></div>
                </div>
                <div style="font-size:11px; font-weight:700; color:#94a3b8; text-transform:uppercase; margin-bottom:6px; letter-spacing:0.5px;">
                    SCORE: <span style="color:#34d399;">{stock['buy_score']}/10 BUY</span> &nbsp;·&nbsp; <span style="color:#fb7185;">{stock['sell_score']}/10 SELL</span>
                </div>
                {items_html}
            </div>
            """, unsafe_allow_html=True)

            st.markdown("""
            <div style="margin-top:12px; padding:10px 14px; background:linear-gradient(90deg, rgba(15,23,42,0.8), rgba(30,27,75,0.8)); border:1px solid rgba(255,255,255,0.08); border-radius:6px;">
                <span style="font-size:13px; font-weight:700; color:#fff;">💰 Amount & P&L Sizing Engine</span>
            </div>
            """, unsafe_allow_html=True)

            order_mode = st.radio("Order Type", ["Bracket (ROBO Auto-Exit)", "Regular Market"], horizontal=True, label_visibility="collapsed", key=f"mode_{active_sym}")
            
            calc_c1, calc_c2 = st.columns(2)
            with calc_c1:
                trade_qty = st.number_input("Quantity", min_value=1, value=10, step=1, key=f"qty_{active_sym}")
            with calc_c2:
                trade_limit = st.number_input("Entry Price (₹)", value=float(round(stock['ltp'], 2)), step=0.5, key=f"limit_{active_sym}")

            if order_mode == "Bracket (ROBO Auto-Exit)":
                sl_c, tp_c, trail_c = st.columns(3)
                with sl_c:
                    in_sl_pts = st.number_input("SL Points (₹)", min_value=0.5, value=float(stop_points), step=0.5, key=f"sl_{active_sym}")
                with tp_c:
                    in_tp_pts = st.number_input("Target Points (₹)", min_value=1.0, value=float(target_points), step=0.5, key=f"tp_{active_sym}")
                with trail_c:
                    in_trail = st.number_input("Trail (₹)", min_value=0.0, value=1.0, step=0.5, key=f"trail_{active_sym}")

                equity_preview = calculate_directional_preview(
                    preview_action, trade_limit, in_sl_pts, in_tp_pts, trade_qty
                )
                total_capital_required = equity_preview["gross_notional"]
                expected_profit = equity_preview["target_pnl"]
                expected_loss = abs(equity_preview["loss_pnl"])
                reward_risk_ratio = round(expected_profit / expected_loss, 2) if expected_loss > 0 else 0.0

                st.markdown(f"""
                <div class="calc-box">
                    <div class="calc-row">
                        <span style="color:#94a3b8;">{preview_action} entry notional:</span>
                        <strong style="color:#ffffff; font-size:14px;">₹{total_capital_required:,.2f}</strong>
                    </div>
                    <div class="calc-row">
                        <span style="color:#94a3b8;">Entry / stop / target:</span>
                        <strong style="color:#ffffff;">₹{equity_preview['entry_price']:.2f} / <span style="color:#fb7185">₹{equity_preview['stop_price']:.2f}</span> / <span style="color:#34d399">₹{equity_preview['target_price']:.2f}</span></strong>
                    </div>
                    <div class="calc-row">
                        <span style="color:#94a3b8;">Illustrative P&L at target:</span>
                        <strong style="color:#34d399; font-size:14px;">+₹{expected_profit:,.2f}</strong>
                    </div>
                    <div class="calc-row">
                        <span style="color:#94a3b8;">Illustrative P&L at stop:</span>
                        <strong style="color:#fb7185; font-size:14px;">-₹{expected_loss:,.2f}</strong>
                    </div>
                    <div class="calc-row" style="border-top:1px solid rgba(255,255,255,0.06); margin-top:4px; padding-top:4px;">
                        <span style="color:#94a3b8;">Net Risk-to-Reward Ratio:</span>
                        <strong style="color:#00f2fe;">1 : {reward_risk_ratio}</strong>
                    </div>
                </div>
                """, unsafe_allow_html=True)

                live_buy_confirm = live_sell_confirm = False
                if not is_paper_trading:
                    if live_orders_armed:
                        st.markdown("<div class='live-ready-card'>⚡ <strong>LIVE ORDER MODE ARMED</strong> — confirm the exact side below. A successful response means submitted, not filled.</div>", unsafe_allow_html=True)
                    else:
                        st.markdown("<div class='live-arm-card'>🔒 <strong>LIVE ORDER MODE NOT ARMED</strong> — connect Angel One and enable the live-order acknowledgement in the gateway below.</div>", unsafe_allow_html=True)
                    confirm_c1, confirm_c2 = st.columns(2)
                    with confirm_c1:
                        live_buy_confirm = st.checkbox(
                            f"I understand: submit LIVE BUY {active_sym} ({trade_qty} qty) LIMIT ₹{trade_limit:.2f}; SL offset ₹{in_sl_pts:.2f}; target offset ₹{in_tp_pts:.2f}",
                            key=f"confirm_robo_buy_{active_sym}", disabled=not live_orders_armed,
                        )
                    with confirm_c2:
                        live_sell_confirm = st.checkbox(
                            f"I understand: submit LIVE SELL {active_sym} ({trade_qty} qty) LIMIT ₹{trade_limit:.2f}; SL offset ₹{in_sl_pts:.2f}; target offset ₹{in_tp_pts:.2f}",
                            key=f"confirm_robo_sell_{active_sym}", disabled=not live_orders_armed,
                        )
                    st.caption("ROBO orders are submitted as LIMIT + BO using the displayed stop/target offsets. Trailing is not sent until broker-specific trailing behavior is verified.")

                b_col1, b_col2 = st.columns(2)
                buy_disabled = not is_paper_trading and not (live_orders_armed and live_buy_confirm)
                sell_disabled = not is_paper_trading and not (live_orders_armed and live_sell_confirm)
                btn_prefix = "📝 PAPER" if is_paper_trading else "⚡ LIVE"
                with b_col1:
                    btn_robo_buy = st.button(f"🟢 {btn_prefix} BUY ROBO {active_sym}", width="stretch", key=f"btn_buy_{active_sym}", disabled=buy_disabled)
                with b_col2:
                    btn_robo_sell = st.button(f"🔴 {btn_prefix} SELL ROBO {active_sym}", width="stretch", key=f"btn_sell_{active_sym}", disabled=sell_disabled)

                if btn_robo_buy or btn_robo_sell:
                    action_type = "BUY" if btn_robo_buy else "SELL"
                    if is_paper_trading:
                        ok, msg = place_paper_order(
                            symbol=active_sym,
                            action=action_type,
                            qty=trade_qty,
                            entry_price=trade_limit,
                            sl_pts=in_sl_pts,
                            tp_pts=in_tp_pts,
                            trail_pts=in_trail,
                        )
                        if ok:
                            st.success(f"✅ {msg}")
                        else:
                            st.error(f"❌ {msg}")
                    else:
                        with st.spinner("Resolving broker contract and submitting your confirmed ROBO order..."):
                            success, message, details, broker_ltp = submit_live_equity_from_ui(
                                st.session_state.get("smart_api"), active_sym, stock["sym"], trade_qty,
                                action_type, "ROBO", trade_limit, in_sl_pts, in_tp_pts, "INTRADAY",
                            )
                        if success:
                            order_id = details.get("order_id") if isinstance(details, dict) else ""
                            st.session_state["last_live_submission"] = {
                                "symbol": active_sym, "action": action_type, "kind": "ROBO",
                                "order_id": order_id, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            }
                            st.success(f"✅ LIVE order submitted to Angel One{f' · Order ID: {order_id}' if order_id else ''}. Check the broker order book for OPEN, COMPLETE, or REJECTED status.")
                            if broker_ltp and broker_ltp.get("ok"):
                                st.caption(f"Broker LTP verified at submission: ₹{broker_ltp['ltp']:.2f}")
                        else:
                            st.error(f"Live order was not submitted: {message}")
            else:
                reg_capital = trade_qty * trade_limit
                st.markdown(f"""
                <div class="calc-box">
                    <div class="calc-row">
                        <span style="color:#94a3b8;">Total Amount to Invest:</span>
                        <strong style="color:#ffffff; font-size:14px;">₹{reg_capital:,.2f}</strong>
                    </div>
                </div>
                """, unsafe_allow_html=True)

                reg_c1, reg_c2 = st.columns(2)
                with reg_c1:
                    prod = st.selectbox("Product", ["INTRADAY", "DELIVERY"], key=f"prod_{active_sym}")
                with reg_c2:
                    st.caption("Market orders use the broker's execution price; Entry Price above is preview-only.")

                live_market_buy_confirm = live_market_sell_confirm = False
                if not is_paper_trading:
                    if live_orders_armed:
                        st.markdown("<div class='live-ready-card'>⚡ <strong>LIVE MARKET MODE ARMED</strong> — market orders may be converted by the broker to market-price-protection limit orders.</div>", unsafe_allow_html=True)
                    else:
                        st.markdown("<div class='live-arm-card'>🔒 <strong>LIVE MARKET MODE NOT ARMED</strong> — enable it in the broker gateway below.</div>", unsafe_allow_html=True)
                    market_confirm_c1, market_confirm_c2 = st.columns(2)
                    with market_confirm_c1:
                        live_market_buy_confirm = st.checkbox(
                            f"I understand: submit LIVE MARKET BUY {active_sym} ({trade_qty} qty)",
                            key=f"confirm_market_buy_{active_sym}", disabled=not live_orders_armed,
                        )
                    with market_confirm_c2:
                        live_market_sell_confirm = st.checkbox(
                            f"I understand: submit LIVE MARKET SELL {active_sym} ({trade_qty} qty)",
                            key=f"confirm_market_sell_{active_sym}", disabled=not live_orders_armed,
                        )

                market_b1, market_b2 = st.columns(2)
                market_prefix = "📝 PAPER" if is_paper_trading else "⚡ LIVE"
                with market_b1:
                    btn_reg_buy = st.button(
                        f"🟢 {market_prefix} BUY MARKET {active_sym}", width="stretch", key=f"mkt_buy_{active_sym}",
                        disabled=not is_paper_trading and not (live_orders_armed and live_market_buy_confirm),
                    )
                with market_b2:
                    btn_reg_sell = st.button(
                        f"🔴 {market_prefix} SELL MARKET {active_sym}", width="stretch", key=f"mkt_sell_{active_sym}",
                        disabled=not is_paper_trading and not (live_orders_armed and live_market_sell_confirm),
                    )

                if btn_reg_buy or btn_reg_sell:
                    action_type = "BUY" if btn_reg_buy else "SELL"
                    if is_paper_trading:
                        ok, msg = place_paper_order(active_sym, action_type, trade_qty, trade_limit, stop_points, target_points)
                        if ok:
                            st.success(f"✅ {msg}")
                        else:
                            st.error(f"❌ {msg}")
                    else:
                        with st.spinner("Resolving broker contract and submitting your confirmed market order..."):
                            success, message, details, broker_ltp = submit_live_equity_from_ui(
                                st.session_state.get("smart_api"), active_sym, stock["sym"], trade_qty,
                                action_type, "NORMAL", trade_limit, stop_points, target_points, prod,
                            )
                        if success:
                            order_id = details.get("order_id") if isinstance(details, dict) else ""
                            st.session_state["last_live_submission"] = {
                                "symbol": active_sym, "action": action_type, "kind": "MARKET",
                                "order_id": order_id, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            }
                            st.success(f"✅ LIVE market order submitted to Angel One{f' · Order ID: {order_id}' if order_id else ''}. Check the broker order book for fill status.")
                            if broker_ltp and broker_ltp.get("ok"):
                                st.caption(f"Broker LTP verified at submission: ₹{broker_ltp['ltp']:.2f}")
                        else:
                            st.error(f"Live order was not submitted: {message}")

# =========================================================================
# TAB 2: F&O INTELLIGENCE (10-POINT STRATEGY & AUTOMATED STRIKE SELECTION)
# =========================================================================
with main_tab_fo:
    st.markdown("""
    <div class="disclaimer-banner">
        Derivatives technical screen. Option prices, lot size, and Greeks appear only after an authenticated Angel One contract lookup; no PCR, OI, IV, or Delta is inferred here.
    </div>
    """, unsafe_allow_html=True)

    fo_tickers = list(FO_UNIVERSE.values())
    
    @st.cache_data(ttl=180)
    def fetch_fo_market_data():
        # Fetch Intraday and Daily for 10-Point Evaluation on Derivatives Universe
        fo_intra, intraday_message = download_public_chart_data(
            fo_tickers, period=period, interval=interval, group_by='ticker', progress=False, threads=True,
        )
        fo_daily, daily_message = download_public_chart_data(
            fo_tickers, period="1y", interval="1d", group_by='ticker', progress=False, threads=True,
        )
        return fo_intra, fo_daily, intraday_message, daily_message

    fo_intra, fo_daily, fo_intraday_message, fo_daily_message = fetch_fo_market_data()
    unavailable_fo_symbols = missing_public_chart_symbols(fo_intra, fo_tickers)
    if fo_intraday_message:
        st.warning(f"Public underlying intraday chart feed unavailable — {fo_intraday_message} No substitute prices are shown.")
    elif unavailable_fo_symbols:
        st.info(
            "Public underlying chart feed did not return usable data for: "
            + ", ".join(unavailable_fo_symbols)
            + ". Those rows are omitted; option pricing still requires Angel One."
        )
    if fo_daily_message:
        st.caption("Daily public chart data is unavailable; any macro check shown explicitly uses the intraday 50 EMA fallback.")

    fo_rows = []
    fo_checklists = {}

    for name, ticker in FO_UNIVERSE.items():
        try:
            f_df = fo_intra[ticker].dropna()
            if len(f_df) < 20:
                continue

            f_close = f_df['Close']
            f_high = f_df['High']
            f_low = f_df['Low']
            f_vol = f_df['Volume']
            f_open = f_df['Open']

            c_ltp = float(f_close.iloc[-1])
            c_prev = float(f_close.iloc[-2])
            c_open = float(f_open.iloc[0])
            f_chg = ((c_ltp - c_prev) / c_prev) * 100

            # Intraday Indicators
            f_ema20 = float(f_close.ewm(span=20, adjust=False).mean().iloc[-1])
            f_ema50 = float(f_close.ewm(span=50, adjust=False).mean().iloc[-1])

            # RSI
            f_delta = f_close.diff()
            f_gain = (f_delta.where(f_delta > 0, 0)).rolling(14).mean()
            f_loss = (-f_delta.where(f_delta < 0, 0)).rolling(14).mean()
            f_rs = f_gain / f_loss.replace(0, np.nan)
            f_rsi = float((100 - (100 / (1 + f_rs))).iloc[-1]) if pd.notna(f_rs.iloc[-1]) else 50.0

            # Intraday VWAP
            f_typical = (f_high + f_low + f_close) / 3.0
            f_cum_vol = f_vol.cumsum()
            f_vwap_series = (f_typical * f_vol).cumsum() / f_cum_vol.replace(0, np.nan)
            c_vwap = float(f_vwap_series.iloc[-1]) if pd.notna(f_vwap_series.iloc[-1]) else c_ltp

            # ORB Range
            orb_high = float(f_high.iloc[0])
            orb_low = float(f_low.iloc[0])

            # Dynamic Support & Resistance
            f_res = float(f_high.iloc[-extrema_order:].max())
            f_sup = float(f_low.iloc[-extrema_order:].min())

            cur_vol = float(f_vol.iloc[-1])
            avg_vol_20 = float(f_vol.iloc[-20:].mean()) if len(f_vol) >= 20 else cur_vol

            # MTF Daily 50 EMA Macro Alignment
            d_ok = False
            macro_detail = "Daily 50 EMA unavailable"
            try:
                f_d_df = fo_daily[ticker].dropna()
                if len(f_d_df) >= 50:
                    d_ema = f_d_df['Close'].ewm(span=50, adjust=False).mean()
                    f_daily_ema50 = float(d_ema.iloc[-1])
                    d_ok = c_ltp > f_daily_ema50
                    macro_detail = f"Daily 50 EMA: ₹{f_daily_ema50:.2f}"
                else:
                    d_ok = c_ltp > f_ema50
                    macro_detail = f"Intraday 50 EMA fallback: ₹{f_ema50:.2f}"
            except Exception:
                d_ok = c_ltp > f_ema50
                macro_detail = f"Intraday 50 EMA fallback: ₹{f_ema50:.2f}"

            # 10-Point Strategy Evaluation
            c1_pass = d_ok
            c2_pass = c_ltp > c_vwap
            c3_pass = c_ltp > orb_high
            c4_pass = c_ltp > f_ema20
            c5_pass = c_ltp > f_ema50
            c6_pass = 50.0 <= f_rsi <= 70.0
            c7_pass = cur_vol > (1.25 * avg_vol_20)
            c8_pass = c_ltp > c_open
            c9_pass = (((c_ltp - f_sup) / c_ltp) <= 0.02) or (c_ltp >= orb_high)
            c10_pass = ((f_res - c_ltp) / c_ltp) >= 0.02

            checks = [
                ("1. MTF Macro Filter", c1_pass, macro_detail),
                ("2. VWAP Baseline", c2_pass, f"VWAP: ₹{c_vwap:.2f}"),
                ("3. ORB Breakout", c3_pass, f"ORB High: ₹{orb_high:.2f}"),
                ("4. Short-Term Trend", c4_pass, f"20 EMA: ₹{f_ema20:.2f}"),
                ("5. Intermediate Trend", c5_pass, f"50 EMA: ₹{f_ema50:.2f}"),
                ("6. RSI Momentum Corridor", c6_pass, f"RSI @ {f_rsi:.1f} (50-70)"),
                ("7. Volume Expansion", c7_pass, f"{cur_vol:,.0f} vs {1.25*avg_vol_20:,.0f}"),
                ("8. Positive Session Momentum", c8_pass, f"Open: ₹{c_open:.2f} ({f_chg:+.2f}%)"),
                ("9. Support/Breakout Validation", c9_pass, "Near Support or Confirmed ORB"),
                ("10. Room to Target", c10_pass, f"Res: ₹{f_res:.2f} (≥2% clearance)")
            ]

            buy_score = sum(1 for _, met, _ in checks if met)
            sell_score = 10 - buy_score

            # This is a technical bias on the underlying, not an option-chain metric.
            # Actual expiry, strike, lot size, LTP and Greeks are loaded from Angel One below.
            if buy_score >= 7:
                recommended_opt = "CALL (CE)"
                rec_rationale = "Technical bullish bias. Choose a broker-listed CE below; no Delta or payoff has been assumed."
                bias_tag = "STRONG_BULLISH"
            elif sell_score >= 7:
                recommended_opt = "PUT (PE)"
                rec_rationale = "Technical bearish bias. Choose a broker-listed PE below; no Delta or payoff has been assumed."
                bias_tag = "STRONG_BEARISH"
            else:
                recommended_opt = "CALL (CE)" if buy_score >= sell_score else "PUT (PE)"
                rec_rationale = "Mixed technical bias. Review the actual broker-listed option chain before making any decision."
                bias_tag = "CONSOLIDATION"

            fo_checklists[name] = {
                "ltp": c_ltp,
                "chg": f_chg,
                "recommended_opt": recommended_opt,
                "rec_rationale": rec_rationale,
                "bias_tag": bias_tag,
                "vwap": c_vwap,
                "orb_high": orb_high,
                "checks": checks,
                "buy_score": buy_score,
                "sell_score": sell_score
            }

            fo_rows.append({
                "Contract": name,
                "Underlying LTP": round(c_ltp, 2),
                "Chg%": round(f_chg, 2),
                "Score": f"{buy_score}/10 Buy" if buy_score >= sell_score else f"{sell_score}/10 Sell",
                "Technical Bias": recommended_opt.split()[0],
                "Data note": "Underlying chart feed may be delayed"
            })
        except Exception:
            continue

    df_fo = pd.DataFrame(fo_rows)

    fo_left, fo_right = st.columns([6.4, 3.6])

    with fo_left:
        fo_grid = st.dataframe(
            df_fo,
            width="stretch",
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            column_config={
                "Underlying LTP": st.column_config.NumberColumn(format="₹%.2f"),
                "Chg%": st.column_config.NumberColumn(format="%+.2f%%"),
            },
            height=620
        )

    with fo_right:
        active_fo = None
        if fo_grid.selection and fo_grid.selection.rows:
            fo_sel_idx = fo_grid.selection.rows[0]
            if fo_sel_idx < len(df_fo):
                active_fo = df_fo.iloc[fo_sel_idx]["Contract"]

        if not active_fo:
            active_fo = df_fo.iloc[0]["Contract"] if not df_fo.empty else "NIFTY"

        if active_fo in fo_checklists:
            fo_item = fo_checklists[active_fo]
            fo_chg_c = "#34d399" if fo_item['chg'] >= 0 else "#fb7185"

            fo_items_html = "".join([
                f'<div class="check-item"><span>{"✅" if passed else "❌"} {rule}</span><span class="{"tag-bull" if passed else "tag-bear"}">{detail}</span></div>'
                for rule, passed, detail in fo_item['checks']
            ])

            st.markdown(f"""
            <div class="inspector-card">
                <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                    <div>
                        <div style="font-size:22px; font-weight:800; color:#fff; font-family:'JetBrains Mono', monospace;">{active_fo} F&O</div>
                        <div style="font-size:24px; font-weight:800; color:#fff; margin: 4px 0;">
                            ₹{fo_item['ltp']:.2f} <span style="font-size:13px; color:{fo_chg_c}">({fo_item['chg']:+.2f}%)</span>
                        </div>
                    </div>
                    <span style="background:rgba(0, 242, 254, 0.12); border:1px solid rgba(0, 242, 254, 0.3); color:#00f2fe; font-size:11px; padding:4px 8px; border-radius:6px; font-weight:700;">
                        {fo_item['bias_tag']}
                    </span>
                </div>
                <div style="margin: 8px 0; padding: 8px 12px; background:rgba(30, 41, 59, 0.7); border-left: 3px solid #00f2fe; border-radius:4px; font-size:11px; color:#cbd5e1;">
                    🎯 <strong>System Recommendation:</strong><br>{fo_item['rec_rationale']}
                </div>
                <div style="display:flex; justify-content:space-between; font-size:12px; margin-bottom: 8px; padding: 6px 12px; background:rgba(15, 23, 42, 0.6); border-radius:6px;">
                    <div>VWAP: <strong style="color:#00f2fe;">₹{fo_item['vwap']:.2f}</strong></div>
                    <div>ORB HIGH: <strong style="color:#fbbf24;">₹{fo_item['orb_high']:.2f}</strong></div>
                    <div>Option data: <strong>Broker required</strong></div>
                </div>
                <div style="font-size:11px; font-weight:700; color:#94a3b8; text-transform:uppercase; margin-bottom:6px; letter-spacing:0.5px;">
                    SCORE: <span style="color:#34d399;">{fo_item['buy_score']}/10 BUY</span> &nbsp;·&nbsp; <span style="color:#fb7185;">{fo_item['sell_score']}/10 SELL</span>
                </div>
                {fo_items_html}
            </div>
            """, unsafe_allow_html=True)

            # Real option contract selector. Metadata comes from Angel One's published
            # instrument master; LTP/Greeks/candles require the authenticated session.
            st.markdown("""
            <div class="bottom-card" style="margin-top:12px; padding:12px;">
                <div style="font-size:13px; font-weight:700; color:#fff; margin-bottom:5px;">🔗 Angel One Option Contract & Risk Preview</div>
                <div style="font-size:11px; color:#94a3b8;">No manual CSV or estimated premium. Contract metadata is loaded from Angel One; pricing is an authenticated broker snapshot.</div>
            </div>
            """, unsafe_allow_html=True)

            master_rows, master_error = load_angel_option_master()
            option_contracts = extract_option_contracts(master_rows, active_fo) if master_rows else []

            if master_error:
                st.error(master_error)
            elif not option_contracts:
                st.warning(f"No Angel One NFO option contracts are currently available for {active_fo}. No contract details or estimates are shown.")
            else:
                expiry_values = []
                expiry_labels = {}
                for contract in option_contracts:
                    expiry = contract["expiry_raw"]
                    if expiry not in expiry_labels:
                        expiry_values.append(expiry)
                        expiry_labels[expiry] = contract["expiry_label"]
                selected_expiry = st.selectbox(
                    "Expiry (Angel One master)",
                    expiry_values,
                    format_func=lambda value: expiry_labels[value],
                    key=f"option_expiry_{active_fo}",
                )
                expiry_contracts = [row for row in option_contracts if row["expiry_raw"] == selected_expiry]
                available_sides = sorted({row["side"] for row in expiry_contracts})
                preferred_side = "CE" if fo_item["recommended_opt"] == "CALL (CE)" else "PE"
                preferred_index = available_sides.index(preferred_side) if preferred_side in available_sides else 0

                opt_c1, opt_c2 = st.columns(2)
                with opt_c1:
                    option_side = st.selectbox(
                        "Option side", available_sides, index=preferred_index,
                        format_func=lambda side: "CALL (CE)" if side == "CE" else "PUT (PE)",
                        key=f"option_side_{active_fo}_{selected_expiry}",
                    )
                side_contracts = [row for row in expiry_contracts if row["side"] == option_side]
                strikes = sorted({row["strike"] for row in side_contracts})
                nearest_strike = min(strikes, key=lambda strike: abs(strike - fo_item["ltp"]))
                with opt_c2:
                    selected_strike = st.selectbox(
                        "Strike (Angel One master)", strikes,
                        index=strikes.index(nearest_strike),
                        format_func=lambda strike: f"₹{strike:,.2f}",
                        key=f"option_strike_{active_fo}_{selected_expiry}_{option_side}",
                    )
                selected_contract = next(
                    row for row in side_contracts if row["strike"] == selected_strike
                )

                broker_client = st.session_state.get("smart_api")
                option_snapshot = get_option_snapshot_for_ui(broker_client, selected_contract)
                greeks_snapshot = get_option_greeks_for_ui(broker_client, active_fo, selected_expiry)
                if option_snapshot.get("ok"):
                    st.session_state.setdefault("broker_option_prices", {})[selected_contract["symbol"]] = option_snapshot

                detail_columns = st.columns(4)
                detail_columns[0].metric("Selected contract", selected_contract["symbol"])
                detail_columns[1].metric("Lot size", str(selected_contract["lot_size"]))
                detail_columns[2].metric("Tick size", "—" if selected_contract["tick_size"] is None else f"₹{selected_contract['tick_size']:.2f}")
                detail_columns[3].metric("Broker LTP", f"₹{option_snapshot['ltp']:.2f}" if option_snapshot.get("ok") else "Unavailable")

                if option_snapshot.get("ok"):
                    st.caption(broker_snapshot_caption(option_snapshot))
                elif option_snapshot.get("state") == "disconnected":
                    st.warning("Connect Angel One below to retrieve the selected contract's real LTP, Greeks, and chart. No stale or estimated premium is used.")
                else:
                    st.error(f"Selected-contract price unavailable: {option_snapshot.get('message', 'Unknown broker error.')}")

                if greeks_snapshot.get("ok"):
                    selected_greeks = selected_contract_greeks(greeks_snapshot["rows"], selected_contract)
                    if selected_greeks:
                        greek_text = " · ".join(f"{label}: {value}" for label, value in selected_greeks.items())
                        st.caption(f"Broker-reported Greeks/OI: {greek_text} · {broker_snapshot_caption(greeks_snapshot)}")
                    else:
                        st.caption("Angel One returned Greek data for this expiry, but no row could be safely matched to the selected contract.")
                elif broker_client is not None:
                    st.caption(f"Broker Greeks unavailable: {greeks_snapshot.get('message', 'Unknown response.')}")

                if option_snapshot.get("ok"):
                    candle_snapshot = get_option_candles_for_ui(broker_client, selected_contract)
                    if candle_snapshot.get("ok"):
                        candle_df = pd.DataFrame(candle_snapshot["candles"], columns=["time", "Open", "High", "Low", "Close", "Volume"])
                        candle_df["time"] = pd.to_datetime(candle_df["time"], errors="coerce")
                        candle_df = candle_df.dropna(subset=["time", "Open", "High", "Low", "Close"])
                        if not candle_df.empty:
                            option_fig = go.Figure(go.Candlestick(
                                x=candle_df["time"], open=candle_df["Open"], high=candle_df["High"],
                                low=candle_df["Low"], close=candle_df["Close"], name=selected_contract["symbol"],
                                increasing_line_color="#34d399", decreasing_line_color="#fb7185",
                            ))
                            option_fig = apply_chart_style(option_fig, height=320)
                            option_fig.update_xaxes(rangeslider_visible=False)
                            st.plotly_chart(option_fig, width="stretch", key=f"option_chart_{selected_contract['token']}")
                            st.caption(broker_snapshot_caption(candle_snapshot))
                        else:
                            st.caption("Broker returned an unreadable option-candle payload; no chart is shown.")
                    else:
                        st.caption(f"Option chart unavailable: {candle_snapshot.get('message', 'Unknown broker response.')}")

                    opt_q1, opt_q2 = st.columns(2)
                    with opt_q1:
                        lots = st.number_input("Number of lots", min_value=1, value=1, step=1, key=f"option_lots_{selected_contract['token']}")
                    with opt_q2:
                        option_action = st.radio("Risk preview side", ["BUY", "SELL"], horizontal=True, key=f"option_preview_side_{selected_contract['token']}")

                    minimum_step = selected_contract["tick_size"] or 0.05
                    default_stop = max(minimum_step, round(option_snapshot["ltp"] * 0.15, 2))
                    opt_sl_pts = st.number_input(
                        "Risk points (₹)", min_value=float(minimum_step), value=float(default_stop),
                        step=float(minimum_step), key=f"option_sl_{selected_contract['token']}",
                    )
                    opt_tp_pts = round(opt_sl_pts * 3, 2)
                    total_contracts = int(lots) * selected_contract["lot_size"]
                    option_preview = calculate_directional_preview(
                        option_action, option_snapshot["ltp"], opt_sl_pts, opt_tp_pts, total_contracts
                    )
                    premium_label = "Premium outlay (before charges)" if option_action == "BUY" else "Gross premium received (margin not calculated)"
                    st.markdown(f"""
                    <div class="calc-box">
                        <div class="calc-row"><span style="color:#94a3b8;">Quantity ({int(lots)} lot × {selected_contract['lot_size']}):</span><strong style="color:#ffffff;">{total_contracts} Qty</strong></div>
                        <div class="calc-row"><span style="color:#94a3b8;">{premium_label}:</span><strong style="color:#00f2fe; font-size:14px;">₹{option_preview['gross_notional']:,.2f}</strong></div>
                        <div class="calc-row"><span style="color:#94a3b8;">{option_action} entry / stop / target:</span><strong style="color:#ffffff;">₹{option_preview['entry_price']:.2f} / <span style="color:#fb7185">₹{option_preview['stop_price']:.2f}</span> / <span style="color:#34d399">₹{option_preview['target_price']:.2f}</span></strong></div>
                        <div class="calc-row"><span style="color:#94a3b8;">Illustrative P&L at target:</span><strong style="color:#34d399; font-size:14px;">+₹{option_preview['target_pnl']:,.2f}</strong></div>
                        <div class="calc-row"><span style="color:#94a3b8;">Illustrative P&L at stop:</span><strong style="color:#fb7185; font-size:14px;">₹{option_preview['loss_pnl']:,.2f}</strong></div>
                        <div class="calc-row" style="border-top:1px solid rgba(255,255,255,0.06); margin-top:4px; padding-top:4px;"><span style="color:#94a3b8;">Illustrative risk-to-reward:</span><strong style="color:#00f2fe;">1 : 3.00</strong></div>
                    </div>
                    """, unsafe_allow_html=True)
                    st.caption("Illustrative P&L excludes brokerage, taxes, slippage, assignment risk, and broker-specific margin requirements.")

                    if is_paper_trading:
                        paper_label = f"📝 Log {option_action} paper position"
                        btn_option_paper = st.button(
                            paper_label, width="stretch", key=f"paper_option_{selected_contract['token']}_{option_action}",
                        )
                        if btn_option_paper:
                            ok, msg = place_paper_order(
                                selected_contract["symbol"], option_action, total_contracts, option_snapshot["ltp"], opt_sl_pts, opt_tp_pts
                            )
                            if ok:
                                st.success(f"✅ {msg}")
                            else:
                                st.error(f"❌ {msg}")
                    else:
                        if live_orders_armed:
                            st.markdown("<div class='live-ready-card'>⚡ <strong>LIVE F&O ROBO MODE ARMED</strong> — Angel One must still accept BO for your account and selected contract.</div>", unsafe_allow_html=True)
                        else:
                            st.markdown("<div class='live-arm-card'>🔒 <strong>LIVE F&O MODE NOT ARMED</strong> — enable the live-order acknowledgement in the broker gateway below.</div>", unsafe_allow_html=True)
                        option_confirm = st.checkbox(
                            f"I understand: submit LIVE {option_action} {selected_contract['symbol']} ({total_contracts} qty) at broker snapshot ₹{option_snapshot['ltp']:.2f}; SL offset ₹{opt_sl_pts:.2f}; target offset ₹{opt_tp_pts:.2f}",
                            key=f"confirm_option_{selected_contract['token']}_{option_action}", disabled=not live_orders_armed,
                        )
                        option_button_key = f"live_option_{option_action.lower()}_{selected_contract['token']}"
                        btn_option_live = st.button(
                            f"{'🟢' if option_action == 'BUY' else '🔴'} ⚡ LIVE {option_action} ROBO {selected_contract['symbol']}",
                            width="stretch", key=option_button_key,
                            disabled=not (live_orders_armed and option_confirm),
                        )
                        if btn_option_live:
                            with st.spinner("Refreshing broker LTP and submitting your confirmed F&O ROBO order..."):
                                fresh_option_snapshot = fetch_selected_option_snapshot(broker_client, selected_contract)
                                if fresh_option_snapshot.get("ok"):
                                    success, message, details = place_bracket_robo_order(
                                        broker_client, selected_contract, total_contracts, fresh_option_snapshot["ltp"],
                                        opt_sl_pts, opt_tp_pts, option_action,
                                    )
                                else:
                                    success, message, details = False, f"Fresh broker LTP is required before submission: {fresh_option_snapshot.get('message', 'unavailable')}", None
                            if success:
                                order_id = details.get("order_id") if isinstance(details, dict) else ""
                                st.session_state["last_live_submission"] = {
                                    "symbol": selected_contract["symbol"], "action": option_action, "kind": "F&O ROBO",
                                    "order_id": order_id, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                }
                                st.success(f"✅ LIVE F&O order submitted to Angel One{f' · Order ID: {order_id}' if order_id else ''}. Check the broker order book for final status.")
                            else:
                                st.error(f"Live F&O order was not submitted: {message}")

# =========================================================================
# TAB 3: PAPER TRADING PORTFOLIO & LEDGER DESK
# =========================================================================
with tab_paper_ledger:
    pdata = st.session_state["paper_data"]
    
    total_invested = sum(p["qty"] * p["entry_price"] for p in pdata["positions"])
    cur_equity = pdata["cash"] + total_invested
    total_realized_pnl = sum(t["pnl"] for t in pdata["closed_trades"])
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Virtual Cash Balance", f"₹{pdata['cash']:,.2f}")
    c2.metric("Capital in Active Trades", f"₹{total_invested:,.2f}")
    c3.metric("Net Virtual Portfolio", f"₹{cur_equity:,.2f}")
    c4.metric("Total Realized P&L", f"{total_realized_pnl:+,.2f}", delta=f"{((cur_equity-500000)/500000)*100:+.2f}%")

    st.write("")
    st.markdown("<h4 style='color:#fff; margin-bottom:8px;'>Open Paper Positions (Available-Feed Monitoring)</h4>", unsafe_allow_html=True)
    
    if pdata["positions"]:
        open_rows = []
        for p in pdata["positions"]:
            sym = p["symbol"]
            cur_price = current_live_prices.get(sym)
            if cur_price is None:
                broker_snapshot = st.session_state.get("broker_option_prices", {}).get(sym, {})
                snapshot_age = time.time() - broker_snapshot.get("fetched_at", 0)
                if broker_snapshot.get("ok") and snapshot_age <= OPTION_SNAPSHOT_TTL_SECONDS:
                    cur_price = broker_snapshot["ltp"]
            unrealized = None
            if cur_price is not None:
                unrealized = (cur_price - p["entry_price"]) * p["qty"] if p["action"] == "BUY" else (p["entry_price"] - cur_price) * p["qty"]
            open_rows.append({
                "ID": p["id"],
                "Symbol": sym,
                "Action": p["action"],
                "Qty": p["qty"],
                "Entry (₹)": p["entry_price"],
                "LTP (₹)": cur_price,
                "SL (₹)": p["sl_price"],
                "Target (₹)": p["tp_price"],
                "Unrealized P&L": round(unrealized, 2) if unrealized is not None else None,
                "Entered At": p["timestamp"]
            })
        st.dataframe(
            pd.DataFrame(open_rows),
            width="stretch",
            hide_index=True,
            column_config={
                "Entry (₹)": st.column_config.NumberColumn(format="₹%.2f"),
                "LTP (₹)": st.column_config.NumberColumn(format="₹%.2f"),
                "SL (₹)": st.column_config.NumberColumn(format="₹%.2f"),
                "Target (₹)": st.column_config.NumberColumn(format="₹%.2f"),
                "Unrealized P&L": st.column_config.NumberColumn(format="%+,.2f"),
            }
        )
        st.caption("Blank LTP and P&L cells mean no fresh quote is available for that position; the app does not reuse the entry price as a market price.")
    else:
        st.info("No active paper positions. Place bracket orders from Tab 1 or Tab 2 to start testing.")

    st.write("")
    st.markdown("<h4 style='color:#fff; margin-bottom:8px;'>Closed Trade History & Performance Journal</h4>", unsafe_allow_html=True)
    if pdata["closed_trades"]:
        history_df = pd.DataFrame(pdata["closed_trades"])
        st.dataframe(
            history_df[["id", "symbol", "action", "qty", "entry_price", "exit_price", "pnl", "exit_reason", "exit_time"]],
            width="stretch",
            hide_index=True,
            column_config={
                "entry_price": st.column_config.NumberColumn(format="₹%.2f"),
                "exit_price": st.column_config.NumberColumn(format="₹%.2f"),
                "pnl": st.column_config.NumberColumn(format="%+,.2f"),
            }
        )
    else:
        st.caption("Closed trades will automatically populate here as Target or Stop-Loss boundaries are triggered.")

    if st.button("🔄 Reset Paper Trading Account to ₹5,00,000"):
        st.session_state["paper_data"] = {
            "cash": 500000.0,
            "positions": [],
            "closed_trades": []
        }
        save_paper_account(st.session_state["paper_data"])
        st.rerun()

# =========================================================================
# TAB 4: BACKTEST ENGINE
# =========================================================================
with tab_backtest:
    st.markdown("<h3 style='color:#fff; margin-bottom:4px;'>Historical Backtest Engine</h3>", unsafe_allow_html=True)
    st.caption("Quantifies Breakouts & EMA regime pullbacks with simulated capital curves.")

    b1, b2, b3 = st.columns(3)
    with b1:
        bt_sym = st.selectbox("Asset to Backtest", symbols, key="bt_sym")
    with b2:
        bt_years = st.slider("Lookback Period (Years)", 1, 5, 2)
    with b3:
        bt_capital = st.number_input("Starting Capital (₹)", 100000, step=10000)

    if st.button("Run Simulation"):
        with st.spinner("Executing backtest..."):
            raw, backtest_feed_message = download_public_chart_data(
                bt_sym, period=f"{bt_years}y", interval="1d", progress=False,
            )
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            raw.dropna(inplace=True)

            if backtest_feed_message:
                st.warning(f"Simulation not run — {backtest_feed_message} No prices were substituted.")
            elif len(raw) > 50:
                raw['EMA_50'] = raw['Close'].ewm(span=50, adjust=False).mean()
                raw['EMA_200'] = raw['Close'].ewm(span=200, adjust=False).mean()
                raw['Rolling_Res'] = raw['High'].rolling(20).max().shift(1)

                raw['Entry'] = (raw['Close'] > raw['Rolling_Res']) & (raw['EMA_50'] > raw['EMA_200'])
                raw['Exit'] = raw['Close'] < raw['EMA_50']

                position = 0
                trades = []
                entry_price = 0
                cash = bt_capital
                portfolio_curve = []

                for idx, row in raw.iterrows():
                    c_price = row['Close']
                    if position == 0 and row['Entry']:
                        position = cash / c_price
                        entry_price = c_price
                        cash = 0
                    elif position > 0 and row['Exit']:
                        exit_price = c_price
                        cash = position * exit_price * (1 - 0.001)
                        trades.append((exit_price - entry_price) / entry_price)
                        position = 0
                        entry_price = 0

                    portfolio_curve.append(cash if position == 0 else position * c_price)

                raw['Portfolio_Val'] = portfolio_curve
                final_val = raw['Portfolio_Val'].iloc[-1]
                tot_return = ((final_val - bt_capital) / bt_capital) * 100
                win_trades = [t for t in trades if t > 0]
                win_rate = (len(win_trades) / len(trades) * 100) if trades else 0

                roll_max = raw['Portfolio_Val'].cummax()
                drawdown = (raw['Portfolio_Val'] - roll_max) / roll_max
                max_dd = drawdown.min() * 100

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Final Capital", f"₹{final_val:,.2f}")
                m2.metric("Return", f"{tot_return:+.2f}%")
                m3.metric("Win Rate", f"{win_rate:.1f}%")
                m4.metric("Max Drawdown", f"{max_dd:.2f}%")

                fig_bt = go.Figure()
                fig_bt.add_trace(go.Scatter(x=raw.index, y=raw['Portfolio_Val'], mode='lines', line=dict(color='#34d399', width=2), name="Equity"))
                fig_bt = apply_chart_style(fig_bt, height=380)
                st.plotly_chart(fig_bt, width="stretch")
            else:
                st.warning("Insufficient data available for this asset.")

# =========================================================================
# TAB 5: S/R CHART ANALYSIS
# =========================================================================
with tab_chart:
    st.markdown("<h3 style='color:#fff; margin-bottom:4px;'>Support & Resistance Extrema Analysis</h3>", unsafe_allow_html=True)
    c_sym = st.selectbox("Select Asset for Visual Levels", symbols, key="c_sym")

    c_raw, chart_feed_message = download_public_chart_data(c_sym, period=period, interval=interval, progress=False)
    if isinstance(c_raw.columns, pd.MultiIndex):
        c_raw.columns = c_raw.columns.get_level_values(0)
    c_raw.dropna(inplace=True)

    if chart_feed_message:
        st.warning(f"Chart unavailable — {chart_feed_message} No prices were substituted.")
    elif len(c_raw) > 20:
        highs = argrelextrema(c_raw['High'].values, np.greater, order=extrema_order)[0]
        lows = argrelextrema(c_raw['Low'].values, np.less, order=extrema_order)[0]

        c_raw['Res'] = np.nan
        c_raw['Sup'] = np.nan
        c_raw.loc[c_raw.index[highs], 'Res'] = c_raw.iloc[highs]['High']
        c_raw.loc[c_raw.index[lows], 'Sup'] = c_raw.iloc[lows]['Low']
        c_raw['Nearest_Res'] = c_raw['Res'].ffill()
        c_raw['Nearest_Sup'] = c_raw['Sup'].ffill()

        fig_chart = go.Figure()
        fig_chart.add_trace(go.Candlestick(
            x=c_raw.index, open=c_raw['Open'], high=c_raw['High'], low=c_raw['Low'], close=c_raw['Close'], name="Price",
            increasing_line_color="#34d399", decreasing_line_color="#fb7185"
        ))
        fig_chart.add_trace(go.Scatter(
            x=c_raw.index, y=c_raw['Nearest_Res'], mode='lines', line_shape='hv', line=dict(color='#fb7185', dash='dash', width=1.5), name='Resistance'
        ))
        fig_chart.add_trace(go.Scatter(
            x=c_raw.index, y=c_raw['Nearest_Sup'], mode='lines', line_shape='hv', line=dict(color='#34d399', dash='dash', width=1.5), name='Support'
        ))

        fig_chart = apply_chart_style(fig_chart, height=550)
        fig_chart.update_xaxes(rangeslider_visible=False)
        st.plotly_chart(fig_chart, width="stretch")
    else:
        st.info("Insufficient verified chart history for this asset; no support or resistance levels are shown.")

# =========================================================================
# BOTTOM SECTION: ANGEL ONE GATEWAY
# =========================================================================
st.write("")
st.markdown("""
<div class="bottom-card">
    <div class="bottom-title">🔗 Angel One SmartAPI Gateway</div>
</div>
""", unsafe_allow_html=True)

ao_c1, ao_c2 = st.columns(2)
with ao_c1:
    ao_api_key = st.text_input("API Key", value=def_api_key, type="password", placeholder="API Key")
    ao_client = st.text_input("Client ID", value=def_client_id, placeholder="Client Code")
with ao_c2:
    ao_pin = st.text_input("MPIN", value=def_pin, type="password", placeholder="MPIN")
    ao_totp_key = st.text_input("TOTP Secret Key", value=def_totp, type="password", placeholder="Secret Key")

ao_btn_col, ao_status_col = st.columns([1.5, 2.5])
with ao_btn_col:
    if st.button("Connect Broker", width="stretch"):
        if all([ao_api_key, ao_client, ao_pin, ao_totp_key]):
            api_obj, res_msg = connect_angel_one(ao_api_key, ao_client, ao_pin, ao_totp_key)
            if api_obj:
                st.session_state["smart_api"] = api_obj
                st.session_state["live_orders_armed"] = False
                st.success("Broker session connected. Live controls remain locked until you explicitly arm them below.")
            else:
                st.error(f"Failed: {res_msg}")
        else:
            st.warning("Fill in all credentials.")
with ao_status_col:
    if "smart_api" in st.session_state:
        st.markdown("<span style='color:#34d399; font-size:13px; font-weight:700; line-height:38px;'>● BROKER SESSION ACTIVE</span>", unsafe_allow_html=True)
    else:
        st.markdown("<span style='color:#94a3b8; font-size:13px; font-weight:500; line-height:38px;'>Status: Disconnected</span>", unsafe_allow_html=True)

if "smart_api" in st.session_state and LIVE_ORDER_EXECUTION_ENABLED:
    st.markdown("<div class='live-arm-card'><strong>Live-order safety gate</strong><br>Arming enables controls only. Each BUY or SELL still needs its own confirmation, and a broker response means <em>submitted</em>, not filled.</div>", unsafe_allow_html=True)
    armed = st.checkbox(
        "I understand that LIVE mode can submit real orders to Angel One and I want to arm the live controls for this browser session.",
        key="live_order_acknowledgement",
    )
    st.session_state["live_orders_armed"] = armed
    if armed:
        st.markdown("<div class='live-ready-card'>⚡ <strong>LIVE CONTROLS ARMED</strong> — select Live Broker mode, then confirm the exact BUY or SELL order before submitting.</div>", unsafe_allow_html=True)
    else:
        st.caption("Paper Trading remains available without arming. Uncheck this box any time to lock live controls again.")

last_live_submission = st.session_state.get("last_live_submission")
if last_live_submission:
    order_id = last_live_submission.get("order_id") or "not returned"
    st.caption(
        f"Last live submission: {last_live_submission['action']} {last_live_submission['symbol']} · "
        f"{last_live_submission['kind']} · Order ID: {order_id} · {last_live_submission['timestamp']}. "
        "Verify its actual status in Angel One's order book."
    )
