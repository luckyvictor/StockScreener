# Reversal Scanner

Scans all NASDAQ + NYSE common stocks via Yahoo Finance data and finds
stocks that:
- had a **red candle yesterday** (close < open), and
- are **up X% today** (default 2%, measured close-to-close), and
- today's close sits in the **top portion of today's candle** (default: at
  least 60% of the way up the low-to-high range), so a weak bullish candle
  with a big upper wick doesn't count.

Only stocks at or above a minimum market cap (default $10B) are considered.
All rules are adjustable in the sidebar — nothing is hardcoded except the
defaults, which match what you asked for.

## How it works (fast, in two passes)
1. Pulls the full current list of NASDAQ/NYSE common stock tickers
   (~6,000–7,000 symbols) from the official Nasdaq Trader symbol directory.
2. Batch-downloads recent daily price bars for all of them and filters for
   your price-action rules — this is the expensive step, usually a few
   minutes for the full market.
3. Only for the small number of matches, it fetches market cap and drops
   anything under your threshold.

## Run it locally (on a laptop/desktop)
```bash
pip install -r requirements.txt
streamlit run app.py
```
This opens in your desktop browser at `http://localhost:8501`.

## Use it from your Android phone (recommended path)
Streamlit apps are web apps, so the easiest way to get this "as an app" on
your phone is to host it once and just open the link:

1. Create a free GitHub account (if you don't have one) and push this
   folder to a new repo.
2. Go to **share.streamlit.io** (Streamlit Community Cloud, free), sign in
   with GitHub, and deploy the repo — point it at `app.py`.
3. You'll get a permanent URL like `https://your-app.streamlit.app`.
4. Open that URL in Chrome on your Android phone, tap the **⋮** menu →
   **Add to Home screen**. It'll behave like a normal app icon/launcher.

No server maintenance needed — Streamlit Cloud keeps it hosted for free for
personal projects like this. Every time you tap "Run scan" it pulls fresh
Yahoo Finance data live.

## Notes & tips
- All matching charts render in one scrollable "waterfall" list below the
  results table, sorted largest market cap first — no need to pick a ticker
  from a dropdown. Each chart has the key stats (yesterday %, today %,
  market cap, last price) above it.
- Charts are pulled from Finviz (`chart.ashx`), which already includes
  moving averages and generally renders better than a hand-rolled chart —
  nothing is plotted on our end. Each ticker also gets an "Open on Finviz"
  button for the full interactive page. **Caveat:** this uses an unofficial,
  undocumented Finviz image endpoint (widely used this way, but not a
  supported API) — if Finviz ever changes or blocks it, charts may stop
  loading. Let me know if you'd like a self-plotted fallback added back in.
- Results are saved to `last_scan.json` after every scan and auto-loaded
  when you reopen or refresh the page, so you don't need to re-scan just to
  look at your last results again. You'll see a "Showing saved results
  from…" timestamp at the top when this happens. Note: on Streamlit
  Community Cloud's free tier, this resets if the app goes fully idle and
  restarts — normal refreshes and same-day revisits are unaffected.
- Yahoo Finance may occasionally rate-limit large batch requests. If a scan
  comes back short, just tap "Run scan" again after a minute.
- To scan only NASDAQ or only NYSE, adjust the "Exchanges" filter in the
  sidebar.
- "Yesterday must be down (red)" checkbox lets you loosen the rule if you
  ever want to test up-up momentum instead of a reversal.
