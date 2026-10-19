from tools.measure_morphology import strip_arabic_proclitics

def test_strip_proclitic_al():
    assert strip_arabic_proclitics("الكتاب") == "كتاب"
    assert strip_arabic_proclitics("المحكمة") == "محكمة"

def test_strip_proclitic_composite_and_fal():
    assert strip_arabic_proclitics("فالشمس") == "شمس"
    assert strip_arabic_proclitics("والقلم") == "قلم"
    assert strip_arabic_proclitics("كالنجم") == "نجم"
    assert strip_arabic_proclitics("بالحق") == "حق"
    assert strip_arabic_proclitics("للناس") == "ناس"

def test_strip_proclitic_single_letter():
    assert strip_arabic_proclitics("فكتاب") == "كتاب"
    assert strip_arabic_proclitics("وسلام") == "سلام"
    assert strip_arabic_proclitics("بقلم") == "قلم"

def test_strip_proclitic_short_word_preserved():
    assert strip_arabic_proclitics("ورد") == "ورد"
    assert strip_arabic_proclitics("أب") == "أب"
    assert strip_arabic_proclitics("في") == "في"
    assert strip_arabic_proclitics("بي") == "بي"
    assert strip_arabic_proclitics("لا") == "لا"

def test_strip_proclitic_edge_cases():
    assert strip_arabic_proclitics("") == ""
    assert strip_arabic_proclitics(None) is None
