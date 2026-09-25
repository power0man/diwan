"""سجلُّ المسائل (ك٤١، ق٦١): كلُّ مسألةٍ أُغلقت «مكتملةً» يثبتها طلبُ دمجٍ مدموج.

المسائلُ صارت المتتبِّع، لكنّ إغلاقَها زرٌّ يضغطه أيُّ أحد. فالإثباتُ ثلاثة شروط:
١. طلبُ دمجٍ **مدموج** يُغلقها بكلمة إغلاق (`Closes #N`).
٢. إيداعُ دمجه سلفٌ لـ`main`.
٣. كلُّ إيداعٍ فيه يحمل ذيل `Diwan-Agent` بمعرّفٍ مسجَّل.

ما ثبت يُلحَق بـ`docs/TASKS.jsonl`، فيبقى الضمانُ قابلًا للفحص من أي نسخةٍ بلا شبكة،
ويقرؤه حارسُ البصمات (`tests/test_tasks_ledger.py`). وما لم يثبت بعد `ENFORCED_FROM`
تُعاد فتحُه ويُوسم `proof-missing`، ويُعلَّق عليه بالسبب.

الأوامر:
    python3 tools/issue_ledger.py check    # تقريرٌ، ويخرج 1 إن وُجدت مسألةٌ بلا إثبات
    python3 tools/issue_ledger.py apply    # ومعه إعادةُ الفتح والوسم (يلزمه GH_TOKEN بصلاحية issues)
    python3 tools/issue_ledger.py append   # يُلحق الثابتَ الجديد بـdocs/TASKS.jsonl

المُعفى: مهامُّ المالك (بادئة «ح»، أو وسمُ owner-action أو owner-decision)، والمسألةُ بلا معرّفٍ
في عنوانها. وما أُغلق «غيرَ مخطَّط» أو «مكرَّرًا» ليس إنجازًا، فلا يُسجَّل ولا يُعاد فتحه.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_attribution import REGISTRY_PATH, inspect_commit, load_registry, read_commits  # noqa: E402

API = "https://api.github.com"
LEDGER = "docs/TASKS.jsonl"
# ما أُغلق قبله يُسجَّل إن ثبت، ولا يُعاد فتحُه إن لم يثبت: السجلُّ وُلد بعده.
ENFORCED_FROM = "2026-09-26T00:00:00Z"
LABEL = "proof-missing"
EXEMPT_LABELS = frozenset({"owner-action", "owner-decision"})
OWNER_PREFIX = "ح"
TASK_TITLE = re.compile(r"\A\s*\[([^\]\s]{1,16})\]")
# كلماتُ الإغلاق كما يقرؤها GitHub؛ و«Refs #N» ليست منها عمدًا.
CLOSING = re.compile(r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)\b")
ENTRY_KEYS = ("schema_version", "task", "issue", "pull", "merge", "agents", "closed_at")
PER_PAGE = 100
MAX_PAGES = 50


class LedgerError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class Http:
    """نقلٌ بمكتبة بايثون القياسية؛ يُحقن بديلُه في الاختبار."""

    def request(self, method: str, url: str, headers: dict, body: bytes | None = None) -> tuple[int, bytes]:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as response:  # noqa: S310 — نقطةٌ ثابتة معلنة
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except (urllib.error.URLError, OSError) as exc:
            raise LedgerError("github_unreachable", type(exc).__name__) from exc


def _headers(token: str | None, write: bool = False) -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if write:
        headers["Content-Type"] = "application/json"
    return headers


def _pages(slug: str, path: str, token: str | None, http: Http) -> list[dict]:
    items: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        sep = "&" if "?" in path else "?"
        status, raw = http.request("GET", f"{API}/repos/{slug}/{path}{sep}per_page={PER_PAGE}&page={page}",
                                   _headers(token))
        if status != 200:
            raise LedgerError("github_unreachable", f"{path} {status}")
        batch = json.loads(raw)
        items.extend(batch)
        if len(batch) < PER_PAGE:
            return items
    raise LedgerError("too_many_pages", path)


def fetch_closed_issues(slug: str, token: str | None, http: Http) -> list[dict]:
    return [{"number": i["number"], "title": i["title"], "state_reason": i.get("state_reason"),
             "closed_at": i.get("closed_at"), "labels": sorted(l["name"] for l in i.get("labels", []))}
            for i in _pages(slug, "issues?state=closed", token, http) if "pull_request" not in i]


def fetch_merged_pulls(slug: str, token: str | None, http: Http) -> list[dict]:
    return [{"number": p["number"], "body": p.get("body") or "", "merge": p.get("merge_commit_sha"),
             "merged_at": p["merged_at"]}
            for p in _pages(slug, "pulls?state=closed", token, http) if p.get("merged_at")]


def closing_refs(body: str) -> set[int]:
    return {int(n) for n in CLOSING.findall(body or "")}


class Git:
    """ما يلزم من git: السلفُ على الفرع الرئيسيّ، وذيولُ إيداعات الطلب."""

    def __init__(self, root: Path, main_ref: str = "HEAD"):
        self.root, self.main_ref = root, main_ref

    def _git(self, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(self.root), *argv], capture_output=True, encoding="utf-8", timeout=60)

    def is_ancestor(self, sha: str) -> bool:
        return (self._git("cat-file", "-e", f"{sha}^{{commit}}").returncode == 0
                and self._git("merge-base", "--is-ancestor", sha, self.main_ref).returncode == 0)

    def pull_commits(self, merge: str) -> list[dict]:
        """إيداعاتُ الطلب: ما بلغه الأبُ الثاني ولم يبلغه الأول؛ وإيداعُ الدمج الواحدُ الأب نفسُه."""
        parents = self._git("rev-list", "--parents", "-n", "1", merge).stdout.split()[1:]
        revision = f"{parents[0]}..{parents[1]}" if len(parents) == 2 else f"{merge}^..{merge}"
        return read_commits(self.root, revision)


def evaluate(issues: list[dict], pulls: list[dict], git, registry: dict,
             enforced_from: str = ENFORCED_FROM) -> list[dict]:
    """حكمٌ لكل مسألةٍ أُغلقت مكتملةً: proven أو proof_missing أو exempt أو grandfathered."""
    by_issue: dict[int, list[dict]] = {}
    for pull in sorted(pulls, key=lambda p: p["merged_at"]):
        for number in closing_refs(pull["body"]):
            by_issue.setdefault(number, []).append(pull)
    verdicts = []
    for issue in sorted(issues, key=lambda i: i["number"]):
        if issue["state_reason"] != "completed":
            continue
        base = {"issue": issue["number"], "closed_at": issue["closed_at"]}
        match = TASK_TITLE.match(issue["title"])
        if not match:
            verdicts.append({**base, "status": "exempt", "code": "no_task_id"})
            continue
        task = match.group(1)
        base["task"] = task
        if task.startswith(OWNER_PREFIX) or EXEMPT_LABELS & set(issue["labels"]):
            verdicts.append({**base, "status": "exempt", "code": "owner_task"})
            continue
        code = "no_merged_pull_request"
        proven = None
        for pull in by_issue.get(issue["number"], []):
            if not pull["merge"] or not git.is_ancestor(pull["merge"]):
                code = "merge_not_on_main"
                continue
            commits = git.pull_commits(pull["merge"])
            if not commits or any(inspect_commit(c, registry) for c in commits):
                code = "unattributed_commits"
                continue
            agents = sorted({v for c in commits for v in _agent_values(c, registry)})
            proven = {**base, "status": "proven", "pull": pull["number"], "merge": pull["merge"],
                      "agents": agents}
            break
        if proven:
            verdicts.append(proven)
        elif (issue["closed_at"] or "") < enforced_from:
            verdicts.append({**base, "status": "grandfathered", "code": code})
        else:
            verdicts.append({**base, "status": "proof_missing", "code": code})
    return verdicts


def _agent_values(commit: dict, registry: dict) -> list[str]:
    from agent_attribution import parse_trailers
    return parse_trailers(commit["message"]).get(registry["trailer"], [])


def entry(verdict: dict) -> dict:
    return {"schema_version": 1, "task": verdict["task"], "issue": verdict["issue"], "pull": verdict["pull"],
            "merge": verdict["merge"], "agents": verdict["agents"], "closed_at": verdict["closed_at"]}


def read_ledger(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append(verdicts: list[dict], path: Path) -> list[dict]:
    """يُلحق الثابتَ الجديد وحده، بترتيب الإغلاق. السجلُّ إلحاقيّ: لا يُعاد كتابةُ سطرٍ فيه."""
    known = {e["issue"] for e in read_ledger(path)}
    new = [entry(v) for v in sorted(verdicts, key=lambda v: (v["closed_at"] or "", v["issue"]))
           if v["status"] == "proven" and v["issue"] not in known]
    if new:
        with path.open("a", encoding="utf-8") as stream:
            for item in new:
                stream.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
    return new


REASONS = {
    "no_merged_pull_request": "لا طلبَ دمجٍ مدموج يُغلقها بكلمة إغلاق (`Closes #N`).",
    "merge_not_on_main": "إيداعُ دمج الطلب الذي يُغلقها ليس سلفًا لـ`main`.",
    "unattributed_commits": "في الطلب الذي يُغلقها إيداعٌ بلا ذيل `Diwan-Agent` بمعرّفٍ مسجَّل.",
}


def reopen(slug: str, verdict: dict, token: str, http: Http) -> None:
    number = verdict["issue"]
    calls = (
        ("PATCH", f"{API}/repos/{slug}/issues/{number}", {"state": "open"}),
        ("POST", f"{API}/repos/{slug}/issues/{number}/labels", {"labels": [LABEL]}),
        ("POST", f"{API}/repos/{slug}/issues/{number}/comments",
         {"body": (f"أُعيد فتحُ هذه المسألة ووُسمت `{LABEL}` (ك٤١): {REASONS[verdict['code']]}\n\n"
                   "تُغلق حين يُدمج طلبٌ يحمل `Closes #" + str(number) + "`، وإيداعاتُه بذيولٍ مسجَّلة.")}),
    )
    for method, url, payload in calls:
        status, _ = http.request(method, url, _headers(token, write=True),
                                 json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        if status not in (200, 201):
            raise LedgerError("github_write_failed", f"{method} #{number} {status}")


def main(argv: list[str] | None = None, environ=None, http: Http | None = None, git=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("check", "apply", "append"))
    parser.add_argument("--repo-slug", default=None)
    parser.add_argument("--main-ref", default="HEAD")
    args = parser.parse_args(argv)
    env = os.environ if environ is None else environ
    slug = args.repo_slug or env.get("REPO") or "power0man/diwan"
    token = env.get("GH_TOKEN") or env.get("GITHUB_TOKEN")
    http = http or Http()
    git = git or Git(ROOT, args.main_ref)
    try:
        registry = load_registry((ROOT / REGISTRY_PATH).read_bytes())
        verdicts = evaluate(fetch_closed_issues(slug, token, http), fetch_merged_pulls(slug, token, http),
                            git, registry)
        missing = [v for v in verdicts if v["status"] == "proof_missing"]
        report = {"schema_version": 1, "enforced_from": ENFORCED_FROM,
                  "counts": {s: sum(v["status"] == s for v in verdicts)
                             for s in ("proven", "proof_missing", "grandfathered", "exempt")},
                  "proof_missing": missing}
        if args.command == "apply":
            if missing and not token:
                raise LedgerError("token_missing", "GH_TOKEN مطلوبٌ لإعادة الفتح")
            for verdict in missing:
                reopen(slug, verdict, token, http)
            report["reopened"] = [v["issue"] for v in missing]
        if args.command == "append":
            report["appended"] = [e["issue"] for e in append(verdicts, ROOT / LEDGER)]
    except LedgerError as exc:
        print(json.dumps({"status": "error", "code": exc.code, "detail": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if missing and args.command == "check" else 0


if __name__ == "__main__":
    raise SystemExit(main())
