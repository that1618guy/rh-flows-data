"""Post each new day's Smart Filter flags to Discord: one short message per day, and nothing on a day when no token
is flagged.

The flags come from smart_filter.py: the tokens that pass a filter (an exit, smart money leaving, a run-up), one
line each with the figures behind it, then how that filter has done so far. No links.

The webhook address is read from the DISCORD_WEBHOOK_URL environment variable and is never printed. With no webhook
set this does nothing and exits cleanly.

What has been posted is kept in <cache_dir>/alerts_posted.json, so a second run on the same day, or a catch-up run,
never posts twice. The first run with a webhook posts the latest day only, not the backlog. After that, a day that
was missed (a failed run, Discord down) is posted on the next run, up to BACKLOG days back.

A token joining or leaving the list (universe.py) gets one line the day it happens: with that day's flags, or as a
message of its own on a day with none. Pass the list's state file to turn that on.

Usage: post_alerts.py <cache_dir> [universe_state.json] [--dry-run]
       --dry-run prints the messages and sends and records nothing
"""
import json, os, sys, time, urllib.error, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smart_filter as SU
from site_shell import dshort

HOSTS = ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")
BACKLOG = 3                      # missed days posted on the next run, at most
LIMIT = 1900                     # Discord takes 2,000 characters a message
MARK = {"exit": "▼", "smart": "▼", "washout": "▼", "runup": "▲"}      # what is happening to the token now, not a forecast
FOOT = "Data, not advice."


def line(it):
    """One flag as one Discord line: the token, the filter it passed, the figures behind it."""
    also = f" Also: {', '.join(it['also'])}." if it.get("also") else ""
    return f"{MARK.get(it['kind'], '•')} **{it['s']}** · {SU.KINDS[it['kind']][0].lower()}. {it['text']}{also}"


def so_far(items, rec):
    """One line on how the filters in today's message have done, for those with enough outcomes to say."""
    said = []
    for k in SU.ORDER:
        if any(it["kind"] == k for it in items) and rec[k]["n"] >= SU.MIN_RECORD:
            said.append(f"{SU.KINDS[k][0].lower()} {rec[k]['hit']} of {rec[k]['n']} did "
                        f"{'better' if SU.KINDS[k][1] > 0 else 'worse'} over the next {rec[k]['days']} days")
    return ("So far, against the median token: " + "; ".join(said) + ".") if said else ""


def more(cut):
    """The tokens flagged past the day's limit, named without a line each."""
    by = {}
    for it in cut:
        by.setdefault(it["kind"], []).append(it["s"])
    return ("Also flagged: " + "; ".join(f"{', '.join(v)} ({SU.KINDS[k][0].lower()})" for k, v in by.items()) + ".") if by else ""


def list_notes(path, state, asof):
    """({day: "New on the list: A. Dropped: B (why)."}, {day: its newest change}, the newest change so far) for the
    changes to the token list not posted yet. Changes are numbered as they happen, so one made by a later run of
    the same night is still posted. A token read in at the end of a run is dated the next day, the first day the
    pages show it, and is posted then. BACKLOG nights back at most, and only the latest night the first time."""
    try:
        events = [e for e in json.load(open(path))["events"] if e.get("d") and e.get("n")] if path else []
    except (OSError, ValueError, KeyError, TypeError):
        return {}, {}, 0
    top = max((e["n"] for e in events if e["d"] <= asof), default=0)    # one dated tomorrow is still to come
    by = {}
    for e in events:
        if e["d"] <= asof and (e["n"] > state["list"] if "list" in state else e["d"] == asof):
            by.setdefault(e["d"], []).append(e)
    out, last = {}, {}
    for d in sorted(by)[-BACKLOG:]:
        add, drop = ([e for e in by[d] if e["type"] == t] for t in ("add", "drop"))
        said = []
        if add:
            said.append("New on the list: " + ", ".join(e["s"] for e in add) + ".")
        if drop:
            why = lambda e: f" ({e['why']})" if e.get("why") and e["why"] != "blocked" else ""
            said.append("Dropped: " + ", ".join(e["s"] + why(e) for e in drop) + ".")
        if said:
            out[d] = " ".join(said); last[d] = max(e["n"] for e in by[d])
    return out, last, top


def messages(day, items, rec, cut=(), note=""):
    """The day's flags as one message, split only if it runs past Discord's limit. note: that day's changes to the
    token list; on a day with no flags it is the whole message."""
    out, cur = [], f"**Smart Filter · Robinhood Chain · {dshort(day)}**"
    if not items:
        return [cur + "\n" + note] if note else []
    for ln in [line(it) for it in items] + [x for x in (more(cut), so_far(items, rec), note) if x] + [FOOT]:
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


def pending(log, state):
    """[(day, its posted flags)] still to post, oldest first."""
    by = {}
    for it in log["items"]:
        if it["posted"]:
            by.setdefault(it["d"], []).append(it)
    if "through" not in state:
        return [(log["asof"], by[log["asof"]])] if log["asof"] in by else []
    return [(d, by[d]) for d in sorted(by) if d > state["through"]][-BACKLOG:]


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cache, dry = sys.argv[1], "--dry-run" in sys.argv
    lpath = next((x for x in sys.argv[2:] if not x.startswith("--")), None)
    url = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
    log, _ = SU.update(cache)
    sp = os.path.join(cache, "alerts_posted.json")
    state = json.load(open(sp)) if os.path.exists(sp) else {}
    todo = pending(log, state)
    notes, note_n, top_n = list_notes(lpath, state, log["asof"])
    todo = sorted(dict([(d, []) for d in notes] + todo).items())           # a day with only a list change is posted too
    n_today = sum(1 for it in log["items"] if it["d"] == log["asof"] and it["posted"])
    print(f"smart filter: {n_today} flagged on {log['asof']}, {sum(1 for it in log['items'] if it['posted'])} since {log['days'][0]}")
    msgs = lambda d, items: messages(d, items, SU.record(log, upto=d), [it for it in log["items"] if it["d"] == d and it.get("cut")],
                                     notes.get(d, ""))
    if dry:
        for d, items in todo:
            for t in msgs(d, items):
                print(f"--- would post for {d}:\n{t}")
        return
    if not url:
        print("smart filter: no Discord webhook set, nothing sent"); return
    if not url.startswith(HOSTS):
        print("::warning title=Discord::DISCORD_WEBHOOK_URL isn't a Discord webhook address, nothing sent"); return
    sent = 0
    for d, items in todo:
        if not all(post(url, t) for t in msgs(d, items)):
            break
        state["through"] = max(d, state.get("through", "")); sent += 1
        if d in notes:
            state["list"] = max(note_n[d], state.get("list", 0))
    else:
        if lpath:
            state["list"] = max(top_n, state.get("list", 0))        # no list change left to post
    if not todo and state.get("through", "") < log["asof"]:
        state["through"] = log["asof"]                     # a day with nothing to post is done too
    tmp = sp + ".tmp"
    json.dump(state, open(tmp, "w")); os.replace(tmp, sp)
    print(f"smart filter: posted {sent} day{'' if sent == 1 else 's'} to Discord" + (f", through {state['through']}" if state.get("through") else ""))


if __name__ == "__main__":
    main()
