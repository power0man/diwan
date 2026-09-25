"""نظام التوجيه السيادي متعدد المستويات والتعقيم الصارم لبيانات الهوية (م١٧، ق٤٤).

يضمن هذا النظام:
1. تعقيم البيانات الشخصية (PII Sanitization):
   - الهوية الوطنية والإقامة (10 أرقام تبدأ بـ 1 أو 2).
   - أرقام الهواتف السعودية (+966، 05، إلخ).
   - عناوين البريد الإلكتروني.
   - استبدالها برموز مستعارة قابلة للعكس بدقة: [هوية_محجوبة_X].
2. توجيه سيادي ثلاثي الطبقات:
   - المستوى 0 (محلي/حافة - Tier 0: Local Edge): تنفيذ محلي بالكامل (MLX / CPU / Local Ollama)، صفر خروج، لسياسات local_only.
   - المستوى 1 (سحابي وطني سيادي - Tier 1: Sovereign Cloud): استضافة وطنية داخل حدود المملكة بشهادات mTLS لسياسات regulated وinternal.
   - المستوى 2 (نماذج طليعية مع صفر حفظ - Tier 2: Frontier ZDR): نماذج متقدمة باتفاقيات Zero Data Retention، مشروطة بالتعقيم الكامل للبيانات الحساسة.
3. الإخفاق المغلق (Fail-Closed):
   - أي خرق لسياسة البيانات أو فشل في التعقيم يُسفر عن حجب فوري وتوقف آمن.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Callable, Dict, Literal, Optional, Tuple

from core.contracts import DATA_POLICIES, LOCAL_ONLY_POLICIES, Message, Request, Response

# أنماط الكشف عن البيانات الشخصية السعودية والدولية
_SAUDI_ID_PATTERN = re.compile(r"\b([12]\d{9})\b")
_SAUDI_PHONE_PATTERN = re.compile(r"(?:\+966|00966|966|0)(5\d{8}|1[1-7]\d{7})\b")
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b")


class SovereignRoutingError(Exception):
    """خطأ في التوجيه السيادي أو انتهاك لسياسات عزل البيانات."""
    def __init__(self, code: str, reason: str):
        super().__init__(f"[{code}] {reason}")
        self.code = code
        self.reason = reason


@dataclass
class PIISanitizer:
    """معقّم البيانات الشخصية والحساسة ذو الاستبدال العكسي المحكم."""

    @staticmethod
    def sanitize(text: str) -> Tuple[str, Dict[str, str]]:
        """يطهر النص من الهويات وأرقام الهواتف والبريد الإلكتروني ويستبدلها برموز حجب.

        يعيد النص المطهر مع جدول ربط الرموز بقيمها الأصلية لاسترجاعها لاحقًا.
        """
        if not text:
            return text, {}

        token_map: Dict[str, str] = {}
        reverse_map: Dict[str, str] = {}
        counter = 1

        def replace_match(match: re.Match, kind: str) -> str:
            nonlocal counter
            original = match.group(0)
            if original in reverse_map:
                return reverse_map[original]
            token = f"[هوية_محجوبة_{kind}_{counter}]"
            counter += 1
            token_map[token] = original
            reverse_map[original] = token
            return token

        # 1. تعقيم الهوية الوطنية والإقامة
        sanitized = _SAUDI_ID_PATTERN.sub(lambda m: replace_match(m, "هوية"), text)
        # 2. تعقيم أرقام الهواتف
        sanitized = _SAUDI_PHONE_PATTERN.sub(lambda m: replace_match(m, "هاتف"), sanitized)
        # 3. تعقيم البريد الإلكتروني
        sanitized = _EMAIL_PATTERN.sub(lambda m: replace_match(m, "بريد"), sanitized)

        return sanitized, token_map

    @staticmethod
    def desanitize(text: str, token_map: Dict[str, str]) -> str:
        """يعيد القيم الأصلية للنص المسترجع من النموذج الطليعي."""
        if not text or not token_map:
            return text
        result = text
        for token, original in token_map.items():
            result = result.replace(token, original)
        return result


SovereignTier = Literal["local_edge", "sovereign_cloud", "frontier_zdr"]


@dataclass(frozen=True)
class TierEndpoint:
    tier: SovereignTier
    name: str
    is_available: bool = True
    zero_data_retention: bool = False
    in_territory: bool = True  # هل الخادم داخل النطاق الجغرافي للمملكة؟


class SovereignRouter:
    """موجّه المهام السيادي ذو الطبقات الثلاث وسياسة الإخفاق المغلق."""

    def __init__(
        self,
        endpoints: Optional[Dict[SovereignTier, TierEndpoint]] = None,
        sanitizer: Optional[PIISanitizer] = None,
    ):
        self.endpoints = endpoints or {
            "local_edge": TierEndpoint("local_edge", "المعالج المحلي (MLX/CPU)", True, True, True),
            "sovereign_cloud": TierEndpoint("sovereign_cloud", "السحابة الوطنية السيادية", True, True, True),
            "frontier_zdr": TierEndpoint("frontier_zdr", "السحابة الطليعية المحمية (ZDR)", True, True, False),
        }
        self.sanitizer = sanitizer or PIISanitizer()

    def determine_tier(self, request: Request, target_tier: Optional[SovereignTier] = None) -> SovereignTier:
        """يحدد المستوى المسموح به بناءً على تصنيف البيانات (data_policy)."""
        policy = request.data_policy
        if policy not in DATA_POLICIES:
            raise SovereignRoutingError("unknown_policy", f"تصنيف البيانات غير معروف: {policy}")

        # في حال لم يطلب المستدعي مستوى معين، نختار المستوى الافتراضي الأنسب
        if target_tier is None:
            if policy in LOCAL_ONLY_POLICIES:
                target_tier = "local_edge"
            elif policy == "internal":
                target_tier = "sovereign_cloud"
            else:
                target_tier = "frontier_zdr"

        endpoint = self.endpoints.get(target_tier)
        if not endpoint or not endpoint.is_available:
            raise SovereignRoutingError("tier_unavailable", f"المستوى المطلوب غير متاح: {target_tier}")

        # تدقيق السياسات الصارم:
        # local_only لا يغادر الجهاز أبدًا
        if policy == "local_only" and target_tier != "local_edge":
            raise SovereignRoutingError(
                "policy_violation_local_only",
                "بيانات محصورة محليًا (local_only) لا يمكن توجيهها لسحابة وطنية أو خارجية."
            )

        # regulated لا يخرج خارج حدود المملكة إطلاقًا
        if policy == "regulated" and not endpoint.in_territory:
            raise SovereignRoutingError(
                "policy_violation_regulated",
                "بيانات خاضعة للوائح السيادية (regulated) لا يمكن معالجتها خارج الحدود الجغرافية للمملكة."
            )

        # frontier_zdr يتطلب اتفاقية صفر احتفاظ بالبيانات
        if target_tier == "frontier_zdr" and not endpoint.zero_data_retention:
            raise SovereignRoutingError(
                "policy_violation_retention",
                "المستوى الطليعي يفتقر إلى شرط صفر احتفاظ بالبيانات (Zero Data Retention)."
            )

        return target_tier

    def prepare_request(
        self,
        request: Request,
        target_tier: Optional[SovereignTier] = None
    ) -> Tuple[Request, Dict[str, str], SovereignTier]:
        """يجهز الطلب للمستوى المحدد ويطبق التعقيم الآلي إذا كان متجهًا للمستوى الطليعي."""
        effective_tier = self.determine_tier(request, target_tier)
        combined_token_map: Dict[str, str] = {}

        # إذا كان الطلب متجهاً للخارج (Tier 2)، نعقم جميع الرسائل تلقائياً
        if effective_tier == "frontier_zdr":
            new_messages = []
            for msg in request.messages:
                sanitized_content, token_map = self.sanitizer.sanitize(msg.content)
                combined_token_map.update(token_map)
                new_messages.append(
                    Message(
                        role=msg.role,
                        content=sanitized_content,
                        tool_call_id=msg.tool_call_id,
                    )
                )
            
            # إعادة بناء كائن الطلب مع الرسائل المعقمة
            sanitized_request = Request(
                messages=tuple(new_messages),
                model=request.model,
                model_version=request.model_version,
                max_output=request.max_output,
                deadline_s=request.deadline_s,
                data_policy=request.data_policy,
                idempotency_key=request.idempotency_key,
                tools=request.tools,
            )
            return sanitized_request, combined_token_map, effective_tier

        return request, combined_token_map, effective_tier

    def restore_response(self, response: Response, token_map: Dict[str, str]) -> Response:
        """يعيد بناء الجواب النهائي وفك حجب البيانات الشخصية للمستخدم الموثوق."""
        if not token_map or not response.content:
            return response

        restored_content = self.sanitizer.desanitize(response.content, token_map)
        return Response(
            content=restored_content,
            usage=response.usage,
            stop_reason=response.stop_reason,
            cost_micros=response.cost_micros,
            provider=response.provider,
            model_version=response.model_version,
            tool_calls=response.tool_calls,
        )
