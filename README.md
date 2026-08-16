# Reversal Scanner

Two layers:

## 🏢 Large-cap universe
The ticker list both scanners use. **The only filter here is market cap**
(default: greater than $10B). Fetched via Yahoo Finance's own bulk screener
(a `yfinance` feature — `yf.screen()`/`EquityQuery`), saved to
`large_cap_universe.json`, and reused every time you open the app — it is
**not** rebuilt on every scan. Refresh it manually whenever you like via
the **🔄 Refresh list** button — monthly is plenty, since market cap doesn't
move fast.

If no list has been saved yet and you run a scanner, it's built
automatically first (using the threshold currently set in this panel),
saved, then used for that scan — you don't have to remember to build it
yourself first.

**Fallback:** if Yahoo's screener is ever unavailable (it's an unofficial,
reverse-engineered API, not a stable documented one), building the list
falls back automatically to the full NASDAQ+NYSE symbol list from the
Nasdaq Trader directory, with a market-cap check per ticker — much slower,
but keeps the app working.

## 📉 Daily Reversal & 📈 1H EMA Crossover
Both scanners read the saved large-cap list **as-is** and apply only their
own price-action rules — they do **not** re-check market cap (that's
already been filtered upstream by the universe layer):

- **Daily Reversal**: red candle yesterday (close < open), up X% today
  (default 2%, close-to-close), with today's close in the top portion of
  today's range (default: at least 60% of the way from low to high) so a
  weak bullish candle with a big upper wick doesn't count.
- **1H EMA Crossover**: on some candle within the last N hourly candles
  (default 15), the 10-period EMA is above the 90-period EMA (short-term
  uptrend intact) AND that candle closed strong — its close sits at least
  X% (default 80%) of the way up its own low-to-high range, so a
  wishy-washy drift-up candle doesn't count. Uses ~2 months of hourly data.
  Reports how many candles ago the match happened and the exact timestamp.

Each scanner's results are saved separately (`last_scan_daily.json` /
`last_scan_ema.json`) and reload automatically when you reopen or refresh
the page — no need to re-scan just to see your last results.

## Persistence caveat
Like everything saved in this app, these files live in the app's own
storage. They survive normal page refreshes and same-day revisits fine,
but on Streamlit Community Cloud's free tier, if the app goes fully idle
and its container restarts, saved files reset. For the universe list,
that just means the next scan rebuilds it automatically (a few seconds via
the screener, not the tens of minutes a per-ticker approach would take).

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
- The EMA scanner is still slower than the daily one even scanning only
  the pre-filtered universe, since hourly data per ticker is inherently
  heavier than 5 days of daily bars.
- "Yesterday must be down (red)" checkbox on the Daily Reversal tab lets
  you loosen the rule if you ever want to test up-up momentum instead of a
  reversal.
