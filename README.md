# Local Hebrew receptionist MVP

A local microphone → streaming STT → local language/terminology lookup → LLM → streaming TTS → speaker application using the supplied Pipecat source. Hebrew is the default language; the receptionist can continue in Russian or English when the caller clearly switches. Every AI stage can use OpenAI or Gemini independently. With only `GEMINI_API_KEY` configured, the application selects the full Gemini test stack automatically.

## Environment and setup

Use standard CPython 3.13 x64 (tested with 3.13.15), not free-threaded Python. Run commands in PowerShell from `C:\codexprojects\voiceaipipecat`:

```powershell
& 'C:\Users\leoni\AppData\Local\Programs\Python\Python313\python.exe' -m venv .venv
```

The existing `.venv` is already prepared; do not recreate it for normal use. Use `.venv\Scripts\python.exe` for every project Python command; activation and uv are unnecessary.

For an approved fresh installation:

```powershell
& .venv\Scripts\python.exe -m app.prepare_framework
& .venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock.txt
& .venv\Scripts\python.exe -m pip install --no-deps .build/pipecat
& .venv\Scripts\python.exe -m pip check
```

The lock contains the installed third-party runtime dependencies and download hashes. Local Pipecat is installed separately; `framework-source.json` fingerprints every supplied source file. Its archive lacks Git metadata and reports `0.0.0.dev0`, which is **not an upstream release identification**. `app.prepare_framework` copies the source into `.build/` and adjusts only that copy's manifest to include the existing Silero and Smart Turn ONNX models omitted by archive-based wheel building. It never edits `pipecat/`. Pipecat's declared setuptools/setuptools-scm build requirements are installed by pip in build isolation.

Direct runtime requirements are local `pipecat-ai[local,google,websocket]`, PyAudio 0.2.14, python-dotenv 1.2.3, tzdata 2026.3, Twilio 9.11.1 and python-multipart 0.0.32. Pipecat brings its required OpenAI SDK, Google Gen AI SDK, Google speech modules, ONNX runtime, audio and validation dependencies; see `installed-dependencies.txt` for every exact installed version.

## Configuration

```powershell
Copy-Item .env.example .env
& .venv\Scripts\python.exe -m app.main check
```

`STT_PROVIDER`, `LLM_PROVIDER`, and `TTS_PROVIDER` accept `openai` or `gemini`. If only `GEMINI_API_KEY` is populated and provider/model fields remain blank, the test configuration is:

- STT: `gemini-3.5-transcribe-live`
- LLM and post-call summary: `gemini-3.6-flash`
- TTS: `gemini-3.1-flash-tts-preview`, voice `Kore`

These are overridable test defaults from the supplied Pipecat integration, not final production selections. Set provider, model, and voice fields explicitly to compare alternatives. A mixed provider stack requires the corresponding API keys. Gemini free-tier availability and quotas depend on the model, project, account, and region; 429 rate-limit errors leave the session open so another turn can be attempted after the delay reported by Google.

Edit `app/config/business.example.json`, or set `BUSINESS_CONFIG_PATH` to another configuration. The sample has intentionally empty services, prices, and FAQ; add approved information before evaluating business answers. Weekday keys use 0=Monday through 6=Sunday. Hours are local HH:MM, opening inclusive and closing exclusive; overnight intervals are supported. Missing days are closed. Saturday is configurable. `Asia/Jerusalem` uses timezone rules, not a fixed UTC offset. After-hours status is measured at call start; holidays and precise interpretation of requested appointment dates remain future work.

Business configuration also defines the primary and supported languages, service descriptions, known prices and price rules, appointment duration, service area, FAQ, after-hours/urgent/callback policies, and a calendar provider placeholder. `hours_status()` exposes `is_open_now`, `after_hours`, and the next opening time. The legacy JSON key `language` remains accepted, while new configurations use `primary_language`.

## Local language knowledge

The small JSONL dataset under `knowledge/` covers everyday Israeli Hebrew, plumbing terms, informal problem descriptions, normalization aliases, and smaller Russian and English plumbing vocabularies. It is loaded and cached locally. Each completed caller turn uses exact/alias matching, keyword matching, and conservative typo matching; at most five relevant entries are added to a temporary copy of that turn. Nothing is added when neither terminology nor a language/register change needs context. No vector database, embedding call, or separate LLM lookup is used.

Hebrew remains the default. A single foreign word, brand, technical term, or common mixed-language product name does not switch the conversation. An explicit request switches immediately; otherwise a substantive Russian or English sentence is required. Register adaptation becomes casual only after repeated evidence. The agent may sparingly mirror only caller-used terms marked both `agent_can_use` and `safe_to_mirror`; profanity is never eligible. Conversation history remains in the original Pipecat context across language changes.

For the current OpenAI stack, the STT language hint is intentionally omitted so `gpt-live-transcribe` is not pinned to Hebrew. OpenAI documents support for multiple language hints, but Hebrew/Russian/English code-switching accuracy still needs live testing. `gpt-4o-mini-tts` accepts Hebrew, Russian, and English text; OpenAI notes that its built-in voices are optimized for English, so the current `alloy` voice should be evaluated separately in each language. The provider/model/voice remain configurable and no new service was added.

`.env`, local results, `.venv` and staging files are ignored. No raw audio is saved. Console logs omit transcript content by default; `LOG_TRANSCRIPTS=true` enables caller/agent text for intentional testing. Results contain personal information locally in `data/calls/`; delete them when no longer needed. Framework logging is disabled in the CLI because provider bodies/debug messages can include sensitive content.

## Offline checks and audio

```powershell
& .venv\Scripts\python.exe -m unittest discover -s tests -v
& .venv\Scripts\python.exe -m app.main check
& .venv\Scripts\python.exe -m app.main audio-devices
& .venv\Scripts\python.exe -m app.main audio-check
```

`audio-check` opens the default microphone at 16 kHz, reads one second without saving it, and writes a quiet 440 Hz test tone to the default output at 24 kHz. A successful write does not prove the tone was audible. Use a headset to reduce echo. Set `AUDIO_INPUT_DEVICE_INDEX` and `AUDIO_OUTPUT_DEVICE_INDEX` using the device listing if needed. Device indices can change after reconnecting hardware. If opening fails, check Windows microphone permissions, default devices and device availability; no system installation is automatic.

### Isolated Hebrew phone-number TTS check

Run this only when you want to spend TTS API credit and listen through the configured local speaker:

```powershell
.venv\Scripts\python.exe -m app.main phone-tts-test
```

It makes exactly 60 TTS-only requests: ten synthetic mobile-number patterns, each in grouped numeric and Hebrew digit-by-digit forms, repeated three times. It does not open the microphone, STT, LLM, receptionist pipeline or business data. It prints every exact input before playing it and writes WAV files to `data/tts_phone_tests/`. Preview the plan and estimated cost without a provider request via:

```powershell
.venv\Scripts\python.exe -m app.main phone-tts-test --dry-run
```

The Hebrew digit form uses feminine numbers (for example, `אחת`, `שתיים`, `שלוש`), following the Academy of the Hebrew Language's rule for telephone numbers as [numbers in isolation](https://hebrew-academy.org.il/meeting/%D7%A6%D7%91/). This diagnostic does not change the production phone formatter.

The production TTS path now converts any structurally valid Israeli phone number to digit words immediately before synthesis. Hebrew, Russian and English use separate vocabularies; English always says `zero`, never `O`, and no language sends a raw number that could be read as hundreds. The canonical stored number and assistant transcript remain unchanged. Ambiguous or incomplete spoken digits are never guessed: STT receives multilingual number-form context, while the receptionist asks the caller to repeat uncertain digits and explicitly confirm the final number.

## Live test

### Business actions and future integrations

The voice LLM receives explicit Pipecat tools for `check_availability`, `create_appointment`, `get_appointment`, `cancel_appointment`, `reschedule_appointment`, `save_lead`, and `get_business_info`. Those functions validate their input and return one of `succeeded`, `failed`, `needs_clarification`, or `no_availability`. The LLM never receives a calendar or storage object directly.

Normal application configuration uses an unavailable calendar until a real provider is configured, so it cannot claim a booking. The isolated demo uses an in-memory calendar initialized from `demo/calendar.json`; it supports conflict-free availability, creation, lookup, cancellation and rescheduling without external calls. A successful appointment status is copied into the final call result only from the trusted action layer. An unsupported model-generated `booked`, `cancelled`, or `rescheduled` value is downgraded to `pending` when no tool operation succeeded.

`GoogleCalendarAdapter` defines the stable provider boundary. Its future authenticated gateway receives caller name, normalized phone, service, address, language, notes and timezone-aware start/end information. Adding Google credentials and the provider SDK later changes only that gateway and factory wiring. `GOOGLE_CALENDAR_CREDENTIALS_FILE` and `GOOGLE_CALENDAR_ID` are reserved in `.env.example`; neither is used for an API request yet.

The WhatsApp boundary contains `send_customer_confirmation` and `send_business_summary`. Its mock records messages in memory for tests, while normal runs use an unconfigured implementation that returns `failed`. `WHATSAPP_ACCESS_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID` are placeholders only; no Meta request exists in this milestone.

Pipecat's service and user-to-bot observers now log numeric timing only: service TTFB, LLM first answer token when the provider reports it, TTS first audible audio, turn-detection/transcription time, tool duration and total user-stop-to-first-audio latency. Logs include processor/model names and seconds, without transcript content, caller fields, or credentials.

### Twilio phone test through ngrok

Pipecat already supplies the WebSocket audio transport, Twilio serializer, 8 kHz mu-law conversion, interruption frames and call hang-up support. The application supplies the missing phone-facing layer: a signed Twilio webhook, TwiML, a signed Media Streams WebSocket endpoint, business configuration, pipeline lifecycle and result storage. Twilio requires a secure `wss` URL for Media Streams and recommends validating the `X-Twilio-Signature` header with its SDK; see the official [Stream documentation](https://www.twilio.com/docs/voice/twiml/stream) and [security guide](https://www.twilio.com/docs/usage/security).

Add these values to `.env` without committing or printing them:

```dotenv
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PUBLIC_URL=
TELEPHONY_HOST=127.0.0.1
TELEPHONY_PORT=8000
NGROK_AUTHTOKEN=
```

Leave `TWILIO_PUBLIC_URL` blank when using ngrok normally; the app uses ngrok's forwarded HTTPS host. Set it to the tunnel's HTTPS origin if a reverse proxy does not preserve forwarded headers. Start the server in one terminal:

```powershell
& .venv\Scripts\python.exe -m app.main demo-telephony
```

Start the tunnel in a second terminal:

```powershell
& .\scripts\start-ngrok.ps1
```

Copy the generated HTTPS origin into the Twilio phone number's incoming Voice webhook as `https://YOUR-NGROK-HOST/twilio/voice`, method `POST`. Call the number. Twilio requests the webhook, receives a bidirectional `<Connect><Stream>` response, and opens `wss://YOUR-NGROK-HOST/twilio/media`. The app validates both Twilio signatures before it accepts audio. The local microphone and speaker are not used during a phone call; the caller's phone supplies input and output. Use `demo-telephony` to isolate the call to `demo/` data and results.

The official ngrok executable is not bundled. Install it once and either put its authtoken in the ignored `.env` or authenticate the CLI with `ngrok config add-authtoken`. A random free ngrok URL can change after restart, so update the Twilio webhook when it does. Keep both terminals open for the duration of the call.

For this first Windows phone test, ngrok is the recommended tunnel: one `ngrok http 8000` endpoint carries both HTTPS webhook traffic and WSS media, and its local inspector helps diagnose Twilio requests. Cloudflare Quick Tunnel also proxies localhost and is useful without an account, while a named Cloudflare Tunnel is a better later choice when a stable hostname and Cloudflare controls are needed. Neither tunnel client is installed by this project. Expose only local port `8000`; the application itself remains bound to `127.0.0.1`.

Expected lifecycle logs contain `Incoming call received`, the Twilio `CallSid`, `Twilio media stream connected`, `Caller hangup` when the caller ends the call, and `Twilio media stream disconnected`. Authentication tokens and caller phone numbers are not logged.

### Isolated demo

Use the demonstration before adding real business data. These commands force the app to read only `demo/business.json` and `demo/calendar.json`; voice-call results are written only to `demo/results/`.

```powershell
& .venv\Scripts\python.exe -m app.main demo-text
& .venv\Scripts\python.exe -m app.main demo-voice
```

The demo business, prices, service area, and relative calendar slots are fictional. The demo action layer can modify its in-memory calendar for booking-flow tests; no real calendar event is created. Availability must come from the tool each time, so a newly occupied slot is no longer offered during that run. `demo-voice` prints interim caller transcription as `Вы (распознаётся):`, completed caller speech as `Вы:`, and the response as `Агент:` while playing the audio. Text is terminal-only and raw audio is never saved. The receptionist does not announce that the conversation is a test.

Test the receptionist prompt and LLM first, without opening the microphone or using STT/TTS:

```powershell
& .venv\Scripts\python.exe -m app.main text
```

Type `/exit` to stop. The text mode uses the same business configuration, multilingual receptionist prompt, local knowledge lookup and `LLM_PROVIDER`/`LLM_MODEL` as voice mode. It does not save a transcript or request a post-chat summary. For a single request, use `text --message "..."`.

Then test the complete voice path:

```powershell
& .venv\Scripts\python.exe -m app.main voice
```

This command sends audio/text to the providers selected in `.env` and may incur charges outside their free quotas. It opens no inbound server. Pipecat's local Silero VAD, default turn strategies and interruption frames handle barge-in; its Smart Turn model handles turn completion. STT text, LLM text and TTS audio stream through the pipeline.

After collecting the relevant details, the receptionist asks whether anything else is needed. When the caller declines or says goodbye, the agent gives its final goodbye and the session closes after four seconds of silence. Speaking during that grace period cancels the automatic close and continues the conversation. Ctrl+C remains available for manual termination. The runner then attempts a separate summary request with the selected LLM, validates it, merges only trusted calendar-action state and atomically saves JSON and text reports. Each report begins with the structured result and ends with the completed caller and agent transcript; raw audio and partial interim transcription are not retained. A second Ctrl+C may terminate that finalization. Summary failures save an explicitly incomplete result rather than invented details. The structured model normalizes only supported Israeli digit formats. Number structure does not establish ownership or caller confirmation.

Manual acceptance scenarios:

1. In normal mode, ask for a service appointment and verify it remains pending while the calendar is unconfigured. In demo mode, confirm that only a successful mock tool result becomes booked.
2. Ask a configured FAQ and an unknown price: answer the FAQ and avoid inventing a price.
3. Give a phone number, including an incomplete one: ask for clarification/confirmation.
4. Interrupt the agent: speech should stop and the new turn should be understood.
5. Correct the name or phone: the summary should use the corrected value.
6. Give a name and address in Hebrew, ask `Можно по-русски?`, and verify the agent answers in Russian without asking for those details again.
7. Ask a substantive question in English, then return to a full Hebrew sentence and verify a natural language switch in both directions.
8. Say a Hebrew sentence containing `WhatsApp`, `Google`, or `Wi-Fi` and verify that the agent remains in Hebrew.
9. Describe a blocked sink informally in each supported language and verify that the agent clarifies symptoms without claiming a diagnosis.
10. Use casual Hebrew across several turns, then use profanity; verify that tone becomes only slightly more casual and profanity is not repeated.

Check Hebrew names, addresses, mixed English brands, response length, audible pronunciation, latency and several conversational turns. Live quality, actual provider access, audible barge-in and model compatibility remain unverified until that test.

## Files and scope

`app/main.py` provides CLI commands; `config/` handles settings/hours; `agent/` contains the editable multilingual prompt with Hebrew priority; `actions/` owns validated side effects and Pipecat tools; `calendar/` contains provider-neutral models, interfaces, mock and Google adapter boundary; `messaging/` contains the WhatsApp interface/mock; `diagnostics/` records latency metrics; `knowledge/` implements local lookup and language/register state; `voice/` constructs the shared local/telephone pipeline; `telephony/` provides the Twilio webhook and Media Streams adapter; `phone/` normalizes digit formats; `leads/` validates and saves results; `summaries/` extracts post-call data. The root `knowledge/` directory contains the JSONL data and source notes. `tests/` contains offline tests. `prompts/` contains the supplied documentation references. `pipecat/` remains unchanged.

The normal app cannot make a real booking or send a notification until provider gateways are configured. The demo can exercise booking, cancellation and rescheduling against memory only. Twilio inbound phone transport is included for later development through ngrok. A real Google Calendar gateway, real WhatsApp adapter, database, authentication, dashboard and production hosting are not included.

Next: install/authenticate ngrok, add Twilio credentials and connect the phone-number webhook; then compare Hebrew STT/LLM/TTS options, add durable lead storage, real calendar booking, official WhatsApp delivery, multi-business management and production hardening incrementally.
