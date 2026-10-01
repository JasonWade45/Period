# Period / CycleCare AI

Menstrual cycle tracker, symptom journal, medical insights, and AI assistant — Arabic-first (Egyptian dialect aware), RTL frontend.

The assistant is **educational only**: it does not diagnose and does not prescribe. Every answer passes through a deterministic safety layer before and after the language model. The tracker and the medical insights work fully offline — only the conversational answers need a model.

---

## How a request flows

```
POST /api/v1/ai/chat        (or POST /api/v1/ai/summary)
   │
   ├─ 0. Stored data merge (SQLite, per device key)
   │      cycles and symptoms logged via /v1/cycles and /v1/symptoms replace
   │      hand-typed stats: cycle length is averaged from real date gaps
   │
   ├─ 1. Rules engine (pure Python, no model)
   │      compute_findings(merged data) → INSUFFICIENT_DATA / LONG_CYCLE /
   │      SHORT_CYCLE / IRREGULAR_CYCLE / NO_ALERT_PATTERN, plus
   │      PROLONGED_BLEEDING / MISSED_PERIOD / REPEATED_SEVERE_SYMPTOMS + severity
   │
   ├─ 2. Pre-model emergency filter (no model call)
   │      crisis  → fixed reply, crisis=true,  emergency=true
   │      medical → fixed reply,                emergency=true
   │      Matches on the user message and on URGENT/EMERGENCY findings.
   │
   ├─ 3. Retrieval (hybrid: vectors + keywords, RRF — approved KB chunks only)
   │      data/kb.db via app/kb/retrieval.py; drafts need KB_ALLOW_DRAFT=1.
   │      The retrieved chunk ids become the only ids the model may cite.
   │      Empty result → «no reliable source» without calling the model.
   │
   ├─ 4. Prompt build (app/prompts/system_prompt_v1.2.md + variables)
   │
   ├─ 5. Model call (Groq) → validator
   │      JSON shape, required fields, source-id allowlist, banned
   │      attribution patterns ("أنتِ عندك…", dosage talk, false reassurance)
   │      One retry with feedback, then a safe fallback answer.
   │
   └─ 6. Audit (JSONL) — findings, sources, flags, retries, fallback reason,
          message length — never the message text; purged after AUDIT_RETENTION_DAYS
```

Key property: **steps 1, 2 and 6 never depend on the model or on the API key.** If Groq is missing, rate-limited, down, or returns invalid JSON, the user still gets a safe answer and the emergency/crisis paths keep working at full strength.

## Knowledge base

Knowledge lives in three places, deliberately separate:

| Store | Status today | Reaches the model? |
|---|---|---|
| `data/kb.db` (KB v2, from `kb/knowledge/knowledge_seed.jsonl`) | 22 approved chunks (owner-reviewed) | **Yes** — the only retrieval source for `/api/v1/ai/*`, approved only (`KB_ALLOW_DRAFT=1` = internal evaluation) |
| `app/data/sources.json` | 44 reviewed records (reviewer + date): 38 citable, 6 `text_removed` pending licence confirmation | No — backs the `/health` counters and the legacy loader gates; chat retrieves from the KB above |
| `app/data/sources_draft.json` | empty — everything was promoted | **No**, and not citable |

The legacy loader rejects any chunk with missing provenance, a verified chunk without a review date or reviewer, a draft without its origin, or a duplicate id. A draft is never retrievable, so it cannot enter the prompt — and if a model ever cites a draft id anyway, the validator rejects that id and the answer falls back. Both paths are covered by tests (`tests/test_knowledge_base.py`, `tests/test_e2e_pipeline.py`).

Review workflow (legacy loader):

```bash
python tools/review_sources.py list                          # what is pending
python tools/review_sources.py show draft-pcos-diagnosis     # read it in full
python tools/review_sources.py promote draft-pcos-diagnosis \
    --reviewer "د. فلانة — أخصائية نسا وتوليد" --date 2026-10-05
```

Promotion requires a named reviewer; it moves the chunk into `sources.json` as verified. All 44 records were reviewed by the project owner (2026-09-30). The six chunks attributed to NHS/ACOG/WHO keep `text_removed=true` — their **text** is gone pending licence confirmation, so they are never retrieved regardless of status. `KNOWLEDGE_INCLUDE_DRAFTS=1` sends drafts to the model for internal evaluation only — it logs a loud warning, and `/health` reports `drafts_included`.

### What is live, what is held back

The Arabic knowledge draft supplied for this project (cycle basics, PCOS, endometriosis, heavy bleeding, red flags, investigations, treatment options) exists in two forms:

- **The 38 `draft-*` chunks in `sources.json`** — reviewed and promoted by the project owner, citable under the legacy loader. They were held back first for reasons, not formalities: the text is a paraphrase naming NHS/ACOG/NICE/FIGO/ESHRE as its basis, so crediting those bodies verbatim would be **false attribution** (the records carry `attribution_unverified` and `source_name` stays internal); the numbers were never checked against the originals; and it lists treatment options (NSAIDs, tranexamic acid, hormonal IUD, metformin, SSRIs) — as education that is allowed, as directed advice it is exactly what the validator blocks by name (`خدي إيبوبروفين`, `take ibuprofen`, `your dose`), while still allowing "الإيبوبروفين من الخيارات التي تقررها الطبيبة". Owner review is documented as sufficient for educational content **with** the referral seal; a named physician has not reviewed them — see Known gaps.
- **The 22-chunk seed in `kb/`** — what the chat path actually retrieves today (`data/kb.db`, approved-only). Imported as `draft_unreviewed`, then bulk-approved by the owner (`python -m app.kb.review bulk-approve --reviewer "JasonWade45"`).

Two deterministic thresholds were extracted from the draft into the rules engine, marked as draft-derived and pending physician sign-off: bleeding longer than 7 days (`PROLONGED_BLEEDING`) and 90 days since the last logged bleeding (`MISSED_PERIOD`). Both are MONITOR, not MEDICAL_REVIEW, because the input is user-entered and this code cannot distinguish "period actually stopped" from "log not updated" — the finding text says both.

## Safety design

| Layer | File | Guarantee |
|---|---|---|
| Pre-model filter | `app/services/emergency_filter.py` | Emergency/crisis messages never reach the model; fixed replies include the configured numbers verbatim. |
| Rules engine | `app/services/rules_engine.py` | Severity comes from recorded data only, with `evidence` numbers attached. Thresholds are not hard-coded: they load from `app/data/medical_rules.json` (MR-001..MR-010) at import, a missing/invalid registry fails startup loudly, and each finding carries the matching `rule_id`. |
| Prompt contract | `app/prompts/system_prompt_v1.2.md` | No diagnosis, no negation of a diagnosis, no dosing, no reassurance, education only from supplied sources. |
| Validator | `app/services/validator.py` | Rejects bad JSON, unknown `sources_used` ids, and banned attribution/dosage phrasing. Text is normalised first (diacritics, alef/ya/ta-marbuta variants) so dialect spellings cannot slip past a pattern; 20 known bypass phrasings are covered by regression tests. |
| Fallback | `app/services/ai_pipeline.py` | Any model or validation failure degrades to a safe answer — never a 5xx. |
| Post-model check | `app/services/emergency_filter.py` | If the model reports emergency/crisis (a phrasing the lexical filter missed), the number is forced into the answer text, not just the boolean flag. |
| Country numbers | `app/services/emergency_numbers.py` | 23 documented countries with a source each; unknown country → generic number explicitly labelled *unverified*; crisis lines are never invented. |
| Auth & limits | `app/services/security.py` | Optional API key; per-device rate limit that **never** applies to emergency/crisis messages. |
| Audit | `app/services/audit.py` | Every response is logged with its flags and the message length — never the message text. Old records are purged after `AUDIT_RETENTION_DAYS` (startup + CLI). A failed audit write is logged but never breaks the response. |

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
- **Accounts and dashboard** — register with email + password (`POST /v1/auth/register` moves existing device-key data to the new account), log in/out via an httpOnly session cookie, and land on a dashboard of your cycles and symptoms. On every `/v1` endpoint the session identity overrides the client-supplied `user_key`, so a signed-in account cannot be impersonated (`POST /v1/auth/login|logout`, `GET /v1/auth/me`).
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
| `DB_PATH` | `data/cyclecare.db` | SQLite file for tracker data: bleeding logs, symptom logs, health profile, consents. Gitignored. |
| `API_KEY` | *(empty)* | Empty = open (local development only). When set, `/v1/*` requires `X-API-Key`. Emergency and crisis messages are still accepted with a wrong key on purpose. |
| `RATE_LIMIT_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS` | `30` / `60` | Per device key. Never applied to emergency/crisis messages. In-memory: with multiple workers the effective limit multiplies. |
| `CRISIS_LINE` | *(empty)* | Local crisis/support line; empty replies say it is unavailable rather than inventing one. |
| `PROMPT_VERSION` | `v1.2` | Echoed in every response and audit entry. |
| `PROMPT_PATH` | `app/prompts/system_prompt_v1.2.md` | |
| `SOURCES_PATH` | `app/data/sources.json` | 44 سجلًا مُراجَعًا: 38 قابل للاستشهاد (مراجعة المالك 2026-09-30)، و6 مقاطع أُزيل نصها بانتظار تأكيد الترخيص فلا تُسترجَع أبدًا. هذا الملف يُغذّي عدّادات `/health` وبوابات محمّلها؛ استرجاع المحادثة يأتي من قاعدة المعرفة `data/kb.db` (22 مقطعًا معتمدًا). |
| `RULES_GLOSSARY_PATH` | `app/data/rules_glossary.json` | Plain-language meaning per rule code. |
| `AUDIT_LOG_PATH` | `audit/responses.jsonl` | Gitignored. Findings, flags and message length only — no message text. |
| `AUDIT_RETENTION_DAYS` | `90` | Purged at startup and by `python -m app.services.audit purge --days N` (records without a timestamp are kept). |
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
pytest                 # 621 tests: unit, API, e2e, KB, eval, AI integration, medical rules, i18n/RTL static
```

`tests/test_e2e_pipeline.py` drives the real pipeline over real HTTP against the fake provider, so it covers what unit tests cannot: the rendered prompt (no unfilled variables, user text kept out of the system prompt), provider 429/500 handling, one-retry-then-fallback, and the validator gates firing on live traffic.

`smoke_test.py` is a **live** check against a running server (and a real key). It is excluded from automatic collection on purpose:

```bash
uvicorn app.main:app --port 8113 &
python smoke_test.py   # writes _live.json; SMOKE_BASE overrides the base URL
```

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request — it needs no secrets (only the platform's `GITHUB_TOKEN`), and the test job is fully offline:

- **tests**: install → the static checkers (`check_i18n`, `check_prompt`, `check_rtl`, `check_glossary`, `check_no_secrets`) → `node --check frontend/app.js` → `pytest` (`smoke_test.py` stays excluded — it needs a live server and a real key).
- **security**: [gitleaks](https://github.com/gitleaks/gitleaks) across the full history (findings upload to GitHub code scanning), and `pip-audit --strict` against `requirements.txt` — a dependency vulnerability or a service failure fails the job; the current inventory is clean (0 known vulnerabilities).

`python tools/check_no_secrets.py` is the local counterpart of the secret scan: it fails if any **tracked** file is `.env`, a key/certificate (`*.key`, `*.pem`), a health database (`*.db`), an audit log, a local debug dump, or carries a real key fingerprint (`gsk_…`, `AKIA…`, `github_pat_…`, PEM blocks). Short placeholders such as `gsk_xxx` in `.env.example` deliberately do not match.

When `pip-audit` starts failing, that is the job working as intended: upgrade the flagged dependency; if no fixed release exists yet, document a **time-boxed** ignore with a dated note rather than switching the scan off — a skipped scan is worse than a red one. The first `gitleaks` run walks the *entire* history, so anything ever committed (even if since removed) will surface: review that first report before merging.

## API

`POST /api/v1/ai/chat`

```json
{
  "message": "إيه أعراض ما قبل الدورة؟",
  "user_context": {
    "age": 27, "cycles_recorded": 5, "avg_cycle_days": 41,
    "last_cycles": [{"start_date": "2026-06-05", "bleeding_days": 5}]
  }
}
```

Tracker endpoints (all take `?user_key=<device id>`):

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/v1/cycles` | List / add a bleeding log (`start_date`, optional `bleeding_days` 1–30; the legacy name `length_days` is still accepted and still appears in responses) |
| DELETE | `/v1/cycles/{id}` | Delete one bleeding log |
| GET/POST | `/v1/symptoms` | List / add a log (`log_date`, `symptom`, `severity` 1–5, `note`) |
| DELETE | `/v1/symptoms/{id}` | Delete one log |
| GET/PUT | `/v1/profile` | Health profile: `locale`, `country_code`, `digits_style`, `week_start`, `age`, `contraception`, `pregnancy_status`, `conditions`. `PUT` replaces the whole profile (an absent field clears it); `GET` on an unknown key returns an empty profile |
| GET/POST | `/v1/consents` | Record processing consents (`ai_model_processing`, `local_audit_log`). A new decision for the same activity replaces the old one; storage only — nothing is enforced yet |
| GET | `/v1/insights` | Rules-engine findings + glossary, no model |
| DELETE | `/v1/data` | Erase this device key's app data (logs, profile, consents). The audit trail is kept |
| DELETE | `/v1/account` | Same as `/v1/data` **plus** the audit records under this key's fingerprint (`audit_entries_deleted`) |
| GET | `/v1/meta` | Countries, emergency numbers, verification status |

`GET /v1/insights` findings now include `PROLONGED_BLEEDING` and `MISSED_PERIOD` (draft-derived thresholds, see above).

Data semantics worth knowing before writing to the API: `bleeding_logs.bleeding_days` (the request field; the old name `length_days` still works in both directions) is **how many days the bleeding lasted** (1–30), not cycle length. Cycle length is derived on the server from gaps between start dates, which is the medical definition. Mixing the two produced a real bug — a 38-day "cycle length" typed into the bleeding field fired a prolonged-bleeding flag, and the reverse misread bleeding duration as cycle irregularity. Regression tests pin both directions. Databases written before the rename are migrated in place on startup: tables and columns are renamed with their data and ids intact.

`POST /api/v1/ai/chat` returns `answer`, `sources_used`, `needs_doctor`, `emergency`, `crisis`, `missing_info`, `decision`, `emergency_payload`, `retrieval`.
`POST /api/v1/ai/summary` returns `overview`, `what_changed`, `patterns`, `medical_alerts`, `what_this_does_not_mean`, `questions_for_doctor`, `sources_used`.
Both always include `prompt_version`, `rule_codes`, `model`, `language` and `decision` (`ok | no_source | fallback | emergency_filter | summary_empty`). There is no `mode` field — the endpoint *is* the mode. Status is 200 even when the model fails (the body carries the fallback); 422 only for an empty message.
`GET /api/v1/ai/health` → `configured`, `kb_backend`, `retrievable_chunks`, `producible_statuses`, `kb_allow_draft`, `embedding_model`, `embedding_backend`, `prompt_version`, `languages`.

`GET /health` → `status`, `prompt_version`, `model`, `llm_configured`, `chunks_loaded`, `chunks_citable`, `drafts_pending_review`, `chunks_text_removed`, `drafts_included`, `emergency_number_is_default`, `emergency_number_verified`, `crisis_line_configured`, `auth_required`, `country_code`, `tracker_enabled`.

## Layout

```
app/
  main.py                  FastAPI app, lifespan, tracker/insights endpoints, health, frontend mount
  routers/ai.py            /api/v1/ai/chat + /summary + /health
  kb/                      schemas, store (SQLite/PostgreSQL+pgvector), embedding,
                           retrieval (RRF), ingest CLI, review CLI
  i18n/                    resource-file loader, Arabic plural rules, formatting
  eval/                    eval runner + deterministic checks + judge
  services/ai_pipeline.py  rules → emergency → retrieval → LLM → validate → retry
  config.py                env/.env settings
  schemas.py               request/response/audit models
  data/                    sources.json (legacy reviewed set — chat retrieves from
                           data/kb.db), rules_glossary.json, medical_rules.json
                           (MR-001..MR-010 thresholds; rules_engine reads it at import),
                           emergency_numbers.json (per country, with sources)
  prompts/                 versioned system prompt
  services/                emergency_filter, emergency_numbers, rules_engine, rag,
                           prompt_builder, validator, llm, audit, store, security
frontend/                  vanilla JS + CSS, RTL Arabic UI, landing + signup wizard, tracker panel
  i18n.js                  locale loading, RTL direction, plural/digits in JS
  rtl.css                  logical properties, phone isolation, icon flipping
  tests/rtl.spec.js        Playwright RTL screenshot spec (needs a browser)
locales/                   ar.json, en.json, needs_review.json
knowledge/                 glossary_ar.csv + seed/registry templates
kb/                       knowledge seed + eval questions + glossary + registry (owner package)
migrations/                pgvector SQL (kb_sources, kb_chunks, HNSW, GIN)
eval/                      eval set (JSONL) + reports (gitignored)
store/                     ar.md + en.md store listings
tools/                     check_i18n.py, check_rtl.py, check_glossary.py,
                           check_prompt.py, check_no_secrets.py
tests/                     offline unit, API and end-to-end tests (621)
tools/fake_groq_server.py  local Groq-compatible server for development
smoke_test.py              live end-to-end check
```

## Known gaps

These are known gaps, not features:

1. **Clinical sign-off is still pending.** The Arabic draft is live on owner review: the 38 `sources.json` records and the 22 KB chunks were approved by a named person (`JasonWade45`, 2026-09-30) on the documented basis that the content is educational and every answer carries the referral seal. A physician has not reviewed them. The medical rules live in `app/data/medical_rules.json` (MR-001..MR-010): MR-001..MR-008 reuse exactly the thresholds the engine already shipped — nothing was invented — and **every** rule carries `clinical_review: {required: true, reviewer: null, reviewed_at: null}`. MR-009 and MR-010 are deliberately dormant (empty condition, `threshold: null`, `severity: null`) until their specs arrive; an active rule without a threshold is rejected at startup. The six NHS/ACOG/WHO chunks stay `text_removed` until their licence is confirmed.
2. **Emergency number accuracy.** The table covers 23 countries with a source each, but a number can change and an unknown country still gets a generic number. Re-verify before launch and whenever a country is added; a wrong number in a crisis reply is the highest-severity failure mode in this codebase.
3. **Crisis line coverage.** No crisis line is shipped, because inventing one is worse than admitting none is available. Every reply currently says no verified line exists — fill this in per country from an official source.
4. **Device key is not authentication.** `user_key` isolates rows; it is not a credential, and anyone holding it can read that data. Real accounts (and encryption at rest) are needed before this holds anything a user would not want exposed.
5. **Single-process rate limiting.** The limiter is in memory, so N workers allow N× the limit, and it resets on restart. A shared store (Redis) is needed for a real deployment.
6. **Both safety filters are pattern-based.** The pre-model emergency filter and the post-model validator are lexical, so unseen dialect spellings, typo variants, and phrasings outside the pattern set can slip past. The validator now normalises Arabic script and covers 20 previously-bypassing phrasings, but a pattern list is not a classifier: treat every real flagged response as a candidate new test case, and plan for a trained classifier.
7. **Validating harder can make answers worse, not safer.** A validator rejection produces the generic fallback, so an over-eager pattern costs a good answer. The regression suite therefore pins both directions: known violations must be blocked, and legitimate educational sentences must still pass. Add both kinds of test whenever the pattern list changes.
8. **No calendar view.** Logging is a list with a date field, not a month grid, and there is no reminder or prediction. Deliberate: the prompt forbids assured predictions, so any calendar must show logged data only.
9. **Retrieval has a silent embedding fallback and two stores.** Chat retrieval merges vector + keyword candidates (RRF) over `data/kb.db`; if the embedding model cannot load, the app logs a warning server-side and falls back to keyword-only retrieval — a quality regression the client never sees. Meanwhile `/health` counters (`chunks_citable` = 38) come from the legacy `sources.json` loader while the chat path retrieves from the KB (22 approved): two stores, two counts. Consolidate them (or label the counters explicitly) before launch.
10. **Audit log privacy.** `audit/responses.jsonl` stores findings, flags, source ids, a hashed device id (`key_fingerprint`) and the message **length** — never the message text (`request_excerpt` was removed). Retention is `AUDIT_RETENTION_DAYS` (default 90): purged at startup and by `python -m app.services.audit purge`. Still missing before production: access control on the file, encryption at rest, and a decision on whether even fingerprints may be kept.
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
    emergency overlay and language switching now come from `locales/`, but 27
    content strings remain in JS (10 of them fallbacks inside `i18nText`).
    `tools/check_rtl.py` reports the count as a warning rather than a failure
    until they are migrated.

## قاعدة المعرفة (v2): استيراد، استرجاع، مراجعة

هذه الطبقة الجديدة تُنتج المعرفة من ملفات JSONL بمخطط صريح، وتفصل **الاستيراد**
عن **الاعتماد**: استيراد مقطع لا يعني أنه قابل للاسترجاع.

### دورة حياة المقطع

```
                    ┌─▶ owner_reviewed ────┐
draft_unreviewed ───┤                      ├─▶ approved ──▶ retired
                    └─▶ physician_reviewed ┘       │           │
                        ▲                          │           │
                        └──────────────────────────┴───────────┘  (إعادة للمراجعة)
```

الانتقالات مسموحة فقط وفق جدول صريح (`app/kb/schemas.py: ALLOWED_TRANSITIONS`)，
والانتقال يتطلب اسم مراجع وتاريخًا. لا قفز من مسودة إلى معتمد مباشرة.

**مساران مسجّلان، والقرار مقصود:** المحتوى إرشادي مرجعي (لا تشخيص ولا دواء)
ومعه إحالة صريحة لاستشارة الطبيب، فمراجعة المالك (`owner_reviewed`) تكفي بلا
اشتراط طبيب؛ ومسار الطبيب (`physician_reviewed`) يبقى متاحًا لرفعة أعلى إن
أُريد. التوثيق لا يتغيّر: اسم إنسان + تاريخ في الحالتين.

**ختم الاستشارة يفرضه الخادم:** كل إجابة عادية ناجحة في المسار الوحيد
(`/api/v1/ai/chat`) تُختم تلقائيًا بسطر «هذه معلومات إرشادية ولا
تُغني عن استشارة طبيبك» من `locales/<lang>.json` (مفتاح
`answer.referral_notice`)، سواء التزم الموديل بالبرومبت أو لا — دون تكرار إن كان
السطر موجودًا. ردود الطوارئ مستثناة (رقم الطوارئ يسبق كل شيء).

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

### حالة الحزمة: وصلت ✓ — تحقّق آلي بـ `pack check`

الملفات الأربعة موجودة في `kb/` (وليس وعدًا بها):

```bash
$ python -m app.kb.pack check
جذر الحزمة: kb
  [موجود] knowledge/knowledge_seed.jsonl  (15380 بايت)
  [موجود] eval/eval_questions_seed.jsonl  (3523 بايت)
  [موجود] knowledge/glossary_ar.csv  (2365 بايت)
  [موجود] knowledge/sources_registry.json  (7326 بايت)

مقاطع البذرة: 22 (كلها draft_unreviewed، authored_by=ai_draft)
أسئلة التقييم: 25 (كلها needs_review)
مصادر السجل: 18 | محاولات اعتماد آلي رُفضت: 0
مصطلحات القاموس: 30

لا شيء ممّا سبق قابل للاستشهاد: البذرة مسوّدات، والاعتماد بشري.
```

- **لا يُخترع المحتوى.** `pack check` يعيد التحقق مع كل تشغيل؛ أي ملف يغيب
  يظهر `[غائب]` وينتهي الأمر بلا افتراض أو رقم مُختلق.
- تقرير أسئلة صاحبة المشروع (25 سؤالًا) يُقاس منفصلًا عن أسئلة الوكلاء
  (26 سؤالًا)، وكلاهما يعمل اليوم.
- البذرة تُستورد كمسودات ثم لا تُعتمد إلا بأمر بشري — راجع «دورة حياة
  المقطع» أعلاه؛ `data/kb.db` يحوي اليوم 22 مقطعًا معتمدًا بعد bulk-approve.
- القاموس `kb/knowledge/glossary_ar.csv` (30 مصطلحًا) هو المعتمد الآن:
  يفضّله `tools/check_glossary.py` تلقائيًا منذ وصول الحزمة، ونسخة المستودع
  `knowledge/glossary_ar.csv` (25 مصطلحًا) بقيت بديلًا مؤقتًا.

### حزمة `kb/` — الملفات الأربعة التي وصلت من صاحبة المشروع

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
- كل سؤال تقييم: `status="needs_review"`.
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
ثم دمج RRF بمعامل `k=60`، وأخيرًا أعلى `KB_TOP_K` (افتراضي 4 ضمن نطاق البريف 4–6 — تُبقي حجم الطلب تحت حدّ 8000 رمز/دقيقة في خطة Groq المجانية).

- التصفية بالحالة واللغة **قبل** الترتيب: نص غير معتمد لا يظهر ولو بدرجة منخفضة.
- عتبتان:`KB_MIN_SIMILARITY=0.30` و`KB_MIN_KEYWORD_SCORE=0.34`. تجاوزهما لأسفل
  يعني تمرير مقاطع ضعيفة الصلة إلى الموديل.
- نتيجة فارغة ⇐ `reason` تشخيصي، والرد «لا أملك مصدرًا موثوقًا» **بلا استدعاء
  للموديل**. هذا معيار قبول مُختبَر (`tests/test_ai_pipeline.py`).
- خلفية إنجليزية إذا لم يُرجع البحث بلغة المستخدمة شيئًا.

### المراجعة البشرية

```bash
# المسار الأساسي (قرار المالك): ترقية كل المكدّسات دفعة واحدة
python -m app.kb.review bulk-approve --reviewer "JasonWade45"

# أو مقطعًا واحدًا خطوة خطوة
python -m app.kb.review list --status draft_unreviewed
python -m app.kb.review show kb-cycle-length-01          # مع قائمة تحقق قبل الاعتماد
python -m app.kb.review set-status kb-cycle-length-01 owner_reviewed \
    --reviewer "JasonWade45" --date 2026-09-30
python -m app.kb.review set-status kb-cycle-length-01 approved \
    --reviewer "JasonWade45" --date 2026-09-30

# المسار البديل (رفعة أعلى إن أُريد لاحقًا)
python -m app.kb.review set-status kb-cycle-length-01 physician_reviewed \
    --reviewer "د. فلانة — أخصائية نسا وتوليد" --date 2026-10-05
```

النظام القديم له أمر مقابل: `python tools/review_sources.py promote-all
--reviewer "JasonWade45"` (يرقّي `sources_draft.json` إلى `sources.json`؛
والمقاطع الستة التي نصها محجوب بانتظار الترخيص تبقى غير قابلة للاستشهاد
مهما تغيّرت حالتها — علامة `text_removed=true` تحجبها من الاسترجاع).

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
- `decision` في الرد يوضح المسار: `ok | no_source | fallback | emergency_filter | summary_empty`.
- التدقيق يخزّن `prompt_version` و`model` والقرار وطول الرسالة — **ولا يخزّن
  نص رسالة الطوارئ ولا نص رسائلكِ أصلًا** في هذه المسارات. الاحتفاظ
  `AUDIT_RETENTION_DAYS` (90 يومًا افتراضيًا): جرّف عند الإقلاع وأمر
  `python -m app.services.audit purge`.
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
- **قاعدة المعرفة:** ما يصل للموديل اليوم هو مقاطع `data/kb.db` (22 مقطعًا معتمدًا بعد مراجعة المالك) عبر استرجاع هجين. ملف `sources.json` يضم 44 سجلًا مُراجَعًا: 38 قابلًا للاستشهاد (المسودة العربية بعد مراجعتها 2026-09-30) و6 مقاطع أُزيل نصها بانتظار تأكيد الترخيص فلا تُسترجَع أبدًا؛ وهو يُغذّي عدّادات `/health` لا المحادثة، و`sources_draft.json` فارغ لأن كل شيء رُقّى. أسباب التحفظ الأصلية لم تلغِ الكتابة: النص إعادة صياغة وينسب نفسه إلى NHS/ACOG/NICE (إسناد زائف لو نُشر كما هو) والأرقام لم تُراجَع على الأصل — ويبقى ناقصًا مراجعة طبية.
- **طول النزيف ≠ طول الدورة:** `bleeding_days` يعني أيام النزيف (1–30)، واسمها القديم `length_days` ما زال مقبولًا في الإدخال ويظهر في الإخراج، وطول الدورة يُحسب على الخادم من فروق تواريخ البداية.
- **بدون مفتاح:** التطبيق يقلع ويظل رد الطوارئ والأزمات يعمل كاملًا؛ الأسئلة العادية تُرد بإجابة آمنة مع `llm_configured: false` في `/health`.
- **الأرقام:** 23 بلدًا في `app/data/emergency_numbers.json` لكل رقم مصدر؛ البلد غير المُدرج يحصل على رقم عام **مع تنبيه أنه غير مُتحقق منه**. لا تُضاف أرقام دعم نفسي مُخترعة.
- **الاختبارات:** `pytest` (اختبارات محلية بلا شبكة) و`python smoke_test.py` (فحص حقيقي مع خادم يعمل). التكامل المستمر (GitHub Actions) يشغّل الفحوصات الساكنة والاختبارات على كل سحب، ويفحص التاريخ كاملًا بـgitleaks ويثدّق الاعتمادات بـpip-audit؛ ومحلًا `python tools/check_no_secrets.py` يرفض أي مفتاح أو قاعدة بيانات في ملفات متتبَّعة.

## الرخصة

MIT — انظر [LICENSE](LICENSE). الخطوط المضمَّنة برخصتها الخاصة (`assets/fonts/OFL.txt` — OFL-1.1).
