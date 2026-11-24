"""Robinhood Chain helpers: JSON-RPC (single and batched) with retries, logs with adaptive range splitting,
Multicall3 balances, block-by-time search."""
import json, urllib.request, urllib.error, time
RPC = "https://rpc.mainnet.chain.robinhood.com"
HDR = {"content-type": "application/json", "user-agent": "curl/8.5.0", "accept": "*/*"}
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
SWAP_V2 = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
SWAP_V3 = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
SWAP_V4 = "0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f"
POOL_MANAGER = "0x8366a39cc670b4001a1121b8f6a443a643e40951"
MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"
ZERO = "0x0000000000000000000000000000000000000000"
DEAD = "0x000000000000000000000000000000000000dead"
_id = [0]
def _post(payload, timeout=120):
    body = json.dumps(payload).encode()
    return json.load(urllib.request.urlopen(urllib.request.Request(RPC, data=body, headers=HDR), timeout=timeout))
def _wait(e, k):
    time.sleep(12 * (k + 1) if isinstance(e, urllib.error.HTTPError) and e.code == 429 else 2 * (k + 1))
def _passing(e):
    """A JSON-RPC error worth waiting out: a rate limit, or the RPC's own backend failing ("failed to call backend
    endpoint"). Retried with the same backoff as a network error; any other error goes back to the caller."""
    m = str((e or {}).get("message"))
    return bool(e) and (e.get("code") == 429 or "Too Many" in m or "backend endpoint" in m)
def rpc(method, params, tries=10):
    """The raw response. Passing errors are retried; any other error is returned in it for the caller to handle."""
    err = None
    for k in range(tries):
        _id[0] += 1
        try:
            d = _post({"jsonrpc": "2.0", "id": _id[0], "method": method, "params": params})
        except Exception as e:
            err = e; _wait(e, k); continue
        if isinstance(d, dict) and _passing(d.get("error")):
            err = d["error"]; _wait(None, k); continue
        return d
    raise RuntimeError(f"rpc {method} failed: {err}")
def batch(method, params_list, size=50, tries=10):
    """Results in order. An item that comes back with a passing error, or not at all, is asked again on its own with
    the same backoff; any other error raises. A failed call never comes back as None."""
    out = []
    for i in range(0, len(params_list), size):
        part = params_list[i:i + size]
        res, todo, err = [None] * len(part), list(range(len(part))), None
        for k in range(tries):
            try:
                d = _post([{"jsonrpc": "2.0", "id": j, "method": method, "params": part[j]} for j in todo])
            except Exception as e:
                err = e; _wait(e, k); continue
            if isinstance(d, dict):                    # the whole batch refused
                err = d.get("error"); _wait(None, k); continue
            again = []
            for x in d:
                if "error" in x:
                    if not _passing(x["error"]):
                        raise RuntimeError(f"rpc {method} failed: {x['error']}")
                    err = x["error"]; again.append(x["id"])
                else:
                    res[x["id"]] = x.get("result")
            got = {x.get("id") for x in d}
            todo = again + [j for j in todo if j not in got]
            if not todo:
                break
            _wait(None, k)
        else:
            raise RuntimeError(f"rpc batch {method} kept failing: {err}")
        out += res
        time.sleep(0.15)
    return out
def head():
    return int(rpc("eth_blockNumber", [])["result"], 16)
def block_ts(n):
    for k in range(8):
        r = rpc("eth_getBlockByNumber", [hex(n), False])
        if r.get("result"): return int(r["result"]["timestamp"], 16)
        time.sleep(1.5 * (k + 1))
    raise RuntimeError(f"block {n}: {r.get('error')}")
def block_at(ts, lo=1, hi=None):
    """First block with timestamp >= ts."""
    hi = hi or head()
    while lo < hi:
        mid = (lo + hi) // 2
        if block_ts(mid) < ts: lo = mid + 1
        else: hi = mid
    return lo
def logs(address, topics, frm, to, step=50000):
    out, a = [], frm
    while a <= to:
        b = min(to, a + step - 1)
        d = rpc("eth_getLogs", [{"address": address, "topics": topics, "fromBlock": hex(a), "toBlock": hex(b)}])
        if "error" in d:
            if step <= 20: raise RuntimeError(d["error"])
            step = max(20, step // 3); continue
        out += d["result"]; a = b + 1
        if len(d["result"]) < 3000: step = min(step * 2, 800000)
    return out
def topic_addr(t): return "0x" + t[-40:]
def _u(n): return format(n, "064x")
def encode_aggregate3(calls):
    enc_calls = []
    for target, allow, cd in calls:
        cdb = cd[2:]; ln = len(cdb) // 2
        pad = cdb + "0" * ((64 - len(cdb) % 64) % 64)
        enc_calls.append(_u(int(target, 16)) + _u(1 if allow else 0) + _u(96) + _u(ln) + pad)
    offs, cur = [], 32 * len(calls)
    for e in enc_calls:
        offs.append(cur); cur += len(e) // 2
    return "0x82ad56cb" + _u(32) + _u(len(calls)) + "".join(_u(o) for o in offs) + "".join(enc_calls)
def decode_aggregate3(hexdata):
    b = hexdata[2:]
    word = lambda i: int(b[i * 64:(i + 1) * 64], 16)
    base = word(0) // 32; n = word(base); res = []
    for k in range(n):
        off = word(base + 1 + k) // 32 + base + 1
        ok = word(off) == 1; doff = word(off + 1) // 32 + off; ln = word(doff)
        res.append((ok, b[(doff + 1) * 64:(doff + 1) * 64 + ln * 2]))
    return res
def balances(token, holders, block="latest", chunk=300):
    out, sel = {}, "0x70a08231"
    reqs = []
    for i in range(0, len(holders), chunk):
        part = holders[i:i + chunk]
        calls = [(token, True, sel + "0" * 24 + h[2:].lower()) for h in part]
        reqs.append((part, [{"to": MULTICALL3, "data": encode_aggregate3(calls)}, block if isinstance(block, str) else hex(block)]))
    res = batch("eth_call", [r[1] for r in reqs], size=4)
    for (part, _), r in zip(reqs, res):
        for h, (ok, ret) in zip(part, decode_aggregate3(r)):
            out[h] = int(ret[:64], 16) if ok and len(ret) >= 64 else 0
    return out
def decimals(token):
    r = rpc("eth_call", [{"to": token, "data": "0x313ce567"}, "latest"]).get("result") or "0x12"
    return int(r, 16) if r not in ("0x", None) else 18


def iter_logs(address, topics, frm, to, step=50000):
    """Yield chunks of logs in block order, halving the range when the node's 10k cap is hit (constant memory)."""
    a = frm
    while a <= to:
        b = min(to, a + step - 1)
        d = rpc("eth_getLogs", [{"address": address, "topics": topics, "fromBlock": hex(a), "toBlock": hex(b)}])
        if "error" in d:
            if step <= 20: raise RuntimeError(d["error"])
            step = max(20, step // 3); continue
        yield d["result"]
        a = b + 1
        if len(d["result"]) < 3000: step = min(step * 2, 800000)
