#!/usr/bin/env python3
"""مراجعةٌ من عائلةٍ غير عائلة المؤلّف على رأس طلب الدمج الحاليّ (ك٤٠، ق٦١).

`AGENTS.md` §٤: «لا يراجع أحدٌ عمله، ولا عمل عائلته». وقرّر المالك (ق٦١) أن الدمج الآليّ مشروطٌ بمراجعةٍ من عائلةٍ أخرى؛
وهذه الأداةُ تجعل الشرطَ فحصًا يُطلب في حماية `main` لا قاعدةً مكتوبة:

- **عائلاتُ المؤلّفين** من ذيل `Diwan-Agent` في إيداعات الطلب (لا من مؤلّف git ولا من حساب GitHub: كلُّ العملاء يدفعون بحساب المالك).
- **المراجِعون المحتسَبون** بوتاتُ GitHub المدرَجة في `registry/reviewers.json` وحدها، لأن هويّةَ البوت يشهد بها GitHub، أمّا
  حسابُ المالك فيكتب به كلُّ عميل فلا تُحتسب مراجعتُه (والمالكُ يتجاوز الفحصَ بأن يدمج بنفسه).
- **تُحتسب** مراجعةٌ على الرأس الحاليّ فقط، من بوتٍ عائلتُه ليست بين عائلات المؤلّفين، وحالتُها الأخيرةُ على الرأس مقبولة؛
  ومراجِعٌ محتسَبٌ آخرُ حالته «طلبُ تغييرات» يُسقط الفحص.

    python tools/family_review.py --range BASE..HEAD --head HEAD --pr N --repo-slug owner/name   # في CI
    python tools/family_review.py --range BASE..HEAD --head HEAD --reviews-json reviews.json    # بلا شبكة

**تعليقُ المراجعة النظيفة (ق٦٥):** Codex حين لا يجد ملاحظةً لا ينشر «مراجعة» بل تعليقًا نصُّه `Didn't find any major issues`
ومعه `Reviewed commit:` ببصمةٍ مختصرة. فيُقرأ تعليقٌ كهذا من بوتٍ مدرَجٍ في `CLEAN_REVIEW_COMMENTS` مراجعةً بحالة COMMENTED على
الإيداع الذي يسمّيه، ولا تُحتسب إلا إن كان ذلك الإيداعُ الرأسَ الحاليّ. و`--wait-seconds` ينتظر المراجِعَ الذي يأتي بعد الدفع بدقائق.

**الثقةُ من نسخة الأداة لا من الطلب (جديد-actions-hardening ٢):** سجلُّ العملاء وخريطةُ المراجِعين يُقرآن من النسخة التي
فيها هذه الأداة (`ROOT`)، و`--repo` يُقرأ منه تاريخُ git وحده. والمهمّةُ تشغّل الأداةَ من نسخة `main` وتعطيها نسخةَ الطلب
`--repo`؛ فطلبٌ يُدرج بوتًا في `reviewers.json` أو يُخرج عميلَه من `agents.json` لا يغيّر حكمَه على نفسه.

**الحكمُ النظيف لا مجرّدُ المراجعة (تقييم المرحلة ٢ من ق٧٦، ‎docs/probe/team-pilot-20261008.json):** دُمج #353 والفحصُ ناجح،
لأن الأداة احتسبت مراجعةَ Codex بحالة COMMENTED على الرأس، وفيها ملاحظاتٌ P1/P2، وكانت عشرةُ خيوطٍ مفتوحة. فصار يُشترط أمران:
- مراجعةُ COMMENTED التي تحمل ملاحظاتٍ سطرية حكمُ «يحتاج تصحيحًا» يحجب كطلب التغييرات، ولا يُحتسب.
- كلُّ خيطٍ غير محلول فتحه بوتٌ مدرَج من عائلةٍ أخرى يُسقط الفحص، ولو صار قديمًا (outdated). فالرأسُ النظيف وحده لا يكفي ما دامت ملاحظةٌ سابقة لم تُغلق.

**الحدُّ المعلَن:** حلُّ الخيط فعلٌ معلَنٌ بحساب المالك الذي يكتب به كلُّ عميل، لا إثباتٌ أن الملاحظة عولجت. وحلُّ خيطٍ لا يُطلق
الفحصَ من جديد، فيُعاد تشغيلُه بعده يدويًّا أو بدفعٍ جديد.
وملفُّ المهمّة نفسُه يأتي من الطلب (حدثُ `pull_request`)، فطلبٌ يعدّل `.github/workflows/family-review.yml` يعدّل ما يشغّله؛
وسدُّ ذلك قاعدةٌ في حماية الفرع على `.github/` أو مراجعةُ المالك، لا هذه الأداة.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_attribution import (REGISTRY_PATH, AttributionError, load_registry,  # noqa: E402
                               parse_trailers, read_commits)

ROOT = Path(__file__).resolve().parent.parent
REVIEWERS_PATH = "registry/reviewers.json"
ALLOWED_STATES = frozenset({"APPROVED", "COMMENTED"})
BLOCKING_STATE = "CHANGES_REQUESTED"
# مراجعةُ COMMENTED بملاحظاتٍ سطرية: هكذا يقول Codex «يحتاج تصحيحًا»
FINDINGS_STATE = "COMMENTED_WITH_FINDINGS"
BLOCKING_STATES = frozenset({BLOCKING_STATE, FINDINGS_STATE})
API = "https://api.github.com"
# بوتٌ ← عبارةُ «لا ملاحظات» في تعليقه (Codex لا ينشر مراجعةً حين تنظف، ق٦٥)
CLEAN_REVIEW_COMMENTS = {"chatgpt-codex-connector[bot]": "Didn't find any major issues"}
REVIEWED_COMMIT = re.compile(r"Reviewed commit:\*\*\s*`([0-9a-f]{7,40})`")


class ReviewError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(code if not detail else f"{code}: {detail}")


def load_reviewers(raw: str | bytes) -> dict:
    """يقرأ خريطة المراجِعين ويرفض ما يوسّع الثقة: غيرَ البوتات، أو حالةً حاجبةً تُحتسب."""
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ReviewError("reviewers_unreadable") from exc
    if not isinstance(data, dict) or set(data) != {"schema_version", "reviewers", "counted_states", "limits"} \
            or data["schema_version"] != 1:
        raise ReviewError("reviewers_schema_invalid")
    reviewers = data["reviewers"]
    if not isinstance(reviewers, dict) or not reviewers:
        raise ReviewError("reviewers_schema_invalid")
    for login, family in reviewers.items():
        if not isinstance(login, str) or not login.endswith("[bot]"):
            raise ReviewError("reviewer_not_a_bot", str(login))
        if not isinstance(family, str) or not family or "/" in family or family == "human":
            raise ReviewError("reviewers_schema_invalid", str(login))
    states = data["counted_states"]
    if not isinstance(states, list) or not states or not set(states) <= ALLOWED_STATES:
        raise ReviewError("counted_state_not_allowed", str(states))
    return data


def author_families(commits: list[dict], registry: dict) -> set[str]:
    """عائلاتُ الإيداعات من ذيل النسبة (ما قبل «/»)؛ الإيداعُ بلا ذيلٍ شأنُ أداة النسبة.

    والفشلُ مغلق (ملاحظة Codex على #297): ذيلٌ يسمّي هويّةً لا يعرفها السجلُّ الموثوق، أو أكثرُ من هويّة، رفضٌ مسمًّى لا
    إيداعٌ يُسقَط؛ ومدًى بلا هويّةٍ معروفةٍ واحدة رفضٌ كذلك. فالعائلاتُ الفارغة كانت تجعل كلَّ بوتٍ مدرَجٍ «عائلةً أخرى»، ومنه
    بوتُ عائلة المؤلّف الذي سجّل نفسَه في الطلب باسمٍ جديد."""
    families = set()
    for commit in commits:
        values = parse_trailers(commit["message"]).get(registry["trailer"], [])
        if not values:
            continue
        if len(values) != 1 or values[0] not in registry["agents"]:
            raise ReviewError("author_not_in_trusted_registry", ", ".join(values)[:120])
        families.add(values[0].split("/", 1)[0])
    if not families:
        raise ReviewError("no_attributed_author")
    return families


def evaluate(families: set[str], reviews: list[dict], head: str, reviewers: dict,
             threads: list[dict] | None = None) -> dict:
    """الحكم: مراجِعٌ محتسَبٌ من عائلةٍ أخرى على الرأس بلا ملاحظات، ولا مراجِعَ محتسَبًا يحجب، ولا خيطَ مفتوحًا من عائلةٍ أخرى.

    `threads` خيوطُ المراجعة بالشكل {"resolved": bool, "author": login}؛ و`None` يعني أنها لم تُقرأ، ويُعلَن ذلك في التقرير."""
    mapping = reviewers["reviewers"]
    counted_states = set(reviewers["counted_states"])
    latest: dict[str, str] = {}
    ignored = []
    for review in reviews:                                # بترتيب GitHub: الأقدمُ أولًا
        login = (review.get("user") or {}).get("login", "")
        if login not in mapping:
            ignored.append({"login": login, "reason": "not_a_listed_bot"})
            continue
        if review.get("commit_id") != head:
            ignored.append({"login": login, "reason": "stale_head"})
            continue
        state = review.get("state", "")
        if state == "COMMENTED" and review.get("inline_comments", 0) > 0:
            state = FINDINGS_STATE
        latest[login] = state
    blocking = sorted(login for login, state in latest.items()
                      if state in BLOCKING_STATES and mapping[login] not in families)
    unresolved = sum(1 for thread in threads or ()
                     if not thread.get("resolved")
                     and mapping.get(thread.get("author", "")) not in (None, *families))
    counted = sorted(login for login, state in latest.items()
                     if state in counted_states and mapping[login] not in families)
    same_family = sorted(login for login in latest if mapping[login] in families)
    if blocking:
        status, code = "failed", ("changes_requested_by_another_family"
                                  if any(latest[login] == BLOCKING_STATE for login in blocking)
                                  else "findings_from_another_family_on_head")
    elif unresolved:
        status, code = "failed", "unresolved_threads_from_another_family"
    elif counted:
        status, code = "passed", "reviewed_by_another_family"
    elif same_family:
        status, code = "failed", "reviewed_only_by_the_author_family"
    else:
        status, code = "failed", "no_review_from_another_family"
    return {"schema_version": 1, "status": status, "code": code, "head": head,
            "author_families": sorted(families), "counted": counted, "blocking": blocking,
            "same_family": same_family, "unresolved_threads": unresolved, "threads_checked": threads is not None,
            "ignored": ignored, "limits": reviewers["limits"]}


def clean_comment_reviews(comments: list[dict], head: str) -> list[dict]:
    """تعليقاتُ «لا ملاحظات» من بوتٍ مدرَج مراجعاتٌ بحالة COMMENTED على الإيداع الذي تسمّيه (الأقدمُ أولًا)."""
    reviews = []
    for comment in comments:
        login = (comment.get("user") or {}).get("login", "")
        marker = CLEAN_REVIEW_COMMENTS.get(login)
        body = comment.get("body") or ""
        match = REVIEWED_COMMIT.search(body)
        if not marker or marker not in body or not match:
            continue
        short = match.group(1)
        reviews.append({"user": {"login": login}, "state": "COMMENTED", "submitted_at": comment.get("updated_at") or comment.get("created_at", ""),
                        "commit_id": head if head.startswith(short) else short, "source": "clean_comment"})
    return reviews


def _get_all(url: str, token: str | None) -> list[dict]:
    items: list[dict] = []
    page = 1
    while True:
        request = urllib.request.Request(f"{url}?per_page=100&page={page}",
                                         headers={"Accept": "application/vnd.github+json",
                                                  **({"Authorization": f"Bearer {token}"} if token else {})})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 — واجهةُ GitHub المعلنة
                batch = json.loads(response.read().decode("utf-8"))
        except OSError as exc:
            raise ReviewError("reviews_unreachable", type(exc).__name__) from exc
        if not isinstance(batch, list):
            raise ReviewError("reviews_malformed")
        items.extend(batch)
        if len(batch) < 100:
            return items
        page += 1


def with_inline_counts(reviews: list[dict], review_comments: list[dict]) -> list[dict]:
    """يضع على كل مراجعةٍ عددَ ملاحظاتها السطرية (من تعليقات المراجعة بمعرّفها)."""
    counts: dict[object, int] = {}
    for comment in review_comments:
        key = comment.get("pull_request_review_id")
        counts[key] = counts.get(key, 0) + 1
    return [dict(review, inline_comments=counts.get(review.get("id"), 0)) for review in reviews]


THREADS_QUERY = """query($owner:String!,$name:String!,$pr:Int!,$after:String){repository(owner:$owner,name:$name){
pullRequest(number:$pr){reviewThreads(first:100,after:$after){pageInfo{hasNextPage endCursor}
nodes{isResolved comments(first:1){nodes{author{__typename login}}}}}}}}"""


def fetch_threads(repo_slug: str, pr: int, token: str | None) -> list[dict]:
    """خيوطُ المراجعة وحالةُ حلّها من GraphQL (لا تعطيها REST). والإخفاقُ مغلق: خطأٌ مسمًّى لا قائمةٌ فارغة."""
    owner, name = repo_slug.split("/", 1)
    threads: list[dict] = []
    after = None
    while True:
        body = json.dumps({"query": THREADS_QUERY,
                           "variables": {"owner": owner, "name": name, "pr": pr, "after": after}}).encode()
        request = urllib.request.Request(f"{API}/graphql", data=body, method="POST",
                                         headers={"Accept": "application/vnd.github+json",
                                                  **({"Authorization": f"Bearer {token}"} if token else {})})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 — واجهةُ GitHub المعلنة
                data = json.loads(response.read().decode("utf-8"))
        except OSError as exc:
            raise ReviewError("threads_unreachable", type(exc).__name__) from exc
        if not isinstance(data, dict) or data.get("errors"):
            # GraphQL قد يعيد بياناتٍ جزئية مع أخطاء؛ والجزئيُّ قد يُسقط خيطًا مفتوحًا، فيُرفض كلُّه (ملاحظة Codex P1 على #372)
            raise ReviewError("threads_partial" if isinstance(data, dict) and data.get("data") else "threads_malformed")
        try:
            page = data["data"]["repository"]["pullRequest"]["reviewThreads"]
        except (KeyError, TypeError) as exc:
            raise ReviewError("threads_malformed") from exc
        threads.extend(thread_rows(page["nodes"]))
        if not page["pageInfo"]["hasNextPage"]:
            return threads
        after = page["pageInfo"]["endCursor"]


def thread_rows(nodes: list[dict]) -> list[dict]:
    """صفُّ الخيط: محلولٌ أم لا، وكاتبُ أول تعليقٍ فيه. وGraphQL يسمّي البوتَ بلا «[bot]» فيُعاد إليه."""
    rows = []
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("isResolved"), bool):
            raise ReviewError("thread_incomplete")
        first = ((node.get("comments") or {}).get("nodes") or [{}])[0]
        author = first.get("author") or {}
        login = author.get("login", "")
        if not node["isResolved"] and not login:
            # خيطٌ مفتوح بلا كاتبٍ مقروء لا يُعرف أمِن عائلةٍ أخرى هو، فلا يُفترض أنه لا يحجب
            raise ReviewError("thread_incomplete")
        if author.get("__typename") == "Bot" and not login.endswith("[bot]"):
            login += "[bot]"
        rows.append({"resolved": bool(node.get("isResolved")), "author": login})
    return rows


def fetch_reviews(repo_slug: str, pr: int, token: str | None, head: str = "") -> list[dict]:
    """مراجعاتُ الطلب بعدد ملاحظاتها السطرية، وتعليقاتُ المراجعة النظيفة، من واجهة GitHub بالرمز الذي يعطيه Actions (قراءةٌ فقط)."""
    reviews = with_inline_counts(_get_all(f"{API}/repos/{repo_slug}/pulls/{pr}/reviews", token),
                                 _get_all(f"{API}/repos/{repo_slug}/pulls/{pr}/comments", token))
    comments = _get_all(f"{API}/repos/{repo_slug}/issues/{pr}/comments", token)
    return chronological(reviews + clean_comment_reviews(comments, head))


def chronological(reviews: list[dict]) -> list[dict]:
    """المراجعاتُ والتعليقاتُ النظيفة مسارٌ واحدٌ بزمن GitHub، فيبقى «الأخير» أخيرًا (ترتيبٌ مستقرّ لما بلا زمن)."""
    return sorted(reviews, key=lambda r: r.get("submitted_at") or "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--range", required=True, help="مدى إيداعات الطلب، مثل BASE..HEAD")
    parser.add_argument("--head", required=True, help="بصمةُ رأس الطلب الحاليّ")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pr", type=int, help="رقمُ الطلب (يقرأ المراجعات من GitHub)")
    source.add_argument("--reviews-json", type=Path, help="ملفُّ مراجعاتٍ بصيغة GitHub (بلا شبكة)")
    parser.add_argument("--repo-slug", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--repo", type=Path, default=ROOT, help="نسخةُ الطلب: يُقرأ منها تاريخُ git وحده")
    parser.add_argument("--wait-seconds", type=int, default=0, help="مع --pr: انتظر مراجِعًا محتسبًا حتى هذه المدّة")
    args = parser.parse_args(argv)
    try:
        # السجلّان من نسخة الأداة الموثوقة لا من الطلب، فلا يوسّع طلبٌ الثقةَ في حكمه على نفسه
        registry = load_registry((ROOT / REGISTRY_PATH).read_bytes())
        reviewers = load_reviewers((ROOT / REVIEWERS_PATH).read_bytes())
        families = author_families(read_commits(args.repo, args.range), registry)
        threads = None
        if args.reviews_json is not None:
            raw = json.loads(args.reviews_json.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                reviews = chronological(with_inline_counts(raw["reviews"], raw.get("review_comments", []))
                                        + clean_comment_reviews(raw["comments"], args.head))
                threads = raw.get("threads")
            else:
                reviews = raw
        else:
            if not args.repo_slug:
                raise ReviewError("repo_slug_missing")
            token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
            deadline = time.monotonic() + max(0, args.wait_seconds)
            while True:
                report = evaluate(families, fetch_reviews(args.repo_slug, args.pr, token, args.head), args.head, reviewers,
                                  fetch_threads(args.repo_slug, args.pr, token))
                if report["status"] == "passed" or report["blocking"] or report["unresolved_threads"] \
                        or time.monotonic() >= deadline:
                    break
                time.sleep(30)
            reviews = None
        if reviews is not None:
            report = evaluate(families, reviews, args.head, reviewers, threads)
    except (ReviewError, AttributionError, OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "code": getattr(exc, "code", type(exc).__name__),
                          "detail": str(exc)[:200]}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
