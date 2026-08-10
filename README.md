# Reversal Scanner

Scans every NASDAQ + NYSE common stock via Yahoo Finance data and finds
stocks that had a **red candle yesterday** (close < open) and are **up X%
today** (default 2%, measured close-to-close), with a minimum market cap
filter (default $10B). Click any result for an instant candlestick + volume
chart.

Rules are adjustable in the sidebar — nothing is hardcoded except the
defaults, which match what you asked for.

## How it works (fast, in two passes)
1. Pulls the full current list of NASDAQ/NYSE common stock tickers
   (~6,000–7,000 symbols) from the official Nasdaq Trader symbol directory.
2. Batch-downloads recent daily price bars for all of them and filters for
   your price-action rule — this is the expensive step, usually 2–6 minutes
   for the full market.
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
- Results are now saved to `last_scan.json` after every scan and auto-loaded
  when you reopen or refresh the page, so you don't need to re-scan just to
  look at your last results again. You'll see a "Showing saved results from…"
  timestamp at the top when this happens.
- **Caveat on Streamlit Community Cloud (free tier):** the app's storage is
  tied to its running container. It survives normal page refreshes and
  revisits just fine, but if the app goes to sleep from inactivity and later
  restarts, that file resets and you'll need to run a fresh scan. If you
  want results to survive indefinitely across restarts too, the next step
  would be pointing `save_results`/`load_results` at a small external store
  (e.g. a GitHub Gist, or a free cloud database) instead of the local file —
  happy to add that if it matters to you.
- Ticker list is cached for 24h, and price/market cap results are cached for
  15–30 minutes, so re-running the scan shortly after is much faster.
- Yahoo Finance may occasionally rate-limit large batch requests. If a scan
  comes back short, just tap "Run scan" again after a minute.
- To scan only NASDAQ or only NYSE, adjust the "Exchanges" filter in the
  sidebar.
- "Yesterday must be down (red)" checkbox lets you loosen the rule if you
  ever want to test up-up momentum instead of a reversal.
