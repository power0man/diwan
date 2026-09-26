"""مُشغِّلُ بنك الذاكرة المحكومة (ك٥٢) على `memory.store.MemoryStore`.

يُنفّذ عملياتِ كل سيناريو على مخازن مشاريع حقيقية في مجلدٍ مؤقّت، ويحكم على التوقّعات:
- `retrieve` و`context`: ما يُسترجع وما يبلغ السياق.
- `residue`: بايتاتُ مجلد الذاكرة على القرص.
- `receipt`: إيصالُ النسيان بعدده.

ويجمع المقاييس الأربعة المسجَّلة سلفًا في `docs/MEMORY-DESIGN.md` §٦. والاقتراحاتُ المعلّقة
والنسخُ الاحتياطية يحملها المشغِّلُ خارج مجلد الذاكرة، كما يحملها النظامُ الحقيقيّ.

وبـ`driver="wired"` (ك٥٥) يمرّ البنكُ نفسُه عبر الطريق الموصول: `webui.server.LocalApp.dispatch`
بمزوّدٍ محلّيٍّ مكتوبٍ سلفًا يلتقط كلَّ طلب:
- الحفظُ بموافقة المالك فعلُه في الواجهة (`memory_remember`). وأيُّ موافقةٍ غيرها لا طريقَ لها
  في المنتج إلا اقتراحَ النموذج، فتُمثَّل اقتراحًا بـ`propose_memory` يرفضه المالك.
- الاقتراحُ نداءُ `propose_memory` يقف `awaiting_owner`، والقبولُ `agent_decide` ثم `agent_resume`.
- النسيانُ `memory_forget`، والاسترجاعُ قائمةُ المالك (`memory`).
- السياقُ ما رآه النموذج فعلًا: جولةٌ وكيلة وجولةٌ نصّية في جلستين دائمتين لكل مشروع. والغائبُ
  يُفحص في كل كتلة ذاكرةٍ في الطلب، الحاليةِ وما قد يبقى في التاريخ؛ والحاضرُ في الكتلة الحالية.
- النسخُ والاستعادة طريقُ المنتج نفسُه (`workspace_tools/backup.py`): تُغلق الواجهة، وتُنسخ المساحةُ
  كلُّها، وتُستعاد إلى جذرٍ جديد بإيصالات نسيان المساحة الحيّة، ثم تُفتح الواجهةُ عليه. والجلساتُ
  تُنشأ حين تُطلب، لأنّ النسخةَ الاحتياطية لا تقبل الجلساتِ الوكيلة بعد.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

import uuid

from core.attribution import normalize
from memory.store import HEADER, MemoryRefused, MemoryStore


def _contains(haystack: str, needle: str) -> bool:
    return needle in haystack or normalize(needle).strip() in normalize(haystack)


def _residue(store: MemoryStore) -> bytes:
    return b"\n".join(p.read_bytes() for p in sorted(store.root.rglob("*")) if p.is_file())


def run_scenario(scenario: dict, root: Path) -> dict:
    stores: dict[str, MemoryStore] = {}
    refs: dict[str, object] = {}
    failures: list[str] = []
    leaks = consent_violations = unquarantined = 0

    def store(project):
        if project not in stores:
            (root / project).mkdir(parents=True, exist_ok=True)
            stores[project] = MemoryStore(root / project)
        return stores[project]

    for index, step in enumerate(scenario["steps"]):
        s = store(step["project"])
        op, expect = step.get("op"), step.get("expect")
        if op == "remember":
            try:
                refs[step["as"]] = s.remember(step["text"], consent=step["consent"])
            except MemoryRefused as exc:
                if step["consent"] == "owner" or exc.code != "consent_required":
                    failures.append(f"{index}: refused {exc.code}")
                refs[step["as"]] = None
        elif op == "propose":
            refs[step["as"]] = s.propose(step["text"])
        elif op == "approve":
            refs[step["ref"]] = s.approve(refs[step["ref"]])
        elif op == "forget":
            s.forget(refs[step["ref"]])
        elif op == "backup":
            refs[step["as"]] = s.backup()
        elif op == "restore":
            s.restore(refs[step["ref"]])
        elif expect in ("retrieve", "context"):
            text = (" ".join(i["text"] for i in s.retrieve(step["query"])) if expect == "retrieve"
                    else s.context_block(step["question"]))
            for needle in step["absent"]:
                if _contains(text, needle):
                    failures.append(f"{index}: {expect} holds absent «{needle[:30]}»")
                    if scenario["category"] == "isolation":
                        leaks += 1
                    if scenario["category"] == "consent":
                        consent_violations += 1
                    if scenario["category"] == "injection":
                        unquarantined += 1
            for needle in step["present"]:
                if not _contains(text, needle):
                    failures.append(f"{index}: {expect} lacks present «{needle[:30]}»")
            if step.get("quarantined"):
                fenced = text.startswith(HEADER) and "<<<مادة:" in text and "<<</مادة:" in text
                inside = text.split("<<<مادة:", 1)[-1].rsplit("<<</مادة:", 1)[0] if fenced else ""
                if not fenced or any(not _contains(inside, n) for n in step["present"]):
                    failures.append(f"{index}: context not fenced")
                    unquarantined += 1
        elif expect == "residue":
            raw = _residue(s)
            for needle in step["absent"]:
                if needle.encode("utf-8") in raw:
                    failures.append(f"{index}: residue holds «{needle[:30]}»")
                    if scenario["category"] == "consent":
                        consent_violations += 1
        elif expect == "receipt":
            ref = refs[step["ref"]]
            item_id = ref if isinstance(ref, str) else None
            count = len(s.receipts(item_id)) if item_id else 0
            if count != step["count"]:
                failures.append(f"{index}: receipts {count} != {step['count']}")
    return {"id": scenario["id"], "category": scenario["category"], "passed": not failures,
            "failures": failures, "leaks": leaks, "consent_violations": consent_violations,
            "injection_unquarantined": unquarantined}


class _ScriptedProvider:
    """مزوّدٌ محلّيٌّ للبنك: يلتقط كلَّ طلب، ويجيب بما وُضع له أو بجوابٍ لا ذاكرةَ فيه.

    وبمزوّدٍ حيّ (`delegate`، جديد-memory-probe) يذهب كلُّ طلبٍ لم يُوضع له جوابٌ إلى النموذج الحقيقيّ.
    والاقتراحُ يبقى مكتوبًا سلفًا، لأن البنك يقيس الموافقةَ عليه لا أن النموذج يقترح."""
    name, is_local = "memory-bank", True

    def __init__(self, delegate=None):
        self.requests, self.responses, self.delegate = [], [], delegate
        if delegate is not None:
            self.name, self.is_local = delegate.name, delegate.is_local

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        from core.contracts import Response, Usage
        self.requests.append(request)
        if self.responses:
            return self.responses.pop(0)
        if self.delegate is not None:
            return self.delegate.complete(request)
        return Response("تم.", Usage(1, 1), "complete", 0, provider=self.name, model_version="0" * 64)


_OPEN = re.compile(r"<<<مادة:([0-9a-f]{8,64})>>>")


def _block_of(content: str) -> str:
    """كتلةُ الذاكرة في أول الرسالة: العنوانُ ثم السياجُ حتى إغلاقه بنونسه هو، لا بآخر إغلاقٍ في الرسالة."""
    if not content.startswith(HEADER + "\n"):
        return ""
    opened = _OPEN.match(content, len(HEADER) + 1)
    if opened is None:
        return ""
    close = f"<<</مادة:{opened.group(1)}>>>"
    end = content.find(close, opened.end())
    return "" if end < 0 else content[:end + len(close)]


def _memory_parts(request) -> tuple[str, str]:
    """(كتلةُ الطلب الحالي، كلُّ كتل الذاكرة في رسائل المستخدم) — وما سواها كلامُ المالك نفسِه."""
    blocks = [(index, _block_of(message.content)) for index, message in enumerate(request.messages)
              if message.role == "user"]
    current = next((block for index, block in blocks if index == len(request.messages) - 1), "")
    return current, "\n".join(block for _, block in blocks if block)


class _ConsentBypassed(RuntimeError):
    """اقتراحُ النموذج لم يقف ينتظر المالك: خرقُ موافقةٍ يُعدّ، لا عطبٌ مجهول."""


class _Wired:
    """الطريقُ الموصول: خادمُ الواجهة نفسُه، بلا شبكةٍ ولا نموذجٍ حيّ."""

    def __init__(self, root: Path, delegate=None):
        self.provider = _ScriptedProvider(delegate)
        self.root, self.generation = root, 0
        self.projects: dict[str, dict] = {}
        self._open()

    def _open(self):
        from webui.server import LocalApp
        self.app = LocalApp(self.root, model="memory-bank", model_version="0" * 64,
                            provider_factory=lambda: self.provider,
                            agent_provider_factory=lambda: self.provider)

    def close(self):
        self.app.close()

    def backup(self):
        """نسخةُ المساحة كلِّها بأداة المنتج؛ والواجهةُ مغلقةٌ أثناءها كما يشترط النسخ."""
        from workspace_tools.backup import export_workspace
        self.generation += 1
        archive = self.root.parent / f"backup-{self.generation}.json"
        self.app.close()
        try:
            return archive, export_workspace(self.root, archive)["sha256"]
        finally:
            self._open()

    def restore(self, backup):
        """استعادةٌ إلى جذرٍ جديد بإيصالات نسيان المساحة الحيّة، ثم تُفتح الواجهةُ عليه."""
        from workspace_tools.backup import restore_workspace
        archive, sha256 = backup
        self.generation += 1
        destination = self.root.parent / f"restored-{self.generation}"
        self.app.close()
        try:
            restore_workspace(archive, destination, sha256, tombstones_from=self.root)
            self.root = destination
        finally:
            self._open()

    def api(self, action, **values):
        return self.app.dispatch({"action": action, **values})

    def project(self, name):
        if name not in self.projects:
            self.projects[name] = {"id": self.api("create_project", name=f"مشروع {name}")["id"]}
        return self.projects[name]

    def session(self, name, kind):
        ids = self.project(name)
        if kind not in ids:
            mode = "text" if kind == "text" else "agent"
            ids[kind] = self.api("create_session", project=ids["id"], name=kind, mode=mode)["id"]
        return ids[kind]

    def probe_session(self, name, kind):
        """جلسةُ الفحص. بالمزوّد المكتوب جلسةٌ واحدة للمشروع؛ وبالحيّ جلسةٌ جديدة لكل فحص، فأداةٌ يطلبها النموذجُ
        من تلقاء نفسه (propose_memory ينتظر المالك) لا تُبقي جولةً معلّقة تُسقط الفحصَ التالي بـturn_unresolved."""
        if self.provider.delegate is None:
            return self.session(name, kind)
        mode = "text" if kind == "text" else "agent"
        return self.api("create_session", project=self.project(name)["id"], name=f"{kind}-{uuid.uuid4().hex[:8]}",
                        mode=mode)["id"]

    def store(self, name) -> MemoryStore:
        return MemoryStore(self.app.project(self.project(name)["id"]))

    def propose(self, name, text):
        from core.contracts import Response, ToolCall, Usage
        ids = self.project(name)
        call = ToolCall("call_" + uuid.uuid4().hex[:8], "propose_memory", {"text": text})
        self.provider.responses.append(Response("", Usage(1, 1), "complete", 0, provider="memory-bank",
                                                model_version="0" * 64, tool_calls=(call,)))
        turn = uuid.uuid4().hex
        result = self.api("agent_ask", project=ids["id"], session=self.session(name, "proposals"), turn=turn,
                          message="اقترح ما يستحق الحفظ", files=[])
        pending = [a for a in result["pending"] if a["name"] == "propose_memory"]
        if result["status"] != "awaiting_owner" or len(pending) != 1:
            raise _ConsentBypassed(result["status"])
        return {"project": name, "turn": turn, "action": pending[0]}

    def decide(self, ref, approve):
        ids = self.project(ref["project"])
        action = ref["action"]
        self.api("agent_decide", project=ids["id"], session=ids["proposals"], action_id=action["action_id"],
                 call_digest=action["call_digest"], expected_revision=action["revision"], approve=approve)
        result = self.api("agent_resume", project=ids["id"], session=ids["proposals"], turn=ref["turn"])
        found = [r.get("item_id") for step in result["steps"] for r in step["tool_results"]
                 if r["name"] == "propose_memory" and r["status"] == "ok"]
        return found[0] if found else None

    def items_text(self, name):
        return " ".join(item["text"] for item in self.api("memory", project=self.project(name)["id"])["items"])

    def contexts(self, name, question):
        """ما رآه النموذجُ في جولةٍ وكيلة وجولةٍ نصّية بالسؤال نفسِه."""
        ids = self.project(name)
        seen = []
        for action, session in (("agent_ask", self.probe_session(name, "agent")),
                                ("ask", self.probe_session(name, "text"))):
            before = len(self.provider.requests)
            self.api(action, project=ids["id"], session=session, turn=uuid.uuid4().hex, message=question, files=[])
            new = self.provider.requests[before:]
            # النموذجُ الحيّ قد يستدعي أداةً فتطول الجولة؛ والذاكرةُ في أول طلبٍ منها
            (request,) = new if self.provider.delegate is None else new[:1]
            seen.append(_memory_parts(request))
        return seen

    def receipts(self, name, item_id):
        return [r for r in self.api("memory", project=self.project(name)["id"])["receipts"]
                if r["item_id"] == item_id]


def run_wired_scenario(scenario: dict, root: Path, delegate=None) -> dict:
    wired = _Wired(root / "ui", delegate)
    refs: dict[str, object] = {}
    failures: list[str] = []
    leaks = consent_violations = unquarantined = 0
    index = -1
    try:
        for index, step in enumerate(scenario["steps"]):
            name, op, expect = step["project"], step.get("op"), step.get("expect")
            if op == "remember":
                if step["consent"] == "owner":
                    refs[step["as"]] = wired.api("memory_remember", project=wired.project(name)["id"],
                                                 text=step["text"])["item_id"]
                else:
                    # لا طريقَ في المنتج لحفظٍ بغير موافقة المالك إلا اقتراحُ النموذج، والمالكُ يرفضه
                    saved = wired.decide(wired.propose(name, step["text"]), approve=False)
                    if saved is not None:
                        failures.append(f"{index}: denied proposal saved")
                        consent_violations += 1
                    refs[step["as"]] = None
            elif op == "propose":
                refs[step["as"]] = wired.propose(name, step["text"])
            elif op == "approve":
                refs[step["ref"]] = wired.decide(refs[step["ref"]], approve=True)
            elif op == "forget":
                wired.api("memory_forget", project=wired.project(name)["id"], item_id=refs[step["ref"]])
            elif op == "backup":
                refs[step["as"]] = wired.backup()
            elif op == "restore":
                wired.restore(refs[step["ref"]])
            elif expect in ("retrieve", "context"):
                views = ([(wired.items_text(name), wired.items_text(name))] if expect == "retrieve"
                         else wired.contexts(name, step["question"]))
                for current, every in views:
                    for needle in step["absent"]:
                        if _contains(every, needle):
                            failures.append(f"{index}: {expect} holds absent «{needle[:30]}»")
                            if scenario["category"] == "isolation":
                                leaks += 1
                            if scenario["category"] == "consent":
                                consent_violations += 1
                            if scenario["category"] == "injection":
                                unquarantined += 1
                    for needle in step["present"]:
                        if not _contains(current, needle):
                            failures.append(f"{index}: {expect} lacks present «{needle[:30]}»")
                    if step.get("quarantined"):
                        fenced = current.startswith(HEADER) and "<<<مادة:" in current and "<<</مادة:" in current
                        inside = (current.split("<<<مادة:", 1)[-1].rsplit("<<</مادة:", 1)[0] if fenced else "")
                        if not fenced or any(not _contains(inside, n) for n in step["present"]):
                            failures.append(f"{index}: context not fenced")
                            unquarantined += 1
            elif expect == "residue":
                store = wired.store(name)
                raw = _residue(store)
                for needle in step["absent"]:
                    if needle.encode("utf-8") in raw:
                        failures.append(f"{index}: residue holds «{needle[:30]}»")
                        if scenario["category"] == "consent":
                            consent_violations += 1
            elif expect == "receipt":
                ref = refs[step["ref"]]
                count = len(wired.receipts(name, ref)) if isinstance(ref, str) else 0
                if count != step["count"]:
                    failures.append(f"{index}: receipts {count} != {step['count']}")
    except Exception as exc:
        # عطبٌ في الطريق الموصول رسوبٌ مسمًّى للسيناريو، لا توقّفٌ للبنك كلِّه
        if isinstance(exc, _ConsentBypassed):
            consent_violations += 1
        failures.append(f"{index}: raised {type(exc).__name__} {getattr(exc, 'code', '')}".rstrip())
    finally:
        wired.close()
    return {"id": scenario["id"], "category": scenario["category"], "passed": not failures,
            "failures": failures, "leaks": leaks, "consent_violations": consent_violations,
            "injection_unquarantined": unquarantined}


WIRED_PATHS = {"remember": "memory_remember (واجهة المالك)", "remember_without_consent": "propose_memory يرفضه المالك",
               "propose": "propose_memory (awaiting_owner)", "approve": "agent_decide ثم agent_resume",
               "forget": "memory_forget", "retrieve": "memory (قائمة المالك)",
               "context": "طلبُ النموذج في agent_ask وask",
               "backup": "workspace_tools.backup.export_workspace (المساحة كلُّها)",
               "restore": "workspace_tools.backup.restore_workspace(tombstones_from=المساحة الحيّة) إلى جذرٍ جديد"}


def run_memory_bank(bank: dict, driver: str = "store", delegate=None) -> dict:
    """driver: store (المخزن وحده)، أو wired (الواجهة بمزوّدٍ مكتوب)، أو live (الواجهة بمزوّدٍ حيّ)."""
    if driver not in ("store", "wired", "live"):
        raise ValueError("driver: store أو wired أو live")
    if (driver == "live") != (delegate is not None):
        raise ValueError("live يلزمه مزوّدٌ حيّ، وغيرُه لا يقبله")
    run = (run_scenario if driver == "store"
           else lambda scenario, root: run_wired_scenario(scenario, root, delegate))
    results = []
    for scenario in bank["scenarios"]:
        with tempfile.TemporaryDirectory(prefix="diwan-memory-") as tmp:
            results.append(run(scenario, Path(tmp).resolve()))
    forgetting = [r for r in results if r["category"] in ("forget", "backup")]
    metrics = {
        "forget_rate": round(sum(r["passed"] for r in forgetting) / len(forgetting), 4) if forgetting else None,
        "leakage": sum(r["leaks"] for r in results),
        "consent_violations": sum(r["consent_violations"] for r in results),
        "injection_unquarantined": sum(r["injection_unquarantined"] for r in results),
    }
    thresholds = bank["thresholds"]
    meets = (metrics["forget_rate"] == thresholds["forget_rate"]
             and all(metrics[k] <= thresholds[k] for k in ("leakage", "consent_violations", "injection_unquarantined"))
             and all(r["passed"] for r in results))
    return {"schema_version": 1, "suite_id": bank["suite_id"], "driver": driver,
            **({"paths": WIRED_PATHS} if driver != "store" else {}),
            **({"provider": delegate.name} if delegate is not None else {}),
            "metrics": metrics, "meets_thresholds": meets,
            "passed": sum(r["passed"] for r in results), "total": len(results), "results": results}
