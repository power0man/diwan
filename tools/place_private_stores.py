#!/usr/bin/env python3
"""يضع مخازنَ المالك الخاصة في نسخة العمل من المستودع العام ويتحقّق منها، أو يرفعها (ك٢٩).

المستودعُ العام بلا متون (ق٥١، ق٥٢): اللوائحُ والمسردُ وسجلُّ المصادر وبيانُ النشر
ومشتقّاتُها تبقى في Drive وفي `diwan-private`. ومن أراد عقدةَ السياسات واختباراتِها
كاملةً على جهازه يضع هذه المخازنَ محليًّا من نسخةٍ عنده (مزامنةُ Drive بالمسارات نفسِها،
أو نسخةُ `diwan-private`) بهذه الأداة؛ وهي مُتجاهَلةٌ في git فلا تدخل المستودعَ العام أبدًا
(`.gitignore`، والحارس `tests/test_private_stores_are_ignored.py`).

    python tools/place_private_stores.py --from ~/diwan-private
    python tools/place_private_stores.py --remove

المصدرُ الوحيد لقائمة المخازن `tools/export_public.py::EXCLUDED_PATHS`، فلا تفترق القائمتان؛
وما ضمّنه المالكُ في العام (`INCLUDED_PATHS`، ق٥٨: القاموسُ المحيط) ملفٌّ متتبَّع لا مخزن، فلا يُرفع
ولا يُكتب فوقه من المصدر.
وبعد الوضع تُشغَّل `tools/sign_anchors.py verify` فتُفحص المراسي والتواقيعُ التي جاءت مع
المخازن كما تُفحص في المستودع الخاص؛ وإن سقط الفحصُ رُفعت المخازنُ الموضوعة كلُّها فلا يبقى
نصفُ متنٍ بلا مرساة. والأداةُ لا تضع ولا ترفع إلا في نسخةٍ تحمل علامةَ اللقطة العامة
(`PUBLIC-EXPORT.json`)، فلا تمسّ المستودعَ الخاص الذي يتتبّع مخازنه.

**الحدُّ المعلَن:** فحصُ التوقيع نفسُه لا يُعاد إنتاجُه في الاختبار (يحتاج مخازنَ حقيقية
بمراسيها)؛ الاختبارُ يُثبت النسخَ والرفعَ والرفضَ خارج اللقطة.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.public_export import MARKER_NAME, read_marker  # noqa: E402
from export_public import EXCLUDED_PATHS, INCLUDED_PATHS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def store_paths() -> tuple[str, ...]:
    """المخازنُ بمساراتها النسبية بلا شرطة الدليل الختامية."""
    return tuple(entry.rstrip("/") for entry in EXCLUDED_PATHS)


def _included_under(rel: str) -> set[str]:
    """المساراتُ المضمَّنة الواقعة تحت هذا المخزن، نسبيةً إلى الجذر."""
    return {inc for inc in INCLUDED_PATHS if inc == rel or inc.startswith(rel + "/")}


def _remove_tree(root: Path, rel: str) -> None:
    """يرفع ما تحت المخزن عدا المضمَّن، ولا يُبقي دليلًا فارغًا."""
    keep = _included_under(rel)
    base = root / rel
    if not keep:
        shutil.rmtree(base)
        return
    for path in sorted(base.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir():
            if not any(path.iterdir()):
                path.rmdir()
        elif path.relative_to(root).as_posix() not in keep:
            path.unlink()
    if not any(base.iterdir()):
        base.rmdir()


def _not_public(root: Path) -> dict | None:
    if read_marker(root) is None:
        return {"status": "refused", "code": "not_a_public_checkout",
                "reason": f"لا علامةَ {MARKER_NAME} في {root}: الأداةُ للقطة العامة وحدها"}
    return None


def remove(root: Path) -> dict:
    """يرفع المخازنَ الموضوعة (ولا شيءَ غيرها) من نسخةٍ عامة."""
    root = Path(root)
    refusal = _not_public(root)
    if refusal:
        return refusal
    removed = []
    for rel in store_paths():
        target = root / rel
        if target.is_dir():
            _remove_tree(root, rel)
            removed.append(rel)
        elif target.exists() and rel not in INCLUDED_PATHS:
            target.unlink()
            removed.append(rel)
    return {"status": "removed", "removed": removed, "kept": sorted(INCLUDED_PATHS)}


def place(root: Path, source: Path, *, verify: bool = True) -> dict:
    """ينسخ المخازنَ من `source` (بالمسارات نفسِها) ثم يتحقّق من مراسيها وتواقيعها."""
    root = Path(root)
    refusal = _not_public(root)
    if refusal:
        return refusal
    source = Path(source).expanduser().resolve()
    placed: list[str] = []
    missing: list[str] = []
    for rel in store_paths():
        src = source / rel
        if not src.exists():
            missing.append(rel)
            continue
        dst = root / rel
        if src.is_dir():
            keep = _included_under(rel)
            if dst.exists():
                _remove_tree(root, rel)

            def _skip(directory, names, _src=src, _keep=keep):
                base = Path(directory).relative_to(_src)
                return {n for n in names if (Path(rel) / base / n).as_posix() in _keep}
            shutil.copytree(src, dst, dirs_exist_ok=True, ignore=_skip)
        elif rel in INCLUDED_PATHS:
            continue                       # متتبَّعٌ في العام، لا يُكتب فوقه
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        placed.append(rel)
    report: dict = {"status": "placed" if placed else "refused", "placed": placed, "missing": missing}
    if not placed:
        report["code"] = "no_store_found"
        return report
    if verify:
        result = subprocess.run([sys.executable, str(root / "tools" / "sign_anchors.py"), "verify"],
                                cwd=root, capture_output=True, text=True)
        tail = (result.stdout or result.stderr).strip().splitlines()[-1:]
        report["verify"] = {"returncode": result.returncode, "tail": tail}
        if result.returncode not in (0, 3):
            remove(root)
            report.update(status="refused", code="stores_unverified", placed=[])
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--from", dest="source", help="جذرُ النسخة التي فيها المخازن (diwan-private أو مزامنة Drive)")
    group.add_argument("--remove", action="store_true", help="رفعُ المخازن الموضوعة من هذه النسخة")
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args(argv)
    report = remove(Path(args.root)) if args.remove else place(Path(args.root), Path(args.source))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in ("placed", "removed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
