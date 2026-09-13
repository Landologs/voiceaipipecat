from datetime import datetime
import logging

from pipecat.frames.frames import FunctionCallResultProperties
from pipecat.services.llm_service import FunctionCallParams

from app.actions.service import BusinessActions
from app.leads.models import CallResult

logger = logging.getLogger(__name__)


async def _return_tool_result(params: FunctionCallParams, tool_name: str, result):
    """Return a final tool result and always request a spoken LLM follow-up."""
    payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    status = payload.get("status", "unknown") if isinstance(payload, dict) else "unknown"
    logger.info("Business tool completed: %s status=%s", tool_name, status)
    await params.result_callback(
        payload, properties=FunctionCallResultProperties(run_llm=True)
    )


def build_pipecat_tools(actions: BusinessActions):
    async def check_availability(
        params: FunctionCallParams, start: str, end: str, duration_minutes: int = 0
    ):
        """Check real calendar availability. Datetimes must include UTC offsets."""
        try:
            parsed_start, parsed_end = datetime.fromisoformat(start), datetime.fromisoformat(end)
        except (TypeError, ValueError):
            await _return_tool_result(params, "check_availability", {
                "status": "needs_clarification", "message": "Use ISO datetimes with UTC offsets"
            })
            return
        result = await actions.check_availability(
            start=parsed_start,
            end=parsed_end,
            duration_minutes=duration_minutes or None,
        )
        await _return_tool_result(params, "check_availability", result)

    async def create_appointment(
        params: FunctionCallParams,
        customer_name: str,
        phone_raw: str,
        service: str,
        start: str,
        caller_confirmed: bool,
        address: str = "",
        notes: str = "",
        language: str = "he",
    ):
        """Create an appointment only after details and a slot were confirmed."""
        try:
            parsed_start = datetime.fromisoformat(start)
        except (TypeError, ValueError):
            await _return_tool_result(params, "create_appointment", {
                "status": "needs_clarification", "message": "Use an ISO datetime with UTC offset"
            })
            return
        result = await actions.create_appointment(
            customer_name=customer_name,
            phone_raw=phone_raw,
            service=service,
            start=parsed_start,
            caller_confirmed=caller_confirmed,
            address=address,
            notes=notes,
            language=language,
        )
        await _return_tool_result(params, "create_appointment", result)

    async def cancel_appointment(
        params: FunctionCallParams, appointment_id: str, phone_raw: str
    ):
        """Cancel an existing appointment after matching its phone number."""
        result = await actions.cancel_appointment(
            appointment_id=appointment_id, phone_raw=phone_raw
        )
        await _return_tool_result(params, "cancel_appointment", result)

    async def get_appointment(
        params: FunctionCallParams, appointment_id: str, phone_raw: str
    ):
        """Retrieve an existing appointment after matching its phone number."""
        result = await actions.get_appointment(
            appointment_id=appointment_id, phone_raw=phone_raw
        )
        await _return_tool_result(params, "get_appointment", result)

    async def reschedule_appointment(
        params: FunctionCallParams, appointment_id: str, phone_raw: str, new_start: str
    ):
        """Move an existing appointment after matching its phone number."""
        try:
            parsed_start = datetime.fromisoformat(new_start)
        except (TypeError, ValueError):
            await _return_tool_result(params, "reschedule_appointment", {
                "status": "needs_clarification", "message": "Use an ISO datetime with UTC offset"
            })
            return
        result = await actions.reschedule_appointment(
            appointment_id=appointment_id, phone_raw=phone_raw, new_start=parsed_start
        )
        await _return_tool_result(params, "reschedule_appointment", result)

    async def save_lead(
        params: FunctionCallParams,
        customer_name: str = "",
        phone_raw: str = "",
        request_summary: str = "",
        address: str = "",
        language: str = "",
        urgency: str = "standard",
    ):
        """Persist caller details locally when a lead or callback request should be recorded."""
        try:
            lead = CallResult(
                customer_name=customer_name,
                phone_raw=phone_raw,
                request_summary=request_summary,
                address=address,
                language=language,
                urgency=urgency,
            )
            result = actions.save_lead(lead)
        except Exception:
            result = {"status": "needs_clarification", "message": "Lead fields are invalid"}
        await _return_tool_result(params, "save_lead", result)

    async def get_business_info(params: FunctionCallParams, topic: str = ""):
        """Return only approved business configuration and current hours status."""
        result = actions.get_business_info(topic=topic)
        await _return_tool_result(params, "get_business_info", result)

    return [
        check_availability,
        create_appointment,
        get_appointment,
        cancel_appointment,
        reschedule_appointment,
        save_lead,
        get_business_info,
    ]
