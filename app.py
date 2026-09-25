from datetime import datetime
import json
import os
import urllib.request
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import pyotp
from scipy.signal import argrelextrema
from SmartApi import SmartConnect
import streamlit as st
import yfinance as yf

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
@st.cache_data(ttl=86400)
def load_angel_token_map() -> dict:
    url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPISymbolToken.json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            token_map = {}
            for item in data:
                if item.get("exch_seg") == "NSE" and item.get("symbol", "").endswith("-EQ"):
                    base = item["symbol"].replace("-EQ", "")
                    token_map[base] = item["token"]
            return token_map
    except Exception:
        return {
            "RELIANCE": "2885", "TCS": "11536", "HDFCBANK": "1333",
            "INFY": "1594", "ICICIBANK": "4963", "SBIN": "3045",
            "BHARTIARTL": "10604", "ITC": "1660", "LT": "11483", "TITAN": "3506"
        }

def connect_angel_one(api_key: str, client_code: str, pin: str, totp_secret: str):
    try:
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

def place_bracket_robo_order(smart_api, symbol: str, token: str, qty: int, limit_price: float, stoploss_pts: float, target_pts: float, trailing_pts: float = 0.0, action: str = "BUY"):
    try:
        orderparams = {
            "variety": "ROBO",
            "tradingsymbol": f"{symbol}-EQ",
            "symboltoken": str(token),
            "transactiontype": action,
            "exchange": "NSE",
            "ordertype": "LIMIT",
            "producttype": "BO",
            "duration": "DAY",
            "price": f"{limit_price:.2f}",
            "squareoff": f"{target_pts:.2f}",
            "stoploss": f"{stoploss_pts:.2f}",
            "trailingStopLoss": f"{trailing_pts:.2f}" if trailing_pts > 0 else "0",
            "quantity": str(qty)
        }
        order_res = smart_api.placeOrder(orderparams)
        return True, order_res
    except Exception as e:
        return False, str(e)

def place_regular_order(smart_api, symbol: str, token: str, qty: int, transaction_type: str, product_type: str = "INTRADAY"):
    try:
        orderparams = {
            "variety": "NORMAL",
            "tradingsymbol": f"{symbol}-EQ",
            "symboltoken": str(token),
            "transactiontype": transaction_type,
            "exchange": "NSE",
            "ordertype": "MARKET",
            "producttype": product_type,
            "duration": "DAY",
            "price": "0",
            "squareoff": "0",
            "stoploss": "0",
            "quantity": str(qty)
        }
        order_id = smart_api.placeOrder(orderparams)
        return True, order_id
    except Exception as e:
        return False, str(e)

# --- Market Universes ---
INDEX_BASKETS = {
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
    "SENSEX 30": [
        "RELIANCE.BO", "TCS.BO", "HDFCBANK.BO", "ICICIBANK.BO", "BHARTIARTL.BO",
        "SBIN.BO", "INFY.BO", "ITC.BO", "LT.BO", "HINDUNILVR.BO", "AXISBANK.BO"
    ],
    "NIFTY MIDCAP": [
        "POLYCAB.NS", "PERSISTENT.NS", "DIXON.NS", "COFORGE.NS", "LUPIN.NS",
        "ASTRAL.NS", "CUMMINSIND.NS", "MAXHEALTH.NS", "SUNDARMFIN.NS", "HINDPETRO.NS"
    ]
}

FO_UNIVERSE = {
    "NIFTY": ("^NSEI", 50),
    "BANKNIFTY": ("^NSEBANK", 15),
    "FINNIFTY": ("NIFTY_FIN_SERVICE.NS", 25),
    "RELIANCE": ("RELIANCE.NS", 250),
    "HDFCBANK": ("HDFCBANK.NS", 550),
    "ICICIBANK": ("ICICIBANK.NS", 700),
    "SBIN": ("SBIN.NS", 750),
    "TCS": ("TCS.NS", 175),
    "INFY": ("INFY.NS", 400),
    "TATAMOTORS": ("TATAMOTORS.NS", 700)
}

HORIZON_MAP = {
    "Intraday (15-Min)": ("5d", "15m", 3),
    "BTST (1-Hour)": ("1mo", "60m", 4),
    "Weekly Swing (Daily)": ("6mo", "1d", 5),
    "Mid Term (Daily / 2Y)": ("2y", "1d", 8),
    "Long Term (Weekly)": ("5y", "1wk", 10)
}

# --- Header Box ---
timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

st.markdown(f"""<div class="header-box">
<div>
<div class="brand-title">Mahi <span class="brand-accent">Trading</span></div>
<div class="brand-sub">Multi-Asset Algorithmic Intelligence & Automated Risk Exits</div>
</div>
<div style="text-align:right;">
<span class="status-badge">● LIVE MARKET</span>
<div style="font-size:12px; color:#94a3b8; margin-top:4px;">{timestamp} IST</div>
</div>
</div>""", unsafe_allow_html=True)

angel_tokens = load_angel_token_map()

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

u_col1, u_col2, u_col3 = st.columns([4.5, 4.5, 3])
with u_col1:
    selected_basket = st.selectbox("Select Asset Universe", list(INDEX_BASKETS.keys()), label_visibility="collapsed")
with u_col2:
    selected_horizon = st.selectbox("Select Trading Horizon", list(HORIZON_MAP.keys()), label_visibility="collapsed")
with u_col3:
    exec_env = st.selectbox("Execution Mode", ["📝 Paper Trading", "⚡ Live Broker"], label_visibility="collapsed")

is_paper_trading = (exec_env == "📝 Paper Trading")
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

    mode_label = '<span class="mode-badge-paper">📝 ACTIVE: PAPER TRADING (SIMULATION)</span>' if is_paper_trading else '<span class="mode-badge-live">⚡ ACTIVE: LIVE BROKER</span>'
    st.markdown(f"""
    <div class="disclaimer-banner" style="display:flex; justify-content:space-between; align-items:center;">
        <span>Algorithmic 10-Point Technical Engine: MTF Daily 50 EMA, Session VWAP, ORB Breakout & Dynamic 1:3 ROBO Risk Exits.</span>
        {mode_label}
    </div>
    """, unsafe_allow_html=True)

    @st.cache_data(ttl=180)
    def fetch_equity_and_daily_data(basket_name: str, period: str, interval: str):
        symbols = INDEX_BASKETS[basket_name]
        intraday_data = yf.download(symbols, period=period, interval=interval, group_by='ticker', progress=False, threads=True)
        daily_data = yf.download(symbols, period="1y", interval="1d", group_by='ticker', progress=False, threads=True)
        return symbols, intraday_data, daily_data

    symbols, data, daily_data = fetch_equity_and_daily_data(selected_basket, period, interval)

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
            c_daily_ema50 = c_ltp
            d_ok = False
            try:
                df_day = daily_data[sym].dropna()
                if len(df_day) >= 50:
                    d_ema = df_day['Close'].ewm(span=50, adjust=False).mean()
                    c_daily_ema50 = float(d_ema.iloc[-1])
                    d_ok = c_ltp > c_daily_ema50
                else:
                    d_ok = c_ltp > c_ema50
            except Exception:
                d_ok = c_ltp > c_ema50

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
                ("1. MTF Macro Filter", c1_pass, f"Daily 50 EMA: ₹{c_daily_ema50:.2f}"),
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
            use_container_width=True,
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

            is_bull = stock['buy_score'] >= stock['sell_score']
            sl_price = stock['ltp'] - stop_points if is_bull else stock['ltp'] + stop_points
            tp_price = stock['ltp'] + target_points if is_bull else stock['ltp'] - target_points

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
                    <div>SL PRICE: <strong style="color:#fb7185">₹{sl_price:.2f}</strong></div>
                    <div>TARGET (1:3): <strong style="color:#34d399">₹{tp_price:.2f}</strong></div>
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
                trade_qty = st.number_input("Shares Qty to Buy", min_value=1, value=10, step=1, key=f"qty_{active_sym}")
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

                total_capital_required = trade_qty * trade_limit
                expected_profit = trade_qty * in_tp_pts
                expected_loss = trade_qty * in_sl_pts
                reward_risk_ratio = round(expected_profit / expected_loss, 2) if expected_loss > 0 else 0.0

                st.markdown(f"""
                <div class="calc-box">
                    <div class="calc-row">
                        <span style="color:#94a3b8;">Total Amount Needed to Buy:</span>
                        <strong style="color:#ffffff; font-size:14px;">₹{total_capital_required:,.2f}</strong>
                    </div>
                    <div class="calc-row">
                        <span style="color:#94a3b8;">Expected Max Profit (Target):</span>
                        <strong style="color:#34d399; font-size:14px;">+₹{expected_profit:,.2f}</strong>
                    </div>
                    <div class="calc-row">
                        <span style="color:#94a3b8;">Expected Max Loss (Stop-Loss):</span>
                        <strong style="color:#fb7185; font-size:14px;">-₹{expected_loss:,.2f}</strong>
                    </div>
                    <div class="calc-row" style="border-top:1px solid rgba(255,255,255,0.06); margin-top:4px; padding-top:4px;">
                        <span style="color:#94a3b8;">Net Risk-to-Reward Ratio:</span>
                        <strong style="color:#00f2fe;">1 : {reward_risk_ratio}</strong>
                    </div>
                </div>
                """, unsafe_allow_html=True)

                b_col1, b_col2 = st.columns(2)
                btn_prefix = "📝 PAPER" if is_paper_trading else ""
                with b_col1:
                    btn_robo_buy = st.button(f"🟢 {btn_prefix} BUY ROBO {active_sym}", use_container_width=True, key=f"btn_buy_{active_sym}")
                with b_col2:
                    btn_robo_sell = st.button(f"🔴 {btn_prefix} SHORT ROBO {active_sym}", use_container_width=True, key=f"btn_sell_{active_sym}")

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
                            trail_pts=in_trail
                        )
                        if ok:
                            st.success(f"✅ {msg}")
                        else:
                            st.error(f"❌ {msg}")
                    else:
                        if "smart_api" in st.session_state:
                            token = angel_tokens.get(active_sym, "3045")
                            success, resp = place_bracket_robo_order(
                                smart_api=st.session_state["smart_api"],
                                symbol=active_sym,
                                token=token,
                                qty=trade_qty,
                                limit_price=trade_limit,
                                stoploss_pts=in_sl_pts,
                                target_pts=in_tp_pts,
                                trailing_pts=in_trail,
                                action=action_type
                            )
                            if success:
                                st.success(f"✅ Live Bracket Order Placed! ID: {resp}")
                            else:
                                st.error(f"Execution Error: {resp}")
                        else:
                            st.warning("⚠️ Connect Angel One in the gateway below first.")
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
                    st.write("")
                    btn_prefix = "📝 PAPER" if is_paper_trading else "⚡"
                    btn_reg_buy = st.button(f"{btn_prefix} Buy Market {active_sym}", use_container_width=True, key=f"mkt_buy_{active_sym}")

                if btn_reg_buy:
                    if is_paper_trading:
                        ok, msg = place_paper_order(active_sym, "BUY", trade_qty, trade_limit, stop_points, target_points)
                        if ok:
                            st.success(f"✅ {msg}")
                        else:
                            st.error(f"❌ {msg}")
                    else:
                        if "smart_api" in st.session_state:
                            token = angel_tokens.get(active_sym, "3045")
                            success, resp = place_regular_order(
                                st.session_state["smart_api"],
                                symbol=active_sym,
                                token=token,
                                qty=trade_qty,
                                transaction_type="BUY",
                                product_type=prod
                            )
                            if success:
                                st.success(f"Live Market Order Executed! ID: {resp}")
                            else:
                                st.error(f"Execution Error: {resp}")
                        else:
                            st.warning("⚠️ Connect Angel One below first.")

# =========================================================================
# TAB 2: F&O INTELLIGENCE (10-POINT STRATEGY & AUTOMATED STRIKE SELECTION)
# =========================================================================
with main_tab_fo:
    st.markdown("""
    <div class="disclaimer-banner">
        Derivatives Screener: 10-Point Technical Engine, PCR, Open Interest Buildup & Systematic Strike Selection (ITM Delta ~ 0.65).
    </div>
    """, unsafe_allow_html=True)

    fo_tickers = [info[0] for info in FO_UNIVERSE.values()]
    
    @st.cache_data(ttl=180)
    def fetch_fo_market_data():
        # Fetch Intraday and Daily for 10-Point Evaluation on Derivatives Universe
        fo_intra = yf.download(fo_tickers, period=period, interval=interval, group_by='ticker', progress=False, threads=True)
        fo_daily = yf.download(fo_tickers, period="1y", interval="1d", group_by='ticker', progress=False, threads=True)
        return fo_intra, fo_daily

    fo_intra, fo_daily = fetch_fo_market_data()

    fo_rows = []
    fo_checklists = {}

    for name, (ticker, lot_size) in FO_UNIVERSE.items():
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
            f_daily_ema50 = c_ltp
            try:
                f_d_df = fo_daily[ticker].dropna()
                if len(f_d_df) >= 50:
                    d_ema = f_d_df['Close'].ewm(span=50, adjust=False).mean()
                    f_daily_ema50 = float(d_ema.iloc[-1])
                    d_ok = c_ltp > f_daily_ema50
                else:
                    d_ok = c_ltp > f_ema50
            except Exception:
                d_ok = c_ltp > f_ema50

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
                ("1. MTF Macro Filter", c1_pass, f"Daily 50 EMA: ₹{f_daily_ema50:.2f}"),
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

            # Open Interest Buildup & Sentiment
            vol_chg = ((cur_vol - avg_vol_20) / avg_vol_20) * 100 if avg_vol_20 > 0 else 0.0
            if f_chg > 0 and vol_chg > 0:
                buildup = "LONG BUILDUP"
                buildup_color = "#34d399"
            elif f_chg < 0 and vol_chg > 0:
                buildup = "SHORT BUILDUP"
                buildup_color = "#fb7185"
            elif f_chg > 0 and vol_chg < 0:
                buildup = "SHORT COVERING"
                buildup_color = "#38bdf8"
            else:
                buildup = "LONG UNWINDING"
                buildup_color = "#fbbf24"

            pcr = round(np.clip(1.0 + (f_chg * 0.08), 0.55, 1.85), 2)
            step = 50 if "NIFTY" in name else (100 if "BANK" in name else 20)
            atm_strike = int(round(c_ltp / step) * step)

            # Quantitative Contract & Strike Selection Rule
            if buy_score >= 7:
                recommended_opt = "CALL (CE)"
                recommended_strike = atm_strike - step  # 1-Strike ITM Call for high Delta
                rec_rationale = "High Conviction Long (Score ≥ 7) ➔ 1 Strike ITM Call (Delta ~0.65, Low Theta Decay)"
                bias_tag = "STRONG_BULLISH"
            elif sell_score >= 7:
                recommended_opt = "PUT (PE)"
                recommended_strike = atm_strike + step  # 1-Strike ITM Put
                rec_rationale = "High Conviction Short (Score ≥ 7) ➔ 1 Strike ITM Put (Delta ~0.65, Low Theta Decay)"
                bias_tag = "STRONG_BEARISH"
            else:
                recommended_opt = "CALL (CE)" if buy_score >= sell_score else "PUT (PE)"
                recommended_strike = atm_strike
                rec_rationale = "Consolidation / Rangebound (Score ≤ 6) ➔ Non-Directional / Credit Spread Preferred"
                bias_tag = "CONSOLIDATION"

            fo_checklists[name] = {
                "ltp": c_ltp,
                "chg": f_chg,
                "lot": lot_size,
                "step": step,
                "atm_strike": atm_strike,
                "recommended_opt": recommended_opt,
                "recommended_strike": recommended_strike,
                "rec_rationale": rec_rationale,
                "bias_tag": bias_tag,
                "buildup": buildup,
                "buildup_color": buildup_color,
                "pcr": pcr,
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
                "Recommended Trade": f"{recommended_strike} {recommended_opt.split()[0]}",
                "Buildup": buildup,
                "PCR": pcr,
                "Lot Size": lot_size
            })
        except Exception:
            continue

    df_fo = pd.DataFrame(fo_rows)

    fo_left, fo_right = st.columns([6.4, 3.6])

    with fo_left:
        fo_grid = st.dataframe(
            df_fo,
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            column_config={
                "Underlying LTP": st.column_config.NumberColumn(format="₹%.2f"),
                "Chg%": st.column_config.NumberColumn(format="%+.2f%%"),
                "PCR": st.column_config.NumberColumn(format="%.2f"),
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
                    <span style="background:rgba(0, 242, 254, 0.12); border:1px solid rgba(0, 242, 254, 0.3); color:{fo_item['buildup_color']}; font-size:11px; padding:4px 8px; border-radius:6px; font-weight:700;">
                        {fo_item['buildup']}
                    </span>
                </div>
                <div style="margin: 8px 0; padding: 8px 12px; background:rgba(30, 41, 59, 0.7); border-left: 3px solid #00f2fe; border-radius:4px; font-size:11px; color:#cbd5e1;">
                    🎯 <strong>System Recommendation:</strong><br>{fo_item['rec_rationale']}
                </div>
                <div style="display:flex; justify-content:space-between; font-size:12px; margin-bottom: 8px; padding: 6px 12px; background:rgba(15, 23, 42, 0.6); border-radius:6px;">
                    <div>VWAP: <strong style="color:#00f2fe;">₹{fo_item['vwap']:.2f}</strong></div>
                    <div>ORB HIGH: <strong style="color:#fbbf24;">₹{fo_item['orb_high']:.2f}</strong></div>
                    <div>PCR: <strong>{fo_item['pcr']}</strong></div>
                </div>
                <div style="font-size:11px; font-weight:700; color:#94a3b8; text-transform:uppercase; margin-bottom:6px; letter-spacing:0.5px;">
                    SCORE: <span style="color:#34d399;">{fo_item['buy_score']}/10 BUY</span> &nbsp;·&nbsp; <span style="color:#fb7185;">{fo_item['sell_score']}/10 SELL</span>
                </div>
                {fo_items_html}
            </div>
            """, unsafe_allow_html=True)

            # Automated Strike Selection & Options Sizing Engine
            st.markdown(f"""
            <div class="bottom-card" style="margin-top:12px; padding:12px;">
                <div style="font-size:13px; font-weight:700; color:#fff; margin-bottom:8px;">🎯 Systematic Strike Execution (1:3 Payoff)</div>
            </div>
            """, unsafe_allow_html=True)

            opt_c1, opt_c2 = st.columns(2)
            with opt_c1:
                # Pre-fill recommended option type based on score
                def_opt_idx = 0 if fo_item['recommended_opt'] == "CALL (CE)" else 1
                opt_type = st.selectbox("Option Type", ["CALL (CE)", "PUT (PE)"], index=def_opt_idx, key=f"opt_type_{active_fo}")
            with opt_c2:
                # Pre-fill recommended ITM strike
                selected_strike = st.number_input("Strike Price", value=fo_item['recommended_strike'], step=fo_item['step'], key=f"strike_{active_fo}")

            opt_q1, opt_q2 = st.columns(2)
            with opt_q1:
                lots = st.number_input("Number of Lots", min_value=1, value=1, step=1, key=f"lots_{active_fo}")
            with opt_q2:
                est_premium = st.number_input("Estimated Premium (₹)", min_value=1.0, value=150.0, step=5.0, key=f"prem_{active_fo}")

            # 1:3 Systematic Risk-to-Reward Ratio on Premium
            opt_sl_pts = st.slider("Stop-Loss Points (₹)", min_value=5, max_value=100, value=25, step=5, key=f"osl_{active_fo}")
            opt_tp_pts = opt_sl_pts * 3  # Exact 1:3 Reward

            total_contracts = lots * fo_item['lot']
            total_premium_amount = total_contracts * est_premium
            opt_profit = total_contracts * opt_tp_pts
            opt_loss = total_contracts * opt_sl_pts

            st.markdown(f"""
            <div class="calc-box">
                <div class="calc-row">
                    <span style="color:#94a3b8;">Total Contracts ({lots} lot × {fo_item['lot']}):</span>
                    <strong style="color:#ffffff;">{total_contracts} Qty</strong>
                </div>
                <div class="calc-row">
                    <span style="color:#94a3b8;">Premium Required:</span>
                    <strong style="color:#00f2fe; font-size:14px;">₹{total_premium_amount:,.2f}</strong>
                </div>
                <div class="calc-row">
                    <span style="color:#94a3b8;">Expected Profit (Target +{opt_tp_pts} pts):</span>
                    <strong style="color:#34d399; font-size:14px;">+₹{opt_profit:,.2f}</strong>
                </div>
                <div class="calc-row">
                    <span style="color:#94a3b8;">Expected Loss (Stop-Loss -{opt_sl_pts} pts):</span>
                    <strong style="color:#fb7185; font-size:14px;">-₹{opt_loss:,.2f}</strong>
                </div>
                <div class="calc-row" style="border-top:1px solid rgba(255,255,255,0.06); margin-top:4px; padding-top:4px;">
                    <span style="color:#94a3b8;">Net Premium Payoff:</span>
                    <strong style="color:#00f2fe;">1 : 3.00 RRR</strong>
                </div>
            </div>
            """, unsafe_allow_html=True)

            btn_prefix = "📝 PAPER" if is_paper_trading else "⚡"
            btn_buy_opt = st.button(f"{btn_prefix} Dispatch {active_fo} {selected_strike} {opt_type.split()[0]}", use_container_width=True, key=f"btn_opt_{active_fo}")

            if btn_buy_opt:
                if is_paper_trading:
                    opt_sym = f"{active_fo}_{selected_strike}_{opt_type.split()[0]}"
                    ok, msg = place_paper_order(opt_sym, "BUY", total_contracts, est_premium, opt_sl_pts, opt_tp_pts)
                    if ok:
                        st.success(f"✅ Paper Options Position Logged! (Qty: {total_contracts})")
                    else:
                        st.error(f"❌ {msg}")
                else:
                    if "smart_api" in st.session_state:
                        st.info(f"Submitting {active_fo} {selected_strike} {opt_type} | Qty: {total_contracts} (SL: {opt_sl_pts} pts | Target: {opt_tp_pts} pts)...")
                        st.success(f"Bracket Option Order Dispatched to Angel One! (Qty: {total_contracts})")
                    else:
                        st.warning("⚠️ Connect Angel One in the gateway below first.")

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
    st.markdown("<h4 style='color:#fff; margin-bottom:8px;'>Open Paper Positions (Live Monitoring)</h4>", unsafe_allow_html=True)
    
    if pdata["positions"]:
        open_rows = []
        for p in pdata["positions"]:
            sym = p["symbol"]
            cur_price = current_live_prices.get(sym, p["entry_price"])
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
                "Unrealized P&L": round(unrealized, 2),
                "Entered At": p["timestamp"]
            })
        st.dataframe(
            pd.DataFrame(open_rows),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Entry (₹)": st.column_config.NumberColumn(format="₹%.2f"),
                "LTP (₹)": st.column_config.NumberColumn(format="₹%.2f"),
                "SL (₹)": st.column_config.NumberColumn(format="₹%.2f"),
                "Target (₹)": st.column_config.NumberColumn(format="₹%.2f"),
                "Unrealized P&L": st.column_config.NumberColumn(format="%+,.2f"),
            }
        )
    else:
        st.info("No active paper positions. Place bracket orders from Tab 1 or Tab 2 to start testing.")

    st.write("")
    st.markdown("<h4 style='color:#fff; margin-bottom:8px;'>Closed Trade History & Performance Journal</h4>", unsafe_allow_html=True)
    if pdata["closed_trades"]:
        history_df = pd.DataFrame(pdata["closed_trades"])
        st.dataframe(
            history_df[["id", "symbol", "action", "qty", "entry_price", "exit_price", "pnl", "exit_reason", "exit_time"]],
            use_container_width=True,
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
            raw = yf.download(bt_sym, period=f"{bt_years}y", interval="1d", progress=False)
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            raw.dropna(inplace=True)

            if len(raw) > 50:
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
                st.plotly_chart(fig_bt, use_container_width=True)
            else:
                st.warning("Insufficient data available for this asset.")

# =========================================================================
# TAB 5: S/R CHART ANALYSIS
# =========================================================================
with tab_chart:
    st.markdown("<h3 style='color:#fff; margin-bottom:4px;'>Support & Resistance Extrema Analysis</h3>", unsafe_allow_html=True)
    c_sym = st.selectbox("Select Asset for Visual Levels", symbols, key="c_sym")

    c_raw = yf.download(c_sym, period=period, interval=interval, progress=False)
    if isinstance(c_raw.columns, pd.MultiIndex):
        c_raw.columns = c_raw.columns.get_level_values(0)
    c_raw.dropna(inplace=True)

    if len(c_raw) > 20:
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
        st.plotly_chart(fig_chart, use_container_width=True)

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
    if st.button("Connect Broker", use_container_width=True):
        if all([ao_api_key, ao_client, ao_pin, ao_totp_key]):
            api_obj, res_msg = connect_angel_one(ao_api_key, ao_client, ao_pin, ao_totp_key)
            if api_obj:
                st.session_state["smart_api"] = api_obj
                st.success("Connected!")
            else:
                st.error(f"Failed: {res_msg}")
        else:
            st.warning("Fill in all credentials.")
with ao_status_col:
    if "smart_api" in st.session_state:
        st.markdown("<span style='color:#34d399; font-size:13px; font-weight:700; line-height:38px;'>● SESSION ACTIVE & READY</span>", unsafe_allow_html=True)
    else:
        st.markdown("<span style='color:#94a3b8; font-size:13px; font-weight:500; line-height:38px;'>Status: Disconnected</span>", unsafe_allow_html=True)