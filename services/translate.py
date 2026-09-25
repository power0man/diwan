"""خدمة الترجمة — تركيبٌ رقيق فوق عقدة اللغويات (م٦، ق٢٠).

الخدمة لا تملك مسردًا ولا معرفة: تفوّض إلى `LinguisticsNode.translate`
(بوابة المسرد الصرفية وبوابة الحقوق كلتاهما في العقدة — م٥) بميزانية
الخدمة وسجل تشغيلتها، وتختم بقيد `service_bill` — فاتورةُ التعريب
كاملةً في سجلٍّ واحد كما البحث سواء.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from run_node import load_node_module


class TranslateService:
    def __init__(self, root: Path, provider, budget, runs_dir=None,
                 ledger=None):
        self.root = root
        self.provider = provider
        self.budget = budget
        # سجلٌّ لكل تشغيلة بمفتاحها (كما البحث) — وledger المحقون
        # عقدُ حصرٍ على المستدعي (وضع الاختبار)
        self.runs_dir = Path(runs_dir) if runs_dir \
            else root / "var" / "services"
        self.ledger = ledger
        self._mod = load_node_module("linguistics")

    @staticmethod
    def run_key_of(model: str, source_id: str, text: str) -> str:
        import hashlib
        return hashlib.sha256(
            (model + "|tr|" + source_id + "|" + text)
            .encode()).hexdigest()[:24]

    def run(self, text: str, source_id: str, locus: str,
            part: str = "", lang: str = "en") -> dict:
        run_key = self.run_key_of(self.provider.model, source_id, text)
        if self.ledger is None:
            from core.ledger import Ledger
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            self.ledger = Ledger(self.runs_dir
                                 / f"translate-{run_key}.jsonl")
        if self.ledger.count():
            self.ledger.verify_chain()   # لا استهلاك عرضٍ فوق سجل مكسور
        if not any(r.get("kind") == "service_run"
                   and r.get("run_key") == run_key
                   for r in (e["record"] for e in self.ledger.entries())):
            self.ledger.append({"kind": "service_run",
                                "service": "translate",
                                "run_key": run_key, "locus": locus})
        node = self._mod.LinguisticsNode(self.root, self.provider,
                                         self.budget, self.ledger)
        r = node.translate(text, source_id=source_id, locus=locus,
                           part=part, lang=lang)
        recs_all = [e["record"] for e in self.ledger.entries()]
        calls = [x for x in recs_all
                 if x.get("kind") in ("ok", "error")]
        bill = {"kind": "service_bill", "service": "translate",
                "run_key": run_key, "calls": len(calls),
                "settled_micros": sum(c.get("settled_micros", 0)
                                      for c in calls)}
        last_bill = next((x for x in reversed(recs_all)
                          if x.get("kind") == "service_bill"
                          and x.get("run_key") == run_key), None)
        if last_bill != bill:
            self.ledger.append(bill)
        self.ledger.anchor()   # رسوُّ التشغيلة عند ختامها
        return {**r, "bill": bill}
