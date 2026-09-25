"""Pure native Ollama message codec; no transport, model loading or execution.

Wire fields verified against api/types.go at Ollama v0.34.2 and the chat API:
https://github.com/ollama/ollama/blob/v0.34.2/api/types.go
https://docs.ollama.com/api/chat
Thinking may be accepted as a separate field, but is never returned or recorded.
"""
from __future__ import annotations

from core.canonical import PayloadRejected, check_payload, digest
from core.contracts import CALL_ID, TOOL_NAME, Request, Response, ToolCall, Usage
from core.validate import validated
from providers.base import ProviderError


def _strict_count(value, name: str) -> int:
    if type(value) is not int or not 0 <= value <= 2**53 - 1:
        raise ProviderError("malformed",
                            f"عدّاد {name} ليس عددًا صحيحًا غير سالب: {value!r}",
                            retryable=False)
    return value


def tool_payload(tool) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def serialize_tools(tools) -> list[dict]:
    return [tool_payload(tool) for tool in tools]


def serialize_messages(request: Request) -> list[dict]:
    """Ollama's native API accepts both tool_name and tool_call_id.

    The id preserves correlation when the same tool appears more than once.
    https://github.com/ollama/ollama/blob/v0.34.2/api/types.go
    """
    calls, messages = {}, []
    for message in request.messages:
        outgoing = {"role": message.role, "content": message.content}
        if message.tool_calls:
            outgoing["tool_calls"] = [
                {"id": call.call_id, "function": {"index": index, "name": call.name,
                                                 "arguments": call.arguments}}
                for index, call in enumerate(message.tool_calls)]
            calls.update((call.call_id, call) for call in message.tool_calls)
        if message.role == "tool":
            outgoing.update(tool_call_id=message.tool_call_id,
                            tool_name=calls[message.tool_call_id].name)
        messages.append(outgoing)
    return messages


def parse_response(out: dict, *, request: Request | None = None,
                   provider_name: str = "", allow_thinking: bool = False,
                   model_version: str | None = None) -> Response:
    if request is not None:
        validated(request)
    if not isinstance(out, dict) or not isinstance(out.get("message"), dict):
        raise ProviderError("malformed", "جواب Ollama بلا message",
                            retryable=False)
    message = out["message"]
    content = message.get("content")
    thinking = message.get("thinking")
    raw_calls = message.get("tool_calls")
    if (raw_calls is not None and not isinstance(raw_calls, list)):
        raise ProviderError("malformed", "نداءات أدوات Ollama ليست قائمة",
                            retryable=False)
    if (not isinstance(content, str) or message.get("role") != "assistant"
            or out.get("done") is not True
            or message.get("images") not in (None, [])
            or message.get("audio") not in (None, [])
            or (not allow_thinking and thinking not in (None, ""))
            or (allow_thinking and thinking is not None and not isinstance(thinking, str))):
        raise ProviderError("malformed", "محتوى الجواب ليس نصًّا",
                            retryable=False)
    tool_calls = []
    seen_call_ids = {call.call_id for m in request.messages for call in m.tool_calls} if request else set()
    if raw_calls and request is None:
        raise ProviderError("malformed", "نداء أداة بلا سياق طلب معلوم", retryable=False)
    declared = {tool.name for tool in request.tools} if request else None
    for index, raw_call in enumerate(raw_calls or ()):
        if not isinstance(raw_call, dict):
            raise ProviderError("malformed", "نداء أداة Ollama ليس كائنًا",
                                retryable=False)
        function = raw_call.get("function")
        if not isinstance(function, dict):
            raise ProviderError("malformed", "دالة نداء أداة Ollama غائبة",
                                retryable=False)
        name = function.get("name")
        arguments = function.get("arguments")
        if (not isinstance(name, str) or not TOOL_NAME.fullmatch(name)
                or not isinstance(arguments, dict)):
            raise ProviderError("malformed", "نداء أداة Ollama غير صالح",
                                retryable=False)
        if declared is not None and name not in declared:
            raise ProviderError("malformed", f"أداة Ollama غير مُعلنة: {name!r}",
                                retryable=False)
        call_id = raw_call.get("id", raw_call.get("call_id"))
        if call_id is None:
            call_id = function.get("id", function.get("call_id"))
        if call_id is None:
            # Stable on replay, distinct between assistant turns, independent
            # of transient deadlines and the caller's idempotency key.
            call_id = f"ollama-{digest(request.fingerprint_payload())[:40]}-{index}"
        if not isinstance(call_id, str) or not CALL_ID.fullmatch(call_id):
            raise ProviderError("malformed", "معرّف نداء أداة Ollama غير صالح",
                                retryable=False)
        if call_id in seen_call_ids:
            raise ProviderError("malformed", "معرّف نداء أداة Ollama مكرّر",
                                retryable=False)
        seen_call_ids.add(call_id)
        try:
            check_payload(arguments, "ollama.tool_call.arguments")
        except PayloadRejected as exc:
            raise ProviderError("malformed", "وسائط الأداة خارج عقد JSON", retryable=False) from exc
        tool_calls.append(ToolCall(call_id, name, arguments))
    if request is not None and tool_calls and not request.tools:
        raise ProviderError("malformed", "نداءات أدوات بلا أدوات مُعلنة",
                            retryable=False)
    done_reason = out.get("done_reason")
    if done_reason not in ("stop", "length"):
        raise ProviderError("malformed", "سبب اكتمال الجواب غائب أو غير معروف",
                            retryable=False)
    model = out.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ProviderError("malformed", "هوية نموذج الجواب غائبة أو غير صالحة",
                            retryable=False)
    stop = "complete" if done_reason == "stop" else "max_output"
    return Response(
        content=content,
        usage=Usage(_strict_count(out.get("prompt_eval_count"),
                                  "prompt_eval_count"),
                    _strict_count(out.get("eval_count"), "eval_count")),
        stop_reason=stop,
        cost_micros=0,
        provider=provider_name,
        model_version=model if model_version is None else model_version,
        tool_calls=tuple(tool_calls),
    )
