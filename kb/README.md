# حزمة المعرفة `kb/` — الوصف التقني للملفات الأربعة

هذا المجلد مخصّص لملفات صاحبة المشروع. **لا يوجد فيه أي محتوى مُخترع**: تصل
الملفات كما هي، ويستقبلها الكود بالمسار الآمن الموصوف أدناه.

```
kb/
├─ knowledge/knowledge_seed.jsonl      مقاطع معرفة مكتوبة آليًا (بذرة)
├─ eval/eval_questions_seed.jsonl      أسئلة تقييم صاحبة المشروع
├─ knowledge/glossary_ar.csv           قاموس المصطلحات — مصدر الحقيقة
└─ knowledge/sources_registry.json     سجل المصادر وحالة الاعتماد
```

## ما يحدث عند وصول الملفات (بلا أي تعديل كود)

```bash
python -m app.kb.pack check                                  # ماذا وصل وماذا ينقص
python -m app.kb.pack ingest --db data/kb.db                 # استيراد البذرة كمسودات
python -m app.eval.compare                                   # تقريران منفصلان
```

## صيغة كل ملف

### `knowledge_seed.jsonl` — سطر JSON لكل مقطع

الحقول المقروءة من الملف: `id`, `source_id`, `title`, `topic`, `language`,
`content`, `source_refs_to_verify`. أي حقل حالة/مراجعة في الملف **يُلغى**
ويُبلَّغ عنه، وتُثبَّت القيم الإلزامية:

```json
{"id": "kb-cycle-01", "source_id": "who-education", "title": "<عنوان>", "topic": "<موضوع>",
 "language": "ar", "content": "<النص كما هو>",
 "source_refs_to_verify": [{"ref": "<مرجع للتحقق>"}]}
```

| الحقل | القيمة المفروضة | لماذا |
| --- | --- | --- |
| `status` | `draft_unreviewed` | لا يُستشهد بمقطع لم تراجعه طبيبة |
| `authored_by` | `ai_draft` | المكتوب آليًا لا يحمل اسم طبيب |
| `reviewed_by` / `reviewed_at` | فارغان | لا مراجعة بلا إنسان |
| `license_note` | `AI-written summary; verify against source_refs_to_verify; never present refs as verbatim source` | تمنع تقديم المراجع كمنقولة حرفيًا |

### `eval_questions_seed.jsonl` — سطر JSON لكل سؤال

الحقل الأساسي `question` (وتُقبل `prompt`/`q`/`text`). الباقي اختياري:
`id` (يُولَّد إن غاب)، `category`، `language`، `expect`، `must_not_reach_llm`،
`traps`. كل سجل يأخذ `status="needs_physician_review"`،
وتبقى المجموعة **منفصلة تمامًا** عن أسئلة الوكلاء في `eval/`.

### `glossary_ar.csv` — قاموس المصطلحات

CSV بترميز UTF-8. المقصود: `term,preferred,avoid,status` (تُقبل أعمدة إضافية).
عند وصوله يصبح **مصدر الحقيقة** تلقائيًا: `tools/check_glossary.py` يعطيه
الأولوية على `knowledge/glossary_ar.csv`، ويفشل على أي صيغة غير مفضّلة.

### `sources_registry.json`

```json
{"sources": [{"id": "who-education", "name": "<الاسم>", "language": "ar",
              "url": "", "licence": "", "approved_for_ingest": false, "notes": ""}]}
```

`approved_for_ingest` يبقى `false` من هذا المسار **دائمًا**، وأي `true` يُرفض
ويُسجَّل (`rejected_auto_approvals`). الاعتماد قرار بشري موثّق.
للبذرة المكتوبة آليًا لا يلزم اعتماد رخصة (تُستورد كمسودات)؛ أما النصوص
المنسوخة من مصادر خارجية فتحتاج `--require-approved-source` مع اعتماد بشري.
