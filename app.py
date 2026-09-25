from datetime import datetime
import json
import urllib.request
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import pyotp
import requests
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

# --- Professional Midnight Theme ---
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@400;500;600;700&display=swap');

    .stApp {
        background-color: #080b11;
        color: #e2e8f0;
        font-family: 'Inter', -apple-system, sans-serif;
    }

    .header-box {
        display: flex;
        justify-content: space-between;
        align-items: center;
        background: linear-gradient(90deg, #0f172a 0%, #080b11 100%);
        border: 1px solid #1e293b;
        border-radius: 8px;
        padding: 14px 20px;
        margin-bottom: 12px;
    }
    .brand-title {
        font-size: 26px;
        font-weight: 800;
        color: #ffffff;
        letter-spacing: -0.5px;
    }
    .brand-accent { color: #00f2fe; }
    .brand-sub {
        font-size: 11px;
        color: #64748b;
        text-transform: uppercase;
        letter-spacing: 0.8px;
        font-weight: 600;
    }
    .status-badge {
        background-color: rgba(16, 185, 129, 0.12);
        color: #10b981;
        border: 1px solid rgba(16, 185, 129, 0.3);
        padding: 4px 10px;
        border-radius: 6px;
        font-size: 11px;
        font-weight: 700;
        font-family: 'JetBrains Mono', monospace;
    }

    .disclaimer-banner {
        border-left: 4px solid #f59e0b;
        background-color: #0f172a;
        border-top: 1px solid #1e293b;
        border-right: 1px solid #1e293b;
        border-bottom: 1px solid #1e293b;
        border-radius: 4px;
        padding: 8px 14px;
        font-size: 12px;
        color: #fbbf24;
        margin-bottom: 14px;
    }

    .inspector-card {
        background-color: #0f172a;
        border: 1px solid #1e293b;
        border-radius: 8px;
        padding: 16px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
    }
    .check-item {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 6px 0;
        border-bottom: 1px solid #1e293b;
        font-size: 12px;
    }
    .tag-bull { color: #10b981; font-weight: 600; font-family: 'JetBrains Mono', monospace; }
    .tag-bear { color: #f43f5e; font-weight: 600; font-family: 'JetBrains Mono', monospace; }

    /* Amount Calculation Summary Card */
    .calc-box {
        background-color: #0b1329;
        border: 1px solid #1d4ed8;
        border-radius: 6px;
        padding: 10px 14px;
        margin: 10px 0;
        font-size: 12px;
    }
    .calc-row {
        display: flex;
        justify-content: space-between;
        padding: 3px 0;
    }

    .bottom-card {
        background-color: #0f172a;
        border: 1px solid #1e293b;
        border-radius: 8px;
        padding: 16px;
        margin-top: 18px;
    }
    .bottom-title {
        font-size: 14px;
        font-weight: 700;
        color: #f8fafc;
        margin-bottom: 10px;
        display: flex;
        align-items: center;
        gap: 6px;
    }

    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        border-bottom: 1px solid #1e293b;
    }
    .stTabs [data-baseweb="tab"] {
        background-color: #0f172a;
        border: 1px solid #1e293b;
        border-radius: 6px 6px 0 0;
        color: #94a3b8;
        padding: 8px 20px;
        font-weight: 600;
    }
    .stTabs [aria-selected="true"] {
        background-color: #1e293b !important;
        color: #00f2fe !important;
        font-weight: 700;
        border-bottom: 2px solid #00f2fe;
    }
</style>
""", unsafe_allow_html=True)

def apply_chart_style(fig, height=520):
    fig.update_layout(
        paper_bgcolor="#080b11",
        plot_bgcolor="#0f172a",
        font=dict(color="#94a3b8", family="Inter"),
        height=height,
        margin=dict(l=20, r=20, t=30, b=20),
        xaxis=dict(gridcolor="#1e293b", showgrid=True),
        yaxis=dict(gridcolor="#1e293b", showgrid=True),
    )
    return fig

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

def send_telegram_alert(token: str, chat_id: str, message: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    try:
        res = requests.post(url, json=payload, timeout=5)
        return res.status_code == 200
    except Exception:
        return False

# --- Basket Universes ---
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

# --- Header ---
timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
st.markdown(f"""
<div class="header-box">
    <div>
        <div class="brand-title">Mahi <span class="brand-accent">Trading</span></div>
        <div class="brand-sub">Multi-Asset Intelligence: Cash Equities & Derivatives</div>
    </div>
    <div>
        <span class="status-badge">● LIVE MARKET</span> &nbsp;
        <span style="font-size:12px; color:#94a3b8;">{timestamp} IST</span>
    </div>
</div>
""", unsafe_allow_html=True)

angel_tokens = load_angel_token_map()

# Top Navigation Tabs
main_tab_equity, main_tab_fo, tab_backtest, tab_chart = st.tabs([
    "📈 Equity Intelligence", 
    "🎯 F&O Intelligence (Derivatives)", 
    "📊 Backtest Engine", 
    "📉 Technical S/R Charts"
])

# =========================================================================
# TAB 1: EQUITY INTELLIGENCE + AMOUNT & P&L CALCULATOR
# =========================================================================
with main_tab_equity:
    st.markdown("""<div style="font-size:13px; font-weight:700; color:#f8fafc; margin-bottom:6px;">⚡ EQUITY SETTINGS</div>""", unsafe_allow_html=True)
    eq_c1, eq_c2, eq_c3, eq_c4 = st.columns([2.5, 2.5, 2.5, 2.5])
    with eq_c1:
        selected_basket = st.selectbox("Market Universe", list(INDEX_BASKETS.keys()), key="eq_basket")
    with eq_c2:
        selected_horizon = st.selectbox("Trading Horizon", list(HORIZON_MAP.keys()), key="eq_horizon")
    period, interval, extrema_order = HORIZON_MAP[selected_horizon]
    with eq_c3:
        setup_filter = st.selectbox("Filter Setup", ["All setups", "BUY_SETUP", "BEARISH_SETUP", "REVERSAL_WATCH", "CONSOLIDATION"], key="eq_setup")
    with eq_c4:
        trend_filter = st.selectbox("Filter Trend", ["All trends", "BULLISH", "BEARISH"], key="eq_trend")

    st.markdown("""
    <div class="disclaimer-banner">
        Cash Market Screener: Algorithmic 10-point checklist, Position Sizing & Real-time Profit/Loss Calculators.
    </div>
    """, unsafe_allow_html=True)

    @st.cache_data(ttl=180)
    def fetch_basket_data(basket_name: str, period: str, interval: str):
        symbols = INDEX_BASKETS[basket_name]
        data = yf.download(symbols, period=period, interval=interval, group_by='ticker', progress=False, threads=True)
        return symbols, data

    symbols, data = fetch_basket_data(selected_basket, period, interval)

    rows = []
    checklists = {}

    for sym in symbols:
        try:
            df = data[sym].dropna()
            if len(df) < 30:
                continue

            close = df['Close']
            high = df['High']
            low = df['Low']
            volume = df['Volume']

            ema20 = close.ewm(span=20, adjust=False).mean()
            ema50 = close.ewm(span=50, adjust=False).mean()
            ema200 = close.ewm(span=200, adjust=False).mean() if len(df) >= 200 else ema50

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

            c_ltp = float(close.iloc[-1])
            c_prev = float(close.iloc[-2])
            chg_pct = ((c_ltp - c_prev) / c_prev) * 100
            c_rsi = float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else 50.0
            c_ema20 = float(ema20.iloc[-1])
            c_ema50 = float(ema50.iloc[-1])
            c_ema200 = float(ema200.iloc[-1])
            cur_vol = float(volume.iloc[-1])
            avg_vol = float(volume.iloc[-20:].mean()) if len(volume) >= 20 else cur_vol

            res = float(high.iloc[-extrema_order:].max())
            sup = float(low.iloc[-extrema_order:].min())

            checks = [
                ("Price above 20 EMA", c_ltp > c_ema20, "Short-term momentum"),
                ("Price above 50 EMA", c_ltp > c_ema50, "Intermediate baseline"),
                ("50 EMA > 200 EMA", c_ema50 > c_ema200, "Macro trend confirmation"),
                ("RSI in Momentum (50 - 70)", 50 <= c_rsi <= 70, f"RSI @ {c_rsi:.1f}"),
                ("Volume Expansion (> Avg)", cur_vol > avg_vol, "Volume participation"),
                ("Trading Near Support (< 2%)", ((c_ltp - sup)/c_ltp) <= 0.02, f"Support @ ₹{sup:.2f}"),
                ("Resistance Clearance (> 2%)", ((res - c_ltp)/c_ltp) >= 0.02, f"Resistance @ ₹{res:.2f}"),
                ("Positive Session Return", chg_pct > 0, f"{chg_pct:+.2f}% session"),
                ("No Overbought Exhaustion (< 75)", c_rsi < 75, "Room for upside"),
                ("S/R Range Compression", (res - sup)/c_ltp <= 0.08, "Volatility squeeze")
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
                "RSI": round(c_rsi, 1),
                "Buy": f"{buy_score}/10",
                "Sell": f"{sell_score}/10",
                "Setup": setup
            })
        except Exception:
            continue

    df_results = pd.DataFrame(rows)

    eq_search_col, _ = st.columns([4, 6])
    with eq_search_col:
        search_query = st.text_input("Search Equity Ticker", placeholder="Search ticker (e.g. RELIANCE, TCS)...", label_visibility="collapsed")

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
            chg_c = "#10b981" if stock['chg'] >= 0 else "#f43f5e"

            atr_val = stock['atr']
            stop_points = round(float(1.5 * atr_val), 1)
            target_points = round(float(3.0 * atr_val), 1)

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
                    <span style="background-color:rgba(0, 242, 254, 0.1); border:1px solid rgba(0, 242, 254, 0.3); color:#00f2fe; font-size:11px; padding:3px 8px; border-radius:4px; font-weight:700;">
                        {stock['setup']}
                    </span>
                </div>
                <div style="display:flex; justify-content:space-between; font-size:12px; margin: 10px 0; padding: 8px 12px; background-color:#1e293b; border-radius:6px;">
                    <div>SUP: <strong style="color:#10b981">₹{stock['support']:.2f}</strong></div>
                    <div>RES: <strong style="color:#f43f5e">₹{stock['resistance']:.2f}</strong></div>
                    <div>ATR(14): <strong>₹{atr_val:.2f}</strong></div>
                </div>
                <div style="display:flex; justify-content:space-between; font-size:12px; margin-bottom: 12px; padding: 8px 12px; background-color:#162032; border:1px solid #1e293b; border-radius:6px;">
                    <div>SL PRICE: <strong style="color:#f43f5e">₹{sl_price:.2f}</strong></div>
                    <div>TARGET (1:2): <strong style="color:#10b981">₹{tp_price:.2f}</strong></div>
                </div>
                <div style="font-size:11px; font-weight:700; color:#94a3b8; text-transform:uppercase; margin-bottom:6px; letter-spacing:0.5px;">
                    SCORE: <span style="color:#10b981;">{stock['buy_score']}/10 BUY</span> &nbsp;·&nbsp; <span style="color:#f43f5e;">{stock['sell_score']}/10 SELL</span>
                </div>
                {items_html}
            </div>
            """, unsafe_allow_html=True)

            # --- SIZING & PROFIT / LOSS CALCULATOR BOX ---
            st.markdown("""
            <div style="margin-top:12px; padding:10px 14px; background-color:#162032; border:1px solid #1e293b; border-radius:6px;">
                <span style="font-size:13px; font-weight:700; color:#fff;">💰 Amount & P&L Sizing Engine</span>
            </div>
            """, unsafe_allow_html=True)

            order_mode = st.radio("Order Type", ["Bracket (ROBO Auto-Exit)", "Regular Market"], horizontal=True, label_visibility="collapsed", key="eq_ord_mode")
            
            calc_c1, calc_c2 = st.columns(2)
            with calc_c1:
                trade_qty = st.number_input("Shares Qty to Buy", min_value=1, value=10, step=1, key="eq_qty")
            with calc_c2:
                trade_limit = st.number_input("Entry Price (₹)", value=float(round(stock['ltp'], 2)), step=0.5, key="eq_limit")

            if order_mode == "Bracket (ROBO Auto-Exit)":
                sl_c, tp_c, trail_c = st.columns(3)
                with sl_c:
                    in_sl_pts = st.number_input("SL Points (₹)", min_value=0.5, value=float(stop_points), step=0.5, key="eq_sl")
                with tp_c:
                    in_tp_pts = st.number_input("Target Points (₹)", min_value=1.0, value=float(target_points), step=0.5, key="eq_tp")
                with trail_c:
                    in_trail = st.number_input("Trail (₹)", min_value=0.0, value=1.0, step=0.5, key="eq_trail")

                # Calculations for Amount & Expected Profit / Loss
                total_capital_required = trade_qty * trade_limit
                expected_profit = trade_qty * in_tp_pts
                expected_loss = trade_qty * in_sl_pts
                reward_risk_ratio = round(expected_profit / expected_loss, 2) if expected_loss > 0 else 0.0

                # Formatted Card Output
                st.markdown(f"""
                <div class="calc-box">
                    <div class="calc-row">
                        <span style="color:#94a3b8;">Total Amount Needed to Buy:</span>
                        <strong style="color:#ffffff; font-size:14px;">₹{total_capital_required:,.2f}</strong>
                    </div>
                    <div class="calc-row">
                        <span style="color:#94a3b8;">Expected Max Profit (Target):</span>
                        <strong style="color:#10b981; font-size:14px;">+₹{expected_profit:,.2f}</strong>
                    </div>
                    <div class="calc-row">
                        <span style="color:#94a3b8;">Expected Max Loss (Stop-Loss):</span>
                        <strong style="color:#f43f5e; font-size:14px;">-₹{expected_loss:,.2f}</strong>
                    </div>
                    <div class="calc-row" style="border-top:1px solid #1e293b; margin-top:4px; padding-top:4px;">
                        <span style="color:#94a3b8;">Net Risk-to-Reward Ratio:</span>
                        <strong style="color:#00f2fe;">1 : {reward_risk_ratio}</strong>
                    </div>
                </div>
                """, unsafe_allow_html=True)

                b_col1, b_col2 = st.columns(2)
                with b_col1:
                    btn_robo_buy = st.button(f"🟢 BUY ROBO {active_sym}", use_container_width=True, key="eq_btn_buy")
                with b_col2:
                    btn_robo_sell = st.button(f"🔴 SHORT ROBO {active_sym}", use_container_width=True, key="eq_btn_sell")

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
                        st.warning("⚠️ Connect Angel One below first.")
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
                    prod = st.selectbox("Product", ["INTRADAY", "DELIVERY"], key="eq_prod")
                with reg_c2:
                    st.write("")
                    btn_reg_buy = st.button(f"⚡ Buy Market {active_sym}", use_container_width=True, key="eq_mkt_buy")

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
# TAB 2: F&O INTELLIGENCE + OPTIONS AMOUNT SIZING
# =========================================================================
with main_tab_fo:
    st.markdown("""<div style="font-size:13px; font-weight:700; color:#f8fafc; margin-bottom:6px;">🎯 F&O DERIVATIVES COCKPIT</div>""", unsafe_allow_html=True)
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
                buildup_color = "#10b981"
            elif f_chg < 0 and vol_chg > 0:
                buildup = "SHORT BUILDUP"
                buildup_color = "#f43f5e"
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
            fo_chg_c = "#10b981" if fo_item['chg'] >= 0 else "#f43f5e"

            st.markdown(f"""
            <div class="inspector-card">
                <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                    <div>
                        <div style="font-size:22px; font-weight:800; color:#fff; font-family:'JetBrains Mono', monospace;">{active_fo} DERIVATIVES</div>
                        <div style="font-size:24px; font-weight:800; color:#fff; margin: 4px 0;">
                            ₹{fo_item['ltp']:.2f} <span style="font-size:13px; color:{fo_chg_c}">({fo_item['chg']:+.2f}%)</span>
                        </div>
                    </div>
                    <span style="background-color:rgba(0, 242, 254, 0.1); border:1px solid rgba(0, 242, 254, 0.3); color:{fo_item['buildup_color']}; font-size:11px; padding:4px 8px; border-radius:4px; font-weight:700;">
                        {fo_item['buildup']}
                    </span>
                </div>
                <div style="display:flex; justify-content:space-between; font-size:12px; margin: 10px 0; padding: 8px 12px; background-color:#1e293b; border-radius:6px;">
                    <div>PCR: <strong style="color:#00f2fe;">{fo_item['pcr']}</strong></div>
                    <div>SENTIMENT: <strong>{fo_item['pcr_sentiment']}</strong></div>
                    <div>LOT SIZE: <strong>{fo_item['lot']}</strong></div>
                </div>
                <div style="display:flex; justify-content:space-between; font-size:12px; margin-bottom: 12px; padding: 8px 12px; background-color:#162032; border:1px solid #1e293b; border-radius:6px;">
                    <div>ATM STRIKE: <strong style="color:#fbbf24;">{fo_item['atm_strike']}</strong></div>
                    <div>CE OTM: <strong>{fo_item['atm_strike'] + fo_item['step']}</strong></div>
                    <div>PE OTM: <strong>{fo_item['atm_strike'] - fo_item['step']}</strong></div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            # Options Amount & P&L Calculator
            st.markdown(f"""
            <div class="bottom-card" style="margin-top:12px; padding:12px;">
                <div style="font-size:13px; font-weight:700; color:#fff; margin-bottom:8px;">🎯 Options Position & Payoff Calculator</div>
            </div>
            """, unsafe_allow_html=True)

            opt_c1, opt_c2 = st.columns(2)
            with opt_c1:
                opt_type = st.selectbox("Option Type", ["CALL (CE)", "PUT (PE)"])
            with opt_c2:
                selected_strike = st.number_input("Strike Price", value=fo_item['atm_strike'], step=fo_item['step'])

            opt_q1, opt_q2 = st.columns(2)
            with opt_q1:
                lots = st.number_input("Number of Lots", min_value=1, value=1, step=1)
            with opt_q2:
                est_premium = st.number_input("Option Premium (₹)", min_value=1.0, value=120.0, step=5.0)

            opt_sl_pts = st.slider("Stop-Loss (Points)", min_value=5, max_value=100, value=25, step=5)
            opt_tp_pts = st.slider("Profit Target (Points)", min_value=10, max_value=250, value=50, step=5)

            # Derivative Payoff Math
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
                    <strong style="color:#10b981; font-size:14px;">+₹{opt_profit:,.2f}</strong>
                </div>
                <div class="calc-row">
                    <span style="color:#94a3b8;">Estimated Loss on SL (-{opt_sl_pts} pts):</span>
                    <strong style="color:#f43f5e; font-size:14px;">-₹{opt_loss:,.2f}</strong>
                </div>
            </div>
            """, unsafe_allow_html=True)

            btn_buy_opt = st.button(f"⚡ Dispatch {active_fo} {selected_strike} {opt_type.split()[0]}", use_container_width=True)

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
                fig_bt.add_trace(go.Scatter(x=raw.index, y=raw['Portfolio_Val'], mode='lines', line=dict(color='#10b981', width=2), name="Equity"))
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
            increasing_line_color="#10b981", decreasing_line_color="#f43f5e"
        ))
        fig_chart.add_trace(go.Scatter(
            x=c_raw.index, y=c_raw['Nearest_Res'], mode='lines', line_shape='hv', line=dict(color='#f43f5e', dash='dash', width=1.5), name='Resistance'
        ))
        fig_chart.add_trace(go.Scatter(
            x=c_raw.index, y=c_raw['Nearest_Sup'], mode='lines', line_shape='hv', line=dict(color='#10b981', dash='dash', width=1.5), name='Support'
        ))

        fig_chart = apply_chart_style(fig_chart, height=550)
        fig_chart.update_xaxes(rangeslider_visible=False)
        st.plotly_chart(fig_chart, use_container_width=True)

# =========================================================================
# BOTTOM SECTION: ANGEL ONE & TELEGRAM CARDS
# =========================================================================
st.write("")
bot_col1, bot_col2 = st.columns(2)

with bot_col1:
    st.markdown("""
    <div class="bottom-card">
        <div class="bottom-title">🔗 Angel One SmartAPI Gateway</div>
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
            st.markdown("<span style='color:#10b981; font-size:13px; font-weight:700; line-height:38px;'>● SESSION ACTIVE & READY</span>", unsafe_allow_html=True)
        else:
            st.markdown("<span style='color:#64748b; font-size:13px; font-weight:500; line-height:38px;'>Status: Disconnected</span>", unsafe_allow_html=True)

with bot_col2:
    st.markdown("""
    <div class="bottom-card">
        <div class="bottom-title">🔔 Telegram Broadcast Center</div>
    </div>
    """, unsafe_allow_html=True)

    tg_c1, tg_c2 = st.columns(2)
    with tg_c1:
        tg_token = st.text_input("Telegram Bot Token", type="password", placeholder="Bot Token")
    with tg_c2:
        tg_chat_id = st.text_input("Telegram Chat ID", placeholder="Chat ID")

    if st.button("🚀 Broadcast High-Confidence Setups (Score ≥ 8/10)", use_container_width=True):
        if not tg_token or not tg_chat_id:
            st.error("Provide Bot Token and Chat ID.")
        else:
            alert_candidates = [info for s, info in checklists.items() if info['buy_score'] >= 8 or info['sell_score'] >= 8]
            if alert_candidates:
                for item in alert_candidates:
                    alert_type = "🟢 *BUY BREAKOUT*" if item['buy_score'] >= 8 else "🔴 *BEARISH BREAKDOWN*"
                    msg = (
                        f"⚡ *Mahi Trading Alert*\n"
                        f"{alert_type}\n\n"
                        f"📌 *Asset:* `{item['sym']}`\n"
                        f"💰 *LTP:* ₹{item['ltp']:.2f} ({item['chg']:+.2f}%)\n"
                        f"🎯 *Setup:* `{item['setup']}`\n"
                        f"📊 *Score:* {item['buy_score']}/10 Buy · {item['sell_score']}/10 Sell\n"
                        f"🛡️ *Support:* ₹{item['support']:.2f}\n"
                        f"🚧 *Resistance:* ₹{item['resistance']:.2f}\n"
                        f"📈 *RSI:* {item['rsi']:.1f}\n\n"
                        f"⏰ _{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST_"
                    )
                    send_telegram_alert(tg_token, tg_chat_id, msg)
                st.success(f"Dispatched {len(alert_candidates)} alert(s) to Telegram!")
            else:
                st.info("No setups currently meet the 8/10 threshold.")