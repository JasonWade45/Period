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
| `SOURCES_PATH` | `app/data/sources.json` | Curated medical chunks (NHS, ACOG, WHO). |
| `RULES_GLOSSARY_PATH` | `app/data/rules_glossary.json` | Plain-language meaning per rule code. |
| `AUDIT_LOG_PATH` | `audit/responses.jsonl` | Gitignored. |
| `RAG_TOP_K` | `5` | |
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
pytest                 # 98 tests: unit, API, and end-to-end against the fake provider
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

`mode: "chat"` returns `answer`, `sources_used`, `needs_doctor`, `emergency`, `crisis`, `missing_info`.
`mode: "summary"` returns `overview`, `what_changed`, `patterns`, `medical_alerts`, `what_this_does_not_mean`, `questions_for_doctor`, `sources_used`.
Both always include `prompt_version` and `rule_codes`. Status is 200 even when the model fails (the body carries the fallback); 422 only for an empty message.

`GET /health` → `status`, `prompt_version`, `model`, `llm_configured`, `chunks_loaded`, `emergency_number_is_default`, `crisis_line_configured`.

## Layout

```
app/
  main.py                  FastAPI app, pipeline, endpoints, static frontend mount
  config.py                env/.env settings
  schemas.py               request/response/audit models
  data/                    sources.json (RAG), rules_glossary.json,
                           emergency_numbers.json (per country, with sources)
  prompts/                 versioned system prompt
  services/                emergency_filter, emergency_numbers, rules_engine, rag,
                           prompt_builder, validator, llm, audit, store, security
frontend/                  vanilla JS + CSS, RTL Arabic UI, tracker panel
tests/                     offline unit, API and end-to-end tests (325)
tools/fake_groq_server.py  local Groq-compatible server for development
smoke_test.py              live end-to-end check
```

## Before a real launch

These are known gaps, not features:

1. **Emergency number accuracy.** The table covers 23 countries with a source each, but a number can change and an unknown country still gets a generic number. Re-verify before launch and whenever a country is added; a wrong number in a crisis reply is the highest-severity failure mode in this codebase.
2. **Crisis line coverage.** No crisis line is shipped, because inventing one is worse than admitting none is available. Every reply currently says no verified line exists — fill this in per country from an official source.
3. **Device key is not authentication.** `user_key` isolates rows; it is not a credential, and anyone holding it can read that data. Real accounts (and encryption at rest) are needed before this holds anything a user would not want exposed.
4. **Single-process rate limiting.** The limiter is in memory, so N workers allow N× the limit, and it resets on restart. A shared store (Redis) is needed for a real deployment.
5. **Both safety filters are pattern-based.** The pre-model emergency filter and the post-model validator are lexical, so unseen dialect spellings, typo variants, and phrasings outside the pattern set can slip past. The validator now normalises Arabic script and covers 20 previously-bypassing phrasings, but a pattern list is not a classifier: treat every real flagged response as a candidate new test case, and plan for a trained classifier.
6. **Validating harder can make answers worse, not safer.** A validator rejection produces the generic fallback, so an over-eager pattern costs a good answer. The regression suite therefore pins both directions: known violations must be blocked, and legitimate educational sentences must still pass. Add both kinds of test whenever the pattern list changes.
7. **No calendar view.** Logging is a list with a date field, not a month grid, and there is no reminder or prediction. Deliberate: the prompt forbids assured predictions, so any calendar must show logged data only.
8. **Keyword RAG.** Matching is lexical, so paraphrased questions retrieve nothing. Replace with embeddings while keeping the source-id allowlist.
9. **Audit log privacy.** `request_excerpt` stores up to 300 characters of user text in plaintext JSONL — sensitive health data. Define retention, access control, and encryption before production.
10. **Conversation state.** Each request is independent; there is no multi-turn memory.

---

## نظرة عربية سريعة

**CycleCare** مساعد تثقيفي لصحة الدورة الشهرية: محرك قواعد يحلّل الدورات المسجّلة، وطبقة سلامة تعمل **قبل** الموديل، وفلتر يمنع التشخيص ووصف الأدوية بعد الموديل، وسجل تدقيق لكل رد.

- **التشغيل:** `pip install -r requirements.txt` ثم ضعي `GROQ_API_KEY` في ملف `.env` ثم `uvicorn app.main:app --host 0.0.0.0 --port 8113`.
- **التتبّع:** سجّلي الدورات والأعراض من زر 📖 في الواجهة، وتُحفظ في SQLite على الخادم بمعرّف جهازكِ. الرؤى (`/v1/insights`) تعمل بلا إنترنت وبلا مفتاح.
- **الأمان:** اضبطي `API_KEY` قبل أي نشر؛ الرسائل التي يُفعّل فيها فلتر الطوارئ تُقبل دائمًا حتى لو كان المفتاح خاطئًا — قرار مقصود.
- **بدون مفتاح:** التطبيق يقلع ويظل رد الطوارئ والأزمات يعمل كاملًا؛ الأسئلة العادية تُرد بإجابة آمنة مع `llm_configured: false` في `/health`.
- **الأرقام:** 23 بلدًا في `app/data/emergency_numbers.json` لكل رقم مصدر؛ البلد غير المُدرج يحصل على رقم عام **مع تنبيه أنه غير مُتحقق منه**. لا تُضاف أرقام دعم نفسي مُخترعة.
- **الاختبارات:** `pytest` (اختبارات محلية بلا شبكة) و`python smoke_test.py` (فحص حقيقي مع خادم يعمل).
