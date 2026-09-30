# Period / CycleCare AI

Menstrual cycle tracker, symptom journal, medical insights, and AI assistant — Arabic-first (Egyptian dialect aware), RTL frontend.

The assistant is **educational only**: it does not diagnose and does not prescribe. Every answer passes through a deterministic safety layer before and after the language model. The tracker and the medical insights work fully offline — only the conversational answers need a model.

---

## How a request flows

```
POST /v1/chat
   │
   ├─ 0. Stored data merge (SQLite, per device key)
   │      cycles and symptoms logged via /v1/cycles and /v1/symptoms replace
   │      hand-typed stats: cycle length is averaged from real date gaps
   │
   ├─ 1. Rules engine (pure Python, no model)
   │      compute_findings(user_context) → INSUFFICIENT_DATA / LONG_CYCLE /
   │      SHORT_CYCLE / IRREGULAR_CYCLE / NO_ALERT_PATTERN + severity
   │
   ├─ 2. Pre-model emergency filter (no model call)
   │      crisis  → fixed reply, crisis=true,  emergency=true
   │      medical → fixed reply,                emergency=true
   │      Matches on the user message and on URGENT/EMERGENCY findings.
   │
   ├─ 3. Retrieval (keyword RAG over app/data/sources.json)
   │      The retrieved chunk ids become the only ids the model may cite.
   │
   ├─ 4. Prompt build (app/prompts/system_prompt_v1.1.md + variables)
   │
   ├─ 5. Model call (Groq) → validator
   │      JSON shape, required fields, source-id allowlist, banned
   │      attribution patterns ("أنتِ عندك…", dosage talk, false reassurance)
   │      One retry with feedback, then a safe fallback answer.
   │
   └─ 6. Audit (JSONL) — findings, sources, flags, retries, fallback reason
```

Key property: **steps 1, 2 and 6 never depend on the model or on the API key.** If Groq is missing, rate-limited, down, or returns invalid JSON, the user still gets a safe answer and the emergency/crisis paths keep working at full strength.

## Knowledge base

Two files, deliberately separate:

| File | Status | Reaches the model? |
|---|---|---|
| `app/data/sources.json` | verified — `source_name`, `section`, `reviewed_at`, `reviewer` | Yes, and only these ids may be cited |
| `app/data/sources_draft.json` | draft — `drafted_at` + `derived_from`, explicitly named as unreviewed | **No** (default), and not citable |

The loader rejects any chunk with missing provenance, a verified chunk without a review date or reviewer, a draft without its origin, or a duplicate id. A draft is never retrievable, so it cannot enter the prompt — and if a model ever cites a draft id anyway, the validator rejects that id and the answer falls back. Both paths are covered by tests (`tests/test_knowledge_base.py`, `tests/test_e2e_pipeline.py`).

Review workflow:

```bash
python tools/review_sources.py list                          # what is pending
python tools/review_sources.py show draft-pcos-diagnosis     # read it in full
python tools/review_sources.py promote draft-pcos-diagnosis \
    --reviewer "د. فلانة — أخصائية نسا وتوليد" --date 2026-10-05
```

Promotion requires a named reviewer; it moves the chunk into `sources.json` as verified. (All six chunks that used to be `verified` were demoted and had their text removed pending licence confirmation — `sources.json` is intentionally empty right now, so every answer path that needs a source says so instead of guessing.) `KNOWLEDGE_INCLUDE_DRAFTS=1` sends drafts to the model for internal evaluation only — it logs a loud warning, and `/health` reports `drafts_included`.

### The medical draft is explicitly *not* live knowledge

The Arabic knowledge draft supplied for this project (cycle basics, PCOS, endometriosis, heavy bleeding, red flags, investigations, treatment options) was ingested as **38 draft chunks waiting for clinician sign-off**. It is not citable and never reaches a user today. Reasons, not formalities:

- it is a paraphrase, and it names NHS/ACOG/NICE/FIGO/ESHRE as its basis. Publishing it as a source would be **false attribution** — `source_name` is left as an internal unreviewed draft precisely so no body is credited with text it did not write;
- the numbers and thresholds in it have not been checked against the originals;
- it contains treatment options (NSAIDs, tranexamic acid, hormonal IUD, metformin, SSRIs). As education that is allowed; as directed advice it is exactly what the validator now blocks by name (`خدي إيبوبروفين`, `take ibuprofen`, `your dose`), while still allowing "الإيبوبروفين من الخيارات التي تقررها الطبيبة".

Two deterministic thresholds were extracted from it into the rules engine, marked in the source as draft-derived and pending sign-off: bleeding longer than 7 days (`PROLONGED_BLEEDING`) and 90 days since the last logged bleeding (`MISSED_PERIOD`). Both are MONITOR, not MEDICAL_REVIEW, because the input is user-entered and this code cannot distinguish "period actually stopped" from "log not updated" — the finding text says both.

## Safety design

| Layer | File | Guarantee |
|---|---|---|
| Pre-model filter | `app/services/emergency_filter.py` | Emergency/crisis messages never reach the model; fixed replies include the configured numbers verbatim. |
| Rules engine | `app/services/rules_engine.py` | Severity comes from recorded data only, with `evidence` numbers attached. |
| Prompt contract | `app/prompts/system_prompt_v1.1.md` | No diagnosis, no negation of a diagnosis, no dosing, no reassurance, education only from supplied sources. |
| Validator | `app/services/validator.py` | Rejects bad JSON, unknown `sources_used` ids, and banned attribution/dosage phrasing. Text is normalised first (diacritics, alef/ya/ta-marbuta variants) so dialect spellings cannot slip past a pattern; 20 known bypass phrasings are covered by regression tests. |
| Fallback | `app/main.py` | Any model or validation failure degrades to a safe answer — never a 5xx. |
| Post-model check | `app/services/emergency_filter.py` | If the model reports emergency/crisis (a phrasing the lexical filter missed), the number is forced into the answer text, not just the boolean flag. |
| Country numbers | `app/services/emergency_numbers.py` | 23 documented countries with a source each; unknown country → generic number explicitly labelled *unverified*; crisis lines are never invented. |
| Auth & limits | `app/services/security.py` | Optional API key; per-device rate limit that **never** applies to emergency/crisis messages. |
| Audit | `app/services/audit.py` | Every response is logged with its flags; a failed audit write is logged but never breaks the response. |

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # then set GROQ_API_KEY (a placeholder .env may already exist)
$EDITOR .env

uvicorn app.main:app --host 0.0.0.0 --port 8113
```

Open <http://127.0.0.1:8113/> for the app, <http://127.0.0.1:8113/docs> for the API schema, <http://127.0.0.1:8113/health> for runtime status.

`.env` is loaded by `app/config.py` (via `python-dotenv`). Real environment variables always win over the file, and `ENV_FILE=/path/to/env` points at a different file. Without a key the server still starts: `/health` reports `llm_configured: false`, non-emergency questions get a safe "assistant unavailable" answer, and emergency/crisis replies work normally.

## Features

- **Cycle tracker** — log the first day of bleeding and (optionally) its length. Cycle length is averaged from real gaps between start dates, and gaps over 400 days are treated as entry errors rather than long cycles.
- **Symptom journal** — date, symptom, a 1–5 severity you assign yourself, and a note. Three or more severe entries within 90 days raise a `REPEATED_SEVERE_SYMPTOMS` finding.
- **Medical insights** (`GET /v1/insights`) — findings plus plain-language explanations, computed locally. Works with no internet and no API key.
- **Chat and summaries** — educational answers grounded in the supplied sources, with the safety layers above.
- **Country-aware emergency numbers** — chosen per user, with verification status shown.

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `GROQ_API_KEY` | *(empty)* | Required for model answers. Anything else still runs safely. |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | |
| `GROQ_BASE_URL` | `https://api.groq.com` | |
| `LLM_TEMPERATURE` / `LLM_MAX_TOKENS` | `0.2` / `1200` | |
| `LLM_MAX_ATTEMPTS` / `LLM_RETRY_BACKOFF_SECONDS` | `3` / `20` | Our retry loop, for 413/429 only. |
| `LLM_SDK_MAX_RETRIES` | `2` | The `groq` SDK's own retries (5xx/429). |
| `COUNTRY_CODE` | `EG` | Selects the emergency number from `app/data/emergency_numbers.json`. |
| `EMERGENCY_NUMBER` | *(unset)* | Only set this to force one number for **every** request (single-country deployment). An explicitly set value overrides the country table; the built-in default does not, otherwise choosing a country would have no effect. |
| `DB_PATH` | `data/cyclecare.db` | SQLite file for cycles and symptoms. Gitignored. |
| `API_KEY` | *(empty)* | Empty = open (local development only). When set, `/v1/*` requires `X-API-Key`. Emergency and crisis messages are still accepted with a wrong key on purpose. |
| `RATE_LIMIT_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS` | `30` / `60` | Per device key. Never applied to emergency/crisis messages. In-memory: with multiple workers the effective limit multiplies. |
| `CRISIS_LINE` | *(empty)* | Local crisis/support line; empty replies say it is unavailable rather than inventing one. |
| `PROMPT_VERSION` | `v1.1` | Echoed in every response and audit entry. |
| `PROMPT_PATH` | `app/prompts/system_prompt_v1.1.md` | |
| `SOURCES_PATH` | `app/data/sources.json` | المقاطع القابلة للاستشهاد في المسار القديم. **فارغ عن قصد حاليًا:** المقاطع الستة نُسبت إلى NHS/ACOG/WHO بلا مراجعة موثوقة، فأُزيل نصها ونُقلت إلى `sources_draft.json` بانتظار تأكيد الترخيص. النتيجة المطلوبة: صفر مقاطع قابلة للاستشهاد، والمساعد يقول «لا أملك مصدرًا موثوقًا». |
| `RULES_GLOSSARY_PATH` | `app/data/rules_glossary.json` | Plain-language meaning per rule code. |
| `AUDIT_LOG_PATH` | `audit/responses.jsonl` | Gitignored. |
| `RAG_TOP_K` | `5` | |
| `DRAFT_SOURCES_PATH` | `app/data/sources_draft.json` | Unreviewed material, kept out of circulation. |
| `KNOWLEDGE_INCLUDE_DRAFTS` | `0` | Never enable in production. Internal evaluation only. |
| `CORS_ALLOW_ORIGINS` | *(empty)* | Empty = same origin only (the frontend is served by this app). Set a comma-separated list only if you host the frontend elsewhere. `*` is unsafe here: the API has no auth and would become a free proxy for anyone's website. |

## Developing without a key

`tools/fake_groq_server.py` is a small Groq-compatible server for offline work — no key, no network, no quota:

```bash
python tools/fake_groq_server.py --port 8300        # terminal 1
GROQ_API_KEY=dummy GROQ_BASE_URL=http://127.0.0.1:8300 \
    uvicorn app.main:app --port 8113                # terminal 2
```

The full pipeline runs (prompt → HTTP → SDK → validator → audit) against canned, correctly-shaped replies — useful for frontend work. **It is not a model**: the text is a placeholder, never a medical answer. Failure handling can be exercised with `--fail-first N` (provider 500s) and `--bad-json N` (invalid JSON, to watch the retry-then-fallback path).

## Tests

```bash
pytest                 # 572 test: unit, API, e2e, KB, eval, AI integration, i18n/RTL static
```

`tests/test_e2e_pipeline.py` drives the real pipeline over real HTTP against the fake provider, so it covers what unit tests cannot: the rendered prompt (no unfilled variables, user text kept out of the system prompt), provider 429/500 handling, one-retry-then-fallback, and the validator gates firing on live traffic.

`smoke_test.py` is a **live** check against a running server (and a real key). It is excluded from automatic collection on purpose:

```bash
uvicorn app.main:app --port 8113 &
python smoke_test.py   # writes _live.json; SMOKE_BASE overrides the base URL
```

## API

`POST /v1/chat`

```json
{
  "message": "إيه أعراض ما قبل الدورة؟",
  "mode": "chat",
  "user_context": {
    "age": 27, "cycles_recorded": 5, "avg_cycle_days": 41,
    "last_cycles": [{"start_date": "2026-06-05", "length_days": 38}]
  }
}
```

Tracker endpoints (all take `?user_key=<device id>`):

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/v1/cycles` | List / add a cycle (`start_date`, optional `length_days` 1–90) |
| DELETE | `/v1/cycles/{id}` | Delete one cycle |
| GET/POST | `/v1/symptoms` | List / add a log (`log_date`, `symptom`, `severity` 1–5, `note`) |
| DELETE | `/v1/symptoms/{id}` | Delete one log |
| GET | `/v1/insights` | Rules-engine findings + glossary, no model |
| DELETE | `/v1/data` | Erase all data for this device key |
| GET | `/v1/meta` | Countries, emergency numbers, verification status |

`GET /v1/insights` findings now include `PROLONGED_BLEEDING` and `MISSED_PERIOD` (draft-derived thresholds, see above).

Data semantics worth knowing before writing to the API: `cycles.length_days` is **how many days the bleeding lasted** (1–30), not cycle length. Cycle length is derived on the server from gaps between start dates, which is the medical definition. Mixing the two produced a real bug — a 38-day "cycle length" typed into the bleeding field fired a prolonged-bleeding flag, and the reverse misread bleeding duration as cycle irregularity. Regression tests pin both directions.

`mode: "chat"` returns `answer`, `sources_used`, `needs_doctor`, `emergency`, `crisis`, `missing_info`.
`mode: "summary"` returns `overview`, `what_changed`, `patterns`, `medical_alerts`, `what_this_does_not_mean`, `questions_for_doctor`, `sources_used`.
Both always include `prompt_version` and `rule_codes`. Status is 200 even when the model fails (the body carries the fallback); 422 only for an empty message.

`GET /health` → `status`, `prompt_version`, `model`, `llm_configured`, `chunks_loaded`, `emergency_number_is_default`, `crisis_line_configured`.

## Layout

```
app/
  main.py                  FastAPI app, pipeline, endpoints, static frontend mount
  routers/ai.py            /api/v1/ai/chat + /summary + /health
  kb/                      schemas, store (SQLite/PostgreSQL+pgvector), embedding,
                           retrieval (RRF), ingest CLI, review CLI
  i18n/                    resource-file loader, Arabic plural rules, formatting
  eval/                    eval runner + deterministic checks + judge
  services/ai_pipeline.py  rules → emergency → retrieval → LLM → validate → retry
  config.py                env/.env settings
  schemas.py               request/response/audit models
  data/                    sources.json (RAG), rules_glossary.json,
                           emergency_numbers.json (per country, with sources)
  prompts/                 versioned system prompt
  services/                emergency_filter, emergency_numbers, rules_engine, rag,
                           prompt_builder, validator, llm, audit, store, security
frontend/                  vanilla JS + CSS, RTL Arabic UI, tracker panel
  i18n.js                  locale loading, RTL direction, plural/digits in JS
  rtl.css                  logical properties, phone isolation, icon flipping
  tests/rtl.spec.js        Playwright RTL screenshot spec (needs a browser)
locales/                   ar.json, en.json, needs_review.json
knowledge/                 glossary_ar.csv + seed/registry templates
migrations/                pgvector SQL (kb_sources, kb_chunks, HNSW, GIN)
eval/                      eval set (JSONL) + reports (gitignored)
store/                     ar.md + en.md store listings
tools/                     check_i18n.py, check_rtl.py, check_glossary.py
tests/                     offline unit, API and end-to-end tests (325)
tools/fake_groq_server.py  local Groq-compatible server for development
smoke_test.py              live end-to-end check
```

## Before a real launch

These are known gaps, not features:

1. **The knowledge draft needs clinical review.** 38 chunks are waiting. Until a clinician signs them off and a source's own text or licence permits reuse, the assistant can only answer from the 6 verified chunks — for most topics it will correctly say it has no reliable information rather than answer from the draft.
2. **Emergency number accuracy.** The table covers 23 countries with a source each, but a number can change and an unknown country still gets a generic number. Re-verify before launch and whenever a country is added; a wrong number in a crisis reply is the highest-severity failure mode in this codebase.
3. **Crisis line coverage.** No crisis line is shipped, because inventing one is worse than admitting none is available. Every reply currently says no verified line exists — fill this in per country from an official source.
4. **Device key is not authentication.** `user_key` isolates rows; it is not a credential, and anyone holding it can read that data. Real accounts (and encryption at rest) are needed before this holds anything a user would not want exposed.
5. **Single-process rate limiting.** The limiter is in memory, so N workers allow N× the limit, and it resets on restart. A shared store (Redis) is needed for a real deployment.
6. **Both safety filters are pattern-based.** The pre-model emergency filter and the post-model validator are lexical, so unseen dialect spellings, typo variants, and phrasings outside the pattern set can slip past. The validator now normalises Arabic script and covers 20 previously-bypassing phrasings, but a pattern list is not a classifier: treat every real flagged response as a candidate new test case, and plan for a trained classifier.
7. **Validating harder can make answers worse, not safer.** A validator rejection produces the generic fallback, so an over-eager pattern costs a good answer. The regression suite therefore pins both directions: known violations must be blocked, and legitimate educational sentences must still pass. Add both kinds of test whenever the pattern list changes.
8. **No calendar view.** Logging is a list with a date field, not a month grid, and there is no reminder or prediction. Deliberate: the prompt forbids assured predictions, so any calendar must show logged data only.
9. **Keyword RAG.** Matching is lexical, so paraphrased questions retrieve nothing. Replace with embeddings while keeping the source-id allowlist.
10. **Audit log privacy.** `request_excerpt` stores up to 300 characters of user text in plaintext JSONL — sensitive health data. Define retention, access control, and encryption before production.
11. **Conversation state.** Each request is independent; there is no multi-turn memory.
12. **PostgreSQL/pgvector path is written but not executed here.** `app/kb/postgres.py`
    and `migrations/001_kb_pgvector.sql` could not be run in this environment
    (no PostgreSQL, no package mirrors, Hugging Face and api.groq.com blocked).
    They match the SQLite implementation's semantics and are written for review,
    but they must be exercised once (`pytest -m postgres` after setting
    `DATABASE_URL`) before any deployment. The same applies to the embedding
    benchmark between bge-m3 and multilingual-e5-large.
13. **Arabic PDF depends on the font you supply.** IBM Plex Sans Arabic and
    Noto Sans Arabic are not bundled (licence), and ReportLab has no native
    Arabic shaping. A system font is used as a fallback so the smoke test can
    run; ship a proper Arabic font and re-check the rendering visually.
14. **Arabic strings still inside `frontend/app.js`.** The chrome, suggestions,
    emergency overlay and language switching now come from `locales/`, but ~19
    content strings remain in JS. `tools/check_rtl.py` reports the count as a
    warning rather than a failure until they are migrated.

## قاعدة المعرفة (v2): استيراد، استرجاع، مراجعة

هذه الطبقة الجديدة تُنتج المعرفة من ملفات JSONL بمخطط صريح، وتفصل **الاستيراد**
عن **الاعتماد**: استيراد مقطع لا يعني أنه قابل للاسترجاع.

### دورة حياة المقطع

```
draft_unreviewed ──▶ physician_reviewed ──▶ approved ──▶ retired
       ▲                    │                   │           │
       └────────────────────┴───────────────────┴───────────┘  (إعادة للمراجعة)
```

الانتقالات مسموحة فقط وفق جدول صريح (`app/kb/schemas.py: ALLOWED_TRANSITIONS`)،
والانتقال يتطلب اسم مراجع وتاريخًا. لا قفز من مسودة إلى معتمد: مراجعة الطبيبة
خطوة إلزامية ومسجّلة.

**حالة الاسترجاع في الإنتاج:** `approved` فقط. `KB_ALLOW_DRAFT=1` يضيف المسودات
لبيئة تجريبية داخلية فقط، و`/api/v1/ai/health` يُظهر الحالات المسموحة فعليًا.

### الاستيراد

```bash
python -m app.kb.ingest --path knowledge/knowledge_seed.example.jsonl \
    --registry knowledge/sources_registry.example.json --dry-run
```

- **بوابة الرخصة:** أي مقطع من مصدر `approved_for_ingest=false` لا يُكتب إطلاقًا.
  إن لم يحمل الملف أي مصدر معتمد، يرفض الاستيراد كاملًا (exit 3) إلا مع
  `--skip-unapproved`. القرار بشري: لا يُضبط هذا الحقل آليًا ولا من سكربت.
- **Idempotency:** إعادة الاستيراد لا تغيّر حالة مقطع لم يتغيّر محتواه ولا تزيد
  إصداره. تغيّر المحتوى يُعيده إلى `draft_unreviewed`، يزيد
  `content_version`، **ويمسح متجهه القديم** (متجه نص قديم على نص جديد يجعل البحث
  يعقّر بنتيجة لا تخص المحتوى الحالي).
- مقارنة المحتوى تتم على نص مُطبَّع: اختلاف تشكيل أو مسافات أو أرقام عربية-هندية
  ليس «تغيّر محتوى».
- السجلات غير الصالحة تُبلَّغ عنها سطرًا سطرًا ولا تُسقط الملف (exit 1).

**التضمين:** `KB_EMBEDDING_MODEL=BAAI/bge-m3` (افتراضيًا) أو
`intfloat/multilingual-e5-large`، عبر `sentence-transformers`. للاختبار بلا شبكة
يوجد `KB_EMBEDDING_BACKEND=local` (محوّل حتمي) — **لا يُستخدم في الإنتاج**.
لمقارنة النموذجين: `python -m app.eval.run --set ...` مع كل نموذج وسجّلي
`retrievable_chunks` ونتيجة الفئات.

### حالة الحزمة الآن (2026-09-30): لم تصل بعد

الملفات الأربعة **غير موجودة** في المستودع ولا في أي مسار رفع — تحقّق آلي، لا
افتراض. محاولتا تسليم سابقتان لم تحملا محتوى («في `/home/user/uploads`» ثم
«ملصقة أدناه»). لذلك:

```bash
$ python -m app.kb.pack check
  [غائب] knowledge/knowledge_seed.jsonl
  [غائب] eval/eval_questions_seed.jsonl
  [غائب] knowledge/glossary_ar.csv
  [غائب] knowledge/sources_registry.json
الحزمة ناقصة: لا يُنشأ أي محتوى بالنيابة عن صاحبة المشروع.
```

- **لا يُخترع المحتوى.** الملفات تُبنى كما هي لحظة وصول نصّها في المحادثة.
- تقرير أسئلة صاحبة المشروع يقول صراحةً «لم تُقَس — الملف غير موجود» بدل رقم
  مُخترع، وتقرير الوكلاء (26 سؤالًا) يعمل كما هو.
- الكود جاهز: لا يلزم أي تعديل بعد وصول الحزمة، فقط `pack check` ثم `pack ingest`
  ثم `eval.compare`.
- القاموس المؤقت الحالي `knowledge/glossary_ar.csv` (25 مصطلحًا) بديل عن قاموس
  الحزمة؛ عند وصول `kb/knowledge/glossary_ar.csv` تُعطى الأولوية له تلقائيًا في
  `tools/check_glossary.py`.

### حزمة `kb/` — الملفات الأربعة التي تصل من صاحبة المشروع

الحزمة تُقرأ من مسارات ثابتة، ولا يُنشئ المستودع محتواها بالنيابة عنها:

| الملف | الدور |
| --- | --- |
| `kb/knowledge/knowledge_seed.jsonl` | مقاطع معرفة مكتوبة آليًا (بذرة) |
| `kb/eval/eval_questions_seed.jsonl` | أسئلة تقييم صاحبة المشروع (تُقاس منفصلة) |
| `kb/knowledge/glossary_ar.csv` | قاموس المصطلحات — مصدر الحقيقة للّغة |
| `kb/knowledge/sources_registry.json` | سجل المصادر وحالة الاعتماد |

```bash
python -m app.kb.pack check          # ما وصل وما لم يصل، وعدد المقاطع/الأسئلة
python -m app.kb.pack ingest --db data/kb.db                # البذرة كمسودات
python -m app.kb.pack ingest --require-approved-source --db data/kb.db   # للنصوص المنسوخة
```

**الثوابت المفروضة على أي محتوى يمرّ من الحزمة** (`app/kb/pack.py`) — تُثبَّت في
الكود ولا تُقرأ من الملف：

- كل مقطع بذرة: `status="draft_unreviewed"`، `authored_by="ai_draft"`،
  `reviewed_by=null`، `reviewed_at=null`، وملاحظة ترخيص إلزامية:
  *AI-written summary; verify against source_refs_to_verify; never present refs
  as verbatim source*. أي ادّعاء مراجعة داخل الملف **يُلغى ويُبلَّغ عنه**.
- كل سؤال تقييم: `status="needs_physician_review"`.
- كل مصدر في السجل: `approved_for_ingest=false`. هذا المسار **لا يقلب الحقل إلى
  `true` أبدًا**؛ لو جاء `true` في الملف يُسجَّل ويُبقى `false` في الذاكرة،
  فيبقى الاستيراد موقوفًا حتى يُراجَع الترخيص بشريًا. (اختبارات:
  `tests/test_kb_pack.py`.)
- البذرة تُستورد **كمسودات** (`draft_unreviewed`) بأمر واحد: لا شيء منها قابل
  للاستشهاد ولا يصل لمستخدمة حتى تراجعه طبيبة وترقّيه. إعادة الاستيراد لا
  تُغيّر حالة مقطع لم يتغيّر نصه (الاعتماد لا يضيع بتشغيل الأمر ثانية).
- للنصوص **المنسوخة** من مصادر خارجية مسار منفصل: `--require-approved-source`
  يرفض كل مقطع مصدره غير معتمد في السجل.
- وصف الملفات الأربعة وصيغها الكاملة في `kb/README.md`.

### الاسترجاع

`app/kb/retrieval.py` — استرجاع هجين: متجهات (أفضل 20) + كلمات مفتاحية (أفضل 20)
ثم دمج RRF بمعامل `k=60`، وأخيرًا أعلى `KB_TOP_K` (افتراضي 6، والبريف يطلب 4–6).

- التصفية بالحالة واللغة **قبل** الترتيب: نص غير معتمد لا يظهر ولو بدرجة منخفضة.
- عتبتان:`KB_MIN_SIMILARITY=0.30` و`KB_MIN_KEYWORD_SCORE=0.34`. تجاوزهما لأسفل
  يعني تمرير مقاطع ضعيفة الصلة إلى الموديل.
- نتيجة فارغة ⇐ `reason` تشخيصي، والرد «لا أملك مصدرًا موثوقًا» **بلا استدعاء
  للموديل**. هذا معيار قبول مُختبَر (`tests/test_ai_pipeline.py`).
- خلفية إنجليزية إذا لم يُرجع البحث بلغة المستخدمة شيئًا.

### المراجعة البشرية

```bash
python -m app.kb.review list --status draft_unreviewed
python -m app.kb.review show kb-cycle-length-01          # مع قائمة تحقق قبل الاعتماد
python -m app.kb.review set-status kb-cycle-length-01 physician_reviewed \
    --reviewer "د. فلانة — أخصائية نسا وتوليد" --date 2026-10-05
```

`/health` يعرض حالة المعرفة بأرقام صريحة: `chunks_loaded`, `chunks_citable`,
`drafts_pending_review`, و`chunks_text_removed` (مقاطع أُزيل نصها بانتظار
الترخيص — لا تُسترجع أبدًا ولو فُعِّل تضمين المسودات).

## واجهة /api/v1/ai

الترتيب غير قابل للتفاوض:

```
توثيق ← فلتر طوارئ ← محرك القواعد ← استرجاع ← موديل (JSON) ← تحقق ← إعادة مرة ← رد احتياطي
```

- `POST /api/v1/ai/chat` و`POST /api/v1/ai/summary`، والمصادقة داخل المسار لا
  كاعتماد عام، حتى لا يحجب مفتاح خاطئ ردَّ طوارئ.
- الطوارئ/الأزمة: رد ثابت من `locales/`، **بلا استدعاء للموديل**، ومعه
  `emergency_payload` يحتوي الرقم وحالة التحقق منه.
- كل إجابة تحمل `sources_used` من معرّفات المقاطع المسترجَعة فقط؛ موديل يستشهد
  بمعرّف غير مسترجَع يُرفض ← إعادة ← رد احتياطي.
- `decision` في الرد يوضح المسار: `ok | no_source | fallback | emergency_filter`.
- التدقيق يخزّن `prompt_version` و`model` والقرار وطول الرسالة — **ولا يخزّن
  نص رسالة الطوارئ ولا نص رسائلكِ أصلًا** في هذه المسارات.
- `GET /api/v1/ai/health` يعرض عدد المقاطع القابلة للاسترجاع والنموذج واللغات.

## التقييم (Eval)

```bash
python -m app.eval.compare                    # تقريران: أسئلة الوكلاء + أسئلة صاحبة المشروع
python -m app.eval.run --set eval/eval_questions_seed.jsonl           # بلا شبكة
python -m app.eval.run --live --judge auto                            # بموديل حقيقي
python -m app.eval.run --kb-db data/kb.db --allow-draft               # قاعدة معرفة مسوَّرة
```

**مجموعتان منفصلتان لا تُدمجان** (البريف يطلب تقريرين منفصلين):

| المجموعة | الملف | التسمية في التقرير |
| --- | --- | --- |
| أسئلة الوكلاء (26) | `eval/eval_questions_seed.jsonl` | `report-agent-*.json` |
| أسئلة صاحبة المشروع (25) | `kb/eval/eval_questions_seed.jsonl` | `report-user-*.json` |

التسمية تُشتق من المسار، و`--label` لا يستطيع تسمية أسئلة `kb/` بـ`agent`
(يرفض الخلط). الملفان يُكتبان منفصلين ولا يُعاد ترقيم أسئلة صاحبة المشروع.

كل سؤال يمرّ بخط الأنابيب الكامل، ثم تُطبَّق **فحوص حتمية** (لا تتأثر بموديل):
لا عبارات تشخيص، لا جرعات، الطوارئ لا تصل إلى الموديل (عدّاد نداءات = 0)،
عقد JSON (قرار `ok` لا `fallback`)، التطابق اللغوي، والإسناد للمصادر المسترجَعة.
لكل سؤال طوارئ/أزمة فحص إضافي: يجب إعلان العلَم ووجود الرقم في النص.

- **البوّابة:** فشل أي سؤال طوارئ/أزمة أو أي اختبار مصيدة ⇒ مخرج غير صفري
  (exit 1) مع قائمة `gating_failures` في التقرير. فشل تعليمي يُسجَّل ولا يوقف.
- **الحكم (judge):** افتراضيًا محلي حتمي (`local_rubric`) وموسوم بأنه غير
  متحقَّق منه؛ `--judge llm/auto` يستخدم موديلًا إن توفّر. الحكم لا يُصلح فحصًا
  حتميًا فاشلًا ولا يُسقط سلامة.
- التقرير JSON في `eval/reports/` (مُتجاهَل في git) وفيه: `by_category`,
  `totals`, `gating_failures`, وتحليل كل سؤال.

## التعريب (i18n) و RTL

- **مصدر واحد للنص:** `locales/ar.json` و`locales/en.json`. لا نص عربي في الكود
  لسطح المستخدمة — حتى ردود الطوارئ انتقلت إلى الملفات بنفس نصها حرفيًا
  (والاختبارات القديمة هي الدليل على عدم تغيّر السلوك).
- **جمع عربي صحيح** (CLDR: zero/one/two/few/many/other) في الباك-إند
  (`app/i18n/plural.py`) وفي الواجهة (`frontend/i18n.js`).
- **الأرقام:** غربية افتراضيًا، والعربية-الهندية إعداد مستخدمة؛ والتحقق والقواعد
  تعمل على الأرقام بعد التطبيع دائمًا.
- **التواريخ:** غريغوري، وبداية الأسبوع إعداد (`WEEK_START=saturday` افتراضيًا).
- **الواجهة:** `dir` يُشتق من اللغة (`rtl` للعربية)، خصائص CSS منطقية، أيقونات
  اتجاهية بـ`flip-rtl`، وأرقام الهواتف داخل عزل LTR حتى لا يقلبها BiDi.
- **التدقيق:** `python tools/check_i18n.py` (تكافؤ المفاتيح، المفاتيح غير
  المستخدمة، نصوص عربية في مسارات API) و`python tools/check_rtl.py` (فحوص RTL
  ساكنة) و`python tools/check_glossary.py` (المصطلحات المعتمدة).
- **لقطات RTL:** `frontend/tests/rtl.spec.js` جاهز لـPlaywright:
  `npx playwright test frontend/tests/rtl.spec.js` (يحتاج متصفحًا وتنزيله).
- **المصطلحات:** `knowledge/glossary_ar.csv` هو المصدر الوحيد؛ أي صيغة غير
  مفضّلة تُفشل الفحص. النصوص التي تحتاج مراجعة طبيبة/مترجم في
  `locales/needs_review.json`.

## التصدير

- **CSV:** UTF-8 مع BOM (بدونه يقرأ Excel العربي مشوّهًا)، وترويسات من ملفات
  الموارد، وأرقام/تواريخ حسب إعداد المستخدمة (`app/services/export_ar.py`).
- **PDF عربي:** إعادة تشكيل الحروف (`arabic-reshaper`) + ترتيب ثنائي الاتجاه
  (`python-bidi`) + محاذاة يمين + سطر بارتفاع 1.7 + خط عربي مُضمَّن. الخط
  المفضّل IBM Plex Sans Arabic ثم Noto Sans Arabic، ويُضبط بـ`PDF_ARABIC_FONT_PATH`.
  **حدّ مكتبي:** reportlab لا يشكّل عربيًا أصليًا (بلا HarfBuzz)، فالناتج جيد
  للنص العادي وقد يقصّر في الاتجاه المختلط المعقّد؛ البديل WeasyPrint عند الحاجة.

---

## نظرة عربية سريعة

**CycleCare** مساعد تثقيفي لصحة الدورة الشهرية: محرك قواعد يحلّل الدورات المسجّلة، وطبقة سلامة تعمل **قبل** الموديل، وفلتر يمنع التشخيص ووصف الأدوية بعد الموديل، وسجل تدقيق لكل رد.

- **التشغيل:** `pip install -r requirements.txt` ثم ضعي `GROQ_API_KEY` في ملف `.env` ثم `uvicorn app.main:app --host 0.0.0.0 --port 8113`.
- **التتبّع:** سجّلي الدورات والأعراض من زر 📖 في الواجهة، وتُحفظ في SQLite على الخادم بمعرّف جهازكِ. الرؤى (`/v1/insights`) تعمل بلا إنترنت وبلا مفتاح.
- **الأمان:** اضبطي `API_KEY` قبل أي نشر؛ الرسائل التي يُفعّل فيها فلتر الطوارئ تُقبل دائمًا حتى لو كان المفتاح خاطئًا — قرار مقصود.
- **قاعدة المعرفة:** ما يصل للموديل هو `sources.json` فقط (6 مقاطع مُتحقَّقة). المسودة التي أُرسلت للمشروع محفوظة في `sources_draft.json` كـ38 مقطعًا **محجوبة عن الاستشهاد** حتى تُراجَع طبيبيًا وتُرقّى بـ`tools/review_sources.py`. سبب الرفض ليس شكليًا: النص إعادة صياغة وينسب نفسه إلى NHS/ACOG/NICE، ونشره كمصدر إسناد زائف، والأرقام لم تُراجَع على الأصل.
- **طول النزيف ≠ طول الدورة:** `length_days` يعني أيام النزيف (1–30)، وطول الدورة يُحسب على الخادم من فروق تواريخ البداية.
- **بدون مفتاح:** التطبيق يقلع ويظل رد الطوارئ والأزمات يعمل كاملًا؛ الأسئلة العادية تُرد بإجابة آمنة مع `llm_configured: false` في `/health`.
- **الأرقام:** 23 بلدًا في `app/data/emergency_numbers.json` لكل رقم مصدر؛ البلد غير المُدرج يحصل على رقم عام **مع تنبيه أنه غير مُتحقق منه**. لا تُضاف أرقام دعم نفسي مُخترعة.
- **الاختبارات:** `pytest` (اختبارات محلية بلا شبكة) و`python smoke_test.py` (فحص حقيقي مع خادم يعمل).
