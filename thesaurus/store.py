"""Хранилище тезауруса: id, проверка дублей, слияние, сохранение в JSON.

Формат id: <цифра сайта><буква раздела><порядковый номер>, например 2P00001.
Порядковый номер ведётся отдельно для каждой пары сайт+раздел.
Дубли проверяются по всему тезаурусу (в том числе среди записей другого
сайта): повторно найденный объект не создаёт новой записи, а лишь пополняет
источники, формы и ключевые слова существующей.
"""

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .config import DEFAULT_ROLE, SECTIONS, SITES
from .translit import transliterate

_PUNCT_RE = re.compile(r"[«»\"„“”'’.,;:!?()\[\]]")


def norm_key(name):
    """Ключ для поиска дублей: регистр, ё/е, кавычки, пробелы, дефисы."""
    s = name.lower().replace("ё", "е")
    s = _PUNCT_RE.sub(" ", s)
    s = re.sub(r"\s*-\s*", "-", s)
    return re.sub(r"\s+", " ", s).strip()


class Thesaurus:
    def __init__(self):
        self.entries = {}      # id -> entry
        self._index = {}       # (section, key) -> id
        self._counters = Counter()
        self._roles = {}       # id -> Counter ролей (только на время прогона)

    # ---------------------------------------------------------------- I/O
    @classmethod
    def load(cls, path):
        th = cls()
        path = Path(path)
        if not path.exists():
            return th
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data.get("entries", []):
            th._register(entry)
            th._counters[(entry["site"], entry["section"])] = max(
                th._counters[(entry["site"], entry["section"])], entry["seq"])
        return th

    def save(self, path, extra_meta=None):
        entries = sorted(self.entries.values(), key=lambda e: e["id"])
        meta = {
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sites": {str(k): v for k, v in SITES.items()},
            "sections": SECTIONS,
            "id_format": "<site digit><section letter><5-digit seq>, e.g. 2P00001",
            "counts": dict(Counter(e["section"] for e in entries)),
        }
        if extra_meta:
            meta.update(extra_meta)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps({"meta": meta, "entries": entries}, ensure_ascii=False, indent=2),
            encoding="utf-8")

    # ------------------------------------------------------------ lookup
    def _register(self, entry):
        self.entries[entry["id"]] = entry
        for name in [entry["name_ru"], *entry.get("aliases", [])]:
            self._index[(entry["section"], norm_key(name))] = entry["id"]

    def find(self, section, name):
        """Ищет существующую запись, включая совпадение по фамилии персоналии."""
        key = norm_key(name)
        if (section, key) in self._index:
            return self.entries[self._index[(section, key)]]
        if section == "P":
            tokens = key.split()
            for entry in self.entries.values():
                if entry["section"] != "P":
                    continue
                etok = norm_key(entry["name_ru"]).split()
                # «Кваренги» -> уже есть «Джакомо Кваренги»
                if len(tokens) == 1 and len(etok) > 1 and tokens[0] == etok[-1]:
                    return entry
                # «Джакомо Кваренги» -> уже есть «Кваренги»
                if len(etok) == 1 and len(tokens) > 1 and etok[0] == tokens[-1]:
                    return entry
        return None

    # ------------------------------------------------------------- write
    def add(self, site, section, name, *, role=None, keywords=(), form=None,
            source=None, definitional=False):
        """Добавляет объект или пополняет существующий. Возвращает (entry, is_new)."""
        name = re.sub(r"\s+", " ", name).strip()
        entry = self.find(section, name)
        is_new = entry is None
        if is_new:
            self._counters[(site, section)] += 1
            seq = self._counters[(site, section)]
            entry = {
                "id": f"{site}{section}{seq:05d}",
                "site": site,
                "site_name": SITES[site],
                "section": section,
                "section_name": SECTIONS[section],
                "seq": seq,
                "name_ru": name,
                "name_en": transliterate(name),
                "name_en_source": "translit",
                "name_en_translit": transliterate(name),
                "role_type": DEFAULT_ROLE[section],
                "keywords": [],
                "aliases": [],
                "forms": [],
                "wiki": {"status": "not_searched", "candidates": []},
                "sources": [],
                "mentions": 0,
            }
            self._register(entry)
        else:
            self._merge_name(entry, name)

        entry["mentions"] += 1
        if form and form not in entry["forms"]:
            entry["forms"].append(form)
        for kw in keywords:
            if kw and kw not in entry["keywords"]:
                entry["keywords"].append(kw)
        if source and source not in entry["sources"]:
            entry["sources"].append(source)
        if role:
            roles = self._roles.setdefault(entry["id"], Counter())
            # Роль из определения в собственной статье объекта весит больше.
            roles[role] += 100 if definitional else 1
        return entry, is_new

    def _merge_name(self, entry, name):
        """Если найдено более полное имя — делаем его основным, старое в алиасы."""
        if norm_key(name) == norm_key(entry["name_ru"]):
            return
        old = entry["name_ru"]
        if entry["section"] == "P" and len(name.split()) > len(old.split()):
            entry["name_ru"] = name
            entry["name_en_translit"] = transliterate(name)
            if entry["name_en_source"] == "translit":
                entry["name_en"] = entry["name_en_translit"]
            name = old
        if name not in entry["aliases"]:
            entry["aliases"].append(name)
        self._register(entry)

    def finalize_roles(self):
        for eid, roles in self._roles.items():
            entry = self.entries[eid]
            role = roles.most_common(1)[0][0]
            entry["role_type"] = role
            if role not in entry["keywords"]:
                entry["keywords"].insert(0, role)
        self._roles.clear()

    def link_wiki(self, linker, only_new_ids=None, log=None):
        for entry in self.entries.values():
            if only_new_ids is not None and entry["id"] not in only_new_ids:
                continue
            if entry["wiki"]["status"] not in ("not_searched", "error"):
                continue
            try:
                cands = linker.candidates(entry["name_ru"], entry["section"])
            except Exception as exc:  # сеть, формат ответа и т.п.
                if log:
                    log.warning("wiki: %s: %s", entry["name_ru"], exc)
                entry["wiki"] = {"status": "error", "candidates": []}
                continue
            entry["wiki"] = {
                "status": "candidate" if cands else "not_found",
                "verified": False,
                "candidates": cands,
            }
            if cands and cands[0]["label_en"] and entry["name_en_source"] == "translit":
                entry["name_en"] = cands[0]["label_en"]
                entry["name_en_source"] = "wikidata_candidate"
