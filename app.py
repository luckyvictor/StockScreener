"""
US Stock Screener — "Down Yesterday, Up Today" scanner
--------------------------------------------------------
Scans all NASDAQ + NYSE common stocks using Yahoo Finance data (via yfinance),
filters for stocks that closed DOWN yesterday and are UP by at least a chosen
% today, with an optional minimum market cap filter. Click any result to see
an instant candlestick chart.

Run locally:
    streamlit run app.py

Deploy for phone use: push this folder to a GitHub repo and deploy for free
on https://share.streamlit.io (Streamlit Community Cloud). Open the resulting
URL in your Android browser and use "Add to Home Screen" for an app-like icon.
"""

import time
import io
import requests
import pandas as pd
import numpy as np
import streamlit as st
import yfinance as yf
import plotly.graph_objects as go

st.set_page_config(page_title="Reversal Scanner", layout="wide", page_icon="📈")

# ----------------------------------------------------------------------------
# Universe: full NASDAQ + NYSE common-stock list (from Nasdaq Trader FTP feed)
# ----------------------------------------------------------------------------
NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def get_universe():
    """Return a DataFrame of (symbol, name, exchange) for NASDAQ + NYSE
    common stocks, excluding ETFs, test issues, warrants, units and rights."""
    frames = []

    # NASDAQ-listed
    r = requests.get(NASDAQ_LISTED_URL, timeout=30)
    df = pd.read_csv(io.StringIO(r.text), sep="|")
    df = df[df["Test Issue"] == "N"]
    df = df[df["ETF"] == "N"]
    df = df[~df["Symbol"].str.contains(r"[.$]", regex=True, na=False)]
    df = df.rename(columns={"Symbol": "symbol", "Security Name": "name"})
    df["exchange"] = "NASDAQ"
    frames.append(df[["symbol", "name", "exchange"]])

    # NYSE / NYSE American / other listed (via "otherlisted" feed)
    r = requests.get(OTHER_LISTED_URL, timeout=30)
    df2 = pd.read_csv(io.StringIO(r.text), sep="|")
    df2 = df2[df2["Test Issue"] == "N"]
    df2 = df2[df2["ETF"] == "N"]
    # Keep NYSE (N) and NYSE American (A) only, skip other small venues
    df2 = df2[df2["Exchange"].isin(["N", "A"])]
    df2 = df2[~df2["ACT Symbol"].str.contains(r"[.$]", regex=True, na=False)]
    df2 = df2.rename(columns={"ACT Symbol": "symbol", "Security Name": "name"})
    df2["exchange"] = df2["Exchange"].map({"N": "NYSE", "A": "NYSE American"})
    frames.append(df2[["symbol", "name", "exchange"]])

    out = pd.concat(frames, ignore_index=True).drop_duplicates(subset="symbol")
    out = out.sort_values("symbol").reset_index(drop=True)
    return out


# ----------------------------------------------------------------------------
# Scanning
# ----------------------------------------------------------------------------
def chunk(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


def scan_prices(tickers, min_today_pct, require_red_yesterday, batch_size=150, progress_cb=None):
    """Batch-download 5 days of daily bars and find tickers where yesterday's
    close < prior close (red day) and today's close is up >= min_today_pct%
    versus yesterday's close. Returns a DataFrame of matches with raw % moves."""
    matches = []
    batches = list(chunk(tickers, batch_size))
    total = len(batches)

    for i, batch in enumerate(batches):
        try:
            data = yf.download(
                tickers=" ".join(batch),
                period="5d",
                interval="1d",
                group_by="ticker",
                threads=True,
                progress=False,
                auto_adjust=False,
            )
        except Exception:
            data = None

        if data is not None and not data.empty:
            for t in batch:
                try:
                    if len(batch) == 1:
                        sub = data
                    else:
                        if t not in data.columns.get_level_values(0):
                            continue
                        sub = data[t]
                    closes = sub["Close"].dropna()
                    opens = sub["Open"].dropna()
                    vols = sub["Volume"].dropna()
                    if len(closes) < 2 or len(opens) < 2:
                        continue
                    c_today, c_yesterday = closes.iloc[-1], closes.iloc[-2]
                    o_yesterday = opens.iloc[-2]
                    if pd.isna(c_today) or pd.isna(c_yesterday) or pd.isna(o_yesterday):
                        continue

                    # "Down yesterday" = a red candle: yesterday's close < yesterday's open
                    yesterday_pct = (c_yesterday - o_yesterday) / o_yesterday * 100
                    # "Up today" = today's close vs. yesterday's close (standard daily % change)
                    today_pct = (c_today - c_yesterday) / c_yesterday * 100

                    yesterday_ok = (c_yesterday < o_yesterday) if require_red_yesterday else True
                    today_ok = today_pct >= min_today_pct

                    if yesterday_ok and today_ok:
                        matches.append(
                            {
                                "symbol": t,
                                "yesterday_pct": round(yesterday_pct, 2),
                                "today_pct": round(today_pct, 2),
                                "last_close": round(float(c_today), 2),
                                "volume": int(vols.iloc[-1]) if len(vols) else np.nan,
                            }
                        )
                except Exception:
                    continue

        if progress_cb:
            progress_cb((i + 1) / total)

    return pd.DataFrame(matches)


@st.cache_data(ttl=60 * 30, show_spinner=False)
def get_market_caps(symbols):
    """Fetch market cap for a (small) list of candidate symbols."""
    out = {}
    for s in symbols:
        try:
            fi = yf.Ticker(s).fast_info
            out[s] = fi.get("market_cap") or fi.get("marketCap")
        except Exception:
            out[s] = None
    return out


@st.cache_data(ttl=60 * 15, show_spinner=False)
def get_history(symbol, period="6mo"):
    return yf.download(symbol, period=period, interval="1d", progress=False, auto_adjust=False)


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
st.title("📈 Reversal Scanner")
st.caption("Down yesterday, up today — scanned from Yahoo Finance data (best used after market close).")

with st.sidebar:
    st.header("Scan rules")
    require_red_yesterday = st.checkbox("Yesterday must be down (red)", value=True)
    min_today_pct = st.number_input("Minimum % up today", min_value=0.0, value=2.0, step=0.5)
    min_market_cap_b = st.number_input("Minimum market cap ($B)", min_value=0.0, value=10.0, step=1.0)

    st.header("Universe")
    exchanges = st.multiselect(
        "Exchanges", ["NASDAQ", "NYSE", "NYSE American"], default=["NASDAQ", "NYSE", "NYSE American"]
    )

    st.divider()
    run = st.button("🔍 Run scan", type="primary", use_container_width=True)
    if st.button("Clear cache / refresh ticker list", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

if "results" not in st.session_state:
    st.session_state.results = None

if run:
    with st.spinner("Loading NASDAQ + NYSE ticker list..."):
        universe = get_universe()
    universe = universe[universe["exchange"].isin(exchanges)]
    tickers = universe["symbol"].tolist()
    st.write(f"Scanning **{len(tickers):,}** tickers for price action...")

    progress = st.progress(0.0)
    matches = scan_prices(
        tickers,
        min_today_pct=min_today_pct,
        require_red_yesterday=require_red_yesterday,
        progress_cb=lambda p: progress.progress(p),
    )
    progress.empty()

    if matches.empty:
        st.session_state.results = pd.DataFrame()
    else:
        with st.spinner(f"Checking market cap for {len(matches)} candidates..."):
            caps = get_market_caps(matches["symbol"].tolist())
        matches["market_cap"] = matches["symbol"].map(caps)
        matches = matches[matches["market_cap"].fillna(0) >= min_market_cap_b * 1e9]
        matches = matches.merge(universe[["symbol", "name", "exchange"]], on="symbol", how="left")
        matches["market_cap_b"] = (matches["market_cap"] / 1e9).round(2)
        matches = matches.sort_values("today_pct", ascending=False).reset_index(drop=True)
        st.session_state.results = matches

# ----------------------------------------------------------------------------
# Results
# ----------------------------------------------------------------------------
results = st.session_state.results

if results is not None:
    if results.empty:
        st.info("No matches found with the current rules.")
    else:
        st.success(f"Found {len(results)} match(es).")
        display_cols = ["symbol", "name", "exchange", "yesterday_pct", "today_pct", "market_cap_b", "last_close", "volume"]
        display_df = results[display_cols].rename(
            columns={
                "symbol": "Ticker",
                "name": "Company",
                "exchange": "Exchange",
                "yesterday_pct": "Yesterday % (Open→Close)",
                "today_pct": "Today %",
                "market_cap_b": "Mkt Cap ($B)",
                "last_close": "Last Close",
                "volume": "Volume",
            }
        )
        st.dataframe(display_df, use_container_width=True, hide_index=True)

        st.subheader("Chart")
        selected = st.selectbox("Select a ticker to view its chart", results["symbol"].tolist())

        if selected:
            hist = get_history(selected)
            if isinstance(hist.columns, pd.MultiIndex):
                hist.columns = hist.columns.get_level_values(0)
            fig = go.Figure()
            fig.add_trace(
                go.Candlestick(
                    x=hist.index,
                    open=hist["Open"],
                    high=hist["High"],
                    low=hist["Low"],
                    close=hist["Close"],
                    name=selected,
                )
            )
            fig.update_layout(
                title=f"{selected} — 6 month chart",
                xaxis_rangeslider_visible=False,
                height=550,
                margin=dict(l=10, r=10, t=40, b=10),
            )
            st.plotly_chart(fig, use_container_width=True)

            vol_fig = go.Figure(go.Bar(x=hist.index, y=hist["Volume"]))
            vol_fig.update_layout(title="Volume", height=200, margin=dict(l=10, r=10, t=30, b=10))
            st.plotly_chart(vol_fig, use_container_width=True)
else:
    st.info("Set your rules on the left and tap **Run scan**.")
