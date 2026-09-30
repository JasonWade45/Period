# تقرير التسليم — توسعة البريف (المرحلة أ: قاعدة المعرفة/RAG/التقييم + المرحلة ب: التعريب الكامل)

**الفرع:** `arena/01a0efe1-period` · **آخر إيداع:** `ee5bdf2` · **طلب السحب:**
https://github.com/JasonWade45/Period/pull/new/arena/01a0efe1-period

**حالة الاختبارات:** 572 اختبارًا ناجحًا (`pytest`) · **حزمة التقييم:** 26/26
سؤالًا للوكلاء، وبوّابة الطوارئ/المصائد سليمة · **مدقّقات:** `check_i18n` و
`check_glossary` و`check_rtl` و`check_prompt` كلها بلا أخطاء · **التشغيل الحقيقي:**
`/api/v1/ai/chat` بلا مصدر معتمد ⇒ `no_source` بلا نداء موديل، ورسالة طوارئ ⇒
`emergency_filter` برقم 123، و`/health` يقول الحقيقة (`chunks_citable=0`,
`chunks_text_removed=6`).

---

## 1) ما تم تسليمه

### المرحلة أ — قاعدة المعرفة والاسترجاع والواجهة والتقييم

| المكوّن | المسار | الحالة |
|---|---|---|
| مخطط pgvector + HNSW + GIN + TSVECTOR | `migrations/001_kb_pgvector.sql` | ✅ مكتوب — ⚠️ غير مُشغَّل هنا (لا PostgreSQL في البيئة) |
| تنفيذ PostgreSQL | `app/kb/postgres.py` | ✅ مكتوب — ⚠️ غير مُشغَّل |
| تنفيذ SQLite (تطوير/اختبار) | `app/kb/store.py` | ✅ مُختبَر (41 اختبارًا) |
| دورة حياة 4 حالات + جدول انتقالات | `app/kb/schemas.py` | ✅ مُختبَر |
| التضمين (bge-m3 / e5-large) | `app/kb/embedding.py` | ✅ الواجهة مُختبَرة، ⚠️ تنزيل النموذج مستحيل هنا |
| استرجاع هجين RRF | `app/kb/retrieval.py` | ✅ مُختبَر (k=60، 20+20 → 6، عتبتان) |
| استيراد ببوابة رخصة + idempotency | `app/kb/ingest.py` | ✅ مُختبَر (تغيّر المحتوى يعيد للمراجعة ويمسح المتجه) |
| مراجعة بشرية CLI | `app/kb/review.py` | ✅ مُختبَر |
| `POST /api/v1/ai/chat` + `/summary` + `/health` | `app/routers/ai.py`, `app/services/ai_pipeline.py` | ✅ مُختبَر (27 اختبارًا) |
| حزمة التقييم | `app/eval/run.py`, `app/eval/judge.py`, `eval/eval_questions_seed.jsonl` | ✅ مُختبَر (11 اختبارًا) |
| تطبيع عربي موحّد | `app/services/arabic.py` | ✅ مُختبَر ومُستخدَم في 4 طبقات |

### المرحلة ب — التعريب

| المكوّن | المسار | الحالة |
|---|---|---|
| ملفات الموارد (99 مفتاحًا متكافئًا) | `locales/ar.json`, `locales/en.json` | ✅ |
| جمع عربي CLDR + تنسيق أرقام/تواريخ | `app/i18n/plural.py`, `app/i18n/format.py` | ✅ مُختبَر |
| CSV (UTF-8+BOM) وPDF عربي (تشكيل+bidi) | `app/services/export_ar.py` | ✅ تحقّق بصري فعلي |
| إشعارات لا تكشف شيئًا على شاشة القفل | `app/services/notifications.py` | ✅ مُختبَر بحاجز يرفض التسريب |
| المصطلحات كمصدر وحيد | `knowledge/glossary_ar.csv` + `tools/check_glossary.py` | ✅ |
| تدقيق الترجمة/RTL/النصوص المكتوبة في الكود | `tools/check_i18n.py`, `tools/check_rtl.py` | ✅ |
| الواجهة: RTL + تبديل لغة + عزل الأرقام | `frontend/i18n.js`, `frontend/rtl.css`, `frontend/app.js` | ✅ فحوص ساكنة، ⚠️ اللقطات تحتاج متصفحًا |
| اختبار لقطات RTL جاهز | `frontend/tests/rtl.spec.js` | ⚠️ يحتاج تنزيل Chromium (محجوب هنا) |
| صفحتا المتجر | `store/ar.md`, `store/en.md` | ✅ |

---

## 2) أوامر التشغيل

```bash
# قاعدة المعرفة
python -m app.kb.ingest --path knowledge/knowledge_seed.example.jsonl \
    --registry knowledge/sources_registry.example.json --dry-run
python -m app.kb.review list --status draft_unreviewed
python -m app.kb.review set-status <id> physician_reviewed --reviewer "د. ..." --date 2026-10-05

# الواجهة الجديدة
uvicorn app.main:app --host 0.0.0.0 --port 8113
#   POST /api/v1/ai/chat | POST /api/v1/ai/summary | GET /api/v1/ai/health

# حزمة صاحبة المشروع (kb/): فحص ما وصل ثم استيراد المسودات
python -m app.kb.pack check
python -m app.kb.pack ingest --db data/kb.db

# التقييم (غير صفري عند فشل أي مصيدة أو طوارئ) — تقريران منفصلان
python -m app.eval.compare
python -m app.eval.run --set eval/eval_questions_seed.jsonl
python -m app.eval.run --live --judge auto

# التدقيق
python tools/check_i18n.py && python tools/check_glossary.py && python tools/check_rtl.py
python tools/check_glossary.py --report audit/glossary_pending_review.json
```

---

## 3) النصوص التي تحتاج مراجعة طبيبة/مترجم قبل الإطلاق

القائمة الكاملة في `locales/needs_review.json`، وأهمّها:

1. **ردود الطوارئ والأزمات** (`emergency.*`) — نقلت من الكود إلى ملف الموارد بنفس
   النص حرفيًا، ولم تُترجم آليًا. النص الإنجليزي ترجمة أولية تحتاج اعتمادًا.
2. **حدود الإجابة** (`answer.no_reliable_source`, `answer.fallback`,
   `answer.not_configured`) — هذه الجمل تحدد ما يقوله المنتج حين لا يعرف.
3. **إخلاء المسؤولية** (`app.not_a_diagnosis`, `pdf.intro`, عناوين نوافذ الطوارئ).
4. **19 مصطلحًا** في `knowledge/glossary_ar.csv` بعمود `needs_review=true`.
5. **النصوص التثقيفية** في `knowledge/knowledge_seed.example.jsonl` (7 مقاطع
   كتبت داخليًا كقالب توضيحي — لم تُراجَع طبيًا ولا رخصتها مثبتة، ولذلك
   `approved_for_ingest=false` فلا تُستَرجَع في الإنتاج).
6. **صياغة اللمسات/الترحيب** — لم يُحسم بعد: فصحى أم عامية مصرية؟ (سؤال مفتوح)

> لا ترجمة آلية صامتة: أي نص طبي/أمني جديد يُضاف إلى `needs_review.json` أولًا.

---

## 4) قيم الإعدادات التي تحتاج تحققًا قبل الإطلاق

| المتغيّر | القيمة | الحالة |
|---|---|---|
| `EMERGENCY_NUMBER` | `123` | ✅ مُتحقَّق منه في الجدول (مصر) |
| `POLICE_NUMBER` | `122` | ⚠️ من البريف — يحتاج تأكيدًا رسميًا |
| `UNIFIED_EMERGENCY` | `112` | ⚠️ **غير مؤكَّد**؛ `UNIFIED_EMERGENCY_VERIFIED=0` وموسوم في `.env.example` |
| `HEALTH_HOTLINE` | `105` | ⚠️ من البريف — يحتاج تأكيدًا |
| `CHILD_HELPLINE` | `16000` | ⚠️ من البريف — يحتاج تأكيدًا |
| `CRISIS_LINE` | **فارغ** | ⚠️ القيمة `08008880700` مخزّنة كـ`crisis_line_pending_verification` في الجدول فقط. **قرار مقصود:** رقم دعم نفسي ميت في لحظة أزمة أسوأ من عدم ذكره؛ يُفعَّل بعد التأكد أنه يعمل |
| `KB_EMBEDDING_MODEL` | `BAAI/bge-m3` | ⚠️ لم تُجرَ المقارنة مع `multilingual-e5-large` (تنزيل النماذج محجوب) |

---

## 5) أسئلة مفتوحة (تحتاج قرارًا بشريًا)

1. **مقاطع NHS/ACOG/WHO الستة القائمة:** وُصفت سابقًا بأنها صياغة عامة نُسبت إلى
   جهات لم تُراجعها. قرار التسوية المقترح: تخفيضها إلى `draft_unreviewed` حتى
   تُراجَع، فيبقى «صفر مقاطع معتمدة» ويُختبر معيار «لا مصدر موثوق» حرفيًا.
   **لم أُنفّذ التخفيض بعد** — ينتظر موافقتك.
2. **الطبيبة المراجعة:** اسم/بيانات المراجعة (يُطلب في كل `set-status`).
3. **تراخيص المصادر:** كل مصدر في `sources_registry` يحتاج قرار `licence` بشريًا.
   لا شيء مُعتمد حاليًا (`approved_for_ingest=false` للجميع).
4. **صياغة اللمسات:** فصحى أم عامية مصرية في الترحيب والتلميحات؟
5. **النصوص القانونية:** عناصر نائبة فقط حتى يعتمدها مستشار قانوني.
6. **نموذج التضمين النهائي** بعد تشغيل المقارنة في بيئة فيها شبكة.

---

## 6) حدود المكتبات والبيئة (ما لم يُتحقَّق منه، ولماذا)

| الحدّ | السبب | الأثر على التسليم |
|---|---|---|
| PostgreSQL/pgvector | لا حزم ولا مستودعات شبكة (`deb.debian.org` محجوب) | `migrations/001_kb_pgvector.sql` و`app/kb/postgres.py` مرجعان مكتوبان للمراجعة، دلالاتهما مطابقة لتنفيذ SQLite المُختبَر. **يجب** تشغيل `pytest -m postgres` بعد ضبط `DATABASE_URL` قبل النشر |
| نموذج التضمين | `huggingface.co` محجوب | الواجهة جاهزة (`sentence-transformers`)، والاختبارات تعمل بمحوّل حتمي محلي. المقارنة bge-m3 ↔ e5-large تُنفَّذ في بيئة متصلة |
| التوليد الحقيقي | `api.groq.com` محجوب | كل اختبارات خط الأنابيب بموديل مُحاكى (`RecordingLLM`)؛ `--live` جاهز للتشغيل في بيئة متصلة |
| PDF عربي | reportlab بلا HarfBuzz | الناتج مُتحقَّق بصريًا (تشكيل + RTL + محاذاة يمين + سطر 1.7). يقصّر في الاتجاه المختلط المعقّد؛ البديل WeasyPrint عند الحاجة |
| الخطوط | لا IBM Plex Sans Arabic / Noto Sans Arabic في البيئة (وحقوق الخطوط) | المُصدِّر يبحث عن الخط المطلوب أولًا (`PDF_ARABIC_FONT_PATH` أو `assets/fonts/`)، واستخدم في الاختبار خط نظام عربي. **يجب** إضافة خط معتمد ومراجعة الشكل بصريًا قبل النشر |
| لقطات RTL | تنزيل Chromium لـPlaywright محجوب | `frontend/tests/rtl.spec.js` جاهز للـCI + `tools/check_rtl.py` يغطي ما يمكن فحصه ساكنًا |
| شبكة الأزمات (مساعد طبي حقيقي) | لا يمكن قياس جودة الإجابات بلا موديل | الفحوص الحتمية هي الحاكم، ونتائج الحكم موسومة `verified_locally=false` |

---

## 7) معايير القبول — أين تُثبت

| المعيار | الإثبات |
|---|---|
| صفر معتمد + `KB_ALLOW_DRAFT=false` ⇒ «لا مصدر موثوق» بلا استدعاء موديل | `tests/test_ai_pipeline.py::test_no_approved_chunks_returns_no_source_answer` + تشغيل حقيقي على `/api/v1/ai/chat` |
| كل اختبارات الطوارئ/المصائد تمرّ، والطوارئ لا تصل للموديل | `eval` بوّابة `gating_failures` + 11 اختبار طوارئ (`llm_skipped`) |
| كل إجابة تحمل `sources_used` من المعرّفات المسترجَعة | `test_answer_uses_only_retrieved_chunk_ids` + رفض موديل يستشهد بمصدر غير مسترجَع |
| إعادة الاستيراد لا تغيّر حالة مقطع غير متغيّر | `tests/test_kb_core.py::test_reingest_same_content_does_not_change_status` (+ اختلاف تشكيل/أرقام عربية) |
| لا أسرار في التطبيق ولا PII في السجلات/طلبات الموديل | مسارات AI لا تخزّن نص الرسالة (`test_audit_entry_has_no_message_text_and_keeps_chunk_ids`)، ومفتاح الـLLM خادمي فقط |
| التطبيق عربي افتراضيًا وRTL صحيح | `check_rtl` + `check_i18n` + اختبارات الجمع/التواريخ/الأرقام + PDF مُتحقَّق بصريًا |
| الرقم لا يُقلب في RTL | عزل `phone-number` في CSS واختبار Playwright + فحص ساكن |
| لا نص طبي مترجم آليًا بصمت | `locales/needs_review.json` + `check_glossary` يفشل على أي صيغة غير معتمدة |

---

## 8) المستجدّ في هذه الجولة (2026-09-30)

### 8.1 المقاطع الستة المُتحقَّقة: إزالة النص وتخفيض الحالة

**من أين جاء النص؟** فحص `git` يحسم المسألة:

- `git show 096c40a:app/data/sources.json` — النصوص موجودة في **أول كوميت في
  المستودع** (قبل أي عمل للوكيل). أي أنها جاءت مع المستودع نفسه، لا نقلها وكيل.
- الفرق `096c40a → 7350e61` يُظهر أن ما أضافه الوكيل هو الحالة والمراجع
  (`status=verified`, `reviewer`, `reviewed_at`) فقط، والنصوص لم تُمسّ.
- صياغة النصوص تتبع صفحات NHS/ACOG/WHO المذكورة في أسمائها، والتحقق الحرفي
  ممكن فقط بمقارنة أصلية (لا شبكة في هذه البيئة) ⇒ تُعامَل كمنقولة احتياطًا.

**ما نُفِّذ** (تطبيقًا لتعليمك: «إن كان أي نص منسوخًا … أخفضي الحالة إلى
`draft_unreviewed` وأزيلي النص حتى تأكيد الترخيص»):

- `app/data/sources.json` أصبح `[]` — **صفر مقاطع قابلة للاستشهاد**.
- المقاطع الستة انتقلت إلى `app/data/sources_draft.json` بحالتها المسودّة، مع
  `text_removed=true` و`attribution_unverified=true`، ونصّها استُبدل بملاحظة
  إدارية تشرح سبب الإزالة. **السجل محفوظ** لا محذوف، حتى يُراجَع ويرجع بعد
  التحقق من الأصل والترخيص.
- `source_name` صار «مسودة داخلية غير مُراجَعة طبيًا» بدل NHS/ACOG/WHO: نسبَة
  النص إلى جهة لم تُراجعه = إسناد زائف مرفوض في الكود (`
  test_draft_source_name_does_not_impersonate_a_body`).
- مقاطع مُزالة النص **لا تُسترجع أبدًا**، حتى لو فُعِّل `KNOWLEDGE_INCLUDE_DRAFTS`
  (`RagRetriever.text_removed_chunks`)، و`/health` يعرض `chunks_text_removed`.

**الأثر المقصود:** بلا مصدر معتمد لا إجابة من معرفة عامة — الرد صريح: «لا أملك
مصدرًا موثوقًا معتمدًا»، بلا استدعاء للموديل، في المسارين (القديم `/v1/*`
والجديد `/api/v1/ai/*`). هذا مُختبَر في `tests/test_knowledge_base.py`.

### 8.2 حزمة `kb/`: الكود جاهز، والمحتوى لم يصل

| محاولة التسليم | ما تحقق آليًا |
| --- | --- |
| «في `/home/user/uploads`» | المجلد غير موجود |
| «ملصقة أدناه كنص» | الرسالة حملت التعليمات فقط؛ لا نص الحزمة |

لا وجود لـ`kb/` ولا لأي من الأسماء الأربعة في أي مسار. لذلك **لم يُخترع أي
محتوى**، وبُني بدلًا من ذلك المسار الكامل الذي يستقبل الحزمة (`app/kb/pack.py`):

- الثوابت الإلزامية تُفرض في الكود ولا تُقرأ من الملفات: `draft_unreviewed` +
  `authored_by="ai_draft"` + `reviewed_by/reviewed_at = null` + ملاحظة الترخيص
  الحرفية لكل مقطع بذرة؛ `needs_physician_review` لكل سؤال تقييم.
- `approved_for_ingest` **لا يُقلب إلى `true` من هذا المسار أبدًا**، وأي `true`
  في ملف السجل يُسجَّل ويُبقى `false` (بوابة الرخصة تبقى موقوفة حتى قرار بشري).
- أي ادّعاء مراجعة داخل ملف الاستيراد يُلغى ويُبلَّغ عنه (`ignored_claims`).
- تقريران منفصلان دائمًا: `python -m app.eval.compare` يطبع «أسئلة الوكلاء
  (26)» و«أسئلة صاحبة المشروع (25)» منفصلين، مع تقريرين على القرص
  (`report-agent-*.json`, `report-user-*.json`). اليوم يطبع:
  `أسئلة الوكلاء 26/26 (1.0)` و`أسئلة صاحبة المشروع: لم تُقَس — الملف غير موجود`.
- **الاستيراد بأمر واحد لحظة الوصول:** `python -m app.kb.pack ingest --db
  data/kb.db` يُدخل المقاطع الـ22 كمسودات `draft_unreviewed` (لا شيء قابل
  للاستشهاد، والمراجع محفوظة في `source_refs_to_verify`)، وإعادة الاستيراد لا
  تُلغي أي اعتماد طبي لاحق. وللنصوص المنسوخة من مصادر خارجية مسار صارم منفصل:
  `--require-approved-source`.
- وصف صيغ الملفات الأربعة وثوابتها في `kb/README.md` (الملفات نفسها لم تُنشأ).
- القاموس: `tools/check_glossary.py` يعطي أولوية تلقائية لـ
  `kb/knowledge/glossary_ar.csv` فور وصوله، وعندها يصبح هو مصدر الحقيقة.

**ما ينتظرك:** لصق محتوى الملفات الأربعة كما هو في المحادثة. لا نصوص «مُعاد
بناؤها»، ولا حفظ حتى تُستورد كمسودات وتُراجَع طبيًا.

---

## 9) سياق مهم عن ملفات `kb/` المذكورة في البريف

البريف أشار إلى حزمة مرفقات (`AGENT_BRIEF.md`, `cyclecare-system-prompt.md`,
`knowledge/knowledge_seed.jsonl`, `knowledge/sources_registry.json`,
`knowledge/glossary_ar.csv`, `eval/eval_questions_seed.jsonl`, `ARABIC_REFERENCES.md`,
`BOOKS_AND_REFERENCES.md`) ولم تصل إلى بيئة العمل. لذلك:

- بُنيت **بنية** كل ملف بمساراته المتوقعة وبمخطط واضح، ومُلئت بقوالب
  توضيحية (`knowledge/knowledge_seed.example.jsonl`,
  `knowledge/sources_registry.example.json`).
- `knowledge/glossary_ar.csv` و`eval/eval_questions_seed.jsonl` كُتبا فعليًا
  (المصطلحات الـ25 والأسئلة الـ26) لأن البريف حدّد سلوكهما بدقة. **تصحيح
  للالتباس:** هذان ملفّان من عمل الوكلاء في `knowledge/` و`eval/`، وليسا
  ملفّي حزمة صاحبة المشروع في `kb/knowledge/glossary_ar.csv` و
  `kb/eval/eval_questions_seed.jsonl`. لا يُدمجان: تقرير الأسئلة الـ26 يبقى
  منفصلًا عن الـ25، وقاموس الحزمة يأخذ الأولوية تلقائيًا عند وصوله.
- لم يُستورد أي نص من الكتب/المراجع المذكورة (لا محتوى محمي بحقوق نشر)،
  والقرارات الجوهرية غير المحسومة مسجّلة في القسمين 4 و5 أعلاه.
