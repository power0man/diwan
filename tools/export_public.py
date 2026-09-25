#!/usr/bin/env python3
"""مصدِّرُ اللقطة العامة: يبني من المستودع الخاص شجرةً تصلح للنشر، وملفاتُ المالك خارجها.

القاعدة (ك٢٧، بقرار المالك في ٢٥ سبتمبر ٢٠٢٦): «كلُّ ملفات المالك تبقى في Drive،
حتى اللوائح والأنظمة، لأن بعضها مسوداتٌ غير منشورة». فاللقطةُ العامة تستبعد المتونَ
ومشتقّاتِها كلَّها (`EXCLUDED_PATHS`) إلا ما ضمّنه المالكُ بقرارٍ مسمًّى (`INCLUDED_PATHS`، ق٥٨)،
ثم **تُثبت** الاستبعادَ بالبصمة لا بالاسم:
كلُّ ملفٍّ نصّي في اللقطة يُمسح بحثًا عن أيّ اثنتي عشرة كلمةً متتاليةً من نصوص المتون
والمسرد، وعن عناوين وثائق المتن، وعن البيانات الشخصية والأسرار، وعن محتوًى محجوب.
وأيُّ إصابةٍ تُسقط اللقطةَ كلَّها برمزٍ مسمًّى **ولا يُكتب شيء**.

الاستعمال:
    python tools/export_public.py <المسار الهدف> [--report تقرير.json]

ثم يُنشأ المستودعُ العام من الشجرة المصدَّرة بتاريخٍ جديد (`git init`)، فلا يحمل
تاريخَ المستودع الخاص الذي مرّت فيه ملفاتُ المالك.

**الحدُّ المعلَن:** البصمةُ تلتقط الاقتباسَ الحرفيَّ الطويل والعناوينَ، لا إعادةَ
الصياغة ولا الاقتباسَ الأقصر من اثنتي عشرة كلمة (`tests/test_export_public.py`
يسمّي هذا الحدّ اختبارًا). والمضمَّنُ بقرارٍ يخرج من البصمة ومن المسح معًا: ما قرّر
المالكُ نشرَه ليس تسرّبًا، والحارسُ يُثبت أن ما خرج هو ما قُرّر لا أكثر.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.public_export import MARKER_KIND, MARKER_NAME  # noqa: E402
from core.signing import SIGNED_LEDGERS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# ما لا يدخل اللقطةَ العامة. المجلّدُ ينتهي بـ«/» فيُستبعد بما تحته؛ والملفُّ بمساره.
EXCLUDED_PATHS: tuple[str, ...] = (
    "corpus/",                                       # المتون: اللوائحُ (وفيها مسودات) والمعاجمُ وفهارسُها
    "glossaries/",                                   # المسرد: تعريفاتٌ حرفية بشواهدها من اللوائح
    "sources/",                                      # سجلُّ المصادر: أسماءُ ملفات المالك ووصفُ تصديره
    "publish/",                                      # بيانُ النشر: مساراتُ الوثائق ومواضعُها
    "evaluation/suites/benchmark_m14.json",          # حقائقُ ذهبية تقتبس اللوائح
    "nodes/maritime/evidence/golden-answers.json",   # أجوبةٌ ذهبية تقتبس اللوائح
    "services/evidence/research-m6.json",            # شواهدُ بحثٍ تقتبس اللوائح
    "nodes/linguistics/evidence/roots-20.json",      # شواهدُ جذورٍ تقتبس مداخلَ المعاجم
    "docs/probe/m14-human-sample-review.json",       # عيّنةُ مراجعةٍ تحمل أجوبةً مقتبسة
    "docs/probe/policy-library-index.md",            # فهرسُ مكتبة اللوائح على Drive
    "docs/probe/drive-library-index.md",             # فهرسُ Drive بمعرّفات مجلداته
    "tools/build_benchmark_suite.py",                # يحمل نصوصَ اللوائح حرفيًّا
    "tools/seed_acquisitions.py",                    # يحمل أسماءَ وثائق التصدير
)

# مصادرُ البصمة: كلُّ نصٍّ في هذه المسارات يُبصَم، لا أسماؤها وحدها.
FINGERPRINT_SOURCES: tuple[str, ...] = ("corpus/", "glossaries/")

# ما يُضمَّن رغم وقوعه تحت مسارٍ مستبعَد، بقرارٍ مسمًّى من المالك (ق٥٨): يخرج من الاستبعاد ومن
# بصمات النصّ الخاص ومن المسح. القاموسُ المحيط (الفيروزآبادي) ملكٌ عام؛ والمعجمُ الوسيط
# وفهرسُ المعاجم يبقيان مع ملفات المالك.
INCLUDED_PATHS: tuple[str, ...] = (
    "corpus/lexicons/qamus-muhit.jsonl",
    "corpus/lexicons/qamus-muhit.jsonl.anchor",
)
SHINGLE_WORDS = 12
MIN_TITLE_WORDS = 3
SEALED_MANIFEST = "MANIFEST.json"

_TASHKEEL = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")
_WORD = re.compile(r"\w+")
_TITLE_INDEX = re.compile(r"^\d+__")
_TITLE_VERSION = re.compile(r"(?:__v\d+)?\.jsonl$")
PERSONAL_PATTERNS: dict[str, re.Pattern[str]] = {
    "personal_email": re.compile(r"[\w.+-]+@(?:gmail|googlemail|icloud|hotmail|outlook|yahoo|live)\.com", re.I),
    "home_path": re.compile(r"/Users/[A-Za-z0-9._-]+"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY"),
    "token": re.compile(r"(?<![A-Za-z0-9])(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{20,}"
                        r"|(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}"
                        r"|(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}(?![A-Za-z0-9])"),
}
_DRIVE_ID = re.compile(r"(?<![A-Za-z0-9_-])1[A-Za-z0-9_-]{32}(?![A-Za-z0-9_-])")

REFUSAL_ORDER = ("sealed_content", "private_text", "private_title", "personal_data")


def words(text: str) -> list[str]:
    """كلماتٌ بلا تشكيلٍ ولا تطويل، والشرطةُ السفلية فاصلٌ (أسماءُ الوثائق تُبنى بها)."""
    return _WORD.findall(_TASHKEEL.sub("", text.replace("_", " ")).casefold())


def title_of(name: str) -> str:
    """عنوانُ الوثيقة من اسم ملفها: بلا رقم الترتيب ولا النسخة ولا الامتداد."""
    return " ".join(words(_TITLE_VERSION.sub("", _TITLE_INDEX.sub("", name)).replace("-", " ")))


def _flatten(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _flatten(item)
    elif isinstance(value, list):
        for item in value:
            yield from _flatten(item)


def is_included(rel: str) -> bool:
    return rel in INCLUDED_PATHS


def is_excluded(rel: str) -> bool:
    if is_included(rel):
        return False
    for pattern in EXCLUDED_PATHS:
        if pattern.endswith("/"):
            if rel.startswith(pattern):
                return True
        elif rel == pattern:
            return True
    return False


def excluded_signed_ledgers() -> tuple[str, ...]:
    """السجلاتُ الحاكمة (`core.signing.SIGNED_LEDGERS`) التي تخرج من اللقطة."""
    return tuple(sorted(rel for rel in SIGNED_LEDGERS if is_excluded(rel)))


def tracked_files(root: Path) -> list[str]:
    """ملفاتُ git المتتبَّعة وحدها: ما ليس في المستودع لا يُصدَّر ولو كان على القرص."""
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], check=True,
                         capture_output=True).stdout
    return [rel for rel in out.decode("utf-8").split("\0") if rel]


@dataclass(frozen=True)
class Fingerprints:
    shingles: frozenset[int]
    titles: tuple[str, ...]
    sources: tuple[str, ...]


def private_fingerprints(root: Path, tracked: list[str]) -> Fingerprints:
    """بصماتُ النصّ الخاص: كلُّ نافذةٍ من `SHINGLE_WORDS` كلمةً، وعناوينُ الوثائق."""
    shingles: set[int] = set()
    titles: set[str] = set()
    sources: list[str] = []
    for rel in tracked:
        if not any(rel.startswith(src) for src in FINGERPRINT_SOURCES) or is_included(rel):
            continue                       # المضمَّنُ بقرارٍ نصٌّ عام لا خاص
        name = Path(rel).name
        if not name.endswith(".jsonl"):
            continue                       # المراسي والتواقيع بصماتٌ لا نصوص
        if rel.startswith("corpus/") and not name.startswith("_"):
            title = title_of(name)
            if len(title.split()) >= MIN_TITLE_WORDS:
                titles.add(title)
        sources.append(rel)
        with (root / rel).open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if name == "_catalog.jsonl" and isinstance(record, dict):
                    doc_id = (record.get("record") or {}).get("doc_id")
                    if isinstance(doc_id, str):
                        title = title_of(doc_id + ".jsonl")
                        if len(title.split()) >= MIN_TITLE_WORDS:
                            titles.add(title)
                for text in _flatten(record):
                    tokens = words(text)
                    for i in range(len(tokens) - SHINGLE_WORDS + 1):
                        shingles.add(hash(tuple(tokens[i:i + SHINGLE_WORDS])))
    return Fingerprints(frozenset(shingles), tuple(sorted(titles)), tuple(sources))


@dataclass(frozen=True)
class Finding:
    path: str
    kind: str
    excerpt: str
    count: int = 1

    def as_dict(self) -> dict:
        return {"path": self.path, "kind": self.kind, "excerpt": self.excerpt, "count": self.count}


def _drive_ids(text: str):
    """معرّفُ مجلّد Drive: ٣٣ محرفًا يبدأ بـ1 ويخلط الحالتين — فلا بصمةٌ سداسية عشرية
    (بلا أحرفٍ كبيرة) ولا وسمُ عجلةٍ مثل `1-cp311-cp311-macosx_10_15_x86_64`."""
    for match in _DRIVE_ID.finditer(text):
        token = match.group(0)
        if sum(ch.isupper() for ch in token) >= 3 and sum(ch.islower() for ch in token) >= 3:
            yield token


def scan_path(rel: str, text: str | None, fingerprints: Fingerprints) -> list[Finding]:
    """كلُّ ما يمنع خروجَ هذا الملفّ في اللقطة، مسمًّى بنوعه."""
    findings: list[Finding] = []
    parts = Path(rel).parts
    if any(part.casefold() == "sealed" for part in parts[:-1]) and parts[-1] != SEALED_MANIFEST:
        findings.append(Finding(rel, "sealed_content", rel))
    if text is None:
        return findings
    tokens = words(text)
    hits = 0
    first = ""
    for i in range(len(tokens) - SHINGLE_WORDS + 1):
        if hash(tuple(tokens[i:i + SHINGLE_WORDS])) in fingerprints.shingles:
            hits += 1
            if not first:
                first = " ".join(tokens[i:i + SHINGLE_WORDS])
    if hits:
        findings.append(Finding(rel, "private_text", first, hits))
    joined = f" {' '.join(tokens)} "
    for title in fingerprints.titles:
        if f" {title} " in joined:
            findings.append(Finding(rel, "private_title", title))
            break
    for kind, pattern in PERSONAL_PATTERNS.items():
        match = pattern.search(text)
        if match:
            findings.append(Finding(rel, "personal_data", f"{kind}: {match.group(0)[:40]}"))
    for token in _drive_ids(text):
        findings.append(Finding(rel, "personal_data", f"drive_id: {token}"))
        break
    return findings


def _refusal_code(findings: list[Finding]) -> str:
    kinds = {f.kind for f in findings}
    for kind in REFUSAL_ORDER:
        if kind in kinds:
            return f"{kind}_in_export"
    return "refused"


def _source_commit(root: Path) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                             capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.decode("ascii").strip() or None


def regenerate_docs(dest: Path) -> dict | None:
    """الأرقامُ المولَّدة في الوثائق (عددُ الاختبارات، وملفّاتُ بايثون وسطورُها…) تُعاد
    اشتقاقُها داخل اللقطة بأداة المستودع نفسِها، فتصف اللقطةَ لا المستودعَ الخاص ويمرّ
    `check_docs --check` فيها. لا يُستدعى في الجذور المصغّرة التي بلا أداة."""
    tool = dest / "tools" / "check_docs.py"
    if not tool.is_file():
        return None
    result = subprocess.run([sys.executable, str(tool), "--write"], cwd=dest, capture_output=True, text=True)
    return {"returncode": result.returncode, "output": (result.stdout or result.stderr)[-400:]}


def build(root: Path, dest: Path, *, tracked: list[str] | None = None) -> dict:
    """يمسح أولًا ثم ينسخ: لا يُكتب ملفٌّ واحد ما لم تخلُ اللقطةُ كلُّها من الإصابات."""
    root = Path(root).resolve()
    dest = Path(dest).resolve()          # نسبيٌّ كان يُضاعَف عند تشغيل check_docs بـcwd=dest
    tracked = list(tracked_files(root) if tracked is None else tracked)
    if dest.exists() and any(dest.iterdir()):
        return {"status": "refused", "code": "dest_not_empty", "dest": str(dest)}
    fingerprints = private_fingerprints(root, tracked)
    kept = [rel for rel in tracked if not is_excluded(rel)]
    excluded = [rel for rel in tracked if is_excluded(rel)]
    findings: list[Finding] = []
    for rel in kept:
        if is_included(rel):
            continue                       # ما ضمّنه المالك بقرارٍ لا يُمسح: ليس تسرّبًا
        data = (root / rel).read_bytes()
        try:
            text: str | None = data.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        findings.extend(scan_path(rel, text, fingerprints))
    report = {
        "status": "refused" if findings else "exported",
        "source_commit": _source_commit(root),
        "files_exported": 0 if findings else len(kept),
        "files_excluded": len(excluded),
        "fingerprints": {"shingle_words": SHINGLE_WORDS, "shingles": len(fingerprints.shingles),
                         "titles": len(fingerprints.titles), "sources": len(fingerprints.sources)},
        "findings": [f.as_dict() for f in findings],
    }
    if findings:
        report["code"] = _refusal_code(findings)
        return report
    dest.mkdir(parents=True, exist_ok=True)
    for rel in kept:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, target)
    marker = {
        "schema_version": 1,
        "kind": MARKER_KIND,
        "source_commit": report["source_commit"],
        "exported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "excluded_paths": list(EXCLUDED_PATHS),
        "included_paths": list(INCLUDED_PATHS),
        "excluded_streams": list(excluded_signed_ledgers()),
        "files_exported": len(kept),
        "files_excluded": len(excluded),
        "fingerprints": dict(report["fingerprints"], sources=list(fingerprints.sources)),
    }
    (dest / MARKER_NAME).write_text(json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8")
    regenerated = regenerate_docs(dest)
    if regenerated is not None and regenerated["returncode"] != 0:
        shutil.rmtree(dest)
        report.update(status="refused", code="docs_regeneration_failed", files_exported=0,
                      docs=regenerated)
        return report
    report["dest"] = str(dest)
    report["marker"] = MARKER_NAME
    report["docs_regenerated"] = regenerated is not None
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dest", help="مجلّدٌ فارغ أو غير موجود تُكتب فيه اللقطة")
    parser.add_argument("--root", default=str(ROOT), help="جذرُ المستودع الخاص (الافتراضي: هذا المستودع)")
    parser.add_argument("--report", help="مسارُ تقرير JSON يُكتب في الحالين")
    args = parser.parse_args(argv)
    report = build(Path(args.root), Path(args.dest))
    if args.report:
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shown = dict(report)
    shown["findings"] = report.get("findings", [])[:50]
    print(json.dumps(shown, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "exported" else 2


if __name__ == "__main__":
    raise SystemExit(main())
