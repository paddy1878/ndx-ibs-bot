# NDX IBS Bot

Runs every US trading day ~5 min before the close (≈8:55pm UK). Reads the Nasdaq-100 cash index, computes IBS,
and buys / closes a US Tech 100 spread bet. Logs everything to the repo.

## Setup (≈15 min, once)
1. New GitHub repo → upload `bot.py`, `README.md`, `.github/workflows/bot.yml`.
   Make it **public** (free unlimited Actions minutes; private repos get 2,000 min/month and this uses ~1,600).
2. Optional alerts: Discord server → channel settings → Integrations → Webhook → copy URL →
   repo Settings → Secrets → Actions → `DISCORD_WEBHOOK`.
3. Paper mode works with nothing else. To trade an **IG demo** spread-bet account:
   - Open a free IG demo, then My IG → Settings → API keys → create key.
   - Add secrets `IG_API_KEY`, `IG_USERNAME`, `IG_PASSWORD` (the demo login).
   - Run `python bot.py --find-epic` locally with those env vars once to confirm the US Tech 100 DFB epic;
     if it isn't `IX.D.NASDAQ.IFD.IP`, add it as secret/env `IG_EPIC`.
   - Stake: repo Settings → Variables → `STAKE_PER_POINT` (1x exposure ≈ account size ÷ index level).
4. Actions tab → ndx-ibs-bot → Run workflow (manual test). Outside US hours it just logs "market not open".

## Files it writes
- `daily_log.csv` — every day's signal-time price, high, low, IBS, action
- `trades.csv` — each completed trade and return
- `state.json` — current position

Live trading is blocked unless `IG_ENV=live` **and** `LIVE_CONFIRM=yes` are both set.
