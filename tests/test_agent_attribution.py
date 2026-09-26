"""بوابةُ وسمِ العميل: تمنع المجهولية، ولا تدّعي إثباتَ هوية."""
from __future__ import annotations

import json

import pytest

from tools.agent_attribution import (AttributionError, check_commits,
                                     inspect_commit, load_registry,
                                     parse_log, parse_trailers)

AGENT = "anthropic/claude-opus-5"
CUTOFF = 1790067600  # 2026-09-22T09:00:00Z
RAW = {"schema_version": 1, "trailer": "Diwan-Agent",
       "enforced_from": "2026-09-22T09:00:00Z",
       "limits": ["self_declared_not_authenticated"],
       "agents": {AGENT: {"surface": "Claude Code", "admitted": "2026-09-22"},
                  "openai/codex": {"surface": "Codex", "admitted": "2026-09-22"}}}


@pytest.fixture
def registry():
    return load_registry(json.dumps(RAW))


def made(message: str, *, at: int = CUTOFF + 60, sha: str = "a" * 40):
    return {"sha": sha, "committed_at": at, "message": message}


def tagged(agent: str = AGENT, *, subject: str = "عنوان") -> str:
    return f"{subject}\n\nشرحٌ للتغيير.\n\nDiwan-Agent: {agent}\n"


# ————— قراءةُ السجل: فشلٌ مغلق على كل شكلٍ غير مُعلَن —————

def test_registry_round_trips_and_derives_the_cutoff(registry):
    assert registry["trailer"] == "Diwan-Agent"
    assert registry["enforced_from_epoch"] == CUTOFF


@pytest.mark.parametrize("mutate,code", [
    (lambda r: r.pop("agents"), "registry_schema_invalid"),
    (lambda r: r.update(extra=1), "registry_schema_invalid"),
    (lambda r: r.update(schema_version=2), "registry_schema_invalid"),
    (lambda r: r.update(agents={}), "registry_schema_invalid"),
    (lambda r: r.update(enforced_from="2026-09-22T09:00:00"), "registry_schema_invalid"),
    (lambda r: r.update(enforced_from="غير تاريخ"), "registry_schema_invalid"),
    (lambda r: r.update(trailer="Bad Trailer"), "registry_schema_invalid"),
    (lambda r: r.update(agents={"NoSlash": {"surface": "x", "admitted": "y"}}),
     "registry_agent_id_invalid"),
    (lambda r: r.update(agents={AGENT: {"surface": "x"}}), "registry_schema_invalid"),
    (lambda r: r.update(limits=[]), "registry_schema_invalid"),
])
def test_malformed_registry_is_refused_by_name(mutate, code):
    raw = json.loads(json.dumps(RAW))
    mutate(raw)
    with pytest.raises(AttributionError) as exc:
        load_registry(json.dumps(raw))
    assert exc.value.code == code


def test_unreadable_registry_is_refused_by_name():
    with pytest.raises(AttributionError) as exc:
        load_registry("{ not json")
    assert exc.value.code == "registry_unreadable"


# ————— قراءةُ الذيل: حتمية، لا تتبع إعداد git لدى المستخدم —————

def test_trailers_come_from_the_last_paragraph_only():
    message = "عنوان\n\nDiwan-Agent: مزيف\n\nDiwan-Agent: " + AGENT + "\n"
    assert parse_trailers(message)["Diwan-Agent"] == [AGENT]


def test_a_prose_line_in_the_last_paragraph_voids_the_block():
    # وإلّا صار سطرٌ نثريٌّ فيه نقطتان ذيلًا زائفًا.
    assert parse_trailers("عنوان\n\nملاحظة: هذا نثر\nDiwan-Agent: x\nونثرٌ آخر") == {}


def test_a_trailer_glued_to_the_subject_line_is_not_a_trailer_block():
    """العنوانُ نثرٌ، فالمقطعُ الأخيرُ فيه سطرٌ غيرُ ذيل — فيبطل كلُّه.

    وهذا موافقٌ لقاعدة git نفسها: الذيلُ مقطعٌ أخيرٌ مستقل. و`git commit -m
    "عنوان" -m "Diwan-Agent: …"` يُدخل السطرَ الفارغ فيصحّ.
    """
    assert parse_trailers("عنوان\nDiwan-Agent: " + AGENT) == {}
    assert parse_trailers("عنوان\n\nDiwan-Agent: " + AGENT)["Diwan-Agent"] == [AGENT]


# ————— الحكم على الدفعة —————

def test_tagged_registered_commit_passes(registry):
    assert inspect_commit(made(tagged()), registry) is None


def test_untagged_commit_after_the_cutoff_is_refused(registry):
    finding = inspect_commit(made("عنوان\n\nبلا وسم\n"), registry)
    assert finding["code"] == "agent_tag_missing"


def test_untagged_commit_before_the_cutoff_is_exempt(registry):
    # الماضي يُسجَّل ولا يُزوَّر: ٣٨ دفعةً مجهولةً لا تُعاد كتابتها.
    assert inspect_commit(made("عنوان\n\nبلا وسم\n", at=CUTOFF - 1), registry) is None


def test_the_cutoff_second_itself_is_enforced(registry):
    assert inspect_commit(made("بلا وسم", at=CUTOFF), registry)["code"] == "agent_tag_missing"


def test_two_agent_lines_are_ambiguous_not_acceptable(registry):
    message = "عنوان\n\nDiwan-Agent: " + AGENT + "\nDiwan-Agent: openai/codex\n"
    assert inspect_commit(made(message), registry)["code"] == "agent_tag_ambiguous"


@pytest.mark.parametrize("value", ["Claude", "anthropic", "ANTHROPIC/Claude",
                                   "anthropic/claude opus", "/claude", "anthropic/"])
def test_malformed_agent_values_are_refused(registry, value):
    assert inspect_commit(made(tagged(value)), registry)["code"] == "agent_tag_malformed"


def test_wellformed_but_unregistered_agent_is_refused(registry):
    finding = inspect_commit(made(tagged("blackbox/agent-1")), registry)
    assert finding["code"] == "agent_not_registered"
    assert "registry/agents.json" in finding["detail"]


def test_co_authored_by_does_not_satisfy_the_gate(registry):
    """السطرُ الذي تكتبه الأداةُ لنفسها ليس إعلانَ مُنتِج."""
    message = "عنوان\n\nCo-Authored-By: Claude Opus 5 <noreply@anthropic.com>\n"
    assert inspect_commit(made(message), registry)["code"] == "agent_tag_missing"


# ————— التقرير المجمَّع —————

def test_report_names_every_offender_not_just_the_first(registry):
    commits = [made(tagged(), sha="1" * 40), made("بلا وسم", sha="2" * 40),
               made(tagged("blackbox/x"), sha="3" * 40)]
    report = check_commits(commits, registry)
    assert report["status"] == "failed" and report["checked"] == 3
    assert [f["sha"] for f in report["findings"]] == ["2" * 40, "3" * 40]
    assert "self_declared_not_authenticated" in report["limits"]


def test_all_tagged_passes_with_no_findings(registry):
    report = check_commits([made(tagged(), sha="1" * 40)], registry)
    assert report["status"] == "passed" and report["findings"] == []


def test_empty_range_passes(registry):
    assert check_commits([], registry)["status"] == "passed"


# ————— فكُّ مخرج git —————

def test_parse_log_splits_messages_containing_blank_lines():
    sep = "<<<diwan-commit-end>>>"
    raw = (f"{'1' * 40}\x00{CUTOFF}\x00عنوان\n\nمتن\n\nDiwan-Agent: {AGENT}\n{sep}\n"
           f"{'2' * 40}\x00{CUTOFF}\x00ثانٍ\n{sep}\n")
    commits = parse_log(raw)
    assert [c["sha"] for c in commits] == ["1" * 40, "2" * 40]
    assert parse_trailers(commits[0]["message"])["Diwan-Agent"] == [AGENT]


def test_parse_log_refuses_garbage_rather_than_skipping_it():
    with pytest.raises(AttributionError) as exc:
        parse_log("لا فواصل هنا<<<diwan-commit-end>>>")
    assert exc.value.code == "git_log_unparsable"


def test_the_real_commit_footer_shape_is_read(registry):
    """الشكلُ الواقعيّ لرسائل هذا المستودع، لا مثالًا مؤلَّفًا.

    الأداةُ المستضيفة تُلزم بأن يكون Co-Authored-By وClaude-Session آخرَ
    سطرين. فلو وُضع Diwan-Agent في مقطعٍ مستقلٍّ قبلهما لصار المقطعُ
    الأخيرُ بلا وسم — ومرّت الدفعةُ مجهولةً في نظر البوابة. الصحيح أن
    يكون في المقطع نفسه. وهذا عيبٌ وقع فعلًا في أول دفعةٍ حملت الوسم.
    """
    separate_block = ("عنوان\n\nمتن الشرح.\n\nDiwan-Agent: " + AGENT +
                      "\n\nCo-Authored-By: Claude Opus 5 <noreply@anthropic.com>\n"
                      "Claude-Session: https://claude.ai/code/session_x\n")
    assert inspect_commit(made(separate_block), registry)["code"] == "agent_tag_missing"

    one_block = ("عنوان\n\nمتن الشرح.\n\nDiwan-Agent: " + AGENT +
                 "\nCo-Authored-By: Claude Opus 5 <noreply@anthropic.com>\n"
                 "Claude-Session: https://claude.ai/code/session_x\n")
    assert inspect_commit(made(one_block), registry) is None


# ————— الدمجُ الذي يحمل محتواه يُوسم، والفارغُ معفًى —————

def _git(repo, *argv, env=None):
    import subprocess
    return subprocess.run(["git", "-C", str(repo), *argv], check=True, capture_output=True, text=True,
                          env=env).stdout.strip()


def _repo(tmp_path):
    import os
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    _git(repo, "init", "-q", "-b", "main", env=env)
    return repo, env


def _commit(repo, env, name, text, message):
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", name, env=env)
    _git(repo, "commit", "-q", "-m", message, env=env)


def test_a_merge_that_resolves_a_conflict_must_carry_the_tag_and_a_clean_merge_is_exempt(tmp_path):
    from tools.agent_attribution import read_content_merges
    repo, env = _repo(tmp_path)
    tag = f"\n\nDiwan-Agent: {AGENT}"
    _commit(repo, env, "a.txt", "base\n", "أساس" + tag)
    base = _git(repo, "rev-parse", "HEAD", env=env)
    _git(repo, "checkout", "-q", "-b", "side", env=env)
    _commit(repo, env, "a.txt", "side\n", "فرع" + tag)
    _commit(repo, env, "b.txt", "only side\n", "ملفٌّ في الفرع" + tag)
    _git(repo, "checkout", "-q", "main", env=env)
    _commit(repo, env, "a.txt", "main\n", "رئيس" + tag)
    _commit(repo, env, "c.txt", "only main\n", "ملفٌّ في الرئيس" + tag)
    import subprocess
    subprocess.run(["git", "-C", str(repo), "merge", "-q", "side", "-m", "دمجٌ بحلّ تعارض"], env=env,
                   capture_output=True)
    (repo / "a.txt").write_text("resolved by hand\n", encoding="utf-8")
    _git(repo, "add", "a.txt", env=env)
    _git(repo, "commit", "-q", "--no-edit", env=env)
    resolved = read_content_merges(repo, f"{base}..HEAD")
    assert [m["message"].splitlines()[0] for m in resolved] == ["دمجٌ بحلّ تعارض"]
    # ودمجٌ نظيفٌ بلا تعارض لا يحمل محتواه: يبقى معفًى
    _git(repo, "checkout", "-q", "-b", "clean", base, env=env)
    _commit(repo, env, "d.txt", "clean\n", "فرعٌ نظيف" + tag)
    _git(repo, "checkout", "-q", "main", env=env)
    _git(repo, "merge", "-q", "--no-ff", "clean", "-m", "دمجٌ نظيف", env=env)
    names = [m["message"].splitlines()[0] for m in read_content_merges(repo, f"{base}..HEAD")]
    assert names == ["دمجٌ بحلّ تعارض"]


def test_the_cli_refuses_an_untagged_resolving_merge(tmp_path, capsys):
    from tools import agent_attribution
    repo, env = _repo(tmp_path)
    tag = f"\n\nDiwan-Agent: {AGENT}"
    _commit(repo, env, "a.txt", "base\n", "أساس" + tag)
    base = _git(repo, "rev-parse", "HEAD", env=env)
    _git(repo, "checkout", "-q", "-b", "side", env=env)
    _commit(repo, env, "a.txt", "side\n", "فرع" + tag)
    _git(repo, "checkout", "-q", "main", env=env)
    _commit(repo, env, "a.txt", "main\n", "رئيس" + tag)
    import subprocess
    subprocess.run(["git", "-C", str(repo), "merge", "-q", "side", "-m", "دمجٌ بحلّ تعارض"], env=env,
                   capture_output=True)
    (repo / "a.txt").write_text("resolved\n", encoding="utf-8")
    _git(repo, "add", "a.txt", env=env)
    _git(repo, "commit", "-q", "--no-edit", env=env)
    registry_path = tmp_path / "agents.json"
    registry_path.write_text(json.dumps(RAW), encoding="utf-8")
    code = agent_attribution.main(["--repo", str(repo), "--range", f"{base}..HEAD",
                                   "--registry", str(registry_path)])
    report = json.loads(capsys.readouterr().out.splitlines()[0])
    assert code == 1 and report["checked"] == 3
    assert [f["subject"] for f in report["findings"]] == ["دمجٌ بحلّ تعارض"]
