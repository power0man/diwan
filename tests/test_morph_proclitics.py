from tools.measure_morphology import strip_arabic_proclitics

def test_strip_proclitic_al():
    assert strip_arabic_proclitics("الكتاب") == "كتاب"
    assert strip_arabic_proclitics("المحكمة") == "محكمة"

def test_strip_proclitic_short_word_preserved():
    assert strip_arabic_proclitics("ورد") == "ورد"
    assert strip_arabic_proclitics("أب") == "أب"

def test_strip_proclitic_waw():
    assert strip_arabic_proclitics("والكتاب") in ("كتاب", "الكتاب")
    assert strip_arabic_proclitics("فكتاب") == "كتاب"
