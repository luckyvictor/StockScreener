"""
US Stock Screener — large-cap universe + two pattern scanners
----------------------------------------------------------------
Two-layer design:
1. Large-cap universe: a list of NASDAQ + NYSE tickers filtered ONLY by
   market cap (default > $10B), fetched via Yahoo Finance's own bulk
   screener and saved to disk. You refresh this manually, whenever you
   want (monthly is plenty — market cap doesn't move fast).
2. Pattern scanners (Daily Reversal, 1H EMA Crossover) read that saved
   list as-is and only apply their own price-action rules — they do NOT
   re-check market cap. If no list has been saved yet, a scanner will
   build one automatically (using the current market cap threshold shown
   in the universe panel) before scanning.

Each scanner's results are saved separately and reload automatically on
refresh, so nothing needs to be re-run just to look at your last results.
Charts are pulled from Finviz rather than plotted here.

Run locally:
    streamlit run app.py

Deploy for phone use: push this folder to a GitHub repo and deploy for free
on https://share.streamlit.io (Streamlit Community Cloud). Open the resulting
URL in your Android browser and use "Add to Home Screen" for an app-like icon.
"""

import io
import json
import os
import base64
from datetime import datetime, timezone
import requests
import pandas as pd
import numpy as np
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="Reversal Scanner", layout="wide", page_icon="📈")

ALL_EXCHANGES = ["NASDAQ", "NYSE", "NYSE American"]

# ----------------------------------------------------------------------------
# Persistence: scan results (per scanner) and the large-cap universe.
#
# Local files (in the app's own storage) are read/written first — fast, and
# work fine for normal use. But that storage resets if the app's container
# restarts (e.g. after being idle a while on Streamlit Community Cloud's
# free tier). To make saves genuinely permanent, every save also pushes to
# a "data/" folder in your GitHub repo (if configured via secrets — see
# README), and every load falls back to fetching from GitHub if the local
# file is missing. This costs nothing (GitHub's API is free at this scale)
# and doubles as free version history for your saved lists/results.
# ----------------------------------------------------------------------------
RESULTS_FILE_DAILY = "last_scan_daily.json"
RESULTS_FILE_EMA = "last_scan_ema.json"
LARGE_CAP_FILE = "large_cap_universe.json"
GITHUB_DATA_DIR = "data"


def get_github_config():
    """Reads GITHUB_TOKEN / GITHUB_REPO / GITHUB_BRANCH from Streamlit
    secrets. Returns (None, None, None) if not configured — GitHub sync is
    entirely optional and the app works fine without it (just without the
    permanence)."""
    try:
        token = st.secrets.get("GITHUB_TOKEN")
        repo = st.secrets.get("GITHUB_REPO")
        branch = st.secrets.get("GITHUB_BRANCH", "main")
    except Exception:
        return None, None, None
    if not token or not repo:
        return None, None, None
    return token, repo, branch


def github_configured():
    token, repo, _ = get_github_config()
    return bool(token and repo)


def github_get_file(path_in_repo):
    """Fetch a file's text content from the repo. Returns (content, sha) or
    (None, None) if not found/not configured/any error."""
    token, repo, branch = get_github_config()
    if not token:
        return None, None
    try:
        url = f"https://api.github.com/repos/{repo}/contents/{path_in_repo}"
        r = requests.get(
            url, params={"ref": branch},
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"},
            timeout=15,
        )
        if r.status_code != 200:
            return None, None
        data = r.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        return content, data.get("sha")
    except Exception:
        return None, None


def github_put_file(path_in_repo, content_str, message):
    """Create/update a file in the repo. Returns True on success, False on
    any failure (including not being configured) — callers should treat
    this as best-effort and never let a GitHub failure block a local save."""
    token, repo, branch = get_github_config()
    if not token:
        return False
    try:
        _, sha = github_get_file(path_in_repo)
        url = f"https://api.github.com/repos/{repo}/contents/{path_in_repo}"
        payload = {
            "message": message,
            "content": base64.b64encode(content_str.encode("utf-8")).decode("utf-8"),
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha
        r = requests.put(
            url, json=payload,
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"},
            timeout=15,
        )
        return r.status_code in (200, 201)
    except Exception:
        return False


def save_results(path, df, rules):
    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "rules": rules,
        "results": df.to_dict(orient="records"),
    }
    json_str = json.dumps(payload)
    with open(path, "w") as f:
        f.write(json_str)
    return github_put_file(f"{GITHUB_DATA_DIR}/{path}", json_str, f"Update {path}")


def load_results(path):
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                payload = json.load(f)
            return pd.DataFrame(payload["results"]), payload["saved_at"], payload["rules"]
        except Exception:
            pass
    # Local file missing (e.g. after a container restart) — try GitHub.
    content, _ = github_get_file(f"{GITHUB_DATA_DIR}/{path}")
    if content:
        try:
            payload = json.loads(content)
            with open(path, "w") as f:  # cache locally for next time
                f.write(content)
            return pd.DataFrame(payload["results"]), payload["saved_at"], payload["rules"]
        except Exception:
            pass
    return None, None, None


def save_large_cap_list(df, meta):
    payload = {"meta": meta, "rows": df.to_dict(orient="records")}
    json_str = json.dumps(payload)
    with open(LARGE_CAP_FILE, "w") as f:
        f.write(json_str)
    return github_put_file(f"{GITHUB_DATA_DIR}/{LARGE_CAP_FILE}", json_str, "Update large_cap_universe.json")


def load_large_cap_list():
    if os.path.exists(LARGE_CAP_FILE):
        try:
            with open(LARGE_CAP_FILE, "r") as f:
                payload = json.load(f)
            return pd.DataFrame(payload["rows"]), payload["meta"]
        except Exception:
            pass
    content, _ = github_get_file(f"{GITHUB_DATA_DIR}/{LARGE_CAP_FILE}")
    if content:
        try:
            payload = json.loads(content)
            with open(LARGE_CAP_FILE, "w") as f:
                f.write(content)
            return pd.DataFrame(payload["rows"]), payload["meta"]
        except Exception:
            pass
    return None, None


def chunk(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


# ----------------------------------------------------------------------------
# Large-cap universe source #1 (primary): Yahoo's own bulk screener via
# yfinance. Fast — a handful of API calls instead of thousands. This is an
# unofficial/reverse-engineered part of yfinance (yf.screen / EquityQuery
# talk to Yahoo's internal screener API) — not a stable documented API.
# ----------------------------------------------------------------------------
YF_EXCHANGE_MAP = {
    "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ",  # Nasdaq Global Select / Global Market / Capital Market
    "NYQ": "NYSE",
    "ASE": "NYSE American",
}


def fetch_large_cap_via_screener(min_cap_b):
    """Query Yahoo's screener for US equities with market cap > min_cap_b
    (in $B) across ALL_EXCHANGES. Raises on failure/empty result."""
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
            if exch_label is None:
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


# ----------------------------------------------------------------------------
# Large-cap universe source #2 (fallback): full NASDAQ+NYSE symbol list from
# the Nasdaq Trader FTP feed, then a per-ticker market cap check. Only used
# if the Yahoo screener call fails — much slower, but keeps the app working.
# ----------------------------------------------------------------------------
NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def get_full_symbol_list():
    """Full NASDAQ + NYSE common-stock list, no market cap filter."""
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
    return out.sort_values("symbol").reset_index(drop=True)


@st.cache_data(ttl=60 * 30, show_spinner=False)
def get_market_caps(symbols):
    out = {}
    for s in symbols:
        try:
            fi = yf.Ticker(s).fast_info
            out[s] = fi.get("market_cap") or fi.get("marketCap")
        except Exception:
            out[s] = None
    return out


def build_large_cap_universe(min_cap_b):
    """Try the fast Yahoo-screener path; fall back to the slow full-list +
    per-ticker market cap check if that fails. Always returns a DataFrame
    with symbol/name/exchange/market_cap/market_cap_b."""
    try:
        return fetch_large_cap_via_screener(min_cap_b), True
    except Exception as e:
        st.warning(f"Yahoo screener unavailable ({e}) — falling back to the full market list + per-ticker market cap check (much slower).")
        full = get_full_symbol_list()
        symbols = full["symbol"].tolist()
        caps = {}
        prog = st.progress(0.0)
        for i, batch in enumerate(chunk(symbols, 50)):
            batch_caps = get_market_caps(batch)
            caps.update(batch_caps)
            prog.progress(min((i + 1) * 50 / len(symbols), 1.0))
        prog.empty()
        full["market_cap"] = full["symbol"].map(caps)
        full = full[full["market_cap"].fillna(0) >= min_cap_b * 1e9].copy()
        full["market_cap_b"] = (full["market_cap"] / 1e9).round(2)
        full = full.sort_values("market_cap", ascending=False).reset_index(drop=True)
        return full, False


def ensure_universe_loaded():
    """Returns the currently saved/loaded large-cap universe DataFrame.
    If none is saved yet, builds and saves one automatically using the
    market cap threshold currently set in the universe panel."""
    if st.session_state.get("universe_df") is not None and not st.session_state.universe_df.empty:
        return st.session_state.universe_df, st.session_state.universe_meta

    min_cap_b = st.session_state.get("u_mcap", 10.0)
    st.info(f"No saved large-cap list yet — building one now (market cap > ${min_cap_b:.0f}B). This only happens once; future scans will reuse the saved list.")
    with st.spinner("Fetching large-cap list..."):
        df, via_screener = build_large_cap_universe(min_cap_b)
    meta = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "min_cap_b": min_cap_b,
        "exchanges": ALL_EXCHANGES,
        "count": len(df),
        "via_screener": via_screener,
    }
    synced = save_large_cap_list(df, meta)
    st.session_state.universe_df = df
    st.session_state.universe_meta = meta
    st.success(f"Built and saved — {len(df):,} tickers." + (" ☁️ Backed up to GitHub." if synced else ""))
    return df, meta


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
# Scanner 1: Daily Reversal (pattern rules only — no market cap check here)
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
# Scanner 2: 1H EMA10 > EMA90 with a strong close — pattern rules only
# ----------------------------------------------------------------------------
def scan_ema_trend_strong_candle(tickers, lookback_candles, min_close_position, batch_size=100, progress_cb=None):
    """Batch-download hourly bars and find tickers where, on some candle
    within the last `lookback_candles` hourly bars:
    - the 10-period EMA is above the 90-period EMA (short-term uptrend), AND
    - that candle's close sits at least min_close_position (0-1) of the way
      up its own low-to-high range (a strong close, not just drifting up).
    Reports the most recent such candle."""
    matches = []
    batches = list(chunk(tickers, batch_size))
    total = len(batches)
    min_bars_needed = 90 + 20  # enough history for a stable EMA90 plus a little buffer

    for i, batch in enumerate(batches):
        try:
            data = yf.download(
                tickers=" ".join(batch), period="2mo", interval="60m",
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
                    highs = sub["High"].dropna()
                    lows = sub["Low"].dropna()
                    if len(closes) < min_bars_needed or len(highs) < min_bars_needed or len(lows) < min_bars_needed:
                        continue

                    ema10 = closes.ewm(span=10, adjust=False).mean()
                    ema90 = closes.ewm(span=90, adjust=False).mean()
                    n = len(closes)
                    lookback = min(lookback_candles, n)
                    start = n - lookback

                    match_idx = None
                    match_close_pos = None
                    for idx in range(n - 1, start - 1, -1):
                        if ema10.iloc[idx] <= ema90.iloc[idx]:
                            continue
                        candle_range = highs.iloc[idx] - lows.iloc[idx]
                        close_pos = (closes.iloc[idx] - lows.iloc[idx]) / candle_range if candle_range > 0 else 1.0
                        if close_pos >= min_close_position:
                            match_idx = idx
                            match_close_pos = close_pos
                            break

                    if match_idx is not None:
                        bars_ago = (n - 1) - match_idx
                        matches.append({
                            "symbol": t,
                            "bars_ago": int(bars_ago),
                            "match_time": str(closes.index[match_idx]),
                            "close_position_pct": round(match_close_pos * 100, 1),
                            "ema10_last": round(float(ema10.iloc[-1]), 2),
                            "ema90_last": round(float(ema90.iloc[-1]), 2),
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


def finalize_matches(matches, universe_df, sort_col, sort_asc):
    """Attach name/exchange/market cap from the universe list (already
    known — no re-checking) and sort."""
    matches = matches.merge(
        universe_df[["symbol", "name", "exchange", "market_cap", "market_cap_b"]],
        on="symbol", how="left",
    )
    return matches.sort_values(sort_col, ascending=sort_asc).reset_index(drop=True)


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
st.title("📈 Reversal Scanner")
st.caption("Scanned from Yahoo Finance data — best used after market close for the daily scanner.")

# ----------------------------------------------------------------------------
# Large-cap universe: the ONLY filter here is market cap. Saved to disk;
# refresh manually whenever you want (monthly is plenty).
# ----------------------------------------------------------------------------
if "universe_df" not in st.session_state:
    df0, meta0 = load_large_cap_list()
    st.session_state.universe_df = df0
    st.session_state.universe_meta = meta0

with st.expander("🏢 Large-cap universe", expanded=(st.session_state.universe_df is None)):
    st.caption("The ticker list used by both scanners below. Filtered ONLY by market cap — refresh this occasionally (e.g. monthly), not every time you scan.")
    if github_configured():
        st.caption("☁️ GitHub backup is configured — saves here are permanent.")
    else:
        st.caption("⚠️ GitHub backup not configured — saves only live in this app's temporary storage. See README to set it up.")
    u1, u2 = st.columns([3, 1.3])
    with u1:
        universe_min_cap_b = st.number_input("Market cap threshold ($B)", min_value=0.0, value=10.0, step=1.0, key="u_mcap")
    with u2:
        st.write("")  # vertical spacer to align button with the input
        refresh_clicked = st.button("🔄 Refresh list", use_container_width=True)

    if refresh_clicked:
        with st.spinner("Fetching large-cap list..."):
            df, via_screener = build_large_cap_universe(universe_min_cap_b)
        meta = {
            "built_at": datetime.now(timezone.utc).isoformat(),
            "min_cap_b": universe_min_cap_b,
            "exchanges": ALL_EXCHANGES,
            "count": len(df),
            "via_screener": via_screener,
        }
        synced = save_large_cap_list(df, meta)
        st.session_state.universe_df = df
        st.session_state.universe_meta = meta
        st.success(f"Refreshed — {len(df):,} tickers saved." + (" ☁️ Backed up to GitHub." if synced else ""))

    if st.session_state.universe_df is not None and not st.session_state.universe_df.empty:
        m = st.session_state.universe_meta or {}
        built_at = m.get("built_at", "unknown")
        try:
            built_at = datetime.fromisoformat(built_at).strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            pass
        st.caption(f"Loaded: **{len(st.session_state.universe_df):,} tickers** · market cap > ${m.get('min_cap_b', '?')}B · built {built_at}")
    else:
        st.caption("No saved list yet — one will be built automatically the first time you run a scan below, using the threshold set here (or tap Refresh list now).")

tab_daily, tab_ema = st.tabs(["📉 Daily Reversal", "📈 1H EMA Crossover"])

# ============================== TAB 1: DAILY ================================
with tab_daily:
    st.markdown("Finds stocks with a **red candle yesterday**, **up X% today**, closing strong — scanned from the large-cap universe above.")

    with st.expander("⚙️ Rules", expanded=True):
        require_red_yesterday = st.checkbox("Yesterday must be down (red)", value=True, key="d_red")
        min_today_pct = st.number_input("Minimum % up today", min_value=0.0, value=2.0, step=0.5, key="d_pct")
        min_close_position_pct = st.number_input(
            "Min close position within today's range (%)", min_value=0.0, max_value=100.0, value=60.0, step=5.0,
            key="d_close_pos",
            help="Today's close must sit at least this far up today's low-to-high range.",
        )

    run_daily = st.button("🔍 Run Daily Reversal scan", type="primary", use_container_width=True, key="run_daily")

    if "daily_results" not in st.session_state:
        df0, saved_at0, rules0 = load_results(RESULTS_FILE_DAILY)
        st.session_state.daily_results = df0
        st.session_state.daily_saved_at = saved_at0

    if run_daily:
        universe, universe_meta = ensure_universe_loaded()
        tickers = universe["symbol"].tolist()
        st.write(f"Scanning **{len(tickers):,}** large-cap tickers for price action...")

        progress = st.progress(0.0)
        matches = scan_daily_reversal(
            tickers, min_today_pct=min_today_pct, require_red_yesterday=require_red_yesterday,
            min_close_position=min_close_position_pct / 100.0, progress_cb=lambda p: progress.progress(p),
        )
        progress.empty()

        rules_used = {
            "require_red_yesterday": require_red_yesterday, "min_today_pct": min_today_pct,
            "min_close_position_pct": min_close_position_pct,
            "universe_min_cap_b": universe_meta.get("min_cap_b") if universe_meta else None,
        }

        if matches.empty:
            st.session_state.daily_results = pd.DataFrame()
            save_results(RESULTS_FILE_DAILY, pd.DataFrame(), rules_used)
        else:
            matches = finalize_matches(matches, universe, sort_col="today_pct", sort_asc=False)
            st.session_state.daily_results = matches
            synced = save_results(RESULTS_FILE_DAILY, matches, rules_used)
            if synced:
                st.caption("☁️ Results backed up to GitHub.")

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
    st.markdown("Finds stocks where, on some candle in the last N hourly candles, the **10 EMA is above the 90 EMA** and that **candle closed strong** (close near the high) — scanned from the large-cap universe above.")

    with st.expander("⚙️ Rules", expanded=True):
        lookback_candles = st.number_input(
            "Must have happened within the last N hourly candles",
            min_value=1, value=15, step=1, key="e_lookback",
        )
        min_ema_close_position_pct = st.number_input(
            "Min close position within that candle's range (%)",
            min_value=0.0, max_value=100.0, value=80.0, step=5.0, key="e_close_pos",
            help="On the matching candle, the close must sit at least this far up that candle's own low-to-high range — a strong close, not just drifting up.",
        )
        st.caption("Uses ~2 months of hourly data so the 90-period EMA has enough history to be meaningful.")

    run_ema = st.button("🔍 Run 1H EMA scan", type="primary", use_container_width=True, key="run_ema")

    if "ema_results" not in st.session_state:
        df0, saved_at0, rules0 = load_results(RESULTS_FILE_EMA)
        st.session_state.ema_results = df0
        st.session_state.ema_saved_at = saved_at0

    if run_ema:
        universe, universe_meta = ensure_universe_loaded()
        tickers = universe["symbol"].tolist()
        st.write(f"Scanning **{len(tickers):,}** large-cap tickers on the 1H chart for EMA10>EMA90 with a strong close (this can take a while — hourly data is heavier than daily)...")

        progress = st.progress(0.0)
        matches = scan_ema_trend_strong_candle(
            tickers, lookback_candles=lookback_candles,
            min_close_position=min_ema_close_position_pct / 100.0,
            progress_cb=lambda p: progress.progress(p),
        )
        progress.empty()

        rules_used = {
            "lookback_candles": lookback_candles,
            "min_close_position_pct": min_ema_close_position_pct,
            "universe_min_cap_b": universe_meta.get("min_cap_b") if universe_meta else None,
        }

        if matches.empty:
            st.session_state.ema_results = pd.DataFrame()
            save_results(RESULTS_FILE_EMA, pd.DataFrame(), rules_used)
        else:
            matches = finalize_matches(matches, universe, sort_col="bars_ago", sort_asc=True)
            st.session_state.ema_results = matches
            synced = save_results(RESULTS_FILE_EMA, matches, rules_used)
            if synced:
                st.caption("☁️ Results backed up to GitHub.")

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
            display_cols = ["symbol", "name", "exchange", "bars_ago", "match_time", "close_position_pct", "ema10_last", "ema90_last", "market_cap_b", "last_close"]
            display_df = results_e[display_cols].rename(columns={
                "symbol": "Ticker", "name": "Company", "exchange": "Exchange",
                "bars_ago": "Candles Ago", "match_time": "Match Time (UTC)",
                "close_position_pct": "Close Position %",
                "ema10_last": "EMA10 (now)", "ema90_last": "EMA90 (now)",
                "market_cap_b": "Mkt Cap ($B)", "last_close": "Last Close",
            })
            st.dataframe(display_df, use_container_width=True, hide_index=True)
            render_charts(results_e.sort_values("market_cap_b", ascending=False), key_prefix="ema")
    else:
        st.info("Set your rules above and tap **Run 1H EMA scan**.")
