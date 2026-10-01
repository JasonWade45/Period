from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import settings
from ..schemas import AuditEntry


def _record_time(record: dict) -> datetime | None:
    """وقت السجل من حقل created_at (يكتبه write دائمًا) — None إن كان غائبًا."""
    raw = record.get("created_at")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


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


def _rewrite(path: Path, keep) -> int:
    """يعيد كتابة الملف بالسجلات المحفوظة فقط. يُرجع عدد السجلات المُزالة."""
    if not path.exists():
        return 0
    kept: list[str] = []
    removed = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            removed += 1
            continue
        if keep(record):
            kept.append(json.dumps(record, ensure_ascii=False))
        else:
            removed += 1
    if removed:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        tmp.replace(path)
    return removed


def purge_old(days: int | None = None, *, now: datetime | None = None) -> int:
    """يزيل السجلات الأقدم من مدة الاحتفاظ (AUDIT_RETENTION_DAYS). يُرجع المُزالة.

    السجل بلا created_at يبقى (لا نُسقط سجلًا لعجز عنه وقت بل نخطئ في الحفظ).
    """
    retention = settings.audit_retention_days if days is None else days
    if retention <= 0:
        return 0
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention)
    return _rewrite(
        Path(settings.audit_log_path),
        lambda record: (_rt := _record_time(record)) is None or _rt >= cutoff,
    )


def purge_user(user_id: str) -> int:
    """يزيل كل سجلات مستخدمة واحدة (بصمة user_id) — لحق الحذف.

    البصمة نفسها التي يكتبها المسار: key_fingerprint(user_key). لا نستطيع
    التراجع عن بصمة، لذا الحذف يعيد حسابها من المفتاح ثم يمسح المطابق.
    """
    if not user_id:
        return 0
    return _rewrite(Path(settings.audit_log_path),
                    lambda record: record.get("user_id") != user_id)


def _main(argv: list[str] | None = None) -> int:
    """أمر يدوي: python -m app.services.audit purge --days 90 [--user-key K]"""
    parser = argparse.ArgumentParser(description="تنظيف سجل التدقيق")
    parser.add_argument("command", choices=["purge"])
    parser.add_argument("--days", type=int, default=None,
                        help="مدة الاحتفاظ بالأيام (الافتراضي AUDIT_RETENTION_DAYS)")
    parser.add_argument("--user-key", default="",
                        help="حذف سجلات مفتاح/جهاز محدد بعد بصمته")
    parser.add_argument("--dry-run", action="store_true",
                        help="إظهار العدد دون كتابة أي تغيير")
    args = parser.parse_args(argv)

    if args.user_key:
        from .security import key_fingerprint
        target = key_fingerprint(args.user_key)
        if not target:
            print("user-key فارغ", file=sys.stderr)
            return 2
        path = Path(settings.audit_log_path)
        if args.dry_run:
            count = sum(
                1 for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and json.loads(line).get("user_id") == target)
            print(f"dry-run: {count} سجل للمصمة {target}")
            return 0
        print(f"أُزيل {purge_user(target)} سجل للمصمة {target}")
        return 0

    retention = settings.audit_retention_days if args.days is None else args.days
    if args.dry_run:
        path = Path(settings.audit_log_path)
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention)
        count = 0
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                when = _record_time(record)
                if when is not None and when < cutoff:
                    count += 1
        print(f"dry-run: {count} سجل أقدم من {retention} يومًا")
        return 0
    print(f"أُزيل {purge_old(retention)} سجلًا (الاحتفاظ {retention} يومًا)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
