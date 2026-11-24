"""Post each new day's large-wallet alerts to Discord: one short message per day, and nothing on a day with no alerts.

The message is a one-sentence summary and then one short line per token: the token, the amount and what kind of move
it was. On the latest day the summary also says what the most profitable wallets did, read from the Smart money page.
No links.

The alerts come from alerts.py. The webhook address is read from the DISCORD_WEBHOOK_URL environment variable and is
never printed. With no webhook set this does nothing and exits cleanly.

What has been posted is kept in <cache_dir>/alerts_posted.json, so a second run on the same day, or a catch-up run,
never posts twice. The first run with a webhook posts the latest day only, not the backlog. After that, a day that
was missed (a failed run, Discord down) is posted on the next run, up to BACKLOG days back.

Usage: post_alerts.py <cache_dir> [--dry-run]     --dry-run prints the messages and sends and records nothing
"""
import json, os, re, sys, time, urllib.error, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alerts as AL
from site_shell import fm, signed, dshort

HOSTS = ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")
BACKLOG = 3                      # days back a missed day is still posted
LIMIT = 1900                     # Discord takes 2,000 characters a message
QUIET = 5_000                    # smart money's net on the day under which they "were quiet"
FOOT = "Data, not advice."


def line(a):
    """One alert as one short Discord line: the token, the amount and what kind of move it was."""
    buy = a["side"] == "buy"
    if a["kind"] == "run":
        s = f"{signed(a['usd'])} over {a['run']} days"
    elif a["kind"] == "turn":
        s = f"{signed(a['usd'])} · turned {'buyers' if buy else 'sellers'}"
    else:
        s = signed(a["usd"])
    return f"{'▲' if buy else '▼'} **{a['s']}** {s}"


def smart_line(cache, day):
    """One sentence on what the most profitable wallets did that day, read from the Smart money page built earlier in
    the same run so it matches the site. None when that page is missing, unreadable or for another day."""
    try:
        s = open(os.path.join(cache, "smart.html"), encoding="utf-8").read()
        w = json.loads(re.search(r'<script[^>]*id="smart-money-data"[^>]*>(.*?)</script>', s, re.S).group(1))["wins"]["1d"]
        if w["label"] != dshort(day):
            return None
        T = w["tokens"]; net = sum(t["net"] for t in T); crowd = sum(t["crowd"] for t in T)
        who = f"Smart money (top {w['n']} wallets by profit)"
        rest = f"everyone else net {'bought' if crowd >= 0 else 'sold'} {fm(abs(crowd))}"
        if abs(net) < QUIET:
            return f"{who} was quiet, and {rest}."
        lead = max(T, key=lambda t: t["net"]) if net > 0 else min(T, key=lambda t: t["net"])
        join = "and" if (net > 0) == (crowd >= 0) else "while"
        return (f"{who} net {'bought' if net > 0 else 'sold'} {fm(abs(net))}, led by {lead['s']} "
                f"({signed(lead['net'])}), {join} {rest}.")
    except Exception:
        return None


def summary(alerts, smart=None):
    """The day in one or two sentences: how many tokens saw a big buy or sell, then smart money when there is a read."""
    n = lambda k: f"{k} token{'' if k == 1 else 's'}"
    b = sum(1 for a in alerts if a["side"] == "buy"); s = len(alerts) - b
    if b and s:
        did = f"made big buys in {n(b)} and big sells in {n(s)}"
    elif b:
        did = f"made big buys in {n(b)}, no big sells"
    else:
        did = f"made big sells in {n(s)}, no big buys"
    return f"Large wallets ($5K+ holders) {did}." + (f" {smart}" if smart else "")


def messages(day, alerts, extra=None):
    """The day's alerts as one message, split only if it runs past Discord's limit. Buys first, largest first."""
    head = f"**Big moves on Robinhood Chain · {dshort(day)}**"
    rows = sorted(alerts, key=lambda a: (a["side"] != "buy", -abs(a["usd"]), a["s"]))
    out, cur = [], head
    for ln in [summary(alerts, extra)] + [line(a) for a in rows] + [FOOT]:
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
