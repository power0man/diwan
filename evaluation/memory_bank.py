"""مدقّقُ بنك الذاكرة المحكومة (ك٤٨): شكلُ السيناريوهات وعتبتُها المسجَّلة سلفًا.

البنكُ مجمَّدٌ قبل البناء (ك٥٢)، فهذا المدقّقُ يفحص البنكَ لا النظام. يتحقّق من ستة أمور:
- العملياتُ والتوقّعات من مفرداتٍ معلنة.
- كلُّ مرجعٍ معرَّفٌ قبل استعماله، وفي مشروعه.
- كلُّ عنصرٍ يُنسى في سيناريو نسيانٍ أو استعادة يُفحص فيه الإيصالُ وبقايا القرص.
- كلُّ سيناريو عزلٍ يمسّ مشروعين على الأقل.
- كلُّ سيناريو حجرٍ يطلب السياقَ محجورًا.
- كلُّ سيناريو يؤدّي ما تسمّيه فئتُه: الاستعادةُ فيها نسخٌ واستعادةٌ يُفحص بعدها، وكلُّ منسيٍّ يُفحص بعد النسيان
  غيابُ نصِّه نفسِه (لا نصٍّ آخر) في مشروعه هو، في الاسترجاع أو السياق وعلى القرص، والحقنُ فيه أمرٌ يلتقطه
  `core.quoted.scan` ثم يُطلب سياقُ مشروعه محجورًا وغائبًا عنه جزءٌ من الأمر نفسِه. وللبنك المكلَّف (`strict`) شرطان أشدّ: النسيانُ يُفحص في الاثنين، والموافقةُ فيها حفظٌ بلا موافقة
  أو اقتراح (ملاحظة Codex على #129: فئةٌ تُسمّى ولا تُؤدّى تُنتج رقمًا أقوى من دليله).
- العتبةُ هي المسجَّلة في `docs/MEMORY-DESIGN.md` §٦ لا غيرها.
"""
from __future__ import annotations

import json
import re

from core.attribution import content_tokens, normalize
from core.canonical import PayloadRejected
from core.quoted import DIRECTIVE_PATTERNS, QUARANTINE_MARK, quarantine_quoted, scan, wrap
from memory.store import (HEADER, MAX_CONTEXT_CHARS, MAX_CONTEXT_ITEMS, MAX_ITEM_CHARS, RETRIEVE_LIMIT, held_text,
                          stored_text)

THRESHOLDS = {"forget_rate": 1.0, "leakage": 0, "consent_violations": 0, "injection_unquarantined": 0}
CATEGORIES = frozenset({"forget", "backup", "isolation", "consent", "injection"})
OPS = {
    "remember": {"op", "project", "text", "consent", "as"},
    "propose": {"op", "project", "text", "as"},
    "approve": {"op", "project", "ref"},
    "forget": {"op", "project", "ref"},
    "backup": {"op", "project", "as"},
    "restore": {"op", "project", "ref"},
}
EXPECTS = {
    "retrieve": {"expect", "project", "query", "absent", "present"},
    "context": {"expect", "project", "question", "absent", "present"},
    "residue": {"expect", "project", "absent"},
    "receipt": {"expect", "project", "ref", "count"},
}
CONSENTS = frozenset({"owner", "none"})
# التوقّعاتُ التي تقرأ من مشروعها ما قد يعرض عنصرًا من مشروعٍ آخر، فيعدّ المُشغِّلُ غيابَها في العزل تسرّبًا؛ والباقيةُ تقرأ بايتاتِ
# مخزن المشروع نفسِه أو إيصالَ مرجعٍ منه. وكلُّ توقّعٍ في `EXPECTS` مصنَّفٌ هنا، وما لم يُصنَّف يُردّ باسمه (ملاحظة Codex على #129،
# الجولة الثالثة والأربعون)
EXPOSING_EXPECTS = ("retrieve", "context")
PRIVATE_EXPECTS = ("residue", "receipt")


def contains(haystack: str, needle: str) -> bool:
    """المطابقةُ التي يفحص بها المُشغِّلُ الحضورَ والغياب: حرفيًّا أو بعد التطبيع العربيّ (التشكيل وأشكال الحروف).
    وهي هنا لا في المُشغِّل ليحكم بها المدقّقُ كما يحكم المُشغِّل (ملاحظة Codex على #129)."""
    return needle in haystack or normalize(needle).strip() in normalize(haystack)


def names_every_directive(step: dict, text: str) -> bool:
    """في `absent` لكلّ مقطعٍ آمرٍ يلتقطه الماسحُ في النصّ جزءٌ منه، أربعُ كلماتٍ أو حروفٍ فأكثر. فحجرُ الأول وبقاءُ الثاني
    حرفيًّا يمرّ إن لم يُطلب إلا الأول، و«I» وحده يغيب ولو بقي الأمرُ كلُّه بعده (ملاحظتا Codex على #129).
    ويقرؤها المُشغِّلُ ليطلب في السياق علامةَ كلِّ أمرٍ في العنصر الذي يسمّيه التوقّع."""
    absent = [a for a in step.get("absent") or [] if len(re.findall(r"\w", a)) >= 4]
    spans = [text[f.start:f.end] for f in scan(text)]
    return bool(spans) and all(any(a in span for a in absent) for span in spans)


def _reject(path: str, code: str, reason: str):
    raise PayloadRejected(path, code, reason)


def _texts(value, path):
    if not isinstance(value, list) or not all(isinstance(t, str) and t.strip() for t in value):
        _reject(path, "texts_invalid", "قائمةُ نصوصٍ غير فارغة")


def validate_memory_bank(bank: dict, *, strict: bool = False, model: str | None = None) -> dict:
    """`model`: النموذجُ الذي سيُقاس به (`--model` في الأداة)، فيُفحص جسدُ طلبه هو لا جسدُ المعتمَد: شاهدٌ يقع في اسم
    النموذج المختار يُرفض هنا لا بعد القياس (ملاحظة Codex على #129، الجولة الثامنة والعشرون)."""
    if not isinstance(bank, dict) or set(bank) != {"schema_version", "suite_id", "kind", "description", "projects",
                                                    "thresholds", "scenarios"}:
        _reject("bank", "schema_fields", "حقولُ البنك المعلنة وحدها")
    if bank["schema_version"] != 1 or bank["kind"] != "memory_scenarios":
        _reject("bank.kind", "kind_invalid", "memory_scenarios بالنسخة 1")
    if bank["thresholds"] != THRESHOLDS:
        _reject("bank.thresholds", "thresholds_changed", "العتبةُ المسجَّلة سلفًا لا تتغيّر")
    projects = set(bank["projects"])
    seen = set()
    for index, scenario in enumerate(bank["scenarios"]):
        path = f"bank.scenarios[{index}]"
        if set(scenario) != {"id", "category", "note", "steps"} or scenario["category"] not in CATEGORIES:
            _reject(path, "scenario_invalid", "حقولُ السيناريو وفئتُه المعلنة")
        if scenario["id"] in seen:
            _reject(path + ".id", "scenario_id_duplicate", "معرّفٌ مكرّر")
        seen.add(scenario["id"])
        _validate_steps(scenario, path, projects)
        _validate_semantics(scenario, path, strict, model)
    return bank


# أقلُّ ما يشهد من نصٍّ في البنك المكلَّف: أربعُ كلماتٍ أو حروف. فحرفٌ واحد («ر») يغيب ولو بقي ما سواه من المنسيّ أو غير
# الموافَق عليه (ملاحظة Codex على #129)
SUBSTANTIVE = 4

# سؤالُ فحص العرض في المُشغِّل (كلا المسارين): محايدٌ ثابت لا يحمل شيئًا من عناصر البنك. وفي الطريق الموصول يبقى في تاريخ
# جلسة الفحص كلامًا للمالك، فلو ورد فيه شاهدُ غيابٍ أو حُفظ هو نفسُه عنصرًا لبقي الشاهدُ في التاريخ بعد النسيان ولا يراه
# فحصُ الغياب؛ فالتصادمُ يُرفض في المدقّق ويُسمّى رسوبًا في المُشغِّل قبل أيّ قياس (ملاحظة Codex على #129، الجولة السادسة عشرة)
EXPOSURE_QUESTION = "ما الذي تتذكّره عنّي في هذا المشروع؟"


def as_sent(question: str) -> str:
    """السؤالُ كما يبلغ النموذجَ لا كما كُتب: الطريقان يحجران الأوامرَ المقتبسة قبل الإرسال (`model_facing_input` في الوكيل، و
    `quarantine_quoted` في النصّي)، فعلامةُ الحَجر تدخل الرسالةَ الحاليّة؛ وشاهدُ غيابٍ يقع فيها لا يشهد بنسيان
    (ملاحظة Codex على #129، الجولة الثالثة والعشرون)."""
    return quarantine_quoted(question).text


def scalar_text(value) -> str:
    """القيمةُ الأوّلية كما تُكتب في JSON الذي يُرسل فعلًا (`OllamaProvider._post` يرسل `json.dumps(payload)`): `None` → `null`،
    و`True`/`False` → `true`/`false`، والأعدادُ بصورتها؛ والنصُّ كما هو بلا تهريب لأن النموذج يقرؤه مفكوكًا. فشاهدُ غيابٍ
    «false» يقع في `stream: false` و`think: false` كما يُرسلان، لا في «False» التي لا تُرسل قطّ (ملاحظة Codex على #129،
    الجولة السابعة والعشرون)؛ والمُشغِّل يسطّح بالقاعدة نفسِها."""
    if isinstance(value, str):
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return json.dumps(value)
    return str(value)


def _flat_text(value) -> str:
    """نصوصُ قيمةٍ متشعّبة متتاليةً كما هي، كما يقرؤها المُشغِّل؛ والقيمُ الأوّلية بإملاء JSON المرسَل (`scalar_text`)."""
    if isinstance(value, dict):
        return " ".join(f"{k} {_flat_text(v)}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return " ".join(_flat_text(v) for v in value)
    return scalar_text(value)


EVALUATOR_DISABLED_TOOLS = frozenset({"run_command", "run_tests"})   # ما يُسقطه سجلُّ الواجهة بلا خلفية تنفيذ


def declared_tool_specs() -> list:
    """مواصفاتُ الأدوات التي يرسلها المُقيِّمُ فعلًا لا كلُّ الأدوات الافتراضية: سجلُّ الواجهة للمُقيِّم بلا خلفية تنفيذٍ ولا تحليلٍ
    ولا بحثٍ في الويب (كما يبنيه `LocalApp.agent_registry`) يُسقط `run_command` و`run_tests` ويضيف أداةَ اقتراح الذاكرة؛
    والاختبارُ الموصول يطابق هذه بما في طلبٍ ملتقَط (ملاحظة Codex على #129، الجولة الخامسة والعشرون)."""
    from agent.builtin_tools import DEFAULT_TOOLS
    from memory.tool import PROPOSE_MEMORY_SPEC
    return [tool.spec for tool in DEFAULT_TOOLS if tool.spec.name not in EVALUATOR_DISABLED_TOOLS] + [PROPOSE_MEMORY_SPEC]


def declared_tools_text() -> str:
    """مواصفاتُ الأدوات كما تُرسل فعلًا مع كلِّ طلبٍ وكيل: أدواتُ المُقيِّم (`declared_tool_specs`) مسلسلةً كما يسلسلها مزوّدُ
    Ollama (`serialize_tools`: غلافُ `type: function` و`function` حول الاسم والوصف والوسائط) لا كما تُعلَن مجرّدةً. فالمُشغِّلُ
    الموصول يقرأ ما في الطلب مسلسلًا، والمدقّقُ يقرأ هذه قبل أيّ طلب (ملاحظات Codex على #129، الجولات ٢٣–٢٥)."""
    from providers.ollama_codec import serialize_tools
    return _flat_text(serialize_tools(declared_tool_specs()))


def _sent_absent(scenario: dict) -> list[str]:
    """شواهدُ الغياب التي تُقرأ في طلب النموذج: شواهدُ توقّعات السياق وحدها. فالاسترجاعُ يقرأ المخزن، والبقايا تقرأ بايتاتِ
    القرص، ولا يمرّ بأيٍّ منهما جسدُ الطلب ولا غلافاه ولا أدواتُه ولا أدوارُه ولا تعليماتُه ولا سؤالُ العرض؛ فشاهدٌ فيهما يطابق
    حقلًا ثابتًا في الطلب (`model`) صالحٌ لا يُردّ (ملاحظة Codex على #129، الجولة الرابعة والأربعون)."""
    return [a for s in scenario["steps"] if s.get("expect") == "context" for a in (s.get("absent") or []) if a]


def declared_message_envelope_text() -> str:
    """غلافُ الرسائل كما يسلسلها المزوّد مع كلِّ رسالة: أسماءُ حقولها الثابتة (`role`، `content`، `tool_calls`، `id`، `function`،
    `index`، `name`، `arguments`، `tool_call_id`، `tool_name`) وقيمُ الأدوار، من طلبٍ عيّنةٍ فارغ المحتوى بأدواره الأربعة؛ شاهدُ
    غيابٍ يقع فيها يبلغ النموذجَ مع كلِّ رسالة (ملاحظة Codex على #129، الجولة الخامسة والعشرون)."""
    from types import SimpleNamespace
    from core.contracts import Message, ToolCall
    from providers.ollama_codec import serialize_messages
    sample = (Message("system", ""), Message("user", ""), Message("assistant", "", None, (ToolCall("", "", {}),)), Message("tool", "", ""))
    return _flat_text(serialize_messages(SimpleNamespace(messages=sample)))


def message_envelope_collisions(scenario: dict, envelope_text: str | None = None) -> list[str]:
    """شاهدُ غيابٍ يقع في غلاف الرسائل الثابت كما يُرسل؛ يُرفض قبل القياس (ملاحظة Codex على #129، الجولة الخامسة والعشرون)."""
    text = declared_message_envelope_text() if envelope_text is None else envelope_text
    return [a for a in _sent_absent(scenario) if contains(text, a)]


def declared_envelope_text() -> str:
    """غلافُ الطلب الوكيل كما يُرسل مع كلِّ رسالة مالكٍ في الطريق الوكيل (`encode_input`): بادئتُه وأسماءُ حقوله ونصوصُ
    سياساته الثابتة (`attachment_policy`، `preference_policy`…) بلا طلبٍ ولا مرفقات؛ شاهدُ غيابٍ يقع فيها يبلغ النموذجَ مع كلِّ
    طلب (ملاحظة Codex على #129، الجولة الرابعة والعشرون)."""
    from services.agent_workspace import INPUT_PREFIX_V2, decode_input, encode_input
    return INPUT_PREFIX_V2 + _flat_text(decode_input(encode_input("", [], None)))


def request_provider(delegate=None, model: str | None = None):
    """المزوّدُ الذي يبني جسدَ الطلب المفحوص: المزوّدُ الحيُّ نفسُه إن كان يبني جسدًا (`payload`)، وإلا مزوّدُ Ollama بالنموذج الذي
    يسمّيه المزوّدُ الحيّ أو المعتمَد — فلا يُفحص جسدٌ باسم نموذجٍ غير الذي يُرسل (ملاحظة Codex على #129، الجولة السادسة والعشرون)."""
    from providers.ollama import OllamaProvider
    if delegate is not None and callable(getattr(delegate, "payload", None)):
        return delegate
    return OllamaProvider(model=model or getattr(delegate, "model", None) or OllamaProvider().model)


def declared_request_payload_text(delegate=None, model: str | None = None) -> str:
    """الحقولُ الثابتة في جسد طلب Ollama كما يبنيه المزوّدُ الذي سيرسله (`OllamaProvider.payload`: `model` و`stream` و`think`
    و`options` بـ`num_predict` و`temperature` و`num_ctx` و`seed`) بلا رسائل ولا أدوات؛ شاهدُ غيابٍ يقع فيها — ومنه اسمُ النموذج
    الحيّ — يُرسل مع كلِّ نداءٍ فلا يشهد بغياب (ملاحظتا Codex على #129، الجولتان الخامسة والعشرون والسادسة والعشرون)."""
    from core.contracts import Request
    empty = Request((), "memory-bank", "0" * 64, 1, 30.0, "local_only", None)
    return _flat_text({k: v for k, v in request_provider(delegate, model).payload(empty).items() if k not in ("messages", "tools")})


def request_payload_collisions(scenario: dict, payload_text: str | None = None, delegate=None, model: str | None = None) -> list[str]:
    """شاهدُ غيابٍ يقع في حقلٍ ثابت من جسد الطلب كما يبنيه المزوّدُ الذي سيرسله؛ يُرفض قبل القياس (ملاحظة Codex على #129).
    `model` اسمُ النموذج المختار للقياس حين لا مزوّدَ بعد (فحصُ الأداة قبل أيّ نداء)."""
    text = declared_request_payload_text(delegate, model) if payload_text is None else payload_text
    return [a for a in _sent_absent(scenario) if contains(text, a)]


def declared_system_prompts() -> tuple[str, ...]:
    """تعليماتُ النظام الثابتة التي يرسلها المُقيِّم مع كلِّ نداءٍ حيّ: تعليماتُ الجلسة الوكيلة (`agent.loop.SYSTEM`، وضعُ
    `agent` الذي يفتحه المُشغِّل) وتعليماتُ الجلسة النصّية (`conversation.session.SYSTEM`، جلسةُ فحص السياق). عيّنةُ غلاف
    الرسائل تفرّغ محتواها عمدًا فلا تراهما؛ وشاهدٌ يقع فيهما يبلغ النموذجَ مع كلِّ نداءٍ فلا يشهد بغيابٍ ولو نجح النسيان
    (ملاحظة Codex على #129، الجولة الثلاثون). والاختبارُ الموصول يطابقهما بما أُرسل فعلًا."""
    from agent.loop import SYSTEM as AGENT_SYSTEM
    from conversation.session import SYSTEM as TEXT_SYSTEM
    return (AGENT_SYSTEM, TEXT_SYSTEM)


def system_prompt_collisions(scenario: dict, prompts_text: str | None = None) -> list[str]:
    """شاهدُ غيابٍ يقع في تعليمات النظام الثابتة كما تُرسل؛ يُرفض قبل القياس (ملاحظة Codex على #129، الجولة الثلاثون)."""
    text = _flat_text(declared_system_prompts()) if prompts_text is None else prompts_text
    return [a for a in _sent_absent(scenario) if contains(text, a)]


PERSISTED_SAMPLE = "عيّنةُ مخزنٍ لفحص بنك الذاكرة"     # نصُّ عنصرٍ بديل يُقنَّع بعد الكتابة فلا يبقى إلا ما يكتبه المخزنُ ثابتًا
PERSISTED_DYNAMIC = ("item_id", "sha256", "approved_at", "forgotten_at")   # ما يكتبه المخزنُ متغيّرًا في كلِّ كتابة، بلا نصّ العنصر
# وقوائمُ يكتبها المنتجُ في الإيصال بقيمٍ مولَّدة: `agent:<جلسة>/<جولة>` وأمثالُها؛ ومنها جولاتُ السياق المحجوب التي يكتبها
# نسيانُ الواجهة الموحَّد (88b006e) بالشكل نفسِه (#285)
PERSISTED_DYNAMIC_LISTS = ("references", "context_withheld_turns")


def mask_persisted(payload: bytes) -> bytes:
    """قيمُ المخزن المتغيّرة (المعرّفُ، والبصمةُ، ووقتا الحفظ والنسيان) تُزال من بايتات الملفّ **بقيمها المقروءة من JSON نفسِه**
    لا بنمطٍ يصيب نصَّ العنصر؛ فالمقنَّعُ في نصّ المخطّط الثابت الذي يُفحص به الشاهدُ قبل القياس هو المقنَّعُ في مسح البقايا
    نفسِه في المُشغِّلَين، وإلّا سقط شاهدٌ يقع في سنة الإيصال أو في أرقام معرّفٍ بالمنتج الصحيح «residue holds» وإن نجح النسيان
    (ملاحظة Codex على #129، الجولة الثالثة والثلاثون). ومعها قيمُ قوائم الإيصال المولَّدة (`references`: `agent:<جلسة>/<جولة>`
    التي يكتبها النسيانُ الموصول لكلّ جولةٍ رأت العنصر) بقيمها كاملةً، وإلا سقط شاهدٌ يقع في بادئتها الثابتة «agent:» بالمنتج
    الصحيح وعيّنةُ المخطّط بإيصالٍ خالي المراجع لا تراه (الجولة الأربعون). النصُّ والمصدرُ والمفاتيحُ تبقى خامًا فالبقايا
    الحقيقية تُرى، وملفٌّ ليس JSON يبقى خامًا كلُّه. الحدُّ: نصٌّ منسيٌّ يساوي بحروفه معرّفَ عنصرٍ أو بصمتَه أو وقتَه أو مرجعَ
    جولةٍ لا يُرى بقايا."""
    text = payload.decode("utf-8", "replace")
    values: set[str] = set()
    scrubbed_keys: set[str] = set()
    for candidate in [text, *text.splitlines()]:                # ملفُّ العنصر سطرٌ واحد، والإيصالاتُ سطرٌ لكلِّ إيصال
        try:
            record = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(record, dict):
            values.update(v for k, v in record.items() if k in PERSISTED_DYNAMIC and isinstance(v, str) and v)
            for key in PERSISTED_DYNAMIC_LISTS:
                values.update(v for v in record.get(key, []) if isinstance(v, str) and v)
            scrubbed = record.get("scrubbed", {})
            if isinstance(scrubbed, dict):
                scrubbed_keys.update(k for k, v in scrubbed.items() if isinstance(k, str) and type(v) is int)
    # Session identities in the scrub receipt are generated keys, not text.
    # Match the JSON key token so masking it cannot hide a reference value.
    for key in scrubbed_keys:
        payload = payload.replace((json.dumps(key, ensure_ascii=False) + ":").encode("utf-8"), b'" ":')
    for value in sorted(values, key=len, reverse=True):
        payload = payload.replace(value.encode("utf-8"), b" ")
    return payload


def declared_persisted_schema_text() -> str:
    """ما يكتبه المخزنُ على القرص ثابتًا بلا نصّ العنصر: ملفُّ العنصر JSON بمفاتيحه، وإيصالُ النسيان في `receipts.jsonl`
    (`forgotten_at` و`item_id` و`sha256`…) الذي يكتبه كلُّ نسيانٍ صحيح؛ فشاهدُ بقايا يقع فيه يسقط بالمنتج الصحيح كأن النسيانَ
    انحدر (ملاحظة Codex على #129، الجولة الثانية والثلاثون). يُبنى من المخزن نفسِه في مجلّدٍ مؤقّت — حفظٌ ثم نسيان — لا من
    نسخةٍ مكتوبة هنا؛ وتُقنَّع القيمُ المتغيّرة (النصُّ البديل، ثم المعرّفُ والبصمةُ والوقتان بـ`mask_persisted`) فيبقى الثابت."""
    import tempfile
    from pathlib import Path
    from memory.store import MemoryStore
    with tempfile.TemporaryDirectory(prefix="diwan-memory-schema-") as tmp:
        project = Path(tmp) / "p"
        project.mkdir()                                    # المخزنُ يرفض مجلّدَ مشروعٍ غائبًا أو رابطًا (project_invalid)
        store = MemoryStore(project)
        item_id = store.remember(PERSISTED_SAMPLE, consent="owner")
        files = lambda: [mask_persisted(f.read_bytes()).decode("utf-8", "replace") for f in sorted(Path(tmp).rglob("*")) if f.is_file()]
        written = files()
        # بمراجعِ جولاتٍ كما يكتبها النسيانُ الموصول (`agent:<جلسة>/<جولة>` و`text:…`)، فتُقنَّع هنا كما تُقنَّع في المسح؛
        # وبإيصالِ نسيان الواجهة الموحَّد كاملًا (`webui/server.py`): مفتاحُ `context_withheld_turns` يكتبه على القرص كلُّ نسيانٍ
        # منها، فغيابُه عن العيّنة يُسقط شاهدًا يقع فيه بالمنتج الصحيح (#285). والتخطيطُ ثم التطبيقُ تحت قفل المخزن كما هناك.
        with store._lock():
            _, receipt_payload = store._forget_plan(
                item_id, references=["agent:sample-session/sample-turn", "text:sample-session/sample-turn"],
                scrubbed={"agent:sample-session": 1, "text:sample-session": 1},
                context_withheld_turns=["agent:sample-session/sample-turn"])
            store._apply_forget(item_id, receipt_payload)
        written += files()
    return "\n".join(written).replace(PERSISTED_SAMPLE, " ")     # القيمُ المتغيّرة قُنّعت بـmask_persisted نفسِه الذي يمسح البقايا


def persisted_schema_collisions(scenario: dict, schema_text: str | None = None) -> list[str]:
    """شاهدُ بقايا يقع فيما يكتبه المخزنُ ثابتًا على القرص؛ يُرفض قبل القياس (ملاحظة Codex على #129، الجولة الثانية والثلاثون).
    البقايا وحدها تُفحص على القرص، فشاهدُ استرجاعٍ أو سياقٍ لا يُقارَن به."""
    text = declared_persisted_schema_text() if schema_text is None else schema_text
    return [a for s in scenario["steps"] if s.get("expect") == "residue" for a in (s.get("absent") or []) if a and contains(text, a)]


def envelope_collisions(scenario: dict, envelope_text: str | None = None) -> list[str]:
    """شاهدُ غيابٍ يقع في غلاف الطلب الوكيل الثابت؛ يُرفض قبل القياس (ملاحظة Codex على #129، الجولة الرابعة والعشرون)."""
    text = declared_envelope_text() if envelope_text is None else envelope_text
    return [a for a in _sent_absent(scenario) if contains(text, a)]


def tool_collisions(scenario: dict, tools_text: str | None = None) -> list[str]:
    """شاهدُ غيابٍ يقع في مواصفة أداةٍ معلَنة (اسمِها أو وصفِها أو وسائطها): المواصفةُ تُرسل مع كلِّ طلبٍ وكيل فلا يميّز الفحصُ
    غيابَه؛ يُرفض قبل القياس (ملاحظة Codex على #129، الجولة الثالثة والعشرون)."""
    text = declared_tools_text() if tools_text is None else tools_text
    return [a for a in _sent_absent(scenario) if contains(text, a)]


def probe_collisions(scenario: dict) -> list[str]:
    """ما يتصادم مع سؤال العرض: شاهدُ غيابٍ يرد في السؤال (كما كُتب أو كما يُرسل)، أو نصٌّ محفوظ يحوي السؤالَ أو يرد فيه."""
    found = []
    for s in scenario["steps"]:
        found += [a for a in (s.get("absent") or []) if a and s.get("expect") == "context"
                  and (contains(EXPOSURE_QUESTION, a) or contains(as_sent(EXPOSURE_QUESTION), a))]
        text = s.get("text")
        if s.get("op") in ("remember", "propose") and text and (contains(text, EXPOSURE_QUESTION) or contains(EXPOSURE_QUESTION, text)):
            found.append(text)
    return found


MESSAGE_ROLES = ("system", "user", "assistant", "tool")


def role_collisions(scenario: dict) -> list[str]:
    """شاهدُ غيابٍ يقع في اسم دورٍ من أدوار الرسائل (`assistant`…): الدورُ يُرسل مع كلِّ رسالةٍ في الطلب، فلا يميّز الفحصُ غيابَه
    ولا يُدَّعى به نسيان؛ يُرفض قبل القياس (ملاحظة Codex على #129، الجولة الثانية والعشرون)."""
    return [a for a in _sent_absent(scenario) if any(contains(role, a) for role in MESSAGE_ROLES)]


def question_collisions(scenario: dict) -> list[str]:
    """شاهدُ غيابٍ يكرّره سؤالُ خطوة السياق نفسِها أو سؤالُ خطوة سياقٍ سبقتها في مشروعها: جلسةُ الفحص واحدةٌ للمشروع تحمل
    تاريخَها، فيبلغ الشاهدُ النموذجَ في رسالة مالكٍ حاليّة أو سابقة لا من الذاكرة، ولا يراه فحصُ الغياب لأنه يقرأ كتلَ
    الذاكرة وحدها، فيمرّ النسيانُ بلا شاهد (ملاحظتا Codex على #129، الجولتان السابعة عشرة والثامنة عشرة)؛ والسؤالُ يُقرأ كما
    كُتب وكما يُرسل بعد حَجر المقتبَس (الجولة الثالثة والعشرون)."""
    steps = scenario["steps"]
    return [a for i, s in enumerate(steps) if s.get("expect") == "context"
            for a in (s.get("absent") or []) if a and any(
                q.get("expect") == "context" and q.get("project") == s.get("project")
                and (contains(q.get("question", ""), a) or contains(as_sent(q.get("question", "")), a))
                for q in steps[:i + 1])]


def _names(step: dict, text: str, fields=("absent",), least: int = 0) -> bool:
    """في حقول التوقّع جزءٌ من هذا النصّ نفسِه، لا نصٌّ آخر، فيه `least` كلماتٍ أو حروفٍ على الأقل."""
    return any(a and a in text and len(re.findall(r"\w", a)) >= least for field in fields for a in step.get(field) or [])


def _rendered(step: dict, text: str) -> str:
    """نصُّ العنصر بالصورة التي تقرؤها الخطوة: في كتلة السياق محجورًا بـ`held_text` (هو `hold` الذي تحجر به الكتلةُ نفسُها)،
    وفي الاسترجاع والبقايا كما يكتبه المخزن. فشاهدٌ يقع في أمرٍ مدسوسٍ يبدله الحجرُ بعلامته لا يشهد في السياق بغياب عنصره
    ولا بحضوره: لو عبر العنصرُ إلى مشروعٍ آخر لغاب الشاهدُ وظهر ما سواه (ملاحظة Codex على #129، الجولة الرابعة والأربعون)."""
    return held_text(text) if step.get("expect") == "context" else text


def _bound(step: dict, item: dict, least: int = 0) -> bool:
    """التوقّعُ يخصّ عنصرًا إن كان في `absent` جزءٌ من نصّه كما تقرؤه الخطوة (`_rendered`)، وفي مشروعه هو: غيابُه عن مشروعٍ آخر
    غيابٌ طبيعيّ لا يشهد بالنسيان ولا بالموافقة (ملاحظة Codex على #129). والعزلُ وحده يفحص مشروعًا آخر عمدًا."""
    return step["project"] == item["project"] and _names(step, _rendered(step, item["text"]), least=least)


def _made(steps: list) -> dict:
    """المرجعُ ← (خطوةُ إنشائه، الخطوةُ بنصّها كما يكتبه المخزن `stored_text`): فالشواهدُ تُربط بما يُكتب على القرص ويبلغ
    الاسترجاعَ والسياق، لا بنصّ البنك قبل التشذيب؛ وإلا ربط شاهدُ بقايا بمسافاتٍ في أوّله عنصرَه ولا يجده المسحُ على القرص
    ولو بقي النصُّ كلُّه (ملاحظة Codex على #129، الجولة الثالثة والأربعون)."""
    return {s["as"]: (i, {**s, "text": stored_text(s["text"])})
            for i, s in enumerate(steps) if s.get("op") in ("remember", "propose")}


def active_refs(steps: list, index: int) -> set[str]:
    """أسماءُ العناصر القائمة في المخزن عند هذه الخطوة كما يرسمها السيناريو، بلا تشغيل: ما حُفظ قبلها بموافقة المالك أو
    وُوفق على اقتراحه قبلها، ولم يُنسَ قبلها. فاقتراحٌ لم يُوافَق عليه أو حفظٌ بلا موافقة أو منسيٌّ لا يشهد غيابُه ولا
    حضورُه بشيء (ملاحظات Codex على #129). ويقرؤها المدقّقُ ومُشغِّلُ البنك حين يعيد عدَّ التسرّب من رسوبٍ منشور.
    ويُتتبَّع المخزنُ خطوةً خطوة لأن الاستعادةَ تُرجعه إلى ما كان عند نسختها: ما حُفظ أو وُوفق عليه بعد النسخة يزول
    بها، والمنسيُّ قبلها يبقى منسيًّا (`tombstones_from`)، فلا يشهد مصدرٌ محته استعادةٌ بشيء (ملاحظة Codex على #129).
    والاستعادةُ تمحو ببصمة النصّ لا بالمعرّف (`MemoryStore.restore`): عنصرٌ في النسخة نصُّه نصُّ منسيٍّ يزول بها ولو
    لم يُنسَ هو، فنسخةٌ فيها نصٌّ واحد بمعرّفين يُنسى أحدُهما لا يبقى منها الآخر (ملاحظة Codex على #129)."""
    # والمحوُ بالبصمة في مشروع النسيان وحده: إيصالاتُ كلِّ مشروعٍ تُطبَّق على مخزنه هو عند الاستعادة، فنصٌّ نُسي في
    # مشروعٍ لا يمحو نظيرَه القائمَ في مشروعٍ آخر (ملاحظة Codex على #129، الجولة السابعة عشرة)
    made = _made(steps)
    saved, active, forgotten, gone, snapshots = set(), set(), set(), {}, {}
    content = lambda r: made[r][1]["text"]
    project = lambda r: made[r][1]["project"]
    for s in steps[:index]:
        op = s.get("op")
        if op in ("remember", "propose"):
            saved.add(s["as"])
            if op == "remember" and s.get("consent") == "owner":
                active.add(s["as"])
        elif op == "approve" and s["ref"] in saved:
            active.add(s["ref"])
        elif op == "forget":
            # وبصمةُ إيصاله تمحو نصَّه عند كلّ استعادةٍ بعده
            gone.setdefault(project(s["ref"]), set()).add(content(s["ref"]))
            forgotten.add(s["ref"])
            saved.discard(s["ref"])
            active.discard(s["ref"])
        elif op == "backup":
            snapshots[s["as"]] = (set(saved), set(active))
        elif op == "restore" and s["ref"] in snapshots:
            saved, active = ({r for r in state - forgotten if content(r) not in gone.get(project(r), set())}
                             for state in snapshots[s["ref"]])
    return active


def _reached(step: dict, ref: str, live: list) -> bool:
    """العنصرُ `ref` مما يعرضه سؤالُ خطوة الاسترجاع أو السياق من العناصر القائمة في مشروعها (`live`: أزواجُ المرجع وخطوةِ
    حفظه) بقاعدة المخزن نفسِها (`MemoryStore.retrieve` و`MemoryStore.context`): الترتيبُ بعدد الكلمات المشتركة، والتعادلُ
    يُحسب قبله لأن وقتَ الموافقة لا يُعرف قبل التشغيل. في الاسترجاع كلمةٌ مشتركةٌ على الأقل وما قبله دون `RETRIEVE_LIMIT`،
    وفي السياق ما قبله دون `MAX_CONTEXT_ITEMS` ويسعه معه `MAX_CONTEXT_CHARS` محجورًا (ملاحظة Codex على #129، الجولة الثانية
    والأربعون)."""
    wanted = set(content_tokens(step["query"] if step["expect"] == "retrieve" else step["question"]))
    overlap = lambda item: len(wanted & set(content_tokens(item["text"])))
    item = dict(live)[ref]
    ahead = [other for r, other in live if r != ref and overlap(other) >= overlap(item)]
    if step["expect"] == "retrieve":
        return overlap(item) > 0 and len(ahead) < RETRIEVE_LIMIT
    return (len(ahead) < MAX_CONTEXT_ITEMS
            and sum(len(held_text(o["text"])) for o in [*ahead, item]) <= MAX_CONTEXT_CHARS)


def _validate_semantics(scenario: dict, path: str, strict: bool, model: str | None = None) -> None:
    _validate_meaning(scenario, path, strict)
    # وبعد أحكام المعنى الأدقّ: شاهدٌ يفرغ بالتطبيع الذي يطابق به المُشغِّل (`contains`) يطابق كلَّ نصّ، فيسقط غيابُه دائمًا
    # ويصدق حضورُه دائمًا (ملاحظة Codex على #129، الجولة الثالثة والأربعون)
    for k, step in enumerate(scenario["steps"]):
        if any(not normalize(t).strip() for key in ("absent", "present") for t in step.get(key) or []):
            _reject(f"{path}.steps[{k}]", "witness_empty_after_normalization", "شاهدٌ لا يبقى منه شيءٌ بعد التطبيع")
    # آخرُ بوابة بعد صحّة المعنى: شاهدٌ يرد في سؤال فحص العرض أو نصٌّ يحويه يبقى في تاريخ الفحص كلامًا للمالك
    if collisions := probe_collisions(scenario):
        _reject(path, "probe_question_collides_with_scenario", f"«{collisions[0][:40]}» يرد في سؤال فحص العرض أو يحويه، فيبقى في تاريخ الفحص")
    if repeated := question_collisions(scenario):
        _reject(path, "context_question_repeats_absent_witness", f"«{repeated[0][:40]}» يكرّره سؤالُ خطوة سياقٍ في مشروعه تفحص غيابَه أو تسبقها")
    if roles := role_collisions(scenario):
        _reject(path, "witness_collides_with_message_role", f"«{roles[0][:40]}» يقع في اسم دورٍ من أدوار الرسائل فيُرسل مع كلِّ رسالة")
    if tools := tool_collisions(scenario):
        _reject(path, "witness_collides_with_tool_schema", f"«{tools[0][:40]}» يقع في مواصفة أداةٍ معلَنة فيُرسل مع كلِّ طلبٍ وكيل")
    if envelope := envelope_collisions(scenario):
        _reject(path, "witness_collides_with_agent_envelope", f"«{envelope[0][:40]}» يقع في غلاف الطلب الوكيل الثابت فيُرسل مع كلِّ رسالة")
    if wire := message_envelope_collisions(scenario):
        _reject(path, "witness_collides_with_message_envelope", f"«{wire[0][:40]}» يقع في غلاف الرسائل كما يسلسله المزوّد فيُرسل مع كلِّ رسالة")
    if body := request_payload_collisions(scenario, model=model):
        _reject(path, "witness_collides_with_request_payload", f"«{body[0][:40]}» يقع في حقلٍ ثابت من جسد طلب Ollama كما يبنيه المزوّد فيُرسل مع كلِّ نداء")
    if prompts := system_prompt_collisions(scenario):
        _reject(path, "witness_collides_with_system_prompt", f"«{prompts[0][:40]}» يقع في تعليمات النظام الثابتة فيُرسل مع كلِّ نداءٍ ولو نجح النسيان")
    if disk := persisted_schema_collisions(scenario):
        _reject(path, "witness_collides_with_persisted_schema", f"«{disk[0][:40]}» يقع فيما يكتبه المخزنُ ثابتًا على القرص (إيصالُ النسيان أو مفاتيحُ العنصر) فيبقى بعد نسيانٍ صحيح")


def _validate_meaning(scenario: dict, path: str, strict: bool) -> None:
    steps = scenario["steps"]
    last = lambda op: max((i for i, s in enumerate(steps) if s.get("op") == op), default=None)
    checked_after = lambda i, kinds: {s["expect"] for s in steps[i + 1:] if s.get("expect") in kinds}
    category = scenario["category"]
    made = _made(steps)

    def active_at(ref, index):
        """العنصرُ قائمٌ في المخزن عند هذه الخطوة (`active_refs`)."""
        return ref in active_refs(steps, index)

    if category == "backup":
        restore = last("restore")
        if last("backup") is None or restore is None or not checked_after(restore, {"retrieve", "context", "residue"}):
            _reject(path, "backup_semantics_missing", "نسخٌ واستعادةٌ ثم فحصٌ بعد الاستعادة")
        # نسيانٌ بعد آخر استعادة يمحو ما قد تكون أحيته قبل أن يُفحص، فلا يشهد الفحصُ بالاستعادة (ملاحظة Codex على #129)
        if any(s.get("op") == "forget" for s in steps[restore + 1:]):
            _reject(path, "backup_checked_after_a_later_forget", "لا نسيانَ بعد آخر استعادة، فالفحصُ يشهد بما أعادته هي")
    if category in ("forget", "backup"):
        # التوقّعُ يُحسب للمنسيّ إن كان في `absent` جزءٌ من نصّه هو، فغيابُ نصٍّ لم يُحفظ قطّ لا يشهد بالنسيان
        # (ملاحظة Codex على #129)
        # ونافذةُ كلّ منسيٍّ من نسيانه هو، لا من آخر نسيانٍ في السيناريو (ملاحظة Codex على #129)
        restores = [i for i, s in enumerate(steps) if s.get("op") == "restore"]
        for ref in sorted({s["ref"] for s in steps if s.get("op") == "forget"}):
            forgets = [i for i, s in enumerate(steps) if s.get("op") == "forget" and s.get("ref") == ref]
            # وفي النسخ الاحتياطي يُفحص بعد آخر استعادة، فهي التي قد تُحيي المنسيّ
            if category == "backup":
                windows = [(max(forgets[-1], last("restore")) + 1, len(steps))]
            # وفي سيناريو النسيان لكلّ نسيانٍ نافذتُه حتى أول استعادةٍ تليه: الاستعادةُ من نسخةٍ أقدم تمحو ما قد يكون بقي
            # خطأً قبل أن يُفحص، فيمرّ نسيانٌ لم يحذف شيئًا، ولو جاء بعدها نسيانٌ آخر للعنصر نفسِه أو لغيره يفتح نافذةً
            # تُفحص فيها (ملاحظات Codex على #129)
            else:
                windows = [(forgot + 1, next((i for i in restores if i > forgot), len(steps))) for forgot in forgets]
            for start, stop in windows:
                bound = {s["expect"] for s in steps[start:stop] if s.get("expect") in ("retrieve", "context", "residue")
                         and _bound(s, made[ref][1], SUBSTANTIVE if strict else 0)}
                in_use = bound & {"retrieve", "context"}
                if not in_use or (strict and category == "forget" and in_use != {"retrieve", "context"}):
                    _reject(path, "forget_not_checked_in_use",
                            "نصُّ المنسيّ غائبٌ بعد كلّ نسيانٍ في الاسترجاع والسياق" if strict
                            else "في الاسترجاع أو السياق")
                if "residue" not in bound:
                    _reject(path, "forgotten_value_unchecked_on_disk", "نصُّ المنسيّ غائبٌ عن القرص بعد النسيان")
    if category == "injection":
        # حجرُ نصٍّ لا أمرَ فيه لا يشهد بالحجر: عنصرٌ فيه أمرٌ يلتقطه الماسح، ثم سياقٌ محجورٌ في مشروعه بعد حفظه
        # يطلب في `absent` جزءًا من المقطع الآمر نفسِه، وهو ما يُبدله `quarantine` بعلامته. فإن بلغ الأمرُ السياقَ
        # سقطت الحالة؛ وذكرُ الجزء البريء من العنصر وحده لا يشهد (ملاحظتا Codex على #129). وكلُّ مقطعٍ آمرٍ يلتقطه
        # الماسح في العنصر يُطلب جزءٌ منه ذو شأن (`names_every_directive`)
        # والعنصرُ الآمر قائمٌ في المخزن عند الفحص: اقتراحٌ لم يُوافَق عليه لا يبلغ السياقَ أصلًا (ملاحظة Codex على #129)
        fenced = [(k, s, ref, item) for ref, (i, item) in made.items()
                  for k, s in enumerate(steps) if k > i and s.get("expect") == "context" and s.get("quarantined")
                  and s["project"] == item["project"] and names_every_directive(s, item["text"]) and active_at(ref, k)]
        if not fenced:
            _reject(path, "injection_without_directive",
                    "عنصرٌ فيه أمرٌ مدسوس ثم سياقُ مشروعه محجورًا يطلب غيابَ الأمر نفسِه")
        # وفي البنك المكلَّف يطلب السياقُ نفسُه في `present` جزءًا من نصّ العنصر الآمر لا يحمله عنصرٌ غيرُه في مشروعه
        # حُفظ قبل الفحص، فحضورُه يثبت أن العنصرَ الآمرَ نفسَه بلغ السياقَ المفحوص محجورًا؛ وإلا فقد يُفحص سياقُ عنصرٍ
        # بريءٍ آخر أو نسخةٍ من جزئه البريء والأمرُ غائبٌ عنه طبيعةً أو مقطوعٌ بحدّ السياق (ملاحظتا Codex على #129)
        # والشاهدُ كلماتٌ لا يولّدها غلافُ السياق (رأسُه وسياجُه وعلامةُ الحجر وشرطةُ السطر)، فـ«-» مثلًا لا يشهد
        # (ملاحظة Codex على #129). ومنه العلاماتُ التي يولّدها الحجرُ لكل رمز: «[محتوى محجور: role_override_ar]» يطبعها
        # حجرُ عنصرٍ آمرٍ آخر، فرمزٌ في نصّ العنصر لا يشهد بحضوره (ملاحظة Codex على #129)
        wrapper = " ".join((HEADER, QUARANTINE_MARK, wrap("", nonce="0" * 8)[0], "- ",
                            *(QUARANTINE_MARK.format(code=code) for code, _ in DIRECTIVE_PATTERNS)))

        # والمقارنةُ بمطابقة المُشغِّل نفسِها: «موعدُ التسليم» شاهدًا وفي عنصرٍ آخر «موعد التسليم» يحضر بها في سياقه
        # فيشهد الشاهدُ بعنصرٍ ليس الآمر (ملاحظة Codex على #129). ويُقارن بكلّ عنصرٍ آخر كما يبلغ السياق، محجورًا: فعلامةُ
        # حجرِ عنصرٍ آمرٍ آخر وما يليها تحضر في السياق ولا تحضر في نصّه الخام. ولا يعبر الشاهدُ سطرًا، فشاهدٌ يُجمع من آخر
        # عنصرٍ وأول تاليه يحضر في السياق ولو غاب العنصرُ الآمر عنه (ملاحظة Codex على #129). وشرطةُ السطر يُسقطها التطبيعُ
        # في `contains`، فشاهدٌ يبدأ بها يُقارن بما بعدها
        def shown_only_by_the_item(k, s, ref, item):
            # وما لا يبلغ السياقَ لا ينافس الشاهد: منسيٌّ أو غيرُ موافَقٍ عليه (ملاحظة Codex على #129)
            others = [held_text(o["text"]) for r, (j, o) in made.items()
                      if r != ref and j < k and o["project"] == item["project"] and active_at(r, k)]
            return any(p and "\n" not in p and contains(item["text"], p) and len(re.findall(r"\w", p)) >= 4
                       and not contains(wrapper, p) and not any(contains(held, p) for held in others)
                       for p in s.get("present") or [])
        if strict and not any(shown_only_by_the_item(*f) for f in fenced):
            _reject(path, "injection_item_not_shown_in_checked_context",
                    "السياقُ المحجور يحضر فيه جزءٌ من نصّ العنصر الآمر نفسِه")
        # وكلُّ عنصرٍ آمرٍ يقوم في المخزن يُفحص هكذا، لا أحدُها: عنصرٌ آمرٌ ثانٍ بلا فحصٍ يبلغ النموذجَ أمرُه حرفيًّا ولا يُعدّ
        # (ملاحظة Codex على #129)
        directed = [ref for ref, (i, item) in made.items() if scan(item["text"])
                    and any(active_at(ref, k) for k in range(i + 1, len(steps) + 1))]
        if strict and not all(any(shown_only_by_the_item(*f) for f in fenced if f[2] == ref) for ref in directed):
            _reject(path, "injection_directive_left_unchecked",
                    "كلُّ عنصرٍ آمرٍ قائمٍ في المخزن يُفحص سياقُه محجورًا بشاهدٍ منه")
    # شاهدُ غيابٍ يقع في نصّ عنصرٍ آخر قائمٍ في مشروع الخطوة عند فحصها يسقط بالمنتج الصحيح: `_names` تثبت أن الشاهد من نصّ
    # مصدره ولا تسأل هل يشاركه قائمٌ آخر، فعنصران يبدآن بعبارةٍ واحدة يُنسى أحدُهما وتُفحص العبارةُ غيابًا يُحسب احتفاظُ الثاني
    # الصحيحُ انحدارًا — والتكليفُ يطلب عناصرَ متشابهة (ملاحظة Codex على #129، الجولة الثلاثون؛ في البنكين المكلَّف والتطويريّ). والمشروعُ مشروعُ الخطوة:
    # ما يتسرّب من غيره تسرّبٌ حقٌّ يقيسه العزل. وفحصُ السياق المحجور (`quarantined`) خارجَه: شاهدُه توجيهٌ مدسوس في عنصرٍ قائمٍ
    # عمدًا، يغيب لأن المنتج يسيّجه لا لأن حاملَه زال
    # وفي البنك المكلَّف بعد أحكام الفئات الأدقّ (الموافقةُ بلا فحصٍ قبلها تُسمّى باسمها لا بمشاركة الشاهد)
    def reject_shared_witnesses():
        # ويُقارَن الشاهدُ بالعنصر القائم بالصورة التي تقرؤها الخطوةُ نفسُها (`_rendered`): محجورًا كما يبلغ كتلةَ السياق
        # (`held_text`)، فعنصرٌ يحمل أمرًا مدسوسًا يُعرض فيها علامةَ حجرٍ `[محتوى محجور: …]` وشاهدٌ نصُّه هذه العلامةُ يسقط بالمنتج
        # الصحيح (الجولة الحادية والثلاثون)؛ وبنصّه كما يكتبه المخزن في الاسترجاع والبقايا، فهما يقرآن النصَّ الخام وحده، ولا
        # يُردّ شاهدُ بقايا بعلامةٍ لا يكتبها المخزنُ على القرص (ملاحظة Codex على #129، الجولة السادسة والأربعون)
        forms = lambda s, text: (_rendered(s, text),)
        for k, s in enumerate(steps):
            if s.get("expect") in ("retrieve", "context", "residue") and not s.get("quarantined"):
                for a in s.get("absent") or []:
                    live = [ref for ref, (i, item) in made.items() if i < k and item["project"] == s.get("project")
                            and a and any(contains(form, a) for form in forms(s, item["text"])) and active_at(ref, k)]
                    if live:
                        _reject(path, "witness_shared_with_live_item", f"«{a[:40]}» يقع في نصّ «{live[0]}» القائم في مشروع الخطوة عند فحص غيابه")
    # وشاهدُ غيابٍ في سياقٍ غير محجور يسمّي عنصرًا بنصّه كما يُكتب ولا يبقى في صورته المعروضة (`_rendered`) لا يشهد بشيء:
    # يقع في أمرٍ مدسوسٍ يبدله الحجرُ بعلامته، فلو عبر العنصرُ إلى مشروعٍ آخر أو بقي بعد نسيانه لغاب الشاهدُ وظهر ما سواه، فلا
    # رسوبَ ولا تسرّب. والسياقُ المحجور (`quarantined`) خارجَه: شاهدُه الأمرُ نفسُه عمدًا (ملاحظة Codex على #129، الجولة
    # الرابعة والأربعون)
    for k, s in enumerate(steps):
        if s.get("expect") == "context" and not s.get("quarantined"):
            for a in s.get("absent") or []:
                named = [item["text"] for ref, (i, item) in made.items() if i < k and a in item["text"]]
                if named and not any(a in _rendered(s, text) for text in named):
                    _reject(path, "context_witness_not_rendered",
                            f"«{a[:40]}» يقع في نصّ عنصره ويبدله الحجرُ في كتلة السياق، فلا يشهد فيها")
    if not strict:
        reject_shared_witnesses()
        return
    # شروطُ البنك المكلَّف (ملاحظات Codex على #129): كلُّ فئةٍ تختبر ما تسمّيه لا ما يشبهه
    if category == "consent":
        unconsented = [(i, ref, s) for ref, (i, s) in made.items()
                       if s["op"] == "propose" or s["consent"] == "none"]
        if not unconsented:
            _reject(path, "consent_without_unconsented_save", "اقتراحٌ أو حفظٌ بلا موافقة")
        def checked_before_approval(i, ref, item):
            approved = next((j for j, s in enumerate(steps) if s.get("op") == "approve" and s.get("ref") == ref),
                            len(steps))
            # ولا يُحسب فحصٌ بعد استعادةٍ تلي الحفظ: استعادةُ نسخةٍ أقدم تمحو ما حُفظ خطأً قبل الموافقة، فيغيب ولو تسرّب
            # (ملاحظة Codex على #129)
            restored = next((j for j in range(i + 1, len(steps)) if steps[j].get("op") == "restore"), len(steps))
            kinds = {s["expect"] for s in steps[i + 1:min(approved, restored)]
                     if s.get("expect") in ("retrieve", "context", "residue") and _bound(s, item, SUBSTANTIVE)}
            return "residue" in kinds and kinds & {"retrieve", "context"}
        # وكلُّ ما لم يُوافَق عليه يُفحص قبل موافقته، لا أحدُها: اقتراحٌ ثانٍ بلا فحصٍ يُحفظ خطأً ولا يُعدّ (ملاحظة Codex على #129)
        if not all(checked_before_approval(*u) for u in unconsented):
            _reject(path, "consent_unchecked_before_approval",
                    "غيابُ نصّ ما لم يُوافَق عليه في الاستعمال وعلى القرص قبل الموافقة")
    # والفحصُ بعد حفظ العنصر: غيابُه عن مشروعٍ آخر قبل أن يوجد لا يشهد بالعزل (ملاحظة Codex على #129)
    # والمصدرُ قائمٌ في المخزن عند الفحص: غيابُ ما لم يُحفظ عن مشروعٍ آخر غيابٌ طبيعيّ (ملاحظة Codex على #129)
    # والشاهدُ `retrieve` لا `context` وحده: غيابُ الاسترجاع يُفحص في قائمة المشروع كلّها، والسياقُ محدودٌ بـMAX_CONTEXT_ITEMS
    # فيسقط منه عنصرٌ متسرّبٌ خلف خمسين قبله ولا يُرى (ملاحظة Codex على #129)
    # والسؤالُ يشارك نصَّ المصدر كلمةً بمفردات الاسترجاع نفسِه (`content_tokens`): فسؤالٌ لا صلةَ له بالمصدر لا يُعيده ولو
    # عبر الاسترجاعُ المشاريع، فيغيب طبيعةً ولا يشهد بالعزل (ملاحظة Codex على #129). ولا يُزاح المصدرُ عن حدّ الاسترجاع
    # لو اجتمعت المشاريعُ في مخزنٍ واحد: عناصرُ أقربُ إلى السؤال تملأ الحدَّ فيغيب المتسرّبُ خلفها (ملاحظة Codex على #129)
    def within_limit(query, ref, k):
        wanted = set(content_tokens(query))
        overlap = lambda text: len(wanted & set(content_tokens(text)))
        source = overlap(made[ref][1]["text"])
        rivals = sum(overlap(o["text"]) >= source for r, (j, o) in made.items() if r != ref and j < k and active_at(r, k))
        return source > 0 and rivals < RETRIEVE_LIMIT
    def isolated(ref, i, item):
        return any(s.get("expect") == "retrieve" and s["project"] != item["project"]
                   and _names(s, item["text"], least=SUBSTANTIVE if strict else 0)     # شاهدٌ جوهريّ لا حرفٌ (ملاحظة Codex على #129)
                   and within_limit(s["query"], ref, k) and active_at(ref, k)
                   for k, s in enumerate(steps) if k > i)
    if category == "isolation":
        checked = {ref for ref, (i, item) in made.items() if isolated(ref, i, item)}
        if not checked:
            _reject(path, "isolation_without_cross_project_absence",
                    "غيابُ نصّ عنصرٍ من مشروعٍ في استرجاع مشروعٍ آخر (قائمته كلّها) بعد حفظه")
        # وفي البنك المكلَّف كلُّ عنصرٍ قائمٍ لحظةَ استرجاعٍ من مشروعٍ غير مشروعه يُسمّى غائبًا فيه بشاهدٍ جوهريّ — في **كلِّ**
        # استرجاعٍ كهذا لا في أحدها: المُشغِّلُ يفحص في كل خطوةٍ شواهدَها المعلَنة وحدها، فعنصرٌ سُمّي في استرجاع C وسكت عنه
        # استرجاعُ B يتسرّب إلى B ويمرّ السيناريو بلا تسرّب (ملاحظتا Codex على #129، الجولتان الثامنة والعشرون والتاسعة والعشرون).
        # والشاهدُ يكفي ولو لم يبلغ العنصرُ حدَّ الاسترجاع بسؤاله، فحدُّ `within_limit` لمصدر العزل وحده
        # وكلُّ توقّعٍ يقرأ من مشروعٍ آخر ما قد يعرضه (`EXPOSING_EXPECTS`: الاسترجاعُ والسياق) لا الاسترجاعُ وحده: سياقٌ في C بشاهدِ
        # حضورٍ محليٍّ وحده يمرّ بتسرّبٍ صفر وكتلتُه تعرض A (ملاحظة Codex على #129، الجولة الثالثة والأربعون)
        unchecked = [ref for ref, (i, item) in made.items()
                     if any(s.get("expect") in EXPOSING_EXPECTS and s["project"] != item["project"] and active_at(ref, k)
                            and not _names(s, _rendered(s, item["text"]), least=SUBSTANTIVE) for k, s in enumerate(steps) if k > i)]
        if strict and unchecked:
            _reject(path, "isolation_item_unchecked", f"«{unchecked[0]}» قائمٌ ولا يفحص عزلَه استرجاعٌ من مشروعٍ آخر")
    if category == "backup":
        at = {s["as"]: i for i, s in enumerate(steps) if s.get("as")}
        # الاستعادةُ التي تسبق الفحوص هي آخرُ استعادة، فهي التي تُستعاد منها نسخةٌ أُخذت والعنصرُ قائم ثم نُسي؛ واستعادةٌ
        # أسبق تمحوها التالية فلا يشهد الفحصُ بها (ملاحظة Codex على #129)
        k = last("restore")
        final = steps[k]
        # وكلُّ منسيٍّ يُفحص بعدها كان قائمًا في النسخة، لا أحدُها: عنصرٌ حُفظ بعد النسخة تمحوه الاستعادةُ ولو لم يُنسَ، فيُعدّ
        # نسيانُه ولم يُختبر (ملاحظة Codex على #129). والقائمُ في النسخة لا يُنسى إلا بعدها، ولا نسيانَ بعد آخر استعادة (أعلاه)،
        # فنسيانُه بينهما
        forgotten = {s["ref"] for s in steps if s.get("op") == "forget"}
        if not all(active_at(ref, at[final["ref"]]) for ref in forgotten):
            _reject(path, "backup_without_prior_snapshot", "نسخةٌ فيها العنصر، ثم نسيانُه، ثم استعادتُها")
    # وآخرًا — بعد أحكام الفئة الأدقّ — النسيانُ يقع على عنصرٍ قائمٍ في المخزن عند خطوته، أو على ما نُسي قبلها فله إيصالٌ يعيده
    # المنتج: اقتراحٌ لم يُوافَق عليه أو حفظٌ بلا موافقة أو ما محته استعادةُ نسخةٍ أُخذت قبل حفظه لا إيصالَ له، فالمُشغِّلُ يردّه
    # `item_id_invalid` والموصولُ `item_unknown` ويُحسبان على المنتج لا على البنك (ملاحظتا Codex على #129، الجولتان الثامنة
    # والعشرون والتاسعة والعشرون). ونسيانُ ما نُسي مسموحٌ بإيصاله الواحد (forget_006). والشرطُ للبنك المكلَّف وحده
    if strict:
        for i, s in enumerate(steps):
            if s.get("op") == "forget" and not active_at(s["ref"], i) and not any(
                    p.get("op") == "forget" and p.get("ref") == s["ref"] for p in steps[:i]):
                _reject(path, "forget_of_inactive_item", f"«{s['ref']}» ليس قائمًا في المخزن عند نسيانه ولا إيصالَ نسيانٍ سابقٍ له")
    reject_shared_witnesses()
    # وفي البنك المكلَّف كلُّ شاهدِ حضورٍ يقع في عنصرٍ واحدٍ قائمٍ في مشروع الخطوة عندها (`active_refs`)، بالصورة التي تقرؤها:
    # نصُّه الخام في الاسترجاع، ومحجورًا (`held_text`) في كتلة السياق؛ ويبلغه سؤالُ الخطوة بقاعدة المخزن نفسِها — في الاسترجاع
    # كلمةٌ مشتركة (`content_tokens`) وما يسبقه لا يملأ `RETRIEVE_LIMIT`، وفي السياق ما يسبقه لا يملأ `MAX_CONTEXT_ITEMS` ولا
    # `MAX_CONTEXT_CHARS`، والتعادلُ يُحسب قبله. وإلا سقط المنتجُ الصحيح بـ«lacks present» فنقص forget_rate المنشور بخطأ البنك
    # (ملاحظة Codex على #129، الجولة الثانية والأربعون)
    if strict:
        for k, s in enumerate(steps):
            if s.get("expect") not in ("retrieve", "context"):
                continue
            live = [(ref, item) for ref, (i, item) in made.items()
                    if i < k and item["project"] == s["project"] and active_at(ref, k)]
            for p in s.get("present") or []:
                shown = (lambda text: text) if s["expect"] == "retrieve" else held_text
                holders = [ref for ref, item in live if contains(shown(item["text"]), p)]
                if not holders:
                    _reject(path, "present_not_active", f"«{p[:40]}» لا يقع في عنصرٍ قائمٍ في مشروع الخطوة {k} عندها")
                if not any(_reached(s, ref, live) for ref in holders):
                    _reject(path, "present_unretrievable", f"«{p[:40]}» في عنصرٍ لا يبلغه سؤالُ الخطوة {k} بقاعدة المخزن")


def _validate_steps(scenario: dict, path: str, projects: set[str]) -> None:
    refs: dict[str, str] = {}            # المرجع ← مشروعه
    kinds: dict[str, str] = {}           # المرجع ← نوعه (عنصر، اقتراح، نسخة)
    forgotten, checked_residue, receipted, touched = set(), set(), set(), set()
    approved: set[str] = set()           # اقتراحٌ وُوفق عليه لا يُوافَق عليه ثانيةً
    pending: set[str] = set()            # اقتراحاتٌ تنتظر المالك: ما دامت قائمةً يرفض المنتجُ النسخَ الاحتياطية
    born: dict[str, int] = {}            # المرجع ← خطوةُ إنشائه (لمعرفة ما محته استعادةُ نسخةٍ أُخذت قبله)
    erased: set[str] = set()             # اقتراحاتٌ محتها استعادةٌ فلا تُوافَق عليها (المُشغِّلُ لا يجدها: KeyError)
    expects = 0
    for i, step in enumerate(scenario["steps"]):
        where = f"{path}.steps[{i}]"
        if step.get("project") not in projects:
            _reject(where + ".project", "project_unknown", "مشروعٌ غير معلن")
        touched.add(step["project"])
        if "op" in step:
            op = step["op"]
            if op not in OPS or set(step) != OPS[op]:
                _reject(where, "op_invalid", f"عمليةٌ بحقولها المعلنة: {op!r}")
            if op in ("remember", "propose"):
                if not isinstance(step["text"], str) or not step["text"].strip():
                    _reject(where + ".text", "text_invalid", "نصٌّ غير فارغ")
                # وبحدِّ المخزن نفسِه (`MAX_ITEM_CHARS`، قبل التشذيب كما يعدّه): نصٌّ أطولُ يُردّ في الحفظ الأول بـtext_too_long
                # في المُشغِّلَين ويُحسب انحدارًا على المنتج (ملاحظة Codex على #129، الجولة الحادية والأربعون)
                if len(step["text"]) > MAX_ITEM_CHARS:
                    _reject(where + ".text", "text_too_long", f"نصٌّ أطولُ مما يقبله المخزن ({MAX_ITEM_CHARS} محرف)")
                if op == "remember" and step["consent"] not in CONSENTS:
                    _reject(where + ".consent", "consent_invalid", "owner أو none")
            if op == "backup" and pending:
                # المُشغِّلان يُبقيان الاقتراحَ الذي لم يُبتّ فيه فعلًا ينتظر المالك، فيرفض المنتجُ النسخَ بـbackup_pending ويُحسب
                # على المنتج لا على البنك (ملاحظة Codex على #129، الجولة السابعة والثلاثون)
                _reject(where, "backup_with_pending_proposal", "نسخةٌ احتياطية واقتراحٌ ينتظر المالك: " + ", ".join(sorted(pending)))
            if "as" in step:
                if step["as"] in refs:
                    _reject(where + ".as", "ref_reused", "مرجعٌ معرَّفٌ سابقًا")
                refs[step["as"]] = step["project"]
                born[step["as"]] = i
                kinds[step["as"]] = {"backup": "backup", "propose": "proposal"}.get(op, "item")
                if op == "propose":
                    # واقتراحٌ واحد ينتظر المالك في المشروع: جلسةُ الاقتراحات واحدةٌ للمشروع في المُشغِّل الموصول، فاقتراحٌ ثانٍ
                    # قبل البتّ في الأول يصطدم بدورٍ لم يُحسم (turn_unresolved) ويُحسب على المنتج (الجولة الثامنة والثلاثون)
                    if any(refs[r] == step["project"] for r in pending):
                        _reject(where, "propose_while_pending", "اقتراحٌ ثانٍ في مشروعٍ اقتراحُه ينتظر المالك")
                    pending.add(step["as"])
            if "ref" in step:
                ref = step["ref"]
                if ref not in refs or refs[ref] != step["project"]:
                    _reject(where + ".ref", "ref_unknown", "مرجعٌ غير معرَّف في مشروعه")
                wanted = {"approve": ("proposal",), "forget": ("item", "proposal"), "restore": ("backup",)}[op]
                if kinds[ref] not in wanted:
                    _reject(where + ".ref", "ref_kind", f"{op} لا يقع على {kinds[ref]}")
                # والموافقةُ على الاقتراح مرّةٌ واحدة: بعدها يحمل المرجعُ عنصرَه في المُشغِّلَين، فموافقةٌ ثانية تُرسل عنصرًا
                # حيث يُنتظر اقتراحٌ ويُحسب ردُّها على المنتج (ملاحظة Codex على #129، الجولة السادسة والثلاثون)
                if op == "approve":
                    if ref in approved:
                        _reject(where + ".ref", "approve_repeated", "اقتراحٌ وُوفق عليه سابقًا")
                    if ref in erased:
                        _reject(where + ".ref", "approve_of_erased_proposal", "اقتراحٌ محته استعادةُ نسخةٍ أُخذت قبله")
                    approved.add(ref)
                    pending.discard(ref)
                if op == "restore":
                    # الاستعادةُ تمحو ما اقتُرح بعد النسخة (جلسةَ الاقتراح وفعلَه)، فلا يبقى ينتظر المالك ولا يُوافَق عليه بعدها
                    # (ملاحظة Codex على #129، الجولة التاسعة والثلاثون)
                    for proposal in [p for p in pending if born[p] > born[ref]]:
                        pending.discard(proposal)
                        erased.add(proposal)
                if op == "forget":
                    forgotten.add(ref)
        elif "expect" in step:
            kind = step["expect"]
            allowed = EXPECTS.get(kind)
            extra = {"quarantined"} if kind == "context" else set()
            if allowed is None or not allowed <= set(step) <= allowed | extra:
                _reject(where, "expect_invalid", f"توقّعٌ بحقوله المعلنة: {kind!r}")
            if kind not in EXPOSING_EXPECTS + PRIVATE_EXPECTS:
                _reject(where, "expect_unclassified", f"توقّعٌ لم يُصنَّف: أيقرأ ما قد يعرض عنصرًا من مشروعٍ آخر؟ {kind!r}")
            for key in ("absent", "present"):
                if step.get(key):
                    _texts(step[key], f"{where}.{key}")
            if kind in ("retrieve", "context") and not (step["absent"] or step["present"]):
                _reject(where, "expect_empty", "توقّعٌ بلا نصٍّ غائبٍ ولا حاضر")
            if kind == "residue":
                _texts(step["absent"], f"{where}.absent")
                checked_residue.add(step["project"])
            if kind == "receipt":
                if step["ref"] not in refs or type(step["count"]) is not int or step["count"] < 1:
                    _reject(where, "receipt_invalid", "إيصالٌ لمرجعٍ معرَّف بعددٍ موجب")
                # والعددُ واحدٌ لا غير: المخزنُ يعيد إيصالَ النسيان الأول عند تكرار النسيان، فعددٌ غيرُه يحكم على مخزنٍ صحيح
                # «receipts 1 != n» (ملاحظة Codex على #129، الجولة السادسة والثلاثون)
                if step["count"] != 1:
                    _reject(where, "receipt_count_invalid", "إيصالُ النسيان واحدٌ لكلّ عنصر")
                # الإيصالُ يُفحص في مشروع مرجعه وبعد نسيانه، وإلا حُكم على مخزنٍ صحيح بـ«receipts 0 != 1» وعُدّ انحدارًا
                # (ملاحظة Codex على #129، الجولة الخامسة والثلاثون)
                if step["project"] != refs[step["ref"]]:
                    _reject(where, "receipt_project_mismatch", "إيصالٌ يُفحص في غير مشروع مرجعه")
                if step["ref"] not in forgotten:
                    _reject(where, "receipt_before_forget", "إيصالٌ قبل نسيان مرجعه")
                receipted.add(step["ref"])
            if step.get("quarantined") not in (None, True):
                _reject(where + ".quarantined", "quarantined_invalid", "True وحدها")
            expects += 1
        else:
            _reject(where, "step_invalid", "عمليةٌ أو توقّع")
    category = scenario["category"]
    if not expects:
        _reject(path, "no_expectation", "سيناريو بلا توقّع")
    if category in ("forget", "backup"):
        if not forgotten:
            _reject(path, "forget_missing", "سيناريو نسيانٍ بلا نسيان")
        if {refs[r] for r in forgotten} - checked_residue:
            _reject(path, "residue_unchecked", "منسيٌّ لم تُفحص بقاياه على القرص")
        if category == "forget" and forgotten - receipted:
            _reject(path, "receipt_unchecked", "منسيٌّ بلا فحص إيصال")
    if category == "isolation" and len(touched) < 2:
        _reject(path, "isolation_single_project", "العزلُ يُقاس بمشروعين على الأقل")
    if category == "injection" and not any(s.get("quarantined") for s in scenario["steps"]):
        _reject(path, "quarantine_unchecked", "سيناريو حجرٍ لا يطلب السياقَ محجورًا")
