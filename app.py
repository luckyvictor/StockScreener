"""
US Stock Screener — two scanners in one app
--------------------------------------------
1. Daily Reversal: red candle yesterday, up X% today with a strong close.
2. 1H EMA Crossover: 90 EMA crosses above 200 EMA (golden cross) on the
   hourly chart within the last N candles.

Both scan all NASDAQ + NYSE common stocks using Yahoo Finance data (via
yfinance) and apply a minimum market cap filter. Charts are pulled from
Finviz rather than plotted here.

Run locally:
    streamlit run app.py

Deploy for phone use: push this folder to a GitHub repo and deploy for free
on https://share.streamlit.io (Streamlit Community Cloud). Open the resulting
URL in your Android browser and use "Add to Home Screen" for an app-like icon.
"""

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
# Persistence: save the last scan of each type to disk so a page refresh /
# new visit doesn't force a re-scan.
# ----------------------------------------------------------------------------
RESULTS_FILE_DAILY = "last_scan_daily.json"
RESULTS_FILE_EMA = "last_scan_ema.json"


def save_results(path, df, rules):
    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "rules": rules,
        "results": df.to_dict(orient="records"),
    }
    with open(path, "w") as f:
        json.dump(payload, f)


def load_results(path):
    if not os.path.exists(path):
        return None, None, None
    try:
        with open(path, "r") as f:
            payload = json.load(f)
        df = pd.DataFrame(payload["results"])
        return df, payload["saved_at"], payload["rules"]
    except Exception:
        return None, None, None


LARGE_CAP_FILE = "large_cap_universe.json"


def save_large_cap_list(df, meta):
    payload = {"meta": meta, "rows": df.to_dict(orient="records")}
    with open(LARGE_CAP_FILE, "w") as f:
        json.dump(payload, f)


def load_large_cap_list():
    if not os.path.exists(LARGE_CAP_FILE):
        return None, None
    try:
        with open(LARGE_CAP_FILE, "r") as f:
            payload = json.load(f)
        df = pd.DataFrame(payload["rows"])
        return df, payload["meta"]
    except Exception:
        return None, None


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

    r = requests.get(NASDAQ_LISTED_URL, timeout=30)
    df = pd.read_csv(io.StringIO(r.text), sep="|")
    df = df[df["Test Issue"] == "N"]
    df = df[df["ETF"] == "N"]
    df = df[~df["Symbol"].str.contains(r"[.$]", regex=True, na=False)]
    df = df.rename(columns={"Symbol": "symbol", "Security Name": "name"})
    df["exchange"] = "NASDAQ"
    frames.append(df[["symbol", "name", "exchange"]])

    r = requests.get(OTHER_LISTED_URL, timeout=30)
    df2 = pd.read_csv(io.StringIO(r.text), sep="|")
    df2 = df2[df2["Test Issue"] == "N"]
    df2 = df2[df2["ETF"] == "N"]
    df2 = df2[df2["Exchange"].isin(["N", "A"])]
    df2 = df2[~df2["ACT Symbol"].str.contains(r"[.$]", regex=True, na=False)]
    df2 = df2.rename(columns={"ACT Symbol": "symbol", "Security Name": "name"})
    df2["exchange"] = df2["Exchange"].map({"N": "NYSE", "A": "NYSE American"})
    frames.append(df2[["symbol", "name", "exchange"]])

    out = pd.concat(frames, ignore_index=True).drop_duplicates(subset="symbol")
    out = out.sort_values("symbol").reset_index(drop=True)
    return out


def chunk(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


# ----------------------------------------------------------------------------
# Fast pre-filter: use Yahoo Finance's own bulk screener (via yfinance) to
# get only large-cap tickers directly, instead of checking market cap for
# every one of ~7,000 tickers individually. This is an unofficial/reverse-
# engineered part of yfinance (yf.screen / EquityQuery talk to Yahoo's
# internal screener API) — not guaranteed stable, but it stays within the
# same Yahoo Finance ecosystem the rest of the app already relies on. If it
# fails or its response fields don't match what's expected, every caller
# falls back automatically to the slower full-market-scan path.
# ----------------------------------------------------------------------------
YF_EXCHANGE_MAP = {
    "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ",  # Nasdaq Global Select / Global Market / Capital Market
    "NYQ": "NYSE",
    "ASE": "NYSE American",
}


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def get_large_cap_universe(min_cap_b, exchanges):
    """Query Yahoo's screener for US equities with market cap > min_cap_b
    (in $B), then keep only the requested exchanges. Raises on any failure
    or empty result so callers can fall back to the full-market path."""
    q = yf.EquityQuery("and", [
        yf.EquityQuery("gt", ["intradaymarketcap", int(min_cap_b * 1e9)]),
        yf.EquityQuery("eq", ["region", "us"]),
    ])

    rows = []
    offset = 0
    size = 250
    total = None
    max_offset = 6000  # safety net against a runaway loop

    while True:
        resp = yf.screen(q, offset=offset, size=size, sortField="intradaymarketcap", sortAsc=False)
        quotes = resp.get("quotes") or []
        if total is None:
            total = resp.get("total", len(quotes))

        for r in quotes:
            exch_label = YF_EXCHANGE_MAP.get(r.get("exchange"))
            if exch_label is None or exch_label not in exchanges:
                continue
            cap = r.get("marketCap") or r.get("intradaymarketcap") or r.get("regularMarketCap")
            symbol = r.get("symbol")
            if not symbol or not cap:
                continue
            rows.append({
                "symbol": symbol,
                "name": r.get("shortName") or r.get("longName") or "",
                "exchange": exch_label,
                "market_cap": cap,
            })

        offset += size
        if not quotes or offset >= (total or 0) or offset >= max_offset:
            break

    if not rows:
        raise RuntimeError("Yahoo screener returned no usable results")

    df = pd.DataFrame(rows).drop_duplicates(subset="symbol")
    df["market_cap_b"] = (df["market_cap"] / 1e9).round(2)
    df = df.sort_values("market_cap", ascending=False).reset_index(drop=True)
    return df


def load_scan_universe(min_cap_b, exchanges, use_screener_key):
    """Universe resolution order for a scan:
    1. The saved/shared large-cap list (session_state, loaded from disk at
       startup) — free, no API call, IF it covers the requested threshold
       and exchanges.
    2. A fresh live call to Yahoo's screener (not persisted).
    3. Full NASDAQ+NYSE fallback.
    Returns (universe_df, market_cap_known: bool)."""
    saved_df = st.session_state.get("universe_df")
    saved_meta = st.session_state.get("universe_meta") or {}

    if saved_df is not None and not saved_df.empty:
        saved_min_cap = saved_meta.get("min_cap_b")
        saved_exch = set(saved_meta.get("exchanges", []))
        if saved_min_cap is not None and saved_min_cap <= min_cap_b and saved_exch.issuperset(set(exchanges)):
            filtered = saved_df[saved_df["exchange"].isin(exchanges) & (saved_df["market_cap_b"] >= min_cap_b)].copy()
            st.success(f"Using saved large-cap list: {len(filtered):,} tickers with market cap ≥ ${min_cap_b:.0f}B (no re-fetch needed).")
            return filtered, True
        else:
            st.info(
                f"Saved large-cap list (≥ ${saved_min_cap}B, built for {sorted(saved_exch)}) doesn't cover this "
                f"scan's threshold/exchanges — fetching fresh for this scan only. Use **Refresh list** above "
                f"to update the saved list to your current settings."
            )

    if st.session_state.get(use_screener_key, True):
        try:
            universe = get_large_cap_universe(min_cap_b, exchanges)
            st.success(f"Pre-filtered to {len(universe):,} tickers with market cap ≥ ${min_cap_b:.0f}B via Yahoo's screener.")
            return universe, True
        except Exception as e:
            st.warning(f"Yahoo screener unavailable ({e}) — falling back to a full market scan (slower).")

    universe = get_universe()
    universe = universe[universe["exchange"].isin(exchanges)].copy()
    return universe, False


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
    with moving averages already built in. theme=dark is a community-used
    but UNCONFIRMED/undocumented parameter for this endpoint — test it after
    deploying; if the background doesn't actually go dark, the endpoint
    likely doesn't support it and this is the one place to change/remove it.
    quote.ashx is the full interactive page, which does officially support a
    dark theme (as your own Finviz account setting)."""
    chart_img = f"https://finviz.com/chart.ashx?t={symbol}&ty=c&ta=1&p=d&s=l&theme=dark"
    quote_page = f"https://finviz.com/quote.ashx?t={symbol}"
    return chart_img, quote_page


# ----------------------------------------------------------------------------
# Scanner 1: Daily Reversal
# ----------------------------------------------------------------------------
def scan_daily_reversal(tickers, min_today_pct, require_red_yesterday, min_close_position, batch_size=150, progress_cb=None):
    """Batch-download 5 days of daily bars and find tickers where:
    - yesterday's close < yesterday's open (a red candle), and
    - today's close is up >= min_today_pct% vs. yesterday's close, and
    - today's close sits at least min_close_position (0-1) of the way up
      today's low-to-high range, so a weak/wicky bullish candle doesn't count.
    """
    matches = []
    batches = list(chunk(tickers, batch_size))
    total = len(batches)

    for i, batch in enumerate(batches):
        try:
            data = yf.download(
                tickers=" ".join(batch), period="5d", interval="1d",
                group_by="ticker", threads=True, progress=False, auto_adjust=False,
            )
        except Exception:
            data = None

        if data is not None and not data.empty:
            for t in batch:
                try:
                    sub = data if len(batch) == 1 else (data[t] if t in data.columns.get_level_values(0) else None)
                    if sub is None:
                        continue
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

                    yesterday_pct = (c_yesterday - o_yesterday) / o_yesterday * 100
                    today_pct = (c_today - c_yesterday) / c_yesterday * 100
                    yesterday_ok = (c_yesterday < o_yesterday) if require_red_yesterday else True
                    today_ok = today_pct >= min_today_pct

                    today_range = h_today - l_today
                    close_position = (c_today - l_today) / today_range if today_range > 0 else 1.0
                    close_position_ok = close_position >= min_close_position

                    if yesterday_ok and today_ok and close_position_ok:
                        matches.append({
                            "symbol": t,
                            "yesterday_pct": round(yesterday_pct, 2),
                            "today_pct": round(today_pct, 2),
                            "close_position_pct": round(close_position * 100, 1),
                            "last_close": round(float(c_today), 2),
                            "volume": int(vols.iloc[-1]) if len(vols) else np.nan,
                        })
                except Exception:
                    continue

        if progress_cb:
            progress_cb((i + 1) / total)

    return pd.DataFrame(matches)


# ----------------------------------------------------------------------------
# Scanner 2: 1H EMA Crossover (golden cross)
# ----------------------------------------------------------------------------
def scan_ema_crossover(tickers, lookback_candles, batch_size=100, progress_cb=None):
    """Batch-download hourly bars and find tickers where the 90-period EMA
    crossed above the 200-period EMA (golden cross) within the last
    `lookback_candles` hourly bars. Reports the most recent such crossover."""
    matches = []
    batches = list(chunk(tickers, batch_size))
    total = len(batches)
    min_bars_needed = 200 + 5  # enough history for a stable EMA200 plus a little buffer

    for i, batch in enumerate(batches):
        try:
            data = yf.download(
                tickers=" ".join(batch), period="3mo", interval="60m",
                group_by="ticker", threads=True, progress=False, auto_adjust=False,
            )
        except Exception:
            data = None

        if data is not None and not data.empty:
            for t in batch:
                try:
                    sub = data if len(batch) == 1 else (data[t] if t in data.columns.get_level_values(0) else None)
                    if sub is None:
                        continue
                    closes = sub["Close"].dropna()
                    if len(closes) < min_bars_needed:
                        continue

                    ema90 = closes.ewm(span=90, adjust=False).mean()
                    ema200 = closes.ewm(span=200, adjust=False).mean()
                    diff = ema90 - ema200
                    n = len(diff)
                    lookback = min(lookback_candles, n - 1)
                    start = n - lookback

                    cross_idx = None
                    for idx in range(n - 1, start - 1, -1):
                        prev, curr = diff.iloc[idx - 1], diff.iloc[idx]
                        if prev < 0 and curr >= 0:
                            cross_idx = idx
                            break

                    if cross_idx is not None:
                        bars_ago = (n - 1) - cross_idx
                        matches.append({
                            "symbol": t,
                            "bars_ago": int(bars_ago),
                            "cross_time": str(closes.index[cross_idx]),
                            "ema90_last": round(float(ema90.iloc[-1]), 2),
                            "ema200_last": round(float(ema200.iloc[-1]), 2),
                            "last_close": round(float(closes.iloc[-1]), 2),
                        })
                except Exception:
                    continue

        if progress_cb:
            progress_cb((i + 1) / total)

    return pd.DataFrame(matches)


# ----------------------------------------------------------------------------
# Shared UI helpers
# ----------------------------------------------------------------------------
def render_charts(results_sorted_df, key_prefix):
    st.subheader(f"Charts ({len(results_sorted_df)})")
    st.caption("Charts are from Finviz (includes moving averages). Tap 'Open on Finviz' for the full interactive version.")
    for _, row in results_sorted_df.iterrows():
        symbol = row["symbol"]
        name = row.get("name", "")
        header = f"{symbol} — {name}" if isinstance(name, str) and name else symbol
        st.markdown(f"**{header}**")
        chart_img_url, quote_page_url = finviz_urls(symbol)
        st.image(chart_img_url, use_container_width=True)
        st.link_button(f"Open {symbol} on Finviz ↗", quote_page_url, use_container_width=True, key=f"{key_prefix}_link_{symbol}")
        st.divider()


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
st.title("📈 Reversal Scanner")
st.caption("Scanned from Yahoo Finance data — best used after market close for the daily scanner.")

# ----------------------------------------------------------------------------
# Shared large-cap universe: fetched via Yahoo's screener and saved to disk,
# so scans don't need to re-fetch it every time. Refresh it manually
# whenever you want (weekly/monthly is plenty, since market cap moves slowly).
# ----------------------------------------------------------------------------
if "universe_df" not in st.session_state:
    df0, meta0 = load_large_cap_list()
    st.session_state.universe_df = df0
    st.session_state.universe_meta = meta0

with st.expander("🏢 Large-cap universe (shared by both scanners)", expanded=(st.session_state.universe_df is None)):
    u1, u2, u3 = st.columns([2, 2, 1.3])
    with u1:
        universe_min_cap_b = st.number_input("Market cap threshold ($B)", min_value=0.0, value=10.0, step=1.0, key="u_mcap")
    with u2:
        universe_exchanges = st.multiselect(
            "Exchanges", ["NASDAQ", "NYSE", "NYSE American"],
            default=["NASDAQ", "NYSE", "NYSE American"], key="u_exch",
        )
    with u3:
        st.write("")  # vertical spacer to align button with inputs
        refresh_clicked = st.button("🔄 Refresh list", use_container_width=True)

    if refresh_clicked:
        try:
            with st.spinner("Fetching large-cap list from Yahoo's screener..."):
                fresh_df = get_large_cap_universe(universe_min_cap_b, universe_exchanges)
            meta = {
                "built_at": datetime.now(timezone.utc).isoformat(),
                "min_cap_b": universe_min_cap_b,
                "exchanges": universe_exchanges,
                "count": len(fresh_df),
            }
            save_large_cap_list(fresh_df, meta)
            st.session_state.universe_df = fresh_df
            st.session_state.universe_meta = meta
            st.success(f"Refreshed — {len(fresh_df):,} tickers saved.")
        except Exception as e:
            st.error(f"Couldn't refresh the list ({e}). The previously saved list (if any) is unchanged.")

    if st.session_state.universe_df is not None and not st.session_state.universe_df.empty:
        m = st.session_state.universe_meta or {}
        built_at = m.get("built_at", "unknown")
        try:
            built_at = datetime.fromisoformat(built_at).strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            pass
        st.caption(f"Loaded: **{len(st.session_state.universe_df):,} tickers** · cap ≥ ${m.get('min_cap_b', '?')}B · exchanges: {', '.join(m.get('exchanges', []))} · built {built_at}")
    else:
        st.caption("No saved list yet. Set your threshold above and tap **Refresh list** — this uses Yahoo's screener and takes only a few seconds.")

tab_daily, tab_ema = st.tabs(["📉 Daily Reversal", "📈 1H EMA Crossover"])

# ============================== TAB 1: DAILY ================================
with tab_daily:
    st.markdown("Finds stocks with a **red candle yesterday**, **up X% today**, closing strong.")

    with st.expander("⚙️ Rules", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            require_red_yesterday = st.checkbox("Yesterday must be down (red)", value=True, key="d_red")
            min_today_pct = st.number_input("Minimum % up today", min_value=0.0, value=2.0, step=0.5, key="d_pct")
            min_close_position_pct = st.number_input(
                "Min close position within today's range (%)", min_value=0.0, max_value=100.0, value=60.0, step=5.0,
                key="d_close_pos",
                help="Today's close must sit at least this far up today's low-to-high range.",
            )
        with c2:
            min_market_cap_b_d = st.number_input("Minimum market cap ($B)", min_value=0.0, value=10.0, step=1.0, key="d_mcap")
            exchanges_d = st.multiselect(
                "Exchanges", ["NASDAQ", "NYSE", "NYSE American"],
                default=["NASDAQ", "NYSE", "NYSE American"], key="d_exch",
            )
            st.checkbox(
                "Pre-filter market cap via Yahoo's screener (fast, recommended)",
                value=True, key="d_use_screener",
                help="Fetches only large-cap tickers upfront instead of checking market cap "
                     "for the whole market. Falls back automatically if unavailable.",
            )

    run_daily = st.button("🔍 Run Daily Reversal scan", type="primary", use_container_width=True, key="run_daily")

    if "daily_results" not in st.session_state:
        df0, saved_at0, rules0 = load_results(RESULTS_FILE_DAILY)
        st.session_state.daily_results = df0
        st.session_state.daily_saved_at = saved_at0

    if run_daily:
        with st.spinner("Loading ticker universe..."):
            universe, used_screener = load_scan_universe(min_market_cap_b_d, exchanges_d, "d_use_screener")
        tickers = universe["symbol"].tolist()
        st.write(f"Scanning **{len(tickers):,}** tickers for price action...")

        progress = st.progress(0.0)
        matches = scan_daily_reversal(
            tickers, min_today_pct=min_today_pct, require_red_yesterday=require_red_yesterday,
            min_close_position=min_close_position_pct / 100.0, progress_cb=lambda p: progress.progress(p),
        )
        progress.empty()

        rules_used = {
            "require_red_yesterday": require_red_yesterday, "min_today_pct": min_today_pct,
            "min_close_position_pct": min_close_position_pct, "min_market_cap_b": min_market_cap_b_d,
            "exchanges": exchanges_d, "used_screener": used_screener,
        }

        if matches.empty:
            st.session_state.daily_results = pd.DataFrame()
            save_results(RESULTS_FILE_DAILY, pd.DataFrame(), rules_used)
        else:
            if used_screener:
                # Market cap is already known from the screener pre-filter — no extra lookups needed.
                matches = matches.merge(universe[["symbol", "name", "exchange", "market_cap", "market_cap_b"]], on="symbol", how="left")
            else:
                with st.spinner(f"Checking market cap for {len(matches)} candidates..."):
                    caps = get_market_caps(matches["symbol"].tolist())
                matches["market_cap"] = matches["symbol"].map(caps)
                matches = matches[matches["market_cap"].fillna(0) >= min_market_cap_b_d * 1e9]
                matches = matches.merge(universe[["symbol", "name", "exchange"]], on="symbol", how="left")
                matches["market_cap_b"] = (matches["market_cap"] / 1e9).round(2)
            matches = matches.sort_values("today_pct", ascending=False).reset_index(drop=True)
            st.session_state.daily_results = matches
            save_results(RESULTS_FILE_DAILY, matches, rules_used)

        st.session_state.daily_saved_at = datetime.now(timezone.utc).isoformat()

    results_d = st.session_state.daily_results

    if results_d is not None and not results_d.empty and st.session_state.get("daily_saved_at"):
        saved_dt = datetime.fromisoformat(st.session_state.daily_saved_at)
        st.caption(f"🕒 Showing saved results from **{saved_dt.strftime('%Y-%m-%d %H:%M UTC')}**. Tap **Run scan** to refresh.")

    if results_d is not None:
        if results_d.empty:
            st.info("No matches found with the current rules.")
        else:
            st.success(f"Found {len(results_d)} match(es).")
            display_cols = ["symbol", "name", "exchange", "yesterday_pct", "today_pct", "close_position_pct", "market_cap_b", "last_close", "volume"]
            display_df = results_d[display_cols].rename(columns={
                "symbol": "Ticker", "name": "Company", "exchange": "Exchange",
                "yesterday_pct": "Yesterday % (Open→Close)", "today_pct": "Today %",
                "close_position_pct": "Close Position %", "market_cap_b": "Mkt Cap ($B)",
                "last_close": "Last Close", "volume": "Volume",
            })
            st.dataframe(display_df, use_container_width=True, hide_index=True)
            render_charts(results_d.sort_values("market_cap_b", ascending=False), key_prefix="daily")
    else:
        st.info("Set your rules above and tap **Run Daily Reversal scan**.")

# ============================== TAB 2: 1H EMA ================================
with tab_ema:
    st.markdown("Finds stocks where the **90 EMA crossed above the 200 EMA** (golden cross) on the **1-hour chart**, within the last N candles.")

    with st.expander("⚙️ Rules", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            lookback_candles = st.number_input(
                "Crossover must have happened within the last N hourly candles",
                min_value=1, value=15, step=1, key="e_lookback",
            )
        with c2:
            min_market_cap_b_e = st.number_input("Minimum market cap ($B)", min_value=0.0, value=10.0, step=1.0, key="e_mcap")
            exchanges_e = st.multiselect(
                "Exchanges", ["NASDAQ", "NYSE", "NYSE American"],
                default=["NASDAQ", "NYSE", "NYSE American"], key="e_exch",
            )
            st.checkbox(
                "Pre-filter market cap via Yahoo's screener (fast, recommended)",
                value=True, key="e_use_screener",
                help="Fetches only large-cap tickers upfront instead of pulling hourly data "
                     "for the whole market — this matters a lot here since intraday data is heavy. "
                     "Falls back automatically if unavailable.",
            )
        st.caption("Uses ~3 months of hourly data so the 200-period EMA has enough history to be meaningful.")

    run_ema = st.button("🔍 Run 1H EMA Crossover scan", type="primary", use_container_width=True, key="run_ema")

    if "ema_results" not in st.session_state:
        df0, saved_at0, rules0 = load_results(RESULTS_FILE_EMA)
        st.session_state.ema_results = df0
        st.session_state.ema_saved_at = saved_at0

    if run_ema:
        with st.spinner("Loading ticker universe..."):
            universe, used_screener = load_scan_universe(min_market_cap_b_e, exchanges_e, "e_use_screener")
        tickers = universe["symbol"].tolist()
        st.write(f"Scanning **{len(tickers):,}** tickers on the 1H chart for EMA crossovers (this can take a while — hourly data is heavier than daily)...")

        progress = st.progress(0.0)
        matches = scan_ema_crossover(
            tickers, lookback_candles=lookback_candles, progress_cb=lambda p: progress.progress(p),
        )
        progress.empty()

        rules_used = {"lookback_candles": lookback_candles, "min_market_cap_b": min_market_cap_b_e, "exchanges": exchanges_e, "used_screener": used_screener}

        if matches.empty:
            st.session_state.ema_results = pd.DataFrame()
            save_results(RESULTS_FILE_EMA, pd.DataFrame(), rules_used)
        else:
            if used_screener:
                matches = matches.merge(universe[["symbol", "name", "exchange", "market_cap", "market_cap_b"]], on="symbol", how="left")
            else:
                with st.spinner(f"Checking market cap for {len(matches)} candidates..."):
                    caps = get_market_caps(matches["symbol"].tolist())
                matches["market_cap"] = matches["symbol"].map(caps)
                matches = matches[matches["market_cap"].fillna(0) >= min_market_cap_b_e * 1e9]
                matches = matches.merge(universe[["symbol", "name", "exchange"]], on="symbol", how="left")
                matches["market_cap_b"] = (matches["market_cap"] / 1e9).round(2)
            matches = matches.sort_values("bars_ago", ascending=True).reset_index(drop=True)
            st.session_state.ema_results = matches
            save_results(RESULTS_FILE_EMA, matches, rules_used)

        st.session_state.ema_saved_at = datetime.now(timezone.utc).isoformat()

    results_e = st.session_state.ema_results

    if results_e is not None and not results_e.empty and st.session_state.get("ema_saved_at"):
        saved_dt = datetime.fromisoformat(st.session_state.ema_saved_at)
        st.caption(f"🕒 Showing saved results from **{saved_dt.strftime('%Y-%m-%d %H:%M UTC')}**. Tap **Run scan** to refresh.")

    if results_e is not None:
        if results_e.empty:
            st.info("No matches found with the current rules.")
        else:
            st.success(f"Found {len(results_e)} match(es).")
            display_cols = ["symbol", "name", "exchange", "bars_ago", "cross_time", "ema90_last", "ema200_last", "market_cap_b", "last_close"]
            display_df = results_e[display_cols].rename(columns={
                "symbol": "Ticker", "name": "Company", "exchange": "Exchange",
                "bars_ago": "Candles Ago", "cross_time": "Crossover Time (UTC)",
                "ema90_last": "EMA90 (now)", "ema200_last": "EMA200 (now)",
                "market_cap_b": "Mkt Cap ($B)", "last_close": "Last Close",
            })
            st.dataframe(display_df, use_container_width=True, hide_index=True)
            render_charts(results_e.sort_values("market_cap_b", ascending=False), key_prefix="ema")
    else:
        st.info("Set your rules above and tap **Run 1H EMA Crossover scan**.")
