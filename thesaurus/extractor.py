"""Извлечение имён собственных из текста и их классификация P / A / G.

* Natasha NER находит PER и LOC и приводит их к начальной форме
  («Александра Павловича» -> «Александр Павлович»).
* LOC делится на артефакты (дворец, павильон, зал...) и геообъекты (город,
  парк, река...) по ключевому существительному.
* Дополнительно ищутся артефакты, которые NER пропускает:
  «Китайский театр» (прилагательное с заглавной + существительное-тип) и
  названия в кавычках после слова-типа («картина «Вид на Камеронову галерею»»).
* Роль персоналии берётся из ключевых слов в окне контекста вокруг упоминания.
"""

import logging
import re
import warnings
from dataclasses import dataclass, field

from .config import (ARTEFACT_NOUNS, CONTEXT_WINDOW, GEO_NOUNS, QUOTED_TITLE_RE,
                     ROLE_KEYWORDS, STOP_NAMES)

warnings.filterwarnings("ignore", category=UserWarning, module="pymorphy2")
log = logging.getLogger(__name__)

_ROLE_RES = [(re.compile(rf"\b(?:{pat})", re.IGNORECASE), role) for pat, role in ROLE_KEYWORDS]
_ROMAN_RE = re.compile(r"^[IVXLC]+$")


@dataclass
class Mention:
    section: str            # P / A / G
    name: str               # нормализованная форма
    form: str               # как в тексте
    start: int
    stop: int
    role_type: str = None
    keywords: list = field(default_factory=list)


class Extractor:
    def __init__(self):
        from natasha import (Doc, MorphVocab, NewsEmbedding, NewsMorphTagger,
                             NewsNERTagger, Segmenter)
        from natasha.norm import normalize

        self._Doc = Doc
        self._normalize = normalize
        self.segmenter = Segmenter()
        self.morph_vocab = MorphVocab()
        emb = NewsEmbedding()
        self.morph_tagger = NewsMorphTagger(emb)
        self.ner_tagger = NewsNERTagger(emb)

    # ------------------------------------------------------------ helpers
    def _doc(self, text):
        doc = self._Doc(text)
        doc.segment(self.segmenter)
        doc.tag_morph(self.morph_tagger)
        doc.tag_ner(self.ner_tagger)
        for token in doc.tokens:
            token.lemmatize(self.morph_vocab)
        return doc

    def lemma(self, word):
        parses = self.morph_vocab.parse(word)
        return parses[0].normal if parses else word.lower()

    @staticmethod
    def find_role(text):
        """Первая (по порядку ROLE_KEYWORDS) роль, встретившаяся в тексте."""
        found = []
        for rx, role in _ROLE_RES:
            m = rx.search(text)
            if m:
                found.append((m.start(), role))
        return [r for _, r in sorted(found)]

    def classify_place(self, lemmas):
        """По леммам слов названия: ('A'|'G', тип) или None."""
        for lem in reversed(lemmas):          # главное слово обычно последнее
            if lem in ARTEFACT_NOUNS:
                return "A", ARTEFACT_NOUNS[lem]
            if lem in GEO_NOUNS:
                return "G", GEO_NOUNS[lem]
        for lem in lemmas:
            if lem in ARTEFACT_NOUNS:
                return "A", ARTEFACT_NOUNS[lem]
            if lem in GEO_NOUNS:
                return "G", GEO_NOUNS[lem]
        return None

    @staticmethod
    def _clean(name):
        name = re.sub(r"\s+", " ", name).strip(" -–—,.;:")
        if len(name) < 3 or name.lower() in STOP_NAMES:
            return None
        if not any(ch.isupper() for ch in name):
            return None
        return name

    def _context(self, text, start, stop):
        """Окно вокруг упоминания, обрезанное по границам предложения."""
        left = text[max(0, start - CONTEXT_WINDOW):start]
        right = text[stop:stop + CONTEXT_WINDOW]
        left = re.split(r"[.!?]\s", left)[-1]
        right = re.split(r"[.!?]\s", right)[0]
        return left, right

    # ------------------------------------------------------------ main
    def extract(self, text):
        doc = self._doc(text)
        mentions = []
        taken = []  # занятые интервалы, чтобы не дублировать пересечения

        def overlaps(a, b):
            return any(a < s2 and s1 < b for s1, s2 in taken)

        # 1. Natasha NER
        for span in doc.spans:
            span.normalize(self.morph_vocab)
            name = self._clean(span.normal or span.text)
            if not name:
                continue
            lemmas = [t.lemma for t in span.tokens]
            if span.type == "PER":
                left, right = self._context(text, span.start, span.stop)
                roles = self.find_role(left[-40:] + " " + right[:40]) or self.find_role(left + " " + right)
                mentions.append(Mention("P", name, span.text, span.start, span.stop,
                                        roles[0] if roles else None, roles))
            elif span.type == "LOC":
                cls = self.classify_place(lemmas)
                section, kind = cls if cls else ("G", None)
                mentions.append(Mention(section, name, span.text, span.start, span.stop,
                                        kind, [kind] if kind else []))
            elif span.type == "ORG":
                cls = self.classify_place(lemmas)
                if cls:  # «Царскосельский лицей», «Фёдоровский собор» и т.п.
                    mentions.append(Mention(cls[0], name, span.text, span.start,
                                            span.stop, cls[1], [cls[1]]))
                else:
                    continue
            else:
                continue
            taken.append((span.start, span.stop))

        # 2. «Прилагательное(-ые) с заглавной + существительное-тип»
        tokens = doc.tokens
        for i, tok in enumerate(tokens):
            if tok.lemma not in ARTEFACT_NOUNS and tok.lemma not in GEO_NOUNS:
                continue
            j = i
            while (j > 0 and tokens[j - 1].pos == "ADJ" and tokens[j - 1].text[:1].isupper()
                   and i - j < 3):
                j -= 1
            # «Зал Александра I», «Павильон «Эрмитаж»» обрабатываются ниже
            if j == i:
                continue
            start, stop = tokens[j].start, tok.stop
            if overlaps(start, stop):
                continue
            span_tokens = tokens[j:i + 1]
            try:
                name = self._normalize(self.morph_vocab, span_tokens)
            except Exception:  # нормализация natasha иногда падает на редких формах
                name = " ".join(t.text for t in span_tokens)
            name = self._clean(name)
            if not name:
                continue
            section, kind = self.classify_place([t.lemma for t in span_tokens])
            mentions.append(Mention(section, name, text[start:stop], start, stop, kind, [kind]))
            taken.append((start, stop))

        # 3. Названия в кавычках после слова-типа
        for m in QUOTED_TITLE_RE.finditer(text):
            section = None
            for word in reversed(m.group("pre").split()):   # ближайшее к кавычкам слово-тип
                kind_lemma = self.lemma(word)
                if kind_lemma in ARTEFACT_NOUNS:
                    section, kind = "A", ARTEFACT_NOUNS[kind_lemma]
                elif kind_lemma in GEO_NOUNS:
                    section, kind = "G", GEO_NOUNS[kind_lemma]
                if section:
                    break
            if not section:
                continue
            title = self._clean(m.group("title"))
            if not title:
                continue
            start, stop = m.start("title") - 1, m.end("title") + 1
            # Явный тип перед кавычками надёжнее NER: заменяем пересекающиеся.
            mentions = [mm for mm in mentions if not (mm.start < stop and start < mm.stop)]
            mentions.append(Mention(section, title, text[start:stop], start, stop, kind,
                                    [kind, kind_lemma]))
            taken.append((start, stop))

        mentions.sort(key=lambda mm: mm.start)
        return mentions

    def classify_title(self, title, lead=""):
        """Классификация заголовка статьи. lead — начало статьи (определение).

        Возвращает Mention или None, если тип определить не удалось.
        """
        title = re.sub(r"\s+", " ", title).strip()
        if not self._clean(title):
            return None
        doc = self._doc(title)
        lemmas = [t.lemma for t in doc.tokens]
        per = [s for s in doc.spans if s.type == "PER"]
        lead_roles = self.find_role(lead.split(".")[0] if lead else "")

        # Персоналия: NER видит PER на большей части заголовка, либо в
        # определении есть роль, а в заголовке нет слов-типов.
        cls = self.classify_place(lemmas)
        per_len = sum(s.stop - s.start for s in per)
        if (per and per_len >= 0.6 * len(title)) or (lead_roles and not cls and
                                                      re.match(r"^[А-ЯЁ][а-яё-]+(\s[А-ЯЁ][а-яё-]+){1,2}$", title)):
            return Mention("P", title, title, 0, len(title),
                           lead_roles[0] if lead_roles else None, lead_roles)
        if cls:
            return Mention(cls[0], title, title, 0, len(title), cls[1], [cls[1]])
        # Определение «X — павильон в Екатерининском парке...»
        if lead:
            first = lead.split(".")[0]
            m = re.search(r"\s[—–-]\s+(.*)", first)
            if m:
                words = re.findall(r"[А-ЯЁа-яё-]+", m.group(1))[:6]
                cls = self.classify_place([self.lemma(w) for w in words])
                if cls:
                    return Mention(cls[0], title, title, 0, len(title), cls[1], [cls[1]])
        loc = [s for s in doc.spans if s.type == "LOC"]
        if loc:
            return Mention("G", title, title, 0, len(title), None, [])
        return None
