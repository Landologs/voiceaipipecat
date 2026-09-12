import logging
from urllib.parse import urlsplit, urlunsplit
from xml.etree.ElementTree import Element, SubElement, tostring

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from starlette.datastructures import FormData
from twilio.request_validator import RequestValidator

from app.config.business_config import BusinessConfig
from app.config.settings import Settings
from app.voice.pipeline import run_voice_session

logger = logging.getLogger(__name__)


async def _close_websocket(websocket: WebSocket, code: int):
    """Close an accepted or rejected socket without masking the original error."""
    try:
        await websocket.close(code=code)
    except RuntimeError:
        # Starlette raises if the peer has already completed the close handshake.
        pass


def _forwarded_origin(headers, fallback_url: str, configured_origin: str = "") -> str:
    if configured_origin:
        return configured_origin.rstrip("/")
    fallback = urlsplit(fallback_url)
    proto = headers.get("x-forwarded-proto", fallback.scheme).split(",", 1)[0].strip()
    host = headers.get("x-forwarded-host", headers.get("host", fallback.netloc)).split(",", 1)[0].strip()
    return f"{proto}://{host}".rstrip("/")


def public_request_url(headers, fallback_url: str, configured_origin: str = "") -> str:
    parsed = urlsplit(fallback_url)
    origin = urlsplit(_forwarded_origin(headers, fallback_url, configured_origin))
    return urlunsplit((origin.scheme, origin.netloc, parsed.path, parsed.query, ""))


def websocket_url(public_origin: str) -> str:
    parsed = urlsplit(public_origin)
    if parsed.scheme not in {"https", "http"}:
        raise ValueError("Public origin must use HTTPS or HTTP")
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit((scheme, parsed.netloc, "/twilio/media", "", ""))


def build_twiml(stream_url: str, from_number: str = "", to_number: str = "") -> str:
    response = Element("Response")
    connect = SubElement(response, "Connect")
    stream = SubElement(connect, "Stream", {"url": stream_url})
    if from_number:
        SubElement(stream, "Parameter", {"name": "from_number", "value": from_number})
    if to_number:
        SubElement(stream, "Parameter", {"name": "to_number", "value": to_number})
    return '<?xml version="1.0" encoding="UTF-8"?>' + tostring(
        response, encoding="unicode", short_empty_elements=True
    )


def _valid_signature(token: str, url: str, params, signature: str) -> bool:
    if not token or not signature:
        return False
    validator = RequestValidator(token)
    candidates = (url, url + "/") if not url.endswith("/") else (url, url.rstrip("/"))
    return any(validator.validate(candidate, params, signature) for candidate in candidates)


def create_app(settings: Settings | None = None, business: BusinessConfig | None = None) -> FastAPI:
    settings = settings or Settings.load()
    business = business or BusinessConfig.load(settings.business_path)
    app = FastAPI(title="Receptionist telephony", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/twilio/voice")
    async def incoming_call(request: Request):
        form: FormData = await request.form()
        request_url = public_request_url(
            request.headers, str(request.url), settings.twilio_public_url
        )
        if not _valid_signature(
            settings.twilio_auth_token,
            request_url,
            form,
            request.headers.get("x-twilio-signature", ""),
        ):
            logger.warning("Rejected incoming call with invalid Twilio signature")
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")
        call_sid = str(form.get("CallSid", ""))
        logger.info("Incoming call received: CallSid=%s", call_sid or "unknown")
        origin = _forwarded_origin(
            request.headers, str(request.url), settings.twilio_public_url
        )
        stream_url = websocket_url(origin)
        if not stream_url.startswith("wss://"):
            raise HTTPException(status_code=503, detail="Twilio Media Streams require public HTTPS")
        return Response(
            build_twiml(stream_url, form.get("From", ""), form.get("To", "")),
            media_type="application/xml",
        )

    @app.websocket("/twilio/media")
    async def media_stream(websocket: WebSocket):
        origin = _forwarded_origin(
            websocket.headers, str(websocket.url), settings.twilio_public_url
        )
        request_url = websocket_url(origin)
        if not _valid_signature(
            settings.twilio_auth_token,
            request_url,
            {},
            websocket.headers.get("x-twilio-signature", ""),
        ):
            logger.warning("Rejected WebSocket with invalid Twilio signature")
            await _close_websocket(websocket, code=1008)
            return
        await websocket.accept()
        call_sid = "unknown"
        stream_sid = "unknown"
        try:
            transport_type, call_data = await parse_telephony_websocket(websocket)
            if transport_type != "twilio" or not call_data.stream_id or not call_data.call_id:
                logger.warning("Rejected malformed Twilio Media Streams handshake")
                await _close_websocket(websocket, code=1003)
                return
            call_sid = call_data.call_id
            stream_sid = call_data.stream_id
            logger.info(
                "Twilio media stream connected: CallSid=%s StreamSid=%s",
                call_sid,
                stream_sid,
            )
            serializer = TwilioFrameSerializer(
                stream_sid=stream_sid,
                call_sid=call_sid,
                account_sid=settings.twilio_account_sid,
                auth_token=settings.twilio_auth_token,
            )
            transport = FastAPIWebsocketTransport(
                websocket,
                FastAPIWebsocketParams(
                    audio_in_enabled=True,
                    audio_out_enabled=True,
                    add_wav_header=False,
                    serializer=serializer,
                ),
            )
            await run_voice_session(
                settings,
                business,
                transport,
                source="twilio",
                session_id=call_sid,
            )
        except ValueError as exc:
            logger.warning("Twilio stream rejected: %s", exc)
            await _close_websocket(websocket, code=1003)
        except Exception:
            logger.exception("Twilio voice session failed: CallSid=%s", call_sid)
            await _close_websocket(websocket, code=1011)
        finally:
            logger.info(
                "Twilio media stream disconnected: CallSid=%s StreamSid=%s",
                call_sid,
                stream_sid,
            )

    return app


def run_server(settings: Settings, business: BusinessConfig):
    import uvicorn

    settings.validate_telephony()
    app = create_app(settings, business)
    uvicorn.run(
        app,
        host=settings.telephony_host,
        port=settings.telephony_port,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        log_level="info",
        ws_max_size=1_048_576,
    )
