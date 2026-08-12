"""
US Stock Screener — "Down Yesterday, Up Today" scanner
--------------------------------------------------------
Scans all NASDAQ + NYSE common stocks using Yahoo Finance data (via yfinance),
filters for stocks that had a red candle yesterday and are up by at least a
chosen % today with a strong close, with a minimum market cap filter. Charts
are pulled from Finviz (includes moving averages) rather than plotted here.

Run locally:
    streamlit run app.py

Deploy for phone use: push this folder to a GitHub repo and deploy for free
on https://share.streamlit.io (Streamlit Community Cloud). Open the resulting
URL in your Android browser and use "Add to Home Screen" for an app-like icon.
"""

import time
import io
import json
import os
from datetime import datetime, timezone
import requests
import pandas as pd
import numpy as np
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="Reversal Scanner", layout="wide", page_icon="📈")

# ----------------------------------------------------------------------------
# Persistence: save the last scan to disk so a page refresh / new visit
# doesn't force a full market re-scan.
# ----------------------------------------------------------------------------
RESULTS_FILE = "last_scan.json"


def save_results(df, rules):
    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "rules": rules,
        "results": df.to_dict(orient="records"),
    }
    with open(RESULTS_FILE, "w") as f:
        json.dump(payload, f)


def load_results():
    if not os.path.exists(RESULTS_FILE):
        return None, None, None
    try:
        with open(RESULTS_FILE, "r") as f:
            payload = json.load(f)
        df = pd.DataFrame(payload["results"])
        return df, payload["saved_at"], payload["rules"]
    except Exception:
        return None, None, None

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


def scan_prices(tickers, min_today_pct, require_red_yesterday, min_close_position, batch_size=150, progress_cb=None):
    """Batch-download 5 days of daily bars and find tickers where:
    - yesterday's close < yesterday's open (a red candle), and
    - today's close is up >= min_today_pct% vs. yesterday's close, and
    - today's close sits at least min_close_position (0-1) of the way up
      today's low-to-high range, so a weak/wicky bullish candle doesn't count.
    Returns a DataFrame of matches with raw % moves."""
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
                    highs = sub["High"].dropna()
                    lows = sub["Low"].dropna()
                    vols = sub["Volume"].dropna()
                    if len(closes) < 2 or len(opens) < 2 or len(highs) < 1 or len(lows) < 1:
                        continue
                    c_today, c_yesterday = closes.iloc[-1], closes.iloc[-2]
                    o_yesterday = opens.iloc[-2]
                    h_today, l_today = highs.iloc[-1], lows.iloc[-1]
                    if pd.isna(c_today) or pd.isna(c_yesterday) or pd.isna(o_yesterday) or pd.isna(h_today) or pd.isna(l_today):
                        continue

                    # "Down yesterday" = a red candle: yesterday's close < yesterday's open
                    yesterday_pct = (c_yesterday - o_yesterday) / o_yesterday * 100
                    # "Up today" = today's close vs. yesterday's close (standard daily % change)
                    today_pct = (c_today - c_yesterday) / c_yesterday * 100

                    yesterday_ok = (c_yesterday < o_yesterday) if require_red_yesterday else True
                    today_ok = today_pct >= min_today_pct

                    # Reject weak bullish candles: close must sit high up in
                    # today's low-to-high range, not just barely above the low.
                    today_range = h_today - l_today
                    if today_range > 0:
                        close_position = (c_today - l_today) / today_range
                    else:
                        close_position = 1.0  # no range (open=high=low=close): treat as fully at the top
                    close_position_ok = close_position >= min_close_position

                    if yesterday_ok and today_ok and close_position_ok:
                        matches.append(
                            {
                                "symbol": t,
                                "yesterday_pct": round(yesterday_pct, 2),
                                "today_pct": round(today_pct, 2),
                                "close_position_pct": round(close_position * 100, 1),
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


def finviz_urls(symbol):
    """Finviz's chart.ashx image endpoint renders a daily candlestick chart
    with moving averages already built in — no need to plot it ourselves.
    quote.ashx is the full interactive page, for tapping through for more
    detail. Both are unofficial/undocumented Finviz endpoints commonly used
    this way; if Finviz ever changes or blocks them, this is the one place
    to swap back to a self-plotted chart."""
    chart_img = f"https://finviz.com/chart.ashx?t={symbol}&ty=c&ta=1&p=d&s=l"
    quote_page = f"https://finviz.com/quote.ashx?t={symbol}"
    return chart_img, quote_page

# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
st.title("📈 Reversal Scanner")
st.caption("Down yesterday, up today — scanned from Yahoo Finance data (best used after market close).")

with st.sidebar:
    st.header("Scan rules")
    require_red_yesterday = st.checkbox("Yesterday must be down (red)", value=True)
    min_today_pct = st.number_input("Minimum % up today", min_value=0.0, value=2.0, step=0.5)
    min_close_position_pct = st.number_input(
        "Min close position within today's range (%)",
        min_value=0.0, max_value=100.0, value=60.0, step=5.0,
        help="Today's close must sit at least this far up today's low-to-high range, "
             "so a weak bullish candle with a long upper wick doesn't count.",
    )
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

# Load the last saved scan from disk on first load of this session
# (e.g. after a page refresh) instead of forcing a re-scan.
if "results" not in st.session_state:
    saved_df, saved_at, saved_rules = load_results()
    st.session_state.results = saved_df
    st.session_state.saved_at = saved_at
    st.session_state.saved_rules = saved_rules

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
        min_close_position=min_close_position_pct / 100.0,
        progress_cb=lambda p: progress.progress(p),
    )
    progress.empty()

    rules_used = {
        "require_red_yesterday": require_red_yesterday,
        "min_today_pct": min_today_pct,
        "min_close_position_pct": min_close_position_pct,
        "min_market_cap_b": min_market_cap_b,
        "exchanges": exchanges,
    }

    if matches.empty:
        st.session_state.results = pd.DataFrame()
        save_results(pd.DataFrame(), rules_used)
    else:
        with st.spinner(f"Checking market cap for {len(matches)} candidates..."):
            caps = get_market_caps(matches["symbol"].tolist())
        matches["market_cap"] = matches["symbol"].map(caps)
        matches = matches[matches["market_cap"].fillna(0) >= min_market_cap_b * 1e9]
        matches = matches.merge(universe[["symbol", "name", "exchange"]], on="symbol", how="left")
        matches["market_cap_b"] = (matches["market_cap"] / 1e9).round(2)
        matches = matches.sort_values("today_pct", ascending=False).reset_index(drop=True)
        st.session_state.results = matches
        save_results(matches, rules_used)

    st.session_state.saved_at = datetime.now(timezone.utc).isoformat()
    st.session_state.saved_rules = rules_used

# ----------------------------------------------------------------------------
# Results
# ----------------------------------------------------------------------------
results = st.session_state.results

if results is not None and not results.empty and st.session_state.get("saved_at"):
    saved_dt = datetime.fromisoformat(st.session_state.saved_at)
    local_str = saved_dt.strftime("%Y-%m-%d %H:%M UTC")
    st.caption(f"🕒 Showing saved results from **{local_str}**. Tap **Run scan** to refresh.")

if results is not None:
    if results.empty:
        st.info("No matches found with the current rules.")
    else:
        st.success(f"Found {len(results)} match(es).")
        display_cols = ["symbol", "name", "exchange", "yesterday_pct", "today_pct", "close_position_pct", "market_cap_b", "last_close", "volume"]
        display_df = results[display_cols].rename(
            columns={
                "symbol": "Ticker",
                "name": "Company",
                "exchange": "Exchange",
                "yesterday_pct": "Yesterday % (Open→Close)",
                "today_pct": "Today %",
                "close_position_pct": "Close Position %",
                "market_cap_b": "Mkt Cap ($B)",
                "last_close": "Last Close",
                "volume": "Volume",
            }
        )
        st.dataframe(display_df, use_container_width=True, hide_index=True)

        st.subheader(f"Charts ({len(results)})")
        st.caption("Charts are from Finviz (includes moving averages). Tap 'Open on Finviz' for the full interactive version.")

        for _, row in results.sort_values("market_cap_b", ascending=False).iterrows():
            symbol = row["symbol"]
            name = row.get("name", "")
            header = f"{symbol} — {name}" if isinstance(name, str) and name else symbol
            st.markdown(
                f"**{header}**  \n"
                f"Yesterday: {row['yesterday_pct']:+.2f}% · Today: {row['today_pct']:+.2f}% · "
                f"Mkt Cap: ${row.get('market_cap_b', float('nan')):.1f}B · Last: ${row['last_close']:.2f}"
            )

            chart_img_url, quote_page_url = finviz_urls(symbol)
            st.image(chart_img_url, use_container_width=True)
            st.link_button(f"Open {symbol} on Finviz ↗", quote_page_url, use_container_width=True)
            st.divider()
else:
    st.info("Set your rules on the left and tap **Run scan**.")
