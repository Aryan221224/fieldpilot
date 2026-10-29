"""Live label check via Tavily; writes tavily_probe_log.txt with citation, calls, latency."""
import sys, time, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fieldpilot import config, label

key = config.os.environ.get("TAVILY_API_KEY", "")
calls = []
orig = label.tavily_http
def counting(k):
    h = orig(k)
    def wrapped(*a, **kw):
        t = time.time(); r = h(*a, **kw); calls.append(round(time.time() - t, 2)); return r
    return wrapped
label.tavily_http = counting
out = []
for product, crop, pest, dose in [("Mancozeb 75% WP", "potato", "late blight", 1500),
                                  ("Mancozeb 75% WP", "potato", "late blight", 3000)]:
    calls.clear(); t0 = time.time()
    v = label.verify(product, crop, pest, dose, tavily_key=key)
    out.append({"product": product, "proposed_per_ha": dose, "ok": v.ok, "reason": v.reason,
                "claim": str(v.claim), "citations": v.citations, "tavily_calls": len(calls),
                "call_secs": list(calls), "total_secs": round(time.time() - t0, 2)})
(ROOT / "tavily_probe_log.txt").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
print(json.dumps(out, indent=2, default=str))
