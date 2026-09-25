"""سجلُّ المسائل (ك٤١): المسألةُ المغلقة مكتملةً يثبتها طلبٌ مدموج، وإلا أُعيد فتحُها.

كلُّه بلا شبكة: GitHub بنقلٍ مزيَّف، وgit بكائنٍ مزيَّف يقول ما السلفُ وما ذيولُ الطلب.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import issue_ledger as il  # noqa: E402
from agent_attribution import load_registry  # noqa: E402

REGISTRY = load_registry((ROOT / "registry" / "agents.json").read_bytes())
AFTER = "2026-09-27T10:00:00Z"
BEFORE = "2026-09-25T10:00:00Z"
SIGNED = ("عمل\n\nDiwan-Agent: anthropic/claude-opus-5-5\n")
UNSIGNED = "عمل بلا ذيل\n"


def issue(number, title, closed_at=AFTER, reason="completed", labels=()):
    return {"number": number, "title": title, "state_reason": reason, "closed_at": closed_at,
            "labels": list(labels)}


def pull(number, body, merge, merged_at="2026-09-27T09:00:00Z"):
    return {"number": number, "body": body, "merge": merge, "merged_at": merged_at}


class FakeGit:
    def __init__(self, on_main=(), commits=None):
        self.on_main, self.commits = set(on_main), commits or {}

    def is_ancestor(self, sha):
        return sha in self.on_main

    def pull_commits(self, merge):
        return [{"sha": f"{merge}-{i}", "committed_at": 1_800_000_000, "message": m}
                for i, m in enumerate(self.commits.get(merge, [SIGNED]))]


def judge(issues, pulls, git):
    return il.evaluate(issues, pulls, git, REGISTRY)


# — الحكم —

def test_a_merged_pull_on_main_with_signed_commits_proves_the_issue():
    [v] = judge([issue(37, "[ج٧] قفل")], [pull(70, "Closes #37", "m70")], FakeGit({"m70"}))
    assert v["status"] == "proven" and v["pull"] == 70 and v["merge"] == "m70"
    assert v["task"] == "ج٧" and v["agents"] == ["anthropic/claude-opus-5-5"]


def test_a_closed_issue_without_a_closing_pull_is_proof_missing():
    [v] = judge([issue(21, "[ع٢] Nitro")], [pull(69, "Refs #21", "m69")], FakeGit({"m69"}))
    assert (v["status"], v["code"]) == ("proof_missing", "no_merged_pull_request")


def test_a_merge_that_is_not_on_main_does_not_prove():
    [v] = judge([issue(5, "[ك١] س")], [pull(9, "closes #5", "m9")], FakeGit(on_main=()))
    assert (v["status"], v["code"]) == ("proof_missing", "merge_not_on_main")


def test_an_unsigned_commit_in_the_pull_does_not_prove():
    git = FakeGit({"m9"}, {"m9": [SIGNED, UNSIGNED]})
    [v] = judge([issue(5, "[ك١] س")], [pull(9, "Fixes #5", "m9")], git)
    assert (v["status"], v["code"]) == ("proof_missing", "unattributed_commits")


def test_a_later_valid_pull_proves_after_an_earlier_invalid_one():
    pulls = [pull(8, "Closes #5", "m8", "2026-09-27T08:00:00Z"), pull(9, "Resolves #5", "m9")]
    [v] = judge([issue(5, "[ك١] س")], pulls, FakeGit({"m9"}))
    assert v["status"] == "proven" and v["pull"] == 9


def test_before_enforcement_an_unproven_close_is_grandfathered_not_reopened():
    [v] = judge([issue(18, "[ك٣٢] س", closed_at=BEFORE)], [], FakeGit())
    assert (v["status"], v["code"]) == ("grandfathered", "no_merged_pull_request")


@pytest.mark.parametrize("title,labels", [("[ح٣] قرارات", ()), ("[ك٩٩] س", ("owner-action",)),
                                          ("بلا معرّف", ())])
def test_owner_tasks_and_untitled_issues_are_exempt(title, labels):
    [v] = judge([issue(55, title, labels=labels)], [], FakeGit())
    assert v["status"] == "exempt"


@pytest.mark.parametrize("reason", ["not_planned", "duplicate", None])
def test_an_issue_not_closed_as_completed_is_not_judged(reason):
    assert judge([issue(3, "[ك١] س", reason=reason)], [], FakeGit()) == []


@pytest.mark.parametrize("body,expected", [
    ("Closes #37", {37}), ("closes: #5 and Fixes #6", {5, 6}), ("resolved #7", {7}),
    ("Refs #21", set()), ("See #4", set()), ("", set()),
])
def test_only_closing_keywords_link_an_issue(body, expected):
    assert il.closing_refs(body) == expected


# — السجلّ —

def test_append_adds_only_new_proven_entries_in_close_order(tmp_path):
    path = tmp_path / "TASKS.jsonl"
    verdicts = [
        {"status": "proven", "task": "ج٧", "issue": 37, "pull": 70, "merge": "m70",
         "agents": ["anthropic/claude-opus-5-5"], "closed_at": "2026-09-27T02:00:00Z"},
        {"status": "proven", "task": "ك٤٠", "issue": 15, "pull": 58, "merge": "m58",
         "agents": ["anthropic/claude-opus-5-5"], "closed_at": "2026-09-27T01:00:00Z"},
        {"status": "proof_missing", "task": "ع٢", "issue": 21, "code": "no_merged_pull_request",
         "closed_at": AFTER},
    ]
    assert [e["issue"] for e in il.append(verdicts, path)] == [15, 37]
    assert il.append(verdicts, path) == []
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [tuple(e) for e in lines] == [il.ENTRY_KEYS] * 2


# — إعادة الفتح —

class FakeHttp(il.Http):
    def __init__(self, issues, pulls):
        self.pages = {"issues": issues, "pulls": pulls}
        self.writes = []

    def request(self, method, url, headers, body=None):
        if method == "GET":
            kind = "issues" if "/issues?" in url else "pulls"
            page = int(url.rsplit("page=", 1)[1])
            return 200, json.dumps(self.pages[kind] if page == 1 else []).encode()
        self.writes.append((method, url.split("/repos/owner/repo/")[1], json.loads(body)))
        return 200, b"{}"


def _raw_issue(number, title, closed_at=AFTER):
    return {"number": number, "title": title, "state_reason": "completed", "closed_at": closed_at,
            "labels": [{"name": "task"}]}


def test_apply_reopens_labels_and_explains_only_what_lacks_proof(capsys):
    http = FakeHttp([_raw_issue(21, "[ع٢] Nitro"), _raw_issue(37, "[ج٧] قفل"),
                     _raw_issue(18, "[ك٣٢] س", closed_at=BEFORE)],
                    [{"number": 70, "body": "Closes #37", "merge_commit_sha": "m70",
                      "merged_at": "2026-09-27T09:00:00Z"}])
    code = il.main(["apply", "--repo-slug", "owner/repo"], environ={"GH_TOKEN": "t"}, http=http,
                   git=FakeGit({"m70"}))
    assert code == 0
    assert [(m, path) for m, path, _ in http.writes] == [
        ("PATCH", "issues/21"), ("POST", "issues/21/labels"), ("POST", "issues/21/comments")]
    assert http.writes[0][2] == {"state": "open"}
    assert http.writes[1][2] == {"labels": ["proof-missing"]}
    assert "Closes #21" in http.writes[2][2]["body"]
    report = json.loads(capsys.readouterr().out)
    assert report["reopened"] == [21] and report["counts"]["grandfathered"] == 1


def test_check_fails_on_missing_proof_and_writes_nothing(capsys):
    http = FakeHttp([_raw_issue(21, "[ع٢] Nitro")], [])
    assert il.main(["check", "--repo-slug", "owner/repo"], environ={}, http=http, git=FakeGit()) == 1
    assert http.writes == []


def test_apply_without_a_token_is_a_named_error(capsys):
    http = FakeHttp([_raw_issue(21, "[ع٢] Nitro")], [])
    assert il.main(["apply", "--repo-slug", "owner/repo"], environ={}, http=http, git=FakeGit()) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "token_missing" and http.writes == []
