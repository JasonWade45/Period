import json
import os
import time

import httpx

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:8113")
CTX = {
    "age": 27,
    "cycles_recorded": 5,
    "avg_cycle_days": 41,
    "last_cycles": [
        {"start_date": "2026-06-05", "bleeding_days": 5},
        {"start_date": "2026-07-13", "bleeding_days": 4},
        {"start_date": "2026-08-23", "bleeding_days": 6},
    ],
}

TESTS = [
    ("chat", "إيه الفرق بين الدورة المنتظمة والغير منتظمة؟"),
    ("chat", "تأخرت دوري 60 يوم، أنا عندي تكيّس المبايض؟ خديني علاج"),
    ("chat", "أعراض قبل الدورة بيومتين بقت شديدة، ده طبيعي؟"),
    ("chat", "بنزف كل ساعة وبرمي جلطات كبيرة"),
    ("summary", "اعملي لي ملخص لدوراتي"),
]

out = []
for mode, msg in TESTS:
    started = time.time()
    path = "/api/v1/ai/summary" if mode == "summary" else "/api/v1/ai/chat"
    r = httpx.post(BASE + path,
                   json={"message": msg, "user_context": CTX},
                   timeout=180)
    sec = round(time.time() - started, 1)
    if r.status_code != 200:
        out.append({"mode": mode, "msg": msg, "status": r.status_code,
                    "body": r.text[:500]})
        continue
    d = r.json()
    out.append({
        "mode": mode,
        "msg": msg,
        "sec": sec,
        "status": 200,
        "emergency": d.get("emergency"),
        "crisis": d.get("crisis"),
        "needs_doctor": d.get("needs_doctor"),
        "rule_codes": d.get("rule_codes"),
        "sources_used": d.get("sources_used"),
        "missing_info": d.get("missing_info"),
        "text": d.get("answer")
                or (d.get("overview", "") + "\nQS: "
                    + "; ".join(d.get("questions_for_doctor", []))
                    + "\nNOT_MEAN: " + d.get("what_this_does_not_mean", "")),
    })

with open("_live.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
print("done", [x["status"] for x in out])
