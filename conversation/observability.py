"""Opt-in provider-call metadata. Never collect messages, IDs, paths or results.

Observers are trusted in-process callbacks: ordinary observer failures are isolated,
but a hung callback or process loss is not an exactly-once telemetry guarantee.
No SDK is imported and no event is allocated while observation is disabled.
"""
from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Mapping

from core.contracts import Response
from core.locality import is_local_provider

FIELDS = ("duration_ms", "model", "error_code", "input_tokens", "output_tokens")
MODELS = frozenset({"qwen3.5:9b", "qwen3:14b", "gemma4", "synthetic"})
ERRORS = frozenset({"none", "provider_timeout", "engine_unavailable", "transport_error",
                    "response_error", "operation_error", "other_error"})
PROVIDER_ERRORS = {
    "timeout": "provider_timeout",
    "local_chat_timeout": "provider_timeout",
    "unreachable": "engine_unavailable",
    "local_chat_transport": "transport_error",
    "malformed": "response_error",
    "local_chat_malformed": "response_error",
    "local_tools_malformed": "response_error",
}


def _number(value, maximum, *, integer=False):
    kinds = (int,) if integer else (int, float)
    if type(value) not in kinds or not 0 <= value <= maximum:
        return None
    if type(value) is float and not math.isfinite(value):
        return None
    return value


def safe_metadata(event):
    event = event if isinstance(event, Mapping) else {}
    model, code = event.get("model"), event.get("error_code")
    return {"duration_ms": _number(event.get("duration_ms"), 3_600_000),
            "model": model if type(model) is str and model in MODELS else "unknown_model",
            "error_code": code if type(code) is str and code in ERRORS else "other_error",
            "input_tokens": _number(event.get("input_tokens"), 1_000_000, integer=True),
            "output_tokens": _number(event.get("output_tokens"), 1_000_000, integer=True)}


class ObservedProvider:
    def __init__(self, provider, observer):
        self.provider, self.observer = provider, observer

    @property
    def name(self):
        return getattr(self.provider, "name", "?")

    @property
    def is_local(self):
        return is_local_provider(self.provider)

    def estimate_micros(self, request):
        return self.provider.estimate_micros(request)

    def complete(self, request):
        try:
            started = time.monotonic()
        except Exception:
            started = None
        response, code = None, "operation_error"
        provider_error = None
        try:
            response = self.provider.complete(request)
            code = "none"
            return response
        except Exception as exc:
            provider_error = exc
            raise
        finally:
            try:
                if provider_error is not None:
                    code = getattr(provider_error, "code", "operation_error")
                    if type(code) is str:
                        code = PROVIDER_ERRORS.get(code, code)
                    if type(code) is not str or code not in ERRORS - {"none"}:
                        code = "operation_error"
                elif isinstance(response, Response) and response.retryable_error:
                    code = "response_error"
                usage = response.usage if isinstance(response, Response) else None
                duration = None if started is None else (time.monotonic() - started) * 1000
                event = safe_metadata({"duration_ms": duration,
                                       "model": request.model, "error_code": code,
                                       "input_tokens": getattr(usage, "input_tokens", None),
                                       "output_tokens": getattr(usage, "output_tokens", None)})
                self.observer(event)
            except Exception:
                pass  # Telemetry must not replace the provider result or exception.


def observed(provider, observer):
    return provider if observer is None else ObservedProvider(provider, observer)


class LocalLangfuseObserver:
    """Real optional SDK, bounded RAM export, fixed identity and no service keys.

The client does not fetch prompts, run experiments or contact a Langfuse service.
The acceptance probe also denies network at the OS boundary. This collector is
ephemeral and is not a hosted dashboard or a durable store.
"""
    def __init__(self, capacity=128):
        if type(capacity) is not int or not 1 <= capacity <= 1024:
            raise ValueError("observer_capacity_invalid")
        import json
        import uuid
        from langfuse import Langfuse
        from langfuse.types import MaskOtelSpansResult, OtelSpanPatch
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

        self.events = deque(maxlen=capacity)
        self.total = 0
        outer = self
        prefix = "langfuse.observation.metadata."

        class Sink(SpanExporter):
            def export(self, spans):
                for span in spans:
                    if span.name != "diwan.provider.call":
                        continue
                    raw = {}
                    for key in FIELDS:
                        value = span.attributes.get(prefix + key)
                        try:
                            raw[key] = json.loads(value) if isinstance(value, str) else value
                        except ValueError:
                            raw[key] = None
                    outer.events.append(safe_metadata(raw))
                    outer.total += 1
                return SpanExportResult.SUCCESS

            def shutdown(self):
                pass

        def mask(*, params):
            patches = {}
            for key, span in params.spans.items():
                values = {}
                for field in FIELDS:
                    value = span.attributes.get(prefix + field)
                    try:
                        values[field] = json.loads(value) if isinstance(value, str) else value
                    except ValueError:
                        values[field] = value
                safe = safe_metadata(values)
                patches[key] = OtelSpanPatch(delete_attributes=tuple(span.attributes),
                    set_attributes={prefix + field: json.dumps(safe[field]) for field in FIELDS})
            return MaskOtelSpansResult(span_patches=patches)

        self._provider = TracerProvider(resource=Resource({"service.name": "diwan-local-metadata"}))
        tag = uuid.uuid4().hex
        self._client = Langfuse(public_key="pk-lf-local-" + tag, secret_key="sk-lf-local-unused",
            base_url="http://127.0.0.1:1", tracer_provider=self._provider, span_exporter=Sink(),
            mask_otel_spans=mask, flush_at=1, flush_interval=1,
            should_export_span=lambda span: span.instrumentation_scope.name == "langfuse-sdk"
                and span.name == "diwan.provider.call")

    def __call__(self, event):
        with self._client.start_as_current_observation(name="diwan.provider.call", as_type="span",
                                                      metadata=safe_metadata(event)):
            pass

    def snapshot(self):
        self._client.flush()
        return {"events": list(self.events), "total_exported": self.total,
                "retained": len(self.events), "capacity": self.events.maxlen,
                "durable": False}

    def close(self):
        self._client.shutdown()
