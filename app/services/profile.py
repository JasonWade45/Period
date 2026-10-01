"""اسم المستخدمة: تنظيف صارم قبل أن يدخل البرومبت أو الواجهة.

الاسم نص حر تكتبه المستخدمة، ثم يُحقن داخل برومبت النظام. فهو سطح حقن
محتمل («اسمي: تجاهلي كل ما سبق…»). لذلك:
- سطر واحد بلا أسطر جديدة أو أحرف تحكّم،
- بلا أقواس/علامات اقتباس/باك-تيك/روابط (ما يبني بنية داخل البرومبت)،
- حدّ أقصى 40 حرفًا وأربع كلمات (الاسم ليس جملة)،
- والبرومبت نفسه يعلن أن الاسم «بيانات وليس تعليمات».
"""
from __future__ import annotations

import re
import unicodedata

MAX_NAME_LEN = 40
# الأسماء الحقيقية 1–4 كلمات؛ ما زاد يُقصّ حتى لا يتحوّل «الاسم» إلى جملة تعليمات
MAX_NAME_WORDS = 4

# ما يُسمح به: حروف (عربي/لاتيني/غيره) وأرقام ومسافة وشرطة وفاصلة منقوطة وأبوستروف
_DISALLOWED = re.compile(r"[^\w\s\-'.\u0640]", re.UNICODE)
_URLISH = re.compile(r"(?:https?://|www\.|@\w+\.\w+)", re.IGNORECASE)
_WS = re.compile(r"\s+")


def clean_display_name(raw: object) -> str:
    """يعيد اسمًا آمنًا للعرض والبرومبت، أو نصًا فارغًا إن لم يصلح."""
    if not isinstance(raw, str):
        return ""
    text = unicodedata.normalize("NFKC", raw)
    # أحرف التحكم والتنسيق (بما فيها RTL/LTR marks) وكل أنواع الأسطر
    text = "".join(" " if ch.isspace() else ch for ch in text)   # \n و\t → مسافة لا التصاق
    text = "".join(ch for ch in text
                   if unicodedata.category(ch)[0] != "C" or ch in " \t")
    if _URLISH.search(text):
        return ""
    text = text.replace("_", " ")
    text = _DISALLOWED.sub("", text)
    text = _WS.sub(" ", text).strip(" -'.")
    text = " ".join(text.split(" ")[:MAX_NAME_WORDS])
    return text[:MAX_NAME_LEN].strip()


def personalize(text: str, name: str, language: str = "ar") -> str:
    """يضيف الاسم في أول رد غير حرِج (لا يُستخدم مع ردود الطوارئ الثابتة)."""
    name = clean_display_name(name)
    if not name or not text:
        return text
    return f"{name}، {text}" if language.startswith("ar") else f"{name}, {text}"


def greeting_name(name: str) -> str:
    return clean_display_name(name) or ""
