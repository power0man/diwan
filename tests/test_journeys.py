"""حارسُ سجلّ رحلات المالك بلا نصوص (جديد-journeys-log، #163).

المخزنُ يبنيه المنتجُ نفسُه: `webui.server.LocalApp` بمزوّدٍ محلّيٍّ مكتوبٍ سلفًا، بمشروعٍ وجلساتٍ بكل وضعٍ يبلغه الاختبار
(نصّ، ووسائط، ووكيل، ومبرمج، وترجمة) وبكل صنف نتيجةٍ ينتجه المنتج (منجزة، ومبتورة، ومرفوضة، وتنتظر المالك، وانتهت مهلتُها،
وبلغت حدَّ الخطوات، وأوقفها المالك، ومجهولةُ النتيجة). وفي رسائل المالك وأجوبة النموذج واسم المشروع واسم الجلسة واسم الملف المرفوع ومحتواه
ومسار ما كتبه الوكيل وذاكرة المشروع نصوصٌ مميّزة، ثم يُشغَّل `tools/journeys.py` فلا يحمل تقريرُه شيئًا منها، ولا مسارًا ولا
معرّفًا ولا بصمة — بنصّه الخام وبصورتَي هروب JSON، ولو أُطفئ فحصُ الأداة الذاتي — وأعدادُه تطابق ما صنعه المنتج.
"""
from __future__ import annotations

from datetime import datetime, timezone
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


def report_of(root: Path, tmp_path: Path, capsys) -> dict:
    out = tmp_path / f"report-{uuid.uuid4().hex[:6]}.json"
    code, printed = run(root, out, capsys)
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
    assert report["completion_rate"] == round(5 / 16, 4)
    assert report["outcome_labels"]["completed"] == "منجزة" and report["outcome_labels"]["refused"] == "مرفوضة"
    seen = sorted((j["mode"], j["status"], j["outcome"], j["error_code"], j["steps"], j["tool_calls"])
                  for j in report["journeys"])
    assert seen == sorted([
        ("text", "complete", "completed", None, 1, 0),
        ("text", "truncated", "truncated", "output_truncated", 1, 0),
        ("text", "error", "refused", "provider_error", 1, 0),
        ("text", "error", "refused", "provider_unavailable", 1, 0),
        ("text", "error", "outcome_unknown", "outcome_uncertain", 1, 0),
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
    report = report_of(copy, tmp_path, capsys)
    assert report["unreadable"] == {"projects": 0, "sessions": 5,
                                    "by_code": {"entry_invalid": 1, "mode_unknown": 1, "state_corrupt": 1,
                                                "state_missing": 1, "unsafe_path": 1}}
    assert report["totals"]["sessions"] == 9
    assert report["totals"]["journeys"] == 16 - 5 - 1 - 1
    assert report["by_mode"]["text"] == 0 and report["by_mode"]["media"] == 0 and report["by_mode"]["coder"] == 0


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


def test_baseline_ready_needs_thirty_dated_journeys_on_two_dates(thirty, tmp_path, capsys):
    root = tmp_path / "ui"
    shutil.copytree(thirty, root)
    one, fourteen, fifteen = sorted(session_files(root),
                                    key=lambda item: len(json.loads(item[1].read_text())["state"]["turns"]))

    def place(meta, state, created, last):
        os.utime(meta, (created, created))
        os.utime(state, (last, last))

    for meta, state, _ in (one, fourteen, fifteen):
        place(meta, state, DAY_ONE, DAY_ONE + 60)
    one_day = report_of(root, tmp_path, capsys)
    assert (one_day["totals"]["dated_journeys"], one_day["distinct_dates"]) == (30, 1)
    assert one_day["baseline_ready"] is False and one_day["baseline"]["missing"] == ["too_few_distinct_dates"]
    place(*fourteen[:2], DAY_ONE + DAY, DAY_ONE + DAY + 60)
    place(*one[:2], DAY_ONE + DAY, DAY_ONE + DAY + 60)
    ready = report_of(root, tmp_path, capsys)
    assert ready["by_date"] == {"2026-10-01": 15, "2026-10-02": 15}
    assert ready["baseline_ready"] is True and ready["baseline"]["missing"] == []
    assert ready["completion_rate"] == 1.0
    place(*one[:2], DAY_ONE, DAY_ONE + DAY)                          # جلسةُ الرحلة الواحدة امتدّت يومين
    short = report_of(root, tmp_path, capsys)
    assert (short["totals"]["dated_journeys"], short["distinct_dates"]) == (29, 2)
    assert short["baseline_ready"] is False and short["baseline"]["missing"] == ["too_few_dated_journeys"]


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
