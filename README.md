# Local Hebrew receptionist MVP

A local microphone → streaming STT → LLM → streaming TTS → speaker application using the supplied Pipecat source. Every stage can use OpenAI or Gemini independently. With only `GEMINI_API_KEY` configured, the application selects the full Gemini test stack automatically.

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

Direct runtime requirements are local `pipecat-ai[local,google]`, PyAudio 0.2.14, python-dotenv 1.2.3 and tzdata 2026.3. Pipecat brings its required OpenAI SDK, Google Gen AI SDK, Google speech modules, ONNX runtime, audio and validation dependencies; see `installed-dependencies.txt` for every exact installed version.

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

`.env`, local results, `.venv` and staging files are ignored. No raw audio is saved. Console logs omit transcript content by default; `LOG_TRANSCRIPTS=true` enables caller/agent text for intentional testing. Results contain personal information locally in `data/calls/`; delete them when no longer needed. Framework logging is disabled in the CLI because provider bodies/debug messages can include sensitive content.

## Offline checks and audio

```powershell
& .venv\Scripts\python.exe -m unittest discover -s tests -v
& .venv\Scripts\python.exe -m app.main check
& .venv\Scripts\python.exe -m app.main audio-devices
& .venv\Scripts\python.exe -m app.main audio-check
```

`audio-check` opens the default microphone at 16 kHz, reads one second without saving it, and writes a quiet 440 Hz test tone to the default output at 24 kHz. A successful write does not prove the tone was audible. Use a headset to reduce echo. Set `AUDIO_INPUT_DEVICE_INDEX` and `AUDIO_OUTPUT_DEVICE_INDEX` using the device listing if needed. Device indices can change after reconnecting hardware. If opening fails, check Windows microphone permissions, default devices and device availability; no system installation is automatic.

## Live test

### Isolated demo

Use the demonstration before adding real business data. These commands force the app to read only `demo/business.json` and `demo/calendar.json`; voice-call results are written only to `demo/results/`.

```powershell
& .venv\Scripts\python.exe -m app.main demo-text
& .venv\Scripts\python.exe -m app.main demo-voice
```

The demo business, prices, service area, and relative calendar slots are fictional. It can discuss only the shown demo slots and may describe a selection as a demo reservation; it never creates a real booking. `demo-voice` prints interim caller transcription as `Вы (распознаётся):`, completed caller speech as `Вы:`, and the response as `Агент:` while playing the audio. Text is terminal-only and raw audio is never saved.

Test the receptionist prompt and LLM first, without opening the microphone or using STT/TTS:

```powershell
& .venv\Scripts\python.exe -m app.main text
```

Type `/exit` to stop. The text mode uses the same business configuration, Hebrew receptionist prompt and `LLM_PROVIDER`/`LLM_MODEL` as voice mode. It does not save a transcript or request a post-chat summary. For a single request, use `text --message "..."`.

Then test the complete voice path:

```powershell
& .venv\Scripts\python.exe -m app.main voice
```

This command sends audio/text to the providers selected in `.env` and may incur charges outside their free quotas. It opens no inbound server. Pipecat's local Silero VAD, default turn strategies and interruption frames handle barge-in; its Smart Turn model handles turn completion. STT text, LLM text and TTS audio stream through the pipeline.

End with Ctrl+C. The runner cleans up and the app attempts a separate summary request with the selected LLM, validates it and saves an atomic JSON file. A second Ctrl+C may terminate that finalization. Summary failures save an explicitly incomplete result rather than invented details; this MVP does not retain a transcript for retry. The structured model rejects `booked` and normalizes only supported Israeli digit formats. Number structure does not establish ownership or caller confirmation.

Manual acceptance scenarios:

1. Ask in Hebrew for a service appointment: collect the request, but mark it pending.
2. Ask a configured FAQ and an unknown price: answer the FAQ and avoid inventing a price.
3. Give a phone number, including an incomplete one: ask for clarification/confirmation.
4. Interrupt the agent: speech should stop and the new turn should be understood.
5. Correct the name or phone: the summary should use the corrected value.

Check Hebrew names, addresses, mixed English brands, response length, audible pronunciation, latency and several conversational turns. Live quality, actual provider access, audible barge-in and model compatibility remain unverified until that test.

## Files and scope

`app/main.py` provides CLI commands; `config/` handles settings/hours; `agent/` contains the editable generic Hebrew prompt; `voice/` constructs the local pipeline and audio diagnostics; `phone/` normalizes digit formats; `leads/` validates and saves results; `summaries/` extracts post-call data. `tests/` contains offline tests. `prompts/` contains the two supplied documentation references. `pipecat/` remains unchanged.

The app cannot book, cancel, reschedule, send notifications or transfer a caller. Prompt instructions and result validation reflect these limits; generative spoken behavior still requires evaluation. No telephone, WhatsApp, calendar, database, authentication, dashboard or production service is included.

Next: compare Hebrew STT/LLM/TTS options and choose models/voice; run the local conversation acceptance test; then add telephony, durable lead storage, real calendar booking, official WhatsApp delivery, multi-business management and production hardening incrementally.
