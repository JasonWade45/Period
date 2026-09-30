"""خادم Groq وهمي متوافق مع OpenAI — للاختبار المحلي فقط.

الغرض: تشغيل المسار الكامل (prompt → HTTP → SDK → validator → audit) بدون
شبكة وبدون مفتاح حقيقي، واختبار حالات الفشل التي يصعب إحداثها مع مزوّد حقيقي
(JSON غير صالح، إسناد ممنوع، معرّف مصدر مجهول، 429، 500).

لا يُستخدم في الإنتاج بأي حال: لا يعرف شيئًا عن الطب ولا يمثّل أي نموذج.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

_CHAT_COMPLETIONS = "/chat/completions"

_MODE_RE = re.compile(r"المسار الحالي|MODE`?\s*الحالي:[^`]*`?(\w+)?")


def _mode_from_prompt(system: str) -> str:
    """يستخرج MODE من الـ prompt المرسوم."""
    m = re.search(r"الحالي:\s*`([a-z]+)`", system)
    return m.group(1) if m else "chat"


@dataclass
class FakeGroq:
    """خادم وهمي قابلة للتوجيه بسيناريوهات.

    scenarios: طابور من الردود. كل عنصر إما:
      - {"status": 200, "content": "<نص الرد>"}  → رد ناجح
      - {"status": 200, "json": {...}}            → رد ناجح بمحتوى JSON
      - {"status": 429} / {"status": 500}          → خطأ من المزوّد
      - {"default": True}                          → سلوك افتراضي (رد صالح حسب MODE)
    """

    scenarios: list[dict[str, Any]] = field(default_factory=list)
    port: int = 0  # 0 = منفذ حر يختاره النظام
    requests: list[dict[str, Any]] = field(default_factory=list)
    _server: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> "FakeGroq":
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # امنعي ضجيج الخادم في الاختبارات
                pass

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                path = self.path.split("?")[0]

                if not path.endswith(_CHAT_COMPLETIONS):
                    self._send(404, {"error": {"message": "unknown path"}})
                    return

                if "authorization" not in {k.lower() for k in self.headers.keys()}:
                    self._send(401, {"error": {"message": "missing api key"}})
                    return

                messages = body.get("messages") or []
                system = next((m.get("content", "") for m in messages
                               if m.get("role") == "system"), "")
                outer.requests.append({
                    "path": path,
                    "model": body.get("model"),
                    "temperature": body.get("temperature"),
                    "max_tokens": body.get("max_tokens"),
                    "response_format": body.get("response_format"),
                    "messages": messages,
                    "system": system,
                    "mode": _mode_from_prompt(system),
                    "user": next((m.get("content", "") for m in messages
                                  if m.get("role") == "user"), ""),
                })

                scenario = outer.scenarios.pop(0) if outer.scenarios else {"default": True}
                status = scenario.get("status", 200)
                if status != 200:
                    self._send(status, {"error": {"message": f"fake provider error {status}"}})
                    return

                if "content" in scenario:
                    content = scenario["content"]
                else:
                    content = _default_content(outer.requests[-1]["mode"])
                self._send(200, {
                    "id": "chatcmpl-fake",
                    "object": "chat.completion",
                    "created": 0,
                    "model": body.get("model", "fake"),
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                })

            def _send(self, status: int, payload: dict) -> None:
                raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        # poll_interval الافتراضي 0.5s يضيف نصف ثانية لكل اختبار عند الإيقاف
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self) -> "FakeGroq":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # ------------------------------------------------------------------ helpers
    @property
    def base_url(self) -> str:
        assert self._server is not None, "start the fake server first"
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def call_count(self) -> int:
        return len(self.requests)

    @property
    def last_system_prompt(self) -> str:
        return self.requests[-1]["system"] if self.requests else ""


def _default_content(mode: str) -> str:
    """رد JSON صالح يحاكي شكل الإخراج المطلوب — محتوى عام غير طبي بالمعنى الدقيق."""
    if mode == "summary":
        return json.dumps({
            "overview": "ملخص تجريبي للبيانات المسجّلة في التطبيق.",
            "what_changed": "لا تغييرات جوهرية في الأرقام المسجّلة.",
            "patterns": "النمط المذكور أعلاه مأخوذ من بياناتك المسجّلة فقط.",
            "medical_alerts": "راجع ما ورد أعلاه.",
            "what_this_does_not_mean": "هذا وصف لبياناتك المسجّلة وليس تشخيصًا.",
            "questions_for_doctor": [
                "هل أحتاج فحوصات إضافية؟",
                "هل نمط دوراتي يستدعي متابعة؟",
                "متى أراجع مرة أخرى؟",
            ],
            "sources_used": [],
        }, ensure_ascii=False)

    return json.dumps({
        "answer": "هذه إجابة تجريبية قصيرة من خادم وهمي، ولا تحتوي معلومات طبية.",
        "sources_used": [],
        "needs_doctor": False,
        "emergency": False,
        "crisis": False,
        "missing_info": [],
    }, ensure_ascii=False)
