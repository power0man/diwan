#!/usr/bin/env python3
"""Derive bounded current-status blocks; historical acceptance reports stay historical.

This checks documented counts, not test success, quality or release readiness.
Run with the reference Python: tools/check_docs.py --write, then --check in CI.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.corpus import CorpusCatalog
from core.public_export import excluded_streams

AR = str.maketrans('0123456789', '٠١٢٣٤٥٦٧٨٩')


class DocsError(ValueError):
    pass


def decision_titles(text):
    titles = {}
    for line in text.splitlines():
        table = re.match(r'\| ق([٠-٩0-9]+) \| (.*?) \|', line)
        heading = re.match(r'## ق([٠-٩0-9]+) — (.+)', line)
        if not (table or heading):
            continue
        number = int((table or heading)[1])
        if number in titles:
            raise DocsError('decision_duplicate')
        title = (table or heading)[2]
        if table:
            bold = re.search(r'\*\*(.+?)\*\*', title)
            title = bold[1] if bold else title
        titles[number] = title
    if not titles or set(titles) != set(range(1, max(titles) + 1)):
        raise DocsError('decision_gap')
    return titles


def owner_gate(decisions):
    line = next((line for line in decisions.splitlines() if line.startswith('| ق١٩ |')), '')
    match = re.search(r'«(لن ننتقل[^»]+)»', line)
    if not match:
        raise DocsError('owner_gate_missing')
    return match[1]


def effective_gate(decisions):
    """Preserve q19 as history; require an explicit visible successor policy."""
    if not re.search(r"^## ق٤٩ — ", decisions, re.MULTILINE):
        return "ق١٩", owner_gate(decisions)
    body = decisions.split("## ق٤٩ — ", 1)[1].split("\n## ", 1)[0]
    matches = re.findall(r'^قاعدة الانتقال المعتمدة: «(لن ننتقل[^»]+)»\.$', body, re.MULTILINE)
    if len(matches) != 1:
        raise DocsError("effective_gate_missing")
    # Reuse rendering checks to prevent a quote hidden in a fence or comment.
    visible = body.replace("قاعدة الانتقال المعتمدة:", "نص ق٤٩ الحاكم:")
    check_owner_gate("## طريقة الانتقال\n" + visible[visible.find("\n") + 1:],
                     matches[0], decision_ref="ق٤٩", allow_preamble=True)
    return "ق٤٩", matches[0]


def check_owner_gate(roadmap, gate, *, decision_ref="ق١٩", allow_preamble=False):
    visible = re.sub(r'<!--.*?(?:-->|$)', '', roadmap, flags=re.DOTALL)
    lines, fence = [], None
    for line in visible.splitlines():
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if fence:
            if (marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1]
                    and not marker[2].strip()):
                fence = None
            continue
        if marker and (marker[1][0] != '`' or '`' not in marker[2]):
            fence = (marker[1][0], len(marker[1]))
            continue
        lines.append(line)
    # This policy document uses plain Markdown, without raw HTML containers.
    # Reject a hidden HTML wrapper instead of claiming to interpret its rendering.
    if any(re.match(r'^\s*</?[A-Za-z]', line) for line in lines):
        raise DocsError('owner_gate_drift')
    headings = [i for i, line in enumerate(lines) if line == '## طريقة الانتقال']
    if len(headings) != 1:
        raise DocsError('owner_gate_drift')
    section = []
    for line in lines[headings[0] + 1:]:
        if line.startswith('## '):
            break
        if line.strip():
            section.append(line)
    expected = f'نص {decision_ref} الحاكم: «{gate}».'
    if not section or (not allow_preamble and section[0] != expected) or section.count(expected) != 1:
        raise DocsError('owner_gate_drift')


def block(text, name, value):
    begin, end = f'<!-- generated:{name}:begin -->', f'<!-- generated:{name}:end -->'
    if text.count(begin) != 1 or text.count(end) != 1 or text.index(begin) >= text.index(end):
        raise DocsError('generated_block_invalid')
    left, rest = text.split(begin)
    _, right = rest.split(end)
    return left + begin + '\n' + value.rstrip() + '\n' + end + right


def collect_count(root):
    result = subprocess.run([sys.executable, '-m', 'pytest', 'tests/', '--collect-only',
                             '-q', '-o', 'addopts='], cwd=root, capture_output=True, text=True)
    # Node IDs can contain arbitrary parametrization labels, including this text.
    # Only pytest's final, complete summary line is an authoritative count.
    lines = result.stdout.strip().splitlines()
    match = re.fullmatch(r'(\d+) tests? collected in [0-9.]+s', lines[-1]) if lines else None
    if result.returncode or not match:
        raise DocsError('test_collection_failed')
    return int(match[1])


# The gate materializes a candidate from raw blobs: that tree has no .git, so
# `git ls-files` there fails and the whole docs check with it. A plain walk with
# a fixed skip set gives the identical answer in both places — verified equal to
# `git ls-files '*.py'` on the working tree, which additionally holds .venv and
# caches that the candidate never has.
UNTRACKED_TREES = frozenset({'.git', '.venv', 'venv', '__pycache__',
                             '.pytest_cache', '.mypy_cache', 'node_modules',
                             '.opencode', '.agents'})


def source_inventory(root):
    """Python sources and their line count, counted the same way in any checkout."""
    files = lines = 0
    for path in sorted(root.rglob('*.py')):
        if (UNTRACKED_TREES & set(path.relative_to(root).parts)
                or any(part.startswith('.') for part in path.relative_to(root).parts[:-1])):
            continue
        files += 1
        lines += path.read_bytes().count(b'\n')
    if not files:
        raise DocsError('source_inventory_failed')
    return files, lines


def maritime_counts(root):
    """وثائقُ المخزن البحري النافذة وصفحاتُها: من الفهرس إن وُجد. وفي اللقطة العامة
    (ك٢٧)، حيث الفهرسُ مستبعَدٌ بعلامتها، تُحمل الأرقامُ كما نُشرت في
    `docs/probe/project-status.json` لأنها أرقامُ المستودع الخاص لا أرقامُ اللقطة؛
    وغيابُ الفهرس بلا علامةٍ عطبٌ مسمًّى لا رقمٌ محمول."""
    catalog_path = root / 'corpus/maritime/_catalog.jsonl'
    if catalog_path.exists():
        catalog = CorpusCatalog(catalog_path, create=False)
        catalog.verify(strict=True, root=root)
        current = catalog.current()
        return len(current), sum(record['pages'] for record in current.values())
    if 'corpus/maritime/_catalog.jsonl' in excluded_streams(root):
        published = json.loads((root / 'docs/probe/project-status.json').read_text())
        return published['maritime_current_documents'], published['maritime_current_pages']
    raise DocsError('maritime_catalog_missing')


STATUS_LIMITS = [
    "tests_collected_is_a_pytest_collection_count_not_a_pass_count",
    "python_files_and_lines_count_tracked_py_files_and_their_newlines_not_code_quality",
    "maritime_counts_come_from_the_owner_store_and_are_carried_as_published_where_the_store_is_excluded",
    "historical_defects_are_read_from_the_m1_to_m7_acceptance_documents_not_recounted",
]


def derive(root, test_count):
    decisions = (root / 'docs/DECISIONS.md').read_text()
    titles = decision_titles(decisions)
    gate_ref, gate = effective_gate(decisions)
    check_owner_gate((root / 'docs/ROADMAP.md').read_text(), gate, decision_ref=gate_ref)
    defects = {}
    for stage in range(1, 8):
        text = (root / f'docs/M{stage}-ACCEPTANCE.md').read_text()
        match = re.search(r'\*\*(\d+) (?:عيبًا|عيوب)\s+(?:مؤكدًا|مؤكدة)', text)
        if not match:
            raise DocsError('historical_defect_count_missing')
        defects[str(stage)] = int(match[1])
    maritime_documents, maritime_pages = maritime_counts(root)
    stages = []
    for path in (root / 'docs').glob('M*-ACCEPTANCE.md'):
        match = re.fullmatch(r'M(\d+)([A-Z]?)-ACCEPTANCE.md', path.name)
        if match:
            stages.append((int(match[1]), match[2], path.name))
    stage, part, document = max(stages)
    label = 'م' + str(stage).translate(AR) + ({'A': '-أ', 'B': '-ب'}.get(part, part))
    python_files, python_lines = source_inventory(root)
    state = {'schema_version': 1, 'tests_collected': test_count,
             'latest_technical_document': document, 'latest_technical_scope': label,
             'latest_decision': max(titles), 'historical_m1_m7_defects': defects,
             'historical_m1_m7_defect_total': sum(defects.values()),
             'maritime_current_documents': maritime_documents,
             'maritime_current_pages': maritime_pages,
             'acceptance_documents': len(stages), 'python_files': python_files,
             'python_lines': python_lines,
             'quality_review': 'automated_multi_system_pending', 'active_gate': gate_ref, 'release_ready': False,
             # كلُّ رقمٍ في docs/probe بحدوده (دليلُ المراجعة §٢): هذه أعدادُ جردٍ لا قياسُ جودة
             'measurement_limits': STATUS_LIMITS}
    return state, titles, gate


def expected_files(root, state, titles, gate):
    readme = (f"أحدث نطاق تقني موثق: **{state['latest_technical_scope']}** "
              f"(`docs/{state['latest_technical_document']}`).\n"
              f"الاختبارات المجمعة حاليًا: **{state['tests_collected']}**؛ "
              "هذا عدد جمع، ونجاح التشغيل له دليله المنفصل.\n"
              f"حصيلة عيوب تدقيق م١..م٧ التاريخية: **{state['historical_m1_m7_defect_total']}**، "
              "بجمع أعداد أدلة المراحل السبع، دون دمج عيوب الدفعات اللاحقة.\n"
              "قبول الجودة الآلي متعدد الأنظمة معلق؛ `release_ready=false`.")
    summary = ['| # | القرار (مشتق من سجل القرارات) |', '|---|---|']
    for number, title in sorted(titles.items()):
        title = f'«{owner_gate((root / "docs/DECISIONS.md").read_text())}» (تاريخي؛ ق٤٩ لاحق)' if number == 19 else title
        summary.append(f'| ق{str(number).translate(AR)} | {title} |')
    current = (f"**الحالة الحالية للمخزن:** {state['maritime_current_documents']} وثيقة، "
               f"{state['maritime_current_pages']} صفحة في النسخ النافذة.\n"
               "الأرقام في سرد إنجاز م٢ أدناه تاريخية، قبل تصحيحات المراحل اللاحقة.")
    # A study prompt whose header demands every number be re-derivable cannot carry
    # hand-written counts: concurrent sessions rot them within minutes.
    study = '\n'.join([
        '| المقياس | القيمة | كيف تتحقق بنفسك |',
        '|---|---|---|',
        f"| اختبارات مجموعة | **{state['tests_collected']}** | `.venv/bin/python -m pytest` "
        "(هذا عدد جمع؛ النجاح له دليله المنفصل) |",
        f"| قرارات | **{state['latest_decision']}** بلا فجوة | "
        "`grep -oE 'ق[٠-٩]+' docs/DECISIONS.md \\| sort -u \\| wc -l` |",
        f"| وثائق قبول | **{state['acceptance_documents']}** | `ls docs/M*-ACCEPTANCE.md` |",
        f"| ملفات بايثون | **{state['python_files']}** / **{state['python_lines']}** سطرًا | "
        "`git ls-files '*.py' \\| xargs wc -l` |",
        f"| المخزن البحري النافذ | **{state['maritime_current_documents']}** وثيقة / "
        f"**{state['maritime_current_pages']}** صفحة | `docs/probe/project-status.json` |",
        f"| أحدث نطاق موثق | **{state['latest_technical_scope']}** | "
        f"`docs/{state['latest_technical_document']}` |",
    ])
    results = {}
    for name, marker, value in (
        ('README.md', 'current-status', readme),
        ('docs/HANDOFF-PROMPT.md', 'decision-summary', '\n'.join(summary)),
        ('docs/M2-ACCEPTANCE.md', 'current-maritime', current),
        ('docs/STUDY-PROMPT.md', 'study-numbers', study),
    ):
        path = root / name
        results[path] = block(path.read_text(), marker, value)
    results[root / 'docs/probe/project-status.json'] = json.dumps(state, ensure_ascii=False, indent=2) + '\n'
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true')
    group.add_argument('--write', action='store_true')
    args = parser.parse_args(argv)
    try:
        state, titles, gate = derive(ROOT, collect_count(ROOT))
        expected = expected_files(ROOT, state, titles, gate)
        stale = [path for path, value in expected.items() if not path.exists() or path.read_text() != value]
        if args.write:
            for path in stale:
                path.write_text(expected[path])
        elif stale:
            print(json.dumps({'status': 'stale', 'files': [str(p.relative_to(ROOT)) for p in stale]}))
            return 1
        print(json.dumps({'status': 'updated' if args.write else 'verified', 'counts': state}, ensure_ascii=False))
        return 0
    except DocsError as exc:
        print(json.dumps({'status': 'error', 'code': str(exc)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
