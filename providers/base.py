"""حدّ المزوّد — دالّةٌ واحدة، وكلّ ما بعدها محوّلات صغيرة."""
from __future__ import annotations

from typing import Protocol

from core.contracts import Request, Response


class ProviderError(RuntimeError):
    def __init__(self, code: str, reason: str, retryable: bool):
        super().__init__(f"{reason} [{code}]")
        self.code = code
        self.reason = reason
        self.retryable = retryable


def require_text_only(request: Request, *, code: str = "tools_unsupported",
                      reason: str = "المزود النصي لا يدعم نداءات الأدوات أو تاريخها") -> None:
    """Reject structured history before it can be silently flattened to text."""
    if request.tools or any(message.role == "tool" or message.tool_call_id is not None
                            or message.tool_calls for message in request.messages):
        raise ProviderError(code, reason, retryable=False)


class Provider(Protocol):
    name: str
    is_local: bool

    def estimate_micros(self, request: Request) -> int:
        """تقديرٌ بالميكرو-دولار **قبل** النداء. صحيحٌ غير سالب."""
        ...

    def complete(self, request: Request) -> Response:
        ...
