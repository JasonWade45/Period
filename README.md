# CycleCare (Period)

Menstrual cycle tracker, symptom journal, deterministic medical-safety engine, and a Grok-powered explanation layer.

> **Positioning:** tracking, education, pattern detection and triage guidance — **not diagnosis.**
> The medical rules are an MVP draft and **must be reviewed by a qualified clinician before public release.**

```
User data → Normalize → Medical Rules Engine (deterministic, versioned, tested) → Findings → Grok (explains only)
```

## Status

| PRD phase | Status |
|---|---|
| 1. FastAPI + PostgreSQL + migrations + auth | ✅ `backend/` |
| 2. Cycles / bleeding / symptoms / meds / pregnancy tests | ✅ API (mobile UI: next) |
| 3. Analytics + prediction | ✅ |
| 4. Medical Rules Engine MR-001 → MR-011 | ✅ 87 unit tests |
| 5. Notifications / reminders | ⏳ table exists; scheduler + push not yet |
| 6. Grok integration, context builder, response validation | ✅ (mocked in tests) |
| 7. Export JSON / CSV + doctor report | ✅ (PDF pending — needs Arabic shaping) |
| 8. Security: Argon2id, refresh rotation, rate limiting, audit log, account deletion | ✅ |
| 9. Tests | ✅ 183 tests (unit + integration on real PostgreSQL) |
| Mobile app (Expo / React Native, Arabic RTL) | ⏳ next milestone |

## Quick start

**Option A: no Docker (embedded PostgreSQL)**

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m scripts.dev          # migrates, seeds demo user, serves http://0.0.0.0:8000/docs
```

Demo login: `demo@cyclecare.app` / `demo-password-123` (Arabic, 6 variable cycles, today = cycle day 24).

**Option B: Docker Compose**

```bash
cp backend/.env.example backend/.env    # set JWT_SECRET, AUDIT_IP_SALT, GROK_API_KEY
docker compose up --build               # API on :8000, Postgres on :5432
```

**Tests**

```bash
cd backend && pytest -q          # uses TEST_DATABASE_URL, or starts embedded Postgres via pgserver
ruff check app alembic scripts
```

## Grok

- Set `GROK_API_KEY` (and optionally `GROK_MODEL`, default `grok-4.7`) **on the server only**. The mobile app only talks to `/api/v1/ai/*`.
- Without a key, the app still works: AI endpoints return a safe fallback, and `/ai/summary` returns a deterministic summary.
- Every AI request: auth → Rules Engine → emergency screen (**bypasses the LLM**) → minimal context (no email/name/DOB/notes) → system prompt (`app/ai/system_prompt.txt`) → Grok → prohibited-claim validator (EN + AR) → one regeneration attempt → safe fallback.

## Medical rules (`backend/app/medical/rules.json`, `content.json`)

| Code | Rule | Severity |
|---|---|---|
| MR-001 | Cycle < 21 or > 35 days (adolescents: 45) | ≥ 2 of last 6 → MEDICAL_REVIEW; 1 isolated → MONITOR |
| MR-002 | Period > 7 days | MEDICAL_REVIEW |
| MR-003 | Heavy-bleeding indicators (1–2 h changes, double protection, leakage, clots > 2.5 cm, affects daily life) | MEDICAL_REVIEW; + dizziness/breathless → URGENT; + fainting → EMERGENCY |
| MR-004 | Bleeding between periods (reported, or derived from logs) | MEDICAL_REVIEW (single derived day → MONITOR) |
| MR-005 | Bleeding after sex | MEDICAL_REVIEW |
| MR-006 | Severe pain (≥ 7/10) | affects daily life → MEDICAL_REVIEW; painkillers not helping / fever / sudden / possible pregnancy → URGENT; + fainting, or + pregnancy with sudden pain/shoulder pain/dizziness → EMERGENCY |
| MR-007 | ≥ 3 missed periods | MEDICAL_REVIEW (1–2 → MONITOR); breastfeeding / hormonal contraception → MONITOR + note; never infers pregnancy |
| MR-008 | Bleeding with a confirmed pregnancy | URGENT; + heavy / severe pain / shoulder pain / faint / dizzy → EMERGENCY |
| MR-009 | Change from personal baseline (median of 6) | 1 cycle → MONITOR; 2 consecutive → MEDICAL_REVIEW |
| MR-010 | Multiple findings | One consolidated alert |
| MR-011 | Pain with sex / urination / bowel movements | ≥ 2 days → MEDICAL_REVIEW |

Every finding stores `rule_code`, `rule_version` and `content_version`. It also carries its evidence, a *why*, a next step, and a no-diagnosis disclaimer. Clients can only **acknowledge** a finding; severity is never client-writable. Acute escalations use only recent logs (2–7 days), so an old entry can't raise an emergency today.

See [`docs/DECISIONS.md`](docs/DECISIONS.md) for where the implementation deviates from the PRD, and why.

## API (`/api/v1`, live docs at `/docs`)

`auth/{register,login,refresh,logout,me,account}` · `users/me`, `users/me/settings`, `users/me/health-profile` · `cycles` (+ `/{id}/complete`) · `bleeding` · `symptoms` · `medications` · `pregnancy-tests` · `analytics/{overview,cycles,symptoms,bleeding}` · `predictions/next-period` · `dashboard` · `medical/{evaluate,findings,findings/active,findings/{id}/acknowledge,rules}` · `ai/{chat,summary,conversations}` · `export/{health-data?format=json|csv,doctor-report?format=json|text}`

Health-data writes return `{data, medical}`, so an urgent finding surfaces immediately in the response.
