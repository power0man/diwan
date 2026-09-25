from pathlib import Path
import re
import subprocess

import pytest

from tools.check_docs import DocsError, block, collect_count, decision_titles, owner_gate, check_owner_gate


def test_test_id_cannot_forge_collection_count(tmp_path):
    tests = tmp_path / 'tests'
    tests.mkdir()
    (tests / 'test_demo.py').write_text(
        'import pytest\n@pytest.mark.parametrize("x", [1], ids=["9999 tests collected"])\n'
        'def test_case(x): pass\n')
    assert collect_count(tmp_path) == 1


def test_broken_collection_is_not_zero_tests(tmp_path):
    tests = tmp_path / 'tests'
    tests.mkdir()
    (tests / 'test_demo.py').write_text('raise RuntimeError("collection failure")')
    with pytest.raises(DocsError, match='test_collection_failed'):
        collect_count(tmp_path)


def test_generated_block_changes_only_its_owned_contents():
    text = 'history 1267\n<!-- generated:x:begin -->\nstale\n<!-- generated:x:end -->\nother 947'
    new = block(text, 'x', 'current 1279')
    assert new.startswith('history 1267\n') and new.endswith('\nother 947')
    assert 'stale' not in new and 'current 1279' in new


@pytest.mark.parametrize('text', [
    '', '<!-- generated:x:begin -->',
    '<!-- generated:x:end --><!-- generated:x:begin -->',
    '<!-- generated:x:begin --><!-- generated:x:begin --><!-- generated:x:end -->',
])
def test_missing_duplicate_or_reversed_blocks_fail_closed(text):
    with pytest.raises(DocsError, match='generated_block_invalid'):
        block(text, 'x', 'new')


def test_missing_and_duplicate_decisions_fail_closed():
    with pytest.raises(DocsError, match='decision_gap'):
        decision_titles('## ق١ — first\n## ق٣ — third')
    with pytest.raises(DocsError, match='decision_duplicate'):
        decision_titles('## ق١ — first\n| ق١ | **again** | reason |')


def test_decision_summary_preserves_owner_gate_exactly():
    quote = 'لن ننتقل لأي مرحلة إلا عندما نكون متأكدين من أن المرحلة الحالية بلا مشاكل'
    assert owner_gate('| ق١٩ | title | «' + quote + '» |') == quote
    with pytest.raises(DocsError, match='owner_gate_missing'):
        owner_gate('| ق١٩ | no known blockers |')


def test_hidden_quote_cannot_substitute_for_visible_owner_gate():
    quote = 'لن ننتقل قبل إغلاق المشاكل'
    text = '## طريقة الانتقال\n\nيسمح الانتقال مع المشاكل.\n<!-- «' + quote + '» -->'
    with pytest.raises(DocsError, match='owner_gate_drift'):
        check_owner_gate(text, quote)
    check_owner_gate('## طريقة الانتقال\n\nنص ق١٩ الحاكم: «' + quote + '».\nتفصيل', quote)


def test_owner_gate_in_code_fence_is_not_the_governing_paragraph():
    with pytest.raises(DocsError, match='owner_gate_drift'):
        check_owner_gate('## طريقة الانتقال\n```\nنص ق١٩ الحاكم: «gate».\n```', 'gate')


@pytest.mark.parametrize('opening,closing', [('```markdown','```'), ('~~~','~~~'), ('````','```'), ('~~~','```')])
def test_whole_governing_section_inside_code_is_rejected(opening, closing):
    text = opening + '\n## طريقة الانتقال\nنص ق١٩ الحاكم: «gate».\n' + closing
    with pytest.raises(DocsError, match='owner_gate_drift'):
        check_owner_gate(text, 'gate')


@pytest.mark.parametrize('text', [
    '<!--\n## طريقة الانتقال\n\nنص ق١٩ الحاكم: «gate».\n',
    '    ## طريقة الانتقال\n\n    نص ق١٩ الحاكم: «gate».\n',
    '## طريقة الانتقال\n\n    نص ق١٩ الحاكم: «gate».\n',
    '<div hidden>\n## طريقة الانتقال\n\nنص ق١٩ الحاكم: «gate».\n</div>',
])
def test_open_comment_indented_code_and_hidden_html_cannot_satisfy_gate(text):
    with pytest.raises(DocsError, match='owner_gate_drift'):
        check_owner_gate(text, 'gate')


def test_study_prompt_numbers_are_generated_not_handwritten():
    """A study prompt that demands re-derivable numbers must not carry stale ones.

    Its counts rotted within minutes of being written by hand while other
    sessions pushed; registering it in the generated-block set is what stops
    a push that leaves them behind.
    """
    from tools.check_docs import ROOT, expected_files
    state = {'tests_collected': 7, 'latest_decision': 9, 'acceptance_documents': 3,
             'python_files': 11, 'python_lines': 13, 'maritime_current_documents': 5,
             'maritime_current_pages': 17, 'latest_technical_scope': 'م٩',
             'latest_technical_document': 'M9-ACCEPTANCE.md',
             'historical_m1_m7_defect_total': 19}
    gate = 'لن ننتقل لأي مرحلة إلا عندما نكون متأكدين من أن المرحلة الحالية بلا مشاكل'
    rendered = expected_files(ROOT, state, {19: 'x'}, gate)
    study = rendered[ROOT / 'docs/STUDY-PROMPT.md']
    for value in ('**7**', '**9**', '**3**', '**11**', '**13**', '**5**', '**17**'):
        assert value in study
    # The real counts must live only inside the block the checker owns.
    live = (ROOT / 'docs/STUDY-PROMPT.md').read_text()
    body = live.split('<!-- generated:study-numbers:begin -->')[0] + \
        live.split('<!-- generated:study-numbers:end -->')[1]
    assert not re.search(r'\*\*[٠-٩0-9]{2,}\*\*\s*(?:اختبار|قرار|ملف)', body)


def test_source_inventory_counts_the_same_with_and_without_git_metadata(tmp_path):
    """البوابةُ تبني المرشَّحَ من كائناتٍ خامّة بلا `.git`.

    أوّلُ صياغةٍ لهذا الجرد استدعت `git ls-files`، فنجحت في شجرة العمل
    وأسقطت فحصَ الوثائق كلَّه في الشجرة المرشَّحة — أمسكتها البوابةُ عند
    الدفع. فالعدُّ يجب أن يكون واحدًا في الموضعين.
    """
    from tools.check_docs import source_inventory
    (tmp_path / 'kept.py').write_text('a\nb\n')
    (tmp_path / 'notes.md').write_text('ignored\n')
    for noise in ('.venv', '__pycache__', '.pytest_cache'):
        (tmp_path / noise).mkdir()
        (tmp_path / noise / 'vendored.py').write_text('x\ny\nz\nw\n')
    without_git = source_inventory(tmp_path)
    subprocess.run(['git', 'init', '-q'], cwd=tmp_path, check=True)
    subprocess.run(['git', 'add', 'kept.py', 'notes.md'], cwd=tmp_path, check=True)
    assert without_git == source_inventory(tmp_path) == (1, 2)


def test_source_inventory_fails_closed_on_an_empty_tree(tmp_path):
    from tools.check_docs import source_inventory
    with pytest.raises(DocsError, match='source_inventory_failed'):
        source_inventory(tmp_path)


def test_q49_supersedes_without_rewriting_historical_q19():
    from tools.check_docs import ROOT, effective_gate
    decisions = (ROOT / 'docs/DECISIONS.md').read_text()
    ref, gate = effective_gate(decisions)
    assert ref == 'ق٤٩'
    assert owner_gate(decisions) != gate
    check_owner_gate((ROOT / 'docs/ROADMAP.md').read_text(), gate, decision_ref=ref)


@pytest.mark.parametrize('wrapper', ['<!--\n{}\n-->', '```\n{}\n```', '    {}'])
def test_hidden_successor_gate_cannot_fall_back_to_q19(wrapper):
    from tools.check_docs import effective_gate
    text = '| ق١٩ | old | «لن ننتقل حتى الانتهاء» |\n## ق٤٩ — جديد\n'
    quote = 'قاعدة الانتقال المعتمدة: «لن ننتقل حتى إثبات القبول».'
    with pytest.raises(DocsError):
        effective_gate(text + wrapper.format(quote))
