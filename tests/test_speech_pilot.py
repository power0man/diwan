from evaluation.speech_pilot import report, score


def test_edit_counts_penalize_deletion_substitution_and_insertions():
    row = score("أكل الولد تفاحة", "أكل بنت تفاحة كبيرة جدا")
    assert row["word_edits"] == 3 and row["reference_words"] == 3
    assert score("أكل الولد تفاحة", "أكل")["word_edits"] == 2
    assert score("أكل", "أكل كثيرا جدا")["word_edits"] == 2


def test_normalization_does_not_conflate_hamza_or_punctuation():
    assert score("أَكَلَ الـولد", "أكل الولد")["word_edits"] == 0
    assert score("أكل", "اكل")["word_edits"] == 1
    assert score("كتاب", "كتاب.")["word_edits"] == 1


def test_report_is_corpus_weighted_and_can_exceed_one():
    result = report([{"status": "ok", "reference": "ا ب ج", "text": "ا"},
                     {"status": "ok", "reference": "ا", "text": "ا د ه و"}])
    assert result["wer"] == 5 / 4
    assert result["cer"] == 10 / 6


def test_errors_and_silence_are_separate_and_never_release_ready():
    result = report([{"status": "error", "reference": "نص غير مسموع"},
                     {"status": "ok", "reference": "", "text": "مرحبا"}])
    assert result["errors"] == 1 and result["completed"] == 1
    assert result["wer"] is None and result["cer"] is None
    assert result["silence_hallucinations"] == 1
    assert result["synthetic_only"] is True and result["release_ready"] is False


def test_no_speech_is_a_completed_detection_not_an_error():
    result = report([{"status": "no_speech", "reference": "", "text": ""}])
    assert result["completed"] == 1 and result["errors"] == 0
    assert result["silence_hallucinations"] == 0 and result["wer"] is None
