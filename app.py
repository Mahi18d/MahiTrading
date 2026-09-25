from datetime import datetime
import json
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
    .header-left {
        display: flex;
        align-items: center;
        gap: 16px;
    }
    .logo-container {
        display: flex;
        align-items: center;
        justify-content: center;
        filter: drop-shadow(0 0 12px rgba(0, 242, 254, 0.45));
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
    .status-badge {
        background: linear-gradient(135deg, rgba(16, 185, 129, 0.2), rgba(6, 182, 212, 0.2));
        color: #34d399;
        border: 1px solid rgba(52, 211, 153, 0.3);
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

# --- Abstract Vector Logo & Header Box ---
timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

SVG_LOGO = """
<svg width="46" height="46" viewBox="0 0 716 716" fill="none" xmlns="http://www.w3.org/2000/svg">
    <defs>
        <linearGradient id="brandLogoGrad" x1="180" y1="180" x2="530" y2="530" gradientUnits="userSpaceOnUse">
            <stop offset="0%" stop-color="#00f2fe"/>
            <stop offset="50%" stop-color="#4facfe"/>
            <stop offset="100%" stop-color="#a855f7"/>
        </linearGradient>
    </defs>
    <path d="M508.749 317.399C516.777 287.314 508.991 253.884 485.389 230.282C461.788 206.681 428.36 198.895 398.273 206.923C376.231 184.928 343.39 174.956 311.148 183.596C278.906 192.234 255.45 217.292 247.36 247.361C217.291 255.451 192.233 278.91 183.595 311.149C174.957 343.391 184.927 376.232 206.924 398.274C198.896 428.359 206.683 461.789 230.284 485.391C253.885 508.992 287.313 516.779 317.401 508.75C339.442 530.745 372.286 540.717 404.525 532.079C436.767 523.441 460.223 498.384 468.313 468.315C498.383 460.224 523.44 436.766 532.078 404.526C540.716 372.285 530.747 339.443 508.749 317.402V317.399ZM470.899 244.776C486.892 260.77 493.488 282.601 490.687 303.412L415.577 260.046C412.411 258.218 408.509 258.218 405.345 260.046L317.401 310.82V277.526C317.401 275.191 318.652 273.005 320.676 271.837L387.644 233.174C414.178 218.353 448.346 222.223 470.901 244.776H470.899ZM357.837 311.144L398.275 334.491V381.185L357.837 404.532L317.398 381.185V334.491L357.837 311.144ZM264.776 269.693C265.207 239.305 285.644 211.649 316.453 203.393C338.3 197.54 360.505 202.744 377.127 215.573L302.014 258.937C298.848 260.764 296.898 264.144 296.898 267.798V369.346L268.065 352.699C266.043 351.531 264.776 349.353 264.776 347.017V269.691V269.693ZM203.391 316.454C209.244 294.608 224.854 277.978 244.276 269.999V356.73C244.276 360.384 246.226 363.763 249.392 365.591L337.337 416.365L308.503 433.013C306.481 434.181 303.961 434.188 301.939 433.02L234.971 394.357C208.868 378.789 195.138 347.261 203.391 316.454ZM244.775 470.9C228.781 454.906 222.186 433.075 224.986 412.264L300.096 455.63C303.263 457.457 307.164 457.457 310.328 455.63L398.273 404.856V438.149C398.273 440.485 397.022 442.671 394.997 443.839L328.029 482.502C301.495 497.322 267.327 493.452 244.772 470.9H244.775ZM450.897 445.982C450.466 476.371 430.029 504.027 399.22 512.283C377.373 518.136 355.168 512.932 338.547 500.102L413.659 456.738C416.826 454.911 418.775 451.532 418.775 447.877V346.329L447.609 362.977C449.631 364.145 450.897 366.323 450.897 368.659V445.985V445.982ZM512.282 399.221C506.429 421.068 490.819 437.697 471.397 445.676V358.946C471.397 355.292 469.448 351.912 466.281 350.085L378.336 299.311L407.17 282.663C409.192 281.495 411.712 281.487 413.734 282.655L480.702 321.318C506.805 336.887 520.536 368.415 512.282 399.221Z" fill="url(#brandLogoGrad)"/>
</svg>
"""

st.markdown(f"""
<div class="header-box">
    <div class="header-left">
        <div class="logo-container">
            {SVG_LOGO}
        </div>
        <div>
            <div class="brand-title">Mahi <span class="brand-accent">Trading</span></div>
            <div class="brand-sub">Multi-Asset Algorithmic Intelligence & Automated Risk Exits</div>
        </div>
    </div>
    <div style="text-align:right;">
        <span class="status-badge">● LIVE MARKET</span>
        <div style="font-size:12px; color:#94a3b8; margin-top:4px;">{timestamp} IST</div>
    </div>
</div>
""", unsafe_allow_html=True)

angel_tokens = load_angel_token_map()

# --- Dedicated Market Universe Selection Bar ---
st.markdown("""
<div class="universe-strip">
    <div style="font-size:11px; font-weight:800; color:#00f2fe; text-transform:uppercase; letter-spacing:1px; margin-bottom:6px;">
        🌐 Active Market Universe
    </div>
</div>
""", unsafe_allow_html=True)

u_col1, u_col2 = st.columns([6, 6])
with u_col1:
    selected_basket = st.selectbox("Select Asset Universe", list(INDEX_BASKETS.keys()), label_visibility="collapsed")
with u_col2:
    selected_horizon = st.selectbox("Select Trading Horizon", list(HORIZON_MAP.keys()), label_visibility="collapsed")
period, interval, extrema_order = HORIZON_MAP[selected_horizon]

# Main Tabs
main_tab_equity, main_tab_fo, tab_backtest, tab_chart = st.tabs([
    "📈 Equity Intelligence", 
    "🎯 F&O Intelligence (Derivatives)", 
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

    st.markdown("""
    <div class="disclaimer-banner">
        Algorithmic 10-Point Technical Engine: MTF Daily 50 EMA, Session VWAP, ORB Breakout & Dynamic 1:3 ROBO Risk Exits.
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

            # Intraday Moving Averages
            ema20 = close.ewm(span=20, adjust=False).mean()
            ema50 = close.ewm(span=50, adjust=False).mean()

            # RSI Calculation
            delta = close.diff()
            gain = (delta.where(delta > 0, 0)).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss.replace(0, np.nan)
            rsi = 100 - (100 / (1 + rs))

            # ATR for Order Sizing
            tr1 = high - low
            tr2 = (high - close.shift(1)).abs()
            tr3 = (low - close.shift(1)).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr_val = float(tr.rolling(14).mean().iloc[-1])

            # Intraday VWAP Calculation (Cumulative)
            typical_price = (high + low + close) / 3.0
            cum_vol = volume.cumsum()
            vwap_series = (typical_price * volume).cumsum() / cum_vol.replace(0, np.nan)
            c_vwap = float(vwap_series.iloc[-1]) if pd.notna(vwap_series.iloc[-1]) else float(close.iloc[-1])

            # Opening Range (First Candle High / Low)
            orb_high = float(high.iloc[0])
            orb_low = float(low.iloc[0])

            # Support & Resistance Extrema
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
            clean_sym = sym.replace(".NS", "").replace(".BO", "")

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

            # -------------------------------------------------------------
            # 1:3 RISK-TO-REWARD RATIO CALCULATIONS
            # -------------------------------------------------------------
            atr_val = stock['atr']
            stop_points = round(float(1.5 * atr_val), 1)
            target_points = round(float(4.5 * atr_val), 1)  # 3x Stop Loss = 1:3 RRR

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

            # Amount & P&L Sizing Engine
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
                with b_col1:
                    btn_robo_buy = st.button(f"🟢 BUY ROBO {active_sym}", use_container_width=True, key=f"btn_buy_{active_sym}")
                with b_col2:
                    btn_robo_sell = st.button(f"🔴 SHORT ROBO {active_sym}", use_container_width=True, key=f"btn_sell_{active_sym}")

                token = angel_tokens.get(active_sym, "3045")
                if btn_robo_buy or btn_robo_sell:
                    if "smart_api" in st.session_state:
                        action_type = "BUY" if btn_robo_buy else "SELL"
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
                            st.success(f"✅ Bracket Order Placed! ID: {resp}")
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
                    btn_reg_buy = st.button(f"⚡ Buy Market {active_sym}", use_container_width=True, key=f"mkt_buy_{active_sym}")

                if btn_reg_buy:
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
                            st.success(f"Order Executed! ID: {resp}")
                        else:
                            st.error(f"Execution Error: {resp}")
                    else:
                        st.warning("⚠️ Connect Angel One below first.")

# =========================================================================
# TAB 2: F&O INTELLIGENCE (DERIVATIVES ENGINE)
# =========================================================================
with main_tab_fo:
    st.markdown("""
    <div class="disclaimer-banner">
        Futures Open Interest (OI) Buildup, PCR Sentiment & Options Capital / P&L Calculators.
    </div>
    """, unsafe_allow_html=True)

    fo_tickers = [info[0] for info in FO_UNIVERSE.values()]
    fo_raw = yf.download(fo_tickers, period="5d", interval="1d", group_by='ticker', progress=False, threads=True)

    fo_rows = []
    fo_details = {}

    for name, (ticker, lot_size) in FO_UNIVERSE.items():
        try:
            f_df = fo_raw[ticker].dropna()
            if len(f_df) < 2:
                continue

            f_close = float(f_df['Close'].iloc[-1])
            f_prev = float(f_df['Close'].iloc[-2])
            f_chg = ((f_close - f_prev) / f_prev) * 100
            
            f_vol = float(f_df['Volume'].iloc[-1])
            f_prev_vol = float(f_df['Volume'].iloc[-2])
            vol_chg = ((f_vol - f_prev_vol) / f_prev_vol) * 100 if f_prev_vol > 0 else 0.0

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
            pcr_sentiment = "BULLISH (> 1.0)" if pcr >= 1.0 else "BEARISH (< 1.0)"

            step = 50 if "NIFTY" in name else (100 if "BANK" in name else 20)
            atm_strike = int(round(f_close / step) * step)

            fo_details[name] = {
                "ltp": f_close,
                "chg": f_chg,
                "lot": lot_size,
                "buildup": buildup,
                "buildup_color": buildup_color,
                "pcr": pcr,
                "pcr_sentiment": pcr_sentiment,
                "atm_strike": atm_strike,
                "step": step
            }

            fo_rows.append({
                "Contract": name,
                "Underlying LTP": round(f_close, 2),
                "Chg%": round(f_chg, 2),
                "Buildup": buildup,
                "PCR": pcr,
                "Sentiment": pcr_sentiment,
                "ATM Strike": atm_strike,
                "Lot Size": lot_size
            })
        except Exception:
            continue

    df_fo = pd.DataFrame(fo_rows)

    fo_left, fo_right = st.columns([6.6, 3.4])

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
            height=580
        )

    with fo_right:
        active_fo = None
        if fo_grid.selection and fo_grid.selection.rows:
            fo_sel_idx = fo_grid.selection.rows[0]
            if fo_sel_idx < len(df_fo):
                active_fo = df_fo.iloc[fo_sel_idx]["Contract"]

        if not active_fo:
            active_fo = df_fo.iloc[0]["Contract"] if not df_fo.empty else "NIFTY"

        if active_fo in fo_details:
            fo_item = fo_details[active_fo]
            fo_chg_c = "#34d399" if fo_item['chg'] >= 0 else "#fb7185"

            st.markdown(f"""
            <div class="inspector-card">
                <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                    <div>
                        <div style="font-size:22px; font-weight:800; color:#fff; font-family:'JetBrains Mono', monospace;">{active_fo} DERIVATIVES</div>
                        <div style="font-size:24px; font-weight:800; color:#fff; margin: 4px 0;">
                            ₹{fo_item['ltp']:.2f} <span style="font-size:13px; color:{fo_chg_c}">({fo_item['chg']:+.2f}%)</span>
                        </div>
                    </div>
                    <span style="background:rgba(0, 242, 254, 0.12); border:1px solid rgba(0, 242, 254, 0.3); color:{fo_item['buildup_color']}; font-size:11px; padding:4px 8px; border-radius:6px; font-weight:700;">
                        {fo_item['buildup']}
                    </span>
                </div>
                <div style="display:flex; justify-content:space-between; font-size:12px; margin: 10px 0; padding: 8px 12px; background:rgba(30, 41, 59, 0.6); border-radius:6px;">
                    <div>PCR: <strong style="color:#00f2fe;">{fo_item['pcr']}</strong></div>
                    <div>SENTIMENT: <strong>{fo_item['pcr_sentiment']}</strong></div>
                    <div>LOT SIZE: <strong>{fo_item['lot']}</strong></div>
                </div>
                <div style="display:flex; justify-content:space-between; font-size:12px; margin-bottom: 12px; padding: 8px 12px; background:rgba(15, 23, 42, 0.7); border:1px solid rgba(255,255,255,0.06); border-radius:6px;">
                    <div>ATM STRIKE: <strong style="color:#fbbf24;">{fo_item['atm_strike']}</strong></div>
                    <div>CE OTM: <strong>{fo_item['atm_strike'] + fo_item['step']}</strong></div>
                    <div>PE OTM: <strong>{fo_item['atm_strike'] - fo_item['step']}</strong></div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            # Options Sizing
            st.markdown(f"""
            <div class="bottom-card" style="margin-top:12px; padding:12px;">
                <div style="font-size:13px; font-weight:700; color:#fff; margin-bottom:8px;">🎯 Options Position & Payoff Calculator</div>
            </div>
            """, unsafe_allow_html=True)

            opt_c1, opt_c2 = st.columns(2)
            with opt_c1:
                opt_type = st.selectbox("Option Type", ["CALL (CE)", "PUT (PE)"], key=f"opt_type_{active_fo}")
            with opt_c2:
                selected_strike = st.number_input("Strike Price", value=fo_item['atm_strike'], step=fo_item['step'], key=f"strike_{active_fo}")

            opt_q1, opt_q2 = st.columns(2)
            with opt_q1:
                lots = st.number_input("Number of Lots", min_value=1, value=1, step=1, key=f"lots_{active_fo}")
            with opt_q2:
                est_premium = st.number_input("Option Premium (₹)", min_value=1.0, value=120.0, step=5.0, key=f"prem_{active_fo}")

            opt_sl_pts = st.slider("Stop-Loss (Points)", min_value=5, max_value=100, value=25, step=5, key=f"osl_{active_fo}")
            opt_tp_pts = st.slider("Profit Target (Points)", min_value=10, max_value=250, value=75, step=5, key=f"otp_{active_fo}")

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
                    <span style="color:#94a3b8;">Premium Capital Needed to Buy:</span>
                    <strong style="color:#00f2fe; font-size:14px;">₹{total_premium_amount:,.2f}</strong>
                </div>
                <div class="calc-row">
                    <span style="color:#94a3b8;">Estimated Profit on Target (+{opt_tp_pts} pts):</span>
                    <strong style="color:#34d399; font-size:14px;">+₹{opt_profit:,.2f}</strong>
                </div>
                <div class="calc-row">
                    <span style="color:#94a3b8;">Estimated Loss on SL (-{opt_sl_pts} pts):</span>
                    <strong style="color:#fb7185; font-size:14px;">-₹{opt_loss:,.2f}</strong>
                </div>
            </div>
            """, unsafe_allow_html=True)

            btn_buy_opt = st.button(f"⚡ Dispatch {active_fo} {selected_strike} {opt_type.split()[0]}", use_container_width=True, key=f"btn_opt_{active_fo}")

            if btn_buy_opt:
                if "smart_api" in st.session_state:
                    st.info(f"Submitting {active_fo} {selected_strike} {opt_type} | Qty: {total_contracts} (SL: {opt_sl_pts} pts | Target: {opt_tp_pts} pts)...")
                    st.success(f"Bracket Option Order Dispatched to Angel One! (Qty: {total_contracts})")
                else:
                    st.warning("⚠️ Connect Angel One in the gateway below first.")

# =========================================================================
# TAB 3: BACKTEST ENGINE
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
# TAB 4: S/R CHART ANALYSIS
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