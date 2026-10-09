#!/usr/bin/env python3
"""NDX IBS dip bot.
Signal: Nasdaq-100 cash index (^NDX), IBS = (last-low)/(high-low) taken ~5 min before the US close.
Rules: flat & IBS < 0.15 -> BUY.  Long & (IBS > 0.5 or held >= 10 sessions) -> CLOSE.
Broker: PAPER (default) or IG spread bet (demo by default; live needs IG_ENV=live AND LIVE_CONFIRM=yes).
Usage: python bot.py            normal run (waits until close-5min)
       python bot.py --force    run now on latest data, no wait (testing)
       python bot.py --find-epic  list IG epics for US Tech 100
"""
import os, sys, json, time, csv, datetime as dt, urllib.request, urllib.parse

ENTRY_TH = float(os.getenv("ENTRY_TH", 0.15)); EXIT_TH = float(os.getenv("EXIT_TH", 0.5))
MAX_HOLD = int(os.getenv("MAX_HOLD", 10));    LEAD = int(os.getenv("LEAD_SECONDS", 300))
MAX_WAIT = int(os.getenv("MAX_WAIT_MIN", 80)) * 60      # skip if close is further away (other cron handles it)
COST = 0.0003                                          # paper: 3bp per side
STATE, TRADES, DAILY = "state.json", "trades.csv", "daily_log.csv"
UA = {"User-Agent": "Mozilla/5.0"}

def http(url, method="GET", headers=None, body=None):
    req = urllib.request.Request(url, method=method, headers={**UA, **(headers or {})},
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=30) as r:
        txt = r.read().decode()
        return (json.loads(txt) if txt else {}), r.headers

def ndx():
    d, _ = http("https://query1.finance.yahoo.com/v8/finance/chart/%5ENDX?range=1mo&interval=1d")
    return d["chart"]["result"][0]

def notify(msg):
    print(msg)
    hook = os.getenv("DISCORD_WEBHOOK")
    if hook:
        try: http(hook, "POST", {"Content-Type": "application/json"}, {"content": msg})
        except Exception as e: print("discord failed:", e)

def append(path, row):
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row)); new and w.writeheader(); w.writerow(row)

# ---------------- brokers ----------------
class Paper:
    name = "PAPER"
    def buy(self, px):  return {"deal_id": "paper", "fill": px}
    def close(self, st, px): return {"fill": px}

class IG:
    def __init__(self):
        env = os.getenv("IG_ENV", "demo")
        if env == "live" and os.getenv("LIVE_CONFIRM") != "yes":
            sys.exit("Refusing live trading: set LIVE_CONFIRM=yes as well as IG_ENV=live")
        self.name = f"IG-{env.upper()}"
        self.base = "https://api.ig.com/gateway/deal" if env == "live" else "https://demo-api.ig.com/gateway/deal"
        self.epic = os.getenv("IG_EPIC", "IX.D.NASDAQ.IFD.IP")       # US Tech 100 DFB — verify with --find-epic
        self.size = float(os.getenv("STAKE_PER_POINT", "1"))
        key = os.environ["IG_API_KEY"]
        base_h = {"X-IG-API-KEY": key, "Content-Type": "application/json", "Accept": "application/json; charset=UTF-8"}
        sess, h = http(self.base + "/session", "POST", {**base_h, "Version": "2"},
                       {"identifier": os.environ["IG_USERNAME"], "password": os.environ["IG_PASSWORD"]})
        self.h = {**base_h, "CST": h.get("CST"), "X-SECURITY-TOKEN": h.get("X-SECURITY-TOKEN")}
        acct = os.getenv("IG_ACCOUNT_ID")                              # spread-bet account, not CFD
        if acct and sess.get("currentAccountId") != acct:
            _, h2 = http(self.base + "/session", "PUT", {**self.h, "Version": "1"},
                         {"accountId": acct, "defaultAccount": False})
            if h2.get("X-SECURITY-TOKEN"): self.h["X-SECURITY-TOKEN"] = h2.get("X-SECURITY-TOKEN")
        elif not acct:
            print("WARNING: IG_ACCOUNT_ID not set — trading whatever account IG defaults to")
    def _confirm(self, ref):
        for _ in range(10):
            try:
                c, _ = http(f"{self.base}/confirms/{ref}", headers={**self.h, "Version": "1"})
                if c.get("dealStatus") != "ACCEPTED": raise SystemExit(f"IG rejected: {c.get('reason')}")
                return c
            except urllib.error.HTTPError: time.sleep(1)
        raise SystemExit("IG confirm timed out")
    def buy(self, px):
        r, _ = http(self.base + "/positions/otc", "POST", {**self.h, "Version": "2"},
                    {"epic": self.epic, "expiry": "DFB", "direction": "BUY", "size": self.size, "orderType": "MARKET",
                     "currencyCode": "GBP", "forceOpen": True, "guaranteedStop": False})
        c = self._confirm(r["dealReference"]); return {"deal_id": c["dealId"], "fill": c.get("level", px)}
    def close(self, st, px):
        r, _ = http(self.base + "/positions/otc", "POST", {**self.h, "Version": "1", "_method": "DELETE"},
                    {"dealId": st["deal_id"], "direction": "SELL", "size": self.size, "orderType": "MARKET"})
        c = self._confirm(r["dealReference"]); return {"fill": c.get("level", px)}
    def find_epic(self):
        q = urllib.parse.quote("US Tech 100")
        m, _ = http(f"{self.base}/markets?searchTerm={q}", headers={**self.h, "Version": "1"})
        for x in m.get("markets", []): print(x["epic"], "|", x["instrumentName"], "|", x.get("expiry"))

# ---------------- main ----------------
def main():
    force = "--force" in sys.argv
    use_ig = bool(os.getenv("IG_API_KEY"))
    if "--find-epic" in sys.argv: IG().find_epic(); return
    st = json.load(open(STATE)) if os.path.exists(STATE) else {"in_pos": False}

    meta = ndx()["meta"]; per = meta["currentTradingPeriod"]["regular"]; now = time.time()
    today = dt.datetime.fromtimestamp(per["start"], dt.timezone.utc).date().isoformat()
    if not force:
        if not (per["start"] <= now < per["end"]): print("US market not open now — skipping."); return
        if st.get("last_run") == today: print("Already ran today — skipping."); return
        wait = per["end"] - LEAD - now
        if wait > MAX_WAIT: print(f"Close is {wait/60:.0f} min away — later cron will handle it."); return
        if wait > 0: print(f"Sleeping {wait/60:.1f} min until close-{LEAD//60}min"); time.sleep(wait)

    res = ndx(); meta = res["meta"]
    last, hi, lo = meta["regularMarketPrice"], meta["regularMarketDayHigh"], meta["regularMarketDayLow"]
    ibs = (last - lo) / (hi - lo) if hi > lo else 0.5
    sessions = sorted({dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat() for t in res["timestamp"]} | {today})
    held = len([d for d in sessions if st.get("entry_date", "9999") < d <= today]) if st["in_pos"] else 0

    action = "HOLD" if st["in_pos"] else "FLAT"
    broker = None
    if not st["in_pos"] and ibs < ENTRY_TH:
        broker = IG() if use_ig else Paper(); f = broker.buy(last)
        st = {"in_pos": True, "entry_date": today, "entry_px": f["fill"], "deal_id": f["deal_id"], "broker": broker.name}
        action = "BUY"
        notify(f"🟢 NDX IBS {ibs:.2f} → BUY US100 @ {f['fill']:.1f} [{broker.name}]")
    elif st["in_pos"] and (ibs > EXIT_TH or held >= MAX_HOLD):
        broker = IG() if st.get("broker", "PAPER").startswith("IG") else Paper(); f = broker.close(st, last)
        ret = f["fill"] / st["entry_px"] - 1 - (2 * COST if broker.name == "PAPER" else 0)
        append(TRADES, {"entry_date": st["entry_date"], "entry_px": round(st["entry_px"], 2), "exit_date": today,
                        "exit_px": round(f["fill"], 2), "ret_pct": round(ret * 100, 3), "sessions_held": held,
                        "reason": "IBS" if ibs > EXIT_TH else "time", "broker": broker.name})
        notify(f"🔴 NDX IBS {ibs:.2f} → CLOSE US100 @ {f['fill']:.1f} | trade {ret*100:+.2f}% over {held} sessions [{broker.name}]")
        st = {"in_pos": False}; action = "SELL"
    else:
        print(f"NDX IBS {ibs:.2f} — {action} (held {held})")

    append(DAILY, {"date": today, "utc": dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S"), "last": last,
                   "high": hi, "low": lo, "ibs": round(ibs, 3), "action": action, "held": held})
    st["last_run"] = today
    json.dump(st, open(STATE, "w"), indent=2)

if __name__ == "__main__":
    main()
