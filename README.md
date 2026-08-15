# Reversal Scanner

Two independent scanners, in separate tabs, both scanning NASDAQ + NYSE
common stocks via Yahoo Finance data:

## 📉 Daily Reversal
Finds stocks that:
- had a **red candle yesterday** (close < open), and
- are **up X% today** (default 2%, measured close-to-close), and
- today's close sits in the **top portion of today's candle** (default: at
  least 60% of the way up the low-to-high range), so a weak bullish candle
  with a big upper wick doesn't count.

## 📈 1H EMA Crossover
Finds stocks where the **90-period EMA crossed above the 200-period EMA**
(a "golden cross") on the **1-hour chart**, within the last N candles
(default 15). Uses ~3 months of hourly data. Reports how many candles ago
the crossover happened and the exact timestamp.

Both scanners share a minimum market cap filter (default $10B) and let you
pick which exchanges to include.

## The large-cap universe is now saved, not re-fetched every scan
Above the two tabs there's a **"Large-cap universe (shared by both
scanners)"** panel. It works like this:

- The first time you tap **Refresh list**, it queries Yahoo Finance's own
  bulk screener (`yf.screen()`/`EquityQuery` in `yfinance`) for every US
  stock above your chosen market cap threshold — a handful of API calls,
  usually done in a few seconds — and **saves the result to
  `large_cap_universe.json`**.
- Every time you open the app or run a scan afterward, it's loaded from
  that saved file automatically. No re-fetch, no re-scan of the whole
  market, unless you explicitly tap **Refresh list** again.
- Both scanners check this saved list first. If your scan's market cap
  threshold and exchange selection are covered by what's saved (e.g. saved
  at ≥$10B and you scan at ≥$15B — that's just a narrower filter on the same
  data), it's reused directly with **zero extra API calls**. If you lower
  the threshold below what was saved, or add an exchange that wasn't
  included, the app fetches fresh for that one scan (and tells you so) —
  tap **Refresh list** with your new settings to make that the saved
  default going forward.
- Refresh it whenever you like — weekly or monthly is plenty, since market
  cap doesn't move fast.

**Caveat:** like scan results, this file lives in the app's own storage.
It survives normal page refreshes and same-day revisits fine, but on
Streamlit Community Cloud's free tier, if the app goes fully idle and its
container restarts, this resets and you'd tap Refresh once to rebuild it
(a few seconds, not the 30+ minutes a per-ticker approach would take).

If the Yahoo screener call itself fails (it's an unofficial, reverse-
engineered part of `yfinance`, not a stable documented API), the app falls
back automatically to the original approach: full NASDAQ+NYSE list from the
Nasdaq Trader symbol directory, then a market-cap check only on whatever
price-action matches are found. Each scanner also has a checkbox to force
that fallback path manually if you'd rather not rely on the screener.

## Run it locally (on a laptop/desktop)
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Use it from your Android phone (recommended path)
1. Push this folder to a GitHub repo.
2. Deploy it for free on **share.streamlit.io** (Streamlit Community
   Cloud), pointing at `app.py`.
3. Open the resulting URL in Chrome on your phone, tap **⋮ → Add to Home
   screen** for an app-like icon.

## Notes & tips
- All matching charts render in one scrollable "waterfall" list below each
  results table, sorted largest market cap first, pulled from Finviz
  (`chart.ashx`, includes moving averages) with an "Open on Finviz" link
  for the full interactive page.
  **Dark theme caveat:** the chart URL includes `&theme=dark`, a
  community-reported but **unconfirmed** parameter for this specific image
  endpoint — test it after deploying; if the background stays light, this
  is the one line in `finviz_urls()` to revisit.
  This is also an unofficial/undocumented Finviz endpoint in general — if
  Finviz ever changes or blocks it, charts may stop loading.
- Each scanner's own results are saved separately (`last_scan_daily.json` /
  `last_scan_ema.json`) and auto-load on refresh, same idea as the
  large-cap list.
- The EMA scanner is still slower than the daily one even with the
  pre-filtered universe, since hourly data per ticker is inherently
  heavier than 5 days of daily bars.
- "Yesterday must be down (red)" checkbox on the Daily Reversal tab lets
  you loosen the rule if you ever want to test up-up momentum instead of a
  reversal.
