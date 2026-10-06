#!/usr/bin/env python3
"""لا يضع قالبُ مسألةٍ وسمَ عائلةٍ عاملة ولا وسمَ إذنٍ تلقائيًّا (البندان ٤ و٦ من #296؛ docs/AGENT-INTAKE.md).

وسمُ `family:<عائلة>` توجيهٌ لا إذن، والإذنُ `ready:<عائلة>` يضعه المالك وحده (docs/PLAN-20260926.md). ومن لا صلاحيةَ له في
مستودعٍ عام لا يضع وسمًا إلا ما تضعه القوالبُ تلقائيًّا، فالقوالبُ حدُّ الاستلام. والوسومُ تُقرأ بصورتها المضمَّنة وحدها (`labels: [...]`)، في نموذج YAML أو في رأس قالب Markdown؛ وصورةٌ
غيرُها تُرفض باسمها: يُغلق عند الشكّ.

وحارسُ الإطلاق (`launch`، البند ٤ من #296) يجري في `.github/workflows/intake-gate.yml` على أحداث المسائل: وسمٌ يُطلق وكيلًا
آليًّا بذاته (`AGENT_TRIGGERS`؛ اليوم `jules`) لا يبقى على مسألةٍ إلا إن كان آخرُ حدثٍ على `ready:<عائلته>` وضعًا فاعلُه المالك،
ونصُّ المسألة يمرّ بـ`quarantine_quoted` بلا موجود (وبـ`scan` كلِّه إن لم يكتبها المالك). وإلا يُنزع الوسمُ ويُعلَّق بالرموز.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.quoted import quarantine_quoted, scan  # noqa: E402
TEMPLATES = ROOT / ".github" / "ISSUE_TEMPLATE"
LABELS_LINE = re.compile(r"^labels:[ \t]*\[(?P<items>[^\]\n]*)\][ \t]*(?:#.*)?$")
NON_WORKING = frozenset({"family:owner"})


# مفتاحُ `labels` في أيّ موضعٍ من السطر: بإزاحةٍ، أو داخل خريطةٍ مضمَّنة `--- {labels: [...]}` (ملاحظة Codex على #304)؛
# وسطرٌ يبدأ خريطةً مضمَّنة أو مفتاحًا مركّبًا (`? labels`) صورةٌ لا تُقرأ سطرًا سطرًا.
LABELS_KEY = re.compile(r"""(?:^|[\s{,])['"]?labels['"]?[ \t]*:""")
UNREADABLE_STARTS = ("{", "?")
# مفتاحٌ مقتبسٌ بتهريبٍ (`"labe\u006cs":`) يُفكّ إلى labels ولا يُرى في نصّه، والمرساةُ (`&k`) تجعل اسمَها المستعار
# (`*k :`) مفتاحًا لا يُرى؛ فكلاهما صورةٌ لا تُقرأ (ملاحظة Codex على #304).
ESCAPED_KEY = re.compile(r'"(?:[^"\\\n]|\\.)*\\.(?:[^"\\\n]|\\.)*"[ \t]*:')
ANCHOR = re.compile(r"(?:^|[\s\[{,])&[^\s,\[\]{}]")
BLANK_KEY = re.compile(r"""(?:^|[\s{,])['"]?blank_issues_enabled['"]?[ \t]*:""")
BLANK_DISABLED = re.compile(r"^blank_issues_enabled:[ \t]*false[ \t]*$")


def unreadable_line(line: str) -> bool:
    """سطرٌ لا يُقرأ سطرًا سطرًا: خريطةٌ مضمَّنة أو مفتاحٌ مركّب، أو مقتبسٌ بتهريبٍ يليه «:»، أو مرساة."""
    return line.lstrip().startswith(UNREADABLE_STARTS) or bool(ESCAPED_KEY.search(line)) or bool(ANCHOR.search(line))


def template_labels(text: str) -> list[str] | None:
    """وسومُ القالب من سطرها المضمَّن في العمود الأول؛ وكلُّ ذكرٍ آخر لمفتاح `labels` (مُزاحًا أو في خريطةٍ مضمَّنة)، وكلُّ
    خريطةٍ مضمَّنة أو مفتاحٍ مركّب (`?`)، يعيد None: صورةٌ لا تُقرأ، فيُغلق عند الشكّ."""
    found = None
    for line in text.splitlines():
        if unreadable_line(line):
            return None
        if not LABELS_KEY.search(line):
            continue
        match = LABELS_LINE.match(line)
        if match is None or found is not None:
            return None
        found = [json.loads(item) for item in re.findall(r'"[^"\n]*"', match["items"])]
        if len(found) != len([part for part in match["items"].split(",") if part.strip()]):
            return None
    return found or []


def blank_issues_disabled(config: str) -> bool:
    """المسائلُ الفارغة معطّلةٌ بسطرٍ واحدٍ في العمود الأول هو كلُّ ذكرٍ لمفتاحها، وقيمتُه `false`. فسطرٌ داخل نصٍّ مقتبسٍ
    متعدّد الأسطر لا يغلب المفتاحَ الفعليّ، ولا تُقبل صورةٌ لا تُقرأ سطرًا سطرًا (ملاحظة Codex على #304)."""
    found = []
    for line in config.removeprefix("\ufeff").splitlines():
        if unreadable_line(line):
            return False
        if BLANK_KEY.search(line):
            found.append(line)
    return len(found) == 1 and bool(BLANK_DISABLED.match(found[0]))


def front_matter(text: str) -> str | None:
    """رأسُ قالب Markdown بين سطرَي `---` في أوّله، ومنه وحده تضع GitHub الوسوم؛ وبلا رأسٍ فلا وسوم. ورأسٌ لا يُغلق
    None (صورةٌ لا تُقرأ)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for index, line in enumerate(lines[1:], 1):
        # الفاصلُ الخاتم في العمود الأول وحده؛ و`---` مُزاحٌ نصٌّ داخل قيمةٍ كتليّة (ملاحظة Codex على #304)
        if line.rstrip() == "---":
            return "\n".join(lines[1:index])
    return None


def intake_findings(templates: dict[str, str], config: str) -> list[str]:
    problems = []
    for name, text in sorted(templates.items()):
        text = text.removeprefix("\ufeff")   # علامةُ ترتيب البايتات يُسقطها مفسّرُ YAML، فلا تُخفي ما بعدها
        if name.endswith(".md"):
            text = front_matter(text)
        labels = None if text is None else template_labels(text)
        if labels is None:
            problems.append(f"labels_unreadable:{name}")
            continue
        # GitHub تطابق أسماءَ الوسوم بلا اعتبارٍ لحالة الأحرف، فـ`Family:OpenAI` يضع `family:openai` (ملاحظة Codex على #304)
        problems += [f"working_family_label:{name}:{label}" for label in labels
                     if label.strip().casefold().startswith(("family:", "ready:"))
                     and label.strip().casefold() not in NON_WORKING]
    if not blank_issues_disabled(config):
        problems.append("blank_issues_enabled")
    return problems


def published() -> tuple[dict[str, str], str]:
    """كلُّ ملفٍّ في مجلّد القوالب سوى إعداده: نماذجُ YAML (`.yml` و`.yaml`) وقوالبُ Markdown (`.md`) كلّها تضع وسومًا، فلا
    يُفلت امتدادٌ من الفحص (ملاحظة Codex على #304)."""
    templates = {p.name: p.read_text(encoding="utf-8") for p in sorted(TEMPLATES.iterdir())
                 if p.is_file() and p.name not in {"config.yml", "config.yaml"}}
    return templates, (TEMPLATES / "config.yml").read_text(encoding="utf-8")


# حارسُ الإطلاق ---------------------------------------------------------------------------------------------------

OWNER = "power0man"
# وسومٌ يلتقطها وكيلٌ آليٌّ فيبدأ بها، وعائلةُ كلٍّ منها: Jules يبدأ من وسم jules على مسألةٍ موسومة ready:google
# (docs/PLAN-20260926.md، قسم Jules). وCodex يُستدعى بذكره لا بوسم، فلا يحكمه هذا الجدول (docs/AGENT-INTAKE.md §الحدود).
AGENT_TRIGGERS = {"jules": "google"}


def _fold(name: object) -> str:
    return str(name or "").strip().casefold()


def ready_granted(events: list[dict], family: str, owner: str = OWNER) -> bool:
    """آخرُ حدثٍ على وسم `ready:<family>` في خطّ المسألة الزمني وضعٌ فاعلُه المالك؛ فنزعُه بعد الوضع، أو وضعُه بيد غيره، لا يُحتسب."""
    wanted = f"ready:{family}"
    last = None
    for event in events:
        if event.get("event") in ("labeled", "unlabeled") and _fold((event.get("label") or {}).get("name")) == wanted:
            last = event
    return last is not None and last["event"] == "labeled" and (last.get("actor") or {}).get("login") == owner


def text_findings(issue: dict, owner: str = OWNER) -> list[str]:
    """رموزُ الحقن في عنوان المسألة ونصِّها. نصُّ المالك يُقرأ بـ`quarantine_quoted` (ما خارج الاقتباس كلامُه)، ونصُّ غيره
    بياناتٌ كلُّه فيُفحص بـ`scan` كلِّه أيضًا."""
    text = f"{issue.get('title') or ''}\n{issue.get('body') or ''}"
    found = list(quarantine_quoted(text).findings)
    if (issue.get("user") or {}).get("login") != owner:
        found += scan(text)
    return sorted({f.code for f in found})


def launch_decision(issue: dict, events: list[dict], owner: str = OWNER) -> list[dict]:
    """كلُّ وسمِ إطلاقٍ على المسألة لا يحقّ له البقاء، بأسباب رفضه؛ والقائمةُ الفارغة أنْ لا شيءَ يُنزع."""
    present = {_fold(label.get("name")): label.get("name") for label in issue.get("labels") or []}
    blocked = []
    for trigger, family in sorted(AGENT_TRIGGERS.items()):
        if trigger not in present:
            continue
        reasons = []
        if f"ready:{family}" not in present or not ready_granted(events, family, owner):
            reasons.append(f"ready_not_granted_by_owner:ready:{family}")
        reasons += [f"injection_in_issue_text:{code}" for code in text_findings(issue, owner)]
        if reasons:
            blocked.append({"label": present[trigger], "family": family, "reasons": reasons})
    return blocked


def payload_event(payload: dict) -> list[dict]:
    """حدثُ الوسم الذي أطلق هذا التشغيل من الحمولة نفسِها، فلا يفوت لتأخّر واجهة الأحداث عنه."""
    label = payload.get("label")
    if payload.get("action") not in ("labeled", "unlabeled") or not label:
        return []
    return [{"event": payload["action"], "label": label, "actor": payload.get("sender") or {}}]


def merge_payload_event(events: list[dict], payload: dict) -> tuple[list[dict], bool]:
    """يعيد (خطَّ الأحداث، هل لقطةُ الحمولة قديمة).

    القاعدةُ الوحيدةُ التي لا تخمّن: طوابعُ GitHub بدقّة الثانية، فحدثٌ على الوسم نفسِه طابعُه **بعد** ثانيةِ الحمولة
    (`created_at` > `issue.updated_at`) أحدثُ منها قطعًا؛ عندها اللقطةُ قديمة، فلا يُلحق حدثُها وتُقرأ المسألةُ من المصدر.
    وما سوى ذلك (لا طوابع، أو الثانيةُ نفسُها، أو خطٌّ متأخّر) يُلحق حدثُ الحمولة كما هو: **الاتجاهُ الآمن**، فنزعٌ يُحتسب
    ولو كان قديمًا، وإعادةُ إذنٍ في الثانية نفسِها قد تتطلّب من المالك إعادةَ وسم الإطلاق بيده. لا مطابقةَ لهويّة الحدث داخل
    الثانية ولا استدلالَ من لقطة الوسوم: كلاهما خمّن فأخطأ في اتجاهٍ مفتوح (ملاحظاتُ Codex على #346؛ فُرزت في #345)."""
    extra = payload_event(payload)
    if not extra:
        return events, False
    name = _fold((extra[0].get("label") or {}).get("name"))
    stamp = str((payload.get("issue") or {}).get("updated_at") or "")
    newer = [e for e in events if e.get("event") in ("labeled", "unlabeled")
             and _fold((e.get("label") or {}).get("name")) == name and str(e.get("created_at") or "") > stamp]
    if stamp and newer:
        return events, True
    return events + extra, False


class GitHub:
    """واجهةُ REST بالقدر اللازم: أحداثُ المسألة، ونزعُ وسم، وتعليق. والاختباراتُ تبدّلها بمزيَّف."""

    def __init__(self, repo: str, token: str):
        self.base = f"https://api.github.com/repos/{repo}/issues"
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28"}

    def _call(self, method: str, url: str, body: dict | None = None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(url, data=data, method=method, headers=self.headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
        return json.loads(raw) if raw else None

    def issue(self, number: int) -> dict:
        """المسألةُ الحاليةُ من المصدر (عنوانٌ ونصٌّ ووسوم) حين تكون لقطةُ الحمولة قديمة."""
        return self._call("GET", f"{self.base}/{number}") or {}

    def events(self, number: int) -> list[dict]:
        found: list[dict] = []
        for page in range(1, 11):
            batch = self._call("GET", f"{self.base}/{number}/events?per_page=100&page={page}")
            found += batch
            if len(batch) < 100:
                return found
        raise RuntimeError("issue_timeline_too_long")   # لا يُحكم على خطٍّ زمنيٍّ لم يُقرأ كلُّه

    def remove_label(self, number: int, name: str) -> None:
        self._call("DELETE", f"{self.base}/{number}/labels/{urllib.parse.quote(name, safe='')}")

    def comment(self, number: int, body: str) -> None:
        self._call("POST", f"{self.base}/{number}/comments", {"body": body})


def apply_launch_gate(payload: dict, client, owner: str = OWNER) -> int:
    """يَنزع كلَّ وسمِ إطلاقٍ لا يحقّ له البقاء، ويعلّق بالرموز وحدها (لا يُعاد نصُّ المسألة)، ويُحمرّ التشغيل."""
    issue = payload.get("issue") or {}
    if "pull_request" in issue:
        return 0
    if not any(_fold(label.get("name")) in AGENT_TRIGGERS for label in issue.get("labels") or []):
        print(json.dumps({"status": "passed", "blocked": []}))
        return 0
    number = int(issue["number"])
    events, stale = merge_payload_event(client.events(number), payload)
    if stale:
        # لقطةٌ قديمة: المسألةُ (نصًّا ووسومًا) من المصدر، **ثم** خطُّ الأحداث من جديد بعدها، فلا تُحكم وسومٌ حديثة بسجلٍّ قُرئ
        # قبلها (إذنٌ أعاده غيرُ المالك بين القراءتين؛ ملاحظة Codex السادسة على #346). ما سبق خطَّ الأحداث من تغيّرٍ يظهر فيه.
        issue = client.issue(number)
        events = client.events(number)
    blocked = launch_decision(issue, events, owner)
    for item in blocked:
        client.remove_label(number, item["label"])
    if blocked:
        lines = [f"- نُزع وسمُ `{item['label']}`: " + "، ".join(f"`{r}`" for r in item["reasons"]) for item in blocked]
        client.comment(number, "**حارسُ الاستلام (`tools/intake_gate.py`، docs/AGENT-INTAKE.md):** لا يُطلَق وكيلٌ من هذه "
                       "المسألة.\n\n" + "\n".join(lines) + "\n\nيُعاد الوسمُ بعد أن يضع المالك `ready:<العائلة>` "
                       "ويخلو النصُّ مما حُجر.")
    print(json.dumps({"status": "failed" if blocked else "passed", "blocked": blocked}, ensure_ascii=False))
    return 1 if blocked else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    launch = sub.add_parser("launch", help="حارسُ الإطلاق على حدث مسألة (يجري في intake-gate.yml)")
    launch.add_argument("--event", required=True, help="مسارُ حمولة الحدث (GITHUB_EVENT_PATH)")
    launch.add_argument("--repo", required=True)
    args = parser.parse_args(argv)
    if args.command == "launch":
        payload = json.loads(Path(args.event).read_text(encoding="utf-8"))
        return apply_launch_gate(payload, GitHub(args.repo, os.environ["GH_TOKEN"]))
    problems = intake_findings(*published())
    print(json.dumps({"status": "failed" if problems else "passed", "findings": problems}, ensure_ascii=False))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
