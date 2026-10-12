"""Поиск кандидатов в Wikidata / Википедии для связывания записей тезауруса.

Кандидаты НЕ проверены: в записи хранится статус "candidate", окончательный
выбор делает человек.
"""

import logging

log = logging.getLogger(__name__)

WIKIDATA_API = "https://www.wikidata.org/w/api.php"

# Слова в описании Wikidata, повышающие доверие к кандидату.
_SECTION_HINTS = {
    "P": ["архитектор", "художник", "скульптор", "император", "императрица",
          "князь", "княжна", "княгиня", "государственный", "писатель", "поэт",
          "architect", "painter", "sculptor", "emperor", "empress", "grand duke",
          "grand duchess", "writer", "poet"],
    "A": ["дворец", "павильон", "царское село", "пушкин", "скульптура", "картина",
          "palace", "pavilion", "tsarskoye selo", "pushkin", "sculpture", "painting",
          "building", "здание", "памятник", "monument"],
    "G": ["город", "село", "река", "парк", "улица", "санкт-петербург", "пушкин",
          "city", "village", "river", "park", "street", "saint petersburg"],
}
_LOCAL_HINTS = ["царское село", "царского села", "пушкин", "tsarskoye selo", "pushkin"]


class WikiLinker:
    def __init__(self, fetcher, limit=3):
        self.fetcher = fetcher
        self.limit = limit

    def candidates(self, name, section):
        data = self.fetcher.get_json(WIKIDATA_API, {
            "action": "wbsearchentities", "search": name, "language": "ru",
            "uselang": "ru", "type": "item", "limit": 7, "format": "json",
        })
        if not data or not data.get("search"):
            return []
        ids = [item["id"] for item in data["search"]]
        ents = self.fetcher.get_json(WIKIDATA_API, {
            "action": "wbgetentities", "ids": "|".join(ids),
            "props": "labels|descriptions|sitelinks", "languages": "ru|en",
            "sitefilter": "ruwiki|enwiki", "format": "json",
        }) or {}
        ents = ents.get("entities", {})

        result = []
        for rank, qid in enumerate(ids):
            ent = ents.get(qid, {})
            labels = ent.get("labels", {})
            descs = ent.get("descriptions", {})
            links = ent.get("sitelinks", {})
            label_ru = labels.get("ru", {}).get("value", "")
            desc = " ".join(d.get("value", "") for d in descs.values()).lower()

            score = 1.0 - rank * 0.1
            if label_ru.lower().replace("ё", "е") == name.lower().replace("ё", "е"):
                score += 0.5
            score += 0.2 * sum(h in desc for h in _SECTION_HINTS.get(section, []))
            score += 0.5 * any(h in desc for h in _LOCAL_HINTS)

            ruwiki = links.get("ruwiki", {}).get("title")
            enwiki = links.get("enwiki", {}).get("title")
            result.append({
                "qid": qid,
                "wikidata_url": f"https://www.wikidata.org/wiki/{qid}",
                "label_ru": label_ru,
                "label_en": labels.get("en", {}).get("value", ""),
                "description_ru": descs.get("ru", {}).get("value", ""),
                "description_en": descs.get("en", {}).get("value", ""),
                "ruwiki": f"https://ru.wikipedia.org/wiki/{ruwiki.replace(' ', '_')}" if ruwiki else None,
                "enwiki": f"https://en.wikipedia.org/wiki/{enwiki.replace(' ', '_')}" if enwiki else None,
                "score": round(score, 2),
            })
        result.sort(key=lambda c: -c["score"])
        return result[: self.limit]
