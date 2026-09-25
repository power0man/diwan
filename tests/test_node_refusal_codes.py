from types import SimpleNamespace

import pytest

from core.budget import BudgetRefused
from core.canonical import PayloadRejected
from tools.run_node import refusal_details, refuse_event


@pytest.mark.parametrize('exc,code', [
    (BudgetRefused('day_cap', 'نفد الحد'), 'day_cap'),
    (PayloadRejected('item', 'source_unacquired', 'مصدر غير مكتسب'), 'source_unacquired'),
])
def test_domain_code_survives_outgoing_refusal(exc, code):
    query = SimpleNamespace(from_node='gateway', correlation_id='q', data_policy='internal')
    result = refuse_event('maritime', query, 'a' * 64, *refusal_details(exc))
    assert result.payload['code'] == code
    assert result.payload['reason'] == exc.reason


@pytest.mark.parametrize('exc', [ValueError('private text'), FileNotFoundError('/private/location')])
def test_unexpected_exception_does_not_publish_class_or_private_detail(exc):
    code, reason = refusal_details(exc)
    assert code == 'node_execution_failed'
    assert type(exc).__name__ not in code + reason and str(exc) not in reason


def test_untrusted_code_shape_is_not_exported():
    exc = RuntimeError('private')
    exc.code, exc.reason = 'RawClassName', 'private'
    assert refusal_details(exc)[0] == 'node_execution_failed'
