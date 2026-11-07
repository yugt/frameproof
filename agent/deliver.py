"""The delivery console: find the desk's pending call, wait for its render, answer by id.

ADK surfaces a RequestInput as a function call named `adk_request_input` whose id
is the interrupt id (here the render job id). Answering it is a user message
holding a FunctionResponse with that id; the graph resumes at `deliver`, which
receives the response as its input.
"""

from __future__ import annotations

from google.genai import types

from . import desk as desk_mod
from .schemas import DeskAnswer, DeskReceipt

REQUEST_INPUT = "adk_request_input"


def pending_calls(events) -> list[types.FunctionCall]:
    """Unanswered adk_request_input calls in a list of events."""
    calls, answered = {}, set()
    for e in events:
        for p in (e.content.parts if e.content and e.content.parts else []):
            if p.function_call and p.function_call.name == REQUEST_INPUT:
                calls[p.function_call.id] = p.function_call
            if p.function_response and p.function_response.name == REQUEST_INPUT:
                answered.add(p.function_response.id)
    return [c for cid, c in calls.items() if cid not in answered]


def receipt_of(call: types.FunctionCall) -> DeskReceipt:
    return DeskReceipt(**(call.args or {}).get("payload", {}))


def answer_message(call_id: str, answer: DeskAnswer) -> types.Content:
    return types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
        id=call_id, name=REQUEST_INPUT, response=answer.model_dump()))])


def wait_and_answer(call: types.FunctionCall, timeout_s: float = 1800, on_tick=None) -> types.Content:
    """Block until the call's render job finishes, then build the answer for it."""
    r = receipt_of(call)
    state = desk_mod.wait(r, timeout_s=timeout_s, on_tick=on_tick)
    ok = state == "done"
    return answer_message(call.id, DeskAnswer(approved=ok, ok=ok, video=r.video,
                                              note=f"{r.kind} render {state}"))
