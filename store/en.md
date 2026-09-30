# Dawrati — Cycle tracking and health education

**Version:** 0.1 (beta) · **Primary language:** Arabic (ar-EG) · **Supported:** Arabic, English

## About

An app to track your menstrual cycle and symptoms, explain what you record in
plain language, and remind you of what is coming. **It is not a diagnosis and
not a substitute for your doctor**, and it never prescribes medicines or doses.

## Features

- **Simple logging:** first day of bleeding, duration, and daily symptoms.
- **Clear reading of your data:** you see what you actually recorded, with no
  hidden inference.
- **Private reminders:** notification text on the lock screen reveals no health
  information at all.
- **Explanations from approved sources only:** any medical statement must come
  from a chunk approved by a physician or a trusted body. Otherwise the app says
  plainly: *"I do not have an approved, reliable source."*
- **Immediate emergency guidance:** when dangerous symptoms are mentioned
  (heavy bleeding, fainting, sudden severe pain, shoulder pain with possible
  pregnancy, self-harm thoughts) a fixed message appears with the emergency
  number, without waiting for any model call.

## Language and interface

- The interface is fully Arabic, right-to-left, with English available from the
  language button.
- Digits: Western (0-9) by default; Arabic-Indic (٤٥) is a user setting.
- Calendar: Gregorian. Week starts on Saturday (configurable).
- Phone numbers are direction-isolated so they never reverse inside Arabic text.

## Privacy

- No name and no email are requested. Data is stored under a random device key.
- No identifying data is ever sent to an AI provider, and your text is treated
  as data, never as instructions.
- Operational logs contain no message text.

## Explicit limits

- The app **does not diagnose** and does not rule out conditions, and it does
  not recommend medicines or doses.
- Emergency numbers are shown together with their verification status; when a
  number is not verified, the app says so.
- Cycle information is quoted from approved sources only; when no source exists,
  the app says so instead of answering from general knowledge.

## Support

Questions or issues: [put support email here] — to be added before release.

---

**Required before release:** [ ] physician review of fixed responses, [ ] legal
sign-off on policies, [ ] verification of emergency and crisis numbers, [ ]
Arabic store screenshots.
