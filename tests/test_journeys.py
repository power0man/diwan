"""حارسُ سجلّ رحلات المالك بلا نصوص (جديد-journeys-log، #163).

المخزنُ يبنيه المنتجُ نفسُه: `webui.server.LocalApp` بمزوّدٍ محلّيٍّ مكتوبٍ سلفًا، بمشروعٍ وجلساتٍ بكل وضعٍ يبلغه الاختبار
(نصّ، ووسائط، ووكيل، ومبرمج، وترجمة) وبكل صنف نتيجةٍ ينتجه المنتج (منجزة، ومبتورة، ومرفوضة، وتنتظر المالك، وانتهت مهلتُها،
وبلغت حدَّ الخطوات، وأوقفها المالك، ومجهولةُ النتيجة). وفي رسائل المالك وأجوبة النموذج واسم المشروع واسم الجلسة واسم الملف المرفوع ومحتواه
ومسار ما كتبه الوكيل وذاكرة المشروع نصوصٌ مميّزة، ثم يُشغَّل `tools/journeys.py` فلا يحمل تقريرُه شيئًا منها، ولا مسارًا ولا
معرّفًا ولا بصمة — بنصّه الخام وبصورتَي هروب JSON، ولو أُطفئ فحصُ الأداة الذاتي — وأعدادُه تطابق ما صنعه المنتج.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import uuid

import pytest

from core.canonical import canonical_bytes, digest
from core.contracts import Response, ToolCall, Usage
from providers.base import ProviderError
from webui.server import LocalApp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import journeys  # noqa: E402

VERSION = "0" * 64
# نصوصٌ مميّزة في كل موضعٍ يحفظ فيه المنتجُ نصًّا؛ وآخرُها بحروفٍ لاتينية صغيرة كرمز آلة (اسمُ جلسةٍ ورسالتُها)، فلا
# يكفي فحصُ الأداة لتقريرها وحده لصيده: الحارسُ يبحث عنها في بايتات التقرير نفسِها. والمنتجُ يحفظ رسالةَ الجولة داخل
# غلافٍ JSON (DIWAN_AGENT_INPUT_V2 وأشباهه) فتظهر مهرَّبةً، ولذلك يُبحث عن صورتَي الهروب أيضًا
SECRETS = {
    "project": "مشروع قلعة-الزمرد ٧٣٩١",
    "session": "جلسة كنز-البحر ٥٥٢٠",
    "user": "كلمة المرور السرية زبرجد-٨٨٤٦ فلا تخبر أحدًا",
    "answer": "الجواب المكتوم ياقوت-٣٣١٧",
    "file_name": "خطة-سرية-٦٦١٤.txt",
    "file_text": "محتوى الملف السري فيروز-٢٢٥٩",
    "written_path": "مذكرة-مخفية-١١٠٢.txt",
    "memory": "رمز الخزنة عقيق-٤٤٠٠",
    "proposal": "اقتراح الذاكرة مرجان-٥١٧٣",
    "machine_like": "zircon_vault_opens_at_dawn",
}
TOKENS = ("٧٣٩١", "٥٥٢٠", "٨٨٤٦", "٣٣١٧", "٦٦١٤", "٢٢٥٩", "١١٠٢", "٤٤٠٠", "٥١٧٣", "زمرد", "زبرجد", "ياقوت", "فيروز",
          "عقيق", "مرجان", "zircon", "owner_root_zq9")
DAY_ONE = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc).timestamp()
# التقديرُ يسقط قبل أن يُقيَّد النداء، فيحفظ المنتجُ الجولةَ النصّية «error» برمز outcome_uncertain (conversation/session.py)
UNCERTAIN = object()
DAY = 24 * 3600
# نافذةُ خطّ أساسٍ يمرّرها الاختبار (أكتوبر ٢٠٢٦ كلُّه)، فلا تتعلّق أعدادُه بتواريخ م١ في الخطة
WINDOW = ("--baseline-from", "2026-10-01", "--baseline-until", "2026-10-31")


def respond(content="تم.", *calls, stop="complete"):
    return Response(content, Usage(1, 1), stop, 0, provider="fixture", model_version=VERSION, tool_calls=tuple(calls))


class Provider:
    """مزوّدٌ محلّيٌّ مكتوبٌ سلفًا: يجيب بما وُضع له بالترتيب، أو بالجواب المكتوم."""
    name, is_local = "fixture", True

    def __init__(self):
        self.responses = []

    def estimate_micros(self, request):
        if self.responses and self.responses[0] is UNCERTAIN:
            self.responses.pop(0)
            raise RuntimeError("interrupted before the call was recorded")
        return 0

    def complete(self, request):
        item = self.responses.pop(0) if self.responses else respond(SECRETS["answer"])
        if isinstance(item, Exception):
            raise item
        return item


class World:
    """مخزنُ الواجهة اليومية كما يكتبه المنتج، ومعه كلُّ معرّفٍ صنعه، ليُبحث عنه في التقرير."""

    def __init__(self, root: Path):
        self.root, self.provider = root, Provider()
        self.ids: list[str] = []
        self.sessions: dict[str, str] = {}
        self.app = LocalApp(root, model="fixture", model_version=VERSION, provider_factory=lambda: self.provider,
                            agent_provider_factory=lambda: self.provider, media_model="fixture-vision",
                            media_model_version="1" * 64, media_provider_factory=lambda: self.provider)
        try:
            self._build()
        finally:
            self.app.close()

    def api(self, action, **values):
        return self.app.dispatch({"action": action, **values})

    def session(self, key, mode, name=SECRETS["session"]):
        self.sessions[key] = self.api("create_session", project=self.project, name=name, mode=mode)["id"]
        self.ids.append(self.sessions[key])
        return self.sessions[key]

    def ask(self, key, message, *responses, action="ask", files=()):
        self.provider.responses.extend(responses)
        turn = uuid.uuid4().hex
        self.ids.append(turn)
        extra = {"media": []} if action == "ask_media" else {"files": list(files)}
        result = self.api(action, project=self.project, session=self.sessions[key], turn=turn, message=message,
                          **extra)
        assert not self.provider.responses, "كلُّ جوابٍ مكتوبٍ سلفًا استُهلك"
        return turn, result

    def _build(self):
        self.project = self.api("create_project", name=SECRETS["project"])["id"]
        self.ids.append(self.project)
        upload = uuid.uuid4().hex
        self.ids.append(upload)
        attached = self.api("upload", project=self.project, upload=upload, name=SECRETS["file_name"],
                            content=SECRETS["file_text"])["path"]
        # الطريقُ النصّي: منجزة، ومبتورة، وعطبان (جوابٌ بسبب error، ومزوّدٌ لم يُجب)؛ والعطبُ «مرفوضة»؛ ثم جولةٌ انقطعت
        # قبل أن يُقيَّد نداؤها فنتيجتُها مجهولة (error برمز outcome_uncertain)، لا «مرفوضة»
        self.session("text", "text")
        results = [self.ask("text", SECRETS["user"], respond(SECRETS["answer"]), files=[attached])[1],
                   self.ask("text", SECRETS["user"], respond(SECRETS["answer"], stop="max_output"))[1],
                   self.ask("text", SECRETS["user"], respond("", stop="error"))[1],
                   self.ask("text", SECRETS["user"],
                            ProviderError("provider_unavailable", SECRETS["answer"], False))[1],
                   self.ask("text", SECRETS["user"], UNCERTAIN)[1]]
        assert [(r["status"], r["error_code"]) for r in results] == [
            ("complete", None), ("truncated", "output_truncated"), ("error", "provider_error"),
            ("error", "provider_unavailable"), ("error", "outcome_uncertain")]
        # الطريقُ الوكيل: كتابةُ ملفٍّ ثم جواب، ورفض، ومهلة، ومزوّدٌ لم يُجب، وبتر، وحدُّ الخطوات الثماني
        self.session("agent", "agent")
        write = ToolCall("w", "write_file", {"path": SECRETS["written_path"], "content": SECRETS["file_text"]})
        statuses = [self.ask("agent", SECRETS["user"], respond(SECRETS["answer"], write), respond(SECRETS["answer"]),
                             action="agent_ask", files=[attached])[1]["status"]]
        for responses in ([respond(SECRETS["answer"], stop="refused")], [respond("", stop="deadline")],
                          [ProviderError("provider_unavailable", SECRETS["answer"], False)],
                          [respond(SECRETS["answer"], stop="max_output")],
                          [respond(SECRETS["answer"], ToolCall(f"c{i}", "list_files", {})) for i in range(8)]):
            statuses.append(self.ask("agent", SECRETS["user"], *responses, action="agent_ask")[1]["status"])
        assert statuses == ["complete", "refused", "timed_out", "failed", "truncated", "step_limit"]
        propose = ToolCall("m", "propose_memory", {"text": SECRETS["proposal"]})
        self.session("awaiting", "agent")
        assert self.ask("awaiting", SECRETS["user"], respond("", propose), action="agent_ask")[1]["status"] == \
            "awaiting_owner"
        self.session("stopped", "agent")
        turn, result = self.ask("stopped", SECRETS["user"], respond("", propose), action="agent_ask")
        assert result["status"] == "awaiting_owner"
        stop = self.api("agent_stop", project=self.project, session=self.sessions["stopped"], turn=turn)
        assert stop["status"] == "cancelled"
        # ذاكرةُ المشروع بعد الإيقاف: إيقافُ جولةٍ في مشروعٍ له ذاكرة يُرفض اليوم بـstate_corrupt (عطبٌ في مسار openai،
        # #147)، والجولاتُ بعدها تحمل كتلتَها في حالتها
        self.ids.append(self.api("memory_remember", project=self.project, text=SECRETS["memory"])["item_id"])
        # جلسةٌ اسمُها بشكل رمز آلة: لو بلغ حقلًا مفتوحًا لمرّ من فحص الأداة الذاتي، فالبايتاتُ وحدها تصيده
        self.session("coder", "coder", name=SECRETS["machine_like"])
        assert self.ask("coder", SECRETS["machine_like"], respond(SECRETS["machine_like"]),
                        action="agent_ask")[1]["status"] == "complete"
        self.session("translate", "translate")
        assert self.ask("translate", SECRETS["user"], respond(SECRETS["answer"]),
                        action="agent_ask")[1]["status"] == "complete"
        self.session("media", "media")
        assert self.ask("media", SECRETS["user"], respond(SECRETS["answer"]), action="ask_media")[1]["status"] == \
            "complete"
        # أزمنةُ الملفات ثابتةٌ في يومٍ واحد، فلا يتقلّب التاريخُ إن مرّ الاختبارُ على منتصف الليل
        for meta, state, _ in session_files(self.root):
            os.utime(meta, (DAY_ONE, DAY_ONE))
            os.utime(state, (DAY_ONE + 300, DAY_ONE + 300))


def session_files(root: Path) -> list[tuple[Path, Path, str]]:
    """(meta.json، state.json، الوضع) لكل جلسة، بتخطيط LocalApp نفسِه."""
    found = []
    for project in sorted((root / "projects").iterdir()):
        for session in sorted((project / "sessions").iterdir()):
            mode = json.loads((session / "meta.json").read_text(encoding="utf-8")).get("mode", "text")
            state = (project / "agent-control" / session.name if mode in journeys.AGENT_MODES
                     else session / "chat" / session.name) / "state.json"
            found.append((session / "meta.json", state, mode))
    return found


@pytest.fixture(autouse=True)
def probe(tmp_path, monkeypatch):
    """دليلُ docs/probe في كل اختبارٍ دليلٌ مؤقّت: لا يُقرأ منه ولا يُجمَّد فيه شيءٌ من المستودع."""
    directory = tmp_path / "probe"
    directory.mkdir()
    monkeypatch.setattr(journeys, "PROBE_DIR", directory)
    return directory


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return World(tmp_path_factory.mktemp("journeys").resolve() / "owner_root_zq9")


@pytest.fixture
def copy(world, tmp_path):
    """نسخةٌ من المخزن بأزمنة ملفاته، يعدّلها الاختبارُ وحده."""
    target = tmp_path / "owner_root_zq9"
    shutil.copytree(world.root, target, symlinks=True)
    return target


def run(root: Path, out: Path, capsys, *extra) -> tuple[int, dict]:
    code = journeys.main(["--root", str(root), "--out", str(out), *extra])
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def report_of(root: Path, tmp_path: Path, capsys, *extra) -> dict:
    out = tmp_path / f"report-{uuid.uuid4().hex[:6]}.json"
    code, printed = run(root, out, capsys, *extra)
    assert (code, printed["status"]) == (0, "written"), printed
    return json.loads(out.read_text(encoding="utf-8"))


def rewrite_state(path: Path, change, *, reseal=True):
    envelope = json.loads(path.read_text(encoding="utf-8"))
    change(envelope["state"])
    if reseal:
        envelope["sha256"] = digest(envelope["state"])
    path.write_bytes(canonical_bytes(envelope))


def forms(needle: str) -> set[str]:
    """النصُّ وصورتاه بهروب JSON: بـ\\uXXXX كما يكتبه ensure_ascii، ثم هروبٌ ثانٍ لنصٍّ حُفظ JSON داخل JSON."""
    once = json.dumps(needle)[1:-1]
    return {needle, once, json.dumps(once)[1:-1]}


def strings_of(value):
    """كلُّ مفتاحٍ وكلُّ قيمةٍ نصّية في التقرير بعد فكّ هروبها مرّةً بالقراءة."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from strings_of(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings_of(item)
    elif isinstance(value, str):
        yield value


def assert_carries_nothing_of(world, raw: bytes):
    """بايتاتُ التقرير لا تحمل نصًّا مميّزًا ولا مسارًا ولا معرّفًا بأيّ صورة، ولا شيئًا خارج الأعداد والرموز والتواريخ."""
    text = raw.decode("utf-8")
    report = json.loads(text)
    hays = [text, *strings_of(report)]
    needles = [*SECRETS.values(), *TOKENS, str(world.root), str(world.root.parent), *world.ids,
               *(value.upper() for value in world.ids)]
    for needle in needles:
        for form in forms(needle):
            assert form.encode("utf-8") not in raw and not any(form in hay for hay in hays), needle
    commit = report.pop("commit")
    assert commit is None or re.fullmatch(r"[0-9a-f]{40}", commit)
    rest = json.dumps(report, ensure_ascii=False)
    assert not re.search(r"[0-9a-fA-F]{12,}", rest), "لا معرّفَ ولا بصمةَ في التقرير"
    assert "/" not in rest.replace(journeys.TOOL, ""), "لا مسار"
    for label in (journeys.TASK, *journeys.OUTCOME_LABELS.values()):   # العربيُّ المسموح: اسمُ المهمة وتسمياتُ الأصناف
        rest = rest.replace(json.dumps(label, ensure_ascii=False), '""')
    assert rest.isascii() and "\\" not in rest, "لا نصَّ ولا هروبَ خارج الثوابت"


def test_the_report_carries_no_text_path_or_identifier(world, tmp_path, capsys):
    stored = b"\n".join([path.relative_to(world.root.parent).as_posix().encode("utf-8") for path in world.root.rglob("*")]
                        + [path.read_bytes() for path in world.root.rglob("*") if path.is_file()])
    for secret in SECRETS.values():                    # كلُّ نصٍّ مميّزٍ محفوظٌ فعلًا في المخزن، فغيابُه عن التقرير يعني شيئًا
        assert secret.encode("utf-8") in stored or json.dumps(secret)[1:-1].encode("ascii") in stored, secret
    out = tmp_path / "report.json"
    code, _ = run(world.root, out, capsys)
    assert code == 0
    assert_carries_nothing_of(world, out.read_bytes())


def test_the_bytes_carry_nothing_even_with_the_self_check_off(world, tmp_path, capsys, monkeypatch):
    """الحارسُ لا يستند إلى فحص الأداة الذاتي: يُطفأ الفحصُ فيُكتب التقريرُ كما بُني، والبحثُ في بايتاته يصيد التسرّب وحده."""
    monkeypatch.setattr(journeys, "check_report", lambda report: None)
    out = tmp_path / "report.json"
    code, _ = run(world.root, out, capsys)
    assert code == 0
    assert_carries_nothing_of(world, out.read_bytes())


def test_the_tool_knows_every_session_mode_of_the_product():
    """وضعٌ يضيفه المنتجُ ولا تعرفه الأداة تُعدّ جلساتُه mode_unknown لا رحلات؛ فالقائمتان تتطابقان."""
    from webui import server
    assert journeys.MODES == server.SESSION_MODES and journeys.AGENT_MODES == server.AGENT_MODES


def test_outcomes_modes_and_steps_are_counted_from_the_real_product(world, tmp_path, capsys):
    report = report_of(world.root, tmp_path, capsys)
    assert report["totals"] == {"journeys": 16, "dated_journeys": 16, "undated_journeys": 0, "projects": 1,
                                "sessions": 7}
    assert report["unreadable"] == {"projects": 0, "sessions": 0, "by_code": {}}
    assert report["by_outcome"] == {"completed": 5, "truncated": 2, "refused": 4, "awaiting_owner": 1,
                                    "outcome_unknown": 1, "timed_out": 1, "step_limit": 1, "stopped": 1}
    assert report["by_mode"] == {"text": 5, "media": 1, "agent": 8, "research": 0, "coder": 1, "translate": 1}
    assert report["cumulative_completion_rate"] == round(5 / 16, 4) and report["cumulative_completion_rate_unavailable_reason"] is None
    assert report["outcome_labels"]["completed"] == "منجزة" and report["outcome_labels"]["refused"] == "مرفوضة"
    seen = sorted((j["mode"], j["status"], j["outcome"], j["error_code"], j["steps"], j["tool_calls"])
                  for j in report["journeys"])
    assert seen == sorted([
        ("text", "complete", "completed", None, 1, 0),
        ("text", "truncated", "truncated", "output_truncated", 1, 0),
        ("text", "error", "refused", "provider_error", 1, 0),
        ("text", "error", "refused", "provider_unavailable", 1, 0),
        ("text", "error", "outcome_unknown", "outcome_uncertain", 0, 0),       # انقطعت قبل أن يُقيَّد نداؤها
        ("agent", "complete", "completed", None, 2, 1),
        ("agent", "refused", "refused", "response_refused", 1, 0),
        ("agent", "timed_out", "timed_out", "response_deadline", 1, 0),
        ("agent", "failed", "refused", "provider_unavailable", 1, 0),
        ("agent", "truncated", "truncated", "response_max_output", 1, 0),
        ("agent", "step_limit", "step_limit", "max_steps_exhausted", 8, 8),
        ("agent", "awaiting_owner", "awaiting_owner", "consent_required", 1, 1),
        ("agent", "cancelled", "stopped", "stop_requested", 1, 1),
        ("coder", "complete", "completed", None, 1, 0),
        ("translate", "complete", "completed", None, 1, 0),
        ("media", "complete", "completed", None, 1, 0),
    ])
    # الخطوةُ نداءٌ قيّده سجلُّ نداءات الجلسة، لا واحدٌ مفترض: الجولةُ التي لا قيدَ لها صفرٌ بدليلٍ مسمًّى
    assert sorted((j["steps_evidence"], j["error_code"]) for j in report["journeys"] if j["steps"] == 0) == \
        [("none", "outcome_uncertain")]
    assert {j["steps_evidence"] for j in report["journeys"] if j["steps"]} == {"call_ledger"}
    assert {(j["duration_s"], j["duration_unknown_reason"]) for j in report["journeys"]} == \
        {(None, "turn_timestamps_not_stored")}
    assert set(report["measurement_limits"]) == set(journeys.MEASUREMENT_LIMITS)
    assert report["schema_version"] == 1 and report["tool"] == "tools/journeys.py" and report["root"] == "custom"


def test_the_data_root_is_left_byte_for_byte_unchanged(copy, tmp_path, capsys):
    def snapshot():
        return {path.relative_to(copy).as_posix(): (path.is_dir(), None if path.is_dir() else path.read_bytes(),
                                                     path.stat().st_mtime_ns, path.stat().st_mode)
                for path in sorted(copy.rglob("*"))}
    before = snapshot()
    report_of(copy, tmp_path, capsys)
    assert snapshot() == before


def test_an_unknown_or_missing_status_is_counted_by_name_never_dropped(copy, tmp_path, capsys):
    (_, state, _), = [item for item in session_files(copy) if item[2] == "text"]

    def change(value):
        value["turns"][1]["result"]["status"] = "new_status_code"
        value["turns"][2]["result"]["status"] = "حالةٌ بنصٍّ حرّ"
        value["turns"][3]["result"] = None
    rewrite_state(state, change)
    report = report_of(copy, tmp_path, capsys)
    assert report["totals"]["journeys"] == 16
    text = sorted((j["status"], j["outcome"]) for j in report["journeys"] if j["mode"] == "text")
    assert text == [("complete", "completed"), ("error", "outcome_unknown"), ("new_status_code", "new_status_code"),
                    ("pending", "outcome_unknown"), ("unknown", "unknown")]
    assert report["by_outcome"]["new_status_code"] == 1 and report["by_outcome"]["unknown"] == 1
    assert report["by_outcome"]["outcome_unknown"] == 2
    assert "بنصٍّ" not in json.dumps(report, ensure_ascii=False)


def test_an_unreadable_or_corrupt_session_is_counted_by_code_and_the_report_is_still_written(copy, tmp_path, capsys):
    files = session_files(copy)
    (_, text_state, _), = [item for item in files if item[2] == "text"]
    (meta, media_state, _), = [item for item in files if item[2] == "media"]
    (coder_meta, _, _), = [item for item in files if item[2] == "coder"]
    # وضعٌ لا تعرفه الأداة (أضافه منتجٌ أحدث): الجلسةُ تُعدّ باسم رمزها ولا تُقرأ بطريق غيرها
    coder_meta.write_text(json.dumps({"id": coder_meta.parent.name, "name": "x", "mode": "future_mode"}),
                          encoding="utf-8")
    # بصمةُ الغلاف لا تطابق: جولةٌ «مبتورة» صارت «منجزة» بلا إعادة ختم
    rewrite_state(text_state, lambda value: value["turns"][1]["result"].update(status="complete"), reseal=False)
    # جلسةٌ بوصلةٍ رمزية إلى نسخةٍ خارج المخزن، باسمٍ ومعرّفٍ صالحين
    media = meta.parent
    outside = tmp_path / "outside"
    shutil.copytree(media, outside)
    alias = uuid.uuid4().hex
    (outside / "chat" / media.name).rename(outside / "chat" / alias)
    (outside / "meta.json").write_text(json.dumps({"id": alias, "name": "x", "mode": "media"}), encoding="utf-8")
    (media.parent / alias).symlink_to(outside, target_is_directory=True)
    (media.parent / "not-an-identifier").mkdir()
    media_state.unlink()
    report = report_of(copy, tmp_path, capsys, *WINDOW)
    assert report["unreadable"] == {"projects": 0, "sessions": 5,
                                    "by_code": {"entry_invalid": 1, "mode_unknown": 1, "state_corrupt": 1,
                                                "state_missing": 1, "unsafe_path": 1}}
    assert report["totals"]["sessions"] == 9
    assert report["totals"]["journeys"] == 16 - 5 - 1 - 1
    assert report["by_mode"]["text"] == 0 and report["by_mode"]["media"] == 0 and report["by_mode"]["coder"] == 0
    # رحلاتُ الجلسات الغائبة قد تكون المتعثّرة: لا نسبةَ نظيفةَ المظهر فوق الباقي، ولا خطَّ أساس
    assert (report["cumulative_completion_rate"], report["cumulative_completion_rate_unavailable_reason"]) == (None, "unreadable_entries")
    cohort = report["baseline"]["cohort"]
    assert cohort["journeys"] == 9 and (cohort["completion_rate"], cohort["completion_rate_unavailable_reason"]) == \
        (None, "unreadable_entries")
    assert report["baseline_ready"] is False
    assert report["baseline"]["missing"] == ["unreadable_entries", "too_few_dated_journeys", "too_few_distinct_dates"]


def test_steps_need_the_call_ledger_and_an_unreadable_ledger_is_named_not_zero(copy, tmp_path, capsys):
    files = session_files(copy)
    (_, text_state, _), = [item for item in files if item[2] == "text"]
    (_, coder_state, _), = [item for item in files if item[2] == "coder"]
    # قيدٌ عُدّل بلا إعادة بصمته في سجلّ الجلسة النصّية: السلسلةُ انكسرت فالخطواتُ مجهولةٌ باسمها
    ledger = text_state.parent / "calls.jsonl"
    lines = ledger.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[0])
    entry["record"]["model"] = "tampered"
    ledger.write_text("\n".join([json.dumps(entry), *lines[1:]]) + "\n", encoding="utf-8")
    # وسجلُّ جلسة المبرمج غائب
    (coder_state.parent / "calls.jsonl").unlink()
    report = report_of(copy, tmp_path, capsys)
    unknown = sorted((j["mode"], j["status"], j["steps"], j["steps_evidence"]) for j in report["journeys"]
                     if j["mode"] in ("text", "coder"))
    assert unknown == sorted([("text", status, None, "ledger_unreadable")
                              for status in ("complete", "truncated", "error", "error", "error")]
                             + [("coder", "complete", None, "ledger_unreadable")])
    # الجلسةُ نفسُها مقروءة ونتائجُها باقية؛ والجلساتُ الأخرى بخطواتها
    assert report["unreadable"]["sessions"] == 0 and report["totals"]["journeys"] == 16
    assert {j["steps_evidence"] for j in report["journeys"] if j["mode"] not in ("text", "coder")} == {"call_ledger"}


def test_a_missing_root_an_unsafe_root_and_an_existing_output_are_refused_by_name(world, tmp_path, capsys):
    out = tmp_path / "report.json"
    assert run(tmp_path / "absent", out, capsys) == (2, {"status": "refused", "code": "root_missing"})
    assert not out.exists()
    link = tmp_path / "linked-root"
    link.symlink_to(world.root, target_is_directory=True)
    assert run(link, out, capsys) == (2, {"status": "refused", "code": "root_unsafe"})
    assert not out.exists()
    out.write_bytes(b"owner's earlier report")
    assert run(world.root, out, capsys) == (2, {"status": "refused", "code": "output_exists"})
    assert out.read_bytes() == b"owner's earlier report"
    # مسارُ الخرج تحت ملفٍّ لا دليل: رفضٌ مسمًّى لا استثناءٌ خام
    assert run(world.root, out / "report.json", capsys) == (2, {"status": "refused", "code": "output_unwritable"})


def test_an_unlistable_projects_root_is_refused_as_root_unreadable(world, tmp_path, capsys, monkeypatch):
    out = tmp_path / "report.json"

    def deny_listing(_):
        raise PermissionError("denied")

    monkeypatch.setattr(journeys.os, "listdir", deny_listing)
    assert run(world.root, out, capsys) == (2, {"status": "refused", "code": "root_unreadable"})
    assert not out.exists()


def test_the_date_is_the_session_day_only_when_the_session_stayed_within_one_utc_day(copy, tmp_path, capsys):
    files = {mode: (meta, state) for meta, state, mode in session_files(copy) if mode != "agent"}
    meta, state = files["text"]
    os.utime(meta, (DAY_ONE + DAY, DAY_ONE + DAY))
    os.utime(state, (DAY_ONE + DAY + 60, DAY_ONE + DAY + 60))       # يومٌ ثانٍ كاملًا: خمسُ رحلاتٍ مؤرَّخةٌ به
    meta, state = files["coder"]
    os.utime(state, (DAY_ONE + DAY + 60, DAY_ONE + DAY + 60))       # أُنشئت يومًا وكُتبت في غيره: بلا تاريخ
    meta, state = files["media"]
    os.utime(meta, (DAY_ONE + 600, DAY_ONE + 600))                  # آخرُ كتابةٍ قبل الإنشاء: بلا تاريخ
    report = report_of(copy, tmp_path, capsys)
    assert report["by_date"] == {"2026-10-01": 9, "2026-10-02": 5} and report["distinct_dates"] == 2
    assert report["totals"]["dated_journeys"] == 14 and report["totals"]["undated_journeys"] == 2
    reasons = {j["mode"]: (j["date"], j["date_basis"], j["date_unknown_reason"]) for j in report["journeys"]}
    assert reasons["text"] == ("2026-10-02", "session_single_day", None)
    assert reasons["coder"] == (None, None, "session_spans_days")
    assert reasons["media"] == (None, None, "timestamps_inconsistent")


@pytest.fixture(scope="module")
def thirty(tmp_path_factory):
    """ثلاثون رحلةً نصّية منجزة في ثلاث جلسات: ١٥ و١٤ و١."""
    root = tmp_path_factory.mktemp("baseline").resolve() / "ui"
    provider = Provider()
    app = LocalApp(root, model="fixture", model_version=VERSION, provider_factory=lambda: provider)
    try:
        project = app.dispatch({"action": "create_project", "name": "م"})["id"]
        for count in (15, 14, 1):
            session = app.dispatch({"action": "create_session", "project": project, "name": "ج", "mode": "text"})["id"]
            for _ in range(count):
                app.dispatch({"action": "ask", "project": project, "session": session, "turn": uuid.uuid4().hex,
                              "message": "سؤال", "files": []})
    finally:
        app.close()
    return root


def test_baseline_ready_needs_thirty_dated_journeys_on_two_dates_inside_the_m1_window(thirty, tmp_path, capsys):
    root = tmp_path / "ui"
    shutil.copytree(thirty, root)
    one, fourteen, fifteen = sorted(session_files(root),
                                    key=lambda item: len(json.loads(item[1].read_text())["state"]["turns"]))

    def place(meta, state, created, last):
        os.utime(meta, (created, created))
        os.utime(state, (last, last))

    def baseline(*window):
        report = report_of(root, tmp_path, capsys, *(window or WINDOW))
        return report, report["baseline"]["cohort"], report["baseline"]["missing"]

    for meta, state, _ in (one, fourteen, fifteen):
        place(meta, state, DAY_ONE, DAY_ONE + 60)
    report, cohort, missing = baseline()
    assert (cohort["journeys"], cohort["distinct_dates"]) == (30, 1)
    assert report["baseline_ready"] is False and missing == ["too_few_distinct_dates"]
    place(*fourteen[:2], DAY_ONE + DAY, DAY_ONE + DAY + 60)
    place(*one[:2], DAY_ONE + DAY, DAY_ONE + DAY + 60)
    report, cohort, missing = baseline()
    assert cohort["by_date"] == {"2026-10-01": 15, "2026-10-02": 15} and cohort["undated_journeys_before_cut"] == 0
    assert report["baseline_ready"] is True and missing == []
    assert cohort["completion_rate"] == 1.0 and report["cumulative_completion_rate"] == 1.0
    assert report["baseline"]["window"] == {"from": "2026-10-01", "from_source": "option", "until": "2026-10-31",
                                            "until_source": "option"}
    # رحلةٌ قبل النافذة (م٠-ب) تُعدّ في المجاميع التراكمية لا في الفوج: «أولُ ٣٠ رحلة في م١»
    place(*one[:2], DAY_ONE - 5 * DAY, DAY_ONE - 5 * DAY + 60)
    report, cohort, missing = baseline()
    assert (report["totals"]["dated_journeys"], report["distinct_dates"]) == (30, 3)
    assert cohort["journeys"] == 29 and report["baseline_ready"] is False and missing == ["too_few_dated_journeys"]
    # وما بعد النافذة كذلك: نافذةٌ تنتهي في اليوم الأول لا تعدّ رحلاتِ اليوم الثاني
    report, cohort, missing = baseline("--baseline-from", "2026-09-01", "--baseline-until", "2026-10-01")
    assert cohort["by_date"] == {"2026-09-26": 1, "2026-10-01": 15} and missing == ["too_few_dated_journeys"]
    # رحلةٌ بلا تاريخ قد تقع في النافذة: الفوجُ ناقصٌ ونسبتُه مجهولة، وإن بقيت المجاميعُ التراكمية كاملة
    place(*one[:2], DAY_ONE, DAY_ONE + DAY)
    report, cohort, missing = baseline()
    assert cohort["undated_journeys_before_cut"] == 1 and missing == ["undated_journeys_before_cut", "too_few_dated_journeys"]
    assert (cohort["completion_rate"], cohort["completion_rate_unavailable_reason"]) == \
        (None, "undated_journeys_before_cut")
    assert report["cumulative_completion_rate"] == 1.0
    # ورحلةٌ بلا تاريخ امتدّت جلستُها يومين قبل النافذة كلِّها لا تحجبها
    place(*one[:2], DAY_ONE - 5 * DAY, DAY_ONE - 4 * DAY)
    report, cohort, missing = baseline()
    assert cohort["undated_journeys_before_cut"] == 0 and missing == ["too_few_dated_journeys"]
    # مشروعٌ لا يُقرأ يحجب خطَّ الأساس ولو بلغ الفوجُ حدَّه، ويجعل النسبتين مجهولتين
    place(*one[:2], DAY_ONE + DAY, DAY_ONE + DAY + 60)
    (root / "projects" / "not-an-identifier").mkdir()
    report, cohort, missing = baseline()
    assert report["unreadable"]["projects"] == 1 and cohort["journeys"] == 30 and cohort["distinct_dates"] == 2
    assert report["baseline_ready"] is False and missing == ["unreadable_entries"]
    assert report["cumulative_completion_rate"] is None and cohort["completion_rate"] is None


def text_store(root: Path, plan: dict) -> tuple[Path, str, dict]:
    """مخزنٌ بجلساتٍ نصّية بأدوارها، ولكل جولةٍ نتيجتُها المكتوبة سلفًا: منجزة أو مبتورة أو معطوبة."""
    provider = Provider()
    app = LocalApp(root, model="fixture", model_version=VERSION, provider_factory=lambda: provider)
    reply = {"complete": lambda: respond("تم."), "truncated": lambda: respond("تم.", stop="max_output"),
             "error": lambda: respond("", stop="error")}
    roles = {}
    try:
        project = app.dispatch({"action": "create_project", "name": "م"})["id"]
        for role, outcomes in plan.items():
            roles[role] = app.dispatch({"action": "create_session", "project": project, "name": role,
                                        "mode": "text"})["id"]
            for outcome in outcomes:
                provider.responses.append(reply[outcome]())
                result = app.dispatch({"action": "ask", "project": project, "session": roles[role],
                                       "turn": uuid.uuid4().hex, "message": "سؤال", "files": []})
                assert result["status"] == outcome
    finally:
        app.close()
    return root, project, roles


@pytest.fixture(scope="module")
def cohort_store(tmp_path_factory):
    """جلساتٌ نصّية بأدوارٍ يضع الاختبارُ أزمنتَها: A ١٢ (١٠ منجزة ثم ٢ مبتورة)، وB ١٢ منجزة، وC ٦ (٥ منجزة ثم مبتورة)،
    وD ٥ مبتورة، وE ٣ معطوبة، وF وG رحلةٌ منجزة لكلٍّ."""
    return text_store(tmp_path_factory.mktemp("cohort").resolve() / "ui",
                      {"A": ["complete"] * 10 + ["truncated"] * 2, "B": ["complete"] * 12,
                       "C": ["complete"] * 5 + ["truncated"], "D": ["truncated"] * 5, "E": ["error"] * 3,
                       "F": ["complete"], "G": ["complete"]})


HOUR = 3600
# اليومُ الأول: A ثم B؛ والثاني: C ثم D؛ والثالث: E؛ وF امتدّت من الثالث إلى الرابع، وG من الأول إلى الثاني
COHORT_TIMES = {"A": (DAY_ONE, DAY_ONE + HOUR / 2), "B": (DAY_ONE + HOUR, DAY_ONE + 1.5 * HOUR),
                "C": (DAY_ONE + DAY - HOUR, DAY_ONE + DAY - HOUR / 2), "D": (DAY_ONE + DAY, DAY_ONE + DAY + HOUR / 2),
                "E": (DAY_ONE + 2 * DAY, DAY_ONE + 2 * DAY + HOUR / 2), "F": (DAY_ONE + 2 * DAY + HOUR, DAY_ONE + 3 * DAY),
                "G": (DAY_ONE + 2 * HOUR, DAY_ONE + DAY + 2 * HOUR)}


def placed(store, tmp_path, *, drop=(), times=None, **moved) -> Path:
    """نسخةٌ من المخزن بلا جلسات `drop`، وأزمنةُ كلِّ جلسةٍ من `times` (COHORT_TIMES افتراضًا) أو ممّا مُرِّر لها."""
    times = COHORT_TIMES if times is None else times
    source, project, roles = store
    root = tmp_path / f"cohort-{uuid.uuid4().hex[:6]}"
    shutil.copytree(source, root)
    sessions = root / "projects" / project / "sessions"
    for role, session in roles.items():
        if role in drop:
            shutil.rmtree(sessions / session)
            continue
        created, last = moved.get(role, times[role])
        os.utime(sessions / session / "meta.json", (created, created))
        os.utime(sessions / session / "chat" / session / "state.json", (last, last))
    return root


def members_of(report) -> list[tuple]:
    return sorted((j["baseline_position"], j["date"], j["status"]) for j in report["journeys"]
                  if j["baseline_position"] is not None)


def test_the_baseline_is_exactly_the_first_thirty_and_later_journeys_do_not_move_it(cohort_store, tmp_path, capsys,
                                                                                    monkeypatch):
    # ٣٥ رحلةً في النافذة (A وB في اليوم الأول، وC وD في الثاني): الفوجُ أولُ ثلاثين، A وB وC، لا كلُّ الخمس والثلاثين
    first = report_of(placed(cohort_store, tmp_path, drop="EFG"), tmp_path, capsys, *WINDOW)
    cohort = first["baseline"]["cohort"]
    assert (cohort["journeys"], cohort["window_dated_journeys"], cohort["cut_date"]) == (30, 35, "2026-10-02")
    assert cohort["by_date"] == {"2026-10-01": 24, "2026-10-02": 6} and cohort["cut_order_known"] is True
    assert cohort["by_outcome"]["completed"] == 27 and cohort["by_outcome"]["truncated"] == 3
    assert cohort["completion_rate"] == 0.9 and first["cumulative_completion_rate"] == round(27 / 35, 4)
    assert first["baseline_ready"] is True and first["baseline"]["missing"] == []
    assert [position for position, _, _ in members_of(first)] == list(range(1, 31))
    # ترتيبُ قراءة الدليل لا يغيّر شيئًا
    listed = journeys._entries
    monkeypatch.setattr(journeys, "_entries", lambda fd: list(reversed(listed(fd))))
    assert report_of(placed(cohort_store, tmp_path, drop="EFG"), tmp_path, capsys, *WINDOW) == first
    monkeypatch.undo()
    # رحلاتٌ بعد القطع (E في اليوم الثالث، وF بلا تاريخٍ بين الثالث والرابع) لا تمسّ أعضاءَه ولا نسبتَه
    later = report_of(placed(cohort_store, tmp_path, drop="G"), tmp_path, capsys, *WINDOW)
    assert members_of(later) == members_of(first) and later["baseline"]["cohort"]["completion_rate"] == 0.9
    assert later["baseline"]["cohort"]["window_dated_journeys"] == 38 and later["baseline_ready"] is True
    assert later["baseline"]["cohort"]["undated_journeys_before_cut"] == 0
    # وG بلا تاريخٍ بين اليوم الأول والثاني قد تكون من الثلاثين: لا يُخمَّن موضعُها
    blocked = report_of(placed(cohort_store, tmp_path), tmp_path, capsys, *WINDOW)
    assert blocked["baseline_ready"] is False and blocked["baseline"]["missing"] == ["undated_journeys_before_cut"]
    assert blocked["baseline"]["cohort"]["completion_rate"] is None
    # الفوجُ يتبع زمنَ إنشاء الجلسة لا معرّفَها: D قبل C يومَ القطع فيدخل D كلُّه وأولُ جولةٍ من C (المنجزة)
    early_d = {"D": COHORT_TIMES["C"], "C": COHORT_TIMES["D"]}
    swapped = report_of(placed(cohort_store, tmp_path, drop="EFG", **early_d), tmp_path, capsys, *WINDOW)
    cohort = swapped["baseline"]["cohort"]
    assert cohort["by_outcome"]["completed"] == 23 and cohort["by_outcome"]["truncated"] == 7
    assert cohort["completion_rate"] == round(23 / 30, 4) and swapped["baseline_ready"] is True
    assert members_of(swapped)[-1] == (30, "2026-10-02", "complete")
    # وقطعٌ بين جلستين تداخلت كتابتُهما في يومه لا يُثبت ترتيبُه: D كُتبت بعد أن أُنشئت C
    overlap = {"D": (DAY_ONE + DAY - HOUR, DAY_ONE + DAY + HOUR / 4), "C": COHORT_TIMES["D"]}
    unknown = report_of(placed(cohort_store, tmp_path, drop="EFG", **overlap), tmp_path, capsys, *WINDOW)
    assert unknown["baseline_ready"] is False and unknown["baseline"]["missing"] == ["baseline_cut_order_unknown"]
    assert unknown["baseline"]["cohort"]["cut_order_known"] is False
    assert unknown["baseline"]["cohort"]["completion_rate"] is None


def test_a_timestamp_tie_at_the_cut_is_unprovable_and_a_tie_on_one_side_of_it_is_not(cohort_store, tmp_path, capsys):
    """أزمنةٌ متساوية (نسخٌ أو استعادة، أو دقّةٌ خشنة) لا تُثبت ترتيبًا: عبر القطع تحجبه، وفي جهةٍ واحدة منه لا تمسّه."""
    tie = DAY_ONE + DAY - HOUR
    # C وD بزمنٍ واحد يومَ القطع والقطعُ بينهما: أيُّهما سبق لا يُعرف، ولا يحسمه المعرّفُ العشوائي
    across = report_of(placed(cohort_store, tmp_path, drop="EFG", C=(tie, tie), D=(tie, tie)), tmp_path, capsys,
                       *WINDOW)
    assert across["baseline_ready"] is False and across["baseline"]["missing"] == ["baseline_cut_order_unknown"]
    assert across["baseline"]["cohort"]["completion_rate"] is None
    # B وC بزمنٍ واحد وكلتاهما داخل الثلاثين، وD بعدهما: الفوجُ هو هو أيًّا سبق
    inside = report_of(placed(cohort_store, tmp_path, drop="EFG", B=(tie, tie), C=(tie, tie)), tmp_path, capsys,
                       *WINDOW)
    cohort = inside["baseline"]["cohort"]
    assert inside["baseline_ready"] is True and cohort["by_date"] == {"2026-10-01": 12, "2026-10-02": 18}
    assert cohort["completion_rate"] == 0.9
    # D وE بزمنٍ واحد وكلتاهما بعد القطع: لا تمسّانه
    later = DAY_ONE + DAY
    after = report_of(placed(cohort_store, tmp_path, drop="FG", D=(later, later), E=(later, later)), tmp_path, capsys,
                      *WINDOW)
    assert after["baseline_ready"] is True and after["baseline"]["cohort"]["window_dated_journeys"] == 38
    assert after["baseline"]["cohort"]["completion_rate"] == 0.9


@pytest.fixture(scope="module")
def m2_store(tmp_path_factory):
    """P عشرون معطوبةً قبل م١؛ وQ1 خمس عشرة منجزة وQ2 ستٌّ منجزة وتسعٌ مبتورة (الثلاثون الأولى: ٠٫٧)؛ وR1 وR2 عشرٌ
    منجزةٌ لكلٍّ (أولُ عشرين في م٢: ١٫٠)؛ وS خمسٌ معطوبةٌ بعدها. فالنسبةُ التراكمية ٤١/٧٥ أدنى من الفوجين كليهما."""
    return text_store(tmp_path_factory.mktemp("m2").resolve() / "ui",
                      {"P": ["error"] * 20, "Q1": ["complete"] * 15, "Q2": ["complete"] * 6 + ["truncated"] * 9,
                       "R1": ["complete"] * 10, "R2": ["complete"] * 10, "S": ["error"] * 5})


PLAN_PHASES = {phase["id"]: phase
               for phase in json.loads((ROOT / "docs" / "PLAN-20260926.json").read_text(encoding="utf-8"))["phases"]}
# أولُ يومٍ في نافذة م٢ كما تسجّله الخطة، الساعةَ العاشرة بتوقيت UTC
M2_DAY = datetime.strptime(PLAN_PHASES["م٢"]["start"], "%Y-%m-%d").replace(hour=10, tzinfo=timezone.utc).timestamp()
M2_END = datetime.strptime(PLAN_PHASES["م٢"]["end"], "%Y-%m-%d").replace(hour=10, tzinfo=timezone.utc).timestamp()
# P قبل م١؛ وQ1 وQ2 في نافذة خطّ الأساس (أكتوبر)؛ وR1 وR2 وS في أول أيام م٢ تباعًا
M2_TIMES = {"P": (DAY_ONE - 5 * DAY, DAY_ONE - 5 * DAY + HOUR / 2), "Q1": (DAY_ONE, DAY_ONE + HOUR / 2),
            "Q2": (DAY_ONE + DAY, DAY_ONE + DAY + HOUR / 2),
            **{role: (M2_DAY + day * DAY, M2_DAY + day * DAY + HOUR / 2) for day, role in enumerate(("R1", "R2", "S"))}}


def day_of(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()


def compared_of(report) -> list[tuple]:
    return sorted((j["comparison_position"], j["date"], j["status"]) for j in report["journeys"]
                  if j["comparison_position"] is not None)


def test_the_m2_gate_compares_the_first_twenty_of_m2_with_the_baseline_not_the_cumulative_rate(m2_store, tmp_path, capsys,
                                                                                               monkeypatch):
    def m2(*window, **options):
        report = report_of(placed(m2_store, tmp_path, times=M2_TIMES, **options), tmp_path, capsys,
                           *(window or WINDOW))
        return report, report["m2_gate"], report["m2_gate"]["comparison"]

    full, gate, comparison = m2()
    assert (gate["source"], gate["baseline_journeys"], gate["target_journeys"], gate["required_points"],
            gate["comparison_size"]) == ("plan_m2_gate", 30, 50, 10, 20)
    assert gate["window"] == {"from": PLAN_PHASES["م٢"]["start"], "until": PLAN_PHASES["م٢"]["end"]}
    assert full["baseline_ready"] is True and full["baseline"]["cohort"]["completion_rate"] == 0.7
    assert (comparison["journeys"], comparison["window_dated_journeys"], gate["m1_after_baseline"]) == (20, 25, 0)
    assert comparison["by_date"] == {day_of(M2_DAY): 10, day_of(M2_DAY + DAY): 10} and comparison["completion_rate"] == 1.0
    assert [position for position, _, _ in compared_of(full)] == list(range(1, 21))
    # البوابةُ تقرأ نسبةَ الفوجين لا التراكمية (٤١/٧٥ ≈ ٠٫٥٥ تُسقطها لو قُرئت)
    assert full["cumulative_completion_rate"] == round(41 / 75, 4)
    assert (gate["baseline_completion_rate"], gate["comparison_completion_rate"], gate["improvement_points"]) == \
        (0.7, 1.0, 30.0)
    assert gate["passed"] is True and gate["missing"] == []
    # ما قبل م١ لا يحرّك المقارنة: بلا P تبقى هي هي وتتغيّر التراكميةُ وحدها
    without, gate_without, _ = m2(drop=("P",))
    assert compared_of(without) == compared_of(full) and gate_without["comparison"] == comparison
    assert gate_without["passed"] is True and without["cumulative_completion_rate"] == round(41 / 55, 4)
    # الحكمُ بنقاط الخطة: خمسُ المعطوبة داخل العشرين (S قبل R2) تجعل المقارنةَ ١٥/٢٠، أي +٥ نقاط لا +١٠
    _, short_gate, short = m2(S=(M2_DAY + DAY - HOUR, M2_DAY + DAY - HOUR / 2))
    assert short["completion_rate"] == 0.75 and short_gate["improvement_points"] == 5.0
    assert short_gate["passed"] is False and short_gate["missing"] == []
    # فوجٌ ناقص (بلا R2: خمس عشرة في م٢) لا نسبةَ له ولا حكم
    _, partial_gate, partial = m2(drop=("R2",))
    assert partial["journeys"] == 15 and partial_gate["missing"] == ["too_few_comparison_journeys"]
    assert (partial["completion_rate"], partial["completion_rate_unavailable_reason"]) == \
        (None, "too_few_comparison_journeys")
    assert partial_gate["passed"] is None and partial_gate["comparison_completion_rate"] is None
    # خمسون كلُّها في م١: لا شيءَ منها يقارَن، والعشرون بعد الثلاثين تُعدّ في m1_after_baseline
    in_m1 = {"R1": (DAY_ONE + 2 * DAY, DAY_ONE + 2 * DAY + HOUR / 2), "R2": (DAY_ONE + 3 * DAY, DAY_ONE + 3 * DAY + HOUR / 2)}
    _, m1_gate, m1 = m2(drop=("S",), **in_m1)
    assert (m1["journeys"], m1_gate["m1_after_baseline"]) == (0, 20)
    assert m1_gate["missing"] == ["too_few_comparison_journeys"] and m1_gate["passed"] is None
    # وما بعد نهاية م٢ لا يدخل: R2 بعد النافذة فتبقى R1 وS
    _, late_gate, late = m2(R2=(M2_END + DAY, M2_END + DAY + HOUR / 2))
    assert (late["journeys"], late["window_dated_journeys"]) == (15, 15)
    assert late_gate["missing"] == ["too_few_comparison_journeys"]
    # قطعُ العشرين بين جلستين بزمنٍ واحد لا يُثبت ترتيبُه
    _, tied_gate, tied = m2(S=M2_TIMES["R2"])
    assert tied_gate["missing"] == ["comparison_cut_order_unknown"] and tied["cut_order_known"] is False
    assert tied_gate["passed"] is None
    # ولا مقارنةَ بخطّ أساسٍ لم يجهز (نافذةٌ من يومٍ واحد لا تبلغ الثلاثين)، ولا بنافذةٍ تداخل م٢
    report, not_ready, _ = m2("--baseline-from", "2026-10-01", "--baseline-until", "2026-10-01")
    assert report["baseline_ready"] is False and not_ready["missing"] == ["baseline_not_ready"]
    assert not_ready["passed"] is None and not_ready["comparison"]["completion_rate"] is None
    _, overlap, _ = m2("--baseline-from", "2026-10-01", "--baseline-until", PLAN_PHASES["م٢"]["start"])
    assert overlap["missing"] == ["baseline_window_overlaps_m2"] and overlap["passed"] is None
    # وخطةٌ بلا نافذة م٢ لا تُخترع لها نافذة
    plan = json.loads((ROOT / "docs" / "PLAN-20260926.json").read_text(encoding="utf-8"))
    for phase in plan["phases"]:
        if phase["id"] == "م٢":
            del phase["start"]
    (tmp_path / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(journeys, "PLAN", tmp_path / "plan.json")
    _, unknown, _ = m2()
    assert unknown["source"] is None and unknown["window"] is None and unknown["comparison"] is None
    assert unknown["missing"] == ["comparison_gate_unknown"] and unknown["passed"] is None


def published(store, probe: Path, tmp_path: Path, capsys, name: str, *options, **placement):
    """تشغيلٌ ينشر في docs/probe (المؤقّت): التقريرُ إن كُتب، وإلا (رمزُ الخروج، والسطرُ المطبوع)."""
    out = probe / f"journeys-{name}.json"
    code, printed = run(placed(store, tmp_path, times=M2_TIMES, **placement), out, capsys, *options)
    return (code, printed) if code else json.loads(out.read_text(encoding="utf-8"))


def test_a_published_baseline_is_frozen_and_later_runs_compare_against_it(m2_store, probe, tmp_path, capsys):
    frozen_file = probe / "journeys-baseline.json"

    def publish(name, *options, **placement):
        return published(m2_store, probe, tmp_path, capsys, name, *options, **placement)

    def frozen_digest():
        return hashlib.sha256(frozen_file.read_bytes()).hexdigest()

    # نافذةٌ مرّرها المالك (أكتوبر) فجهز خطُّ الأساس ونُشر: جُمِّد في journeys-baseline.json بنافذته ومصدرها
    first = publish("r1", *WINDOW)
    assert first["baseline_ready"] is True and first["baseline"]["frozen"]["state"] == "frozen_now"
    artifact = json.loads(frozen_file.read_text(encoding="utf-8"))
    assert artifact["window"] == {"from": "2026-10-01", "from_source": "option", "until": "2026-10-31",
                                  "until_source": "option"}
    assert artifact["cohort"]["completion_rate"] == 0.7 and artifact["cohort"]["journeys"] == 30
    assert first["baseline"]["frozen"]["digest"] == frozen_digest() and artifact["history"] == []
    assert artifact["members_digest"] not in json.dumps(first) and not any(
        session in frozen_file.read_text(encoding="utf-8") for session in m2_store[2].values())
    # تشغيلٌ أسبوعيٌّ عاديّ بلا خيار: نافذةُ الخطة (١٢ أكتوبر) كانت ستُفرغ خطَّ الأساس؛ المجمَّدُ يحكم
    second = publish("r2")
    frozen = second["baseline"]["frozen"]
    assert (frozen["state"], frozen["digest"], frozen["drift"]) == ("loaded", first["baseline"]["frozen"]["digest"], [])
    assert second["baseline"]["window"] == artifact["window"] and second["baseline_ready"] is True
    assert second["m2_gate"]["baseline_completion_rate"] == 0.7 and second["m2_gate"]["passed"] is True
    # بياناتُ اليوم تغيّرت (Q2 غابت): الفرقُ يُعلَن باسمه ولا يحلّ محلَّ المجمَّد
    drifted = publish("r3", drop=("Q2",))
    assert drifted["baseline"]["frozen"]["drift"] == ["baseline_members_changed", "baseline_outcomes_changed",
                                                      "baseline_not_ready_now"]
    assert "too_few_dated_journeys" in drifted["baseline"]["frozen"]["recomputed_missing"]
    assert drifted["baseline"]["cohort"]["completion_rate"] == 0.7 and drifted["m2_gate"]["passed"] is True
    # تغييرُ المجمَّد يحتاج --refreeze-baseline برمز سببٍ ونشرًا في docs/probe
    assert publish("r4", "--baseline-from", "2026-09-01", "--baseline-until", "2026-10-31") == \
        (2, {"status": "refused", "code": "baseline_already_frozen"})
    assert publish("r4", *WINDOW, "--refreeze-baseline", "سببٌ حرّ") == \
        (2, {"status": "refused", "code": "refreeze_reason_invalid"})
    assert run(placed(m2_store, tmp_path, times=M2_TIMES), tmp_path / "dry.json", capsys, *WINDOW,
               "--refreeze-baseline", "owner_restart") == (2, {"status": "refused", "code": "refreeze_requires_probe_output"})
    assert not (probe / "journeys-r4.json").exists() and not (tmp_path / "dry.json").exists()
    old = frozen_digest()
    refrozen = publish("r5", "--baseline-from", "2026-09-01", "--baseline-until", "2026-10-31",
                       "--refreeze-baseline", "owner_restart")
    assert refrozen["baseline"]["frozen"]["state"] == "refrozen" and frozen_digest() != old
    artifact = json.loads(frozen_file.read_text(encoding="utf-8"))
    assert artifact["refreeze_reason"] == "owner_restart" and [item["digest"] for item in artifact["history"]] == [old]
    assert artifact["cohort"]["completion_rate"] == round(10 / 30, 4)
    later = publish("r6")
    assert later["baseline"]["window"]["from"] == "2026-09-01" and later["m2_gate"]["improvement_points"] == 66.67
    # مجمَّدٌ أُعيدت كتابتُه (المحتوى نفسُه بصياغةٍ أخرى) لا تعرف بصمتَه التقاريرُ المنشورة: رفضٌ مسمًّى
    frozen_file.write_text(json.dumps(json.loads(frozen_file.read_text(encoding="utf-8"))), encoding="utf-8")
    assert publish("r7") == (2, {"status": "refused", "code": "frozen_baseline_changed"})
    # ومجمَّدٌ ضاع بعد أن نُشر: رفضٌ مسمًّى لا إعادةُ حساب، ولا يُعاد تجميدُه إلا بسبب
    frozen_file.unlink()
    assert publish("r7") == (2, {"status": "refused", "code": "frozen_baseline_lost"})
    restored = publish("r8", *WINDOW, "--refreeze-baseline", "restored_after_loss")
    assert restored["baseline"]["frozen"]["state"] == "frozen_now"
    assert {item["digest"] for item in json.loads(frozen_file.read_text(encoding="utf-8"))["history"]} == \
        {old, refrozen["baseline"]["frozen"]["digest"]}
    assert publish("r9")["baseline"]["frozen"]["state"] == "loaded"


@pytest.mark.parametrize("damage", [
    ("history", [{}]), ("history", [{"digest": 5, "frozen_on": None, "reason": None}]), ("history", "x"),
    ("history", [{"digest": "abc", "frozen_on": None, "reason": None}]), ("members_digest", "abc"),
    # فوجٌ متّسقُ الأعداد لكنه ليس ثلاثين: عددُ خطّ الأساس وحده يرفضه
    lambda cohort: cohort.update(journeys=29, by_outcome={**cohort["by_outcome"], "truncated": 8},
                                 by_date={"2026-10-01": 15, "2026-10-02": 14}, completion_rate=round(21 / 29, 4)),
    ("window", {"from": "2026-02-30"}), ("refreeze_reason", "سببٌ حرّ"),
    # أعدادٌ صحيحةُ النوع متناقضةٌ فيما بينها (الفوج: ٢١ منجزة و٩ مبتورة، ١٥ في كلٍّ من يومين، ٠٫٧)
    lambda cohort: cohort.update(by_outcome={**cohort["by_outcome"], "completed": 0, "truncated": 30},
                                 completion_rate=1.0),
    lambda cohort: cohort.update(completion_rate=1.0),
    lambda cohort: cohort.update(by_outcome={**cohort["by_outcome"], "truncated": 0}),
    lambda cohort: cohort.update(by_date={"2026-10-01": 15, "2026-10-02": 10}),
    lambda cohort: cohort.update(distinct_dates=3),
], ids=["empty-entry", "numeric-digest", "not-a-list", "short-digest", "short-members-digest", "count-not-thirty",
        "impossible-day", "free-text-reason", "zero-completed-full-rate", "rate-mismatch", "outcomes-not-summing",
        "dates-not-summing", "distinct-dates-mismatch"])
def test_a_damaged_frozen_baseline_is_refused_by_name_and_a_refreeze_repairs_it(damage, m2_store, probe, tmp_path,
                                                                                 capsys):
    frozen_file = probe / "journeys-baseline.json"
    assert published(m2_store, probe, tmp_path, capsys, "r1", *WINDOW)["baseline"]["frozen"]["state"] == "frozen_now"
    artifact = json.loads(frozen_file.read_text(encoding="utf-8"))
    if callable(damage):
        damage(artifact["cohort"])
    else:
        field, value = damage
        artifact[field] = {**artifact[field], **value} if isinstance(value, dict) else value
    frozen_file.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    # رفضٌ مسمًّى لا أثرٌ خام، ولو بنافذةٍ صريحة
    for options in ((), WINDOW):
        assert published(m2_store, probe, tmp_path, capsys, "r2", *options) == \
            (2, {"status": "refused", "code": "frozen_baseline_unreadable"})
    # وإعادةُ التجميد بسببٍ تصلحه، وتحفظ بصمةَ ما نُشر قبله
    repaired = published(m2_store, probe, tmp_path, capsys, "r3", *WINDOW, "--refreeze-baseline", "repaired_after_damage")
    assert repaired["baseline"]["frozen"]["state"] == "frozen_now"
    history = json.loads(frozen_file.read_text(encoding="utf-8"))["history"]
    assert [entry["reason"] for entry in history] == ["repaired_after_damage"]
    assert published(m2_store, probe, tmp_path, capsys, "r4")["baseline"]["frozen"]["state"] == "loaded"


@pytest.mark.parametrize("pointer", [["0" * 64], {"digest": "0" * 64}], ids=["list", "dict"])
def test_a_damaged_published_report_is_refused_by_name_and_a_refreeze_acknowledges_it(pointer, m2_store, probe,
                                                                                      tmp_path, capsys):
    frozen_file = probe / "journeys-baseline.json"
    first = published(m2_store, probe, tmp_path, capsys, "r1", *WINDOW)
    old = first["baseline"]["frozen"]["digest"]
    # مؤشّرُ التقرير المنشور ليس بصمةً: رفضٌ مسمًّى لا أثرٌ خام
    first["baseline"]["frozen"]["digest"] = pointer
    damaged = (json.dumps(first, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    (probe / "journeys-r1.json").write_bytes(damaged)
    assert published(m2_store, probe, tmp_path, capsys, "r2") == \
        (2, {"status": "refused", "code": "journeys_report_unreadable"})
    # إعادةُ التجميد بسببٍ تُقرّ بالتقرير التالف ببصمة ملفّه بعينها، ويعود التشغيلُ العاديّ
    refrozen = published(m2_store, probe, tmp_path, capsys, "r2", *WINDOW, "--refreeze-baseline",
                         "acknowledged_damaged_report")
    assert refrozen["baseline"]["frozen"]["state"] == "refrozen"
    artifact = json.loads(frozen_file.read_text(encoding="utf-8"))
    assert artifact["acknowledged_reports"] == [hashlib.sha256(damaged).hexdigest()]
    assert [entry["digest"] for entry in artifact["history"]] == [old]
    assert published(m2_store, probe, tmp_path, capsys, "r3")["baseline"]["frozen"]["state"] == "loaded"


def test_a_published_report_must_carry_a_name_the_scanner_reads(m2_store, probe, tmp_path, capsys):
    """تقريرٌ في docs/probe باسمٍ لا يقرؤه الماسح (أو باسم المجمَّد) يُرفض قبل أن يُكتب شيء: وإلا وُلد مجمَّدٌ لا يرى
    التشغيلُ التالي تقريرَه فيرفضه يتيمًا."""
    store = placed(m2_store, tmp_path, times=M2_TIMES)
    for name in ("report.json", "journeys-baseline.json", "journeys-r1.txt"):
        assert run(store, probe / name, capsys, *WINDOW) == (2, {"status": "refused", "code": "probe_output_name_invalid"})
        assert sorted(path.name for path in probe.iterdir()) == []
    assert published(m2_store, probe, tmp_path, capsys, "r1", *WINDOW)["baseline"]["frozen"]["state"] == "frozen_now"
    assert published(m2_store, probe, tmp_path, capsys, "r2")["baseline"]["frozen"]["state"] == "loaded"


def test_the_report_and_the_frozen_baseline_are_published_together(m2_store, probe, tmp_path, capsys, monkeypatch):
    frozen_file = probe / "journeys-baseline.json"

    def publish(name, *options):
        return published(m2_store, probe, tmp_path, capsys, name, *options)

    def boom(*_):
        raise OSError("injected")

    # التقريرُ لا يُنشر: فلا مجمَّدَ يولد بلا تقريرٍ يشير إليه، ولا مؤقّتَ يبقى
    monkeypatch.setattr(journeys, "_link", boom)
    assert publish("r1", *WINDOW) == (2, {"status": "refused", "code": "output_unwritable"})
    assert sorted(path.name for path in probe.iterdir()) == []
    monkeypatch.undo()
    monkeypatch.setattr(journeys, "PROBE_DIR", probe)
    first = publish("r1", *WINDOW)
    kept = frozen_file.read_bytes()
    assert first["baseline"]["frozen"]["digest"] == hashlib.sha256(kept).hexdigest()
    # إعادةُ تجميدٍ سقط استبدالُها: التقريرُ الجديد يُزال والمجمَّدُ السابق باقٍ كما كان
    monkeypatch.setattr(journeys, "_swap", boom)
    assert publish("r2", "--baseline-from", "2026-09-01", "--baseline-until", "2026-10-31",
                   "--refreeze-baseline", "owner_restart") == (2, {"status": "refused", "code": "baseline_publish_failed"})
    assert frozen_file.read_bytes() == kept
    assert sorted(path.name for path in probe.iterdir()) == ["journeys-baseline.json", "journeys-r1.json"]
    monkeypatch.undo()
    monkeypatch.setattr(journeys, "PROBE_DIR", probe)
    assert publish("r2")["baseline"]["frozen"]["state"] == "loaded"
    # ومجمَّدٌ لا يشير إليه تقريرٌ منشور (يتيم) لا يُحمَّل
    for report in probe.glob("journeys-r*.json"):
        report.unlink()
    assert publish("r3") == (2, {"status": "refused", "code": "frozen_baseline_unpublished"})


def test_the_baseline_window_is_the_m1_phase_the_plan_records_and_is_never_invented(world, tmp_path, capsys,
                                                                                     monkeypatch):
    plan = json.loads((ROOT / "docs" / "PLAN-20260926.json").read_text(encoding="utf-8"))
    m1, = [phase for phase in plan["phases"] if phase["id"] == "م١"]
    assert m1["start"] > "2026-10-01", "رحلاتُ المخزن المبنيّ قبل م١"
    report = report_of(world.root, tmp_path, capsys)
    assert report["baseline"]["window"] == {"from": m1["start"], "from_source": "plan_m1_phase", "until": m1["end"],
                                            "until_source": "plan_m1_phase"}
    assert report["totals"]["dated_journeys"] == 16 and report["baseline"]["cohort"]["journeys"] == 0
    mixed = report_of(world.root, tmp_path, capsys, "--baseline-from", "2026-10-01")
    assert mixed["baseline"]["window"] == {"from": "2026-10-01", "from_source": "option", "until": m1["end"],
                                           "until_source": "plan_m1_phase"}
    assert mixed["baseline"]["cohort"]["journeys"] == 16
    monkeypatch.setattr(journeys, "PLAN", tmp_path / "no-plan.json")
    unknown = report_of(world.root, tmp_path, capsys)
    assert unknown["baseline"]["window"] == {"from": None, "from_source": None, "until": None, "until_source": None}
    assert unknown["baseline"]["cohort"] is None and unknown["baseline_ready"] is False
    assert unknown["baseline"]["missing"] == ["baseline_window_unknown", "too_few_dated_journeys",
                                              "too_few_distinct_dates"]
    # وبوابةُ م٢ كذلك: عددُها ونقاطُها من الخطة، فإن غابت فلا بوابة
    assert report["m2_gate"]["source"] == "plan_m2_gate" and report["m2_gate"]["target_journeys"] == 50
    assert unknown["m2_gate"]["source"] is None and unknown["m2_gate"]["comparison"] is None
    assert unknown["m2_gate"]["missing"] == ["comparison_gate_unknown", "baseline_window_unknown"]
    out = tmp_path / "refused.json"
    assert run(world.root, out, capsys, "--baseline-from", "2026-13-01", "--baseline-until", "2026-12-31") == \
        (2, {"status": "refused", "code": "baseline_date_invalid"})
    assert run(world.root, out, capsys, "--baseline-from", "2026-10-31", "--baseline-until", "2026-10-01") == \
        (2, {"status": "refused", "code": "baseline_window_invalid"})
    assert not out.exists()


def test_the_self_check_refuses_text_and_identifiers(world, tmp_path, capsys):
    out = tmp_path / "report.json"
    assert run(world.root, out, capsys)[0] == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    journeys.check_report(report)
    leaks = [
        ("journeys", 0, "status", SECRETS["user"]),
        ("journeys", 0, "error_code", "f" + uuid.uuid4().hex),         # يطابق شكلَ الرمز، والتسلسلُ يفضحه
        ("journeys", 0, "mode", "a" * 16 + "_" + uuid.uuid4().hex[:12]),
        ("by_mode", None, SECRETS["session"], 1),
        ("by_date", None, str(world.root), 1),
        ("journeys", 0, "duration_s", [SECRETS["answer"]]),
        # مفتاحٌ لا يبنيه المخطّط ولو كانت قيمتُه بشكل رمز آلة: المخطّطُ المغلق وحده يصيده
        ("journeys", 0, "text", SECRETS["machine_like"]),
        (None, None, "note", SECRETS["machine_like"]),
    ]
    for section, index, key, value in leaks:
        bad = json.loads(json.dumps(report))
        target = bad if section is None else bad[section] if index is None else bad[section][index]
        target[key] = value
        with pytest.raises(journeys.Refused) as refused:
            journeys.check_report(bad)
        assert refused.value.code == "report_leak", (section, key)
    with pytest.raises(journeys.Refused):
        journeys.check_report({**report, "commit": SECRETS["user"]})


def test_the_default_output_is_the_dated_probe_file(world, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(journeys, "PROBE_DIR", tmp_path)
    code = journeys.main(["--root", str(world.root)])
    capsys.readouterr()
    today = datetime.now(timezone.utc).date()
    written = tmp_path / f"journeys-{today:%Y%m%d}.json"
    assert code == 0 and written.is_file()
    assert json.loads(written.read_text(encoding="utf-8"))["generated_on"] == today.isoformat()
