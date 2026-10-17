"""خوارزمية التنبؤ بنطاق للدورة الشهرية (Range-Based Cycle Prediction).

المبادئ الطبية والهندسية (البند 3.2 من برومبت البناء الرئيسي):
1. لا يُعرض تاريخ واحد مطلق أبدًا؛ التوقع دائمًا نطاق («بين X وY»).
2. الخوارزمية: متوسط مرجّح بالأحدث (Weighted Moving Average) + انحراف معياري
   (Standard Deviation)، وتستبعد القيم الشاذة (> 400 يوم أو < 1 يوم كخطأ إدخال).
3. تشترط وجود 3 دورات مسجّلة على الأقل، وإلا تُرجع `insufficient_data` صراحةً.
4. تُرفق مع كل توقع درجة الثقة (`high` / `medium` / `low`) والأساس الرقمي
   (`basis`: عدد الدورات، المتوسط المرجّح، الانحراف المعياري) مع تصريح واضح
   أنه تقدير تقويمي ولا يُعتمد عليه كوسيلة لمنع الحمل أو تأكيد الإباضة.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

MIN_CYCLES_REQUIRED = 3
MAX_VALID_GAP_DAYS = 400


@dataclass(frozen=True)
class PredictionBasis:
    cycles_recorded: int
    valid_gaps_count: int
    weighted_avg_days: float | None = None
    std_dev_days: float | None = None
    min_gap_days: int | None = None
    max_gap_days: int | None = None
    spread_days: int | None = None
    last_start_date: str | None = None


@dataclass(frozen=True)
class CyclePrediction:
    status: str                           # "ok" | "insufficient_data"
    confidence: str                       # "high" | "medium" | "low" | "none"
    window_start: str | None = None       # YYYY-MM-DD (بداية النطاق المتوقع)
    window_end: str | None = None         # YYYY-MM-DD (نهاية النطاق المتوقع)
    fertile_window_start: str | None = None
    fertile_window_end: str | None = None
    ovulation_window_start: str | None = None
    ovulation_window_end: str | None = None
    basis: PredictionBasis = field(
        default_factory=lambda: PredictionBasis(cycles_recorded=0, valid_gaps_count=0)
    )
    contraception_Run_warning: bool = True

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if isinstance(d.get("basis"), dict):
            d["basis"]["cycles_used"] = d["basis"].get("cycles_recorded", 0)
            d["basis"]["weighted_mean_days"] = d["basis"].get("weighted_avg_days")
        return d


def extract_chronological_gaps(cycles: list[dict[str, Any]]) -> tuple[list[date], list[int]]:
    """يرتّب تواريخ البداية تصاعديًا ويستخرج الفروق الصالحة (1..400 يوم)."""
    unique_dates: set[date] = set()
    for c in cycles or []:
        raw = str(c.get("start_date") or "").strip()
        if not raw:
            continue
        try:
            unique_dates.add(date.fromisoformat(raw[:10]))
        except ValueError:
            continue

    ordered = sorted(unique_dates)
    gaps: list[int] = []
    for earlier, later in zip(ordered, ordered[1:]):
        delta = (later - earlier).days
        if 1 <= delta <= MAX_VALID_GAP_DAYS:
            gaps.append(delta)
    return ordered, gaps


def _weighted_mean_and_std(gaps: list[int]) -> tuple[float, float]:
    """يحسب المتوسط المرجّح بالأحدث والانحراف المعياري على آخر 6 فجوات."""
    recent = gaps[-6:]
    n = len(recent)
    weights = [float(i + 1) for i in range(n)]   # الأحدث يأخذ وزنًا أعلى
    total_w = sum(weights)
    w_mean = sum(g * w for g, w in zip(recent, weights)) / total_w

    if n < 2:
        return round(w_mean, 1), 2.0
    variance = sum(w * ((g - w_mean) ** 2) for g, w in zip(recent, weights)) / total_w
    std_dev = math.sqrt(max(0.0, variance))
    return round(w_mean, 1), round(std_dev, 1)


def predict_next_cycle_window(cycles: list[dict[str, Any]]) -> CyclePrediction:
    """يحسب نطاق الدورة القادمة ونافذة الخصوبة التقديرية بدرجة ثقة وأساس رقمي."""
    ordered_dates, gaps = extract_chronological_gaps(cycles)
    cycles_count = len(ordered_dates)
    last_iso = ordered_dates[-1].isoformat() if ordered_dates else None

    if cycles_count < MIN_CYCLES_REQUIRED or len(gaps) < (MIN_CYCLES_REQUIRED - 1):
        return CyclePrediction(
            status="insufficient_data",
            confidence="none",
            basis=PredictionBasis(
                cycles_recorded=cycles_count,
                valid_gaps_count=len(gaps),
                last_start_date=last_iso,
            ),
        )

    w_avg, std_dev = _weighted_mean_and_std(gaps)
    recent_gaps = gaps[-6:]
    min_gap = min(recent_gaps)
    max_gap = max(recent_gaps)
    spread = max_gap - min_gap

    # تحديد درجة الثقة بناءً على عدد الدورات وانتظامها
    if cycles_count >= 5 and spread <= 4 and std_dev <= 2.5:
        confidence = "high"
    elif cycles_count >= 3 and spread <= 9 and std_dev <= 5.0:
        confidence = "medium"
    else:
        confidence = "low"

    # نصف عرض النطاق: لا يقل عن يومين أبداً حتى لا يُفهم كتاريخ واحد مطلق
    half_width = max(2, min(7, int( math.ceil(std_dev) ) + 1))
    center_days = int(round(w_avg))
    last_date = ordered_dates[-1]

    win_start = last_date + timedelta(days=max(15, center_days - half_width))
    win_end = last_date + timedelta(days=center_days + half_width)

    # نافذة الإباضة والخصوبة التقديرية (المرحلة الأصفرية ~14 يومًا قبل النطاق المتوقع)
    ovul_start = win_start - timedelta(days=15)
    ovul_end = win_end - timedelta(days=13)
    fertile_start = ovul_start - timedelta(days=4)
    fertile_end = ovul_end + timedelta(days=1)

    return CyclePrediction(
        status="ok",
        confidence=confidence,
        window_start=win_start.isoformat(),
        window_end=win_end.isoformat(),
        fertile_window_start=fertile_start.isoformat(),
        fertile_window_end=fertile_end.isoformat(),
        ovulation_window_start=ovul_start.isoformat(),
        ovulation_window_end=ovul_end.isoformat(),
        basis=PredictionBasis(
            cycles_recorded=cycles_count,
            valid_gaps_count=len(gaps),
            weighted_avg_days=w_avg,
            std_dev_days=std_dev,
            min_gap_days=min_gap,
            max_gap_days=max_gap,
            spread_days=spread,
            last_start_date=last_iso,
        ),
    )
