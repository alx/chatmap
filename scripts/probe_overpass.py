"""Probe Overpass endpoints and record which ones actually work from here.

For each candidate we try every request form (GET ?data=, POST form-encoded,
POST raw body) against a tiny query, several times, and record:
  - the forms that returned parseable JSON
  - TLS stability across attempts (this host sees intermittent 000)
  - osm_base timestamp, so an empty/partial database is visible
    (overpass.osm.ch answered 200 but had 0 hospitals in central Kyoto)
  - latency

Output: an endpoint catalogue JSON consumed by the failover client.
"""
import json
import statistics
import subprocess
import sys
import time
import urllib.parse

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.nchc.org.tw/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
    "https://overpass.xyz/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
]

# Tiny query: real hospitals near Kyoto Station. `out count` keeps it cheap.
PROBE = '[out:json][timeout:20];node["amenity"="hospital"](around:5000,35.0116,135.7681);out count;'
ATTEMPTS = 2


def _run(cmd, timeout):
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 28, b"", b"timeout"


def try_form(url, form, timeout=8):
    """Return (ok, elapsed, body_or_error)."""
    if form == "post-form":
        cmd = ["curl", "-s", "-m", str(timeout), "-X", "POST",
               "--data-urlencode", f"data={PROBE}", url]
    elif form == "post-body":
        cmd = ["curl", "-s", "-m", str(timeout), "-X", "POST",
               "--data-binary", PROBE, url]
    else:  # get
        cmd = ["curl", "-s", "-m", str(timeout), "-G", url,
               "--data-urlencode", f"data={PROBE}"]

    t0 = time.time()
    rc, out, err = _run(cmd, timeout + 5)
    elapsed = time.time() - t0
    if rc != 0 or not out:
        return False, elapsed, (err.decode(errors="replace").strip() or f"rc={rc}")
    try:
        json.loads(out)
        return True, elapsed, out
    except json.JSONDecodeError:
        head = out[:120].decode(errors="replace").replace("\n", " ")
        return False, elapsed, head


def probe(url):
    rec = {"url": url, "forms": {}, "attempts": ATTEMPTS, "ok_count": 0,
           "osm_base": None, "latencies": [], "note": ""}
    total = 0
    for _ in range(ATTEMPTS):
        total += 1
        for form in ("get", "post-form", "post-body"):
            ok, elapsed, body = try_form(url, form)
            if ok:
                rec["ok_count"] += 1
                rec["forms"][form] = True
                rec["latencies"].append(round(elapsed, 2))
                data = json.loads(body)
                rec["osm_base"] = (data.get("osm3s") or {}).get("timestamp_osm_base")
                els = data.get("elements") or []
                rec["kyoto_hospitals"] = (
                    int(els[0]["tags"]["total"]) if els and "tags" in els[0] else 0
                )
                break
            else:
                rec["forms"].setdefault(form, False)
                rec["note"] = str(body)[:80]
    rec["stability"] = round(rec["ok_count"] / total, 2)
    rec["median_latency_s"] = (
        round(statistics.median(rec["latencies"]), 2) if rec["latencies"] else None
    )
    rec["usable"] = bool(rec["ok_count"]) and (rec.get("kyoto_hospitals") or 0) > 0
    return rec


LOG = None


def log(msg):
    print(msg, file=sys.stderr, flush=True)
    if LOG:
        LOG.write(msg + "\n")
        LOG.flush()


def main():
    global LOG
    global_log_path = "probe.log"
    LOG = open(global_log_path, "w", encoding="utf-8", buffering=1)
    out = []
    for url in dict.fromkeys(ENDPOINTS):  # dedupe, keep order
        log(f"probing {url} ...")
        rec = probe(url)
        out.append(rec)
        status = "OK " if rec["usable"] else "BAD"
        lat = rec["median_latency_s"]
        log(
            f"  {status} stability={rec['stability']:.2f} lat={lat} "
            f"hosp={rec.get('kyoto_hospitals')} "
            f"forms={[k for k, v in rec['forms'].items() if v]} "
            f"{'' if rec['usable'] else rec['note']}"
        )
    usable = sorted([r for r in out if r["usable"]], key=lambda r: r["median_latency_s"] or 99)
    payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "endpoints": usable, "all": out}
    path = sys.argv[1] if len(sys.argv) > 1 else "overpass_endpoints.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    log(f"{len(usable)} usable -> {path}")
    return 0 if usable else 1


if __name__ == "__main__":
    raise SystemExit(main())
