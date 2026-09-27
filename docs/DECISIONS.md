# Implementation decisions & deviations from the PRD

These are deliberate choices made while implementing PRD v1.0. Each is worth a product and clinical sign-off.

## Medical safety

1. **Pregnancy + heavy bleeding → `EMERGENCY` (PRD §61 test says `URGENT`).**
   PRD §24 and the NHS "Vaginal bleeding in pregnancy" page both say heavy bleeding in pregnancy means calling 999. §61 conflicts with §24, so the stricter behaviour was implemented. The test asserts `≥ URGENT` and `== EMERGENCY`. Any bleeding in pregnancy is at least `URGENT`, per NHS ("get urgent help").
2. **Pregnancy is only "confirmed" from explicit data.** That means a positive test with no later negative test, or the user choosing `pregnancy_possibility = confirmed`. It is never inferred from a late period. A positive test is treated as stale once ≥ 2 periods are logged after it; this is surfaced as a data gap.
3. **Acute windows.** URGENT/EMERGENCY escalations consider only the last 2 days of symptoms (7 days for pregnancy bleeding). Older red flags downgrade to `MEDICAL_REVIEW` for discussion.
4. **MR-003 heavy bleeding** needs a recognised NHS indicator. The user labelling a single day "heavy" is not enough; "very heavy" on ≥ 2 days counts. The acute escalations (dizziness → URGENT, fainting → EMERGENCY) are not taken verbatim from the NHS heavy-periods page and are flagged `clinical review required` in `rules.json`.
5. **MR-006 severe pain** follows NHS period pain (non-urgent: affects daily activities; urgent: severe and painkillers not helping) and NHS ectopic guidance (possible pregnancy + severe or sudden pain).
6. **MR-007 missed periods.** The reference cycle is the recorded median (≥ 3 cycles), then the user-reported typical length, then 28. It is clamped to 21–45 days. There is a 7-day grace period before a period counts as late. Breastfeeding and hormonal contraception downgrade the finding to `MONITOR` with an explanatory note.
7. **MR-011 was added** for pain during sex, urination or bowel movements, which NHS lists as reasons to see a GP. The PRD mentions these symptoms but has no rule for them.
8. **PCOS → PMOS.** In 2026 the NHS renamed PCOS to *polyendocrine metabolic ovarian syndrome*. The system prompt and AI validator cover both names.
9. **Deterministic AI safety.** User messages are screened for emergencies (EN + Egyptian/MSA Arabic) *before* any LLM call. Questions like "can I get pregnant if…?" are deliberately not treated as emergencies. AI output is checked for diagnoses, pregnancy or fertility status, dismissals, guarantees and prescription advice. Hedged phrasing ("this does not mean you have…", "if you are pregnant…") is allowed.

## Data model (vs PRD §32)

- `gen_random_uuid()` (built into PostgreSQL 13+) is used instead of the `uuid-ossp` extension. Some managed or embedded Postgres builds don't ship `uuid-ossp`.
- `health_profiles.pregnancy_possibility` is TEXT (`no | yes | unsure | confirmed | prefer_not_to_say`), not a boolean, to match PRD §10. Added `contraception_started_on` (lets predictions exclude older cycles), `typical_cycle_length` and `typical_period_length` (onboarding screen 2).
- `bleeding_logs` gained explicit NHS indicator flags: `frequent_changes`, `double_protection`, `night_changes`, `affects_daily_life`. There is one bleeding log per user per day.
- `symptom_logs.affects_daily_activity` was added (needed by MR-006).
- `medical_findings` gained `rule_version`, `content_version`, `recommended_action`, `is_emergency`, `language`, `acknowledged_at` and `updated_at` (PRD §66–67).
- New tables: `refresh_tokens` (rotation with reuse detection) and `audit_logs` (hashed IPs only).
- Findings are never deleted when they stop applying. They are marked `resolved` for auditability; deleting the account removes them.

## Not yet built

- Expo / React Native app (Arabic-first RTL): next milestone.
- Notification scheduler and push (the `reminders` table exists).
- Apple / Google Sign-In (needs client IDs).
- PDF doctor report (needs Arabic text shaping). JSON and text versions exist.
- Region-specific emergency numbers (currently generic "local emergency number").
- Offline sync queue (a client concern).
- Moving the rate limiter to Redis before running more than one API instance.
