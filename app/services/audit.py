from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..config import settings
from ..schemas import AuditEntry


def write(entry: AuditEntry) -> None:
    """تسجيل كل رد (JSONL) للتدقيق الطبي والإصدارات."""
    path = Path(settings.audit_log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = json.loads(entry.model_dump_json())
    record["created_at"] = datetime.now(timezone.utc).isoformat()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
