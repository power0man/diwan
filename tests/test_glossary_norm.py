from core.glossary import normalize_glossary_key

def test_normalize_glossary_key_alef():
    assert normalize_glossary_key("إجراء") == "اجراء"
    assert normalize_glossary_key("أمن") == "امن"
    assert normalize_glossary_key("آلية") == "الية"

def test_normalize_glossary_key_tatweel():
    assert normalize_glossary_key("تـطـويـل") == "تطويل"
    assert normalize_glossary_key("شـــورى") == "شورى"
