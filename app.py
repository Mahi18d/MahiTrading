import csv
import base64
import concurrent.futures
from datetime import datetime, timedelta
import gzip
import hashlib
import hmac
from io import StringIO
import json
import logging
import os
import re
import sys
import threading
import uuid
import time
import importlib.util
import urllib.request
from urllib.parse import quote
import html
import xml.etree.ElementTree as ET
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
# the user actually connects, so simply opening the app is network-free.
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
OPTION_CHAIN_TTL_SECONDS = 10
UNDERLYING_INDEX_TTL_SECONDS = 10
OPTION_CHAIN_MAX_TOKENS = 50
YFINANCE_LOG_LOCK = threading.RLock()
INDEX_CONSTITUENT_CACHE_LOCK = threading.RLock()
IST_TIMEZONE = ZoneInfo("Asia/Kolkata")

# The F&O desk deliberately exposes only liquid index products requested for
# this workspace.  Each entry declares the exact Angel One master identity
# and exchange instead of guessing from a display label.  In particular,
# SENSEX options are BFO contracts while the other listed index options are
# NFO contracts.
FO_INDEX_UNIVERSE = {
    "NIFTY 50": {
        "broker_underlyings": ("NIFTY",),
        "option_exchange": "NFO",
        "spot_exchange": "NSE",
        "chart_symbol": "^NSEI",
        "description": "NIFTY index options",
    },
    "BANKNIFTY": {
        "broker_underlyings": ("BANKNIFTY",),
        "option_exchange": "NFO",
        "spot_exchange": "NSE",
        "chart_symbol": "^NSEBANK",
        "description": "NIFTY Bank index options",
    },
    "FINNIFTY": {
        "broker_underlyings": ("FINNIFTY",),
        "option_exchange": "NFO",
        "spot_exchange": "NSE",
        "chart_symbol": "NIFTY_FIN_SERVICE.NS",
        "description": "NIFTY Financial Services index options",
    },
    "SENSEX": {
        "broker_underlyings": ("SENSEX",),
        "option_exchange": "BFO",
        "spot_exchange": "BSE",
        "chart_symbol": "^BSESN",
        "description": "BSE SENSEX index options",
    },
    "NIFT Midcap": {
        "broker_underlyings": ("MIDCPNIFTY",),
        "option_exchange": "NFO",
        "spot_exchange": "NSE",
        "chart_symbol": "NIFTY_MID_SELECT.NS",
        "description": "MIDCPNIFTY / NIFTY MID SELECT index options",
    },
}

FO_PUBLIC_CHART_OPTIONS = {
    "5 minute": ("5d", "5m"),
    "15 minute": ("5d", "15m"),
    "1 hour": ("1mo", "60m"),
    "1 day": ("6mo", "1d"),
}
INDEX_CONSTITUENT_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "index_constituents_cache.json"
)

# BSE's official SENSEX constituent download identifies companies with BSE
# scrip codes.  The public chart provider used in this app needs exchange
# ticker symbols instead, so this is an explicitly reviewed crosswalk rather
# than a name-derived guess.  If BSE introduces a new code, parsing fails
# safely and the UI labels the last validated list as stale until the map is
# reviewed and updated.
BSE_SENSEX_YAHOO_SYMBOLS = {
    "532921": "ADANIPORTS.NS", "500820": "ASIANPAINT.NS", "532215": "AXISBANK.NS",
    "500034": "BAJFINANCE.NS", "532978": "BAJAJFINSV.NS", "500049": "BEL.NS",
    "532454": "BHARTIARTL.NS", "543320": "ETERNAL.NS", "532281": "HCLTECH.NS",
    "500180": "HDFCBANK.NS", "500696": "HINDUNILVR.NS", "532174": "ICICIBANK.NS",
    "500209": "INFY.NS", "539448": "INDIGO.NS", "500875": "ITC.NS",
    "500247": "KOTAKBANK.NS", "500510": "LT.NS", "500520": "M&M.NS",
    "532500": "MARUTI.NS", "532555": "NTPC.NS", "532898": "POWERGRID.NS",
    "500325": "RELIANCE.NS", "500112": "SBIN.NS", "524715": "SUNPHARMA.NS",
    "532540": "TCS.NS", "500470": "TATASTEEL.NS", "532755": "TECHM.NS",
    "500114": "TITAN.NS", "500251": "TRENT.NS", "532538": "ULTRACEMCO.NS",
}


def _parse_direct_yahoo_chart_payload(payload):
    """Turn one Yahoo Chart API JSON response into a usable OHLCV frame."""
    try:
        decoded = json.loads(bytes(payload).decode("utf-8"))
        chart = decoded.get("chart", {}) if isinstance(decoded, dict) else {}
        result_rows = chart.get("result") if isinstance(chart.get("result"), list) else []
        result = result_rows[0] if result_rows and isinstance(result_rows[0], dict) else {}
        timestamps = result.get("timestamp") if isinstance(result.get("timestamp"), list) else []
        indicators = result.get("indicators") if isinstance(result.get("indicators"), dict) else {}
        quote_rows = indicators.get("quote") if isinstance(indicators.get("quote"), list) else []
        quote_row = quote_rows[0] if quote_rows and isinstance(quote_rows[0], dict) else {}
        if not timestamps or not quote_row:
            return pd.DataFrame()
        index = pd.to_datetime(timestamps, unit="s", utc=True).tz_convert(IST_TIMEZONE)
        frame = pd.DataFrame({
            "Open": quote_row.get("open", []),
            "High": quote_row.get("high", []),
            "Low": quote_row.get("low", []),
            "Close": quote_row.get("close", []),
            "Volume": quote_row.get("volume", []),
        }, index=index)
        for column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame.dropna(subset=["Open", "High", "Low", "Close"])
    except (TypeError, ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError):
        return pd.DataFrame()


def fetch_direct_yahoo_chart_data(symbols, period, interval, urlopen_fn=None):
    """Read Yahoo's public Chart API when yfinance itself cannot load its cache.

    This is a transport fallback for the same public provider, not a synthetic
    price feed.  Each response is parsed as OHLCV or discarded entirely.
    """
    requested = [symbols] if isinstance(symbols, str) else list(symbols or [])
    if not requested:
        return pd.DataFrame(), "No chart symbols were requested."
    opener = urlopen_fn or urllib.request.urlopen

    def fetch_one(symbol):
        safe_symbol = quote(str(symbol), safe=".^-_")
        source_url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{safe_symbol}"
            f"?range={quote(str(period), safe='')}&interval={quote(str(interval), safe='')}"
        )
        try:
            request = urllib.request.Request(
                source_url,
                headers={"User-Agent": "MahiTrading/1.0", "Accept": "application/json"},
            )
            with opener(request, timeout=15) as response:
                status = getattr(response, "status", None)
                if status is None and hasattr(response, "getcode"):
                    status = response.getcode()
                if status is not None and not 200 <= int(status) < 300:
                    return symbol, pd.DataFrame()
                frame = _parse_direct_yahoo_chart_payload(response.read())
            return symbol, frame
        except (OSError, ValueError, TypeError, TimeoutError):
            return symbol, pd.DataFrame()

    frames = {}
    # Bounded concurrency prevents a failed batch from creating dozens of
    # simultaneous connections while still keeping an all-market scan usable.
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(requested))) as executor:
        for symbol, frame in executor.map(fetch_one, requested):
            if not frame.empty:
                frames[symbol] = frame
    if not frames:
        return pd.DataFrame(), "Yahoo public chart feed returned no data for this request."
    if len(requested) == 1:
        return frames.get(requested[0], pd.DataFrame()), ""
    return pd.concat(frames, axis=1, sort=False), ""


def _fetch_fo_public_index_chart(chart_symbol, period, interval):
    """Load a labelled public index chart for visual context only.

    This feed is deliberately separate from the Angel One broker evidence used
    by the F&O gate.  It may be delayed and is never used to choose ATM or to
    unlock a trade action.
    """
    return fetch_direct_yahoo_chart_data(str(chart_symbol), str(period), str(interval))


if "--self-test" in sys.argv:
    fetch_fo_public_index_chart = _fetch_fo_public_index_chart
else:
    fetch_fo_public_index_chart = st.cache_data(
        ttl=UNDERLYING_INDEX_TTL_SECONDS, show_spinner=False
    )(_fetch_fo_public_index_chart)


def _merge_chart_batches(primary, fallback):
    if primary.empty:
        return fallback
    if fallback.empty:
        return primary
    if isinstance(primary.columns, pd.MultiIndex) or isinstance(fallback.columns, pd.MultiIndex):
        return pd.concat([primary, fallback], axis=1)
    return primary if not primary.empty else fallback


def download_public_chart_data(*args, download_fn=None, **kwargs):
    """Load public Yahoo chart data without treating a missing ticker as a price.

    yfinance emits a terminal-level "possibly delisted" error for any temporary
    Yahoo miss.  That text is not a reliable corporate-action signal. When its
    local cache/database fails, use the same provider's direct Chart API as a
    bounded fallback; otherwise show an explicit unavailable state.
    """
    downloader = download_fn or yf.download
    yf_logger = logging.getLogger("yfinance")
    requested_symbols = args[0] if args else kwargs.get("tickers")
    period = kwargs.get("period", "5d")
    interval = kwargs.get("interval", "1d")
    yfinance_error = ""
    with YFINANCE_LOG_LOCK:
        previous_disabled = yf_logger.disabled
        try:
            yf_logger.disabled = True
            data = downloader(*args, **kwargs)
        except Exception:
            data = pd.DataFrame()
            yfinance_error = "Yahoo public chart feed request failed."
        finally:
            yf_logger.disabled = previous_disabled
    if not isinstance(data, pd.DataFrame):
        data = pd.DataFrame()

    # Keep injected/mocked calls network-free for tests, and only use a second
    # network path for the real yfinance transport.
    if download_fn is not None:
        if data.empty:
            return pd.DataFrame(), yfinance_error or "Yahoo public chart feed returned no data for this request."
        return data, ""

    missing_symbols = missing_public_chart_symbols(data, requested_symbols) if not data.empty else (
        [requested_symbols] if isinstance(requested_symbols, str) else list(requested_symbols or [])
    )
    if missing_symbols:
        fallback_data, fallback_message = fetch_direct_yahoo_chart_data(missing_symbols, period, interval)
        if not fallback_data.empty:
            data = _merge_chart_batches(data, fallback_data)
    if not data.empty:
        return data, ""
    return pd.DataFrame(), yfinance_error or fallback_message or "Yahoo public chart feed returned no data for this request."


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


def parse_official_bse_sensex_constituent_csv(payload):
    """Parse BSE's official SENSEX CSV using only reviewed chart-symbol mappings.

    BSE's file intentionally contains BSE numeric scrip codes, not chart feed
    tickers.  Never turn a company name into a guessed ticker: every code must
    exist in ``BSE_SENSEX_YAHOO_SYMBOLS`` and every result must be unique.
    """
    if not isinstance(payload, (bytes, bytearray)) or len(payload) < 20:
        raise ValueError("Official BSE SENSEX constituent file was empty or unreadable.")
    try:
        text = bytes(payload).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Official BSE SENSEX constituent file was not UTF-8 CSV data.") from exc
    lowered = text.lstrip().lower()
    if lowered.startswith("<!doctype") or lowered.startswith("<html") or "access denied" in lowered[:500]:
        raise ValueError("Official BSE SENSEX constituent download returned an HTML/error page.")
    reader = csv.DictReader(StringIO(text))
    if not reader.fieldnames:
        raise ValueError("Official BSE SENSEX constituent file has no CSV header.")
    normalized_headers = {str(header).strip().casefold(): header for header in reader.fieldnames if header}
    name_header = normalized_headers.get("constituents")
    code_header = normalized_headers.get("symbol")
    if not name_header or not code_header:
        raise ValueError("Official BSE SENSEX constituent file needs Constituents and Symbol columns.")

    symbols = []
    seen_codes = set()
    seen_symbols = set()
    for row in reader:
        company_name = str(row.get(name_header) or "").strip()
        raw_code = str(row.get(code_header) or "").strip()
        if not company_name or not re.fullmatch(r"\d{6}", raw_code):
            raise ValueError("Official BSE SENSEX constituent file contains an invalid company or scrip code.")
        if raw_code in seen_codes:
            raise ValueError(f"Official BSE SENSEX constituent file has duplicate scrip code: {raw_code}.")
        chart_symbol = BSE_SENSEX_YAHOO_SYMBOLS.get(raw_code)
        if not chart_symbol:
            raise ValueError(
                f"BSE SENSEX scrip code {raw_code} has no reviewed public-chart mapping; update it before scanning."
            )
        if chart_symbol in seen_symbols:
            raise ValueError(f"BSE SENSEX mapping has a duplicate chart symbol: {chart_symbol}.")
        seen_codes.add(raw_code)
        seen_symbols.add(chart_symbol)
        symbols.append(chart_symbol)
    if not symbols:
        raise ValueError("Official BSE SENSEX constituent file contained no symbols.")
    return symbols


def fetch_official_index_constituents(source_config, urlopen_fn=None):
    """Fetch one configured official constituent CSV with bounded validation."""
    source_url = str(source_config.get("url") or "")
    parser_name = str(source_config.get("parser") or "nse").lower()
    checked_at = ist_now().strftime("%Y-%m-%d %H:%M:%S %Z")
    metadata = {
        "source_url": source_url,
        "publisher": str(source_config.get("publisher") or "Official constituent CSV"),
        "checked_at": checked_at,
        "last_modified": "",
        "etag": "",
    }
    if parser_name == "nse":
        trusted_source = source_url.startswith("https://www.niftyindices.com/")
        parser = parse_official_index_constituent_csv
    elif parser_name == "bse_sensex":
        trusted_source = source_url == "https://www.bseindices.com/AsiaIndexAPI/api/Codewise_IndicesDownload/w?code=16"
        parser = parse_official_bse_sensex_constituent_csv
    else:
        return [], "Official constituent source parser is not configured safely.", metadata
    if not trusted_source:
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
            symbols = parser(payload)
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
            "source": str(source_config.get("publisher") or "Official constituent CSV"),
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
            "source": f"Previously validated {source_config.get('publisher') or 'official constituent'} cache",
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


def extract_option_contracts(master_rows, underlying, allowed_exchanges=("NFO", "BFO"), instrument_types=("OPTIDX", "OPTSTK")):
    """Build broker-listed option metadata without inventing exchange or price data."""
    contracts = []
    target = str(underlying or "").strip().upper()
    allowed_exchange_set = {str(value).upper() for value in allowed_exchanges}
    allowed_instrument_set = {str(value).upper() for value in instrument_types}
    for item in master_rows or []:
        if not isinstance(item, dict):
            continue
        exchange = str(item.get("exch_seg", "")).upper()
        if exchange not in allowed_exchange_set:
            continue
        instrument_type = str(item.get("instrumenttype", "")).upper()
        symbol = str(item.get("symbol", "")).strip().upper()
        side = _option_side(symbol or item.get("optiontype"))
        if instrument_type not in allowed_instrument_set or side not in {"CE", "PE"}:
            continue
        if str(item.get("name", "")).strip().upper() != target:
            continue
        expiry_raw = str(item.get("expiry", "")).strip().upper()
        expiry_date = _parse_angel_expiry(expiry_raw)
        if expiry_date is not None and expiry_date < ist_now().date():
            continue
        strike = _normalise_angel_strike(item.get("strike"))
        lot_size = _as_int(item.get("lotsize"))
        if not symbol or not item.get("token") or strike is None or lot_size is None or lot_size <= 0:
            continue
        contracts.append({
            "symbol": symbol,
            "token": str(item["token"]),
            "underlying": target,
            "exchange": exchange,
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


def extract_option_underlyings(master_rows):
    """List only broker-listed, non-expired NFO option underlyings from the master."""
    underlyings = set()
    today = ist_now().date()
    for item in master_rows or []:
        if not isinstance(item, dict) or str(item.get("exch_seg", "")).upper() != "NFO":
            continue
        if str(item.get("instrumenttype", "")).upper() not in {"OPTIDX", "OPTSTK"}:
            continue
        if _option_side(item.get("symbol") or item.get("optiontype")) not in {"CE", "PE"}:
            continue
        expiry = _parse_angel_expiry(str(item.get("expiry", "")))
        name = str(item.get("name", "")).strip().upper()
        if name and (expiry is None or expiry >= today):
            underlyings.add(name)
    return sorted(underlyings)


def fo_contract_key(contract):
    """Return an exchange-aware key so BFO/NFO tokens can never collide."""
    row = contract if isinstance(contract, dict) else {}
    return f"{str(row.get('exchange') or '').upper()}:{str(row.get('token') or '')}"


def resolve_fo_index_option_contracts(master_rows, index_name):
    """Return only current OPTIDX contracts for one supported F&O index.

    The display name is never treated as a broker symbol.  A missing index in
    the official master is surfaced as an empty result, not silently replaced
    with a stock or another derivative contract.
    """
    spec = FO_INDEX_UNIVERSE.get(str(index_name))
    if not spec:
        return []
    contracts = []
    for broker_underlying in spec["broker_underlyings"]:
        contracts.extend(extract_option_contracts(
            master_rows,
            broker_underlying,
            allowed_exchanges=(spec["option_exchange"],),
            instrument_types=("OPTIDX",),
        ))
    unique = {}
    for contract in contracts:
        key = fo_contract_key(contract)
        if key and key not in unique:
            unique[key] = contract
    return sorted(
        unique.values(),
        key=lambda row: (
            row["expiry_date"] is None,
            row["expiry_date"] or datetime.max.date(),
            row["strike"],
            row["side"],
        ),
    )


def resolve_fo_index_spot_contract(master_rows, index_name):
    """Resolve the broker's AMXIDX index record for the selected five-index desk."""
    spec = FO_INDEX_UNIVERSE.get(str(index_name))
    if not spec:
        return None
    names = {str(value).upper() for value in spec["broker_underlyings"]}
    candidates = []
    for item in master_rows or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("exch_seg") or "").upper() != spec["spot_exchange"]:
            continue
        if str(item.get("instrumenttype") or "").upper() != "AMXIDX":
            continue
        if str(item.get("name") or "").strip().upper() not in names:
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        token = str(item.get("token") or "").strip()
        if symbol and token:
            candidates.append({
                "exchange": spec["spot_exchange"],
                "symbol": symbol,
                "token": token,
                "underlying": str(item.get("name") or "").strip().upper(),
                "instrument_type": "AMXIDX",
            })
    return sorted(candidates, key=lambda row: (row["symbol"], row["token"]))[0] if candidates else None


def pair_fo_index_contracts(contracts):
    """Pair CE/PE by verified strike without manufacturing a missing leg."""
    pairs = {}
    for contract in contracts or []:
        if not isinstance(contract, dict) or contract.get("side") not in {"CE", "PE"}:
            continue
        strike = _as_float(contract.get("strike"))
        if strike is None:
            continue
        pair = pairs.setdefault(float(strike), {"strike": float(strike)})
        side = contract["side"]
        # Master duplicates are never merged across exchange/token.  The first
        # deterministic record is kept and the duplicate remains excluded.
        pair.setdefault(side, contract)
    return {strike: pairs[strike] for strike in sorted(pairs)}


def nearest_fo_chain_strike(pairs, broker_ltp):
    """Return an actual listed strike nearest a verified broker index LTP."""
    price = _as_float(broker_ltp)
    strikes = sorted(float(value) for value in (pairs or {}).keys())
    if price is None or price <= 0 or not strikes:
        return None
    return min(strikes, key=lambda strike: (abs(strike - price), strike))


def fo_chain_band(pairs, center_strike, depth=5):
    """Return a bounded strike band around a selected, listed centre strike."""
    strikes = sorted(float(value) for value in (pairs or {}).keys())
    if not strikes:
        return []
    centre = _as_float(center_strike)
    if centre is None or centre not in pairs:
        return []
    width = max(0, _as_int(depth) or 0)
    centre_index = strikes.index(float(centre))
    start = max(0, centre_index - width)
    end = min(len(strikes), centre_index + width + 1)
    return [pairs[strike] for strike in strikes[start:end]]


def _quote_number(row, *keys):
    for key in keys:
        if isinstance(row, dict) and row.get(key) not in (None, ""):
            number = _as_float(row.get(key))
            if number is not None:
                return number
    return None


def _quote_best_prices(row):
    """Extract bid/ask only from explicit broker depth fields when present."""
    bid = _quote_number(row, "bestBidPrice", "bestbidprice", "bidPrice", "bidprice", "bid")
    ask = _quote_number(row, "bestAskPrice", "bestaskprice", "askPrice", "askprice", "ask")
    depth = row.get("best5Data") or row.get("best5data") or row.get("depth") if isinstance(row, dict) else None
    if not isinstance(depth, (list, tuple, dict)):
        return bid, ask
    depth_rows = []
    if isinstance(depth, dict):
        for side_name, values in depth.items():
            if isinstance(values, list):
                for value in values:
                    if isinstance(value, dict):
                        decorated = dict(value)
                        decorated.setdefault("_depth_side", side_name)
                        depth_rows.append(decorated)
    else:
        depth_rows = [value for value in depth if isinstance(value, dict)]
    for depth_row in depth_rows:
        side = str(depth_row.get("_depth_side") or depth_row.get("flag") or depth_row.get("side") or depth_row.get("type") or "").upper()
        price = _quote_number(depth_row, "price", "Price", "bid", "ask")
        if price is None:
            continue
        if bid is None and side in {"BUY", "BID", "0"}:
            bid = price
        if ask is None and side in {"SELL", "ASK", "1"}:
            ask = price
    return bid, ask


def normalise_broker_market_quote(row, exchange, token):
    """Keep raw SmartAPI FULL-market fields; unavailable fields stay None."""
    data = row if isinstance(row, dict) else {}
    bid, ask = _quote_best_prices(data)
    return {
        "exchange": str(exchange or "").upper(),
        "token": str(token or ""),
        "ltp": _quote_number(data, "ltp", "lastTradedPrice", "last_traded_price", "lastPrice"),
        "close": _quote_number(data, "close", "previousClose", "previous_close"),
        "open": _quote_number(data, "open"),
        "high": _quote_number(data, "high"),
        "low": _quote_number(data, "low"),
        "net_change": _quote_number(data, "netChange", "netchange", "change"),
        "percent_change": _quote_number(data, "percentChange", "percentchange", "pChange", "pchange"),
        "bid": bid,
        "ask": ask,
        "volume": _quote_number(data, "tradeVolume", "tradevolume", "volume", "totalTradedVolume"),
        "open_interest": _quote_number(data, "opnInterest", "openInterest", "openinterest", "oi"),
        "broker_timestamp": data.get("exchangeFeedTime") or data.get("exchangeTimestamp") or data.get("tradeTime"),
    }


def fetch_broker_market_data(smart_api, contracts, mode="FULL"):
    """Load a bounded batch of real quotes for an exact broker contract list.

    SmartAPI does not provide a ready-made option-chain endpoint.  This calls
    its supported batch market-data method for the selected band only.  No
    missing field is calculated, carried over, or filled from another source.
    """
    rows = [row for row in contracts or [] if isinstance(row, dict) and row.get("token") and row.get("exchange")]
    if smart_api is None:
        return {"ok": False, "state": "disconnected", "message": "Angel One is not connected.", "quotes": {}, "requested_count": len(rows), "fetched_count": 0}
    if not hasattr(smart_api, "getMarketData"):
        return {"ok": False, "state": "unavailable", "message": "Installed SmartAPI client has no batch market-data method.", "quotes": {}, "requested_count": len(rows), "fetched_count": 0}
    if not rows:
        return {"ok": False, "state": "invalid_contract", "message": "No verified F&O contracts were selected for the chain.", "quotes": {}, "requested_count": 0, "fetched_count": 0}
    if len(rows) > OPTION_CHAIN_MAX_TOKENS:
        return {"ok": False, "state": "too_many_contracts", "message": f"The requested chain has {len(rows)} contracts; SmartAPI batch requests are capped at {OPTION_CHAIN_MAX_TOKENS} here.", "quotes": {}, "requested_count": len(rows), "fetched_count": 0}

    exchange_tokens = {}
    requested = {}
    for contract in rows:
        exchange = str(contract["exchange"]).upper()
        token = str(contract["token"])
        exchange_tokens.setdefault(exchange, []).append(token)
        requested[f"{exchange}:{token}"] = contract
    try:
        response = smart_api.getMarketData(str(mode).upper(), exchange_tokens)
    except Exception as exc:
        return {"ok": False, "state": "error", "message": f"Broker option-chain request failed: {exc}", "quotes": {}, "requested_count": len(rows), "fetched_count": 0}
    if not isinstance(response, dict) or response.get("status") is False:
        message = response.get("message", "Broker did not return the option-chain snapshot.") if isinstance(response, dict) else "Invalid broker response."
        return {"ok": False, "state": "unavailable", "message": message, "quotes": {}, "requested_count": len(rows), "fetched_count": 0}

    payload = response.get("data")
    if isinstance(payload, dict):
        fetched_rows = payload.get("fetched") or payload.get("data") or []
    elif isinstance(payload, list):
        fetched_rows = payload
    else:
        fetched_rows = []
    quotes = {}
    for raw in fetched_rows:
        if not isinstance(raw, dict):
            continue
        token = str(raw.get("symbolToken") or raw.get("symboltoken") or raw.get("token") or "")
        exchange = str(raw.get("exchange") or raw.get("exchangeSegment") or raw.get("exch_seg") or "").upper()
        if not exchange:
            matches = [key for key in requested if key.endswith(f":{token}")]
            exchange = matches[0].split(":", 1)[0] if len(matches) == 1 else ""
        key = f"{exchange}:{token}"
        if key not in requested:
            continue
        quotes[key] = normalise_broker_market_quote(raw, exchange, token)
    fetched_count = len(quotes)
    if not fetched_count:
        return {"ok": False, "state": "unavailable", "message": "Broker returned no matching quotes for the selected chain.", "quotes": {}, "requested_count": len(rows), "fetched_count": 0}
    state = "snapshot" if fetched_count == len(rows) else "partial"
    return {
        "ok": True,
        "state": state,
        "message": "" if state == "snapshot" else f"Broker returned {fetched_count} of {len(rows)} requested chain quotes.",
        "quotes": quotes,
        "requested_count": len(rows),
        "fetched_count": fetched_count,
        "fetched_at": time.time(),
        "source": "Angel One SmartAPI FULL market data",
    }


def quote_for_fo_contract(chain_snapshot, contract):
    """Read a chain quote only when it belongs to the exact selected contract."""
    snapshot = chain_snapshot if isinstance(chain_snapshot, dict) else {}
    return (snapshot.get("quotes") or {}).get(fo_contract_key(contract), {})


def build_fo_chain_rows(chain_pairs, chain_snapshot, selected_contract=None):
    """Build a side-by-side CE/PE chain table from broker fields only."""
    rows = []
    selected_key = fo_contract_key(selected_contract)
    for pair in chain_pairs or []:
        ce = pair.get("CE") or {}
        pe = pair.get("PE") or {}
        ce_quote = quote_for_fo_contract(chain_snapshot, ce)
        pe_quote = quote_for_fo_contract(chain_snapshot, pe)
        row = {"CE Contract": ce.get("symbol") or "—"}
        for label, key in (("CE LTP", "ltp"), ("CE Chg%", "percent_change"), ("CE Bid", "bid"), ("CE Ask", "ask"), ("CE Volume", "volume"), ("CE OI", "open_interest")):
            row[label] = ce_quote.get(key)
        row["Strike"] = pair.get("strike")
        for label, key in (("PE OI", "open_interest"), ("PE Volume", "volume"), ("PE Bid", "bid"), ("PE Ask", "ask"), ("PE Chg%", "percent_change"), ("PE LTP", "ltp")):
            row[label] = pe_quote.get(key)
        row["PE Contract"] = pe.get("symbol") or "—"
        selected_side = "CE" if fo_contract_key(ce) == selected_key else "PE" if fo_contract_key(pe) == selected_key else ""
        row["Selected"] = f"← {selected_side}" if selected_side else ""
        rows.append(row)
    return rows


def build_fo_composite_gate(underlying_snapshot, underlying_evidence, selected_contract, option_gate, option_evidence):
    """Require aligned broker-confirmed index and selected-premium evidence.

    A bearish index is *not* a PE-buy signal.  A PE still needs its own
    bullish premium evidence, just as a CE does in a bullish index regime.
    """
    underlying = underlying_snapshot if isinstance(underlying_snapshot, dict) else {}
    underlying_summary = (underlying_evidence or {}).get("summary") if isinstance(underlying_evidence, dict) else {}
    option_summary = (option_evidence or {}).get("summary") if isinstance(option_evidence, dict) else {}
    base_gate = option_gate if isinstance(option_gate, dict) else build_fo_trade_gate({}, {}, {})
    underlying_regime = str((underlying_summary or {}).get("regime") or "INSUFFICIENT_DATA")
    option_regime = str((option_summary or {}).get("regime") or "INSUFFICIENT_DATA")
    side = str((selected_contract or {}).get("side") or "")
    underlying_usable = max(0, _as_int((underlying_summary or {}).get("usable_check_count")) or 0)
    option_usable = max(0, _as_int((option_summary or {}).get("usable_check_count")) or 0)

    result = dict(base_gate)
    result["underlying_regime"] = underlying_regime
    result["option_regime"] = option_regime
    result["underlying_conditions"] = f"{underlying_usable}/10 usable"
    result["option_conditions"] = f"{option_usable}/10 usable"
    result["allow_long_entry"] = False

    if not underlying.get("ok") or underlying_regime == "INSUFFICIENT_DATA":
        result.update({
            "decision": "WAIT / UNDERLYING DATA",
            "decision_note": "NOT A BUY",
            "conditions": f"Index {underlying_usable}/10 · option {option_usable}/10",
            "condition_detail": "Broker index quote and current-session broker candles are required",
            "risk_status": "PLAN ONLY" if base_gate.get("allow_risk_preview") else "LOCKED",
            "message": "The index context is not broker-verified yet. A public chart may be shown for context, but it never unlocks an F&O entry.",
            "tone": "fo-gate-wait",
        })
        return result
    if underlying_regime == "SIDEWAYS / NO TRADE":
        result.update({
            "decision": "NEUTRAL / NO TRADE",
            "decision_note": "NO DIRECTIONAL BUY",
            "conditions": f"Index {underlying_usable}/10 · option {option_usable}/10",
            "condition_detail": "Broker index is mixed/sideways",
            "risk_status": "PLAN ONLY" if base_gate.get("allow_risk_preview") else "LOCKED",
            "message": "The broker-confirmed index is sideways or mixed. Neither CE nor PE is automatically preferred; wait for a clearer regime.",
            "tone": "fo-gate-wait",
        })
        return result
    if not base_gate.get("allow_long_entry") or option_regime != "BULLISH":
        result.update({
            "decision": base_gate.get("decision", "WAIT / NEUTRAL"),
            "decision_note": "OPTION BUY LOCKED",
            "conditions": f"Index {underlying_usable}/10 · option {option_usable}/10",
            "condition_detail": "Selected CE/PE premium has not passed its own bullish check",
            "risk_status": base_gate.get("risk_status", "PLAN ONLY"),
            "message": "The selected option premium must independently pass the broker-candle gate. Underlying direction alone is never an option BUY recommendation.",
            "tone": base_gate.get("tone", "fo-gate-wait"),
        })
        return result
    aligned = (underlying_regime == "BULLISH" and side == "CE") or (underlying_regime == "BEARISH" and side == "PE")
    if not aligned:
        result.update({
            "decision": "DIRECTION MISMATCH / NO TRADE",
            "decision_note": "NOT A BUY",
            "conditions": f"Index {underlying_usable}/10 · option {option_usable}/10",
            "condition_detail": f"Index {underlying_regime}; selected {side or 'unknown'} contract",
            "risk_status": "PLAN ONLY",
            "message": "The index and selected option side are not aligned. This is a planning-only state, not a prompt to switch sides automatically.",
            "tone": "fo-gate-wait",
        })
        return result
    result.update({
        "decision": f"CONDITIONAL {side} LONG REVIEW",
        "decision_note": "NOT AN AUTOMATIC BUY",
        "conditions": f"Index {underlying_usable}/10 · option {option_usable}/10",
        "condition_detail": f"Broker index {underlying_regime} + selected {side} premium BULLISH",
        "risk_status": "RISK PLAN READY",
        "message": "Index direction and the selected option premium are aligned on broker data. Review quote freshness, risk, quantity, and your own plan before any manual confirmation.",
        "tone": "fo-gate-bull",
        "allow_long_entry": True,
    })
    return result


def calculate_investment_plan(entry, stop, target, quantity):
    """A delivery-buy plan; an entered price is never an attached broker exit."""
    values = [_as_float(value) for value in (entry, stop, target, quantity)]
    if any(value is None or not np.isfinite(value) or value <= 0 for value in values):
        raise ValueError("Entry, stop, target and quantity must be positive finite numbers.")
    entry, stop, target, quantity = values
    if quantity != int(quantity):
        raise ValueError("Quantity must be a whole number.")
    if not stop < entry < target:
        raise ValueError("For a delivery BUY, stop must be below entry and target above entry.")
    result = calculate_directional_preview("BUY", entry, entry - stop, target - entry, int(quantity))
    result.update(stop_percent=(entry - stop) / entry * 100,
                  target_percent=(target - entry) / entry * 100)
    return result


def risk_budget_warnings(planned_loss, trades, limits, today):
    """Advisory paper risk budgets using recorded, gross results only."""
    todays = [t for t in trades if str(t.get("timestamp", ""))[:10] == str(today)]
    closed_today = [t for t in trades if str(t.get("exit_time", ""))[:10] == str(today)]
    net_realized = sum(_as_float(t.get("pnl")) or 0.0 for t in closed_today)
    warnings = []
    per_trade = _as_float(limits.get("per_trade")) or 0
    daily_loss = _as_float(limits.get("daily_loss")) or 0
    max_trades = _as_int(limits.get("max_trades")) or 0
    if per_trade > 0 and planned_loss is not None and planned_loss > per_trade:
        warnings.append(f"Planned loss ₹{planned_loss:,.2f} exceeds your ₹{per_trade:,.2f} per-trade budget.")
    if daily_loss > 0 and max(0.0, -net_realized) >= daily_loss:
        warnings.append("Your recorded paper daily loss budget has been reached.")
    if max_trades > 0 and len(todays) >= max_trades:
        warnings.append("Your paper trade-count budget has been reached.")
    return {"warnings": warnings, "trade_count": len(todays), "realized_pnl": net_realized}


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
    if stop_price <= 0 or target_price <= 0:
        raise ValueError("Stop-loss or target would create an invalid zero/negative price.")
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


EVIDENCE_STATES = {"BULLISH", "BEARISH", "NEUTRAL", "UNAVAILABLE"}


def directional_state(bullish, bearish):
    """Return one non-overlapping technical-evidence state."""
    if bullish and bearish:
        raise ValueError("A technical check cannot be bullish and bearish at the same time.")
    if bullish:
        return "BULLISH"
    if bearish:
        return "BEARISH"
    return "NEUTRAL"


def summarize_directional_evidence(checks, min_usable=6, threshold=6, minimum_lead=2):
    """Score independent bull/bear/neutral checks without forced complements."""
    states = []
    for check in checks or []:
        if len(check) < 2:
            raise ValueError("Each technical check needs a label and state.")
        state = str(check[1]).upper()
        if state not in EVIDENCE_STATES:
            raise ValueError(f"Unsupported technical-evidence state: {state}")
        states.append(state)
    bullish = states.count("BULLISH")
    bearish = states.count("BEARISH")
    neutral = states.count("NEUTRAL")
    unavailable = states.count("UNAVAILABLE")
    usable = bullish + bearish + neutral
    if usable < min_usable:
        regime = "INSUFFICIENT_DATA"
    elif bullish >= threshold and bullish >= bearish + minimum_lead:
        regime = "BULLISH"
    elif bearish >= threshold and bearish >= bullish + minimum_lead:
        regime = "BEARISH"
    else:
        regime = "SIDEWAYS / NO TRADE"
    return {
        "buy_score": bullish,
        "sell_score": bearish,
        "neutral_count": neutral,
        "unavailable_count": unavailable,
        "usable_check_count": usable,
        "total_check_count": len(states),
        "regime": regime,
    }


def evidence_items_html(checks):
    """Render a compact, safely escaped list of independent technical evidence."""
    state_style = {
        "BULLISH": ("✅", "tag-bull"),
        "BEARISH": ("🔻", "tag-bear"),
        "NEUTRAL": ("➖", "tag-neutral"),
        "UNAVAILABLE": ("•", "tag-muted"),
    }
    parts = []
    for rule, state, detail in checks:
        label, css_class = state_style.get(str(state).upper(), ("•", "tag-muted"))
        parts.append(
            '<div class="check-item"><span>{} {}</span><span class="{}">{}</span></div>'.format(
                label, html.escape(str(rule)), css_class, html.escape(str(detail))
            )
        )
    return "".join(parts)


def trade_policy_for_horizon(horizon):
    """Return the conservative new-position policy for the selected equity horizon."""
    if str(horizon) == "Long Term (Weekly)":
        return {
            "long_term": True,
            "allowed_actions": {"BUY"},
            "allowed_order_kinds": {"NORMAL"},
            "required_product": "DELIVERY",
            "message": "Long Term is long-only for new positions. Exit-selling verified holdings is not implemented here.",
        }
    return {
        "long_term": False,
        "allowed_actions": {"BUY", "SELL"},
        "allowed_order_kinds": {"NORMAL", "ROBO"},
        "required_product": None,
        "message": "",
    }


EQUITY_SCAN_COLUMNS = [
    "Symbol", "Price", "Chg%", "Session Δ%", "Trend", "VWAP", "RSI",
    "Buy", "Sell", "Setup", "Data status",
]


def _market_frame_for_symbol(batch_data, symbol):
    """Return one usable OHLCV frame from a public-chart batch, or an empty frame."""
    if not isinstance(batch_data, pd.DataFrame) or batch_data.empty:
        return pd.DataFrame()
    try:
        if isinstance(batch_data.columns, pd.MultiIndex):
            if symbol in batch_data.columns.get_level_values(0):
                frame = batch_data[symbol]
            elif symbol in batch_data.columns.get_level_values(1):
                frame = batch_data.xs(symbol, axis=1, level=1)
            else:
                return pd.DataFrame()
        else:
            frame = batch_data
    except (KeyError, TypeError, AttributeError):
        return pd.DataFrame()
    required = ["Open", "High", "Low", "Close", "Volume"]
    if not isinstance(frame, pd.DataFrame) or any(column not in frame.columns for column in required):
        return pd.DataFrame()
    return frame.dropna(subset=["Open", "High", "Low", "Close"])


def _unavailable_equity_scan_row(symbol, detail="Public chart feed returned no usable OHLC data."):
    """Keep a requested member visible without inventing an unavailable price."""
    clean_symbol = str(symbol).removesuffix(".NS").removesuffix(".BO")
    return {
        "Symbol": clean_symbol,
        "Price": np.nan,
        "Chg%": np.nan,
        "Session Δ%": np.nan,
        "Trend": "DATA UNAVAILABLE",
        "VWAP": np.nan,
        "RSI": np.nan,
        "Buy": "— / 10",
        "Sell": "— / 10",
        "Setup": "UNAVAILABLE",
        "Data status": detail,
    }


def market_scan_action(trend, horizon):
    """Use review/no-trade labels; a technical score is never an order instruction."""
    normalized_trend = str(trend or "").upper()
    if normalized_trend == "BULLISH":
        return "BUY REVIEW"
    if normalized_trend == "BEARISH":
        return "BEARISH / NO NEW BUY" if trade_policy_for_horizon(horizon)["long_term"] else "SELL REVIEW"
    if normalized_trend == "DATA UNAVAILABLE" or normalized_trend == "INSUFFICIENT_DATA":
        return "WAIT / DATA"
    return "NEUTRAL / NO TRADE"


def evaluate_equity_intelligence_scan(symbols, intraday_data, daily_data, extrema_order, interval=None, signal_now=None):
    """Evaluate one batch with the same ten independent equity checks used by the UI.

    The result keeps every requested constituent.  A missing public response is
    a visible unavailable row, never a zero price or a stale substitute.
    """
    rows = []
    checklists = {}
    current_prices = {}
    for symbol in list(symbols or []):
        clean_symbol = str(symbol).removesuffix(".NS").removesuffix(".BO")
        try:
            frame = _market_frame_for_symbol(intraday_data, symbol)
            if len(frame) < 25:
                rows.append(_unavailable_equity_scan_row(symbol, "Public chart feed has fewer than 25 usable bars."))
                continue

            close = pd.to_numeric(frame["Close"], errors="coerce")
            high = pd.to_numeric(frame["High"], errors="coerce")
            low = pd.to_numeric(frame["Low"], errors="coerce")
            volume = pd.to_numeric(frame["Volume"], errors="coerce")
            open_series = pd.to_numeric(frame["Open"], errors="coerce")
            if close.isna().any() or high.isna().any() or low.isna().any() or open_series.isna().any():
                rows.append(_unavailable_equity_scan_row(symbol))
                continue

            ema20 = close.ewm(span=20, adjust=False).mean()
            ema50 = close.ewm(span=50, adjust=False).mean()
            delta = close.diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss.replace(0, np.nan)
            rsi = 100 - (100 / (1 + rs))

            true_range = pd.concat([
                high - low,
                (high - close.shift(1)).abs(),
                (low - close.shift(1)).abs(),
            ], axis=1).max(axis=1)
            atr_candidate = _as_float(true_range.rolling(14).mean().iloc[-1])
            ltp = float(close.iloc[-1])
            atr_value = atr_candidate if atr_candidate is not None and atr_candidate > 0 else max(ltp * 0.015, 0.01)

            typical_price = (high + low + close) / 3.0
            session_groups = pd.DatetimeIndex(frame.index).date
            cumulative_volume = volume.fillna(0).groupby(session_groups).cumsum()
            vwap_series = (typical_price * volume.fillna(0)).groupby(session_groups).cumsum() / cumulative_volume.replace(0, np.nan)
            vwap_value = _as_float(vwap_series.iloc[-1])
            volume_available = bool(volume.fillna(0).gt(0).any())

            session_frame = frame
            try:
                session_index = pd.to_datetime(frame.index, errors="coerce")
                latest_session = session_index[-1].date()
                same_session = session_index.date == latest_session
                if same_session.sum() >= 1:
                    session_frame = frame.loc[same_session]
            except (AttributeError, IndexError, TypeError, ValueError):
                pass
            opening_bars = min(3, len(session_frame))
            orb_high = float(pd.to_numeric(session_frame["High"], errors="coerce").iloc[:opening_bars].max())
            orb_low = float(pd.to_numeric(session_frame["Low"], errors="coerce").iloc[:opening_bars].min())
            session_open = float(pd.to_numeric(session_frame["Open"], errors="coerce").iloc[0])

            previous = float(close.iloc[-2])
            last_bar_change = ((ltp - previous) / previous * 100.0) if previous > 0 else np.nan
            session_change = ((ltp - session_open) / session_open * 100.0) if session_open > 0 else np.nan
            rsi_value = _as_float(rsi.iloc[-1])
            ema20_value = float(ema20.iloc[-1])
            ema50_value = float(ema50.iloc[-1])
            current_volume = _as_float(volume.iloc[-1])
            average_volume = _as_float(volume.iloc[-20:].mean()) if len(volume) >= 20 else current_volume
            support = float(low.iloc[-int(extrema_order):].min())
            resistance = float(high.iloc[-int(extrema_order):].max())

            macro_ema = ema50_value
            macro_detail = f"Intraday 50 EMA fallback: ₹{ema50_value:.2f}"
            daily_frame = _market_frame_for_symbol(daily_data, symbol)
            if len(daily_frame) >= 50:
                daily_close = pd.to_numeric(daily_frame["Close"], errors="coerce").dropna()
                if len(daily_close) >= 50:
                    macro_ema = float(daily_close.ewm(span=50, adjust=False).mean().iloc[-1])
                    macro_detail = f"Daily 50 EMA: ₹{macro_ema:.2f}"

            price_buffer = max(atr_value * 0.10, ltp * 0.001)
            history = frame.iloc[-min(20, len(frame) - 1) - 1:-1]
            prior_resistance = float(pd.to_numeric(history["High"], errors="coerce").max())
            prior_support = float(pd.to_numeric(history["Low"], errors="coerce").min())
            macro_state = directional_state(ltp > macro_ema + price_buffer, ltp < macro_ema - price_buffer)
            rsi_state = "UNAVAILABLE" if rsi_value is None else directional_state(
                55.0 <= rsi_value <= 70.0, 30.0 <= rsi_value <= 45.0
            )
            checks = [
                ("1. MTF Macro Filter", macro_state, macro_detail),
                ("2. VWAP Baseline", "UNAVAILABLE" if vwap_value is None else directional_state(ltp > vwap_value + price_buffer, ltp < vwap_value - price_buffer), "VWAP unavailable" if vwap_value is None else f"VWAP: ₹{vwap_value:.2f}"),
                ("3. Opening-Range Break", directional_state(ltp > orb_high + price_buffer, ltp < orb_low - price_buffer), f"ORB: ₹{orb_low:.2f}–₹{orb_high:.2f}"),
                ("4. Price vs 20 EMA", directional_state(ltp > ema20_value + price_buffer, ltp < ema20_value - price_buffer), f"20 EMA: ₹{ema20_value:.2f}"),
                ("5. EMA Alignment", directional_state(ema20_value > ema50_value + price_buffer, ema20_value < ema50_value - price_buffer), f"20 / 50 EMA: ₹{ema20_value:.2f} / ₹{ema50_value:.2f}"),
                ("6. RSI Momentum Corridor", rsi_state, "RSI unavailable" if rsi_value is None else f"RSI @ {rsi_value:.1f}; middle range is neutral"),
                ("7. Volume Expansion", "UNAVAILABLE" if not volume_available or not average_volume or current_volume is None else directional_state(current_volume >= average_volume * 1.25 and ltp > previous, current_volume >= average_volume * 1.25 and ltp < previous), "Volume unavailable" if not volume_available or not average_volume or current_volume is None else f"{current_volume:,.0f} vs {average_volume:,.0f}"),
                ("8. Session Momentum", directional_state(ltp > session_open + price_buffer, ltp < session_open - price_buffer), f"Session open: ₹{session_open:.2f} ({session_change:+.2f}%)"),
                ("9. Prior-Range Break", directional_state(ltp > prior_resistance + price_buffer, ltp < prior_support - price_buffer), f"Prior range: ₹{prior_support:.2f}–₹{prior_resistance:.2f}"),
                ("10. Short Momentum", directional_state(ltp > float(close.iloc[-4]) + price_buffer, ltp < float(close.iloc[-4]) - price_buffer), f"3-bar change: ₹{ltp - float(close.iloc[-4]):+.2f}"),
            ]
            evidence_summary = summarize_directional_evidence(checks, min_usable=7, threshold=6, minimum_lead=2)
            buy_score = evidence_summary["buy_score"]
            sell_score = evidence_summary["sell_score"]
            if rsi_value is not None and (rsi_value >= 74 or rsi_value <= 26):
                setup = "REVERSAL_WATCH"
            elif evidence_summary["regime"] == "BULLISH":
                setup = "BUY_SETUP"
            elif evidence_summary["regime"] == "BEARISH":
                setup = "BEARISH_SETUP"
            else:
                setup = "CONSOLIDATION"

            checklist_key = clean_symbol if clean_symbol not in checklists else str(symbol)
            current_prices[clean_symbol] = ltp
            checklists[checklist_key] = {
                "sym": symbol,
                "ltp": ltp,
                "chg": last_bar_change,
                "session_chg": session_change,
                "rsi": rsi_value,
                "vwap": vwap_value,
                "orb_high": orb_high,
                "orb_low": orb_low,
                "atr": atr_value,
                "support": support,
                "resistance": resistance,
                "checks": checks,
                "buy_score": buy_score,
                "sell_score": sell_score,
                "evidence_summary": evidence_summary,
                "setup": setup,
                "candle_time": pd.Timestamp(frame.index[-1]).isoformat(),
                "signal": detect_price_action_setup(frame, interval, signal_now),
            }
            rows.append({
                "Symbol": clean_symbol,
                "Price": round(ltp, 2),
                "Chg%": round(last_bar_change, 2),
                "Session Δ%": round(session_change, 2),
                "Trend": evidence_summary["regime"],
                "VWAP": round(vwap_value, 2) if vwap_value is not None else np.nan,
                "RSI": round(rsi_value, 1) if rsi_value is not None else np.nan,
                "Buy": f"{buy_score}/10",
                "Sell": f"{sell_score}/10",
                "Setup": setup,
                "Data status": "PUBLIC CHART FEED · MAY BE DELAYED",
            })
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            rows.append(_unavailable_equity_scan_row(symbol))
    return pd.DataFrame(rows, columns=EQUITY_SCAN_COLUMNS), checklists, current_prices


def _fetch_equity_and_daily_data(symbols, period, interval):
    """Fetch the delayed public intraday and daily chart batches once per cache key."""
    requested = list(symbols or [])
    if not requested:
        return requested, pd.DataFrame(), pd.DataFrame(), "No verified constituent list is available.", ""
    intraday_data, intraday_message = download_public_chart_data(
        requested, period=period, interval=interval, group_by="ticker", progress=False, threads=True,
    )
    daily_data, daily_message = download_public_chart_data(
        requested, period="1y", interval="1d", group_by="ticker", progress=False, threads=True,
    )
    return requested, intraday_data, daily_data, intraday_message, daily_message


# Do not create a bare-runtime Streamlit cache while executing the standalone
# network-free self-test.  The app uses the cached version in normal operation.
if "--self-test" in sys.argv:
    fetch_equity_and_daily_data = _fetch_equity_and_daily_data
else:
    fetch_equity_and_daily_data = st.cache_data(ttl=180, show_spinner=False)(_fetch_equity_and_daily_data)


def validate_equity_trade_policy(horizon, action, order_kind, product_type):
    """Prevent a UI state from bypassing the long-term new-position policy."""
    policy = trade_policy_for_horizon(horizon)
    side = str(action).upper()
    kind = str(order_kind).upper()
    product = str(product_type).upper()
    if side not in policy["allowed_actions"]:
        return False, "Long Term mode does not open new SELL/short positions."
    if kind not in policy["allowed_order_kinds"]:
        return False, "Long Term mode uses delivery market orders only; ROBO is unavailable."
    if policy["required_product"] and product != policy["required_product"]:
        return False, "Long Term mode requires the DELIVERY product."
    return True, ""


def normalise_broker_option_candles(candles, session_date=None):
    """Keep only real, current IST-session broker candles; never fill missing data."""
    required = ["time", "Open", "High", "Low", "Close", "Volume"]
    frame = pd.DataFrame(candles or [], columns=required)
    if frame.empty:
        return frame
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
    if getattr(frame["time"].dt, "tz", None) is None:
        frame["time"] = frame["time"].dt.tz_localize(IST_TIMEZONE, ambiguous="NaT", nonexistent="NaT")
    else:
        frame["time"] = frame["time"].dt.tz_convert(IST_TIMEZONE)
    for column in required[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["time", "Open", "High", "Low", "Close"]).sort_values("time")
    session_day = session_date or ist_now().date()
    minutes = frame["time"].dt.hour * 60 + frame["time"].dt.minute
    return frame.loc[
        (frame["time"].dt.date == session_day) & minutes.between(9 * 60 + 15, 15 * 60 + 30)
    ].copy()


def evaluate_intraday_option_evidence(candle_frame, instrument_label="Premium"):
    """Calculate intraday-only evidence from an authenticated broker candle set."""
    label = str(instrument_label or "Premium").strip() or "Premium"
    if not isinstance(candle_frame, pd.DataFrame) or len(candle_frame) < 21:
        return {
            "checks": [],
            "summary": {
                "buy_score": 0, "sell_score": 0, "neutral_count": 0,
                "unavailable_count": 0, "usable_check_count": 0,
                "total_check_count": 10, "regime": "INSUFFICIENT_DATA",
            },
            "message": f"At least 21 current-session broker candles are required for the 10-point intraday {label.lower()} scan.",
        }
    frame = candle_frame.sort_values("time").copy()
    close, high, low, open_, volume = (frame[name] for name in ("Close", "High", "Low", "Open", "Volume"))
    ema9 = close.ewm(span=9, adjust=False).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()
    previous_close = float(close.iloc[-2])
    ltp = float(close.iloc[-1])
    true_range = pd.concat([
        high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = float(true_range.rolling(14).mean().iloc[-1]) if pd.notna(true_range.rolling(14).mean().iloc[-1]) else 0.0
    buffer = max(atr * 0.10, ltp * 0.001)
    typical = (high + low + close) / 3.0
    cumulative_volume = volume.fillna(0).cumsum()
    vwap = (typical * volume.fillna(0)).cumsum() / cumulative_volume.replace(0, np.nan)
    vwap_value = float(vwap.iloc[-1]) if pd.notna(vwap.iloc[-1]) else None
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    if pd.isna(gain.iloc[-1]) or pd.isna(loss.iloc[-1]):
        rsi_value = None
    elif loss.iloc[-1] == 0 and gain.iloc[-1] > 0:
        rsi_value = 100.0
    elif gain.iloc[-1] == 0 and loss.iloc[-1] > 0:
        rsi_value = 0.0
    elif gain.iloc[-1] == 0 and loss.iloc[-1] == 0:
        rsi_value = 50.0
    else:
        rsi_value = float(100 - (100 / (1 + gain.iloc[-1] / loss.iloc[-1])))
    orb_bars = min(3, len(frame) - 1)
    orb_high = float(high.iloc[:orb_bars].max())
    orb_low = float(low.iloc[:orb_bars].min())
    history = frame.iloc[-min(20, len(frame) - 1) - 1:-1]
    prior_resistance = float(history["High"].max())
    prior_support = float(history["Low"].min())
    volume_available = bool(volume.fillna(0).gt(0).any())
    average_volume = float(volume.iloc[-21:-1].mean()) if volume_available else None
    latest_volume = float(volume.iloc[-1]) if volume_available and pd.notna(volume.iloc[-1]) else None
    fast_ema_reference = float(ema9.iloc[-4])
    short_premium_reference = float(close.iloc[-4])
    bar_change_pct = ((ltp - previous_close) / previous_close * 100.0) if previous_close > 0 else None
    checks = [
        ("1. Session VWAP Baseline", "UNAVAILABLE" if vwap_value is None else directional_state(ltp > vwap_value + buffer, ltp < vwap_value - buffer), "VWAP unavailable" if vwap_value is None else f"{label} ₹{ltp:.2f} vs VWAP ₹{vwap_value:.2f}"),
        ("2. Fast/Slow EMA Alignment", directional_state(float(ema9.iloc[-1]) > float(ema20.iloc[-1]) + buffer, float(ema9.iloc[-1]) < float(ema20.iloc[-1]) - buffer), f"EMA 9 ₹{ema9.iloc[-1]:.2f} · EMA 20 ₹{ema20.iloc[-1]:.2f}"),
        ("3. Premium vs EMA 20", directional_state(ltp > float(ema20.iloc[-1]) + buffer, ltp < float(ema20.iloc[-1]) - buffer), f"EMA 20 ₹{ema20.iloc[-1]:.2f}"),
        ("4. Fast EMA Slope", directional_state(float(ema9.iloc[-1]) > fast_ema_reference + buffer, float(ema9.iloc[-1]) < fast_ema_reference - buffer), f"EMA 9 ₹{fast_ema_reference:.2f} → ₹{ema9.iloc[-1]:.2f}"),
        ("5. Opening-Range Break", directional_state(ltp > orb_high + buffer, ltp < orb_low - buffer), f"First {orb_bars} bars ₹{orb_low:.2f}–₹{orb_high:.2f}"),
        ("6. RSI Momentum Corridor", "UNAVAILABLE" if rsi_value is None else directional_state(55.0 <= rsi_value <= 70.0, 30.0 <= rsi_value <= 45.0), "RSI unavailable" if rsi_value is None else f"RSI {rsi_value:.1f}; middle range is neutral"),
        ("7. Volume Expansion", "UNAVAILABLE" if latest_volume is None or not average_volume else directional_state(latest_volume >= average_volume * 1.25 and ltp > previous_close, latest_volume >= average_volume * 1.25 and ltp < previous_close), "Broker volume unavailable" if latest_volume is None or not average_volume else f"{latest_volume:,.0f} vs {average_volume:,.0f}"),
        ("8. Session Momentum", directional_state(ltp > float(open_.iloc[0]) + buffer, ltp < float(open_.iloc[0]) - buffer), f"Session open ₹{open_.iloc[0]:.2f}"),
        ("9. Prior-Range Break", directional_state(ltp > prior_resistance + buffer, ltp < prior_support - buffer), f"Prior range ₹{prior_support:.2f}–₹{prior_resistance:.2f}"),
        (f"10. Short {label} Momentum", directional_state(ltp > short_premium_reference + buffer, ltp < short_premium_reference - buffer), f"3-bar {label.lower()} change ₹{ltp - short_premium_reference:+.2f}"),
    ]
    return {
        "checks": checks,
        "summary": summarize_directional_evidence(checks, min_usable=7, threshold=6, minimum_lead=2),
        "message": "",
        "metrics": {
            "ltp": ltp, "vwap": vwap_value, "ema9": float(ema9.iloc[-1]), "ema20": float(ema20.iloc[-1]),
            "rsi": rsi_value, "orb_high": orb_high, "orb_low": orb_low,
            "support": prior_support, "resistance": prior_resistance,
            "bar_change_pct": bar_change_pct, "candle_count": len(frame),
            "latest_volume": latest_volume, "average_volume": average_volume,
        },
    }


def build_fo_trade_gate(option_snapshot, candle_snapshot=None, option_evidence=None):
    """Return a conservative selected-option long-entry gate from broker-backed data only."""
    snapshot = option_snapshot if isinstance(option_snapshot, dict) else {}
    candles = candle_snapshot if isinstance(candle_snapshot, dict) else {}
    evidence = option_evidence if isinstance(option_evidence, dict) else {}
    summary = evidence.get("summary") if isinstance(evidence.get("summary"), dict) else {}
    total_checks = max(10, _as_int(summary.get("total_check_count")) or 10)
    usable_checks = max(0, _as_int(summary.get("usable_check_count")) or 0)
    bullish = max(0, _as_int(summary.get("buy_score")) or 0)
    bearish = max(0, _as_int(summary.get("sell_score")) or 0)
    neutral = max(0, _as_int(summary.get("neutral_count")) or 0)
    regime = str(summary.get("regime") or "INSUFFICIENT_DATA")
    candle_message = str(candles.get("message") or evidence.get("message") or "Current-session broker candles are required.")

    if not snapshot.get("ok"):
        return {
            "decision": "WAIT / NEUTRAL",
            "decision_note": "NOT A BUY",
            "conditions": f"0 / {total_checks} verified",
            "condition_detail": "Broker candles are not verified yet",
            "risk_status": "LOCKED",
            "message": "Risk, entry, stop, target, and BUY are locked until Angel One returns this contract's real LTP and current-session candles.",
            "allow_risk_preview": False,
            "allow_long_entry": False,
            "tone": "fo-gate-wait",
        }

    condition_detail = f"Bullish {bullish} · Bearish {bearish} · Neutral {neutral}"
    if not candles.get("ok") or regime == "INSUFFICIENT_DATA":
        return {
            "decision": "WAIT / NEUTRAL",
            "decision_note": "NOT A BUY",
            "conditions": f"{usable_checks} / {total_checks} usable",
            "condition_detail": condition_detail,
            "risk_status": "PLAN ONLY",
            "message": f"A real broker LTP is available, but the long entry stays locked until at least 21 current-session candles produce a complete intraday check. {candle_message}",
            "allow_risk_preview": True,
            "allow_long_entry": False,
            "tone": "fo-gate-wait",
        }

    if regime == "BULLISH":
        return {
            "decision": "CONDITIONAL LONG REVIEW",
            "decision_note": "NOT AN AUTOMATIC BUY",
            "conditions": f"{usable_checks} / {total_checks} usable",
            "condition_detail": condition_detail,
            "risk_status": "RISK PLAN READY",
            "message": "The selected option premium passed the intraday evidence gate. Review the broker quote, quantity, stop, target, and order confirmation yourself before any entry.",
            "allow_risk_preview": True,
            "allow_long_entry": True,
            "tone": "fo-gate-bull",
        }

    if regime == "BEARISH":
        return {
            "decision": "BEARISH / NO LONG BUY",
            "decision_note": "BUY LOCKED",
            "conditions": f"{usable_checks} / {total_checks} usable",
            "condition_detail": condition_detail,
            "risk_status": "PLAN ONLY",
            "message": "The selected option premium has bearish evidence. A long-option BUY is locked; SELL values remain illustrative and live short options still require a separate margin/preflight check.",
            "allow_risk_preview": True,
            "allow_long_entry": False,
            "tone": "fo-gate-bear",
        }

    return {
        "decision": "NEUTRAL / NO TRADE",
        "decision_note": "BUY LOCKED",
        "conditions": f"{usable_checks} / {total_checks} usable",
        "condition_detail": condition_detail,
        "risk_status": "PLAN ONLY",
        "message": "The selected option premium is mixed or sideways. This is a wait state, not a BUY signal; risk values are shown only for planning.",
        "allow_risk_preview": True,
        "allow_long_entry": False,
        "tone": "fo-gate-wait",
    }


def option_setup_from_evidence(option_evidence):
    """Classify a broker-candle premium scan without treating neutral as a sell signal."""
    evidence = option_evidence if isinstance(option_evidence, dict) else {}
    summary = evidence.get("summary") if isinstance(evidence.get("summary"), dict) else {}
    regime = str(summary.get("regime") or "INSUFFICIENT_DATA")
    metrics = evidence.get("metrics") if isinstance(evidence.get("metrics"), dict) else {}
    rsi_value = _as_float(metrics.get("rsi"))
    if regime == "INSUFFICIENT_DATA":
        return "DATA_REQUIRED"
    if rsi_value is not None and (rsi_value >= 74.0 or rsi_value <= 26.0):
        return "REVERSAL_WATCH"
    if regime == "BULLISH":
        return "BUY_SETUP"
    if regime == "BEARISH":
        return "BEARISH_SETUP"
    return "CONSOLIDATION"


def _fo_currency(value):
    number = _as_float(value)
    return "—" if number is None else f"₹{number:,.2f}"


def _fo_percent(value):
    number = _as_float(value)
    return "—" if number is None else f"{number:+.2f}%"


def build_fo_intelligence_row(contract, option_snapshot, candle_snapshot, option_evidence):
    """Create one selected-contract row using only values received from Angel One."""
    selected_contract = contract if isinstance(contract, dict) else {}
    snapshot = option_snapshot if isinstance(option_snapshot, dict) else {}
    candles = candle_snapshot if isinstance(candle_snapshot, dict) else {}
    evidence = option_evidence if isinstance(option_evidence, dict) else {}
    summary = evidence.get("summary") if isinstance(evidence.get("summary"), dict) else {}
    metrics = evidence.get("metrics") if isinstance(evidence.get("metrics"), dict) else {}
    quote_ok = bool(snapshot.get("ok"))
    complete_candles = bool(candles.get("ok")) and str(summary.get("regime")) != "INSUFFICIENT_DATA"
    ltp = _as_float(snapshot.get("ltp"))
    broker_close = _as_float(snapshot.get("close"))
    quote_change = ((ltp - broker_close) / broker_close * 100.0) if ltp is not None and broker_close and broker_close > 0 else None
    setup = option_setup_from_evidence(evidence)

    if not quote_ok:
        trend, setup, verification = "WAIT / NEUTRAL", "DATA_REQUIRED", "Angel One LTP required"
    elif not complete_candles:
        trend, setup, verification = "WAIT / DATA REQUIRED", "DATA_REQUIRED", "LTP received; current-session candles incomplete"
    else:
        trend = str(summary.get("regime") or "SIDEWAYS / NO TRADE")
        candle_count = _as_int(metrics.get("candle_count")) or 0
        verification = f"LTP + {candle_count} current-session broker candles"

    score_available = complete_candles
    return {
        "Contract": str(selected_contract.get("symbol") or "—"),
        "Premium": _fo_currency(ltp if quote_ok else None),
        "LTP Chg%": _fo_percent(quote_change),
        "Trend": trend,
        "VWAP": _fo_currency(metrics.get("vwap") if complete_candles else None),
        "RSI": "—" if not complete_candles or _as_float(metrics.get("rsi")) is None else f"{_as_float(metrics.get('rsi')):.1f}",
        "Buy": f"{_as_int(summary.get('buy_score')) or 0}/10" if score_available else "— / 10",
        "Sell": f"{_as_int(summary.get('sell_score')) or 0}/10" if score_available else "— / 10",
        "Setup": setup,
        "Verification": verification,
    }


def choose_fo_contract(contracts, selected_token=None):
    """Return a stable visible F&O contract; never infer CE/PE or a strike."""
    visible_contracts = [row for row in contracts or [] if isinstance(row, dict) and row.get("token")]
    if not visible_contracts:
        return None
    wanted_token = str(selected_token or "")
    return next((row for row in visible_contracts if str(row.get("token")) == wanted_token), visible_contracts[0])


def build_fo_contract_watchlist_rows(contracts, selected_contract, option_snapshot, candle_snapshot, option_evidence):
    """Build a metadata-first F&O table without fabricating unselected premiums.

    Angel One's master verifies every row's contract identity.  Only the one
    selected token can show premium/candle-derived fields because the app does
    not request a deceptive all-contract quote or candle scan.
    """
    selected_token = str((selected_contract or {}).get("token") or "")
    selected_intelligence = build_fo_intelligence_row(
        selected_contract, option_snapshot, candle_snapshot, option_evidence,
    ) if selected_token else {}
    rows = []
    for contract in contracts or []:
        token = str(contract.get("token") or "")
        selected = bool(selected_token and token == selected_token)
        rows.append({
            "Contract": str(contract.get("symbol") or "—"),
            "Expiry": str(contract.get("expiry_label") or contract.get("expiry_raw") or "—"),
            "Side": "CALL (CE)" if contract.get("side") == "CE" else "PUT (PE)" if contract.get("side") == "PE" else "—",
            "Strike": _as_float(contract.get("strike")),
            "Lot": _as_int(contract.get("lot_size")),
            "Tick": _as_float(contract.get("tick_size")),
            "Premium": selected_intelligence.get("Premium", "—") if selected else "—",
            "Trend": selected_intelligence.get("Trend", "—") if selected else "—",
            "Buy": selected_intelligence.get("Buy", "— / 10") if selected else "— / 10",
            "Sell": selected_intelligence.get("Sell", "— / 10") if selected else "— / 10",
            "Setup": selected_intelligence.get("Setup", "—") if selected else "—",
            "Data status": selected_intelligence.get("Verification", "—") if selected else "MASTER VERIFIED · SELECT TO LOAD",
        })
    return rows


def build_fo_verification_rows(contract, option_snapshot, candle_snapshot, option_evidence, selected_greeks=None):
    """Expose what is and is not verified for the selected F&O contract."""
    selected_contract = contract if isinstance(contract, dict) else {}
    snapshot = option_snapshot if isinstance(option_snapshot, dict) else {}
    candles = candle_snapshot if isinstance(candle_snapshot, dict) else {}
    evidence = option_evidence if isinstance(option_evidence, dict) else {}
    summary = evidence.get("summary") if isinstance(evidence.get("summary"), dict) else {}
    metrics = evidence.get("metrics") if isinstance(evidence.get("metrics"), dict) else {}
    candle_count = _as_int(metrics.get("candle_count")) or 0
    complete_evidence = bool(candles.get("ok")) and str(summary.get("regime")) != "INSUFFICIENT_DATA"
    greeks = selected_greeks if isinstance(selected_greeks, dict) else {}
    metadata_ok = bool(selected_contract.get("token") and selected_contract.get("symbol") and selected_contract.get("lot_size"))
    return [
        {
            "Verification": "Selected contract metadata",
            "State": "VERIFIED" if metadata_ok else "UNAVAILABLE",
            "Detail": "Angel One NFO instrument master" if metadata_ok else "A valid broker-listed contract is required.",
        },
        {
            "Verification": "Selected option premium (LTP)",
            "State": "VERIFIED" if snapshot.get("ok") else "UNAVAILABLE",
            "Detail": str(snapshot.get("source") or "Angel One SmartAPI LTP") if snapshot.get("ok") else str(snapshot.get("message") or "Angel One quote required."),
        },
        {
            "Verification": "Current-session option candles",
            "State": "VERIFIED" if complete_evidence else "INCOMPLETE",
            "Detail": f"{candle_count} usable broker candles · current IST session" if complete_evidence else str(candles.get("message") or evidence.get("message") or "At least 21 current-session broker candles are required."),
        },
        {
            "Verification": "10-point selected-premium scan",
            "State": "VERIFIED" if complete_evidence else "INCOMPLETE",
            "Detail": f"Buy {_as_int(summary.get('buy_score')) or 0}/10 · Sell {_as_int(summary.get('sell_score')) or 0}/10 · {summary.get('regime')}" if complete_evidence else "No score is inferred while candle evidence is incomplete.",
        },
        {
            "Verification": "Raw Greeks / OI",
            "State": "VERIFIED RAW" if greeks else "NOT RETURNED",
            "Detail": ", ".join(str(key) for key in greeks) if greeks else "Only shown if Angel One returns a safely matched selected-contract record.",
        },
        {
            "Verification": "Underlying direction / chain flow",
            "State": "NOT VERIFIED HERE",
            "Detail": "This screen does not claim NIFTY/stock direction, PCR, OI buildup, bid/ask depth, or option-chain flow without a separate broker source.",
        },
    ]


def parse_official_rss_items(payload, source_name, source_url, limit=10):
    """Parse headline metadata only; no publisher article text is copied into the app."""
    try:
        root = ET.fromstring(payload)
    except (ET.ParseError, TypeError, ValueError) as exc:
        raise ValueError(f"{source_name} did not return a readable RSS/Atom feed.") from exc
    items = []
    for element in root.findall(".//item") + root.findall(".//{*}entry"):
        title = (element.findtext("title") or element.findtext("{*}title") or "").strip()
        link = (element.findtext("link") or "").strip()
        atom_link = element.find("{*}link")
        if atom_link is not None and atom_link.get("href"):
            link = atom_link.get("href").strip()
        published = (
            element.findtext("pubDate") or element.findtext("published") or
            element.findtext("updated") or element.findtext("{*}published") or element.findtext("{*}updated") or ""
        ).strip()
        if title:
            items.append({"source": source_name, "source_url": source_url, "title": title, "link": link or source_url, "published": published})
        if len(items) >= limit:
            break
    return items


def fetch_official_news_feeds(source_configs, urlopen_fn=None, limit_per_source=8):
    """Fetch source-attributed official headlines and preserve source-specific failures."""
    opener = urlopen_fn or urllib.request.urlopen
    items, errors = [], []
    for source in source_configs:
        name, url = str(source["name"]), str(source["url"])
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "MahiTrading/1.0", "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml,*/*"})
            with opener(request, timeout=12) as response:
                payload = response.read()
            items.extend(parse_official_rss_items(payload, name, url, limit=limit_per_source))
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    return {
        "items": items,
        "errors": errors,
        "fetched_at": ist_now().strftime("%Y-%m-%d %H:%M:%S %Z"),
    }


def brief_published_time(value, source=None):
    """An undated headline never becomes today's news."""
    from email.utils import parsedate_to_datetime

    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(str(value))
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = pd.Timestamp(value).to_pydatetime()
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        # These Indian official feeds sometimes omit an offset. Keep the
        # explicit assumption visible next to the hint; other feeds fail closed.
        if source not in {"RBI", "SEBI", "PIB"}:
            return None
        parsed = parsed.replace(tzinfo=IST_TIMEZONE)
    return parsed.astimezone(IST_TIMEZONE)


def classify_market_headline(title):
    """Conservative headline cues, not article summaries or trading predictions."""
    clean = re.sub(r"\s+", " ", html.unescape(str(title))).strip()
    lower = clean.lower()
    relevant = bool(re.search(
        r"\b(rbi|repo|inflation|gdp|monetary|liquidity|interest rate|crude|oil|"
        r"rupee|budget|tariff|exports?|imports?|sebi|board meeting|stock|equity|"
        r"securities|market|banking|fiscal|industrial production|earnings)\b", lower,
    ))
    routine = bool(re.search(
        r"recovery certificate|release order|adjudication|settlement order|"
        r"order in the matter|penalty|defaulter|public shareholding", lower,
    ))
    ambiguous = bool(re.search(
        r"\b(no|not|denies|denied|unlikely|may|might|could|expected|forecast|"
        r"proposal|proposed|whether)\b|\?", lower,
    ))
    positive = bool(re.search(
        r"\binflation\b.{0,35}\b(eases|eased|falls|fell|declines|declined|slows)\b|"
        r"\b(repo|policy|interest) rate\b.{0,25}\b(cut|cuts|reduced|reduction)\b|"
        r"\b(gdp|exports|industrial production)\b.{0,35}\b(accelerates|accelerated|expands|expanded|rises|rose)\b", lower,
    ))
    negative = bool(re.search(
        r"\binflation\b.{0,35}\b(rises|rose|accelerates|accelerated|surges|surged)\b|"
        r"\b(repo|policy|interest) rate\b.{0,25}\b(hike|hikes|raised|increase)\b|"
        r"\b(gdp|exports|industrial production)\b.{0,35}\b(contracts|contracted|falls|fell|shrinks|slows)\b", lower,
    ))
    category = "watch"
    if relevant and not routine and not ambiguous and positive != negative:
        category = "good" if positive else "bad"
    return {"title": clean, "category": category, "relevant": relevant and not routine}


def build_brief_market_rows(raw, market_symbols):
    """Keep each daily observation tied to its own symbol and source date."""
    rows = []
    if not isinstance(raw, pd.DataFrame) or raw.empty:
        return rows
    for name, symbol in market_symbols.items():
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                frame = raw[symbol] if symbol in raw.columns.get_level_values(0) else raw.xs(symbol, axis=1, level=1)
            elif len(market_symbols) == 1:
                frame = raw
            else:
                continue
            close = pd.to_numeric(frame["Close"], errors="coerce").dropna().sort_index()
            close = close[~close.index.duplicated(keep="last")]
            if len(close) < 2:
                continue
            latest, previous = float(close.iloc[-1]), float(close.iloc[-2])
            if not np.isfinite([latest, previous]).all() or min(latest, previous) <= 0:
                continue
            source_date = pd.Timestamp(close.index[-1]).date()
            rows.append({
                "Market": name, "Last price": latest, "Change %": (latest / previous - 1.0) * 100,
                "Source session": source_date.isoformat(), "source_date": source_date,
                "link": f"https://finance.yahoo.com/quote/{quote(symbol, safe='')}/",
            })
        except (KeyError, TypeError, ValueError, IndexError):
            continue
    return rows


def build_market_quick_brief(news_items, market_rows, now=None):
    """Short, traceable hints; missing or old sources remain explicit."""
    current = now or ist_now()
    current = current.replace(tzinfo=IST_TIMEZONE) if current.tzinfo is None else current.astimezone(IST_TIMEZONE)
    groups = {"good": [], "bad": [], "watch": []}
    seen, dated_news = set(), []
    old_news = 0
    for item in news_items or []:
        cue = classify_market_headline(item.get("title", ""))
        identity = cue["title"].casefold()
        if not identity or identity in seen:
            continue
        seen.add(identity)
        published = brief_published_time(item.get("published"), item.get("source"))
        if not published or not timedelta(0) <= current - published <= timedelta(hours=72):
            old_news += 1
            continue
        if cue["relevant"]:
            dated_news.append((published, item, cue))
    for published, item, cue in sorted(dated_news, key=lambda row: row[0], reverse=True):
        words = cue["title"].split()
        short_title = " ".join(words[:19]) + ("…" if len(words) > 19 else "")
        groups[cue["category"]].append({
            "text": short_title,
            "detail": f"{item.get('source', 'Source')} · {published.strftime('%d %b %H:%M IST')}" + (" (IST assumed; feed omitted timezone)" if brief_published_time(item.get("published")) is None else ""),
            "link": item.get("link") or item.get("source_url") or "",
        })
    recent_markets = []
    for row in market_rows or []:
        age_days = (current.date() - row["source_date"]).days
        if not 0 <= age_days <= 4:
            continue
        recent_markets.append(row)
        change = row["Change %"]
        if abs(change) < 0.1:
            continue
        category = "good" if change > 0 else "bad"
        groups[category].append({
            "text": f"{row['Market']} {'up' if change > 0 else 'down'} {abs(change):.2f}% in its latest daily snapshot.",
            "detail": f"Public chart · session {row['Source session']}", "link": row["link"],
        })
    indian_names = {"NIFTY 50", "BANKNIFTY", "SENSEX"}
    indian = [row for row in recent_markets if row["Market"] in indian_names]
    if len(indian) < 2 or len({row["source_date"] for row in indian}) != 1:
        mood, reason = "Not enough data", "Need matching recent Indian index sessions to describe the market."
    else:
        up = sum(row["Change %"] >= 0.1 for row in indian)
        down = sum(row["Change %"] <= -0.1 for row in indian)
        mood = "Positive" if up == len(indian) else "Cautious" if down == len(indian) else "Mixed"
        reason = f"{up} Indian indices up · {down} down · {len(indian) - up - down} flat, session {indian[0]['Source session']}."
    return {"groups": {key: value[:3] for key, value in groups.items()}, "mood": mood, "reason": reason, "older_news": old_news}


def render_brief_hint(hint):
    """Escape publisher text; only ordinary web links are rendered."""
    link = str(hint.get("link", ""))
    safe_link = link if re.match(r"^https?://", link, re.I) else ""
    label = html.escape(str(hint["text"]))
    linked = f'<a href="{html.escape(safe_link, quote=True)}" target="_blank" rel="noopener noreferrer" style="color:inherit;text-decoration:none;">{label} ↗</a>' if safe_link else label
    st.markdown(
        f"<div style='padding:10px 0;border-bottom:1px solid #ffffff16;font-size:14px;line-height:1.45'>{linked}"
        f"<div style='color:#94a3b8;font-size:11px;margin-top:5px'>{html.escape(str(hint.get('detail', '')))}</div></div>",
        unsafe_allow_html=True,
    )


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
    """Request the broker LTP for exactly one selected broker instrument."""
    if smart_api is None:
        return {"ok": False, "state": "disconnected", "message": "Angel One is not connected."}
    if not contract or not contract.get("token") or not contract.get("symbol"):
        return {"ok": False, "state": "invalid_contract", "message": "Select a valid Angel One broker instrument."}
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
    if isinstance(data, dict):
        returned_token = str(data.get("symboltoken") or data.get("symbolToken") or "")
        returned_exchange = str(data.get("exchange") or "").upper()
        if returned_token and returned_token != str(contract["token"]):
            return {"ok": False, "state": "mismatch", "message": "Broker quote belongs to a different token; refresh the instrument master."}
        if returned_exchange and returned_exchange != str(contract.get("exchange", "NFO")).upper():
            return {"ok": False, "state": "mismatch", "message": "Broker quote belongs to a different exchange."}
    ltp = _as_float(data.get("ltp") or data.get("last_traded_price")) if isinstance(data, dict) else None
    if ltp is None or ltp <= 0:
        return {"ok": False, "state": "unavailable", "message": "Broker response contains no usable LTP."}
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
    now = ist_now().replace(tzinfo=None)
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


def completed_signal_candles(frame, interval, now=None):
    """Use candle-start timestamps to exclude every still-forming bar."""
    durations = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "60m": 60,
                 "ONE_MINUTE": 1, "FIVE_MINUTE": 5, "FIFTEEN_MINUTE": 15,
                 "THIRTY_MINUTE": 30, "ONE_HOUR": 60, "1d": 1440, "1wk": 10080}
    minutes = durations.get(interval)
    if minutes is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame(), "Timeframe or candles unavailable."
    try:
        data = frame.copy()
        stamps = pd.DatetimeIndex(pd.to_datetime(data["time"] if "time" in data.columns else data.index, errors="coerce"))
        if stamps.isna().any():
            return pd.DataFrame(), "Candle timestamps are invalid."
        stamps = stamps.tz_localize(IST_TIMEZONE) if stamps.tz is None else stamps.tz_convert(IST_TIMEZONE)
        data.index = stamps
        if data.index.duplicated().any():
            return pd.DataFrame(), "Duplicate candles need a source refresh."
        data = data.sort_index()
        stamp_now = pd.Timestamp(now or ist_now())
        stamp_now = stamp_now.tz_localize(IST_TIMEZONE) if stamp_now.tzinfo is None else stamp_now.tz_convert(IST_TIMEZONE)
        if interval == "1d":
            ends = data.index.normalize() + pd.Timedelta(hours=15, minutes=30)
        elif interval == "1wk":
            ends = data.index.normalize() + pd.to_timedelta(4 - data.index.weekday, unit="D") + pd.Timedelta(hours=15, minutes=30)
        else:
            ends = data.index + pd.Timedelta(minutes=minutes)
        data = data.loc[ends <= stamp_now].copy()
        if data.empty:
            return data, "Waiting for the first completed candle."
        for col in ("Open", "High", "Low", "Close"):
            data[col] = pd.to_numeric(data[col], errors="coerce")
        valid = np.isfinite(data[["Open", "High", "Low", "Close"]]).all(axis=1)
        valid &= data[["Open", "High", "Low", "Close"]].gt(0).all(axis=1)
        valid &= (data["High"] >= data[["Open", "Close", "Low"]].max(axis=1)) & (data["Low"] <= data[["Open", "Close", "High"]].min(axis=1))
        if not valid.all():
            return pd.DataFrame(), "OHLC candles failed validation."
        if minutes < 1440:
            latest_end = data.index[-1] + pd.Timedelta(minutes=minutes)
            if data.index[-1].date() != stamp_now.date() or (stamp_now - latest_end).total_seconds() > minutes * 120:
                return data, "STALE: latest completed intraday candle is outside the current window."
        else:
            max_age = 5 if interval == "1d" else 12
            if (stamp_now - data.index[-1]).total_seconds() > max_age * 86400:
                return data, "STALE: historical source has not updated."
        return data, ""
    except (KeyError, TypeError, ValueError, AttributeError):
        return pd.DataFrame(), "Candles could not be validated."


def detect_price_action_setup(frame, interval, now=None):
    """Rules-based setup evidence; scores are not probabilities or predictions."""
    data, issue = completed_signal_candles(frame, interval, now)
    result = {"state": "DATA REQUIRED", "pattern": "No confirmed pattern", "reasons": [],
              "candle_time": data.index[-1].isoformat() if not data.empty else "", "interval": interval}
    if issue:
        result.update(state="STALE / REVIEW ONLY" if issue.startswith("STALE") else "DATA REQUIRED", reasons=[issue])
        return result
    if len(data) < 30:
        result["reasons"] = ["At least 30 completed candles are needed."]
        return result
    close = data["Close"]
    ema9, ema20 = close.ewm(span=9, adjust=False).mean(), close.ewm(span=20, adjust=False).mean()
    a, b = data.iloc[-1], data.iloc[-2]
    prior = data.iloc[-21:-1]
    ceiling, floor = float(prior.High.max()), float(prior.Low.min())
    true_range = pd.concat([data.High-data.Low, (data.High-close.shift()).abs(), (data.Low-close.shift()).abs()], axis=1).max(axis=1)
    atr = float(true_range.iloc[-15:-1].mean())
    buffer = max(atr * .1, float(a.Close) * .0005)
    bull_trend = a.Close > ema9.iloc[-1] > ema20.iloc[-1] and ema20.iloc[-1] > ema20.iloc[-4]
    bear_trend = a.Close < ema9.iloc[-1] < ema20.iloc[-1] and ema20.iloc[-1] < ema20.iloc[-4]
    bull, bear = [], []
    if a.Close > ceiling + buffer:
        bull.append("20-bar range breakout")
    if a.Close < floor - buffer:
        bear.append("20-bar range breakdown")
    if b.Close < b.Open and a.Close > a.Open and a.Open <= b.Close and a.Close >= b.Open and a.Close > b.High:
        bull.append("Bullish engulfing with high break")
    if b.Close > b.Open and a.Close < a.Open and a.Open >= b.Close and a.Close <= b.Open and a.Close < b.Low:
        bear.append("Bearish engulfing with low break")
    body = abs(float(b.Close-b.Open))
    span = float(b.High-b.Low)
    if span > 0 and body >= .1*span:
        lower, upper = min(b.Close,b.Open)-b.Low, b.High-max(b.Close,b.Open)
        if lower >= 2*body and upper <= body and a.Close > b.High and b.Low <= float(data.Low.iloc[-8:-2].min()) + buffer:
            bull.append("Hammer at support, next-candle confirmation")
        if upper >= 2*body and lower <= body and a.Close < b.Low and b.High >= float(data.High.iloc[-8:-2].max()) - buffer:
            bear.append("Shooting star at resistance, next-candle confirmation")
    previous_ceiling, previous_floor = float(data.High.iloc[-22:-2].max()), float(data.Low.iloc[-22:-2].min())
    if b.Close > previous_ceiling + buffer and abs(a.Low - previous_ceiling) <= buffer * 2 and a.Close > previous_ceiling + buffer:
        bull.append("Breakout retest held")
    if b.Close < previous_floor - buffer and abs(a.High - previous_floor) <= buffer * 2 and a.Close < previous_floor - buffer:
        bear.append("Breakdown retest held")
    volume = pd.to_numeric(data.get("Volume", pd.Series(index=data.index, dtype=float)), errors="coerce")
    average_volume = volume.iloc[-21:-1].mean()
    volume_ok = pd.notna(average_volume) and average_volume > 0 and pd.notna(volume.iloc[-1]) and volume.iloc[-1] >= 1.2 * average_volume
    direction = "BULLISH" if bull and bull_trend and not bear else "BEARISH" if bear and bear_trend and not bull else None
    reasons = []
    if direction and volume_ok:
        result.update(state=f"{direction} SETUP", pattern=" + ".join(bull if direction == "BULLISH" else bear))
        reasons = ["Completed candle confirms the pattern.", "EMA 9/20 trend agrees.", f"Volume {volume.iloc[-1]:,.0f} ≥ 1.2× prior 20-bar average {average_volume:,.0f}."]
    else:
        result["state"] = "WAIT / NO CLEAN SETUP"
        if bull or bear:
            result["pattern"] = " + ".join(bull+bear)
        reasons = ["Pattern and trend confirmation incomplete." if not direction else "Volume confirmation missing or below threshold."]
        if float(a.High-a.Low) > 0 and abs(float(a.Close-a.Open)) <= .1 * float(a.High-a.Low):
            result["pattern"] = "Doji / indecision"
            reasons = ["Small candle body shows indecision; wait for a subsequent confirmed break."]
    result["reasons"] = reasons
    return result


def render_setup_signal(signal, title="Price-action setup"):
    signal = signal or {}
    state = signal.get("state", "DATA REQUIRED")
    colour = "#34d399" if state == "BULLISH SETUP" else "#fb7185" if state == "BEARISH SETUP" else "#fbbf24"
    st.markdown(f"<div class='bottom-card'><div class='bottom-title'>{html.escape(title)}</div><strong style='color:{colour}'>{html.escape(state)}</strong><div>{html.escape(signal.get('pattern', 'No confirmed pattern'))}</div></div>", unsafe_allow_html=True)
    st.caption(" · ".join(signal.get("reasons", [])))
    if signal.get("candle_time"):
        st.caption(f"Completed source candle: {signal['candle_time']} · {signal.get('interval')}. Rules-based evidence; no measured win rate claimed.")


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
    future_expiry = (ist_now().date() + timedelta(days=7)).strftime("%d%b%Y").upper()
    master = [{"exch_seg": "NFO", "instrumenttype": "OPTIDX", "symbol": "NIFTY26SEP25000CE", "name": "NIFTY", "expiry": future_expiry, "strike": "2500000.000000", "lotsize": "65", "tick_size": "5.000000", "token": "12345"}]
    parsed = extract_option_contracts(master, "NIFTY")
    assert len(parsed) == 1 and parsed[0]["strike"] == 25000.0 and parsed[0]["lot_size"] == 65 and parsed[0]["tick_size"] == 0.05

    def fo_master_row(exchange, instrument_type, symbol, name, token, strike="", lot_size="", tick=""):
        return {
            "exch_seg": exchange, "instrumenttype": instrument_type, "symbol": symbol,
            "name": name, "token": token, "expiry": future_expiry,
            "strike": strike, "lotsize": lot_size, "tick_size": tick,
        }

    fo_master = [
        fo_master_row("NSE", "AMXIDX", "NIFTY 50", "NIFTY", "99926000"),
        fo_master_row("BSE", "AMXIDX", "SENSEX", "SENSEX", "99919000"),
        fo_master_row("NFO", "OPTIDX", "NIFTYTEST25000CE", "NIFTY", "nce1", "2500000", "65", "5"),
        fo_master_row("NFO", "OPTIDX", "NIFTYTEST25000PE", "NIFTY", "npe1", "2500000", "65", "5"),
        fo_master_row("NFO", "OPTIDX", "NIFTYTEST25100CE", "NIFTY", "nce2", "2510000", "65", "5"),
        fo_master_row("NFO", "OPTIDX", "NIFTYTEST25100PE", "NIFTY", "npe2", "2510000", "65", "5"),
        fo_master_row("BFO", "OPTIDX", "SENSEXTEST80000CE", "SENSEX", "sce1", "8000000", "10", "5"),
        fo_master_row("BFO", "OPTIDX", "SENSEXTEST80000PE", "SENSEX", "spe1", "8000000", "10", "5"),
        fo_master_row("NFO", "OPTSTK", "RELIANCETEST2500CE", "RELIANCE", "stock1", "250000", "250", "5"),
    ]
    assert set(FO_INDEX_UNIVERSE) == {"NIFTY 50", "BANKNIFTY", "FINNIFTY", "SENSEX", "NIFT Midcap"}
    nifty_contracts = resolve_fo_index_option_contracts(fo_master, "NIFTY 50")
    sensex_contracts = resolve_fo_index_option_contracts(fo_master, "SENSEX")
    assert len(nifty_contracts) == 4 and all(row["exchange"] == "NFO" and row["instrument_type"] == "OPTIDX" for row in nifty_contracts)
    assert len(sensex_contracts) == 2 and all(row["exchange"] == "BFO" for row in sensex_contracts)
    assert resolve_fo_index_option_contracts(fo_master, "NIFT Midcap") == []
    assert resolve_fo_index_spot_contract(fo_master, "NIFTY 50")["exchange"] == "NSE"
    assert resolve_fo_index_spot_contract(fo_master, "SENSEX")["exchange"] == "BSE"
    nifty_pairs = pair_fo_index_contracts(nifty_contracts)
    assert nearest_fo_chain_strike(nifty_pairs, 25062.0) == 25100.0
    assert len(fo_chain_band(nifty_pairs, 25000.0, 1)) == 2
    assert fo_contract_key(nifty_contracts[0]) != fo_contract_key(sensex_contracts[0])

    class MockMarketDataApi:
        def getMarketData(self, mode, exchange_tokens):
            assert mode == "FULL" and exchange_tokens == {"NFO": ["nce1", "npe1", "nce2", "npe2"]}
            return {
                "status": True,
                "data": {"fetched": [
                    {"exchange": "NFO", "symbolToken": "nce1", "ltp": 110.5, "close": 100.0, "percentChange": 10.5, "tradeVolume": 1234, "opnInterest": 888, "best5Data": [{"flag": "BUY", "price": 110.4}, {"flag": "SELL", "price": 110.6}]},
                    {"exchange": "NFO", "symbolToken": "npe1", "ltp": 90.5, "close": 92.0},
                    {"exchange": "NFO", "symbolToken": "nce2", "ltp": 75.0},
                    {"exchange": "NFO", "symbolToken": "npe2", "ltp": 120.0},
                ]},
            }

    chain_connected = fetch_broker_market_data(MockMarketDataApi(), nifty_contracts)
    assert chain_connected["ok"] and chain_connected["state"] == "snapshot" and chain_connected["fetched_count"] == 4
    chain_ce_quote = quote_for_fo_contract(chain_connected, nifty_pairs[25000.0]["CE"])
    assert chain_ce_quote["ltp"] == 110.5 and chain_ce_quote["bid"] == 110.4 and chain_ce_quote["ask"] == 110.6 and chain_ce_quote["open_interest"] == 888.0
    chain_disconnected = fetch_broker_market_data(None, nifty_contracts)
    assert not chain_disconnected["ok"] and chain_disconnected["state"] == "disconnected" and not chain_disconnected["quotes"]

    bullish_summary = {"regime": "BULLISH", "usable_check_count": 8}
    bearish_summary = {"regime": "BEARISH", "usable_check_count": 8}
    neutral_summary = {"regime": "SIDEWAYS / NO TRADE", "usable_check_count": 8}
    ready_option_gate = {"allow_risk_preview": True, "allow_long_entry": True, "risk_status": "RISK PLAN READY", "tone": "fo-gate-bull", "decision": "CONDITIONAL LONG REVIEW"}
    composite_ce = build_fo_composite_gate({"ok": True}, {"summary": bullish_summary}, nifty_pairs[25000.0]["CE"], ready_option_gate, {"summary": bullish_summary})
    assert composite_ce["allow_long_entry"] and composite_ce["decision"] == "CONDITIONAL CE LONG REVIEW"
    composite_sideways = build_fo_composite_gate({"ok": True}, {"summary": neutral_summary}, nifty_pairs[25000.0]["CE"], ready_option_gate, {"summary": bullish_summary})
    assert not composite_sideways["allow_long_entry"] and composite_sideways["decision"] == "NEUTRAL / NO TRADE"
    composite_mismatch = build_fo_composite_gate({"ok": True}, {"summary": bearish_summary}, nifty_pairs[25000.0]["CE"], ready_option_gate, {"summary": bullish_summary})
    assert not composite_mismatch["allow_long_entry"] and composite_mismatch["decision"] == "DIRECTION MISMATCH / NO TRADE"
    unconfirmed_pe_gate = dict(ready_option_gate, allow_long_entry=False)
    composite_unconfirmed_pe = build_fo_composite_gate({"ok": True}, {"summary": bearish_summary}, nifty_pairs[25000.0]["PE"], unconfirmed_pe_gate, {"summary": bullish_summary})
    assert not composite_unconfirmed_pe["allow_long_entry"] and "premium" in composite_unconfirmed_pe["message"].lower()

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

    direct_chart_payload = json.dumps({
        "chart": {"result": [{
            "timestamp": [1_790_000_000, 1_790_000_900],
            "indicators": {"quote": [{
                "open": [100.0, 101.0], "high": [101.0, 102.0],
                "low": [99.0, 100.0], "close": [100.5, 101.5], "volume": [10, 12],
            }]},
        }], "error": None}
    }).encode("utf-8")

    class MockDirectChartResponse:
        status = 200

        def read(self):
            return direct_chart_payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    direct_chart, direct_chart_error = fetch_direct_yahoo_chart_data(
        "MOCK.NS", "5d", "15m", lambda request, timeout: MockDirectChartResponse()
    )
    assert not direct_chart_error and len(direct_chart) == 2 and float(direct_chart["Close"].iloc[-1]) == 101.5

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

    bse_constituent_csv = (
        b"Constituents,Symbol,Macro-Economic Sector\n"
        b"ADANI PORTS AND SPECIAL ECONOM,532921,Services\n"
        b"RELIANCE INDUSTRIES LTD.,500325,Energy\n"
    )
    assert parse_official_bse_sensex_constituent_csv(bse_constituent_csv) == [
        "ADANIPORTS.NS", "RELIANCE.NS",
    ]

    class MockBseConstituentResponse(MockConstituentResponse):
        def read(self):
            return bse_constituent_csv

    bse_source = {
        "url": "https://www.bseindices.com/AsiaIndexAPI/api/Codewise_IndicesDownload/w?code=16",
        "minimum": 2,
        "maximum": 2,
        "parser": "bse_sensex",
        "publisher": "Test BSE constituent CSV",
    }
    bse_symbols, bse_error, bse_metadata = fetch_official_index_constituents(
        bse_source, lambda request, timeout: MockBseConstituentResponse()
    )
    assert bse_symbols == ["ADANIPORTS.NS", "RELIANCE.NS"] and not bse_error
    assert bse_metadata["publisher"] == "Test BSE constituent CSV"
    try:
        parse_official_bse_sensex_constituent_csv(
            b"Constituents,Symbol,Macro-Economic Sector\nUNKNOWN LTD.,999999,Services\n"
        )
        raise AssertionError("An unmapped BSE scrip code must be rejected rather than guessed.")
    except ValueError as exc:
        assert "reviewed public-chart mapping" in str(exc)

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

    try:
        calculate_directional_preview("SELL", 20, 5, 25, 1)
        raise AssertionError("A SELL target below zero must be rejected.")
    except ValueError as exc:
        assert "invalid" in str(exc).lower()
    strong_bull = summarize_directional_evidence([
        ("A", "BULLISH", ""), ("B", "BULLISH", ""), ("C", "BULLISH", ""),
        ("D", "BULLISH", ""), ("E", "BULLISH", ""), ("F", "BULLISH", ""),
        ("G", "NEUTRAL", ""), ("H", "BEARISH", ""),
    ])
    assert strong_bull["regime"] == "BULLISH" and strong_bull["buy_score"] == 6 and strong_bull["sell_score"] == 1
    sideways = summarize_directional_evidence([
        ("A", "NEUTRAL", ""), ("B", "NEUTRAL", ""), ("C", "NEUTRAL", ""),
        ("D", "BULLISH", ""), ("E", "BEARISH", ""), ("F", "NEUTRAL", ""),
    ], min_usable=6, threshold=5)
    assert sideways["regime"] == "SIDEWAYS / NO TRADE" and sideways["buy_score"] + sideways["sell_score"] < 6
    long_policy = trade_policy_for_horizon("Long Term (Weekly)")
    assert long_policy["allowed_actions"] == {"BUY"}
    assert validate_equity_trade_policy("Long Term (Weekly)", "BUY", "NORMAL", "DELIVERY")[0]
    assert not validate_equity_trade_policy("Long Term (Weekly)", "SELL", "NORMAL", "DELIVERY")[0]
    assert not validate_equity_trade_policy("Long Term (Weekly)", "BUY", "ROBO", "DELIVERY")[0]
    assert market_scan_action("BULLISH", "Intraday (15-Min)") == "BUY REVIEW"
    assert market_scan_action("BEARISH", "Intraday (15-Min)") == "SELL REVIEW"
    assert market_scan_action("BEARISH", "Long Term (Weekly)") == "BEARISH / NO NEW BUY"
    assert market_scan_action("SIDEWAYS / NO TRADE", "Intraday (15-Min)") == "NEUTRAL / NO TRADE"

    scan_index = pd.date_range("2026-09-25 09:15", periods=60, freq="15min")
    scan_close = np.linspace(100.0, 118.0, len(scan_index))
    scan_good = pd.DataFrame({
        "Open": scan_close - 0.20,
        "High": scan_close + 0.60,
        "Low": scan_close - 0.60,
        "Close": scan_close,
        "Volume": np.linspace(1000, 2200, len(scan_index)),
    }, index=scan_index)
    scan_missing = pd.DataFrame({column: [np.nan] * len(scan_index) for column in scan_good.columns}, index=scan_index)
    scan_daily_index = pd.date_range("2026-06-01", periods=80, freq="B")
    scan_daily = pd.DataFrame({
        "Open": np.linspace(80.0, 117.0, len(scan_daily_index)),
        "High": np.linspace(81.0, 118.0, len(scan_daily_index)),
        "Low": np.linspace(79.0, 116.0, len(scan_daily_index)),
        "Close": np.linspace(80.5, 117.5, len(scan_daily_index)),
        "Volume": np.linspace(10000, 12000, len(scan_daily_index)),
    }, index=scan_daily_index)
    scan_batch = pd.concat({"GOOD.NS": scan_good, "MISSING.NS": scan_missing}, axis=1)
    scan_daily_batch = pd.concat({"GOOD.NS": scan_daily, "MISSING.NS": scan_missing}, axis=1)
    scan_rows, scan_checklists, scan_prices = evaluate_equity_intelligence_scan(
        ["GOOD.NS", "MISSING.NS"], scan_batch, scan_daily_batch, 3,
    )
    assert len(scan_rows) == 2 and set(scan_rows["Symbol"]) == {"GOOD", "MISSING"}
    assert scan_rows.loc[scan_rows["Symbol"] == "MISSING", "Trend"].iloc[0] == "DATA UNAVAILABLE"
    assert "GOOD" in scan_checklists and scan_prices["GOOD"] > 0

    class MockCandleApi:
        def __init__(self):
            self.interval = ""

        def getCandleData(self, params):
            self.interval = params["interval"]
            return {"status": True, "data": [["2026-09-26 09:15", 100, 101, 99, 100, 12]]}

    mock_candle_api = MockCandleApi()
    candle_response = fetch_option_candles(mock_candle_api, contract, interval="FIFTEEN_MINUTE")
    assert candle_response["ok"] and mock_candle_api.interval == "FIFTEEN_MINUTE"
    today = ist_now().date()
    candle_times = pd.date_range(f"{today.isoformat()} 09:15", periods=25, freq="5min", tz=IST_TIMEZONE)
    flat_candles = pd.DataFrame({
        "time": candle_times, "Open": [100.0] * 25, "High": [101.0] * 25,
        "Low": [99.0] * 25, "Close": [100.0] * 25, "Volume": [100.0] * 25,
    })
    flat_evidence = evaluate_intraday_option_evidence(flat_candles)
    assert flat_evidence["summary"]["regime"] == "SIDEWAYS / NO TRADE" and len(flat_evidence["checks"]) == 10
    disconnected_gate = build_fo_trade_gate(disconnected, {"ok": False, "message": "Angel One is not connected."})
    assert disconnected_gate["decision"] == "WAIT / NEUTRAL" and not disconnected_gate["allow_long_entry"]
    flat_gate = build_fo_trade_gate({"ok": True, "ltp": 100.0}, {"ok": True}, flat_evidence)
    assert flat_gate["decision"] == "NEUTRAL / NO TRADE" and not flat_gate["allow_long_entry"]
    bull_gate = build_fo_trade_gate({"ok": True, "ltp": 100.0}, {"ok": True}, {"summary": strong_bull})
    assert bull_gate["decision"] == "CONDITIONAL LONG REVIEW" and bull_gate["allow_long_entry"]
    scan_contract = {"symbol": "NIFTY26SEP25000CE", "token": "12345", "lot_size": 65}
    disconnected_scan = build_fo_intelligence_row(scan_contract, disconnected, {"ok": False}, {})
    assert disconnected_scan["Premium"] == "—" and disconnected_scan["Buy"] == "— / 10"
    verified_scan = build_fo_intelligence_row(scan_contract, connected, {"ok": True}, flat_evidence)
    assert verified_scan["Trend"] == "SIDEWAYS / NO TRADE" and verified_scan["Buy"] == "0/10"
    second_scan_contract = {
        "symbol": "NIFTY26SEP25100PE", "token": "67890", "lot_size": 65,
        "expiry_label": "26 Sep 2026", "expiry_raw": "26SEP2026", "side": "PE",
        "strike": 25100.0, "tick_size": 0.05,
    }
    scan_contract.update({
        "expiry_label": "26 Sep 2026", "expiry_raw": "26SEP2026", "side": "CE",
        "strike": 25000.0, "tick_size": 0.05,
    })
    assert choose_fo_contract([scan_contract, second_scan_contract], "67890") == second_scan_contract
    assert choose_fo_contract([scan_contract, second_scan_contract], "missing") == scan_contract
    fo_watchlist = build_fo_contract_watchlist_rows(
        [scan_contract, second_scan_contract], scan_contract, connected, {"ok": True}, flat_evidence,
    )
    assert fo_watchlist[0]["Premium"] == "₹123.45" and fo_watchlist[1]["Premium"] == "—"
    assert fo_watchlist[1]["Data status"] == "MASTER VERIFIED · SELECT TO LOAD"
    verification_rows = build_fo_verification_rows(scan_contract, connected, {"ok": True}, flat_evidence, {"Delta": 0.5})
    assert verification_rows[0]["State"] == "VERIFIED" and verification_rows[-1]["State"] == "NOT VERIFIED HERE"
    parsed_news = parse_official_rss_items(
        b"<rss><channel><item><title>Official update</title><link>https://example.test/item</link><pubDate>Today</pubDate></item></channel></rss>",
        "Test regulator", "https://example.test/feed",
    )
    assert parsed_news[0]["title"] == "Official update" and parsed_news[0]["link"].endswith("/item")
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
    .brand-logo {
        width: 52px;
        height: 52px;
        object-fit: contain;
        margin-right: 13px;
        filter: drop-shadow(0 5px 12px rgba(34, 211, 238, 0.25));
    }
    .brand-block {
        display: flex;
        align-items: center;
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
    .tag-neutral { color: #fbbf24; font-weight: 700; font-family: 'JetBrains Mono', monospace; }
    .tag-muted { color: #94a3b8; font-weight: 600; font-family: 'JetBrains Mono', monospace; }

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
    div[class*="st-key-fo_index_"] button {
        min-height: 46px;
        font-size: 12px;
        letter-spacing: 0.15px;
        border-color: rgba(56, 189, 248, 0.40) !important;
    }
    div[class*="st-key-fo_index_"] button[kind="primary"] {
        background: linear-gradient(135deg, #0e7490, #4338ca) !important;
        border-color: #22d3ee !important;
        color: #ecfeff !important;
        box-shadow: 0 8px 22px rgba(34, 211, 238, 0.22) !important;
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
    .fo-trade-gate {
        background: linear-gradient(135deg, rgba(15, 23, 42, 0.92), rgba(30, 27, 75, 0.72));
        border: 1px solid rgba(148, 163, 184, 0.26);
        border-radius: 12px;
        padding: 15px 17px;
        margin: 14px 0;
        box-shadow: 0 10px 28px rgba(0, 0, 0, 0.24);
    }
    .fo-gate-wait { border-color: rgba(251, 191, 36, 0.52); }
    .fo-gate-bull { border-color: rgba(74, 222, 128, 0.58); }
    .fo-gate-bear { border-color: rgba(251, 113, 133, 0.58); }
    .fo-gate-heading {
        display: flex;
        justify-content: space-between;
        gap: 12px;
        align-items: center;
        color: #e2e8f0;
        font-size: 12px;
        font-weight: 800;
        letter-spacing: 0.7px;
    }
    .fo-gate-decision {
        font-family: 'JetBrains Mono', monospace;
        font-size: 11px;
        border-radius: 999px;
        padding: 5px 9px;
        background: rgba(15, 23, 42, 0.82);
    }
    .fo-gate-wait .fo-gate-decision { color: #fcd34d; border: 1px solid rgba(251, 191, 36, 0.42); }
    .fo-gate-bull .fo-gate-decision { color: #86efac; border: 1px solid rgba(74, 222, 128, 0.42); }
    .fo-gate-bear .fo-gate-decision { color: #fda4af; border: 1px solid rgba(251, 113, 133, 0.42); }
    .fo-gate-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 10px;
        margin: 13px 0 9px;
    }
    .fo-gate-stat {
        background: rgba(2, 6, 23, 0.40);
        border: 1px solid rgba(148, 163, 184, 0.14);
        border-radius: 8px;
        padding: 9px 10px;
    }
    .fo-gate-label { color: #94a3b8; font-size: 10px; font-weight: 700; letter-spacing: 0.6px; text-transform: uppercase; }
    .fo-gate-value { color: #f8fafc; font-family: 'JetBrains Mono', monospace; font-size: 13px; font-weight: 800; margin-top: 4px; }
    .fo-gate-note { color: #cbd5e1; font-size: 11px; line-height: 1.45; }
    .fo-gate-message { color: #cbd5e1; font-size: 12px; line-height: 1.5; }
    @media (max-width: 760px) {
        .fo-gate-heading { align-items: flex-start; flex-direction: column; }
        .fo-gate-grid { grid-template-columns: 1fr; }
    }
    .chart-shell {
        background: linear-gradient(180deg, rgba(15, 23, 42, 0.94), rgba(9, 14, 30, 0.90));
        border: 1px solid rgba(56, 189, 248, 0.18);
        border-radius: 12px;
        padding: 10px 10px 2px;
        box-shadow: 0 12px 34px rgba(0, 0, 0, 0.28);
    }
    .chart-inspector {
        background: linear-gradient(180deg, rgba(30, 41, 59, 0.72), rgba(15, 23, 42, 0.82));
        border: 1px solid rgba(148, 163, 184, 0.16);
        border-radius: 12px;
        padding: 16px;
        min-height: 500px;
    }
    .brief-card {
        background: linear-gradient(135deg, rgba(15, 23, 42, 0.84), rgba(30, 27, 75, 0.62));
        border: 1px solid rgba(56, 189, 248, 0.20);
        border-radius: 12px;
        padding: 16px;
        height: 100%;
    }
    .brief-kicker { color: #67e8f9; font-size: 10px; font-weight: 800; letter-spacing: 1.1px; text-transform: uppercase; }
    .brief-value { color: #f8fafc; font-size: 23px; font-family: 'JetBrains Mono', monospace; font-weight: 800; margin: 6px 0; }
    .brief-note { color: #cbd5e1; font-size: 12px; line-height: 1.5; }
</style>
""", unsafe_allow_html=True)

def apply_chart_style(fig, height=520):
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(15, 23, 42, 0.5)",
        font=dict(color="#94a3b8", family="Plus Jakarta Sans"),
        height=height,
        margin=dict(l=20, r=20, t=30, b=20),
        hovermode="x unified",
        dragmode="pan",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        xaxis=dict(gridcolor="rgba(255,255,255,0.05)", showgrid=True, showspikes=True, spikemode="across", spikesnap="cursor", spikecolor="rgba(148,163,184,0.45)"),
        yaxis=dict(gridcolor="rgba(255,255,255,0.05)", showgrid=True, side="right", showspikes=True, spikemode="across", spikesnap="cursor", spikecolor="rgba(148,163,184,0.45)", tickprefix="₹"),
    )
    return fig

# --- Persistent Paper Trading Engine ---
PAPER_FILE = "paper_trades.json"
APP_DIRECTORY = os.path.dirname(os.path.abspath(__file__))
SAFE_UI_SETTINGS_FILE = os.path.join(APP_DIRECTORY, "mahi_trading_settings.json")
SAFE_UI_SETTING_KEYS = {
    "asset_universe", "trading_horizon", "execution_mode", "chart_timeframe", "fo_interval",
    "fo5_index", "fo5_interval", "fo5_public_timeframe", "fo5_band",
}


def load_safe_ui_settings():
    """Load only non-secret display preferences; broker credentials are never stored here."""
    try:
        with open(SAFE_UI_SETTINGS_FILE, "r", encoding="utf-8") as handle:
            settings = json.load(handle)
        if not isinstance(settings, dict):
            return {}
        return {key: value for key, value in settings.items() if key in SAFE_UI_SETTING_KEYS}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def save_safe_ui_settings(settings):
    """Atomically persist only the allow-listed non-sensitive workspace choices."""
    cleaned = {key: settings[key] for key in SAFE_UI_SETTING_KEYS if key in settings}
    temporary_path = f"{SAFE_UI_SETTINGS_FILE}.tmp"
    try:
        with open(temporary_path, "w", encoding="utf-8") as handle:
            json.dump(cleaned, handle, indent=2, sort_keys=True)
        os.replace(temporary_path, SAFE_UI_SETTINGS_FILE)
        return ""
    except OSError as exc:
        return f"Could not save non-sensitive workspace settings: {exc}"


def clear_all_short_lived_broker_cache():
    """Forget volatile broker snapshots while leaving the authenticated object untouched."""
    for key in list(st.session_state.keys()):
        if key.startswith(("option_snapshot_", "option_greeks_", "option_candles_", "fo5_chain_")):
            st.session_state.pop(key, None)

def load_paper_account():
    # A public Streamlit server has one filesystem for all visitors. A global
    # JSON account leaks portfolios across browser sessions; keep it private.
    return {
        "cash": 500000.0,
        "positions": [],
        "closed_trades": []
    }

def save_paper_account(data):
    st.session_state["paper_data"] = data

if "paper_data" not in st.session_state:
    st.session_state["paper_data"] = load_paper_account()

def place_paper_order(symbol, action, qty, entry_price, sl_pts, tp_pts, trail_pts=0.0, metadata=None):
    pdata = st.session_state["paper_data"]
    try:
        calculate_directional_preview(action, entry_price, sl_pts, tp_pts, qty)
    except (ValueError, TypeError) as exc:
        return False, f"Invalid paper plan: {exc}"
    sl_price = round(entry_price - sl_pts if action == "BUY" else entry_price + sl_pts, 2)
    tp_price = round(entry_price + tp_pts if action == "BUY" else entry_price - tp_pts, 2)
    required_capital = qty * entry_price

    if pdata["cash"] < required_capital:
        return False, "Insufficient virtual balance."

    pdata["cash"] -= required_capital
    position = {
        "id": f"PAPER-{uuid.uuid4().hex[:12]}",
        "symbol": symbol,
        "action": action,
        "qty": qty,
        "entry_price": entry_price,
        "sl_price": sl_price,
        "tp_price": tp_price,
        "sl_pts": sl_pts,
        "tp_pts": tp_pts,
        "trail_pts": trail_pts,
        "timestamp": ist_now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": "OPEN"
    }
    position.update({key: value for key, value in (metadata or st.session_state.get("journal_draft", {})).items()
                     if key in {"setup", "entry_reason", "source", "timeframe", "lesson", "auto_exit", "contract"}})
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
        if _as_float(ltp) is None or ltp <= 0 or pos.get("auto_exit") is False:
            still_open.append(pos)
            continue
        closed = False
        exit_price = ltp
        reason = ""

        if pos["action"] == "BUY":
            if ltp >= pos["tp_price"]:
                closed = True
                reason = "Target observed on feed snapshot"
            elif ltp <= pos["sl_price"]:
                closed = True
                reason = "Stop observed on feed snapshot"
        else:
            if ltp <= pos["tp_price"]:
                closed = True
                reason = "Target observed on feed snapshot"
            elif ltp >= pos["sl_price"]:
                closed = True
                reason = "Stop observed on feed snapshot"

        if closed:
            closed_any = True
            pnl = (exit_price - pos["entry_price"]) * pos["qty"] if pos["action"] == "BUY" else (pos["entry_price"] - exit_price) * pos["qty"]
            pdata["cash"] += (pos["qty"] * pos["entry_price"]) + pnl
            pos["status"] = "CLOSED"
            pos["exit_price"] = exit_price
            pos["pnl"] = round(pnl, 2)
            pos["exit_reason"] = reason
            pos["exit_time"] = ist_now().strftime("%Y-%m-%d %H:%M:%S")
            pdata["closed_trades"].append(pos)
        else:
            still_open.append(pos)

    if closed_any:
        pdata["positions"] = still_open
        save_paper_account(pdata)

def render_paper_journal():
    pdata = st.session_state["paper_data"]
    trades = pdata.get("positions", []) + pdata.get("closed_trades", [])
    with st.expander("Trade journal · reason, setup and lesson", expanded=True):
        if not trades:
            st.caption("Record a paper trade to start your journal.")
        else:
            lookup = {trade["id"]: trade for trade in trades}
            trade_id = st.selectbox("Journal trade", list(lookup), format_func=lambda value: f"{lookup[value]['symbol']} · {lookup[value]['action']} · {value}", key="journal_trade")
            trade = lookup[trade_id]
            with st.form(f"journal_form_{trade_id}"):
                setup_name = st.text_input("Setup", value=trade.get("setup", "Manual"), max_chars=120)
                reason = st.text_area("Entry reason", value=trade.get("entry_reason", ""), max_chars=1000)
                lesson = st.text_area("Review / lesson", value=trade.get("lesson", ""), max_chars=2000)
                if st.form_submit_button("Save journal note"):
                    trade.update(setup=setup_name, entry_reason=reason, lesson=lesson)
                    save_paper_account(pdata)
                    st.success("Journal note saved for this session.")
            screenshot = st.file_uploader("Optional chart screenshot (PNG/JPG, up to 5 MB)", type=["png", "jpg", "jpeg"], key=f"journal_image_{trade_id}")
            if screenshot:
                if screenshot.size <= 5 * 1024 * 1024:
                    st.session_state.setdefault("journal_images", {})[trade_id] = {"name": f"{trade_id}.{screenshot.name.rsplit('.', 1)[-1].lower()}", "data": screenshot.getvalue()}
                    st.image(screenshot.getvalue(), width=320)
                else:
                    st.warning("Choose a screenshot smaller than 5 MB.")
        st.download_button("Export paper journal (JSON)", json.dumps(pdata, indent=2), file_name="mahi-paper-journal.json", mime="application/json", key="export_journal")
        if trades:
            st.download_button("Export trade table (CSV)", pd.DataFrame(trades).to_csv(index=False), file_name="mahi-paper-trades.csv", mime="text/csv", key="export_journal_csv")
        for trade_id, attachment in st.session_state.get("journal_images", {}).items():
            st.download_button(f"Download screenshot · {trade_id}", attachment["data"], file_name=attachment["name"], key=f"download_journal_image_{trade_id}")
    with st.expander("Setup performance · recorded paper trades", expanded=False):
        closed = pdata.get("closed_trades", [])
        if closed:
            history = pd.DataFrame(closed)
            history["setup"] = history.get("setup", pd.Series("Unlabelled", index=history.index)).fillna("Unlabelled")
            metrics = []
            for setup, group in history.groupby("setup"):
                pnl = pd.to_numeric(group["pnl"], errors="coerce")
                metrics.append({"Setup": setup, "Closed trades": len(group), "Wins": int((pnl > 0).sum()), "Losses": int((pnl < 0).sum()), "Gross P&L": float(pnl.sum()), "Average P&L": float(pnl.mean())})
            st.dataframe(pd.DataFrame(metrics), hide_index=True, width="stretch")
            st.caption(f"Sample: {len(closed)} closed paper trades. Results exclude costs and do not establish future reliability; small samples are especially uncertain.")
        else:
            st.caption("No completed paper trades yet. No performance statistics are estimated.")


def render_trade_notes(symbol, setup, timeframe, source, auto_exit=True):
    """Attach the trader's rationale to the next explicit paper submission."""
    with st.expander("Trade note", expanded=False):
        reason = st.text_area("Why this trade?", key=f"entry_reason_{symbol}", max_chars=1000)
        setup_name = st.text_input("Setup name", value=str(setup or "Manual"), key=f"entry_setup_{symbol}", max_chars=120)
    st.session_state["journal_draft"] = {"setup": setup_name, "entry_reason": reason,
        "timeframe": timeframe, "source": source, "auto_exit": auto_exit}


def render_risk_budget(planned_loss=None, is_paper=True):
    limits = st.session_state.get("risk_budgets", {})
    pdata = st.session_state.get("paper_data", {})
    trades = pdata.get("positions", []) + pdata.get("closed_trades", []) if is_paper else []
    result = risk_budget_warnings(planned_loss, trades, limits, ist_now().date())
    for warning in result["warnings"]:
        st.warning(warning)
    if not is_paper:
        st.caption("Per-trade budget is a planning check. Live daily losses and trade count must be checked against the broker account.")


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

# Saved owner credentials must never auto-connect every visitor of a public app.


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
    contract_identity = fo_contract_key(contract)
    return _cached_broker_result(
        f"option_snapshot_{contract_identity}",
        OPTION_SNAPSHOT_TTL_SECONDS,
        lambda: fetch_selected_option_snapshot(smart_api, contract),
    )


def get_option_greeks_for_ui(smart_api, underlying, expiry_raw, exchange="NFO"):
    if smart_api is None:
        return fetch_option_greeks(None, underlying, expiry_raw)
    return _cached_broker_result(
        f"option_greeks_{str(exchange).upper()}_{underlying}_{expiry_raw}",
        OPTION_GREEKS_TTL_SECONDS,
        lambda: fetch_option_greeks(smart_api, underlying, expiry_raw),
    )


def get_option_candles_for_ui(smart_api, contract, interval="FIVE_MINUTE"):
    if smart_api is None:
        return fetch_option_candles(None, contract, interval=interval)
    contract_identity = fo_contract_key(contract)
    return _cached_broker_result(
        f"option_candles_{contract_identity}_{interval}",
        OPTION_CHART_TTL_SECONDS,
        lambda: fetch_option_candles(smart_api, contract, interval=interval),
    )


def get_fo_chain_for_ui(smart_api, index_name, expiry_raw, chain_contracts):
    """Cache a small, exchange-aware batch quote snapshot for the visible chain."""
    ordered_keys = sorted(fo_contract_key(contract) for contract in chain_contracts or [])
    identity = hashlib.sha256("|".join(ordered_keys).encode("utf-8")).hexdigest()[:20]
    cache_key = f"fo5_chain_{str(index_name)}_{str(expiry_raw)}_{identity}"
    if smart_api is None:
        return fetch_broker_market_data(None, chain_contracts)
    return _cached_broker_result(
        cache_key,
        OPTION_CHAIN_TTL_SECONDS,
        lambda: fetch_broker_market_data(smart_api, chain_contracts),
    )


def clear_selected_option_broker_cache(contract, underlying, expiry_raw, interval, exchange="NFO"):
    """Clear only the current selected contract's short-lived broker snapshots."""
    identity = fo_contract_key(contract)
    if identity and not identity.endswith(":"):
        st.session_state.pop(f"option_snapshot_{identity}", None)
        st.session_state.pop(f"option_candles_{identity}_{interval}", None)
    st.session_state.pop(f"option_greeks_{str(exchange).upper()}_{underlying}_{expiry_raw}", None)


def broker_snapshot_caption(result):
    if not result.get("ok"):
        return result.get("message", "Broker data is unavailable.")
    age = result.get("age_seconds", 0.0)
    fetched_at = datetime.fromtimestamp(result.get("fetched_at", time.time()), IST_TIMEZONE).strftime("%H:%M:%S")
    status = "STALE" if age > OPTION_SNAPSHOT_TTL_SECONDS else "BROKER SNAPSHOT"
    return f"{status} · fetched {fetched_at} IST · age {age:.0f}s · {result.get('source', 'Angel One')}"


def broker_chain_caption(result):
    """Describe chain completeness/freshness without calling partial data live."""
    snapshot = result if isinstance(result, dict) else {}
    if not snapshot.get("ok"):
        return snapshot.get("message", "Broker option-chain data is unavailable.")
    requested = _as_int(snapshot.get("requested_count")) or 0
    fetched = _as_int(snapshot.get("fetched_count")) or 0
    age = _as_float(snapshot.get("age_seconds")) or 0.0
    fetched_at = datetime.fromtimestamp(snapshot.get("fetched_at", time.time()), IST_TIMEZONE).strftime("%H:%M:%S")
    state = "CHAIN LIVE" if snapshot.get("state") == "snapshot" and age <= OPTION_CHAIN_TTL_SECONDS else "CHAIN PARTIAL" if snapshot.get("state") == "partial" else "CHAIN STALE"
    return f"{state} · {fetched}/{requested} contracts · fetched {fetched_at} IST · age {age:.0f}s · {snapshot.get('source', 'Angel One')}"

def _fo_widget_suffix(contract):
    """Use a stable, safe widget suffix tied to exchange + broker token."""
    identity = fo_contract_key(contract)
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16] if identity else "unselected"


def _fo_quote_change(snapshot, chain_quote):
    """Prefer SmartAPI's percentage; otherwise derive it from two actual quote fields."""
    quote = chain_quote if isinstance(chain_quote, dict) else {}
    reported = _as_float(quote.get("percent_change"))
    if reported is not None:
        return reported
    ltp = _as_float((snapshot or {}).get("ltp"))
    close = _as_float((snapshot or {}).get("close"))
    if ltp is not None and close is not None and close > 0:
        return (ltp - close) / close * 100.0
    return None


def _format_fo_chain_rows(rows):
    """Format missing broker fields as a visible dash rather than a zero."""
    formatted = []
    for raw in rows or []:
        row = dict(raw)
        for label in ("CE LTP", "CE Bid", "CE Ask", "PE LTP", "PE Bid", "PE Ask", "Strike"):
            row[label] = _fo_currency(row.get(label))
        for label in ("CE Chg%", "PE Chg%"):
            row[label] = _fo_percent(row.get(label))
        for label in ("CE Volume", "CE OI", "PE Volume", "PE OI"):
            value = _as_float(row.get(label))
            row[label] = "—" if value is None else f"{value:,.0f}"
        formatted.append(row)
    return formatted


def _fo_candle_figure(candles, title, height=430):
    """Build one dark TradingView-like candlestick chart from real chart rows."""
    frame = candles.copy() if isinstance(candles, pd.DataFrame) else pd.DataFrame()
    required = {"Open", "High", "Low", "Close"}
    if frame.empty or not required.issubset(frame.columns):
        return None
    x_values = frame["time"] if "time" in frame.columns else frame.index
    close = pd.to_numeric(frame["Close"], errors="coerce")
    figure = go.Figure(go.Candlestick(
        x=x_values,
        open=frame["Open"], high=frame["High"], low=frame["Low"], close=close,
        name=title, increasing_line_color="#34d399", decreasing_line_color="#fb7185",
    ))
    if len(frame) >= 9:
        figure.add_trace(go.Scatter(
            x=x_values, y=close.ewm(span=9, adjust=False).mean(), mode="lines",
            name="EMA 9", line=dict(color="#22d3ee", width=1.25),
        ))
    if len(frame) >= 20:
        figure.add_trace(go.Scatter(
            x=x_values, y=close.ewm(span=20, adjust=False).mean(), mode="lines",
            name="EMA 20", line=dict(color="#a78bfa", width=1.25),
        ))
    if "Volume" in frame.columns:
        volume = pd.to_numeric(frame["Volume"], errors="coerce").fillna(0)
        if volume.gt(0).any():
            typical = (pd.to_numeric(frame["High"], errors="coerce") + pd.to_numeric(frame["Low"], errors="coerce") + close) / 3.0
            cumulative_volume = volume.cumsum().replace(0, np.nan)
            vwap = (typical * volume).cumsum() / cumulative_volume
            if vwap.notna().any():
                figure.add_trace(go.Scatter(
                    x=x_values, y=vwap, mode="lines", name="Session VWAP",
                    line=dict(color="#fbbf24", width=1.2, dash="dot"),
                ))
    figure = apply_chart_style(figure, height=height)
    figure.update_layout(title=dict(text=title, font=dict(size=13, color="#e2e8f0")))
    figure.update_xaxes(rangeslider_visible=False)
    return figure


def _render_fo_gate(gate):
    """Render the consistent F&O gate without trusting values as HTML."""
    safe_gate = {key: html.escape(str(value)) for key, value in (gate or {}).items()}
    st.markdown(
        """
        <div class="fo-trade-gate {tone}">
          <div class="fo-gate-heading">
            <span>🛡️ F&amp;O TRADE GATE</span>
            <span class="fo-gate-decision">{decision_note}</span>
          </div>
          <div class="fo-gate-grid">
            <div class="fo-gate-stat"><div class="fo-gate-label">Decision</div><div class="fo-gate-value">{decision}</div></div>
            <div class="fo-gate-stat"><div class="fo-gate-label">Conditions</div><div class="fo-gate-value">{conditions}</div><div class="fo-gate-note">{condition_detail}</div></div>
            <div class="fo-gate-stat"><div class="fo-gate-label">Risk status</div><div class="fo-gate-value">{risk_status}</div><div class="fo-gate-note">Broker-selected contract only</div></div>
          </div>
          <div class="fo-gate-message">{message}</div>
        </div>
        """.format(**safe_gate),
        unsafe_allow_html=True,
    )


def _render_fo_risk_controls(selected_contract, option_snapshot, gate, is_paper_trading, live_orders_armed, broker_client):
    """Render a directional planning calculator and deliberately gated order actions."""
    if not option_snapshot.get("ok") or not gate.get("allow_risk_preview"):
        st.info(f"Order entry needs this contract's broker premium. {option_snapshot.get('message') or 'Refresh the selected contract.'}")
        missing_buy, missing_sell = st.columns(2)
        missing_suffix = _fo_widget_suffix(selected_contract)
        missing_mode = "PAPER" if is_paper_trading else "LIVE"
        missing_buy.button(f"🟢 {missing_mode} BUY", disabled=True, width="stretch", key=f"missing_buy_{missing_suffix}")
        missing_sell.button(f"🔴 {missing_mode} SELL", disabled=True, width="stretch", key=f"missing_sell_{missing_suffix}")
        st.caption("Login success does not guarantee quote availability. No usable quote can mean session expiry, symbol/token mismatch, entitlement or broker data availability; market closure alone does not prove the cause.")
        return
    suffix = _fo_widget_suffix(selected_contract)
    minimum_step = _as_float(selected_contract.get("tick_size")) or 0.05
    st.markdown("<div class='bottom-card'><div class='bottom-title'>💰 Selected Option Intraday Risk Preview</div><div style='font-size:11px;color:#94a3b8;'>The switch below changes entry, stop, target, and illustrative P&amp;L direction. It does not send an order.</div></div>", unsafe_allow_html=True)
    risk_1, risk_2 = st.columns(2)
    with risk_1:
        lots = st.number_input("Lots", min_value=1, value=1, step=1, key=f"fo5_lots_{suffix}")
    with risk_2:
        option_action = st.radio("Premium action", ["BUY", "SELL"], horizontal=True, key=f"fo5_action_{suffix}")
    risk_3, risk_4 = st.columns(2)
    with risk_3:
        default_stop = max(minimum_step, round(float(option_snapshot["ltp"]) * 0.15, 2))
        option_stop = st.number_input("Risk points (₹)", min_value=float(minimum_step), value=float(default_stop), step=float(minimum_step), key=f"fo5_stop_{suffix}")
    with risk_4:
        option_target = st.number_input("Target points (₹)", min_value=float(minimum_step), value=float(round(option_stop * 3, 2)), step=float(minimum_step), key=f"fo5_target_{suffix}")
    option_quantity = int(lots) * int(selected_contract["lot_size"])
    try:
        preview = calculate_directional_preview(option_action, option_snapshot["ltp"], option_stop, option_target, option_quantity)
    except ValueError as exc:
        st.error(f"Option sizing needs valid values: {exc}")
        return
    premium_label = "Premium outlay before charges" if option_action == "BUY" else "Gross premium received — broker margin is not calculated"
    st.markdown(
        f"<div class='calc-box'><div class='calc-row'><span style='color:#94a3b8;'>Quantity:</span><strong>{int(lots)} lot × {selected_contract['lot_size']} = {option_quantity}</strong></div><div class='calc-row'><span style='color:#94a3b8;'>{premium_label}:</span><strong style='color:#00f2fe;'>₹{preview['gross_notional']:,.2f}</strong></div><div class='calc-row'><span style='color:#94a3b8;'>{option_action} entry / stop / target:</span><strong>₹{preview['entry_price']:.2f} / <span style='color:#fb7185'>₹{preview['stop_price']:.2f}</span> / <span style='color:#34d399'>₹{preview['target_price']:.2f}</span></strong></div><div class='calc-row'><span style='color:#94a3b8;'>Illustrative P&amp;L target / stop:</span><strong><span style='color:#34d399'>+₹{preview['target_pnl']:,.2f}</span> / <span style='color:#fb7185'>₹{preview['loss_pnl']:,.2f}</span></strong></div></div>",
        unsafe_allow_html=True,
    )
    render_trade_notes(selected_contract["symbol"], gate.get("decision", "Manual option plan"), "F&O intraday", "Angel One selected contract")
    st.session_state["journal_draft"]["contract"] = dict(selected_contract)
    render_risk_budget(abs(preview["loss_pnl"]), is_paper_trading)
    buy_locked = not is_paper_trading and option_action == "BUY" and not gate.get("allow_long_entry")
    if buy_locked:
        st.warning(f"BUY is locked by the F&O Trade Gate: {gate.get('decision', 'WAIT / NEUTRAL')}. These are planning values only.")
    if is_paper_trading:
        button_key = f"mkt_buy_fo5_{suffix}" if option_action == "BUY" else f"mkt_sell_fo5_{suffix}"
        if st.button(f"{'🟢' if option_action == 'BUY' else '🔴'} 📝 Log {option_action} paper position", width="stretch", key=button_key, disabled=buy_locked):
            ok, message = place_paper_order(
                selected_contract["symbol"], option_action, option_quantity,
                option_snapshot["ltp"], option_stop, option_target,
            )
            (st.success if ok else st.error)(f"{'✅' if ok else '❌'} {message}")
        if option_action == "SELL":
            st.caption("Paper SELL reserves the entry notional as a simplified simulation. This is not the broker's short-option margin calculation.")
        return
    if option_action == "SELL":
        st.warning("Live short-option submission is unavailable until an actual broker margin and product preflight is implemented. Gross premium is not treated as available capital.")
        return
    if not gate.get("allow_long_entry"):
        st.markdown(f"<div class='live-arm-card'>🔒 <strong>LIVE BUY LOCKED BY F&amp;O TRADE GATE</strong> — {html.escape(str(gate.get('decision', 'WAIT / NEUTRAL')))}. Wait for current broker evidence.</div>", unsafe_allow_html=True)
        return
    if live_orders_armed:
        st.markdown("<div class='live-ready-card'>⚡ <strong>LIVE LONG-OPTION MARKET MODE</strong> — final broker acceptance is still required. Displayed SL/target are planning values, not broker-attached exits.</div>", unsafe_allow_html=True)
    else:
        st.markdown("<div class='live-arm-card'>🔒 <strong>LIVE MODE NOT ARMED</strong> — connect and arm Angel One below.</div>", unsafe_allow_html=True)
    confirmation_key = f"fo5_confirm_buy_{suffix}_{option_quantity}"
    confirmed = st.checkbox(
        f"I understand: submit LIVE MARKET BUY {selected_contract['symbol']} ({option_quantity} qty) as INTRADAY after a fresh broker quote.",
        key=confirmation_key,
        disabled=not live_orders_armed,
    )
    if st.button(f"🟢 ⚡ LIVE BUY MARKET {selected_contract['symbol']}", width="stretch", key=f"live_option_buy_fo5_{suffix}", disabled=not (live_orders_armed and confirmed)):
        with st.spinner("Refreshing broker premium and submitting your confirmed intraday order..."):
            fresh_snapshot = fetch_selected_option_snapshot(broker_client, selected_contract)
            if fresh_snapshot.get("ok"):
                success, message, details = place_regular_order(broker_client, selected_contract, option_quantity, "BUY", "INTRADAY")
            else:
                success, message, details = False, f"Fresh broker LTP is required: {fresh_snapshot.get('message', 'unavailable')}", None
        if success:
            order_id = details.get("order_id") if isinstance(details, dict) else ""
            st.session_state["last_live_submission"] = {
                "symbol": selected_contract["symbol"], "action": "BUY", "kind": "F&O INTRADAY MARKET",
                "order_id": order_id, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            st.success(f"✅ LIVE F&O order submitted to Angel One{f' · Order ID: {order_id}' if order_id else ''}. Verify final status in the broker order book.")
        else:
            st.error(f"Live F&O order was not submitted: {message}")


def build_fo_compact_index_overview(chart_timeframe):
    """Return a five-index public-context table without inventing broker values."""
    period, interval = FO_PUBLIC_CHART_OPTIONS[chart_timeframe]
    frames = {}
    errors = {}
    for index_name, spec in FO_INDEX_UNIVERSE.items():
        frame, error = fetch_fo_public_index_chart(spec["chart_symbol"], period, interval)
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            frames[spec["chart_symbol"]] = frame
        if error:
            errors[index_name] = error
    batch = pd.concat(frames, axis=1, sort=False) if frames else pd.DataFrame()
    symbols = [spec["chart_symbol"] for spec in FO_INDEX_UNIVERSE.values()]
    results, checklists, _ = evaluate_equity_intelligence_scan(symbols, batch, pd.DataFrame(), 3)
    result_by_symbol = {str(row.get("Symbol")): row for _, row in results.iterrows()} if not results.empty else {}
    rows = []
    for index_name, spec in FO_INDEX_UNIVERSE.items():
        result = result_by_symbol.get(spec["chart_symbol"].removesuffix(".NS").removesuffix(".BO"), {})
        price = _as_float(result.get("Price")) if hasattr(result, "get") else None
        change = _as_float(result.get("Chg%")) if hasattr(result, "get") else None
        buy_text = str(result.get("Buy") or "") if hasattr(result, "get") else ""
        sell_text = str(result.get("Sell") or "") if hasattr(result, "get") else ""
        buy = _as_int(buy_text.split("/", 1)[0])
        sell = _as_int(sell_text.split("/", 1)[0])
        trend = str(result.get("Trend") or "DATA REQUIRED") if hasattr(result, "get") else "DATA REQUIRED"
        if trend == "BULLISH":
            bias = "BULLISH · CE CONTEXT"
        elif trend == "BEARISH":
            bias = "BEARISH · PE CONTEXT"
        elif trend == "SIDEWAYS / NO TRADE":
            bias = "NEUTRAL / NO TRADE"
        else:
            bias = "DATA REQUIRED"
        rows.append({
            "Contract": index_name,
            "Underlying LTP": price,
            "Chg%": change,
            "Score": f"{buy or 0}/10 Buy · {sell or 0}/10 Sell" if price is not None else "— / 10",
            "Technical Bias": bias,
            "Data note": "PUBLIC CONTEXT · MAY BE DELAYED" if price is not None else f"DATA UNAVAILABLE · {errors.get(index_name, 'No usable public chart')}",
        })
    return pd.DataFrame(rows), checklists, frames


def render_fo_index_desk(is_paper_trading, live_orders_armed, compact=False):
    """Render the focused five-index F&O desk with source-safe data states."""
    st.markdown(
        """
        <div class="disclaimer-banner"><strong>F&amp;O Index Desk</strong> — NIFTY 50, BANKNIFTY, FINNIFTY, SENSEX, and NIFT Midcap only. Select CE/PE and strike yourself. Chain prices, depth, volume, and OI appear only from Angel One; the public index chart is labelled context-only and cannot unlock a trade.</div>
        """,
        unsafe_allow_html=True,
    )
    first_index = next(iter(FO_INDEX_UNIVERSE))
    if st.session_state.get("fo5_index") not in FO_INDEX_UNIVERSE:
        st.session_state["fo5_index"] = first_index
    if compact:
        compact_top, compact_refresh = st.columns([7.5, 2.5])
        with compact_top:
            chart_timeframe = st.selectbox("Underlying context timeframe", list(FO_PUBLIC_CHART_OPTIONS.keys()), key="fo_compact_public_timeframe")
        with compact_refresh:
            st.write("")
            if st.button("↻ Refresh F&O context", width="stretch", key="refresh_fo_compact"):
                clear_all_short_lived_broker_cache()
                clear_chart = getattr(fetch_fo_public_index_chart, "clear", None)
                if callable(clear_chart):
                    clear_chart()
                st.rerun()
        overview, _, _ = build_fo_compact_index_overview(chart_timeframe)
        grid = st.dataframe(
            overview, width="stretch", hide_index=True, height=250, on_select="rerun",
            selection_mode="single-row", key="fo_compact_index_table",
            column_config={
                "Underlying LTP": st.column_config.NumberColumn(format="₹%.2f"),
                "Chg%": st.column_config.NumberColumn(format="%.2f%%"),
            },
        )
        selected_rows = (grid.selection or {}).get("rows", [])
        if selected_rows:
            selected_name = str(overview.iloc[selected_rows[0]]["Contract"])
            if selected_name in FO_INDEX_UNIVERSE:
                st.session_state["fo5_index"] = selected_name
        st.caption("Select a row to inspect it below. This table is public underlying context only; option prices, chain fields, Greeks and order planning remain Angel One broker data only.")
    else:
        index_columns = st.columns(len(FO_INDEX_UNIVERSE))
        for column, index_name in zip(index_columns, FO_INDEX_UNIVERSE):
            with column:
                active = st.session_state["fo5_index"] == index_name
                if st.button(
                    f"{'● ' if active else ''}{index_name}",
                    width="stretch", type="primary" if active else "secondary", key=f"fo_index_{index_name}",
                ) and not active:
                    st.session_state["fo5_index"] = index_name
                    st.rerun()
    selected_index = st.session_state["fo5_index"]
    spec = FO_INDEX_UNIVERSE[selected_index]
    st.caption(f"{spec['description']} · options exchange: {spec['option_exchange']} · index quote exchange: {spec['spot_exchange']} · no stock or unverified derivative contracts are included.")

    controls_1, controls_2, controls_3, controls_4 = st.columns([2.2, 2.2, 2.0, 2.6])
    with controls_1:
        fo_interval_label = st.selectbox("Broker intraday candles", list(FO_INTERVAL_OPTIONS.keys()), key="fo5_interval")
        fo_interval = FO_INTERVAL_OPTIONS[fo_interval_label]
    with controls_2:
        if compact:
            st.caption(f"Public context: {chart_timeframe}. Broker candles are used for the trade gate.")
        else:
            chart_timeframe = st.selectbox("Underlying chart timeframe", list(FO_PUBLIC_CHART_OPTIONS.keys()), key="fo5_public_timeframe")
    with controls_3:
        band_label = st.radio("Chain range", ["ATM ±5 strikes", "ATM ±10 strikes"], horizontal=False, key="fo5_band")
        chain_depth = 5 if band_label == "ATM ±5 strikes" else 10
    with controls_4:
        st.write("")
        if st.button("↻ Refresh Angel One master + F&O data", width="stretch", key="refresh_fo5_master"):
            load_angel_option_master.clear()
            clear_all_short_lived_broker_cache()
            clear_chart = getattr(fetch_fo_public_index_chart, "clear", None)
            if callable(clear_chart):
                clear_chart()
            st.rerun()

    master_rows, master_error = load_angel_option_master()
    if master_error:
        st.error(f"Angel One instrument master is unavailable: {master_error}")
        st.caption("No contracts, strikes, option prices, OI, Greeks, or lot sizes are substituted while the master is unavailable. You can still use the broker gateway below.")
        return
    index_contracts = resolve_fo_index_option_contracts(master_rows, selected_index)
    spot_contract = resolve_fo_index_spot_contract(master_rows, selected_index)
    if not index_contracts:
        st.warning(f"Angel One's current master has no non-expired {spec['option_exchange']} OPTIDX contracts for {selected_index}. No fallback contract is used.")
        return
    if spot_contract is None:
        st.warning(f"Angel One's current master has no {spec['spot_exchange']} AMXIDX record for {selected_index}. A public chart can be shown, but broker ATM and the F&O trade gate stay unavailable.")

    expiry_values = list(dict.fromkeys(contract["expiry_raw"] for contract in index_contracts))
    expiry_labels = {contract["expiry_raw"]: contract["expiry_label"] for contract in index_contracts}
    selected_expiry = st.selectbox(
        "Expiry", expiry_values, format_func=lambda value: expiry_labels[value],
        key=f"fo5_expiry_{selected_index}",
    )
    expiry_contracts = [contract for contract in index_contracts if contract["expiry_raw"] == selected_expiry]
    chain_pairs_by_strike = pair_fo_index_contracts(expiry_contracts)
    if not chain_pairs_by_strike:
        st.warning("The selected expiry has no valid CE/PE pairs in the Angel One master.")
        return

    broker_client = st.session_state.get("smart_api")
    underlying_snapshot = get_option_snapshot_for_ui(broker_client, spot_contract) if spot_contract else {
        "ok": False, "state": "unavailable", "message": "No broker index contract was resolved from the master.",
    }
    broker_atm_strike = nearest_fo_chain_strike(chain_pairs_by_strike, underlying_snapshot.get("ltp"))
    all_strikes = sorted(chain_pairs_by_strike)
    manual_default = broker_atm_strike if broker_atm_strike in all_strikes else all_strikes[len(all_strikes) // 2]
    centre_key = f"fo5_centre_{selected_index}_{selected_expiry}"
    if st.session_state.get(centre_key) not in all_strikes:
        st.session_state.pop(centre_key, None)
    selection_1, selection_2, selection_3, selection_4 = st.columns([2.5, 2.2, 2.3, 3.0])
    with selection_1:
        center_strike = st.selectbox(
            "Chain centre strike", all_strikes, index=all_strikes.index(manual_default),
            format_func=lambda value: f"₹{value:,.2f}", key=centre_key,
        )
        if broker_atm_strike is not None:
            st.caption(f"Broker ATM nearest listed strike: ₹{broker_atm_strike:,.2f}")
        else:
            st.caption("Broker index LTP unavailable — this is a manual centre, not an ATM claim.")
    chain_pairs = fo_chain_band(chain_pairs_by_strike, center_strike, chain_depth)
    available_sides = [side for side in ("CE", "PE") if any(pair.get(side) for pair in chain_pairs)]
    side_key = f"fo5_side_{selected_index}_{selected_expiry}"
    if st.session_state.get(side_key) not in available_sides:
        st.session_state.pop(side_key, None)
    with selection_2:
        selected_side = st.radio(
            "Option side", available_sides,
            format_func=lambda side: "CALL (CE)" if side == "CE" else "PUT (PE)",
            horizontal=True, key=side_key,
            help="Choose the CE or PE deliberately. The app never flips side automatically from an underlying signal.",
        )
    available_strikes = [pair["strike"] for pair in chain_pairs if pair.get(selected_side)]
    strike_key = f"fo5_strike_{selected_index}_{selected_expiry}_{selected_side}"
    if st.session_state.get(strike_key) not in available_strikes:
        st.session_state.pop(strike_key, None)
    selected_default = broker_atm_strike if broker_atm_strike in available_strikes else available_strikes[0]
    with selection_3:
        selected_strike = st.selectbox(
            "Selected strike", available_strikes, index=available_strikes.index(selected_default),
            format_func=lambda value: f"₹{value:,.2f}", key=strike_key,
        )
    selected_contract = next(pair[selected_side] for pair in chain_pairs if pair["strike"] == selected_strike and pair.get(selected_side))
    with selection_4:
        st.write("")
        if st.button("↻ Refresh selected chain + contract", width="stretch", key=f"refresh_fo5_selected_{_fo_widget_suffix(selected_contract)}_{fo_interval}"):
            clear_all_short_lived_broker_cache()
            clear_selected_option_broker_cache(selected_contract, selected_contract["underlying"], selected_expiry, fo_interval, selected_contract["exchange"])
            st.rerun()

    chain_contracts = [contract for pair in chain_pairs for contract in (pair.get("CE"), pair.get("PE")) if contract]
    chain_snapshot = get_fo_chain_for_ui(broker_client, selected_index, selected_expiry, chain_contracts)
    selected_snapshot = get_option_snapshot_for_ui(broker_client, selected_contract)
    if selected_snapshot.get("ok"):
        st.session_state.setdefault("broker_option_prices", {})[selected_contract["symbol"]] = selected_snapshot
    else:
        st.session_state.setdefault("broker_option_prices", {}).pop(selected_contract["symbol"], None)
    option_candle_snapshot = get_option_candles_for_ui(broker_client, selected_contract, fo_interval)
    option_candles = normalise_broker_option_candles(option_candle_snapshot.get("candles", [])) if option_candle_snapshot.get("ok") else pd.DataFrame()
    option_evidence = evaluate_intraday_option_evidence(option_candles, instrument_label="Selected option premium")
    option_gate = build_fo_trade_gate(selected_snapshot, option_candle_snapshot, option_evidence)
    underlying_candle_snapshot = get_option_candles_for_ui(broker_client, spot_contract, fo_interval) if spot_contract else {
        "ok": False, "state": "unavailable", "message": "No broker index contract was resolved from the master.", "candles": [],
    }
    underlying_candles = normalise_broker_option_candles(underlying_candle_snapshot.get("candles", [])) if underlying_candle_snapshot.get("ok") else pd.DataFrame()
    underlying_evidence = evaluate_intraday_option_evidence(underlying_candles, instrument_label="Underlying index")
    premium_signal = detect_price_action_setup(option_candles, fo_interval)
    underlying_completed, underlying_issue = completed_signal_candles(underlying_candles, fo_interval)
    if not selected_snapshot.get("ok") or not underlying_snapshot.get("ok") or underlying_issue or len(underlying_completed) < 30:
        premium_signal = dict(premium_signal, state="DATA REQUIRED", reasons=["Fresh broker index and selected-premium quotes plus sufficient completed index candles are required."])
    else:
        underlying_close = underlying_completed["Close"]
        index_ema9 = underlying_close.ewm(span=9, adjust=False).mean().iloc[-1]
        index_ema20 = underlying_close.ewm(span=20, adjust=False).mean().iloc[-1]
        direction_agrees = (underlying_close.iloc[-1] > index_ema9 > index_ema20) if selected_side == "CE" else (underlying_close.iloc[-1] < index_ema9 < index_ema20)
        if premium_signal["state"] == "BULLISH SETUP" and not direction_agrees:
            premium_signal = dict(premium_signal, state="WAIT / NO CLEAN SETUP", reasons=premium_signal["reasons"] + ["Underlying direction does not confirm a long position in this option side."])
    render_setup_signal(premium_signal, f"{selected_contract['symbol']} · premium setup")
    st.caption("Bullish/bearish describes this option premium. A bearish index can support a long PE; it does not mean SELL PE. Technical evidence never sends an order.")
    composite_gate = build_fo_composite_gate(
        underlying_snapshot, underlying_evidence, selected_contract, option_gate, option_evidence,
    )
    if spec["option_exchange"] == "NFO":
        greeks_snapshot = get_option_greeks_for_ui(
            broker_client, selected_contract["underlying"], selected_expiry, selected_contract["exchange"],
        )
        selected_greeks = selected_contract_greeks(greeks_snapshot.get("rows", []), selected_contract) if greeks_snapshot.get("ok") else {}
    else:
        greeks_snapshot = {"ok": False, "state": "unavailable", "message": "BFO/SENSEX Greeks are not requested because this SmartAPI endpoint is not verified for BFO.", "rows": []}
        selected_greeks = {}
    public_period, public_interval = FO_PUBLIC_CHART_OPTIONS[chart_timeframe]
    public_chart, public_chart_error = fetch_fo_public_index_chart(spec["chart_symbol"], public_period, public_interval)
    selected_chain_quote = quote_for_fo_contract(chain_snapshot, selected_contract)

    main_left, main_right = st.columns([6.4, 3.6])
    with main_left:
        st.markdown(f"<div class='bottom-card'><div class='bottom-title'>📈 {html.escape(selected_index)} underlying chart</div><div style='font-size:11px;color:#94a3b8;'>Broker chart is preferred for the current session. Public chart is a delayed context fallback only; it never unlocks the trade gate.</div></div>", unsafe_allow_html=True)
        index_metric_1, index_metric_2, index_metric_3 = st.columns(3)
        with index_metric_1:
            st.metric("Broker index LTP", _fo_currency(underlying_snapshot.get("ltp")))
        with index_metric_2:
            st.metric("Broker state", "QUOTE RETURNED" if underlying_snapshot.get("ok") else "UNAVAILABLE")
        with index_metric_3:
            st.metric("Underlying gate", underlying_evidence.get("summary", {}).get("regime", "INSUFFICIENT_DATA"))
        chart_frame = underlying_candles if not underlying_candles.empty else public_chart
        chart_source = "Angel One current-session broker candles" if not underlying_candles.empty else f"Public delayed context · {spec['chart_symbol']}"
        figure = _fo_candle_figure(chart_frame, f"{selected_index} · {chart_source}")
        if figure is not None:
            st.plotly_chart(figure, width="stretch", theme=None, config={"scrollZoom": True, "displaylogo": False}, key=f"fo5_underlying_chart_{selected_index}_{chart_timeframe}_{fo_interval}")
            if not underlying_candles.empty:
                st.caption(broker_snapshot_caption(underlying_candle_snapshot) + " · current IST session only")
            else:
                st.caption(f"{chart_source}. {public_chart_error or 'May be delayed; not used for ATM or an entry decision.'}")
        else:
            st.info(f"Underlying chart unavailable. {public_chart_error or underlying_candle_snapshot.get('message', 'No usable chart rows were returned.')}")
    with main_right:
        option_change = _fo_quote_change(selected_snapshot, selected_chain_quote)
        premium_text = _fo_currency(selected_snapshot.get("ltp")) if selected_snapshot.get("ok") else "Unavailable"
        premium_color = "#34d399" if selected_snapshot.get("ok") else "#fbbf24"
        option_side_label = "CALL (CE)" if selected_contract["side"] == "CE" else "PUT (PE)"
        quote_volume = _as_float(selected_chain_quote.get("volume"))
        quote_oi = _as_float(selected_chain_quote.get("open_interest"))
        volume_text = "—" if quote_volume is None else f"{quote_volume:,.0f}"
        oi_text = "—" if quote_oi is None else f"{quote_oi:,.0f}"
        st.markdown(
            f"""
            <div class="inspector-card">
              <div style="display:flex;justify-content:space-between;gap:9px;align-items:flex-start;">
                <div><div style="font-size:18px;font-weight:800;color:#fff;font-family:'JetBrains Mono',monospace;word-break:break-word;">{html.escape(selected_contract['symbol'])}</div><div style="font-size:25px;font-weight:800;color:{premium_color};margin:4px 0;">{premium_text}</div></div>
                <span style="background:rgba(0,242,254,.12);border:1px solid rgba(0,242,254,.3);color:#67e8f9;font-size:10px;padding:4px 7px;border-radius:6px;font-weight:800;">{html.escape(option_setup_from_evidence(option_evidence))}</span>
              </div>
              <div style="display:flex;justify-content:space-between;gap:7px;flex-wrap:wrap;font-size:11px;margin:10px 0;padding:8px 10px;background:rgba(30,41,59,.6);border-radius:6px;"><span>{option_side_label}</span><span>Strike <strong>₹{selected_contract['strike']:,.2f}</strong></span><span>Lot <strong>{selected_contract['lot_size']}</strong></span><span>Tick <strong>{_fo_currency(selected_contract.get('tick_size'))}</strong></span></div>
              <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px;font-size:11px;"><span>LTP change <strong>{_fo_percent(option_change)}</strong></span><span>Bid / Ask <strong>{_fo_currency(selected_chain_quote.get('bid'))} / {_fo_currency(selected_chain_quote.get('ask'))}</strong></span><span>Volume <strong>{volume_text}</strong></span><span>OI <strong>{oi_text}</strong></span></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if selected_snapshot.get("ok"):
            st.caption(broker_snapshot_caption(selected_snapshot))
        elif selected_snapshot.get("state") == "disconnected":
            st.warning("Connect Angel One below to load this selected CE/PE's real LTP, broker chart, and data fields. No premium is estimated.")
        else:
            st.error(f"Selected-contract LTP unavailable: {selected_snapshot.get('message', 'Unknown broker error.')}")
        _render_fo_gate(composite_gate)
        if selected_greeks:
            st.caption("Broker-reported only: " + " · ".join(f"{label}: {value}" for label, value in selected_greeks.items()) + " · " + broker_snapshot_caption(greeks_snapshot))
        elif greeks_snapshot.get("state") == "unavailable":
            st.caption(f"Greeks/IV: {greeks_snapshot.get('message', 'Not returned by the broker.')}")
        elif broker_client is not None:
            st.caption(f"Greeks/IV unavailable: {greeks_snapshot.get('message', 'No broker response.')}")

    st.markdown("<div class='bottom-card' style='margin-top:12px;'><div class='bottom-title'>🧾 Broker option chain around selected centre</div><div style='font-size:11px;color:#94a3b8;'>LTP, change, bid/ask, volume and OI are direct SmartAPI FULL-market fields where returned. OI change, PCR, IV, and Greeks are not inferred.</div></div>", unsafe_allow_html=True)
    if chain_snapshot.get("ok"):
        st.caption(broker_chain_caption(chain_snapshot))
    elif chain_snapshot.get("state") == "disconnected":
        st.warning("Angel One is disconnected. The table still shows verified contract identities, but every market field remains unavailable.")
    else:
        st.warning(f"Chain quote snapshot unavailable: {chain_snapshot.get('message', 'Unknown broker error.')}")
    chain_rows = _format_fo_chain_rows(build_fo_chain_rows(chain_pairs, chain_snapshot, selected_contract))
    st.dataframe(pd.DataFrame(chain_rows), width="stretch", hide_index=True, height=420, key=f"fo5_chain_table_{selected_index}_{selected_expiry}_{center_strike}_{chain_depth}")

    bottom_left, bottom_right = st.columns([6.0, 4.0])
    with bottom_left:
        st.markdown("<div class='bottom-card'><div class='bottom-title'>📉 Selected CE/PE premium chart and conditions</div><div style='font-size:11px;color:#94a3b8;'>This chart and its ten checks use only the selected contract's current-session broker candles.</div></div>", unsafe_allow_html=True)
        option_figure = _fo_candle_figure(option_candles, f"{selected_contract['symbol']} · Angel One current session")
        if option_figure is not None:
            st.plotly_chart(option_figure, width="stretch", theme=None, config={"scrollZoom": True, "displaylogo": False}, key=f"fo5_option_chart_{_fo_widget_suffix(selected_contract)}_{fo_interval}")
            st.caption(broker_snapshot_caption(option_candle_snapshot) + " · current IST session only")
        else:
            st.info(f"Selected premium chart unavailable: {option_candle_snapshot.get('message', 'At least one current-session broker candle is required.')}")
        if option_evidence.get("message"):
            st.caption(option_evidence["message"])
        else:
            st.markdown(evidence_items_html(option_evidence.get("checks", [])), unsafe_allow_html=True)
    with bottom_right:
        _render_fo_risk_controls(selected_contract, selected_snapshot, composite_gate, is_paper_trading, live_orders_armed, broker_client)
        st.caption("SELL calculations reverse price direction correctly, but live short-option submission remains deliberately unavailable until it has a broker margin/product preflight.")

    with st.expander("🔎 What is verified for this F&O view", expanded=False):
        verification_rows = build_fo_verification_rows(
            selected_contract, selected_snapshot, option_candle_snapshot, option_evidence, selected_greeks,
        )
        verification_rows.insert(0, {
            "Field": "Underlying direction", "Value": underlying_evidence.get("summary", {}).get("regime", "INSUFFICIENT_DATA"),
            "Source / status": "Angel One broker index candles only; public chart is context-only",
        })
        verification_rows.insert(1, {
            "Field": "Option chain", "Value": f"{chain_snapshot.get('fetched_count', 0)}/{chain_snapshot.get('requested_count', 0)} quotes",
            "Source / status": broker_chain_caption(chain_snapshot),
        })
        st.dataframe(pd.DataFrame(verification_rows), width="stretch", hide_index=True, key=f"fo5_verification_{_fo_widget_suffix(selected_contract)}_{fo_interval}")


def fetch_broker_account_view(smart_api):
    """Read order/position endpoints only; never submit, modify or cancel."""
    result = {"orders": [], "positions": [], "errors": [], "fetched_at": ist_now().isoformat()}
    if smart_api is None:
        result["errors"] = ["Angel One is disconnected."]
        return result
    fields = {
        "orders": {"orderid": "Order ID", "tradingsymbol": "Symbol", "exchange": "Exchange", "transactiontype": "Side", "ordertype": "Order type", "producttype": "Product", "status": "Status", "quantity": "Qty", "filledshares": "Filled qty", "unfilledshares": "Pending qty", "averageprice": "Average fill", "triggerprice": "SL trigger", "price": "Order price", "updatetime": "Updated"},
        "positions": {"tradingsymbol": "Symbol", "exchange": "Exchange", "producttype": "Product", "netqty": "Net qty", "buyavgprice": "Buy average", "sellavgprice": "Sell average", "ltp": "LTP", "unrealised": "Unrealized P&L", "realised": "Realized P&L"},
    }
    for section, method in (("orders", "orderBook"), ("positions", "position")):
        try:
            if not callable(getattr(smart_api, method, None)):
                result["errors"].append(f"Broker client does not support {section}.")
                continue
            reply = getattr(smart_api, method)()
            if not isinstance(reply, dict) or reply.get("status") is not True:
                result["errors"].append(f"Broker {section} request was not successful. Reconnect or check the broker app.")
                continue
            rows = reply.get("data") or []
            if not isinstance(rows, list):
                result["errors"].append(f"Broker {section} response has an unexpected format.")
                continue
            result[section] = [{label: row.get(key) for key, label in fields[section].items()} for row in rows if isinstance(row, dict)]
        except Exception:
            result["errors"].append(f"Broker {section} request failed. Reconnect and retry.")
    return result


def render_broker_status():
    with st.expander("Broker orders & positions", expanded=False):
        if st.button("↻ Read broker order book and positions", key="read_broker_account", disabled=st.session_state.get("smart_api") is None):
            st.session_state["broker_account_view"] = fetch_broker_account_view(st.session_state.get("smart_api"))
        snapshot = st.session_state.get("broker_account_view")
        if not snapshot:
            st.caption("Connect Angel One, then refresh to see actual fills, pending orders and position P&L.")
        else:
            st.caption(f"Read at {snapshot['fetched_at']} · this snapshot does not refresh automatically.")
            for error in snapshot["errors"]:
                st.warning(error)
            for section in ("orders", "positions"):
                st.markdown(f"**{section.title()}**")
                if snapshot[section]:
                    st.dataframe(pd.DataFrame(snapshot[section]), hide_index=True, width="stretch")
                else:
                    st.caption("No rows returned. Check any request error above.")
            st.caption("SL trigger is shown only when returned on an order. Targets and protective orders are not inferred from a position or from planning inputs.")


def render_data_health(candle_time=None, interval=None):
    source_state = "Unavailable"
    if candle_time:
        try:
            stamp = pd.Timestamp(candle_time)
            stamp = stamp.tz_localize(IST_TIMEZONE) if stamp.tzinfo is None else stamp.tz_convert(IST_TIMEZONE)
            source_state = stamp.strftime("%d %b %Y %H:%M IST")
        except (ValueError, TypeError):
            pass
    session = "connected session · quote availability checked separately" if st.session_state.get("smart_api") is not None else "disconnected"
    st.caption(f"Data health · public technical candles ({interval or 'timeframe unspecified'}) · latest source: {source_state}. Angel: {session}.")


def live_order_is_armed():
    return bool(
        LIVE_ORDER_EXECUTION_ENABLED
        and st.session_state.get("live_orders_armed")
        and st.session_state.get("live_order_acknowledgement")
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
    if not isinstance(response, dict) or response.get("status") is not True:
        return False, "Broker session preflight was not confirmed. Reconnect before submitting."
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
        if not order_id:
            return False, "Submission state unknown: broker returned no order ID. Check the broker order book before retrying.", response
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


def submit_live_equity_from_ui(smart_api, clean_symbol, chart_symbol, quantity, action, order_mode, limit_price, stoploss_points, target_points, product_type, horizon=None):
    """Resolve, quote-check, and submit a confirmed cash-equity order."""
    if horizon is not None:
        policy_ok, policy_message = validate_equity_trade_policy(horizon, action, order_mode, product_type)
        if not policy_ok:
            return False, policy_message, None, None
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
    # Last reviewed full SENSEX backup.  It is never called live unless the
    # official BSE CSV above is successfully checked on the current IST day.
    "SENSEX": list(BSE_SENSEX_YAHOO_SYMBOLS.values()),
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
        "publisher": "Official NSE Indices public constituent CSV",
    },
    "BANK NIFTY": {
        "url": "https://www.niftyindices.com/IndexConstituent/ind_niftybanklist.csv",
        "minimum": 8,
        "maximum": 20,
        "publisher": "Official NSE Indices public constituent CSV",
    },
    "SENSEX": {
        "url": "https://www.bseindices.com/AsiaIndexAPI/api/Codewise_IndicesDownload/w?code=16",
        "minimum": 30,
        "maximum": 30,
        "parser": "bse_sensex",
        "publisher": "Official BSE SENSEX constituent CSV (reviewed NSE chart-symbol map)",
    },
    "NIFTY MIDCAP 50": {
        "url": "https://www.niftyindices.com/IndexConstituent/ind_niftymidcap50list.csv",
        "minimum": 45,
        "maximum": 55,
        "publisher": "Official NSE Indices public constituent CSV",
    },
}

# Kept as a compatibility alias for any future chart helper.  Unlike the old
# generic F&O list, it intentionally contains only the five supported index
# products and never individual equities.
FO_UNIVERSE = {name: spec["chart_symbol"] for name, spec in FO_INDEX_UNIVERSE.items()}

HORIZON_MAP = {
    "Intraday (15-Min)": ("5d", "15m", 3),
    "BTST (1-Hour)": ("1mo", "60m", 4),
    "Weekly Swing (Daily)": ("6mo", "1d", 5),
    "Mid Term (Daily / 2Y)": ("2y", "1d", 8),
    "Long Term (Weekly)": ("5y", "1wk", 10)
}
CHART_TIMEFRAME_MAP = {
    "5 minute": ("5d", "5m", 3),
    "15 minute": ("5d", "15m", 3),
    "1 hour": ("1mo", "60m", 4),
    "1 day": ("1y", "1d", 5),
    "1 week": ("5y", "1wk", 10),
}
FO_INTERVAL_OPTIONS = {
    "5 minute": "FIVE_MINUTE",
    "15 minute": "FIFTEEN_MINUTE",
}
OFFICIAL_NEWS_SOURCES = [
    {"name": "SEBI", "category": "Regulatory", "url": "https://www.sebi.gov.in/sebirss.xml"},
    {"name": "RBI", "category": "Macro", "url": "https://rbi.org.in/pressreleases_rss.xml"},
    {"name": "PIB", "category": "Government", "url": "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=1"},
]
GLOBAL_MARKET_WATCH = {
    "S&P 500": "^GSPC",
    "NASDAQ": "^IXIC",
    "Dow Jones": "^DJI",
    "Nikkei 225": "^N225",
    "Hang Seng": "^HSI",
}


def load_logo_data_uri():
    logo_path = os.path.join(APP_DIRECTORY, "mahi-trading-logo.png")
    try:
        with open(logo_path, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    except OSError:
        return ""


safe_workspace_settings = load_safe_ui_settings()
saved_asset_universe = safe_workspace_settings.get("asset_universe")
if saved_asset_universe == "SENSEX watchlist (bundled)":
    saved_asset_universe = "SENSEX"
if saved_asset_universe in FALLBACK_INDEX_BASKETS:
    st.session_state.setdefault("asset_universe", saved_asset_universe)
if safe_workspace_settings.get("trading_horizon") in HORIZON_MAP:
    st.session_state.setdefault("trading_horizon", safe_workspace_settings["trading_horizon"])
if safe_workspace_settings.get("execution_mode") in {"📝 Paper Trading", "⚡ Live Broker"}:
    st.session_state.setdefault("execution_mode", safe_workspace_settings["execution_mode"])
if safe_workspace_settings.get("chart_timeframe") in CHART_TIMEFRAME_MAP:
    st.session_state.setdefault("chart_timeframe", safe_workspace_settings["chart_timeframe"])
if safe_workspace_settings.get("fo_interval") in FO_INTERVAL_OPTIONS:
    st.session_state.setdefault("fo_interval", safe_workspace_settings["fo_interval"])
    st.session_state.setdefault("fo5_interval", safe_workspace_settings["fo_interval"])
if safe_workspace_settings.get("fo5_index") in FO_INDEX_UNIVERSE:
    st.session_state.setdefault("fo5_index", safe_workspace_settings["fo5_index"])
if safe_workspace_settings.get("fo5_interval") in FO_INTERVAL_OPTIONS:
    st.session_state.setdefault("fo5_interval", safe_workspace_settings["fo5_interval"])
if safe_workspace_settings.get("fo5_public_timeframe") in FO_PUBLIC_CHART_OPTIONS:
    st.session_state.setdefault("fo5_public_timeframe", safe_workspace_settings["fo5_public_timeframe"])
if safe_workspace_settings.get("fo5_band") in {"ATM ±5 strikes", "ATM ±10 strikes"}:
    st.session_state.setdefault("fo5_band", safe_workspace_settings["fo5_band"])

# --- Header Box ---
timestamp = ist_now().strftime("%Y-%m-%d %H:%M:%S")
logo_data_uri = load_logo_data_uri()
logo_html = f'<img class="brand-logo" src="{logo_data_uri}" alt="Mahi Trading logo">' if logo_data_uri else ""

st.markdown(f"""<div class="header-box">
<div class="brand-block">
{logo_html}
<div>
  <div class="brand-title">Mahi <span class="brand-accent">Trading</span></div>
  <div class="brand-sub">Multi-Asset Market Intelligence &amp; Verified Risk Planning</div>
</div>
</div>
<div style="text-align:right;">
  <span class="status-badge">● DATA SOURCE CHECK</span>
<div style="font-size:12px; color:#94a3b8; margin-top:4px;">{timestamp} IST</div>
</div>
</div>""", unsafe_allow_html=True)

with st.sidebar:
    st.caption("Session risk budgets · optional warnings")
    risk_per_trade = st.number_input("Maximum planned loss per trade (₹)", min_value=0.0, value=0.0, step=100.0, key="risk_per_trade")
    risk_daily_loss = st.number_input("Paper daily loss budget (₹)", min_value=0.0, value=0.0, step=100.0, key="risk_daily_loss")
    risk_max_trades = st.number_input("Paper trades per day", min_value=0, value=0, step=1, key="risk_max_trades")
    st.session_state["risk_budgets"] = {"per_trade": risk_per_trade, "daily_loss": risk_daily_loss, "max_trades": risk_max_trades}
    st.caption("0 leaves a budget unset. Budgets warn; they do not submit or cancel orders. Paper results exclude charges and slippage.")

# Main Tabs
main_tab_equity, main_tab_fo, tab_paper_ledger, tab_backtest, tab_chart, tab_news = st.tabs([
    "📈 Equity Intelligence", 
    "🎯 F&O Intelligence (Derivatives)", 
    "📝 Paper Trading Portfolio",
    "📊 Backtest Engine", 
    "📉 Technical S/R Charts",
    "🗞️ Market Brief & News"
])

with main_tab_equity:
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

    u_col1, u_col2, u_col3, u_col4, u_col5 = st.columns([3.5, 3.5, 2.4, 1.6, 1.7])
    with u_col1:
        selected_basket = st.selectbox("Select Asset Universe", list(FALLBACK_INDEX_BASKETS.keys()), label_visibility="collapsed", key="asset_universe")
    with u_col2:
        selected_horizon = st.selectbox("Select Trading Horizon", list(HORIZON_MAP.keys()), label_visibility="collapsed", key="trading_horizon")
    with u_col3:
        exec_env = st.selectbox("Execution Mode", ["📝 Paper Trading", "⚡ Live Broker"], label_visibility="collapsed", key="execution_mode")
    with u_col4:
        if st.button("↻ Refresh list", width="stretch", key="refresh_index_membership"):
            st.session_state["force_index_basket_refresh"] = True
            load_active_index_basket.clear()
            st.rerun()
    with u_col5:
        if st.button("↻ Refresh data", width="stretch", key="refresh_verified_market_data"):
            st.session_state["force_index_basket_refresh"] = True
            st.cache_data.clear()
            clear_all_short_lived_broker_cache()
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

    persist_c1, persist_c2 = st.columns([3, 7])
    with persist_c1:
        if st.button("💾 Remember workspace", width="stretch", key="save_safe_ui_settings"):
            save_error = save_safe_ui_settings({
                "asset_universe": selected_basket,
                "trading_horizon": selected_horizon,
                "execution_mode": exec_env,
                "chart_timeframe": st.session_state.get("chart_timeframe", "15 minute"),
                "fo_interval": st.session_state.get("fo_interval", "5 minute"),
                "fo5_index": st.session_state.get("fo5_index", "NIFTY 50"),
                "fo5_interval": st.session_state.get("fo5_interval", "5 minute"),
                "fo5_public_timeframe": st.session_state.get("fo5_public_timeframe", "15 minute"),
                "fo5_band": st.session_state.get("fo5_band", "ATM ±5 strikes"),
            })
            if save_error:
                st.error(save_error)
            else:
                st.success("Saved non-sensitive workspace settings. Broker credentials are not stored here.")
    with persist_c2:
        st.caption("Refresh data clears delayed/public cache and broker snapshots, then reloads verified sources. Your broker session may still need a fresh login after expiry.")

    is_paper_trading = (exec_env == "📝 Paper Trading")
    live_orders_armed = live_order_is_armed()
    period, interval, extrema_order = HORIZON_MAP[selected_horizon]

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
            + ". They remain visible as DATA UNAVAILABLE; no price was substituted."
        )
    if equity_daily_message:
        st.caption("Daily public chart data is unavailable; the macro check, where shown, explicitly uses the intraday 50 EMA fallback.")

    df_results, checklists, current_live_prices = evaluate_equity_intelligence_scan(
        symbols, data, daily_data, extrema_order, interval=interval,
    )
    # Only current-session intraday observations may trigger a simulated exit.
    # Historical daily/weekly closes are context, not executable current prices.
    paper_observations = {}
    if interval.endswith("m"):
        for symbol in symbols:
            frame = _market_frame_for_symbol(data, symbol)
            if frame.empty:
                continue
            stamp = pd.Timestamp(frame.index[-1])
            stamp = stamp.tz_localize("Asia/Kolkata") if stamp.tzinfo is None else stamp.tz_convert("Asia/Kolkata")
            age = (pd.Timestamp(ist_now()) - stamp).total_seconds()
            if stamp.date() == ist_now().date() and 0 <= age <= (int(interval[:-1]) + 5) * 60:
                clean = symbol.removesuffix(".NS").removesuffix(".BO")
                paper_observations[clean] = current_live_prices.get(clean)
    evaluate_paper_positions(paper_observations)

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
            render_data_health(stock.get("candle_time"), interval)
            render_setup_signal(stock.get("signal", {}))
            chg_c = "#34d399" if stock['chg'] >= 0 else "#fb7185"

            atr_val = stock['atr']
            stop_points = round(float(1.5 * atr_val), 1)
            target_points = round(float(4.5 * atr_val), 1)  # 1:3 RRR

            equity_trade_policy = trade_policy_for_horizon(selected_horizon)
            sizing_sides = ["BUY"] if equity_trade_policy["long_term"] else ["BUY", "SELL"]
            default_preview_idx = 0 if stock['evidence_summary']['regime'] != "BEARISH" or equity_trade_policy["long_term"] else 1
            preview_action = st.radio(
                "Sizing direction",
                sizing_sides,
                index=default_preview_idx,
                horizontal=True,
                key=f"equity_preview_side_{active_sym}",
                help="Choose BUY or SELL first: the entry, stop, target, P&L, confirmation, and submit button below all use this one side.",
            )
            preview_entry = st.session_state.get(f"limit_{active_sym}", stock['ltp'])
            try:
                if equity_trade_policy["long_term"]:
                    inspector_preview = calculate_investment_plan(preview_entry,
                        st.session_state.get(f"investment_sl_{active_sym}", max(.01, preview_entry-stop_points)),
                        st.session_state.get(f"investment_tp_{active_sym}", preview_entry+target_points), 1)
                else:
                    inspector_preview = calculate_directional_preview(preview_action, preview_entry,
                        st.session_state.get(f"sl_{active_sym}", stop_points),
                        st.session_state.get(f"tp_{active_sym}", target_points), 1)
                inspector_levels = f"{preview_action} plan SL <span style='color:#fb7185'>₹{inspector_preview['stop_price']:.2f}</span> · target <span style='color:#34d399'>₹{inspector_preview['target_price']:.2f}</span>"
            except ValueError:
                inspector_levels = "Check stop and target values in the planner below."

            items_html = evidence_items_html(stock['checks'])

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
                    <div>VWAP: <strong style="color:#00f2fe">{_fo_currency(stock['vwap'])}</strong></div>
                    <div>ORB HIGH: <strong style="color:#fbbf24">₹{stock['orb_high']:.2f}</strong></div>
                    <div>ATR(14): <strong>₹{atr_val:.2f}</strong></div>
                </div>
                <div style="display:flex; justify-content:space-between; font-size:12px; margin-bottom: 12px; padding: 8px 12px; background:rgba(15, 23, 42, 0.7); border:1px solid rgba(255,255,255,0.06); border-radius:6px;">
                    <div>{inspector_levels}</div>
                </div>
                <div style="font-size:11px; font-weight:700; color:#94a3b8; text-transform:uppercase; margin-bottom:6px; letter-spacing:0.5px;">
                    EVIDENCE: <span style="color:#34d399;">{stock['buy_score']} BUY</span> &nbsp;·&nbsp; <span style="color:#fb7185;">{stock['sell_score']} SELL</span> &nbsp;·&nbsp; <span style="color:#fbbf24;">{stock['evidence_summary']['neutral_count']} NEUTRAL</span>
                    <span style="float:right; color:#cbd5e1;">{stock['evidence_summary']['regime']}</span>
                </div>
                {items_html}
            </div>
            """, unsafe_allow_html=True)

            st.markdown("""
            <div style="margin-top:12px; padding:10px 14px; background:linear-gradient(90deg, rgba(15,23,42,0.8), rgba(30,27,75,0.8)); border:1px solid rgba(255,255,255,0.08); border-radius:6px;">
                <span style="font-size:13px; font-weight:700; color:#fff;">💰 Amount & P&L Sizing Engine</span>
            </div>
            """, unsafe_allow_html=True)

            if equity_trade_policy["long_term"]:
                order_mode = "Regular Market"
                st.caption("Long-term delivery BUY · stop and target below are an editable investment plan.")
            else:
                order_mode = st.radio("Order Type", ["Bracket (ROBO Auto-Exit)", "Regular Market"], horizontal=True, label_visibility="collapsed", key=f"mode_{active_sym}")

            calc_c1, calc_c2 = st.columns(2)
            with calc_c1:
                trade_qty = st.number_input("Quantity", min_value=1, value=10, step=1, key=f"qty_{active_sym}")
            with calc_c2:
                trade_limit = st.number_input("Entry Price (₹)", min_value=0.01, value=float(round(stock['ltp'], 2)), step=0.5, key=f"limit_{active_sym}")

            render_trade_notes(active_sym, stock.get("signal", {}).get("pattern") or stock["setup"], selected_horizon, "Public chart context / user entry plan", auto_exit=not equity_trade_policy["long_term"])

            if order_mode == "Bracket (ROBO Auto-Exit)":
                sl_c, tp_c, trail_c = st.columns(3)
                with sl_c:
                    in_sl_pts = st.number_input("SL Points (₹)", min_value=0.5, value=float(stop_points), step=0.5, key=f"sl_{active_sym}")
                with tp_c:
                    in_tp_pts = st.number_input("Target Points (₹)", min_value=1.0, value=float(target_points), step=0.5, key=f"tp_{active_sym}")
                with trail_c:
                    in_trail = st.number_input("Trail (₹)", min_value=0.0, value=1.0, step=0.5, key=f"trail_{active_sym}")
                try:
                    equity_preview = calculate_directional_preview(preview_action, trade_limit, in_sl_pts, in_tp_pts, trade_qty)
                except ValueError as exc:
                    equity_preview = None
                    st.error(f"Sizing needs valid values: {exc}")

                if equity_preview:
                    render_risk_budget(abs(equity_preview["loss_pnl"]), is_paper_trading)
                    total_notional = equity_preview["gross_notional"]
                    expected_profit = equity_preview["target_pnl"]
                    expected_loss = abs(equity_preview["loss_pnl"])
                    reward_risk_ratio = round(expected_profit / expected_loss, 2) if expected_loss > 0 else 0.0
                    notional_label = "BUY entry value" if preview_action == "BUY" else "Estimated sale value — intraday short; broker margin not calculated"
                    st.markdown(f"""
                    <div class="calc-box">
                        <div class="calc-row"><span style="color:#94a3b8;">{notional_label}:</span><strong style="color:#ffffff; font-size:14px;">₹{total_notional:,.2f}</strong></div>
                        <div class="calc-row"><span style="color:#94a3b8;">{preview_action} entry / stop / target:</span><strong style="color:#ffffff;">₹{equity_preview['entry_price']:.2f} / <span style="color:#fb7185">₹{equity_preview['stop_price']:.2f}</span> / <span style="color:#34d399">₹{equity_preview['target_price']:.2f}</span></strong></div>
                        <div class="calc-row"><span style="color:#94a3b8;">Illustrative P&L at target:</span><strong style="color:#34d399; font-size:14px;">+₹{expected_profit:,.2f}</strong></div>
                        <div class="calc-row"><span style="color:#94a3b8;">Illustrative P&L at stop:</span><strong style="color:#fb7185; font-size:14px;">-₹{expected_loss:,.2f}</strong></div>
                        <div class="calc-row" style="border-top:1px solid rgba(255,255,255,0.06); margin-top:4px; padding-top:4px;"><span style="color:#94a3b8;">Risk-to-reward:</span><strong style="color:#00f2fe;">1 : {reward_risk_ratio}</strong></div>
                    </div>
                    """, unsafe_allow_html=True)
                    live_confirm = False
                    if not is_paper_trading:
                        st.markdown("<div class='live-ready-card'>⚡ <strong>LIVE ORDER MODE ARMED</strong> — a broker response means submitted, not filled.</div>" if live_orders_armed else "<div class='live-arm-card'>🔒 <strong>LIVE ORDER MODE NOT ARMED</strong> — connect and arm Angel One below.</div>", unsafe_allow_html=True)
                        live_confirm = st.checkbox(
                            f"I understand: submit LIVE {preview_action} {active_sym} ({trade_qty} qty) LIMIT ₹{trade_limit:.2f}; SL offset ₹{in_sl_pts:.2f}; target offset ₹{in_tp_pts:.2f}",
                            key=f"confirm_robo_{preview_action.lower()}_{active_sym}", disabled=not live_orders_armed,
                        )
                        st.caption("ROBO is submitted as LIMIT + BO. Trailing is a planning value only until broker-specific trailing behavior is verified.")
                    btn_prefix = "📝 PAPER" if is_paper_trading else "⚡ LIVE"
                    button_key = f"btn_buy_{active_sym}" if preview_action == "BUY" else f"btn_sell_{active_sym}"
                    action_marker = "🟢" if preview_action == "BUY" else "🔴"
                    submit_bracket = st.button(
                        f"{action_marker} {btn_prefix} {preview_action} ROBO {active_sym}", width="stretch", key=button_key,
                        disabled=not is_paper_trading and not (live_orders_armed and live_confirm),
                    )
                    if submit_bracket:
                        if is_paper_trading:
                            ok, msg = place_paper_order(active_sym, preview_action, trade_qty, trade_limit, in_sl_pts, in_tp_pts, in_trail)
                            (st.success if ok else st.error)(f"{'✅' if ok else '❌'} {msg}")
                        else:
                            with st.spinner("Resolving broker contract and submitting your confirmed ROBO order..."):
                                success, message, details, broker_ltp = submit_live_equity_from_ui(
                                    st.session_state.get("smart_api"), active_sym, stock["sym"], trade_qty, preview_action,
                                    "ROBO", trade_limit, in_sl_pts, in_tp_pts, "INTRADAY", selected_horizon,
                                )
                            if success:
                                order_id = details.get("order_id") if isinstance(details, dict) else ""
                                st.session_state["last_live_submission"] = {"symbol": active_sym, "action": preview_action, "kind": "ROBO", "order_id": order_id, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
                                st.success(f"✅ LIVE order submitted to Angel One{f' · Order ID: {order_id}' if order_id else ''}. Check the broker order book for final status.")
                                if broker_ltp and broker_ltp.get("ok"):
                                    st.caption(f"Broker LTP verified at submission: ₹{broker_ltp['ltp']:.2f}")
                            else:
                                st.error(f"Live order was not submitted: {message}")
            else:
                plan_valid = True
                paper_stop_points, paper_target_points = stop_points, target_points
                if equity_trade_policy["long_term"]:
                    plan_sl, plan_target = st.columns(2)
                    with plan_sl:
                        investment_stop = st.number_input("Stop-loss / risk exit (₹)", min_value=0.01,
                            value=round(max(0.01, trade_limit - stop_points), 2), step=0.5, key=f"investment_sl_{active_sym}")
                    with plan_target:
                        investment_target = st.number_input("Target / review price (₹)", min_value=0.01,
                            value=round(trade_limit + target_points, 2), step=0.5, key=f"investment_tp_{active_sym}")
                    try:
                        investment_plan = calculate_investment_plan(trade_limit, investment_stop, investment_target, trade_qty)
                        paper_stop_points = trade_limit - investment_stop
                        paper_target_points = investment_target - trade_limit
                        st.markdown(f"<div class='calc-box'><div class='calc-row'><span>Stop / target distance</span><strong style='color:#fbbf24'>−{investment_plan['stop_percent']:.2f}% / +{investment_plan['target_percent']:.2f}%</strong></div><div class='calc-row'><span>Planned loss</span><strong style='color:#fb7185'>₹{abs(investment_plan['loss_pnl']):,.2f}</strong></div><div class='calc-row'><span>Potential profit</span><strong style='color:#34d399'>₹{investment_plan['target_pnl']:,.2f}</strong></div><div class='calc-row'><span>Risk : reward</span><strong>1 : {investment_plan['target_pnl'] / abs(investment_plan['loss_pnl']):.2f}</strong></div></div>", unsafe_allow_html=True)
                        render_risk_budget(abs(investment_plan["loss_pnl"]), is_paper_trading)
                    except ValueError as exc:
                        plan_valid = False
                        st.error(str(exc))
                    st.caption("Planning only: these levels do not create broker SL/target orders or automatic paper exits. Review and exit the delivery holding separately. P&L excludes costs and gaps.")
                else:
                    render_risk_budget(trade_qty * stop_points, is_paper_trading)
                regular_notional = trade_qty * trade_limit
                notional_label = "Total delivery amount" if equity_trade_policy["long_term"] else ("Estimated buy value" if preview_action == "BUY" else "Estimated sale value — broker margin not calculated")
                st.markdown(f"""<div class="calc-box"><div class="calc-row"><span style="color:#94a3b8;">{notional_label}:</span><strong style="color:#ffffff; font-size:14px;">₹{regular_notional:,.2f}</strong></div><div class="calc-row"><span style="color:#94a3b8;">Sizing side:</span><strong style="color:{'#34d399' if preview_action == 'BUY' else '#fb7185'};">{preview_action}</strong></div></div>""", unsafe_allow_html=True)
                if equity_trade_policy["long_term"]:
                    prod = "DELIVERY"
                    st.caption("Delivery BUY uses the broker's execution price; entry above is planning-only.")
                else:
                    prod = st.selectbox("Product", ["INTRADAY", "DELIVERY"], key=f"prod_{active_sym}")
                    st.caption("Market orders use the broker's execution price; entry above is planning-only.")
                live_confirm = False
                if not is_paper_trading:
                    st.markdown("<div class='live-ready-card'>⚡ <strong>LIVE MARKET MODE ARMED</strong> — market orders may be converted by the broker to price-protection limits.</div>" if live_orders_armed else "<div class='live-arm-card'>🔒 <strong>LIVE MARKET MODE NOT ARMED</strong> — enable it in the gateway below.</div>", unsafe_allow_html=True)
                    live_confirm = st.checkbox(
                        f"I understand: submit LIVE MARKET {preview_action} {active_sym} ({trade_qty} qty) as {prod}",
                        key=f"confirm_market_{preview_action.lower()}_{active_sym}", disabled=not live_orders_armed,
                    )
                market_prefix = "📝 PAPER" if is_paper_trading else "⚡ LIVE"
                market_key = f"mkt_buy_{active_sym}" if preview_action == "BUY" else f"mkt_sell_{active_sym}"
                action_marker = "🟢" if preview_action == "BUY" else "🔴"
                submit_market = st.button(
                    f"{action_marker} {market_prefix} {preview_action} MARKET {active_sym}", width="stretch", key=market_key,
                    disabled=not plan_valid or (not is_paper_trading and not (live_orders_armed and live_confirm)),
                )
                if submit_market:
                    if is_paper_trading:
                        ok, msg = place_paper_order(active_sym, preview_action, trade_qty, trade_limit, paper_stop_points, paper_target_points)
                        (st.success if ok else st.error)(f"{'✅' if ok else '❌'} {msg}")
                    else:
                        with st.spinner("Resolving broker contract and submitting your confirmed market order..."):
                            success, message, details, broker_ltp = submit_live_equity_from_ui(
                                st.session_state.get("smart_api"), active_sym, stock["sym"], trade_qty, preview_action,
                                "NORMAL", trade_limit, stop_points, target_points, prod, selected_horizon,
                            )
                        if success:
                            order_id = details.get("order_id") if isinstance(details, dict) else ""
                            st.session_state["last_live_submission"] = {"symbol": active_sym, "action": preview_action, "kind": "MARKET", "order_id": order_id, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
                            st.success(f"✅ LIVE market order submitted to Angel One{f' · Order ID: {order_id}' if order_id else ''}. Check the broker order book for final status.")
                            if broker_ltp and broker_ltp.get("ok"):
                                st.caption(f"Broker LTP verified at submission: ₹{broker_ltp['ltp']:.2f}")
                        else:
                            st.error(f"Live order was not submitted: {message}")


# =========================================================================
# TAB 2: FOCUSED FIVE-INDEX F&O INTRADAY DESK
# =========================================================================
with main_tab_fo:
    fo_exec_env = st.selectbox("F&O execution mode", ["📝 Paper Trading", "⚡ Live Broker"], key="fo_execution_mode")
    render_fo_index_desk(fo_exec_env == "📝 Paper Trading", live_orders_armed, compact=True)

# Previous generic-contract table retained as unreachable reference while the
# focused five-index desk above is tested. It must not render or fetch data.
if False:
    st.markdown("""
    <div class="disclaimer-banner"><strong>F&amp;O Contract Desk</strong> — choose an Angel One NFO contract from the left. The right panel evaluates only that selected CE/PE premium with real broker data. Unselected rows show master metadata only; no premium, Greeks, OI, or score is guessed.</div>
    """, unsafe_allow_html=True)
    fo_controls_1, fo_controls_2 = st.columns([3, 2])
    with fo_controls_1:
        fo_interval_label = st.selectbox("Intraday candle interval", list(FO_INTERVAL_OPTIONS.keys()), key="fo_interval")
        fo_interval = FO_INTERVAL_OPTIONS[fo_interval_label]
    with fo_controls_2:
        if st.button("↻ Refresh Angel One master", width="stretch", key="refresh_fo_master_split"):
            load_angel_option_master.clear()
            st.rerun()

    master_rows, master_error = load_angel_option_master()
    if master_error:
        st.error(master_error)
        st.caption("No option price, OI, Greek, margin, or technical value is substituted while the official master is unavailable.")
    else:
        option_underlyings = extract_option_underlyings(master_rows)
        if not option_underlyings:
            st.warning("No current non-expired NFO option underlying was available in Angel One's master.")
        else:
            filter_1, filter_2, filter_3, filter_4 = st.columns([2.5, 2.2, 1.6, 3.7])
            with filter_1:
                selected_underlying = st.selectbox("Underlying", option_underlyings, key="fo_underlying")
            option_contracts = extract_option_contracts(master_rows, selected_underlying)
            expiry_values = list(dict.fromkeys(contract["expiry_raw"] for contract in option_contracts))
            expiry_labels = {contract["expiry_raw"]: contract["expiry_label"] for contract in option_contracts}
            with filter_2:
                selected_expiry = st.selectbox(
                    "Expiry", expiry_values,
                    format_func=lambda value: expiry_labels[value],
                    key=f"fo_expiry_table_{selected_underlying}",
                )
            with filter_3:
                side_filter = st.selectbox(
                    "CE / PE", ["All", "CE", "PE"],
                    format_func=lambda value: "All sides" if value == "All" else "CALL (CE)" if value == "CE" else "PUT (PE)",
                    key=f"fo_side_table_{selected_underlying}_{selected_expiry}",
                )
            with filter_4:
                contract_search = st.text_input(
                    "Find contract / strike", placeholder="Search symbol or strike…",
                    key=f"fo_contract_search_{selected_underlying}_{selected_expiry}",
                )

            visible_contracts = [
                contract for contract in option_contracts
                if contract["expiry_raw"] == selected_expiry and (side_filter == "All" or contract["side"] == side_filter)
            ]
            if contract_search.strip():
                search_term = contract_search.strip().upper()
                visible_contracts = [
                    contract for contract in visible_contracts
                    if search_term in contract["symbol"].upper() or search_term in f"{contract['strike']:g}".upper()
                ]
            visible_contracts = sorted(visible_contracts, key=lambda contract: (contract["strike"], contract["side"], contract["symbol"]))

            if not visible_contracts:
                st.info("No Angel One contracts match these filters. Change expiry, side, or the search text.")
            else:
                selected_contract = choose_fo_contract(
                    visible_contracts, st.session_state.get("fo_selected_contract_token"),
                )
                st.session_state["fo_selected_contract_token"] = str(selected_contract["token"])
                broker_client = st.session_state.get("smart_api")
                option_snapshot = get_option_snapshot_for_ui(broker_client, selected_contract)
                candle_snapshot = get_option_candles_for_ui(broker_client, selected_contract, fo_interval)
                option_candles = (
                    normalise_broker_option_candles(candle_snapshot["candles"])
                    if candle_snapshot.get("ok") else pd.DataFrame()
                )
                option_evidence = evaluate_intraday_option_evidence(option_candles)
                fo_trade_gate = build_fo_trade_gate(option_snapshot, candle_snapshot, option_evidence)
                selected_greeks = {}
                greeks_snapshot = get_option_greeks_for_ui(broker_client, selected_underlying, selected_expiry)
                if greeks_snapshot.get("ok"):
                    selected_greeks = selected_contract_greeks(greeks_snapshot["rows"], selected_contract)

                fo_left, fo_right = st.columns([6.6, 3.4])
                with fo_left:
                    st.markdown("<h4 style='color:#fff; margin:0 0 8px;'>Broker-listed F&amp;O contracts</h4>", unsafe_allow_html=True)
                    fo_watchlist_rows = build_fo_contract_watchlist_rows(
                        visible_contracts, selected_contract, option_snapshot, candle_snapshot, option_evidence,
                    )
                    fo_grid = st.dataframe(
                        pd.DataFrame(fo_watchlist_rows),
                        width="stretch",
                        hide_index=True,
                        on_select="rerun",
                        selection_mode="single-row",
                        column_config={
                            "Strike": st.column_config.NumberColumn(format="₹%.2f"),
                            "Tick": st.column_config.NumberColumn(format="₹%.2f"),
                        },
                        height=620,
                        key=f"fo_contract_grid_{selected_underlying}_{selected_expiry}_{side_filter}",
                    )
                    if fo_grid.selection and fo_grid.selection.rows:
                        selected_row = fo_grid.selection.rows[0]
                        if selected_row < len(visible_contracts):
                            next_token = str(visible_contracts[selected_row]["token"])
                            if next_token != str(selected_contract["token"]):
                                st.session_state["fo_selected_contract_token"] = next_token
                                st.rerun()
                    st.caption(
                        f"{len(visible_contracts)} broker-listed contracts shown · selected: {selected_contract['symbol']}. "
                        "Only this selected row can have broker premium and technical fields."
                    )

                with fo_right:
                    if st.button("↻ Refresh selected", width="stretch", key=f"refresh_fo_split_{selected_contract['token']}_{fo_interval}"):
                        clear_selected_option_broker_cache(selected_contract, selected_underlying, selected_expiry, fo_interval)
                        st.rerun()

                    option_row = build_fo_intelligence_row(
                        selected_contract, option_snapshot, candle_snapshot, option_evidence,
                    )
                    premium_text = option_row["Premium"]
                    premium_color = "#34d399" if option_snapshot.get("ok") else "#fbbf24"
                    st.markdown(f"""
                    <div class="inspector-card">
                        <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:10px;">
                            <div>
                                <div style="font-size:19px; font-weight:800; color:#fff; font-family:'JetBrains Mono', monospace; word-break:break-word;">{html.escape(selected_contract['symbol'])}</div>
                                <div style="font-size:25px; font-weight:800; color:{premium_color}; margin:4px 0;">{html.escape(premium_text)}</div>
                            </div>
                            <span style="background:rgba(0,242,254,0.12); border:1px solid rgba(0,242,254,0.30); color:#67e8f9; font-size:10px; padding:4px 7px; border-radius:6px; font-weight:800;">{html.escape(option_row['Setup'])}</span>
                        </div>
                        <div style="display:flex; justify-content:space-between; gap:8px; flex-wrap:wrap; font-size:11px; margin:10px 0; padding:8px 10px; background:rgba(30,41,59,0.6); border-radius:6px;">
                            <span>Expiry <strong>{html.escape(selected_contract['expiry_label'])}</strong></span>
                            <span>{html.escape('CALL (CE)' if selected_contract['side'] == 'CE' else 'PUT (PE)')}</span>
                            <span>Strike <strong>₹{selected_contract['strike']:,.2f}</strong></span>
                            <span>Lot <strong>{selected_contract['lot_size']}</strong></span>
                        </div>
                        <div style="font-size:11px; font-weight:700; color:#94a3b8; text-transform:uppercase; letter-spacing:0.4px;">
                            {html.escape(option_row['Trend'])} · <span style="color:#34d399;">{html.escape(option_row['Buy'])} BUY</span> · <span style="color:#fb7185;">{html.escape(option_row['Sell'])} SELL</span>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    if option_snapshot.get("ok"):
                        st.caption(broker_snapshot_caption(option_snapshot))
                    elif option_snapshot.get("state") == "disconnected":
                        st.warning("Connect Angel One below to load this selected contract's real LTP, intraday chart, Greeks, and OI. No stale or estimated premium is used.")
                    else:
                        st.error(f"Selected-contract price unavailable: {option_snapshot.get('message', 'Unknown broker error.')}")

                    st.markdown(
                        """
                        <div class="fo-trade-gate {tone}">
                          <div class="fo-gate-heading">
                            <span>🛡️ F&amp;O TRADE GATE</span>
                            <span class="fo-gate-decision">{decision_note}</span>
                          </div>
                          <div class="fo-gate-grid">
                            <div class="fo-gate-stat"><div class="fo-gate-label">Decision</div><div class="fo-gate-value">{decision}</div></div>
                            <div class="fo-gate-stat"><div class="fo-gate-label">Conditions</div><div class="fo-gate-value">{conditions}</div><div class="fo-gate-note">{condition_detail}</div></div>
                            <div class="fo-gate-stat"><div class="fo-gate-label">Risk status</div><div class="fo-gate-value">{risk_status}</div><div class="fo-gate-note">Selected contract only</div></div>
                          </div>
                          <div class="fo-gate-message">{message}</div>
                        </div>
                        """.format(**{key: html.escape(str(value)) for key, value in fo_trade_gate.items()}),
                        unsafe_allow_html=True,
                    )

                    st.markdown("<div class='bottom-card' style='margin-top:12px;'><div class='bottom-title'>Selected premium conditions</div><div style='font-size:11px;color:#94a3b8;'>Buy/Sell scores are for this CE/PE premium only—not the underlying direction, PCR, or option-chain flow.</div></div>", unsafe_allow_html=True)
                    if option_evidence.get("message"):
                        st.caption(option_evidence["message"])
                    else:
                        st.markdown(evidence_items_html(option_evidence["checks"]), unsafe_allow_html=True)
                    if selected_greeks:
                        st.caption("Broker-reported only: " + " · ".join(f"{label}: {value}" for label, value in selected_greeks.items()) + f" · {broker_snapshot_caption(greeks_snapshot)}")
                    elif broker_client is not None and not greeks_snapshot.get("ok"):
                        st.caption(f"Broker Greeks/OI unavailable: {greeks_snapshot.get('message', 'Unknown response.')}")

                    if fo_trade_gate["allow_risk_preview"]:
                        st.markdown("<div class='bottom-card' style='margin-top:14px;'><div class='bottom-title'>💰 Selected Option Intraday Risk Preview</div><div style='font-size:11px;color:#94a3b8;'>Uses the selected broker premium. Charges, slippage, margin, and assignment risk are excluded.</div></div>", unsafe_allow_html=True)
                        risk_1, risk_2 = st.columns(2)
                        with risk_1:
                            lots = st.number_input("Lots", min_value=1, value=1, step=1, key=f"fo_lots_{selected_contract['token']}")
                        with risk_2:
                            option_action = st.radio("Premium action", ["BUY", "SELL"], horizontal=True, key=f"fo_action_{selected_contract['token']}")
                        minimum_step = float(selected_contract["tick_size"] or 0.05)
                        risk_3, risk_4 = st.columns(2)
                        with risk_3:
                            default_stop = max(minimum_step, round(option_snapshot["ltp"] * 0.15, 2))
                            option_stop = st.number_input("Risk points (₹)", min_value=minimum_step, value=default_stop, step=minimum_step, key=f"fo_stop_{selected_contract['token']}")
                        with risk_4:
                            option_target = st.number_input("Target points (₹)", min_value=minimum_step, value=float(round(option_stop * 3, 2)), step=minimum_step, key=f"fo_target_{selected_contract['token']}")
                        option_quantity = int(lots) * selected_contract["lot_size"]
                        try:
                            option_preview = calculate_directional_preview(option_action, option_snapshot["ltp"], option_stop, option_target, option_quantity)
                        except ValueError as exc:
                            option_preview = None
                            st.error(f"Option sizing needs valid values: {exc}")
                        if option_preview:
                            premium_label = "Premium outlay before charges" if option_action == "BUY" else "Gross premium received — broker margin is not calculated"
                            st.markdown(f"<div class='calc-box'><div class='calc-row'><span style='color:#94a3b8;'>Quantity:</span><strong>{int(lots)} lot × {selected_contract['lot_size']} = {option_quantity}</strong></div><div class='calc-row'><span style='color:#94a3b8;'>{premium_label}:</span><strong style='color:#00f2fe;'>₹{option_preview['gross_notional']:,.2f}</strong></div><div class='calc-row'><span style='color:#94a3b8;'>{option_action} entry / stop / target:</span><strong>₹{option_preview['entry_price']:.2f} / <span style='color:#fb7185'>₹{option_preview['stop_price']:.2f}</span> / <span style='color:#34d399'>₹{option_preview['target_price']:.2f}</span></strong></div><div class='calc-row'><span style='color:#94a3b8;'>Illustrative P&amp;L target / stop:</span><strong><span style='color:#34d399'>+₹{option_preview['target_pnl']:,.2f}</span> / <span style='color:#fb7185'>₹{option_preview['loss_pnl']:,.2f}</span></strong></div></div>", unsafe_allow_html=True)
                            if option_action == "BUY" and not fo_trade_gate["allow_long_entry"]:
                                st.warning(f"BUY is locked by the F&O Trade Gate: {fo_trade_gate['decision']}. These are planning values only.")
                            if is_paper_trading:
                                paper_key = f"paper_option_{option_action.lower()}_{selected_contract['token']}"
                                paper_buy_locked = option_action == "BUY" and not fo_trade_gate["allow_long_entry"]
                                if st.button(f"{'🟢' if option_action == 'BUY' else '🔴'} 📝 Log {option_action} paper position", width="stretch", key=paper_key, disabled=paper_buy_locked):
                                    ok, message = place_paper_order(selected_contract["symbol"], option_action, option_quantity, option_snapshot["ltp"], option_stop, option_target)
                                    (st.success if ok else st.error)(f"{'✅' if ok else '❌'} {message}")
                            elif option_action == "SELL":
                                st.warning("Live short-option submission is unavailable until a real broker margin/preflight check is implemented. Gross premium is not treated as available capital.")
                            else:
                                long_entry_allowed = fo_trade_gate["allow_long_entry"]
                                if not long_entry_allowed:
                                    st.markdown(f"<div class='live-arm-card'>🔒 <strong>LIVE BUY LOCKED BY F&amp;O TRADE GATE</strong> — {html.escape(fo_trade_gate['decision'])}. Wait for current-session broker evidence; this is not a BUY signal.</div>", unsafe_allow_html=True)
                                else:
                                    st.markdown("<div class='live-ready-card'>⚡ <strong>LIVE LONG-OPTION MARKET MODE</strong> — the broker must still accept the contract/product. Displayed SL/target are planning values, not broker-attached exits.</div>" if live_orders_armed else "<div class='live-arm-card'>🔒 <strong>LIVE MODE NOT ARMED</strong> — connect and arm Angel One below.</div>", unsafe_allow_html=True)
                                option_confirm = st.checkbox(f"I understand: submit LIVE MARKET BUY {selected_contract['symbol']} ({option_quantity} qty) as INTRADAY after a fresh broker quote.", key=f"confirm_fo_buy_{selected_contract['token']}", disabled=not (live_orders_armed and long_entry_allowed))
                                if st.button(f"🟢 ⚡ LIVE BUY MARKET {selected_contract['symbol']}", width="stretch", key=f"live_option_buy_{selected_contract['token']}", disabled=not (long_entry_allowed and live_orders_armed and option_confirm)):
                                    with st.spinner("Refreshing broker premium and submitting your confirmed intraday order..."):
                                        fresh_snapshot = fetch_selected_option_snapshot(broker_client, selected_contract)
                                        if fresh_snapshot.get("ok"):
                                            success, message, details = place_regular_order(broker_client, selected_contract, option_quantity, "BUY", "INTRADAY")
                                        else:
                                            success, message, details = False, f"Fresh broker LTP is required: {fresh_snapshot.get('message', 'unavailable')}", None
                                    if success:
                                        order_id = details.get("order_id") if isinstance(details, dict) else ""
                                        st.session_state["last_live_submission"] = {"symbol": selected_contract["symbol"], "action": "BUY", "kind": "F&O INTRADAY MARKET", "order_id": order_id, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
                                        st.success(f"✅ LIVE F&O order submitted to Angel One{f' · Order ID: {order_id}' if order_id else ''}. Verify final status in the broker order book.")
                                    else:
                                        st.error(f"Live F&O order was not submitted: {message}")
                    else:
                        st.info("Connect Angel One to unlock a selected-contract risk preview. No premium is estimated.")

                with st.expander("🔎 What is verified for this F&O view", expanded=False):
                    st.dataframe(
                        pd.DataFrame(build_fo_verification_rows(selected_contract, option_snapshot, candle_snapshot, option_evidence, selected_greeks)),
                        width="stretch", hide_index=True,
                        key=f"fo_verification_split_{selected_contract['token']}_{fo_interval}",
                    )
                with st.expander("📉 Selected CE/PE intraday chart", expanded=False):
                    if candle_snapshot.get("ok") and not option_candles.empty:
                        typical_price = (option_candles["High"] + option_candles["Low"] + option_candles["Close"]) / 3.0
                        volume_total = option_candles["Volume"].fillna(0).cumsum().replace(0, np.nan)
                        session_vwap = (typical_price * option_candles["Volume"].fillna(0)).cumsum() / volume_total
                        option_fig = go.Figure(go.Candlestick(x=option_candles["time"], open=option_candles["Open"], high=option_candles["High"], low=option_candles["Low"], close=option_candles["Close"], name=selected_contract["symbol"], increasing_line_color="#34d399", decreasing_line_color="#fb7185"))
                        option_fig.add_trace(go.Scatter(x=option_candles["time"], y=option_candles["Close"].ewm(span=9, adjust=False).mean(), mode="lines", name="EMA 9", line=dict(color="#22d3ee", width=1.2)))
                        option_fig.add_trace(go.Scatter(x=option_candles["time"], y=option_candles["Close"].ewm(span=20, adjust=False).mean(), mode="lines", name="EMA 20", line=dict(color="#a78bfa", width=1.2)))
                        if session_vwap.notna().any():
                            option_fig.add_trace(go.Scatter(x=option_candles["time"], y=session_vwap, mode="lines", name="Session VWAP", line=dict(color="#fbbf24", width=1.2, dash="dot")))
                        option_fig = apply_chart_style(option_fig, height=470)
                        option_fig.update_xaxes(rangeslider_visible=False)
                        st.plotly_chart(option_fig, width="stretch", key=f"fo_chart_split_{selected_contract['token']}_{fo_interval}")
                        st.caption(broker_snapshot_caption(candle_snapshot) + " · current IST session only")
                    elif candle_snapshot.get("ok"):
                        st.info("Broker candles did not include the current 09:15–15:30 IST session, so no intraday chart is shown.")
                    else:
                        st.info(f"Intraday option chart unavailable: {candle_snapshot.get('message', 'Connect Angel One to load it.')}")

# Retired vertical F&O layout retained only as unreachable reference while the
# new split contract desk above is active. It does not run or fetch data.
if False:
    pass
    fo_controls_1, fo_controls_2 = st.columns([3, 2])
    with fo_controls_1:
        fo_interval_label = st.selectbox("Intraday candle interval", list(FO_INTERVAL_OPTIONS.keys()), key="fo_interval")
        fo_interval = FO_INTERVAL_OPTIONS[fo_interval_label]
    with fo_controls_2:
        if st.button("↻ Refresh Angel One master", width="stretch", key="refresh_fo_master"):
            load_angel_option_master.clear()
            st.rerun()

    master_rows, master_error = load_angel_option_master()
    if master_error:
        st.error(master_error)
        st.caption("No option price, OI, Greek, margin, or technical value is substituted while the official master is unavailable.")
    else:
        option_underlyings = extract_option_underlyings(master_rows)
        if not option_underlyings:
            st.warning("No current non-expired NFO option underlying was available in Angel One's master.")
        else:
            selected_underlying = st.selectbox("Underlying (Angel One NFO master)", option_underlyings, key="fo_underlying")
            option_contracts = extract_option_contracts(master_rows, selected_underlying)
            expiry_values = list(dict.fromkeys(contract["expiry_raw"] for contract in option_contracts))
            expiry_labels = {contract["expiry_raw"]: contract["expiry_label"] for contract in option_contracts}
            if not expiry_values:
                st.warning(f"No live option contract is listed for {selected_underlying}.")
            else:
                choose_1, choose_2, choose_3 = st.columns(3)
                with choose_1:
                    selected_expiry = st.selectbox("Expiry", expiry_values, format_func=lambda value: expiry_labels[value], key=f"fo_expiry_{selected_underlying}")
                expiry_contracts = [row for row in option_contracts if row["expiry_raw"] == selected_expiry]
                available_sides = sorted({row["side"] for row in expiry_contracts})
                with choose_2:
                    selected_option_side = st.selectbox("Option side", available_sides, format_func=lambda value: "CALL (CE)" if value == "CE" else "PUT (PE)", key=f"fo_side_{selected_underlying}_{selected_expiry}", help="Choose CE/PE yourself. Neutral data never auto-selects a side.")
                side_contracts = [row for row in expiry_contracts if row["side"] == selected_option_side]
                strikes = sorted({row["strike"] for row in side_contracts})
                with choose_3:
                    selected_strike = st.selectbox("Strike", strikes, format_func=lambda value: f"₹{value:,.2f}", key=f"fo_strike_{selected_underlying}_{selected_expiry}_{selected_option_side}", help="Choose deliberately; ATM is not inferred from a separate public feed.")
                selected_contract = next(row for row in side_contracts if row["strike"] == selected_strike)
                broker_client = st.session_state.get("smart_api")
                detail_1, detail_2, detail_3, detail_4 = st.columns(4)
                detail_1.metric("Contract", selected_contract["symbol"])
                detail_2.metric("Lot size", str(selected_contract["lot_size"]))
                detail_3.metric("Tick size", "—" if selected_contract["tick_size"] is None else f"₹{selected_contract['tick_size']:.2f}")
                if detail_4.button("↻ Refresh selected", width="stretch", key=f"refresh_fo_{selected_contract['token']}_{fo_interval}"):
                    clear_selected_option_broker_cache(selected_contract, selected_underlying, selected_expiry, fo_interval)
                    st.rerun()

                option_snapshot = get_option_snapshot_for_ui(broker_client, selected_contract)
                if option_snapshot.get("ok"):
                    st.session_state.setdefault("broker_option_prices", {})[selected_contract["symbol"]] = option_snapshot
                    detail_4.metric("Broker LTP", f"₹{option_snapshot['ltp']:.2f}")
                    st.caption(broker_snapshot_caption(option_snapshot))
                elif option_snapshot.get("state") == "disconnected":
                    detail_4.metric("Broker LTP", "Unavailable")
                    st.warning("Connect Angel One below to load this selected contract's real LTP, intraday chart, Greeks, and OI. No stale or estimated premium is used.")
                else:
                    detail_4.metric("Broker LTP", "Unavailable")
                    st.error(f"Selected-contract price unavailable: {option_snapshot.get('message', 'Unknown broker error.')}")

                candle_snapshot = get_option_candles_for_ui(broker_client, selected_contract, fo_interval)
                option_candles = (
                    normalise_broker_option_candles(candle_snapshot["candles"])
                    if candle_snapshot.get("ok") else pd.DataFrame()
                )
                option_evidence = evaluate_intraday_option_evidence(option_candles)
                fo_trade_gate = build_fo_trade_gate(option_snapshot, candle_snapshot, option_evidence)
                st.markdown(
                    """
                    <div class="fo-trade-gate {tone}">
                      <div class="fo-gate-heading">
                        <span>🛡️ F&amp;O TRADE GATE</span>
                        <span class="fo-gate-decision">{decision_note}</span>
                      </div>
                      <div class="fo-gate-grid">
                        <div class="fo-gate-stat"><div class="fo-gate-label">Decision</div><div class="fo-gate-value">{decision}</div></div>
                        <div class="fo-gate-stat"><div class="fo-gate-label">Conditions</div><div class="fo-gate-value">{conditions}</div><div class="fo-gate-note">{condition_detail}</div></div>
                        <div class="fo-gate-stat"><div class="fo-gate-label">Risk status</div><div class="fo-gate-value">{risk_status}</div><div class="fo-gate-note">Selected contract only</div></div>
                      </div>
                      <div class="fo-gate-message">{message}</div>
                    </div>
                    """.format(**{key: html.escape(str(value)) for key, value in fo_trade_gate.items()}),
                    unsafe_allow_html=True,
                )

                st.markdown("<h4 style='color:#fff; margin:18px 0 8px;'>Verified Selected-Contract Scan</h4>", unsafe_allow_html=True)
                st.dataframe(
                    pd.DataFrame([build_fo_intelligence_row(selected_contract, option_snapshot, candle_snapshot, option_evidence)]),
                    width="stretch",
                    hide_index=True,
                    key=f"fo_contract_scan_{selected_contract['token']}_{fo_interval}",
                )
                st.caption("Premium and LTP Chg% use the selected contract's Angel One quote. Trend, VWAP, RSI, Buy, Sell, and Setup use only current-session broker candles for that same CE/PE premium. A dash means unavailable, not zero.")

                selected_greeks = {}
                greeks_snapshot = get_option_greeks_for_ui(broker_client, selected_underlying, selected_expiry)
                if greeks_snapshot.get("ok"):
                    selected_greeks = selected_contract_greeks(greeks_snapshot["rows"], selected_contract)
                    if selected_greeks:
                        st.caption("Broker-reported only: " + " · ".join(f"{label}: {value}" for label, value in selected_greeks.items()) + f" · {broker_snapshot_caption(greeks_snapshot)}")
                    else:
                        st.caption("Greeks/OI were returned for this expiry but could not be matched safely to the selected contract.")
                elif broker_client is not None:
                    st.caption(f"Broker Greeks/OI unavailable: {greeks_snapshot.get('message', 'Unknown response.')}")

                st.markdown("<h4 style='color:#fff; margin:18px 0 8px;'>🔎 What is verified for this F&O view</h4>", unsafe_allow_html=True)
                st.dataframe(
                    pd.DataFrame(build_fo_verification_rows(selected_contract, option_snapshot, candle_snapshot, option_evidence, selected_greeks)),
                    width="stretch",
                    hide_index=True,
                    key=f"fo_verification_{selected_contract['token']}_{fo_interval}",
                )

                if candle_snapshot.get("ok"):
                    if option_candles.empty:
                        st.info("Broker candles did not include the current 09:15–15:30 IST session, so no intraday evidence is shown.")
                    else:
                        chart_column, evidence_column = st.columns([7.2, 2.8])
                        with chart_column:
                            typical_price = (option_candles["High"] + option_candles["Low"] + option_candles["Close"]) / 3.0
                            volume_total = option_candles["Volume"].fillna(0).cumsum().replace(0, np.nan)
                            session_vwap = (typical_price * option_candles["Volume"].fillna(0)).cumsum() / volume_total
                            option_fig = go.Figure(go.Candlestick(x=option_candles["time"], open=option_candles["Open"], high=option_candles["High"], low=option_candles["Low"], close=option_candles["Close"], name=selected_contract["symbol"], increasing_line_color="#34d399", decreasing_line_color="#fb7185"))
                            option_fig.add_trace(go.Scatter(x=option_candles["time"], y=option_candles["Close"].ewm(span=9, adjust=False).mean(), mode="lines", name="EMA 9", line=dict(color="#22d3ee", width=1.2)))
                            option_fig.add_trace(go.Scatter(x=option_candles["time"], y=option_candles["Close"].ewm(span=20, adjust=False).mean(), mode="lines", name="EMA 20", line=dict(color="#a78bfa", width=1.2)))
                            if session_vwap.notna().any():
                                option_fig.add_trace(go.Scatter(x=option_candles["time"], y=session_vwap, mode="lines", name="Session VWAP", line=dict(color="#fbbf24", width=1.2, dash="dot")))
                            option_fig = apply_chart_style(option_fig, height=470)
                            option_fig.update_xaxes(rangeslider_visible=False)
                            st.markdown("<div class='chart-shell'>", unsafe_allow_html=True)
                            st.plotly_chart(option_fig, width="stretch", key=f"fo_chart_{selected_contract['token']}_{fo_interval}")
                            st.markdown("</div>", unsafe_allow_html=True)
                            st.caption(broker_snapshot_caption(candle_snapshot) + " · current IST session only")
                        with evidence_column:
                            evidence_summary = option_evidence["summary"]
                            st.markdown(f"<div class='chart-inspector'><div class='brief-kicker'>Selected CE/PE premium · 10 broker checks</div><div class='brief-value'>{evidence_summary['regime']}</div><div class='brief-note'>Bullish {evidence_summary['buy_score']}/10 · Bearish {evidence_summary['sell_score']}/10 · Neutral {evidence_summary['neutral_count']}/10<br>Only this premium is evaluated; it is not a NIFTY/underlying direction or CE/PE recommendation.</div></div>", unsafe_allow_html=True)
                            if option_evidence["message"]:
                                st.caption(option_evidence["message"])
                            else:
                                st.markdown(evidence_items_html(option_evidence["checks"]), unsafe_allow_html=True)
                else:
                    st.info(f"Intraday option evidence unavailable: {candle_snapshot.get('message', 'Connect Angel One to load it.')}")

                if fo_trade_gate["allow_risk_preview"]:
                    st.markdown("<div class='bottom-card' style='margin-top:14px;'><div class='bottom-title'>💰 Selected Option Intraday Risk Preview</div><div style='font-size:12px;color:#94a3b8;'>Uses the selected broker premium. Charges, slippage, margin, and assignment risk are excluded.</div></div>", unsafe_allow_html=True)
                    risk_1, risk_2, risk_3, risk_4 = st.columns(4)
                    with risk_1:
                        lots = st.number_input("Lots", min_value=1, value=1, step=1, key=f"fo_lots_{selected_contract['token']}")
                    with risk_2:
                        option_action = st.radio("Premium action", ["BUY", "SELL"], horizontal=True, key=f"fo_action_{selected_contract['token']}")
                    minimum_step = float(selected_contract["tick_size"] or 0.05)
                    with risk_3:
                        default_stop = max(minimum_step, round(option_snapshot["ltp"] * 0.15, 2))
                        option_stop = st.number_input("Risk points (₹)", min_value=minimum_step, value=default_stop, step=minimum_step, key=f"fo_stop_{selected_contract['token']}")
                    with risk_4:
                        option_target = st.number_input("Target points (₹)", min_value=minimum_step, value=float(round(option_stop * 3, 2)), step=minimum_step, key=f"fo_target_{selected_contract['token']}")
                    option_quantity = int(lots) * selected_contract["lot_size"]
                    try:
                        option_preview = calculate_directional_preview(option_action, option_snapshot["ltp"], option_stop, option_target, option_quantity)
                    except ValueError as exc:
                        option_preview = None
                        st.error(f"Option sizing needs valid values: {exc}")
                    if option_preview:
                        premium_label = "Premium outlay before charges" if option_action == "BUY" else "Gross premium received — broker margin is not calculated"
                        st.markdown(f"<div class='calc-box'><div class='calc-row'><span style='color:#94a3b8;'>Quantity:</span><strong>{int(lots)} lot × {selected_contract['lot_size']} = {option_quantity}</strong></div><div class='calc-row'><span style='color:#94a3b8;'>{premium_label}:</span><strong style='color:#00f2fe;'>₹{option_preview['gross_notional']:,.2f}</strong></div><div class='calc-row'><span style='color:#94a3b8;'>{option_action} entry / stop / target:</span><strong>₹{option_preview['entry_price']:.2f} / <span style='color:#fb7185'>₹{option_preview['stop_price']:.2f}</span> / <span style='color:#34d399'>₹{option_preview['target_price']:.2f}</span></strong></div><div class='calc-row'><span style='color:#94a3b8;'>Illustrative P&L target / stop:</span><strong><span style='color:#34d399'>+₹{option_preview['target_pnl']:,.2f}</span> / <span style='color:#fb7185'>₹{option_preview['loss_pnl']:,.2f}</span></strong></div></div>", unsafe_allow_html=True)
                        if option_action == "BUY" and not fo_trade_gate["allow_long_entry"]:
                            st.warning(f"BUY is locked by the F&O Trade Gate: {fo_trade_gate['decision']}. These are planning values only.")
                        if is_paper_trading:
                            paper_key = f"paper_option_{option_action.lower()}_{selected_contract['token']}"
                            paper_buy_locked = option_action == "BUY" and not fo_trade_gate["allow_long_entry"]
                            if st.button(f"{'🟢' if option_action == 'BUY' else '🔴'} 📝 Log {option_action} paper position", width="stretch", key=paper_key, disabled=paper_buy_locked):
                                ok, message = place_paper_order(selected_contract["symbol"], option_action, option_quantity, option_snapshot["ltp"], option_stop, option_target)
                                (st.success if ok else st.error)(f"{'✅' if ok else '❌'} {message}")
                        elif option_action == "SELL":
                            st.warning("Live short-option submission is unavailable until a real broker margin/preflight check is implemented. Gross premium is not treated as available capital.")
                        else:
                            long_entry_allowed = fo_trade_gate["allow_long_entry"]
                            if not long_entry_allowed:
                                st.markdown(f"<div class='live-arm-card'>🔒 <strong>LIVE BUY LOCKED BY F&amp;O TRADE GATE</strong> — {html.escape(fo_trade_gate['decision'])}. Wait for current-session broker evidence; this is not a BUY signal.</div>", unsafe_allow_html=True)
                            else:
                                st.markdown("<div class='live-ready-card'>⚡ <strong>LIVE LONG-OPTION MARKET MODE</strong> — the broker must still accept the contract/product. Displayed SL/target are planning values, not broker-attached exits.</div>" if live_orders_armed else "<div class='live-arm-card'>🔒 <strong>LIVE MODE NOT ARMED</strong> — connect and arm Angel One below.</div>", unsafe_allow_html=True)
                            option_confirm = st.checkbox(f"I understand: submit LIVE MARKET BUY {selected_contract['symbol']} ({option_quantity} qty) as INTRADAY after a fresh broker quote.", key=f"confirm_fo_buy_{selected_contract['token']}", disabled=not (live_orders_armed and long_entry_allowed))
                            if st.button(f"🟢 ⚡ LIVE BUY MARKET {selected_contract['symbol']}", width="stretch", key=f"live_option_buy_{selected_contract['token']}", disabled=not (long_entry_allowed and live_orders_armed and option_confirm)):
                                with st.spinner("Refreshing broker premium and submitting your confirmed intraday order..."):
                                    fresh_snapshot = fetch_selected_option_snapshot(broker_client, selected_contract)
                                    if fresh_snapshot.get("ok"):
                                        success, message, details = place_regular_order(broker_client, selected_contract, option_quantity, "BUY", "INTRADAY")
                                    else:
                                        success, message, details = False, f"Fresh broker LTP is required: {fresh_snapshot.get('message', 'unavailable')}", None
                                if success:
                                    order_id = details.get("order_id") if isinstance(details, dict) else ""
                                    st.session_state["last_live_submission"] = {"symbol": selected_contract["symbol"], "action": "BUY", "kind": "F&O INTRADAY MARKET", "order_id": order_id, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
                                    st.success(f"✅ LIVE F&O order submitted to Angel One{f' · Order ID: {order_id}' if order_id else ''}. Verify final status in the broker order book.")
                                else:
                                    st.error(f"Live F&O order was not submitted: {message}")


# =========================================================================
# TAB 3: PAPER TRADING PORTFOLIO & LEDGER DESK
# =========================================================================
with tab_paper_ledger:
    pdata = st.session_state["paper_data"]
    st.caption("Private browser-session paper account. Export the journal before a browser/session reset; old local paper_trades.json files are not loaded or overwritten.")
    render_risk_budget(is_paper=True)
    if st.button("↻ Refresh open F&O paper quotes", key="refresh_paper_fo_quotes", disabled=st.session_state.get("smart_api") is None):
        for position in pdata.get("positions", [])[:30]:
            contract = position.get("contract")
            if not contract:
                continue
            snapshot = fetch_selected_option_snapshot(st.session_state.get("smart_api"), contract)
            st.session_state.setdefault("broker_option_prices", {})[position["symbol"]] = snapshot
        st.caption("Refreshed up to 30 recorded option contracts. Quote snapshots do not guarantee an executable fill.")
    
    total_invested = sum(p["qty"] * p["entry_price"] for p in pdata["positions"])
    cur_equity = pdata["cash"] + total_invested
    total_realized_pnl = sum(t["pnl"] for t in pdata["closed_trades"])
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Virtual Cash Balance", f"₹{pdata['cash']:,.2f}")
    c2.metric("Capital in Active Trades", f"₹{total_invested:,.2f}")
    c3.metric("Virtual capital at cost", f"₹{cur_equity:,.2f}")
    c4.metric("Total Realized P&L", f"{total_realized_pnl:+,.2f}", delta=f"{((cur_equity-500000)/500000)*100:+.2f}%")

    st.write("")
    st.markdown("<h4 style='color:#fff; margin-bottom:8px;'>Open Paper Positions (Available-Feed Monitoring)</h4>", unsafe_allow_html=True)
    
    if pdata["positions"]:
        open_rows = []
        for p in pdata["positions"]:
            sym = p["symbol"]
            cur_price = paper_observations.get(sym)
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
        with st.expander("Close a paper position"):
            close_options = {row["ID"]: row for row in open_rows}
            close_id = st.selectbox("Position to close", list(close_options), format_func=lambda value: f"{close_options[value]['Symbol']} · {value}", key="paper_close_id")
            close_row = close_options[close_id]
            close_price = close_row["LTP (₹)"]
            st.caption("Simulated exit uses the displayed available-feed quote. It sends no broker order; costs and slippage are not simulated.")
            if st.button("Close paper position at displayed quote", disabled=close_price is None, key="close_paper_at_quote"):
                position = next(p for p in pdata["positions"] if p["id"] == close_id)
                pnl = (close_price-position["entry_price"])*position["qty"]*(1 if position["action"] == "BUY" else -1)
                position.update(status="CLOSED", exit_price=close_price, pnl=round(pnl, 2), exit_reason="Manual simulated exit at feed snapshot", exit_time=ist_now().strftime("%Y-%m-%d %H:%M:%S"))
                pdata["cash"] += position["qty"] * position["entry_price"] + pnl
                pdata["positions"] = [p for p in pdata["positions"] if p["id"] != close_id]
                pdata["closed_trades"].append(position)
                save_paper_account(pdata)
                st.rerun()
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
        st.caption("Intraday equity paper exits are checked on available recent feed snapshots during app runs. F&O and long-term paper plans can be closed manually above. Nothing is monitored while the app is asleep.")

    if st.button("🔄 Reset Paper Trading Account to ₹5,00,000"):
        st.session_state["paper_data"] = {
            "cash": 500000.0,
            "positions": [],
            "closed_trades": []
        }
        save_paper_account(st.session_state["paper_data"])
        st.session_state.pop("journal_images", None)
        st.rerun()

    render_paper_journal()

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
            raw = _market_frame_for_symbol(raw, bt_sym)
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
    st.markdown("<h3 style='color:#fff; margin-bottom:4px;'>TradingView-Style Support & Resistance Desk</h3>", unsafe_allow_html=True)
    st.caption("Left: interactive price chart. Right: asset, timeframe, verified source state, and current levels. Public chart data can be delayed and is never replaced with a made-up price.")
    chart_left, chart_right = st.columns([7.3, 2.7])
    with chart_right:
        st.markdown("<div class='chart-inspector'><div class='brief-kicker'>Chart controls</div>", unsafe_allow_html=True)
        c_sym = st.selectbox("Asset", symbols, key="c_sym")
        chart_timeframe = st.selectbox("Timeframe", list(CHART_TIMEFRAME_MAP.keys()), key="chart_timeframe")
        chart_period, chart_interval, chart_extrema_order = CHART_TIMEFRAME_MAP[chart_timeframe]
        st.caption("Pan, zoom, hover, and inspect levels directly on the chart.")
        st.markdown("</div>", unsafe_allow_html=True)

    c_raw, chart_feed_message = download_public_chart_data(c_sym, period=chart_period, interval=chart_interval, progress=False)
    c_raw = _market_frame_for_symbol(c_raw, c_sym)
    c_raw = c_raw.dropna().copy()

    if chart_feed_message:
        with chart_left:
            st.warning(f"Chart unavailable — {chart_feed_message} No prices were substituted.")
        with chart_right:
            st.error("Data source unavailable")
    elif len(c_raw) > 20:
        highs = argrelextrema(c_raw['High'].values, np.greater, order=chart_extrema_order)[0]
        lows = argrelextrema(c_raw['Low'].values, np.less, order=chart_extrema_order)[0]
        c_raw['Res'] = np.nan
        c_raw['Sup'] = np.nan
        c_raw.loc[c_raw.index[highs], 'Res'] = c_raw.iloc[highs]['High']
        c_raw.loc[c_raw.index[lows], 'Sup'] = c_raw.iloc[lows]['Low']
        c_raw['Nearest_Res'] = c_raw['Res'].ffill()
        c_raw['Nearest_Sup'] = c_raw['Sup'].ffill()
        c_raw['EMA20'] = c_raw['Close'].ewm(span=20, adjust=False).mean()
        c_raw['EMA50'] = c_raw['Close'].ewm(span=50, adjust=False).mean()
        last_price = float(c_raw['Close'].iloc[-1])
        prior_price = float(c_raw['Close'].iloc[-2])
        change_pct = (last_price - prior_price) / prior_price * 100
        latest_resistance = c_raw['Nearest_Res'].iloc[-1]
        latest_support = c_raw['Nearest_Sup'].iloc[-1]
        with chart_left:
            fig_chart = go.Figure()
            fig_chart.add_trace(go.Candlestick(
                x=c_raw.index, open=c_raw['Open'], high=c_raw['High'], low=c_raw['Low'], close=c_raw['Close'], name="Price",
                increasing_line_color="#34d399", decreasing_line_color="#fb7185"
            ))
            fig_chart.add_trace(go.Scatter(x=c_raw.index, y=c_raw['EMA20'], mode='lines', line=dict(color='#22d3ee', width=1.2), name='EMA 20'))
            fig_chart.add_trace(go.Scatter(x=c_raw.index, y=c_raw['EMA50'], mode='lines', line=dict(color='#a78bfa', width=1.2), name='EMA 50'))
            fig_chart.add_trace(go.Scatter(x=c_raw.index, y=c_raw['Nearest_Res'], mode='lines', line_shape='hv', line=dict(color='#fb7185', dash='dash', width=1.4), name='Resistance'))
            fig_chart.add_trace(go.Scatter(x=c_raw.index, y=c_raw['Nearest_Sup'], mode='lines', line_shape='hv', line=dict(color='#34d399', dash='dash', width=1.4), name='Support'))
            fig_chart = apply_chart_style(fig_chart, height=625)
            fig_chart.update_xaxes(rangeslider_visible=False)
            st.markdown("<div class='chart-shell'>", unsafe_allow_html=True)
            st.plotly_chart(fig_chart, width="stretch", key=f"sr_chart_{c_sym}_{chart_interval}")
            st.markdown("</div>", unsafe_allow_html=True)
        with chart_right:
            direction_colour = "#34d399" if change_pct >= 0 else "#fb7185"
            st.markdown(f"""
            <div class="brief-card" style="margin-top:12px;">
                <div class="brief-kicker">{c_sym.replace('.NS', '').replace('.BO', '')} · {chart_timeframe}</div>
                <div class="brief-value">₹{last_price:,.2f}</div>
                <div class="brief-note" style="color:{direction_colour}; font-weight:800;">{change_pct:+.2f}% from prior bar</div>
            </div>
            """, unsafe_allow_html=True)
            st.metric("Nearest resistance", "—" if pd.isna(latest_resistance) else f"₹{latest_resistance:,.2f}")
            st.metric("Nearest support", "—" if pd.isna(latest_support) else f"₹{latest_support:,.2f}")
            st.metric("EMA 20 / 50", f"₹{c_raw['EMA20'].iloc[-1]:,.2f} / ₹{c_raw['EMA50'].iloc[-1]:,.2f}")
            st.caption("Verified public chart snapshot. Source time reflects the latest candle returned by the provider.")
    else:
        with chart_left:
            st.info("Insufficient verified chart history for this asset; no support or resistance levels are shown.")
        with chart_right:
            st.warning("Need at least 21 usable candles")

# =========================================================================
# TAB 6: MARKET BRIEF & OFFICIAL NEWS CONTEXT
# =========================================================================
with tab_news:
    st.subheader("Market in 30 Seconds")
    st.caption("Latest available market context · source dates shown on every hint.")

    @st.cache_data(ttl=600, show_spinner=False)
    def load_official_market_news():
        return fetch_official_news_feeds(OFFICIAL_NEWS_SOURCES)

    @st.cache_data(ttl=300, show_spinner=False)
    def load_brief_index_context():
        market_symbols = {"NIFTY 50": "^NSEI", "BANKNIFTY": "^NSEBANK", "SENSEX": "^BSESN", **GLOBAL_MARKET_WATCH}
        raw, error = download_public_chart_data(list(market_symbols.values()), period="5d", interval="1d", group_by="ticker", progress=False, threads=True)
        return build_brief_market_rows(raw, market_symbols), error

    if st.button("↻ Refresh brief", key="refresh_morning_brief"):
        load_official_market_news.clear()
        load_brief_index_context.clear()
        st.rerun()
    news_snapshot = load_official_market_news()
    brief_markets, brief_market_error = load_brief_index_context()
    quick = build_market_quick_brief(news_snapshot.get("items", []), brief_markets)
    st.markdown(f"<div class='brief-card'><div class='brief-kicker'>Market mood · latest source session</div><div class='brief-value'>{html.escape(quick['mood'])}</div><div class='brief-note'>{html.escape(quick['reason'])}</div></div>", unsafe_allow_html=True)
    st.caption("Mood describes the available Indian index snapshot. It does not predict the next session. Public prices may be delayed; news impact can differ by sector.")
    for column, group, title, empty in zip(st.columns(3),
        ("good", "bad", "watch"),
        ("🟢 Good news / positive cues", "🔴 Bad news / negative cues", "🟡 Watch today"),
        ("No recent positive cue verified in these sources.", "No recent negative cue verified in these sources.", "No recent market-wide event verified in these sources.")):
        with column:
            st.markdown(f"#### {title}")
            for hint in quick["groups"][group]:
                render_brief_hint(hint)
            if not quick["groups"][group]:
                st.caption(empty)
    st.caption("Headline cues are conservative interpretations of the linked titles. Official feeds are limited coverage; an empty card does not mean nothing happened.")
    with st.expander("Full official news"):
        for item in news_snapshot.get("items", []):
            render_brief_hint({"text": item["title"], "detail": f"{item['source']} · {item.get('published') or 'Publication time unavailable'}", "link": item["link"]})
        if not news_snapshot.get("items"):
            st.info("No readable official headlines returned.")
        st.caption(f"Feed checked: {news_snapshot.get('fetched_at', 'Unavailable')} · {quick['older_news']} undated/older items excluded from the short brief.")
        for error in news_snapshot.get("errors", []):
            st.caption(error)
    with st.expander("Indian and global market snapshots"):
        if brief_markets:
            st.dataframe(pd.DataFrame(brief_markets)[["Market", "Last price", "Change %", "Source session"]],
                hide_index=True, width="stretch", column_config={
                    "Last price": st.column_config.NumberColumn(format="%.2f"),
                    "Change %": st.column_config.NumberColumn(format="%+.2f%%")})
            st.caption("Daily bars can still be forming during that market's session. Different markets may have different source dates.")
        else:
            st.info("Market snapshot unavailable.")
        if brief_market_error:
            st.caption(brief_market_error)
    with st.expander("My pre-market checklist"):
        st.markdown("1. Check source times and overnight developments.\n2. Mark entry, invalidation and target levels.\n3. Set a risk budget and record your trade reason.\n4. Review fills and journal the outcome.")
    st.caption("Official news sources: SEBI, RBI and PIB. Global context uses public chart snapshots. This brief refreshes in the app; scheduled delivery is not configured.")

# =========================================================================
# BOTTOM SECTION: ANGEL ONE GATEWAY
# =========================================================================
st.write("")
st.markdown("""
<div class="bottom-card">
    <div class="bottom-title">🔗 Angel One SmartAPI Gateway</div>
    <div style="font-size:12px; color:#94a3b8;">Use <strong>↻ Refresh data</strong> above to reload cached market snapshots. Non-sensitive workspace choices can be remembered; API key, Client ID, MPIN, TOTP, access tokens, and broker sessions are never written to the app settings file.</div>
</div>
""", unsafe_allow_html=True)

ao_c1, ao_c2 = st.columns(2)
with ao_c1:
    ao_api_key = st.text_input("API Key", type="password", placeholder="API Key")
    ao_client = st.text_input("Client ID", placeholder="Client Code")
with ao_c2:
    ao_pin = st.text_input("MPIN", type="password", placeholder="MPIN")
    ao_totp_key = st.text_input("TOTP Secret Key", type="password", placeholder="Secret Key")

ao_btn_col, ao_status_col = st.columns([1.5, 2.5])
with ao_btn_col:
    if st.button("Connect / Refresh Broker", width="stretch"):
        if all([ao_api_key, ao_client, ao_pin, ao_totp_key]):
            api_obj, res_msg = connect_angel_one(ao_api_key, ao_client, ao_pin, ao_totp_key)
            if api_obj:
                st.session_state["smart_api"] = api_obj
                st.session_state["live_orders_armed"] = False
                st.session_state["live_order_acknowledgement"] = False
                st.session_state.pop("broker_account_view", None)
                clear_all_short_lived_broker_cache()
                st.rerun()
            else:
                st.error(f"Failed: {res_msg}")
        else:
            st.warning("Fill in all credentials.")
with ao_status_col:
    if "smart_api" in st.session_state:
        st.markdown("<span style='color:#34d399; font-size:13px; font-weight:700; line-height:38px;'>● BROKER SESSION ACTIVE</span>", unsafe_allow_html=True)
    else:
        st.markdown("<span style='color:#94a3b8; font-size:13px; font-weight:500; line-height:38px;'>Status: Disconnected</span>", unsafe_allow_html=True)

if all([def_api_key, def_client_id, def_pin, def_totp]):
    try:
        owner_password_hash = str(st.secrets.get("app_access", {}).get("password_sha256", ""))
    except Exception:
        owner_password_hash = ""
    with st.expander("Connect using saved owner credentials", expanded=False):
        if not re.fullmatch(r"[0-9a-fA-F]{64}", owner_password_hash):
            st.info("Saved owner credentials are protected on this public app. Configure app_access.password_sha256 in Streamlit Secrets to unlock them, or use your own manual login above.")
        else:
            with st.form("owner_unlock"):
                owner_password = st.text_input("Owner access password", type="password")
                if st.form_submit_button("Unlock saved connection"):
                    cooldown = st.session_state.get("owner_retry_after", 0)
                    if time.time() < cooldown:
                        st.warning("Wait before trying another password.")
                    elif hmac.compare_digest(hashlib.sha256(owner_password.encode()).hexdigest(), owner_password_hash.lower()):
                        st.session_state["owner_unlocked"] = True
                        st.success("Saved connection unlocked for this session.")
                    else:
                        st.session_state["owner_retry_after"] = time.time() + 5
                        st.error("Access password did not match.")
            if st.button("Connect saved owner broker", disabled=not st.session_state.get("owner_unlocked", False), key="connect_owner_broker"):
                owner_client, owner_message = connect_angel_one(def_api_key, def_client_id, def_pin, def_totp)
                if owner_client:
                    st.session_state["smart_api"] = owner_client
                    st.session_state["live_orders_armed"] = False
                    st.session_state["live_order_acknowledgement"] = False
                    st.session_state.pop("broker_account_view", None)
                    clear_all_short_lived_broker_cache()
                    st.rerun()
                else:
                    st.error("Saved broker login failed. Verify credentials privately in Streamlit Secrets.")
st.caption("Credentials are never prefilled from server secrets or saved in your journal. Reconnect when the broker session expires.")

if st.session_state.get("smart_api") is not None:
    if st.button("Disconnect broker and lock this session", key="disconnect_broker"):
        for private_key in ("smart_api", "owner_unlocked", "broker_account_view", "broker_option_prices", "last_live_submission"):
            st.session_state.pop(private_key, None)
        st.session_state["live_orders_armed"] = False
        st.session_state["live_order_acknowledgement"] = False
        clear_all_short_lived_broker_cache()
        st.rerun()

render_broker_status()

def sync_live_arming():
    st.session_state["live_orders_armed"] = bool(st.session_state.get("live_order_acknowledgement"))

if "smart_api" in st.session_state and LIVE_ORDER_EXECUTION_ENABLED:
    st.markdown("<div class='live-arm-card'><strong>Live-order safety gate</strong><br>Arming enables controls only. Each BUY or SELL still needs its own confirmation, and a broker response means <em>submitted</em>, not filled.</div>", unsafe_allow_html=True)
    armed = st.checkbox(
        "I understand that LIVE mode can submit real orders to Angel One and I want to arm the live controls for this browser session.",
        key="live_order_acknowledgement",
        on_change=sync_live_arming,
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
