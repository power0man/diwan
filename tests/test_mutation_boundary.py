import pytest
from tools.mutation_check import verify_test_suite_not_empty, EmptyTestSuiteError

def test_verify_non_empty_test_suite():
    assert verify_test_suite_not_empty(["tests/test_a.py", "tests/test_b.py"]) is True

def test_verify_empty_test_suite_raises():
    with pytest.raises(EmptyTestSuiteError, match="مجموعة الاختبارات فارغة"):
        verify_test_suite_not_empty([])
