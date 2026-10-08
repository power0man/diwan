"""أمرُ توزيع ملفّات Kimi كما هو مكتوبٌ في الوثيقة، يُشغَّل هنا على مجلّداتٍ مصطنعة.

الأمرُ يُلصق في الطرفية من `docs/external/PLACE-KIMI-FILES.md`، فالاختبارُ يستخرجه من
الوثيقة نفسها: إن انكسر النصّ المنشور سقط هنا، لا على جهاز المالك.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "external" / "PLACE-KIMI-FILES.md"


def _script() -> str:
    blocks = re.findall(r"```bash\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S)
    assert len(blocks) == 1, "كتلة أمرٍ واحدة في الوثيقة"
    return blocks[0]


def _suite(ids):
    return {"schema_version": 1, "suite_id": "k", "split": "development",
            "description": "d",
            "cases": [{"case_id": i, "capability": "a",
                       "messages": [{"role": "user", "content": "q"}], "reference": "r",
                       "rubric": ["r"], "checks": [], "critical": False} for i in ids]}


def _kimi(tmp: Path, *, tamper=False, unlisted=False) -> Path:
    src = tmp / "kimi-benchmark"
    (src / "open" / "tier_a").mkdir(parents=True)
    (src / "open" / "tier_a" / "kimi_a_001.json").write_text(json.dumps(_suite(["o1"])))
    (src / "open" / "tier_a" / "kimi_a_001.meta.json").write_text("{}")
    (src / "REPORT.md").write_text("# v1.1")
    (src / "disputed.json").write_text("{}")
    sealed = src / "sealed" / "tier_a"
    sealed.mkdir(parents=True)
    raw = json.dumps(_suite(["s1", "s2"])).encode()
    (sealed / "kimi_a_101.json").write_bytes(raw)
    manifest = {"generated_at": "x", "authored_by": "kimi", "files": [
        {"path": "sealed/tier_a/kimi_a_101.json",
         "sha256": hashlib.sha256(raw).hexdigest(),
         "count": 3 if tamper else 2, "capabilities": ["a"]}]}
    (src / "sealed" / "MANIFEST.json").write_text(json.dumps(manifest))
    if unlisted:
        (sealed / "kimi_a_102.json").write_text(json.dumps(_suite(["s3"])))
    return src


def _public_repo(diwan: Path, url: str = "https://github.com/power0man/diwan.git") -> None:
    """مستودعٌ حقيقيّ بأصلٍ مسمًّى: التوزيعُ يرفض غيرَ النسخة العامة."""
    diwan.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(diwan)], check=True)
    subprocess.run(["git", "-C", str(diwan), "remote", "add", "origin", url], check=True)


def _run(tmp: Path, src: Path, url: str = "https://github.com/power0man/diwan.git", **extra) -> subprocess.CompletedProcess:
    diwan = tmp / "diwan"
    if not (diwan / ".git").exists():
        _public_repo(diwan, url)
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp), "SRC": str(src),
           "DIWAN": str(diwan), "SEALED_DST": str(tmp / "diwan-sealed" / "kimi_v1"), **extra}
    return subprocess.run(["bash", "-c", _script()], cwd=src, env=env,
                          capture_output=True, text=True, timeout=60)


def test_the_published_command_is_valid_bash():
    block = _script()
    lines = block.strip("\n").split("\n")
    # يُلصق في zsh، فيُغلَّف في bash مستقلّ: خروجُه برمزٍ لا يُغلق نافذة الطرفية.
    assert lines[0] == "bash <<'KIMI'" and lines[-1] == "KIMI"
    inner = "\n".join(lines[1:-1])
    assert subprocess.run(["bash", "-n", "-c", inner]).returncode == 0


def test_a_refusal_does_not_kill_the_calling_shell(tmp_path):
    """الصقه في صدفةٍ تفاعلية ثم نفّذ أمرًا بعده: يجب أن يصل إليه."""
    src = tmp_path / "empty"
    src.mkdir()
    shell = _script() + "echo السطر_التالي_بقي_حيًّا\n"
    result = subprocess.run(["bash", "-c", shell], cwd=src, capture_output=True, text=True,
                            env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert "توقّفت" in result.stdout and "السطر_التالي_بقي_حيًّا" in result.stdout


def test_the_happy_path_places_everything_and_keeps_sealed_out(tmp_path):
    src = _kimi(tmp_path)
    result = _run(tmp_path, src)
    assert result.returncode == 0, result.stdout + result.stderr
    bank = tmp_path / "diwan" / "evaluation" / "banks" / "kimi_v1"
    assert (bank / "open" / "tier_a" / "kimi_a_001.json").is_file()
    assert (bank / "REPORT.md").is_file() and (bank / "disputed.json").is_file()
    assert (bank / "sealed" / "MANIFEST.json").is_file()
    assert sorted(p.name for p in (bank / "sealed").iterdir()) == ["MANIFEST.json"]
    assert (tmp_path / "diwan-sealed" / "kimi_v1" / "tier_a" / "kimi_a_101.json").is_file()
    assert '"s1"' not in result.stdout, "لا يُطبع محتوى محجوب"


@pytest.mark.parametrize("kind", ["tamper", "unlisted"])
def test_a_manifest_that_does_not_match_stops_before_copying(tmp_path, kind):
    src = _kimi(tmp_path, **{kind: True})
    result = _run(tmp_path, src)
    assert result.returncode != 0
    assert "البيان لا يطابق" in result.stdout
    assert not (tmp_path / "diwan" / "evaluation").exists(), "لم يُنسخ شيء"
    assert not (tmp_path / "diwan-sealed").exists()


def test_an_existing_target_is_never_overwritten(tmp_path):
    src = _kimi(tmp_path)
    earlier = tmp_path / "diwan-sealed" / "kimi_v1"
    earlier.mkdir(parents=True)
    (earlier / "keep.json").write_text("{}")
    result = _run(tmp_path, src)
    assert result.returncode != 0 and "موجود من قبل" in result.stdout
    assert [p.name for p in earlier.iterdir()] == ["keep.json"]


DEV = ("arabic_general_v3_1.json", "arabic_general_v3_2.json", "agentic_v3.json", "agentic_v3.meta.json")


def test_the_development_suites_are_placed_beside_the_bank(tmp_path):
    src = _kimi(tmp_path)
    for name in DEV:
        (src / name).write_text("{}")
    result = _run(tmp_path, src)
    assert result.returncode == 0, result.stdout + result.stderr
    suites = tmp_path / "diwan" / "evaluation" / "suites"
    assert sorted(p.name for p in suites.iterdir()) == sorted(DEV)


def test_an_existing_development_suite_stops_before_anything_is_copied(tmp_path):
    src = _kimi(tmp_path)
    (src / "agentic_v3.json").write_text('{"new": true}')
    suites = tmp_path / "diwan" / "evaluation" / "suites"
    suites.mkdir(parents=True)
    (suites / "agentic_v3.json").write_text('{"old": true}')
    result = _run(tmp_path, src)
    assert result.returncode != 0 and "موجود من قبل" in result.stdout
    assert (suites / "agentic_v3.json").read_text() == '{"old": true}'
    assert not (tmp_path / "diwan" / "evaluation" / "banks").exists()
    assert not (tmp_path / "diwan-sealed").exists()


def _inventory() -> tuple[list[str], dict[str, str], str]:
    block = _script()
    names = re.search(r'^DEV="([^"]+)"', block, re.M).group(1).split()
    delivered = dict(line.split("=", 1) for line in
                     re.search(r'^DELIVERED="([^"]+)"', block, re.M).group(1).split())
    receipt = re.search(r'^RECEIPT="([^"]+)"', block, re.M).group(1)
    return names, delivered, receipt


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_requested_development_suites_do_not_collide_with_committed_banks():
    """ما يطلبه التكليفُ ولم يُسلَّم لا يسمّي بنكًا مودَعًا، وإلّا توقّف التوزيعُ عند «موجود من قبل».

    كان الجزءُ ٣ يطلب agentic_v2.json وهو بنكُ ك٤٤ المودَع (#101). والجزءُ ٢ سُلّم في ٦ أكتوبر وأُودع وجُمّد (ك٤٣ #28)،
    فصار في جردِ المسلَّم `DELIVERED` ببصمته؛ والتكليفُ باقٍ بنصّه مرجعًا. فالمسلَّمُ مودَعٌ بالبصمة نفسِها التي في الأمر
    وفي إيصال التجميد، وما بقي معلَّقًا لا يوجد في المستودع.
    """
    names, delivered, receipt = _inventory()
    assert tuple(names) == DEV and set(delivered) < set(names)
    request = (ROOT / "docs" / "external" / "KIMI-NEXT.md").read_text(encoding="utf-8").split("\n---\n", 1)[1]
    frozen = json.loads((ROOT / receipt).read_text(encoding="utf-8"))
    assert frozen["kind"] == "k43_general_bank_freeze" and frozen["source"]["placed_byte_for_byte"] is True
    frozen_digests = {Path(f["path"]).name: f["sha256"] for f in frozen["files"]}
    for name in names:
        assert f"`{name}`" in request, f"التكليفُ لا يطلب {name}"
        target = ROOT / "evaluation" / "suites" / name
        if name in delivered:
            assert target.is_file() and _sha(target) == delivered[name] == frozen_digests[name], f"{name} لا يطابق جردَه"
        else:
            assert not target.exists(), f"{name} مودَعٌ من قبل وليس في جرد المسلَّم"


def _fulfilled(tmp: Path, *, commit_target=True, commit_receipt=True) -> tuple[Path, Path]:
    """تسليمُ Kimi نفسُه بعد أن أُودع جزؤه ٢: المصدرُ والهدفُ بايتاتُ المستودع، والإيصالُ العامّ بجانبهما."""
    _, delivered, receipt = _inventory()
    src = _kimi(tmp)
    diwan = tmp / "diwan"
    _public_repo(diwan)
    suites = diwan / "evaluation" / "suites"
    suites.mkdir(parents=True)
    for name in delivered:
        raw = (ROOT / "evaluation" / "suites" / name).read_bytes()
        (src / name).write_bytes(raw)
        (suites / name).write_bytes(raw)
    (diwan / receipt).parent.mkdir(parents=True)
    (diwan / receipt).write_bytes((ROOT / receipt).read_bytes())
    git = ["git", "-C", str(diwan), "-c", "user.name=t", "-c", "user.email=t@t"]
    staged = [*([f"evaluation/suites/{n}" for n in delivered] if commit_target else []),
              *([receipt] if commit_receipt else [])]
    if staged:
        subprocess.run([*git, "add", *staged], check=True)
        subprocess.run([*git, "commit", "-qm", "k43"], check=True)
    return src, suites


def test_a_fulfilled_delivery_is_skipped_untouched_while_pending_suites_are_placed(tmp_path):
    _, delivered, _ = _inventory()
    src, suites = _fulfilled(tmp_path)
    before = {n: (suites / n).read_bytes() for n in delivered}
    for name in set(DEV) - set(delivered):
        (src / name).write_text("{}")
    result = _run(tmp_path, src)
    assert result.returncode == 0, result.stdout + result.stderr
    assert {n: (suites / n).read_bytes() for n in delivered} == before
    assert sorted(p.name for p in suites.iterdir()) == sorted(DEV)
    assert all(f"لم يُمسّ: {n}" in result.stdout for n in delivered)


def _bad_receipt(case: str, data: dict) -> str:
    """إيصالاتٌ تحمل البصمتين نصًّا ولا تُثبت التجميد: كان `grep` يقبلها كلَّها."""
    files = data["files"]
    if case == "receipt_unrelated_kind":
        data["kind"] = "unrelated_record"
    elif case == "receipt_wrong_status":
        data["status"] = "measured"
    elif case == "receipt_unrelated_paths":
        for i, entry in enumerate(files):
            entry["path"] = f"unrelated/{i}.json"
    elif case == "receipt_swapped_digests":
        files[0]["sha256"], files[1]["sha256"] = files[1]["sha256"], files[0]["sha256"]
    elif case == "receipt_duplicate_path":
        files += [{"path": "unrelated/0.json", "sha256": "0" * 64}, {"path": "unrelated/0.json", "sha256": "1" * 64}]
    elif case == "receipt_digest_also_elsewhere":
        files.append({"path": "unrelated/0.json", "sha256": files[0]["sha256"]})
    elif case == "receipt_entry_not_object":
        files.append("evaluation/suites/agentic_v3.json")
    text = json.dumps(data, ensure_ascii=False, indent=1)
    return "{not json\n" + text if case == "receipt_malformed" else text


BAD_RECEIPTS = ["receipt_unrelated_kind", "receipt_wrong_status", "receipt_unrelated_paths", "receipt_swapped_digests",
                "receipt_duplicate_path", "receipt_digest_also_elsewhere", "receipt_entry_not_object", "receipt_malformed"]


def _commit_receipt(diwan: Path, receipt: str, case: str) -> None:
    data = json.loads((diwan / receipt).read_text(encoding="utf-8"))
    (diwan / receipt).write_text(_bad_receipt(case, data), encoding="utf-8")
    git = ["git", "-C", str(diwan), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "commit", "-qam", "receipt"], check=True)


@pytest.mark.parametrize("case", ["untracked_target", "ignored_target", "changed_target", "other_source", "receipt_uncommitted",
                                  "receipt_without_digest", *BAD_RECEIPTS])
def test_a_fulfilled_name_that_does_not_match_its_receipt_stops_before_anything_is_copied(tmp_path, case):
    """التخطّي للمسلَّم بعينه لا لكلّ موجود: هدفٌ غيرُ مودَع أو معدَّل، أو مصدرٌ آخر، أو إيصالٌ لا يُعتمد، يُرفض كما كان."""
    _, delivered, receipt = _inventory()
    first = sorted(delivered)[0]
    src, suites = _fulfilled(tmp_path, commit_target=case not in ("untracked_target", "ignored_target"),
                             commit_receipt=case != "receipt_uncommitted")
    diwan = tmp_path / "diwan"
    if case in BAD_RECEIPTS:
        _commit_receipt(diwan, receipt, case)
    if case == "ignored_target":
        # المتجاهَلُ لا يظهر في `git status`، فالإيداعُ يُثبَت بـ`ls-files` لا بنظافة الحالة وحدها
        (diwan / ".gitignore").write_text(f"evaluation/suites/{first}\n")
    if case == "changed_target":
        (suites / first).write_bytes((suites / first).read_bytes() + b"\n")
    if case == "other_source":
        (src / first).write_bytes((src / first).read_bytes() + b"\n")
    if case == "receipt_without_digest":
        text = (diwan / receipt).read_text(encoding="utf-8").replace(delivered[first], "0" * 64)
        (diwan / receipt).write_text(text, encoding="utf-8")
        git = ["git", "-C", str(diwan), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*git, "commit", "-qam", "receipt"], check=True)
    before = {n: (suites / n).read_bytes() for n in delivered}
    (src / "agentic_v3.json").write_text("{}")
    result = _run(tmp_path, src)
    assert result.returncode != 0 and f"suites/{first} موجود من قبل" in result.stdout
    assert {n: (suites / n).read_bytes() for n in delivered} == before
    assert not (suites / "agentic_v3.json").exists()
    assert not (diwan / "evaluation" / "banks").exists()
    assert not (tmp_path / "diwan-sealed").exists()


def test_the_private_copy_is_refused_before_anything_is_copied(tmp_path):
    """على الماك ~/diwan-work/diwan هو diwan-private؛ فالتوزيعُ إليه يُرفض بالاسم."""
    src = _kimi(tmp_path)
    proc = _run(tmp_path, src, url="https://github.com/power0man/diwan-private")
    assert proc.returncode != 0 and "ليس نسخةَ power0man/diwan العامة" in proc.stdout
    assert not (tmp_path / "diwan" / "evaluation").exists()
    assert not (tmp_path / "diwan-sealed").exists()


def _commit_all(diwan: Path) -> None:
    git = ["git", "-C", str(diwan), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "v1.1"], check=True)


def test_an_update_replaces_the_bank_and_keeps_the_old_sealed_beside_it(tmp_path):
    assert _run(tmp_path, _kimi(tmp_path)).returncode == 0
    _commit_all(tmp_path / "diwan")
    newer = _kimi(tmp_path / "v1.2")
    (newer / "open" / "tier_a" / "kimi_a_001.json").write_text(json.dumps(_suite(["o1", "o2"])))
    result = _run(tmp_path, newer, UPDATE="1")
    assert result.returncode == 0, result.stdout + result.stderr
    bank = tmp_path / "diwan" / "evaluation" / "banks" / "kimi_v1"
    assert '"o2"' in (bank / "open" / "tier_a" / "kimi_a_001.json").read_text()
    backups = list((tmp_path / "diwan-sealed").glob("kimi_v1.before-*"))
    assert len(backups) == 1 and (backups[0] / "tier_a" / "kimi_a_101.json").is_file()
    assert (tmp_path / "diwan-sealed" / "kimi_v1" / "tier_a" / "kimi_a_101.json").is_file()
    assert '"s1"' not in result.stdout


def test_an_update_refuses_uncommitted_changes_or_a_missing_bank(tmp_path):
    missing = _run(tmp_path, _kimi(tmp_path), UPDATE="1")
    assert missing.returncode != 0 and "لا بنكَ قائمًا" in missing.stdout
    assert not (tmp_path / "diwan" / "evaluation").exists()
    assert _run(tmp_path, _kimi(tmp_path / "a")).returncode == 0
    _commit_all(tmp_path / "diwan")
    bank = tmp_path / "diwan" / "evaluation" / "banks" / "kimi_v1"
    (bank / "REPORT.md").write_text("تعديلٌ لم يُودَع")
    dirty = _run(tmp_path, _kimi(tmp_path / "b"), UPDATE="1")
    assert dirty.returncode != 0 and "غيرُ مودَعة" in dirty.stdout
    assert (bank / "REPORT.md").read_text() == "تعديلٌ لم يُودَع"
    assert not list((tmp_path / "diwan-sealed").glob("kimi_v1.before-*"))


def _open_only(tmp: Path) -> Path:
    src = _kimi(tmp)
    subprocess.run(["rm", "-rf", str(src / "sealed")], check=True)
    (src / "open" / "tier_a" / "kimi_a_001.json").write_text(json.dumps(_suite(["o1", "o9"])))
    return src


def test_an_open_only_update_never_touches_the_sealed_half(tmp_path):
    assert _run(tmp_path, _kimi(tmp_path)).returncode == 0
    _commit_all(tmp_path / "diwan")
    sealed = tmp_path / "diwan-sealed" / "kimi_v1"
    before = {p: p.read_bytes() for p in sealed.rglob("*") if p.is_file()}
    bank = tmp_path / "diwan" / "evaluation" / "banks" / "kimi_v1"
    manifest = (bank / "sealed" / "MANIFEST.json").read_bytes()
    result = _run(tmp_path, _open_only(tmp_path / "v1.2"), UPDATE="1", OPEN_ONLY="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"o9"' in (bank / "open" / "tier_a" / "kimi_a_001.json").read_text()
    assert {p: p.read_bytes() for p in sealed.rglob("*") if p.is_file()} == before
    assert (bank / "sealed" / "MANIFEST.json").read_bytes() == manifest
    assert not list((tmp_path / "diwan-sealed").glob("kimi_v1.before-*"))


def test_an_open_only_delivery_with_a_sealed_folder_or_without_update_is_refused(tmp_path):
    assert _run(tmp_path, _kimi(tmp_path)).returncode == 0
    _commit_all(tmp_path / "diwan")
    bank = tmp_path / "diwan" / "evaluation" / "banks" / "kimi_v1"
    kept = (bank / "open" / "tier_a" / "kimi_a_001.json").read_text()
    with_sealed = _run(tmp_path, _kimi(tmp_path / "x"), UPDATE="1", OPEN_ONLY="1")
    assert with_sealed.returncode != 0 and "لا محجوبَ فيها" in with_sealed.stdout
    no_update = _run(tmp_path, _open_only(tmp_path / "y"), OPEN_ONLY="1")
    assert no_update.returncode != 0 and "UPDATE=1" in no_update.stdout
    assert (bank / "open" / "tier_a" / "kimi_a_001.json").read_text() == kept


@pytest.mark.parametrize("case", ["receipt_unrelated_kind", "receipt_unrelated_paths"])
def test_an_open_only_update_with_an_unrelated_receipt_copies_nothing(tmp_path, case):
    """دورةُ الشطر المفتوح على بنكٍ قائم: إيصالٌ مودَعٌ نظيف يحمل البصمتين لسجلٍّ آخر لا يجيز نسخَ المعلَّق."""
    _, delivered, receipt = _inventory()
    src, suites = _fulfilled(tmp_path)
    assert _run(tmp_path, _kimi(tmp_path / "v1.1")).returncode == 0
    diwan = tmp_path / "diwan"
    _commit_all(diwan)
    bank = diwan / "evaluation" / "banks" / "kimi_v1"
    kept = (bank / "open" / "tier_a" / "kimi_a_001.json").read_text()
    _commit_receipt(diwan, receipt, case)
    subprocess.run(["rm", "-rf", str(src / "sealed")], check=True)
    (src / "agentic_v3.json").write_text("{}")
    before = {n: (suites / n).read_bytes() for n in delivered}
    result = _run(tmp_path, src, UPDATE="1", OPEN_ONLY="1")
    assert result.returncode != 0 and "موجود من قبل" in result.stdout
    assert "لم يُمسّ" not in result.stdout
    assert not (suites / "agentic_v3.json").exists()
    assert {n: (suites / n).read_bytes() for n in delivered} == before
    assert (bank / "open" / "tier_a" / "kimi_a_001.json").read_text() == kept


def test_an_open_only_update_with_the_committed_receipt_skips_the_delivered_suites(tmp_path):
    """الضابط: الإيصالُ المودَع كما هو يُتخطّى به المسلَّم في دورة الشطر المفتوح، ويُنسخ المعلَّق وحده."""
    _, delivered, _ = _inventory()
    src, suites = _fulfilled(tmp_path)
    assert _run(tmp_path, _kimi(tmp_path / "v1.1")).returncode == 0
    _commit_all(tmp_path / "diwan")
    subprocess.run(["rm", "-rf", str(src / "sealed")], check=True)
    (src / "agentic_v3.json").write_text("{}")
    before = {n: (suites / n).read_bytes() for n in delivered}
    result = _run(tmp_path, src, UPDATE="1", OPEN_ONLY="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert {n: (suites / n).read_bytes() for n in delivered} == before
    assert (suites / "agentic_v3.json").read_text() == "{}"
    assert all(f"لم يُمسّ: {n}" in result.stdout for n in delivered)
