from core.glossary import normalize_glossary_key, Glossary

def test_normalize_glossary_key_alef():
    assert normalize_glossary_key("إجراء") == "اجراء"
    assert normalize_glossary_key("أمن") == "امن"
    assert normalize_glossary_key("آلية") == "الية"
    assert normalize_glossary_key("ٱستقرار") == "استقرار"

def test_normalize_glossary_key_tatweel():
    assert normalize_glossary_key("تـطـويـل") == "تطويل"
    assert normalize_glossary_key("شـــورى") == "شورى"

def test_normalize_glossary_key_edge_cases_none_and_empty():
    assert normalize_glossary_key(None) == ""
    assert normalize_glossary_key("") == ""
    assert normalize_glossary_key("   ") == ""
    assert normalize_glossary_key(123) == ""

def test_normalize_glossary_key_whitespace_and_non_arabic():
    assert normalize_glossary_key("  إجراء   أمني  ") == "اجراء امني"
    assert normalize_glossary_key("  Term   إجراء_01  ") == "Term اجراء_01"

def test_normalize_glossary_key_no_side_effects():
    raw = "  تـطـويـل   الـعـقـد  "
    res = normalize_glossary_key(raw)
    assert res == "تطويل العقد"
    assert raw == "  تـطـويـل   الـعـقـد  "
