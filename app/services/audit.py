from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..config import settings
from ..schemas import AuditEntry


def write(entry: AuditEntry, extra: dict | None = None) -> None:
    """تسجيل كل رد (JSONL) للتدقيق الطبي والإصدارات.

    `extra` يحمل حقول مسارات /api/v1/ai: اللغة، النموذج، القرار (ok/no_source/
    emergency)، وطول الرسالة — **لا نص الرسالة**: التدقيق يحتاج المعرّفات
    والإصدارات لا محتوى المستخدمة.
    """
    path = Path(settings.audit_log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = json.loads(entry.model_dump_json())
    if extra:
        record.update(extra)
    record["created_at"] = datetime.now(timezone.utc).isoformat()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
