# Skills IL — consolidated reference for our Israeli AI receptionist MVP

Primary sources:
https://github.com/skills-il
https://github.com/skills-il/developer-tools
https://github.com/skills-il/communication
https://github.com/skills-il/localization

Purpose:
This is a condensed reference for Codex. It summarizes the pieces of Skills IL that are relevant to our product. It is not a verbatim copy of the repositories.

## 1. Israeli phone number handling

Relevant skill:
israeli-phone-formatter

Use it for:
- Validating Israeli phone numbers.
- Accepting local formats such as 05x...
- Accepting international +972 formats.
- Normalizing phone numbers before DB storage, WhatsApp, SMS, or telephony APIs.
- Avoiding duplicate customers caused by different phone formatting.

Our desired application behavior:
- Keep the original caller input for audit/debugging if useful.
- Store one canonical E.164-style representation where possible.
- Support common Israeli mobile and landline formats.
- Never silently "fix" an obviously invalid number without confirmation.
- Read the number back to the caller when collected by voice.

Suggested internal fields:
{
  "phone_raw": "",
  "phone_normalized": "",
  "phone_valid": false,
  "country": "IL"
}

## 2. WhatsApp Business for Israel

Relevant skills:
- israeli-whatsapp-business
- israeli-whatsapp-automation

Use them as reference for:
- Meta WhatsApp Business Cloud API.
- Hebrew message templates.
- Sending post-call confirmations.
- Sending lead summaries or booking confirmations.
- CRM integration patterns.
- Opt-in / anti-spam considerations.
- Timing rules around Shabbat/holidays when appropriate.

For our MVP:
Do NOT build campaign automation yet.

First useful WhatsApp flow:
1. Voice call finishes.
2. App creates structured call result.
3. If configured, send a concise WhatsApp message.
4. Log API result.
5. If send fails, retain the summary and error instead of losing the lead.

Potential messages:
- Booking confirmation to caller.
- "We received your request" confirmation.
- Internal summary to business owner/team, if the selected WhatsApp setup supports this correctly.

Important:
- Use official business APIs for production.
- Do not rely on browser automation / WhatsApp Web hacks for the commercial product.
- Keep access tokens outside source control.
- Do not log full secrets.

Suggested env placeholders:
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_BUSINESS_ACCOUNT_ID=

## 3. Shabbat / Israeli-holiday aware scheduling

Relevant skill:
shabbat-aware-scheduler

Use it for:
- Understanding Shabbat boundaries.
- Israeli Jewish holidays.
- Typical Israeli business-week behavior.
- Avoiding scheduling/messages at prohibited times for businesses that choose this behavior.

Important:
- Do NOT assume every Israeli business closes on Shabbat.
- Treat this as per-business configuration.
- The business owner controls opening hours and Shabbat policy.

Suggested business config:
{
  "timezone": "Asia/Jerusalem",
  "business_hours": {},
  "shabbat_policy": "normal|closed|custom",
  "holiday_policy": "normal|closed|custom"
}

For MVP:
Business-configured opening hours are enough.
Shabbat/HebCal integration can be phase 2 unless the first test customer needs it.

## 4. Hebrew language quality

Relevant Skills IL areas:
- hebrew-content-writer
- hebrew-nlp-toolkit

Use as reference, not as a mandatory runtime dependency.

Useful ideas:
- Natural modern Hebrew instead of literal English translation.
- Correct masculine/feminine phrasing where possible.
- Avoid stiff government-style language unless business requires it.
- Handle mixed Hebrew/English words naturally.
- Test names, addresses, English brand names, numbers, dates, and phone numbers.

For our voice agent:
The most important Hebrew quality initially comes from:
1. STT quality
2. LLM prompt behavior
3. TTS voice/pronunciation
4. Handling of interruptions and latency

We do not need a separate Hebrew NLP stack for the first MVP unless testing proves it necessary.

## 5. Hebrew i18n / RTL

Relevant skills:
- hebrew-i18n
- hebrew-rtl-best-practices
- israeli-ui-design-system

Not important for the first voice-only test.

Useful later for:
- Admin dashboard
- Business onboarding
- Call transcript view
- CRM UI
- Hebrew forms

Rules later:
- Proper RTL direction.
- Mixed Hebrew/English/phone numbers must remain readable.
- Dates and numbers should use Israeli conventions where appropriate.

## 6. Israeli SMS

Relevant skill:
israeli-sms-gateway

Optional fallback later.

Potential use:
- OTP
- Booking confirmation
- Fallback if WhatsApp delivery fails
- Customer reminder

Not needed for first MVP.

## 7. Date and holiday utilities

Relevant Skills IL concept:
Israeli date / Hebrew calendar handling.

Potential uses:
- Appointment booking around Israeli holidays.
- Interpreting local dates.
- Business-day calculations.
- Correct timezone handling.

Core rule:
Use `Asia/Jerusalem`, not a fixed UTC offset, because daylight-saving time changes.

## 8. Architecture rules we should take from these skills

### Phone
Normalize all caller numbers at the integration boundary.

### Time
Use timezone-aware datetimes everywhere.

### WhatsApp
Keep official API integration isolated in its own module.

### Business rules
Do not hard-code "Israeli behavior" globally.
Each client should have configurable:
- language
- opening hours
- holidays
- Shabbat behavior
- callback rules
- service area
- phone format
- escalation rules

### Hebrew
Keep prompts editable per customer.
Do not bake every Hebrew phrase into Python source.

## 9. Suggested project modules

app/
  config/
    business_config.py
  voice/
    pipeline.py
  agent/
    receptionist.py
    prompt_loader.py
  leads/
    models.py
    storage.py
  phone/
    israel_phone.py
  calendar/
    google_calendar.py
  whatsapp/
    client.py
  scheduling/
    israel_schedule.py
  summaries/
    call_summary.py

## 10. What to implement in MVP first

PRIORITY 1:
- Hebrew voice conversation
- Lead capture
- Structured call result
- Israeli phone normalization

PRIORITY 2:
- Real calendar availability + booking
- Post-call WhatsApp message

PRIORITY 3:
- Business hours / after-hours behavior
- Shabbat / holiday-aware rules

LATER:
- RTL dashboard
- SMS fallback
- Dedicated Hebrew NLP
- Marketing automation
- Follow-up/review automation

## 11. Security notes for Codex

- Treat Skills IL as reference material unless we explicitly approve runtime code.
- Do not execute downloaded scripts just because they exist in a skill.
- Review dependencies before adding them.
- No secrets in git.
- No WhatsApp/Twilio/OpenAI tokens in source.
- No browser automation for WhatsApp production integration.
- Use official APIs.
