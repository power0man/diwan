"""لا أداةَ متتبَّعة تحت tools/ تدفع إلى git (جديد-legacy-archive، ق٧٢).

`tools/mac_run.sh` كان يدفع إلى `main` مباشرةً (`git push origin main`)، وق٣٦ رفض المشغّلَ المقيم، و`main` محميٌّ بفحص
`verify` لا يُدفع إليه إلا بطلب دمج (`AGENTS.md` §٥). فالدفعُ فعلُ العميل بيده، لا أثرٌ جانبيٌّ لأداةٍ تُشغَّل. نُقل السكربتُ إلى
`archive/tools/` بق٧٢، وهذا الحارسُ يمنع عودةَ الدفع إلى أيّ ملفٍّ متتبَّعٍ تحت `tools/`.

القاعدة: نداءُ `git push` بثلاث صور، وما سواها لا يُعدّ:
١. **نصًّا في أيّ ملف:** `git` ثم `push` كلمةً مستقلّة، لا يفصلهما إلا خياراتُ git العامة (`-C <مسار>`، `-c <قيمة>`،
   `--no-pager`...)؛ وهي صورةُ الصدفة وما يُمرَّر إلى صدفةٍ نصًّا (`os.system("git push")`). ومعها صورةُ الوسائط المقتبَسة
   المتجاورة في أيّ لغة (`["git", "push"`، `spawn("git", ["push"`).
٢. **في بايثون:** قائمةٌ أو صفٌّ حرفيّ أوّلُ عناصره `"git"` (أو مسارٌ اسمُه `git`)، وأوّلُ ما بعده من غير الخيارات (وقيمِ
   `-C` و`-c` و`--git-dir` و`--work-tree` و`--namespace`) هو `"push"`: `["git", "-C", str(root), "push"]`.
٣. **في بايثون:** نداءُ مساعدٍ اسمُه `git` أو ينتهي بـ`_git` أو يبدأ بـ`git_`، أوّلُ وسيطٍ نصّيٍّ حرفيٍّ فيه `"push"`:
   `_git(repo, "push", "origin")`.

**الحدود المعلنة:** القاعدةُ تقرأ النصّ ولا تنفّذه؛ فوسائطُ تُبنى في وقت التشغيل (`[GIT, *args]` و`args` من متغيّر، أو
`"pu" + "sh"`، أو `$GIT push` في الصدفة) تفلت منها. والصورةُ الأولى تعدّ ذكرَ `git push` نثرًا في تعليقٍ أو توثيقٍ داخل `tools/`
نداءً، وهو المقصود: دليلُ قبول المهمّة أن `grep 'git push' tools/` فارغ. وما خارج `tools/` (ومنه `archive/` و`.github/`) خارج
نطاقه.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "tools"

# (١) git ثم push كلمةً مستقلّة؛ قبل git لا حرفٌ ولا نقطةٌ ولا شرطة (فلا «legit» ولا «.git»)، وبعد push لا حرفٌ ولا شرطة.
# وبينهما خياراتُ git العامة: ما يأخذ قيمتَه في الكلمة التالية (`-C` و`-c` و`--git-dir` و`--work-tree`...، ملاحظة Codex على
# #306) بقيمةٍ مقتبسةٍ أو مجرّدة، وما سواه بقيمته بعد `=` أو بلا قيمة.
_VALUE = r"""(?:"[^"\n]*"|'[^'\n]*'|\S+)"""
_SPACED = r"(?:-[Cc]|--(?:git-dir|work-tree|namespace|super-prefix|config-env|attr-source))"
SHELL_PUSH = re.compile(rf"(?<![\w.-])git(?:\s+(?:{_SPACED}\s+{_VALUE}|--?[A-Za-z][\w-]*(?:={_VALUE})?))*\s+push(?![\w-])")
QUOTED_PUSH = re.compile(r"""(["'])git\1\s*,\s*\[?\s*(["'])push\2""")
# (٢) خياراتُ git العامة التي تأخذ قيمتها في العنصر التالي.
OPTIONS_WITH_VALUE = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env",
                                "--attr-source"})


def _tracked(prefix: str) -> list[str]:
    """ما يتتبّعه git تحت البادئة؛ والبوابةُ الموثوقة تبني المرشَّحَ من كائناتٍ خامّة بلا `.git`، فما على القرص هو المتتبَّع."""
    if not os.path.lexists(ROOT / ".git"):
        out = []
        for directory, folders, files in os.walk(ROOT / prefix):
            folders[:] = [name for name in folders if name != "__pycache__"]
            out += [(Path(directory) / name).relative_to(ROOT).as_posix() for name in files]
        return sorted(out)
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--", prefix], env=env,
                         capture_output=True, check=True).stdout
    return sorted(name.decode("utf-8") for name in out.split(b"\0") if name)


def _text(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _argv_pushes(elements: list[ast.expr]) -> bool:
    first = _text(elements[0]) if elements else None
    if first is None or PurePosixPath(first).name != "git":
        return False
    rest = iter(elements[1:])
    for node in rest:
        value = _text(node)
        if value is None:
            return False                    # عنصرٌ محسوب: ما بعده لا يُعرف قراءةً (حدٌّ معلن)
        if value in OPTIONS_WITH_VALUE:
            next(rest, None)
        elif not value.startswith("-"):
            return value == "push"
    return False


def _helper_pushes(call: ast.Call) -> bool:
    func = call.func
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
    if not (name == "git" or name.endswith("_git") or name.startswith("git_")):
        return False
    first = next((value for value in map(_text, call.args) if value is not None), None)
    return first == "push"


def _python_pushes(source: str, path: str) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(source, filename=path)):
        if isinstance(node, (ast.List, ast.Tuple)) and _argv_pushes(node.elts):
            lines.append(node.lineno)
        elif isinstance(node, ast.Call) and _helper_pushes(node):
            lines.append(node.lineno)
    return lines


def _logical_lines(source: str):
    """أسطرُ الصدفة المنطقية: السطرُ المنتهي بشرطةٍ مائلةٍ عكسية يُكمَل بما بعده (`git \\` ثم `push`، ملاحظة Codex على
    #306)، ورقمُه رقمُ أوّلِ أسطره."""
    start, buffer = None, ""
    for number, line in enumerate(source.splitlines(), 1):
        start = number if start is None else start
        if line.endswith("\\"):
            buffer += line[:-1] + " "
            continue
        yield start, buffer + line
        start, buffer = None, ""
    if start is not None:
        yield start, buffer


def test_no_tracked_tool_invokes_git_push():
    files = _tracked(PREFIX)
    python = [path for path in files if path.endswith(".py")]
    assert files and python, "لا ملفَّ متتبَّعًا تحت tools/ — فالحارسُ فراغ"
    found = []
    for path in files:
        source = (ROOT / path).read_bytes().decode("utf-8", errors="replace")
        for number, line in _logical_lines(source):
            if SHELL_PUSH.search(line) or QUOTED_PUSH.search(line):
                found.append(f"{path}:{number}: {line.strip()}")
        if path.endswith(".py"):
            found += [f"{path}:{number}: argv" for number in _python_pushes(source, path)]
    assert not found, "أداةٌ تدفع إلى git؛ الدفعُ بطلب دمجٍ بيد العميل لا من أداة (ق٧٢):\n" + "\n".join(found)
