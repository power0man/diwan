"""جلسة قبول دائمة: عد المحاولات والكشف قبل إرجاع نتيجتهما.

المراجع تبقى لدى المستدعي المقيم. هذا ضبط تطبيق على ملفات يملكها
المستخدم، لا عزل صلاحيات عن مالك الجهاز ولا إثبات لهوية مراجع بشري.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from core import filelock
from evaluation.disclosure import Case, DisclosureRefused

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
SHA = re.compile(r"[a-f0-9]{64}\Z")


def _fail(code, reason):
    raise DisclosureRefused(code, reason)


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def _identifier(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def _safe(path):
    if path.is_symlink():
        _fail("unsafe_path", "رابط رمزي في مخزن القبول")
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            _fail("unsafe_path", "ملف غير عادي أو متعدد الروابط")


def _read(path):
    _safe(path)
    def pairs(values):
        out = {}
        for key, value in values:
            if key in out:
                _fail("state_corrupt", "مفتاح JSON مكرر")
            out[key] = value
        return out
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
        _bytes(value)
        return value
    except (OSError, ValueError, UnicodeError):
        _fail("state_corrupt", "ملف قبول غير قابل للقراءة")


def _write(directory, destination, value, *, new=False):
    _safe(destination)
    temporary = directory / ("write-" + uuid.uuid4().hex + ".tmp")
    with temporary.open("xb") as stream:
        os.chmod(temporary, 0o600)
        stream.write(_bytes(value))
        stream.flush()
        os.fsync(stream.fileno())
    if new and destination.exists():
        _fail("state_exists", "لا استبدال لبيان قائم")
    os.replace(temporary, destination)
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class AcceptanceStore:
    """حالة واحدة لكل بنك ونسخة مرشح، تحت قفل غير منتظر."""

    def __init__(self, directory, cases: tuple[Case, ...], *, suite_id: str,
                 candidate_sha256: str, max_attempts: int = 2,
                 min_aggregate: int = 5):
        if (not _identifier(suite_id) or not isinstance(candidate_sha256, str)
                or SHA.fullmatch(candidate_sha256) is None):
            _fail("identity_invalid", "هوية بنك وبصمة مرشح مطلوبتان")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 10:
            _fail("max_attempts", "حد المحاولات عدد صحيح من 1 إلى 10")
        if type(min_aggregate) is not int or min_aggregate < 5:
            _fail("min_aggregate", "حد كشف القبول لا يقل عن خمسة")
        if not isinstance(cases, tuple) or not min_aggregate <= len(cases) <= 1000:
            _fail("suite_size", "حجم البنك لا يحقق حد التجميع")
        seen = set()
        for case in cases:
            if not isinstance(case, Case) or not _identifier(case.case_id):
                _fail("case_invalid", "هوية حالة غير صالحة")
            if case.case_id in seen:
                _fail("case_duplicate", "هوية حالة مكررة")
            seen.add(case.case_id)
            for value in (case.prompt, case.reference, case.rubric):
                if not isinstance(value, str) or not value.strip():
                    _fail("case_invalid", "المهمة والمرجع والمعيار مطلوبة")
                try:
                    value.encode("utf-8")
                except UnicodeEncodeError:
                    _fail("case_invalid", "نص حالة غير صالح لـUTF-8")
        self.cases = cases
        self.ids = seen
        self.directory = Path(directory).absolute()
        if any(p.is_symlink() for p in (self.directory, *self.directory.parents)):
            _fail("unsafe_path", "مجلد قبول عبر رابط رمزي")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.manifest = {"schema_version": 1, "suite_id": suite_id,
                         "suite_sha256": _digest([asdict(c) for c in cases]),
                         "candidate_sha256": candidate_sha256,
                         "max_attempts": max_attempts, "min_aggregate": min_aggregate}
        with self._lock():
            manifest = self.directory / "manifest.json"
            if manifest.exists() or manifest.is_symlink():
                if _digest(_read(manifest)) != _digest(self.manifest):
                    _fail("manifest_conflict", "البنك أو المرشح أو السياسة تغيرت")
                self._load()
            else:
                if any(p.name != "store.lock" for p in self.directory.iterdir()):
                    _fail("manifest_missing", "مجلد سابق بلا بيان؛ لا تصفير للمحاولات")
                _write(self.directory, manifest, self.manifest, new=True)
                self._save({"manifest_sha256": _digest(self.manifest),
                            "attempts": [], "revealed": {}})

    @contextmanager
    def _lock(self):
        path = self.directory / "store.lock"
        _safe(path)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "r+") as stream:
            if os.fstat(stream.fileno()).st_nlink != 1:
                _fail("unsafe_path", "قفل متعدد الروابط")
            try:
                filelock.lock(stream, blocking=False)
            except BlockingIOError:
                _fail("store_busy", "عملية أخرى تملك مخزن القبول")
            try:
                yield
            finally:
                filelock.unlock(stream)

    def _load(self):
        manifest_path = self.directory / "manifest.json"
        _safe(manifest_path)
        if not manifest_path.exists():
            _fail("manifest_missing", "بيان القبول غائب؛ لا متابعة للحالة")
        if _digest(_read(manifest_path)) != _digest(self.manifest):
            _fail("manifest_conflict", "البيان على القرص تغير")
        envelope = _read(self.directory / "state.json")
        if not isinstance(envelope, dict) or set(envelope) != {"state", "sha256"}:
            _fail("state_corrupt", "غلاف الحالة غير صالح")
        state = envelope["state"]
        if (not isinstance(state, dict)
                or set(state) != {"manifest_sha256", "attempts", "revealed"}
                or _digest(state) != envelope["sha256"]
                or state["manifest_sha256"] != _digest(self.manifest)):
            _fail("state_corrupt", "الحالة لا تطابق بصمتها وبيانها")
        attempts, revealed = state["attempts"], state["revealed"]
        if (not isinstance(attempts, list) or len(attempts) > self.manifest["max_attempts"]
                or not isinstance(revealed, dict) or not set(revealed) <= self.ids):
            _fail("state_corrupt", "محاولات أو كشف غير صالح")
        tokens, open_count = set(), 0
        for disclosed in revealed.values():
            if (not isinstance(disclosed, dict) or set(disclosed) != {"actor", "reason"}
                    or not all(isinstance(v, str) and v.strip() for v in disclosed.values())):
                _fail("state_corrupt", "بيانات الكشف غير صالحة")
        for index, entry in enumerate(attempts):
            if (not isinstance(entry, dict)
                    or set(entry) != {"token", "scores", "aggregate"}
                    or not _identifier(entry["token"]) or entry["token"] in tokens
                    or not isinstance(entry["scores"], dict)
                    or not set(entry["scores"]) <= self.ids
                    or any(type(v) is not bool for v in entry["scores"].values())):
                _fail("state_corrupt", "قيد محاولة غير صالح")
            tokens.add(entry["token"])
            if entry["aggregate"] is None:
                open_count += 1
                if set(entry["scores"]) & revealed.keys():
                    _fail("state_corrupt", "حكم نشط على حالة مكشوفة")
            else:
                result = entry["aggregate"]
                if not (self.ids - entry["scores"].keys()) <= revealed.keys():
                    _fail("state_corrupt", "محاولة مغلقة حذفت حالة دون نقلها للتطوير")
                expected = {"n": len(entry["scores"]),
                            "passed": sum(entry["scores"].values()),
                            "attempts_used": index + 1,
                            "attempts_declared": self.manifest["max_attempts"],
                            "live_cases": len(entry["scores"]),
                            "revealed_cases": len(self.ids) - len(entry["scores"])}
                if (not isinstance(result, dict) or set(result) != set(expected)
                        or any(type(v) is not int for v in result.values())
                        or result != expected or result["n"] < self.manifest["min_aggregate"]):
                    _fail("state_corrupt", "تجميع محفوظ خارج عقد الكشف")
        if open_count > 1 or (open_count and attempts[-1]["aggregate"] is not None):
            _fail("state_corrupt", "ترتيب المحاولات غير صالح")
        return state

    def _save(self, state):
        _write(self.directory, self.directory / "state.json",
               {"state": state, "sha256": _digest(state)})

    def _attempt(self, state, token, *, open_only=True):
        entry = next((a for a in state["attempts"] if a["token"] == token), None)
        if entry is None:
            _fail("attempt_unknown", "لا محاولة بهذا الرمز")
        if open_only and entry["aggregate"] is not None:
            _fail("attempt_closed", "المحاولة مغلقة")
        return entry

    def begin(self, token: str) -> int:
        if not _identifier(token):
            _fail("token_invalid", "رمز محاولة غير صالح")
        with self._lock():
            state = self._load()
            old = next((a for a in state["attempts"] if a["token"] == token), None)
            if old is not None:
                self._attempt(state, token)
                return state["attempts"].index(old) + 1
            if state["attempts"] and state["attempts"][-1]["aggregate"] is None:
                _fail("attempt_already_open", "يجب استئناف المحاولة المفتوحة")
            if len(state["attempts"]) >= self.manifest["max_attempts"]:
                _fail("attempts_exhausted", "استنفدت المحاولات المثبتة")
            if len(self.ids - state["revealed"].keys()) < self.manifest["min_aggregate"]:
                _fail("holdout_too_small", "الباقي بعد الكشف أقل من حد التجميع")
            state["attempts"].append({"token": token, "scores": {}, "aggregate": None})
            self._save(state)
            return len(state["attempts"])

    def visible_tasks(self, token: str) -> list[dict]:
        with self._lock():
            state = self._load()
            self._attempt(state, token)
            return [{"case_id": c.case_id, "prompt": c.prompt}
                    for c in self.cases if c.case_id not in state["revealed"]]

    def record(self, token: str, case_id: str, passed: bool):
        if type(passed) is not bool:
            _fail("score_type", "حكم صريح من نوع bool فقط")
        if not _identifier(case_id):
            _fail("case_invalid", "هوية حالة غير صالحة")
        with self._lock():
            state = self._load()
            attempt = self._attempt(state, token)
            if case_id not in self.ids:
                _fail("case_unknown", "حالة غير معروفة")
            if case_id in state["revealed"]:
                _fail("case_moved_to_dev", "الحالة كشفت وصارت تطويرية")
            if case_id in attempt["scores"] and attempt["scores"][case_id] is not passed:
                _fail("score_conflict", "لا استبدال حكم مسجل في المحاولة")
            attempt["scores"][case_id] = passed
            self._save(state)

    def aggregate(self, token: str) -> dict:
        with self._lock():
            state = self._load()
            attempt = self._attempt(state, token, open_only=False)
            if attempt["aggregate"] is not None:
                return dict(attempt["aggregate"])
            live = self.ids - state["revealed"].keys()
            scores = {k: v for k, v in attempt["scores"].items() if k in live}
            if len(live) < self.manifest["min_aggregate"]:
                _fail("aggregate_too_small", "المجموعة أصغر من حد الكشف")
            if set(scores) != live:
                _fail("scores_incomplete", "لا تجميع انتقائي قبل حكم كل الحالات الحية")
            result = {"n": len(scores), "passed": sum(scores.values()),
                      "attempts_used": len(state["attempts"]),
                      "attempts_declared": self.manifest["max_attempts"],
                      "live_cases": len(live), "revealed_cases": len(state["revealed"])}
            attempt["aggregate"] = result
            self._save(state)
            return dict(result)

    def reveal(self, case_id: str, *, actor: str, reason: str) -> Case:
        if not _identifier(case_id):
            _fail("case_invalid", "هوية حالة غير صالحة")
        if not all(isinstance(s, str) and s.strip() for s in (actor, reason)):
            _fail("reveal_metadata", "هوية الكاشف وسبب الكشف مطلوبان")
        try:
            _bytes({"actor": actor, "reason": reason})
        except UnicodeEncodeError:
            _fail("reveal_metadata", "بيانات كشف غير صالحة لـUTF-8")
        with self._lock():
            state = self._load()
            if case_id not in self.ids:
                _fail("case_unknown", "حالة غير معروفة")
            state["revealed"].setdefault(case_id, {"actor": actor, "reason": reason})
            for attempt in state["attempts"]:
                if attempt["aggregate"] is None:
                    attempt["scores"].pop(case_id, None)
            self._save(state)
            return next(c for c in self.cases if c.case_id == case_id)

    def status(self) -> dict:
        with self._lock():
            state = self._load()
            return {"attempts_used": len(state["attempts"]),
                    "attempts_declared": self.manifest["max_attempts"],
                    "attempt_open": bool(state["attempts"] and
                                         state["attempts"][-1]["aggregate"] is None),
                    "live_cases": len(self.ids - state["revealed"].keys()),
                    "revealed_cases": len(state["revealed"])}
