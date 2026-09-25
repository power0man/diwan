"""عقود المعرفة — المادة والرسالة وعقد العقدة (م١ من المعمارية المعتمدة ق٢٠).

«المادة المعرفية» الكيانُ الأول في ديوان: كل قطعة معرفة تدخل النظام أو
تخرج منه تحمل **حقوقها وإسنادها وأصالتها عند الباب** — فمادةٌ بلا شاهد
أو بلا إذن استخدام ليست معرفةً بل قولٌ مرسل. وبهذا يصير النشرُ يوم يحين
استعلامًا («كل مادة وسمُ توزيعها يسمح») لا مشروعًا.

القاعدة الحاكمة قاعدةُ validate نفسها: **يُرفض الغائب والمجهول ولا
يُفترض أيّهما.** والنواة هنا تفرض ولا تفهم: لا معنى لغويًّا ولا مجاليًّا
في هذه العقود — المعنى كله في العقد التخصصية.

عُدِّل بعد التدقيق العدائي لم١ (١٢ عيبًا مثبتًا): حمولة الرسالة صارت
بنيةً مغلقة لكل نوع (لا نصّ حرّ)، والأسماء تُشذَّب ويُحجز البثّ حجزًا
مانعًا، وسلّم السياسات مُعرَّف، وبصمة الرسالة لا تحمل مفتاح عدم التكرار
(عرف م٠: المفتاح بياناتُ إعادةٍ لا حمولة — يبقى في القيد لا في البصمة).

نقصٌ معلن لا مُدَّعى خلافه: مخطط حمولة كل نوع رسالةٍ لكل عقدةٍ على حدة
(«accepts بمخططاتها») يُلحق بنسخة عقدٍ أعلى عند بناء أول عقدة (م٤).
وحقلا digest/prev الموعودان في مخطط الرسالة يضيفهما السجلُ عند التقييد
(غلاف Ledger) لا هذا العقد.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.canonical import PayloadRejected, check_payload
from core.contracts import DATA_POLICIES

SCHEMA_VERSION = 1

# درجات الأصالة: مجموعة مغلقة **مرتبة** — الأسبقُ أعلى حجيةً عند تعارضٍ
# متساوي الاختصاص. الترتيب حقلٌ تفحصه النواة بلا فهمٍ للمحتوى (ق٢٠).
ORIGINALITY_ORDER = ("original", "translated", "summarized", "derived")
ORIGINALITIES = frozenset(ORIGINALITY_ORDER)

# سلّم السياسات: ترتيبٌ كليّ تصاعديّ الحساسية — به تُحسب بوابة «السقف»
# في التوجيه (م٥): لا تُمرَّر رسالةٌ تصنيفُها أعلى من سقف عقدة المستقبِل.
POLICY_ORDER = ("public", "internal", "regulated", "local_only")
_POLICY_RANK = {p: i for i, p in enumerate(POLICY_ORDER)}


def policy_within_ceiling(policy: str, ceiling: str) -> bool:
    """هل يجوز لحمولةٍ بهذا التصنيف أن تصل عقدةً هذا سقفُها؟"""
    if policy not in _POLICY_RANK or ceiling not in _POLICY_RANK:
        raise PayloadRejected("policy", "policy_unknown",
                              f"تصنيف غير معروف: {policy!r}/{ceiling!r}")
    return _POLICY_RANK[policy] <= _POLICY_RANK[ceiling]


# أنواع الرسائل بين الأقسام: مجموعة مغلقة تُرفض المجهولة (كما DATA_POLICIES)
EVENT_KINDS = frozenset({
    "query", "answer", "item_published", "term_proposed",
    "conflict_open", "conflict_resolved", "refuse",
})
# ما يحمل معرفةً حمولتُه مادةٌ كاملة أو إشارة بصمية — حصرًا
ITEM_PAYLOAD_KINDS = frozenset({"item_published", "answer", "term_proposed"})
# ما قد يحرّك مالًا يلزمه سقفُ إنفاقٍ ومفتاحُ عدم تكرارٍ على الرسالة نفسها
BUDGETED_KINDS = frozenset({"query"})

BROADCAST = "broadcast"

_HEX64 = re.compile(r"[0-9a-f]{64}")
_DATE_YMD = re.compile(r"\d{4}-\d{2}-\d{2}")


def _reject(path: str, code: str, reason: str):
    raise PayloadRejected(path, code, reason)


def _require_str(value, path: str, code_prefix: str) -> None:
    if not isinstance(value, str) or not value.strip():
        _reject(path, f"{code_prefix}_missing", "قيمة نصّية غير فارغة مطلوبة")


def _require_clean(value, path: str, code_prefix: str) -> None:
    """نصٌّ غير فارغ **مشذَّب**: معرِّفٌ بمسافة طرفية يفلت من حجز الأسماء
    ويولّد مفاتيح أشباه — فيُرفض عند الباب لا يُصلَح صامتًا."""
    _require_str(value, path, code_prefix)
    if value != value.strip():
        _reject(path, f"{code_prefix}_whitespace", "مسافات طرفية في معرِّف")


def _require_clean_or_empty(value, path: str, code_prefix: str) -> None:
    if not isinstance(value, str):
        _reject(path, f"{code_prefix}_type", "قيمة نصّية مطلوبة")
    if value != value.strip():
        _reject(path, f"{code_prefix}_whitespace", "مسافات طرفية")


def _require_bool(value, path: str, code_prefix: str) -> None:
    if not isinstance(value, bool):
        _reject(path, f"{code_prefix}_type", "قيمة منطقية صريحة مطلوبة (True/False)")


def _require_hex64(value, path: str, code: str) -> None:
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        _reject(path, code, "بصمة sha256 ست عشرية من 64 خانة مطلوبة")


def _is_broadcast_like(name: str) -> bool:
    return name.strip().casefold() == BROADCAST


@dataclass(frozen=True)
class KnowledgeItem:
    # الإلزاميّ أوّلًا
    text: str
    lang: str                # لغة الأصل (مثل "ar"، "en") — لا قائمة مغلقة للغات
    domain: str              # المجال وسمٌ على المادة لا إطار للمكتبة
    use_internal: bool       # إذن الاستخدام الداخلي
    use_distribution: bool   # إذن التوزيع مع النسخة المنشورة
    source_id: str           # إحالة قيد الاستحواذ — الحقوق عند الباب
    locus: str               # الموضع الموافق للمطبوع (صفحة، أو مادة/بند)
    originality: str         # من ORIGINALITY_ORDER
    part: str = ""           # الجزء/المجلد إن وُجد
    glossary_ref: str = ""   # المسرد المطبق — إلزاميّ للمادة المعرَّبة

    def fingerprint_payload(self) -> dict:
        return {
            "text": self.text,
            "lang": self.lang,
            "domain": self.domain,
            "use_internal": self.use_internal,
            "use_distribution": self.use_distribution,
            "source_id": self.source_id,
            "part": self.part,
            "locus": self.locus,
            "originality": self.originality,
            "glossary_ref": self.glossary_ref,
            "schema_version": SCHEMA_VERSION,
        }


# مفاتيح حمولة المادة الكاملة — تُطابَق تطابقًا تامًّا (وتستوردها طبقة المخزن)
ITEM_PAYLOAD_FIELDS = frozenset({
    "text", "lang", "domain", "use_internal", "use_distribution",
    "source_id", "part", "locus", "originality", "glossary_ref",
    "schema_version",
})


def validated_item(item: KnowledgeItem) -> KnowledgeItem:
    """يعيد المادة نفسها إن جازت، وإلا رمى PayloadRejected برمزٍ مُسمّى."""
    if not isinstance(item, KnowledgeItem):
        _reject("item", "not_an_item", f"نوع غير مدعوم: {type(item).__name__}")

    _require_str(item.text, "item.text", "text")
    _require_clean(item.lang, "item.lang", "lang")
    _require_clean(item.domain, "item.domain", "domain")

    # الحقوق: منطقيّان صريحان، ومادة بلا أي إذنٍ ليست مادة
    _require_bool(item.use_internal, "item.use_internal", "use_internal")
    _require_bool(item.use_distribution, "item.use_distribution", "use_distribution")
    if item.use_internal is False and item.use_distribution is False:
        _reject("item.use_internal", "rights_none",
                "مادة بلا أي إذن استخدام — لا داخلي ولا توزيع")

    # الإسناد: مصدرٌ وموضعٌ إلزاميان، والجزء اختياري
    _require_clean(item.source_id, "item.source_id", "source_id")
    _require_clean(item.locus, "item.locus", "locus")
    _require_clean_or_empty(item.part, "item.part", "part")

    # الأصالة: مجموعة مغلقة
    if not isinstance(item.originality, str):
        _reject("item.originality", "originality_type", "درجة الأصالة نصّ")
    if item.originality not in ORIGINALITIES:
        _reject("item.originality", "originality_unknown",
                f"درجة أصالة غير معروفة: {item.originality!r}")

    # المسرد: نصٌّ مشذَّب دائمًا، وإلزاميٌّ للمعرَّب — نقلٌ بلا مسردٍ
    # محكوم هو بالضبط ما تمنعه المعمارية
    _require_clean_or_empty(item.glossary_ref, "item.glossary_ref", "glossary_ref")
    if item.originality == "translated" and not item.glossary_ref:
        _reject("item.glossary_ref", "glossary_required",
                "مادة معرَّبة بلا مسردٍ محكوم")

    check_payload(item.fingerprint_payload(), "item.fingerprint_payload")
    return item


def _item_from_payload(payload: dict, path: str) -> KnowledgeItem:
    """يعيد بناء مادةٍ من حمولة رسالة — البصمة تجمّد الماضي فالنسخة تُفحص."""
    if payload.get("schema_version") != SCHEMA_VERSION:
        _reject(f"{path}.schema_version", "payload_schema_version",
                f"نسخة مخطط غير مدعومة: {payload.get('schema_version')!r}")
    try:
        return KnowledgeItem(
            text=payload["text"], lang=payload["lang"], domain=payload["domain"],
            use_internal=payload["use_internal"],
            use_distribution=payload["use_distribution"],
            source_id=payload["source_id"], locus=payload["locus"],
            originality=payload["originality"], part=payload["part"],
            glossary_ref=payload["glossary_ref"],
        )
    except (KeyError, TypeError) as exc:
        _reject(path, "payload_shape", f"حمولة مادة مشوهة: {exc}")


def _validated_payload(kind: str, payload: dict, path: str) -> None:
    """بنية الحمولة مغلقةٌ لكل نوع — «لا نصّ حرّ أبدًا» فرضًا لا وعدًا.

    ما يحمل معرفةً (ITEM_PAYLOAD_KINDS): مادة كاملة تجتاز validated_item
    أو إشارة بصمية {"item_digest"}. وأما query/refuse/conflict فحمولاتها
    بنى تشغيلية مغلقة المفاتيح: نصُّ السؤال أو سبب الرفض محتوى عابر لا
    معرفةٌ تُخزَّن — وما يُخزَّن يعود موادَّ حصرًا.
    """
    keys = frozenset(payload)
    if kind in ITEM_PAYLOAD_KINDS:
        if keys == {"item_digest"}:
            _require_hex64(payload["item_digest"], f"{path}.item_digest",
                           "item_digest_invalid")
            return
        if keys == ITEM_PAYLOAD_FIELDS:
            validated_item(_item_from_payload(payload, path))
            return
        _reject(path, "payload_keys_unknown",
                f"حمولة {kind}: مادة كاملة أو {{item_digest}} حصرًا — "
                f"المفاتيح الواردة: {sorted(keys)}")
    if kind == "query":
        if keys != {"question", "domain"}:
            _reject(path, "payload_keys_unknown",
                    f"حمولة query: {{question, domain}} حصرًا — الوارد: {sorted(keys)}")
        _require_str(payload["question"], f"{path}.question", "question")
        _require_clean(payload["domain"], f"{path}.domain", "domain")
        return
    if kind == "refuse":
        if keys != {"code", "reason"}:
            _reject(path, "payload_keys_unknown",
                    f"حمولة refuse: {{code, reason}} حصرًا — الوارد: {sorted(keys)}")
        _require_clean(payload["code"], f"{path}.code", "code")
        _require_str(payload["reason"], f"{path}.reason", "reason")
        return
    if kind == "conflict_open":
        if keys != {"topic", "positions"}:
            _reject(path, "payload_keys_unknown",
                    f"حمولة conflict_open: {{topic, positions}} حصرًا — الوارد: {sorted(keys)}")
        _require_str(payload["topic"], f"{path}.topic", "topic")
        pos = payload["positions"]
        if not isinstance(pos, list) or not pos:
            _reject(f"{path}.positions", "positions_empty",
                    "قائمة بصمات مواد غير فارغة مطلوبة")
        for i, p in enumerate(pos):
            _require_hex64(p, f"{path}.positions[{i}]", "item_digest_invalid")
        return
    if kind == "conflict_resolved":
        if keys != {"topic", "ruling_digest"}:
            _reject(path, "payload_keys_unknown",
                    f"حمولة conflict_resolved: {{topic, ruling_digest}} حصرًا — الوارد: {sorted(keys)}")
        _require_str(payload["topic"], f"{path}.topic", "topic")
        _require_hex64(payload["ruling_digest"], f"{path}.ruling_digest",
                       "ruling_digest_invalid")
        return
    raise AssertionError(f"نوع بلا مخطط حمولة: {kind}")  # EVENT_KINDS يمنع بلوغه


@dataclass(frozen=True)
class KnowledgeEvent:
    """الرسالة بين قسمين: بنيةٌ مغلقة لكل نوع — لا نصّ حرّ أبدًا."""
    # الإلزاميّ أوّلًا
    kind: str                     # من EVENT_KINDS
    from_node: str
    to: str                       # اسم عقدة، أو BROADCAST
    correlation_id: str           # خيط المحادثة
    data_policy: str              # على الرسالة نفسها — بوابة التوجيه تحكم به
    payload: dict                 # بنيته يفرضها النوع (_validated_payload)
    idempotency_key: str | None   # إلزاميّ لما قد يحرّك مالًا — خارج البصمة
    budget_cap_micros: int = 0    # سقف إنفاق المستقبِل في خدمة هذه الرسالة
    in_reply_to: str | None = None

    def fingerprint_payload(self) -> dict:
        # مفتاح عدم التكرار بياناتُ إعادةٍ لا حمولة (عرف م٠ في Request):
        # رسالتان متكافئتان تعطيان بصمةً واحدة — والمفتاح يبقى في القيد.
        return {
            "kind": self.kind,
            "from_node": self.from_node,
            "to": self.to,
            "correlation_id": self.correlation_id,
            "data_policy": self.data_policy,
            "payload": self.payload,
            "budget_cap_micros": self.budget_cap_micros,
            "in_reply_to": self.in_reply_to,
            "schema_version": SCHEMA_VERSION,
        }


def validated_event(event: KnowledgeEvent) -> KnowledgeEvent:
    """يعيد الرسالة نفسها إن جازت، وإلا رمى PayloadRejected برمزٍ مُسمّى."""
    if not isinstance(event, KnowledgeEvent):
        _reject("event", "not_an_event", f"نوع غير مدعوم: {type(event).__name__}")

    if not isinstance(event.kind, str):
        _reject("event.kind", "kind_type", "نوع الرسالة نصّ")
    if event.kind not in EVENT_KINDS:
        _reject("event.kind", "kind_unknown", f"نوع غير معروف: {event.kind!r}")

    _require_clean(event.from_node, "event.from_node", "from_node")
    if _is_broadcast_like(event.from_node):
        _reject("event.from_node", "from_node_reserved",
                "البثّ وجهةٌ لا مرسِل — لا عقدة بهذا الاسم")
    _require_clean(event.to, "event.to", "to")
    _require_clean(event.correlation_id, "event.correlation_id", "correlation_id")

    # التصنيف على الرسالة نفسها — نفس قواعد الطلب
    if not isinstance(event.data_policy, str):
        _reject("event.data_policy", "data_policy_type", "تصنيف البيانات نصّ")
    if event.data_policy == "":
        _reject("event.data_policy", "data_policy_missing", "تصنيف البيانات غائب")
    if event.data_policy not in DATA_POLICIES:
        _reject("event.data_policy", "data_policy_unknown",
                f"تصنيف غير معروف: {event.data_policy!r}")

    if not isinstance(event.payload, dict) or not event.payload:
        _reject("event.payload", "payload_empty", "حمولة كائنٍ غير فارغ مطلوبة")
    _validated_payload(event.kind, event.payload, "event.payload")

    cap = event.budget_cap_micros
    if isinstance(cap, bool) or not isinstance(cap, int):
        _reject("event.budget_cap_micros", "budget_cap_type", "عدد صحيح مطلوب")
    if cap < 0:
        _reject("event.budget_cap_micros", "budget_cap_negative", f"قيمة سالبة: {cap}")

    k = event.idempotency_key
    if k is not None and (not isinstance(k, str) or not k.strip()):
        _reject("event.idempotency_key", "idempotency_key_blank",
                "إمّا None أو نصّ غير فارغ")

    # ما قد يحرّك مالًا: سقفٌ موجب ومفتاحُ عدم تكرارٍ — وإلا صار الاستئناف
    # بعد قطعٍ نداءً مدفوعًا مكرَّرًا
    if event.kind in BUDGETED_KINDS:
        if cap <= 0:
            _reject("event.budget_cap_micros", "budget_cap_required",
                    f"رسالة {event.kind} بلا سقف إنفاق")
        if k is None:
            _reject("event.idempotency_key", "idempotency_key_required",
                    f"رسالة {event.kind} بلا مفتاح عدم تكرار")

    r = event.in_reply_to
    if r is not None and (not isinstance(r, str) or not r.strip()):
        _reject("event.in_reply_to", "in_reply_to_blank", "إمّا None أو نصّ غير فارغ")

    check_payload(event.fingerprint_payload(), "event.fingerprint_payload")
    return event


@dataclass(frozen=True)
class NodeManifest:
    """عقدُ عقدةٍ تخصصية — يُبصَم عند التسجيل، وتغييره قيدُ نسخةٍ جديد.

    نقصٌ معلن: مخططات حمولة كل نوعٍ لكل عقدة («accepts بمخططاتها») تُلحق
    بنسخة عقدٍ أعلى عند بناء أول عقدة حقيقية (م٤) — فالنسخة 1 تعتمد
    المخططات العامة في _validated_payload.
    """
    # الإلزاميّ أوّلًا
    name: str
    contract_version: int
    domains: tuple[str, ...]          # اختصاصاتها المعلنة
    accepts: tuple[str, ...]          # أنواع الرسائل التي تجيب عنها
    data_policy_ceiling: str          # أقصى تصنيفٍ يجوز أن يصلها (POLICY_ORDER)

    def fingerprint_payload(self) -> dict:
        return {
            "name": self.name,
            "contract_version": self.contract_version,
            "domains": list(self.domains),
            "accepts": list(self.accepts),
            "data_policy_ceiling": self.data_policy_ceiling,
            "schema_version": SCHEMA_VERSION,
        }


def validated_manifest(manifest: NodeManifest) -> NodeManifest:
    """يعيد العقد نفسه إن جاز، وإلا رمى PayloadRejected برمزٍ مُسمّى."""
    if not isinstance(manifest, NodeManifest):
        _reject("manifest", "not_a_manifest", f"نوع غير مدعوم: {type(manifest).__name__}")

    _require_clean(manifest.name, "manifest.name", "name")
    # الحجز بالتطبيع لا بالمطابقة الحرفية: «broadcast » وBroadcast كلاهما
    # يصطدم بدلالة البثّ غدًا — فيُرفض اليوم قبل أن تجمّده البصمة
    if _is_broadcast_like(manifest.name):
        _reject("manifest.name", "name_reserved", f"اسم محجوز: {BROADCAST!r}")

    cv = manifest.contract_version
    if isinstance(cv, bool) or not isinstance(cv, int):
        _reject("manifest.contract_version", "contract_version_type", "عدد صحيح مطلوب")
    if cv <= 0:
        _reject("manifest.contract_version", "contract_version_not_positive",
                f"قيمة غير موجبة: {cv}")

    if not isinstance(manifest.domains, tuple) or not manifest.domains:
        _reject("manifest.domains", "domains_empty", "اختصاص واحد على الأقل، في tuple")
    for i, d in enumerate(manifest.domains):
        _require_clean(d, f"manifest.domains[{i}]", "domain")

    if not isinstance(manifest.accepts, tuple) or not manifest.accepts:
        _reject("manifest.accepts", "accepts_empty", "نوع رسالة واحد على الأقل، في tuple")
    for i, a in enumerate(manifest.accepts):
        if not isinstance(a, str) or a not in EVENT_KINDS:
            _reject(f"manifest.accepts[{i}]", "accepts_unknown",
                    f"نوع غير معروف: {a!r}")

    c = manifest.data_policy_ceiling
    if not isinstance(c, str) or c not in DATA_POLICIES:
        _reject("manifest.data_policy_ceiling", "ceiling_unknown",
                f"تصنيف غير معروف: {c!r}")

    check_payload(manifest.fingerprint_payload(), "manifest.fingerprint_payload")
    return manifest
