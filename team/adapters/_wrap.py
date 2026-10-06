"""غلافُ إطلاق العامل: يحفظ معرّفَ عملية الوكيل **قبل** أن يأذن لها بالتنفيذ، ويحفظ رمزَ خروجها؛ فلا يعتمد الدليلُ على بقاء المرسِل حيًّا.

    python -m team.adapters._wrap <exit_file> <child_pid_file> -- <argv...>

البروتوكول: `fork` ثم ينتظر الابنُ بايتَ إذنٍ على أنبوبٍ قبل `exec`؛ والأبُ يكتب معرّفَ الابن ذرّيًّا ثم يرسل الإذن. فإن مات الأبُ قبل
الكتابة أُغلق الأنبوبُ وخرج الابنُ بلا تنفيذ (125). ومن ثَمّ: **لا ملفَّ معرّفٍ ⇐ لا وكيلَ نُفّذ قطّ**، فغيابُ الأثر إثباتُ غيابٍ
لا مجرّدُ غيابِ دليل (ملاحظة Codex التاسعة على #344). فشلُ `exec` (ثنائيٌّ غائب) يُكتب 127 مع علامة `launch_failed`.
وقبل ذلك كلِّه يكتب الغلافُ معرّفَه هو (`wrapper_pid`) فيُفحص حيًّا أو موقوفًا عند الاستحواذ؛ وقبل الإذن مباشرةً يفحص علامةَ
`taken_over` التي يكتبها `takeover`، فغلافٌ تأخّر أو أُوقف ثم عاد بعد الاستحواذ لا يطلق وكيلًا ثانيًا (ملاحظة Codex على #347).
المدخلُ (stdin) يُورَّث إلى الوكيل كما هو. POSIX وحده (`os.fork`).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

NOT_RELEASED = 125      # الابنُ لم يُؤذَن له فلم ينفّذ شيئًا
LAUNCH_FAILED = 127


def write_atomic(path: Path, text: str) -> None:
    """ملفٌّ مؤقت ثم استبدال: قتلُ الغلاف وسطَ الكتابة لا يترك ملفَّ خروجٍ فارغًا يُقرأ صفرًا."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def fork_agent(command: list[str]) -> tuple[int, int, int]:
    """يشطر الابنَ ويوقفه قبل `exec` حتى يصله الإذن. يعيد (معرّفُ الابن، طرفُ الإذن للكتابة، طرفُ خطأ exec للقراءة).
    الأنبوبان غيرُ موروثَين عبر exec، فنهايةُ ملفِّ طرف الخطأ تعني أن exec نجح."""
    go_r, go_w = os.pipe()
    err_r, err_w = os.pipe()
    pid = os.fork()
    if pid == 0:                                   # الابن: لا يعود إلى Python أبدًا
        try:
            os.close(go_w)
            os.close(err_r)
            go = os.read(go_r, 1)
            os.close(go_r)
            if go != b"g":
                os._exit(NOT_RELEASED)
            os.execvp(command[0], command)
        except OSError as exc:
            try:
                os.write(err_w, str(exc.errno or 0).encode("ascii"))
            finally:
                os._exit(LAUNCH_FAILED)
        except BaseException:
            os._exit(126)
    os.close(go_r)
    os.close(err_w)
    return pid, go_w, err_r


def release(go_w: int) -> None:
    """الإذنُ بالتنفيذ بعد حفظ المعرّف."""
    os.write(go_w, b"g")
    os.close(go_w)


def main(argv: list[str]) -> int:
    if len(argv) < 4 or argv[2] != "--":
        sys.stderr.write("usage: _wrap <exit_file> <child_pid_file> -- <command...>\n")
        return 64
    exit_file, pid_file, command = Path(argv[0]), Path(argv[1]), argv[3:]
    write_atomic(pid_file.with_name("wrapper_pid"), str(os.getpid()))
    pid, go_w, err_r = fork_agent(command)
    try:
        write_atomic(pid_file, str(pid))
    except BaseException:
        os.close(go_w)                             # لا إذن: يخرج الابنُ بلا تنفيذ
        raise
    if pid_file.with_name("taken_over").exists():
        os.close(go_w)                             # استُحوذ على المحاولة قبل أن نأذن: لا تنفيذَ ثانيًا
        os.close(err_r)
        _, status = os.waitpid(pid, 0)
        write_atomic(exit_file, str(NOT_RELEASED))
        sys.stderr.write("taken_over: الوكيلُ لم يُطلَق\n")
        return NOT_RELEASED
    release(go_w)
    err = b""
    while True:
        chunk = os.read(err_r, 64)
        if not chunk:
            break
        err += chunk
    os.close(err_r)
    _, status = os.waitpid(pid, 0)
    rc = os.waitstatus_to_exitcode(status)
    if err:
        write_atomic(exit_file.with_name("launch_failed"), err.decode("ascii", "replace"))
        write_atomic(exit_file, str(LAUNCH_FAILED))
        sys.stderr.write(f"launch_failed: errno {err.decode('ascii', 'replace')}\n")
        return LAUNCH_FAILED
    write_atomic(exit_file, str(rc))
    return rc if rc >= 0 else 128 + (-rc)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
