"""Post each new day's large-wallet alerts to Discord: one message per day, and nothing on a day with no alerts.
The latest day's message ends with one line on what the most profitable wallets did, from the Smart money page.

The alerts come from alerts.py. The webhook address is read from the DISCORD_WEBHOOK_URL environment variable and is
never printed. With no webhook set this does nothing and exits cleanly, so the workflow step can sit in place before
the Discord side exists.

What has been posted is kept in <cache_dir>/alerts_posted.json, so a second run on the same day, or a catch-up run,
never posts twice. The first run with a webhook posts the latest day only, not the backlog. After that, a day that
was missed (a failed run, Discord down) is posted on the next run, up to BACKLOG days back.

Usage: post_alerts.py <cache_dir> [--dry-run]     --dry-run prints the messages and sends and records nothing
"""
import json, os, re, sys, time, urllib.error, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alerts as AL
from site_shell import fm, signed, dshort

SITE = "https://rhchain.stateofblocks.com/flows"
HOSTS = ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")
BACKLOG = 3                      # days back a missed day is still posted
LIMIT = 1900                     # Discord takes 2,000 characters a message
QUIET = 5_000                    # smart money's net on the day under which they "were quiet"


def line(a):
    """One alert as one Discord line."""
    buy = a["side"] == "buy"
    did, undid = ("bought", "sold") if buy else ("sold", "bought")
    share = AL._share(a["share"])
    if a["kind"] == "turn":
        s = (f"large wallets turned {'buyers' if buy else 'sellers'}: {fm(abs(a['day']))} {did}, after "
             f"{fm(abs(a['prior']))} {undid} over the {a['prior_days']} days before ({share} of market cap).")
    elif a["kind"] == "run":
        s = f"large wallets {did} {a['run']} days running: {fm(abs(a['usd']))} in all ({share} of market cap)."
    else:
        s = f"large wallets {did} {fm(abs(a['usd']))} ({share} of market cap)."
    return f"{'▲' if buy else '▼'} **{a['s']}** · {s}" + (f" {a['more']}" if a["more"] else "")


def smart_line(cache, day):
    """One line on what the most profitable wallets did that day, read from the Smart money page built earlier in
    the same run so it matches the site. None when that page is missing, unreadable or for another day."""
    try:
        s = open(os.path.join(cache, "smart.html"), encoding="utf-8").read()
        w = json.loads(re.search(r'<script[^>]*id="smart-money-data"[^>]*>(.*?)</script>', s, re.S).group(1))["wins"]["1d"]
        if w["label"] != dshort(day):
            return None
        T = w["tokens"]; net = sum(t["net"] for t in T); crowd = sum(t["crowd"] for t in T)
        rest = f"Everyone else net {'bought' if crowd >= 0 else 'sold'} {fm(abs(crowd))}."
        who = f"the {w['n']} most profitable wallets"
        if abs(net) < QUIET:
            return f"• **Smart money** · {who} were quiet. {rest}"
        lead = max(T, key=lambda t: t["net"]) if net > 0 else min(T, key=lambda t: t["net"])
        return (f"{'▲' if net > 0 else '▼'} **Smart money** · {who} net {'bought' if net > 0 else 'sold'} {fm(abs(net))}, "
                f"led by {lead['s']} ({signed(lead['net'])}). {rest}")
    except Exception:
        return None


def messages(day, alerts, extra=None):
    """The day's alerts as one message, split only if it runs past Discord's limit."""
    head = f"**Big moves on Robinhood Chain · {dshort(day)}**"
    foot = f"Large wallets hold $5K+ of the token. Data, not advice. <{SITE}>"
    out, cur = [], head
    for ln in [line(a) for a in alerts] + ([extra] if extra else []) + [foot]:
        if len(cur) + 1 + len(ln) > LIMIT:
            out.append(cur); cur = ln
        else:
            cur += "\n" + ln
    return out + [cur]


def post(url, text, tries=4):
    """Send one message. True when Discord took it."""
    body = json.dumps({"content": text, "allowed_mentions": {"parse": []}}).encode()
    for k in range(tries):
        req = urllib.request.Request(url, data=body, headers={"content-type": "application/json", "user-agent": "sob-rh-alerts"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return 200 <= r.status < 300
        except urllib.error.HTTPError as e:
            if e.code == 429 and k < tries - 1:                       # slow down, as Discord asks
                try:
                    wait = float(json.load(e).get("retry_after", 2))
                except Exception:
                    wait = 2.0
                time.sleep(min(wait, 30) + 0.5); continue
            print(f"::warning title=Alerts::Discord answered {e.code}"); return False
        except Exception as e:
            if k < tries - 1:
                time.sleep(3 * (k + 1)); continue
            print(f"::warning title=Alerts::couldn't reach Discord ({type(e).__name__})"); return False
    return False


def pending(m, state):
    """[(day, alerts)] still to post, oldest first."""
    by_day = {}
    for a in m["alerts"]:
        by_day.setdefault(a["d"], []).append(a)
    days = sorted(by_day)
    if not state.get("through"):                      # first run: the latest day only, never the backlog
        return [(m["asof"], by_day[m["asof"]])] if m["asof"] in by_day else []
    todo = [d for d in days if d > state["through"]][-BACKLOG:]
    return [(d, by_day[d]) for d in todo]


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cache, dry = sys.argv[1], "--dry-run" in sys.argv
    url = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
    m = AL.build(cache)
    sp = os.path.join(cache, "alerts_posted.json")
    state = json.load(open(sp)) if os.path.exists(sp) else {}
    todo = pending(m, state)
    smart = smart_line(cache, m["asof"])               # only the latest day has a page to read
    extra = lambda d: smart if d == m["asof"] else None
    n_today = sum(1 for a in m["alerts"] if a["d"] == m["asof"])
    print(f"alerts: {n_today} on {m['asof']}, {len(m['alerts'])} since {m['from']}"
          + ("" if smart else "; no smart money line, its page is missing or for another day"))
    if dry:
        for d, al in todo:
            for t in messages(d, al, extra(d)):
                print("--- would post:\n" + t)
        return
    if not url:
        print("alerts: no Discord webhook set, nothing sent"); return
    if not url.startswith(HOSTS):
        print("::warning title=Alerts::DISCORD_WEBHOOK_URL isn't a Discord webhook address, nothing sent"); return
    sent = 0
    for d, al in todo:
        if not all(post(url, t) for t in messages(d, al, extra(d))):
            break                                     # leave this day and later ones for the next run
        state["through"] = d; sent += 1
        tmp = sp + ".tmp"
        json.dump(state, open(tmp, "w")); os.replace(tmp, sp)
        time.sleep(1)
    if not todo and state.get("through", "") < m["asof"]:
        state["through"] = m["asof"]                  # a day with no alerts is done too
        json.dump(state, open(sp, "w"))
    print(f"alerts: posted {sent} day{'' if sent == 1 else 's'} to Discord" + (f", through {state.get('through')}" if state.get("through") else ""))


if __name__ == "__main__":
    main()
