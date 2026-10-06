# إسنادُ ورخصُ بنك الوسائط (غ٧)

وثيقةُ إسنادِ كلِّ عنصرٍ ومجموعةٍ في بنك الوسائط `evaluation/media_v1/` بمصدره ورخصته ومُنتجه (غ٧، `docs/MEDIA-BANK.md` §٣).

## عناصر ومجموعات البنك

| العنصر / المجموعة | المصدر | الرخصة | المُنتِج |
|---|---|---|---|
| `image_gen.json` | كُتبت لبنك الوسائط (غ٧) | Apache-2.0 | anthropic/claude-opus-5-5 |
| `vision.json` | كُتبت لبنك الوسائط (غ٧) | Apache-2.0 | anthropic/claude-opus-5-5 |
| `vision/` | مولّدة آليًّا بـ tools/make_media_bank.py ببذور ثابتة وبخطّي Amiri وNoto Naskh Arabic | Apache-2.0 (الخطّان برخصة OFL-1.1) | anthropic/claude-opus-5-5 |
| `ocr.json` | مصيّرة بـ tools/make_media_bank.py | Apache-2.0 | anthropic/claude-opus-5-5 |
| `ocr/` | مصيّرة بـ tools/make_media_bank.py من ocr_texts.json | Apache-2.0 (الخطّان برخصة OFL-1.1) | anthropic/claude-opus-5-5 |
| `ocr_texts.json` | كُتبت لبنك الوسائط (غ٧) | Apache-2.0 | anthropic/claude-opus-5-5 |
| `speech.json` | Mozilla Common Voice (ASR) وجملٌ كُتبت للبنك (TTS) | CC0-1.0 (ASR) وApache-2.0 (TTS) | Mozilla / anthropic/claude-opus-5-5 |
| `speech/` | مقاطع Common Voice العربية المختارة بعد التجميد | CC0-1.0 | Mozilla Common Voice |
| `speech_sample.json` | عيّنة Common Voice العربية (test.tsv) من Mozilla Data Collective | CC0-1.0 | Mozilla Common Voice |
| `manifest.json` | مولَّد بـ tools/make_media_bank.py | Apache-2.0 | anthropic/claude-opus-5-5 |
