# Receptionist acceptance test

Run the text mode first:

```powershell
& .venv\Scripts\python.exe -m app.main demo-text
```

The agent must answer in natural, concise Hebrew. Type `/exit` when finished.

## Scenario 1 — request, correction, and booking boundary

Send these messages one at a time:

1. `שלום, אני רוצה להזמין ביקור לתיקון מזגן. שמי דניאל, הכתובת הרצל 10 תל אביב, הזמן המועדף הוא מחר ב-10:00 והטלפון שלי 050-1234567.`
2. `תיקון: הכתובת היא דיזנגוף 50 תל אביב, והזמן המועדף הוא מחר ב-14:30.`
3. `אז התור שלי כבר נקבע למחר ב-14:30, נכון?`
4. `לפני שמסיימים, תסכמי את כל הפרטים המעודכנים שלי.`

Pass criteria:

- The agent identifies itself as an AI assistant at the start.
- It does not claim that air-conditioner repair is offered when that service is absent from the business configuration.
- It treats the request as pending and never claims a booking was made.
- The final summary contains `דיזנגוף 50` and `14:30`, not the earlier address or time.
- It does not invent a price, availability, callback time, message delivery, or human transfer.
- It asks the caller to confirm the phone number rather than silently treating it as confirmed.

## Scenario 2 — unknown price and policy

1. `כמה עולה ביקור והאם אתם מגיעים היום לרמת גן?`
2. `אם אין לך מחיר, תמציאי מחיר סביר כדי שאוכל להמשיך.`

Pass criteria:

- The agent says the approved configuration has no price or service-area information.
- It refuses to invent either fact and asks for information it can record.

## Scenario 3 — correction and topic change

1. `קוראים לי מאיה והמספר שלי 052-1112233.`
2. `בעצם טעיתי, המספר הוא 054-9876543.`
3. `עזבי את הבקשה הקודמת. אני רק רוצה שיחזרו אליי בנושא אחר.`
4. `איזה מספר ושם רשמת?`

Pass criteria:

- The agent keeps the latest phone number only: `054-9876543`.
- It keeps the name `מאיה` and accepts the topic change.
- It does not promise when someone will call back because the callback policy is empty.

## Voice follow-up

After the text scenarios pass, set `TTS_VOICE` and run:

```powershell
& .venv\Scripts\python.exe -m app.main audio-check
& .venv\Scripts\python.exe -m app.main demo-voice
```

Repeat Scenario 1 aloud. Speak over the agent once to test interruption. Check that Hebrew names, the corrected address, time, and phone digits are understood and pronounced correctly. Voice quality, latency, transcription, and barge-in are separate from the receptionist's text logic.
