"""خادم Groq وهمي مستقل — للتطوير والعرض بدون مفتاح أو شبكة.

    python tools/fake_groq_server.py --port 8300

ثم في نافذة أخرى:

    GROQ_API_KEY=dummy GROQ_BASE_URL=http://127.0.0.1:8300 \\
        uvicorn app.main:app --port 8113

بهذا يعمل المسار الكامل (prompt → HTTP → SDK → validator → audit) بردود
صيغتها صحيحة. الردود **ليست** إجابات طبية ولا تمثّل أي نموذج حقيقي:
الغرض اختبار الواجهة والصيغة ومسار السلامة، لا جودة المحتوى.

مفيد أيضًا لتجربة حالات الفشل يدويًا:
    --fail-first 2   أول طلبين يردّان 500 (ثم يظهر الرد الاحتياطي)
    --bad-json 2     أول طلبين يردّان نصًا غير JSON (لاختبار إعادة المحاولة)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.fake_groq import FakeGroq  # noqa: E402


def build_scenarios(fail_first: int, bad_json: int) -> list[dict]:
    scenarios: list[dict] = []
    scenarios += [{"status": 500}] * fail_first
    scenarios += [{"content": "هذا نص وليس JSON."}] * bad_json
    return scenarios


def main() -> None:
    parser = argparse.ArgumentParser(description="Groq-compatible fake server (dev only)")
    parser.add_argument("--port", type=int, default=8300)
    parser.add_argument("--fail-first", type=int, default=0,
                        help="عدد الطلبات الأولى التي تردّ 500")
    parser.add_argument("--bad-json", type=int, default=0,
                        help="عدد الطلبات الأولى التي تردّ نصًا غير JSON")
    args = parser.parse_args()

    server = FakeGroq(port=args.port, scenarios=build_scenarios(args.fail_first, args.bad_json))
    server.start()
    print(f"fake Groq listening on {server.base_url}")
    print("استخدمي: GROQ_API_KEY=dummy GROQ_BASE_URL=" + server.base_url)
    try:
        server._thread.join()  # type: ignore[union-attr]
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        print("\nstopped. calls served:", server.call_count)


if __name__ == "__main__":  # pragma: no cover
    main()
