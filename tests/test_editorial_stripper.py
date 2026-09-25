"""اختبارات أداة تجريد المتون التراثية من الزيادات التحريرية (المسار ب - م١٩)."""
import pytest

from tools.editorial_stripper import EditorialStripper


def test_strip_footnotes_and_editorial_remarks():
    text = (
        "بحر: الباء والحاء والراء أصلان متباينان: أحدهما يدل على السعة، والآخر على داء.\n"
        "[1] راجع لسان العرب طبعة دار المعارف ص 201.\n"
        "والبَحْرُ: الماء الكثير، سُمّي لسعته.\n"
        "(تعليق المحقق: في نسخة الرياض وردت بلفظ آخر)\n"
        "قال الشاعر: هو البحر من أي النواحي أتيته."
    )
    clean, removed = EditorialStripper.strip_text(text)

    assert "دار المعارف" not in clean
    assert "تعليق المحقق" not in clean
    assert "الباء والحاء والراء أصلان" in clean
    assert "والبَحْرُ: الماء الكثير" in clean
    assert "قال الشاعر: هو البحر" in clean
    assert len(removed) >= 2


def test_strip_pagination_and_apparatus():
    text = (
        "=== فهرس الأبواب ===\n"
        "[ص: 450]\n"
        "فصل الهمزة مع الحاء: أحد: الهمزة والحاء والدال أصل واحد يدل على الانفراد."
    )
    clean, removed = EditorialStripper.strip_text(text)

    assert "=== فهرس الأبواب ===" not in clean
    assert "[ص: 450]" not in clean
    assert "أحد: الهمزة والحاء والدال أصل واحد يدل على الانفراد." in clean


def test_strip_pure_classical_text_untouched():
    text = "شمس: الشين والميم والسين أصل يدل على ضياء وحرارة، ومنه الشَّمْسُ جِرمُ الفلك."
    clean, removed = EditorialStripper.strip_text(text)

    assert clean == text
    assert len(removed) == 0


def test_process_record_legal_basis():
    record = {
        "item": {
            "text": "قمر: القاف والميم والراء أصل يدل على بياض.\n[1] طبعة دار صادر ص 15.",
            "lang": "ar",
            "domain": "lexicon",
        }
    }
    processed = EditorialStripper.process_record(record)
    item = processed["item"]

    assert item["originality"] == "classical_public_domain"
    assert "المادة 19" in item["legal_basis"]
    assert item["editorial_apparatus_stripped"] is True
    assert "طبعة دار صادر" not in item["text"]
    assert "قمر: القاف والميم والراء أصل يدل على بياض." in item["text"]
