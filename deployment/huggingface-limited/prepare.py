"""Assemble only tracked public application code, never a working data tree."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[2]
REF = "e7945ef6c76f06c2d59d559ffd3f24c89adf98c7"
out = Path(sys.argv[1]).resolve()
out.mkdir(exist_ok=False)
app = out / "app"
app.mkdir()
paths = ["agent", "analysis", "conversation", "core", "diwan", "documents", "evaluation",
         "memory", "multimodal", "nodes", "projections", "providers", "services", "tools",
         "webui", "workspace_tools", "registry", "keys/anchor-ed25519.pub", "keys/anchor-policy.json",
         "pyproject.toml", "uv.lock", "PUBLIC-EXPORT.json", "LICENSE", "README.md"]
names = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", REF, "--", *paths], cwd=ROOT).decode().splitlines()
selected = [n for n in names if (
    n.endswith(".py") or n.startswith("webui/static/")
    or n in {"pyproject.toml","uv.lock","PUBLIC-EXPORT.json","LICENSE","README.md",
             "keys/anchor-ed25519.pub","keys/anchor-policy.json","registry/nodes.jsonl",
             "registry/nodes.jsonl.anchor","registry/nodes.jsonl.anchor.sig"})]
selected = [n for n in selected if not n.startswith("evaluation/banks/")
            and n not in {"tools/build_benchmark_suite.py","tools/seed_acquisitions.py"}]
with tempfile.TemporaryFile() as stream:
    subprocess.run(["git", "archive", REF, "--", *selected], cwd=ROOT, stdout=stream, check=True)
    stream.seek(0)
    with tarfile.open(fileobj=stream) as archive:
        archive.extractall(app, filter="data")
index = app / "webui/static/index.html"
s = index.read_text()
assert s.count("<main>") == 1
notice = ('<p role="note" style="padding:12px;border:1px solid #a66;border-radius:8px">'
          'ديوان السحابي المحدود — يعمل مستقلاً عن الماك بمحرك qwen3.5:9b. '
          'المحادثة وملفات المشروع النصية متاحة؛ التنفيذ المعزول والتحليل والبحث والوسائط غير مفعلة. '
          'التخزين مؤقت وقد يُفقد عند إعادة التشغيل؛ انسخ ما تحتاجه. هذه ليست الخدمة الكاملة.</p>')
s = s.replace("<main>", "<main>" + notice).replace("على جهازك", "سحابي محدود")
index.write_text(s)
deploy = ROOT / "deployment/huggingface-limited"
for source, target in (("Dockerfile","Dockerfile"),("launch.py","launch.py"),("README.space.md","README.md")):
    shutil.copyfile(deploy/source, out/target)
manifest = {"source_commit":REF, "deployment_mode":"private_hf_cpu_ephemeral_limited",
            "model":"qwen3.5:9b", "source_files":selected,
            "files_sha256":{str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in sorted(out.rglob("*")) if p.is_file()}}
(out/"SOURCE.json").write_text(json.dumps(manifest,indent=2)+"\n")
print(json.dumps({"files":len(manifest['files_sha256']),"source_commit":REF,
                  "manifest_sha256":hashlib.sha256((out/'SOURCE.json').read_bytes()).hexdigest()}))
