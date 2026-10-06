#!/usr/bin/env python3
"""وسمُ العميل المنتج لكل دفعة — يمنع المجهولية، لا ينتحل ولا يُوثّق.

المشكلة المقيسة: ٨٦ دفعة في المستودع، ٣٨ منها بلا أيِّ وسمِ عميل، وكلُّها
باسمٍ واحد على جهازٍ واحد. وحين يعمل على المستودع أكثرُ من عميل (Claude،
Codex، Gemini، وما سيُضاف) تحت هويةٍ واحدة، صار مَن يدّعي دعوى غيرَ مَن
يُحاسَب عليها — وهو **غسيلُ الدعاوى**: يبني عميلٌ ويُصدّق عميلٌ آخر دعواه
بوصفها «ما في المستودع»، بلا أن يُعرف مَن قالها.

**حدُّ هذه الأداة، مُعلَنًا:** الوسمُ إقرارٌ ذاتيّ. لا يُثبت أيُّ عميلٍ
أنتج الدفعة، ولا يمنع عميلًا أن يكتب اسمَ غيره. يمنع شيئًا واحدًا: أن
تكون الدفعةُ **مجهولةَ المُنتِج**. وهذا وحده يقطع الغسيل، لأن الدعوى
تصير منسوبةً فتُراجَع في سياق قائلها.

ولا تُعالَج الدفعاتُ السابقة لوقت التفعيل (`enforced_from`) — فالماضي
يُسجَّل ولا يُزوَّر.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

REGISTRY_PATH = "registry/agents.json"
_AGENT_ID = re.compile(r"[a-z0-9][a-z0-9.-]{0,31}/[a-z0-9][a-z0-9._-]{0,47}\Z")
_TRAILER_LINE = re.compile(r"\A([A-Za-z][A-Za-z0-9-]*):[ \t]*(.+?)[ \t]*\Z")


class AttributionError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


def load_registry(raw: str | bytes) -> dict:
    """يقرأ السجل ويرفض شكلًا غيرَ مُعلَن — لا يقبل حقولًا زائدة."""
    try:
        registry = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise AttributionError("registry_unreadable") from exc
    required = {"schema_version", "trailer", "enforced_from", "limits", "agents"}
    if not isinstance(registry, dict) or registry.keys() != required:
        raise AttributionError("registry_schema_invalid")
    if registry["schema_version"] != 1:
        raise AttributionError("registry_schema_invalid")
    if not isinstance(registry["trailer"], str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", registry["trailer"]):
        raise AttributionError("registry_schema_invalid")
    try:
        moment = datetime.fromisoformat(registry["enforced_from"].replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise AttributionError("registry_schema_invalid") from exc
    if moment.tzinfo is None:
        raise AttributionError("registry_schema_invalid")
    registry["enforced_from_epoch"] = int(moment.timestamp())
    agents = registry["agents"]
    if not isinstance(agents, dict) or not agents:
        raise AttributionError("registry_schema_invalid")
    for agent_id, entry in agents.items():
        if not _AGENT_ID.fullmatch(agent_id):
            raise AttributionError("registry_agent_id_invalid", agent_id)
        if not isinstance(entry, dict) or entry.keys() != {"surface", "admitted"}:
            raise AttributionError("registry_schema_invalid", agent_id)
    if not isinstance(registry["limits"], list) or not registry["limits"]:
        raise AttributionError("registry_schema_invalid")
    return registry


def parse_trailers(message: str) -> dict[str, list[str]]:
    """مقطعُ الذيل الأخير وحده — سطرٌ واحدٌ خارج الشكل يُبطل المقطع كلَّه.

    لا يُستعمل `git interpret-trailers`: إعدادُ المستخدم يغيّر سلوكَه،
    فتختلف البوابةُ باختلاف الجهاز. القراءة هنا حتمية.
    """
    blocks = [b for b in re.split(r"\n[ \t]*\n", message.replace("\r\n", "\n")) if b.strip()]
    if not blocks:
        return {}
    trailers: dict[str, list[str]] = {}
    for line in blocks[-1].splitlines():
        if not line.strip():
            continue
        match = _TRAILER_LINE.match(line)
        if not match:
            return {}
        trailers.setdefault(match.group(1), []).append(match.group(2))
    return trailers


def inspect_commit(commit: dict, registry: dict) -> dict | None:
    """يُرجع اعتراضًا مسمّى، أو None إن كانت الدفعة منسوبةً نسبةً صالحة."""
    if commit["committed_at"] < registry["enforced_from_epoch"]:
        return None
    key = registry["trailer"]
    values = parse_trailers(commit["message"]).get(key, [])
    base = {"sha": commit["sha"], "subject": commit["message"].splitlines()[0][:72]}
    if not values:
        return {**base, "code": "agent_tag_missing",
                "detail": f"لا يحمل ذيلًا باسم {key}"}
    if len(values) > 1:
        return {**base, "code": "agent_tag_ambiguous",
                "detail": f"{len(values)} أسطر {key}: مُنتِجٌ واحدٌ لكل دفعة"}
    value = values[0]
    if not _AGENT_ID.fullmatch(value):
        return {**base, "code": "agent_tag_malformed",
                "detail": f"«{value}» ليس على شكل vendor/agent"}
    if value not in registry["agents"]:
        return {**base, "code": "agent_not_registered",
                "detail": f"«{value}» غير مسجَّل في {REGISTRY_PATH}"}
    return None


def check_commits(commits: list[dict], registry: dict) -> dict:
    findings = [f for f in (inspect_commit(c, registry) for c in commits) if f]
    return {"schema_version": 1, "status": "failed" if findings else "passed",
            "code": "unattributed_commits" if findings else "attributed",
            "checked": len(commits), "enforced_from": registry["enforced_from"],
            "findings": findings,
            "limits": ["self_declared_not_authenticated",
                       "commits_before_enforced_from_are_exempt"]}


# فاصلٌ مطبوع: لا يُمرَّر NUL في argv، ولا يَرِد في رسالةِ دفعةٍ واقعية.
_SEP = "<<<diwan-commit-end>>>"


LOG_FORMAT = f"--format=%H%x00%ct%x00%B{_SEP}"
LOG_ARGV = ("log", "--no-merges", LOG_FORMAT)


def parse_log(raw: str) -> list[dict]:
    """يفكّ مخرجَ LOG_ARGV — مشتركٌ بين البوابة الموثوقة وسطر الأوامر."""
    commits = []
    for chunk in raw.split(_SEP):
        if not chunk.strip():
            continue
        try:
            sha, epoch, message = chunk.lstrip("\n").split("\x00", 2)
            commits.append({"sha": sha, "committed_at": int(epoch), "message": message})
        except ValueError as exc:
            raise AttributionError("git_log_unparsable") from exc
    return commits


def read_commits(repo: Path, revision_range: str) -> list[dict]:
    result = subprocess.run(["git", "-C", str(repo), *LOG_ARGV, revision_range],
                            capture_output=True, encoding="utf-8", timeout=60)
    if result.returncode:
        raise AttributionError("git_read_failed", result.stderr.strip()[:200])
    return parse_log(result.stdout)


def _git(repo: Path, *argv: str) -> str:
    # git يبتر سياقَ رأس المقطع بالبايت فقد يقطع حرفًا عربيًّا نصفين؛ والمقروءُ هنا وجودُ الفرق لا نصُّه.
    result = subprocess.run(["git", "-C", str(repo), *argv], capture_output=True, encoding="utf-8",
                            errors="replace", timeout=60)
    if result.returncode:
        raise AttributionError("git_read_failed", result.stderr.strip()[:200])
    return result.stdout


def read_content_merges(repo: Path, revision_range: str) -> list[dict]:
    """إيداعاتُ الدمج التي تحمل محتواها: حلَّ تعارضٍ أو تعديلًا ليس في أيٍّ من أبويها.

    `--no-merges` كان يعفي الدمجَ كلَّه، فمرّ إيداعا دمجٍ يحملان عشرين سطرًا من حلّ التعارض بلا وسم
    (تقييم ٢٥ سبتمبر §٥). والدمجُ الفارغ لا مُنتِجَ لمحتواه فيبقى معفًى: فرقُه المركّب (`--cc`) فارغ.
    """
    merges = parse_log(_git(repo, "log", "--merges", LOG_FORMAT, revision_range))
    return [merge for merge in merges if _git(repo, "show", "--cc", "--format=", merge["sha"]).strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--range", dest="revision_range", required=True,
                        help="مدى git، مثل origin/main..HEAD")
    parser.add_argument("--registry", type=Path,
                        help="مسار السجل؛ الافتراض داخل المستودع")
    args = parser.parse_args(argv)
    try:
        path = args.registry or args.repo / REGISTRY_PATH
        registry = load_registry(Path(path).read_bytes())
        commits = read_commits(args.repo, args.revision_range) + read_content_merges(args.repo, args.revision_range)
        report = check_commits(commits, registry)
    except AttributionError as exc:
        report = {"schema_version": 1, "status": "failed", "code": exc.code,
                  "detail": exc.detail, "findings": []}
    except OSError as exc:
        report = {"schema_version": 1, "status": "failed", "code": "registry_unreadable",
                  "detail": str(exc)[:200], "findings": []}
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if report["status"] == "failed":
        print(f"\nكلُّ دفعةٍ تُعلن مُنتِجَها. أضف إلى رسالة الدفعة سطرًا أخيرًا:\n"
              f"  Diwan-Agent: anthropic/claude-opus-5\n"
              f"وإن كان عميلًا جديدًا فسجِّله أولًا في {REGISTRY_PATH}.\n"
              f"والوسمُ إقرارٌ لا إثبات: يمنع المجهولية لا الانتحال.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
