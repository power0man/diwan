"""واجهةُ محوِّل المشروع (ق٧٦ البند ٩): حدُّ الاستخراج من اليوم الأول.

المرسِلُ والمراجعُ والسجلُّ لا يعرفون GitHub ولا الوسوم ولا المسارات ولا قرارات ديوان؛ يسألون هذه الواجهة.
تنفيذُها الوحيد اليوم `team/projects/diwan.py`. وما لا يقدّمه مشروعٌ يُعلن `NotImplementedError` باسمه لا يُحاكى.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    labels: tuple[str, ...] = ()
    author: str = ""


@dataclass(frozen=True)
class PullRequest:
    number: int
    head_sha: str
    base_branch: str
    branch: str
    issue: int | None
    commit_messages: tuple[str, ...] = ()
    state: str = "open"
    merge_sha: str | None = None
    url: str = ""


@dataclass
class ReviewPolicy:
    """من يُحتسب مراجعًا لعائلات المؤلّفين، مرتَّبًا؛ ومن لا يُحتسب أبدًا."""
    candidates: list[str] = field(default_factory=list)
    never: tuple[str, ...] = ()


class ProjectError(RuntimeError):
    """عطبٌ في الوصول إلى منصّة المشروع (شبكةٌ، مصادقة، حصّة): رفضٌ مسمًّى برمزه يقرؤه سطرُ الأوامر، لا انفجارٌ خام."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


class ProjectAdapter:
    name = "abstract"
    default_base_branch = "main"

    # — مرجعُ المهمة والإذن —
    def issue(self, number: int) -> Issue:
        raise NotImplementedError("issue")

    def ready_granted(self, number: int, family: str) -> bool:
        """هل آخرُ حدثٍ على `ready:<family>` وضعٌ فاعلُه المالك؟"""
        raise NotImplementedError("ready_granted")

    def frozen_findings(self, issue: Issue) -> list[str]:
        """رموزُ ما يمنع التكليفَ بقرارات المشروع (تجميدٌ، مساراتُ النواة…)؛ الفارغةُ أنْ لا مانع."""
        return []

    def lane_owner(self, path: str) -> str | None:
        return None

    def worker_findings(self, worker_family: str, *, worker_name: str = "", worker_model: str = "") -> list[str]:
        """Project-specific registration and role checks for the actual worker surface/model."""
        return []

    def brief_header(self, issue: Issue, worker_family: str, *, worker_name: str = "", worker_model: str = "") -> str:
        """ما يُقال للعامل عن قواعد المشروع (الذيل، التسليم، ما لا يُمسّ)."""
        return ""

    # — طلباتُ الدمج والمراجعة —
    def reviewer_identity(self, name: str, family: str, model: str) -> str | None:
        """Registered identity for the actual counted reviewer, or None if not admitted."""
        return None

    def pull(self, number: int) -> PullRequest:
        raise NotImplementedError("pull")

    def pull_for_branch(self, branch: str) -> PullRequest | None:
        raise NotImplementedError("pull_for_branch")

    def create_pull(self, branch: str, title: str, body: str) -> PullRequest:
        raise NotImplementedError("create_pull")

    def comment(self, number: int, body: str) -> str:
        """يعلّق ويعيد مرجعَ التعليق."""
        raise NotImplementedError("comment")

    def checks(self, head_sha: str) -> str:
        """حالةُ فحوص الرأس: success | failure | pending | none."""
        raise NotImplementedError("checks")

    def author_families(self, pull: PullRequest) -> set[str]:
        raise NotImplementedError("author_families")

    def review_policy(self, families: set[str]) -> ReviewPolicy:
        raise NotImplementedError("review_policy")

    def proof_of_acceptance(self, pull: PullRequest) -> str | None:
        """إيداعُ الدمج على الفرع الرئيس إن وُجد؛ يُستنتج من git لا من إعلان."""
        raise NotImplementedError("proof_of_acceptance")
