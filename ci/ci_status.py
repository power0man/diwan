#!/usr/bin/env python3
"""لماذا لا يعمل CI؟ — تشخيصٌ مسمًّى بدل انتظارٍ صامت.

بتصميم ق٣٦ لا مُشغِّل مقيمًا: كلُّ مهمةٍ تحتاج حاويةً عابرة يبدأها
المالك. فالوصلةُ المدفوعة تبقى **في الطابور بلا حدٍّ زمني** حتى
تُشغَّل الحاوية — وهذا سلوكٌ مقصود، لكنه كان **بلا أي إشارة**: لا
رسالة، ولا رمز، ولا موضع يُسأل. وهذا يخالف «لا مسار صامتًا».

يفحص هذا الأمر الأسباب الأربعة بالترتيب ويسمّي أولَ مانعٍ يجده:

- `docker_unavailable` — لا محرك حاويات يعمل.
- `runner_image_missing` — الإيصال يسمّي صورةً غير موجودة محليًّا
  (يقع بعد `docker image prune`؛ وهو ما عطّل CI في ٢١ سبتمبر ٢٠٢٦).
- `no_runner_registered` — لا مُشغِّل، ومهامٌّ تنتظر.
- `queued_waiting` — الحاوية تعمل والمهمة قيد الالتقاط.

ولا يسجّل مُشغِّلًا ولا يبني صورةً ولا يمسّ حالةَ GitHub: **قراءةٌ
وتشخيصٌ فقط**. البناءُ والتسجيل فعلا مالكٍ بنصّ ق٣٦.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, f"{type(exc).__name__}: {exc}"
    return r.returncode, (r.stdout or r.stderr).strip()


def docker_ok() -> tuple[bool, str]:
    if shutil.which("docker") is None:
        return False, "docker غير مثبَّت في المسار"
    code, out = _run(["docker", "info", "--format", "{{.ServerVersion}}"])
    return (code == 0), (out[:120] if code else f"docker {out}")


def image_present(receipt: Path) -> tuple[bool, str]:
    """هل صورةُ المُشغِّل التي يعتمدها الإيصال موجودة محليًّا؟"""
    if not receipt.exists():
        return False, f"إيصال المُشغِّل غائب: {receipt}"
    try:
        data = json.loads(receipt.read_text(encoding="utf-8"))
        image = data["image_id"]
    except (json.JSONDecodeError, KeyError, OSError) as exc:
        return False, f"إيصال غير صالح: {exc}"
    if not _IMAGE_ID.fullmatch(str(image)):
        return False, "معرّف صورة غير قانوني في الإيصال"
    code, out = _run(["docker", "image", "inspect", image, "--format", "{{.Id}}"])
    if code != 0 or out.strip() != image:
        return False, f"الصورة غير موجودة محليًّا: {image[:19]}…"
    return True, image[:19] + "…"


def github_state(repo: str) -> tuple[dict, str]:
    """المُشغِّلون المسجَّلون والمهامُّ المنتظرة — قراءةً فقط."""
    if shutil.which("gh") is None:
        return {}, "gh غير مثبَّت"
    owner_repo = repo.rstrip("/").split("github.com/")[-1]
    code, out = _run(["gh", "api", f"repos/{owner_repo}/actions/runners",
                      "--jq", "[.runners[].labels[].name]"])
    labels = json.loads(out) if code == 0 and out.startswith("[") else []
    code2, out2 = _run(["gh", "run", "list", "--repo", owner_repo, "--limit", "10",
                        "--json", "status,headSha,databaseId"])
    runs = json.loads(out2) if code2 == 0 and out2.startswith("[") else []
    waiting = [r for r in runs if r.get("status") in ("queued", "waiting",
                                                      "pending", "in_progress")]
    return {"labels": labels, "waiting": waiting}, ""


def diagnose(receipt: Path, repo: str) -> dict:
    ok, detail = docker_ok()
    if not ok:
        return {"code": "docker_unavailable", "detail": detail,
                "remedy": "شغّل Docker ثم أعد الفحص"}
    present, detail = image_present(receipt)
    state, err = github_state(repo)
    waiting = state.get("waiting", [])
    isolated = "diwan-isolated" in state.get("labels", [])
    if not present:
        return {"code": "runner_image_missing", "detail": detail,
                "waiting": len(waiting),
                "remedy": "أعد بناء صورة المُشغِّل بإيصالٍ جديد "
                          "(ci/prepare_runtime.py --runner-receipt …) — "
                          "يحتاج مراجعة بصمات الصور الأساس: فعلُ مالك"}
    if err:
        return {"code": "github_unreadable", "detail": err,
                "remedy": "وثّق حالة GitHub يدويًّا"}
    if waiting and not isolated:
        return {"code": "no_runner_registered", "waiting": len(waiting),
                "detail": f"{len(waiting)} مهمة تنتظر ولا مُشغِّل مسجَّل",
                "remedy": "ابدأ حاويةً عابرة واحدة "
                          "(ci/start_isolated_runner.py برمز تسجيل عبر "
                          "stdin) — فعلُ مالك بنصّ ق٣٦"}
    if waiting:
        return {"code": "queued_waiting", "waiting": len(waiting),
                "detail": "الحاوية مسجَّلة والمهمة قيد الالتقاط",
                "remedy": "انتظر"}
    return {"code": "idle", "detail": "لا مهامَّ منتظرة",
            "remedy": "لا شيء مطلوب"}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--runner-receipt", type=Path, required=True)
    p.add_argument("--repository", default="https://github.com/power0man/diwan-private")
    args = p.parse_args()
    report = diagnose(args.runner_receipt, args.repository)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["code"] in ("idle", "queued_waiting") else 1


if __name__ == "__main__":
    raise SystemExit(main())
