"""أمرُ التوزيع يطابق البيانَ قبل أن يُدخل حالةً محجوبةً واحدة إلى الجهاز.

هذا آخرُ حاجزٍ قبل أن تستقرّ حالاتٌ لم يرَها أحدٌ في مكانها. فإن نسخ قبل أن
يطابق، أو قَبِل ملفًّا لا ذكرَ له في البيان، دخل المحجوبُ بلا إثباتٍ أنه هو
الذي أُعلنت بصمتُه — ولم يعد يُعرف بعد ظهور الرقم أنّ البنك لم يُبدَّل.

والبيانُ يُكتب بشكلين: قائمةً فيها `path` و`count` (v1)، وقاموسًا مفتاحُه
المسار وفيه `cases` (v1.1). والتكليفُ اشترط محتواه لا شكلَه، فيُقبل الشكلان
**بالصرامة نفسها**: بصمةٌ لكل ملفّ، وعددٌ لكل حزمة، ولا ملفَّ خارج البيان.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "external" / "PLACE-KIMI-FILES.md"

SEALED_CASES = {"cases": [{"id": f"c{i}"} for i in range(3)]}


def _block() -> str:
    """أمرُ التوزيع كما يستخرجه tools/kimi_drive.sh نفسه — مصدرٌ واحد لا نسختان."""
    lines, inside, out = DOC.read_text(encoding="utf-8").splitlines(), False, []
    for line in lines:
        if line == "```bash":
            inside = True
            continue
        if line == "```":
            inside = False
        if inside:
            out.append(line)
    assert out, "لم أجد أمر التوزيع في الوثيقة"
    return "\n".join(out)


def _build(src: Path, shape: str, *, tamper: bool = False, unlisted: bool = False) -> None:
    (src / "open").mkdir(parents=True)
    (src / "open" / "a.json").write_text('{"cases": []}', encoding="utf-8")
    (src / "REPORT.md").write_text("# تقرير\n", encoding="utf-8")
    (src / "disputed.json").write_text('{"disputes": null}', encoding="utf-8")
    sealed = src / "sealed"
    sealed.mkdir()
    payload = json.dumps(SEALED_CASES, ensure_ascii=False).encode("utf-8")
    (sealed / "s1.json").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    if tamper:
        # البصمةُ معلنة، والملفُّ بُدّل بعدها — وهو بالضبط ما يجب ألّا يمرّ.
        # والعددُ يبقى ثلاثةً كما أُعلن، فلا يكشفه إلا فحصُ البصمة وحده.
        (sealed / "s1.json").write_bytes(
            json.dumps({"cases": [{"id": f"زُرع{i}"} for i in range(3)]},
                       ensure_ascii=False).encode("utf-8")
        )
    if unlisted:
        (sealed / "s2.json").write_bytes(payload)
    entry = {"sha256": digest, "count": 3, "cases": 3, "kind": "suite"}
    files = (
        {"sealed/s1.json": entry}
        if shape == "dict"
        else [{"path": "sealed/s1.json", **entry}]
    )
    (sealed / "MANIFEST.json").write_text(
        json.dumps({"files": files}, ensure_ascii=False), encoding="utf-8"
    )


def _place(tmp_path: Path, shape: str, **kw) -> subprocess.CompletedProcess:
    src, diwan, dst = tmp_path / "src", tmp_path / "diwan", tmp_path / "sealed-dst"
    _build(src, shape, **kw)
    (diwan / ".git").mkdir(parents=True)
    return subprocess.run(
        ["bash", "-c", _block()],
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(tmp_path),
            "SRC": str(src),
            "DIWAN": str(diwan),
            "SEALED_DST": str(dst),
        },
    )


@pytest.mark.parametrize("shape", ["list", "dict"])
def test_both_manifest_shapes_are_accepted(tmp_path, shape):
    done = _place(tmp_path, shape)
    assert done.returncode == 0, done.stdout + done.stderr
    bank = tmp_path / "diwan" / "evaluation" / "banks" / "kimi_v1"
    assert (bank / "open" / "a.json").is_file()
    assert (bank / "sealed" / "MANIFEST.json").is_file()
    assert (tmp_path / "sealed-dst" / "s1.json").is_file()


@pytest.mark.parametrize("shape", ["list", "dict"])
def test_tampered_sealed_file_is_refused_and_nothing_is_copied(tmp_path, shape):
    done = _place(tmp_path, shape, tamper=True)
    assert done.returncode != 0
    assert "لا يطابق" in done.stdout + done.stderr
    assert not (tmp_path / "diwan" / "evaluation" / "banks" / "kimi_v1" / "open").exists()
    assert not (tmp_path / "sealed-dst").exists()


@pytest.mark.parametrize("shape", ["list", "dict"])
def test_sealed_file_outside_the_manifest_is_refused(tmp_path, shape):
    """ملفٌّ محجوبٌ لا ذكرَ له في البيان يدخل بلا بصمةٍ تشهد له."""
    done = _place(tmp_path, shape, unlisted=True)
    assert done.returncode != 0
    assert not (tmp_path / "sealed-dst").exists()


def test_no_content_of_any_sealed_case_is_printed(tmp_path):
    """المخرجُ علاماتٌ وأعدادٌ ومساراتٌ — لا نصُّ حالة."""
    done = _place(tmp_path, "dict")
    assert "زُرع" not in done.stdout
    assert '{"cases"' not in done.stdout
