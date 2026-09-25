"""التقطيع الدلالي الهيكلي للنصوص واللوائح والأنظمة الرسمية العربية (Track A).

تحويل نصوص المواد واللوائح القانونية من كتل صفحات ممتدة إلى مقاطع تشريعية
ذرية (Atomic Legislative Chunks) على مستوى:
  - المادة (Article)
  - الفقرة أو البند (Clause / Paragraph)
  - التعريف المعجمي المصطلحي (Definition Item)

مع حفظ:
  1. موضع الاستدلال الدقيق (Locus)
  2. سياق السند والوثيقة الأصلية
  3. استخراج المصطلحات والكلمات المفتاحية
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
import unicodedata

_ARTICLE_RE = re.compile(
    r"(?:^|\n)(المادة\s+(?:\([0-9٠-٩]+\)|[0-9٠-٩]+|[ء-ي]+)(?:\s*:\s*[^\n]+|\s*\n|$))",
    re.MULTILINE
)
_ARTICLE_NUM_RE = re.compile(r"المادة\s*\(?([0-9٠-٩]+)\)?")
_CLAUSE_RE = re.compile(r"(?:^|\n)([0-9٠-٩]+[-–\.]|\([0-9٠-٩]+\)|[أ-ي][-–\.]|\([أ-ي]\))\s+")
_DEF_LINE_RE = re.compile(r"(?:^|\n)([^:\n]{2,60})\s*:\s*([^\n]+(?:\n(?![^:\n]{2,60}\s*:)[^\n]+)*)")
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


@dataclass(frozen=True)
class LegislativeChunk:
    chunk_id: str
    doc_id: str
    part: str
    locus: str
    heading: str
    text: str
    article_number: int | None
    digest: str

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "part": self.part,
            "locus": self.locus,
            "heading": self.heading,
            "text": self.text,
            "article_number": self.article_number,
            "digest": self.digest,
        }


class LegislativeChunker:
    """مقطّع تشريعي بنيوي للنصوص واللوائح والأنظمة العربية."""

    @staticmethod
    def _compute_digest(text: str) -> str:
        return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()

    @staticmethod
    def _parse_article_num(heading: str) -> int | None:
        m = _ARTICLE_NUM_RE.search(heading)
        if m:
            raw = m.group(1).translate(_AR_DIGITS)
            try:
                return int(raw)
            except ValueError:
                return None
        return None

    def chunk_text(self, text: str, doc_id: str = "", part: str = "",
                   base_locus: str = "") -> list[LegislativeChunk]:
        clean_text = text.strip()
        if not clean_text:
            return []

        # 1. إذا كان النص يحتوي على مادة أو أكثر
        matches = list(_ARTICLE_RE.finditer(clean_text))
        if not matches:
            # نص حر بلا مادة صريحة: يقطّع بالفقرات
            return self._chunk_free_text(clean_text, doc_id, part, base_locus)

        chunks: list[LegislativeChunk] = []
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(clean_text)
            art_block = clean_text[start:end].strip()
            heading_line = m.group(1).strip()
            art_num = self._parse_article_num(heading_line)
            art_locus = heading_line.split(":", 1)[0].strip()
            if base_locus and art_locus not in base_locus:
                art_locus = f"{base_locus} - {art_locus}"

            body = art_block[len(m.group(1)):].strip()
            # فحص إن كانت مادة تعريفات
            if "التعريف" in heading_line or "يقصد بالمصطلحات" in body or "يقصد بالعبارات" in body:
                def_chunks = self._chunk_definitions(body, doc_id, part, art_locus, heading_line, art_num)
                if def_chunks:
                    chunks.extend(def_chunks)
                    continue

            # فحص إن كانت مادة مقسمة إلى بنود وفقرات مرقمة
            clause_chunks = self._chunk_clauses(body, doc_id, part, art_locus, heading_line, art_num)
            if clause_chunks:
                chunks.extend(clause_chunks)
            else:
                chunk_id = hashlib.sha256(f"{doc_id}:{art_locus}:{art_block}".encode()).hexdigest()[:16]
                chunks.append(LegislativeChunk(
                    chunk_id=chunk_id,
                    doc_id=doc_id,
                    part=part,
                    locus=art_locus,
                    heading=heading_line,
                    text=art_block,
                    article_number=art_num,
                    digest=self._compute_digest(art_block),
                ))

        return chunks

    def _chunk_definitions(self, body: str, doc_id: str, part: str,
                           art_locus: str, heading: str, art_num: int | None) -> list[LegislativeChunk]:
        defs = _DEF_LINE_RE.findall(body)
        if not defs or len(defs) < 2:
            return []

        out: list[LegislativeChunk] = []
        for term, meaning in defs:
            term = term.strip().strip("-•*")
            meaning = meaning.strip()
            if not term or not meaning:
                continue
            if any(w in term for w in ("يقصد", "تطبيق", "أحكام", "احكام")):
                continue
            item_text = f"{term}: {meaning}"
            locus = f"{art_locus} - تعريف ({term})"
            chunk_id = hashlib.sha256(f"{doc_id}:{locus}".encode()).hexdigest()[:16]
            out.append(LegislativeChunk(
                chunk_id=chunk_id,
                doc_id=doc_id,
                part=part,
                locus=locus,
                heading=f"{heading} ({term})",
                text=item_text,
                article_number=art_num,
                digest=self._compute_digest(item_text),
            ))
        return out

    def _chunk_clauses(self, body: str, doc_id: str, part: str,
                       art_locus: str, heading: str, art_num: int | None) -> list[LegislativeChunk]:
        matches = list(_CLAUSE_RE.finditer(body))
        if not matches or len(matches) < 2:
            return []

        out: list[LegislativeChunk] = []
        # المقدمة قبل أول بند إن وجدت
        intro = body[:matches[0].start()].strip()
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
            clause_num = m.group(1).strip()
            clause_text = body[start:end].strip()
            full_clause = f"{intro}\n{clause_text}".strip() if intro else clause_text
            locus = f"{art_locus} - الفقرة ({clause_num})"
            chunk_id = hashlib.sha256(f"{doc_id}:{locus}".encode()).hexdigest()[:16]
            out.append(LegislativeChunk(
                chunk_id=chunk_id,
                doc_id=doc_id,
                part=part,
                locus=locus,
                heading=f"{heading} [{clause_num}]",
                text=full_clause,
                article_number=art_num,
                digest=self._compute_digest(full_clause),
            ))
        return out

    def _chunk_free_text(self, text: str, doc_id: str, part: str,
                         base_locus: str) -> list[LegislativeChunk]:
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            return []

        out: list[LegislativeChunk] = []
        for i, p in enumerate(paragraphs, 1):
            locus = f"{base_locus} - ف{i}" if base_locus else f"فقرة {i}"
            chunk_id = hashlib.sha256(f"{doc_id}:{locus}:{p}".encode()).hexdigest()[:16]
            out.append(LegislativeChunk(
                chunk_id=chunk_id,
                doc_id=doc_id,
                part=part,
                locus=locus,
                heading=locus,
                text=p,
                article_number=None,
                digest=self._compute_digest(p),
            ))
        return out
