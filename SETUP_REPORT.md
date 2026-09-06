# Offline setup result

Completed using standard CPython 3.13.15 x64 (`Py_GIL_DISABLED=0`) and the workspace `.venv`. No AI API requests were made, and no final STT/LLM/TTS model or voice was selected.

## Created files

- `.gitignore`, `.env.example`
- `README.md`, `SETUP_REPORT.md`
- `requirements.in`, `requirements.lock.txt`, `installed-dependencies.txt`
- `framework-source.json` (SHA-256 fingerprints of 1,668 supplied Pipecat files)
- `app/main.py`, `app/prepare_framework.py`
- `app/config/settings.py`, `business_config.py`, `business.example.json`
- `app/agent/prompt_loader.py`, `receptionist_he.txt`
- `app/voice/pipeline.py`, `audio_check.py`
- `app/phone/israel_phone.py`
- `app/leads/models.py`, `storage.py`
- `app/summaries/call_summary.py`
- Python package `__init__.py` files under app and its subdirectories
- `tests/test_offline.py`
- Generated/ignored `.venv/` and `.build/` with staging source and pip installation report

The supplied `pipecat/` and `prompts/` remain unchanged. No `.env` containing credentials was created.

## Dependencies

Installed local Pipecat `0.0.0.dev0` (source archive fallback version, not an identified upstream release), PyAudio 0.2.14, python-dotenv 1.2.3, tzdata 2026.3 and necessary framework/transitive dependencies. OpenAI SDK is 2.54.0; Pydantic is 2.13.5; ONNX Runtime is 1.24.4.

`installed-dependencies.txt` lists all exact installed versions, including pip. `requirements.lock.txt` contains the 60 third-party runtime package pins and source archive/wheel hashes; Pipecat is rebuilt separately from the fingerprinted local snapshot. No uv or global software was installed.

## Verification

- 13 offline unit tests passed.
- Pipeline construction succeeded with socket connections blocked, loading local Silero and Smart Turn models.
- VAD turn-start signaling enables interruptions.
- Worker start/end lifecycle passed without provider services.
- Summary adapter tested with a mocked response; an empty transcript does not construct an API client.
- Phone format validation, unconfirmed/invalid numbers, rejection of booked status, atomic Hebrew JSON storage, timezone conversion, opening/closing boundaries, overnight hours and configurable Saturday opening passed.
- `pip check`: no broken requirements.
- Offline configuration check clearly reports the unset model, voice and key variables.
- Default microphone opened at 16 kHz and captured one second (peak amplitude 165); no audio was saved.
- Default speaker opened at 24 kHz and accepted a short test tone. Actual audibility requires human confirmation.
- Pipecat source fingerprint verification passed for all 1,668 files.

## Warnings and resolved errors

- Sandbox execution of the supplied interpreter was initially denied. Approved elevated execution succeeded; Python was not missing or replaced.
- Source-archive wheel building initially omitted existing ONNX assets. The staging manifest includes those two files now; original source is unchanged. The rebuilt package passed construction checks.
- The offline suite emits an internal Pipecat `AudioContextTTSService` deprecation warning. Application code does not import that deprecated class directly.
- Initial test fixtures used an unsupported event-handler argument and a base processor that did not forward frames; both fixtures were corrected. The final suite passes.
- Environment-report generation initially encountered Windows default text encoding; explicit UTF-8 resolved it.

## Before the first real conversation

Choose providers and exact STT model, LLM model, TTS model and voice after comparing Hebrew accuracy/pronunciation, streaming latency, turn/interruption behavior, reliability, cost and compatibility. Only OpenAI adapters are implemented; another provider needs its adapter and any additional dependencies reviewed separately. No particular model is a default or final selection.

For the implemented OpenAI path, supply exactly one credential: `OPENAI_API_KEY`. Set `STT_MODEL`, `LLM_MODEL`, `TTS_MODEL` and `TTS_VOICE`; these four are configuration, not credentials. The selected LLM also handles post-call JSON extraction. See README for required endpoint compatibility.

After approval and configuration, start `app.main voice` and manually test Hebrew conversation, FAQ, phone confirmation, interruption and corrections. Live voice quality, model access, spoken guardrails, acoustic echo behavior and end-to-end latency are not proven by offline tests. No calendar, WhatsApp, telephony or database credentials are needed.
