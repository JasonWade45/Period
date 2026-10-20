"""المكتبة التثقيفية وأوضاع الحياة (البند 3.6 و3.7 من برومبت البناء الرئيسي).

قاعدة صارمة غير قابلة للتفاوض:
- لا يُنشر ولا يُعرض أي مقال في الإنتاج إلا إذا كانت حالته `approved` ومرفقًا
  باسم المراجِعة الطبية (`reviewed_by`) وتاريخ المراجعة (`reviewed_at`) والمصدر.
- لا يظهر محتوى `draft_unreviewed` في الإنتاج أبدًا (`allow_draft=False` افتراضيًا).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .arabic import normalize_arabic


@dataclass(frozen=True)
class LibraryArticle:
  id: str
  title: str
  topic: str
  life_mode: str                   # track | ttc | pregnant | postpartum | perimenopause | all
  summary: str
  body: str
  status: str                      # يجب أن يكون "approved" للعرض في الإنتاج
  reviewed_by: str
  reviewed_at: str
  sources: list[str] = field(default_factory=list)

  def to_dict(self) -> dict[str, Any]:
    return asdict(self)


def filter_approved_articles(
    articles: list[dict[str, Any] | LibraryArticle],
    *,
    query: str = "",
    topic: str = "",
    life_mode: str = "",
    allow_draft: bool = False,
) -> list[dict[str, Any]]:
  """يُرجع فقط المقالات المعتمدة (`status == 'approved'`) التي تملك مراجِعة وتاريخ مراجعة."""
  norm_q = normalize_arabic(query).lower().strip() if query else ""
  clean_topic = (topic or "").strip().lower()
  clean_mode = (life_mode or "").strip().lower()

  out: list[dict[str, Any]] = []
  for item in articles or []:
    raw = item.to_dict() if isinstance(item, LibraryArticle) else dict(item)
    status = str(raw.get("status") or "").strip().lower()
    reviewed_by = str(raw.get("reviewed_by") or raw.get("reviewer") or "").strip()
    reviewed_at = str(raw.get("reviewed_at") or "").strip()

    if not allow_draft:
      # في الإنتاج: يُشترط status == "approved" ووجود اسم المراجِعة وتاريخ المراجعة
      if status != "approved" or not reviewed_by or not reviewed_at:
        continue
    else:
      if status == "retired" or raw.get("text_removed"):
        continue

    item_topic = str(raw.get("topic") or raw.get("section") or "general").strip()
    item_mode = str(raw.get("life_mode") or "all").strip().lower()

    if clean_topic and clean_topic != item_topic.lower():
      continue
    if clean_mode and item_mode not in ("all", clean_mode):
      continue

    title = str(raw.get("title") or raw.get("section") or "").strip()
    body = str(raw.get("body") or raw.get("content") or raw.get("text") or "").strip()
    if not body:
      continue

    if norm_q:
      haystack = normalize_arabic(f"{title} {item_topic} {body}").lower()
      if norm_q not in haystack:
        continue

    out.append(
        {
            "id": str(raw.get("id") or ""),
            "title": title,
            "topic": item_topic,
            "life_mode": item_mode,
            "summary": body[:160] + ("…" if len(body) > 160 else ""),
            "body": body,
            "status": status,
            "reviewed_by": reviewed_by,
            "reviewed_at": reviewed_at,
            "sources": list(raw.get("sources") or ([raw["source_name"]] if raw.get("source_name") else [])),
        }
    )
  return out
