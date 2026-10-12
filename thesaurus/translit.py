"""Транслитерация кириллицы в латиницу (упрощённая BGN/PCGN).

Используется как запасной вариант английского названия, если в Wikidata
кандидата с английской меткой не нашлось.
"""

_TABLE = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}
_VOWELS = set("аеёиоуыэюяъь")


def transliterate(text):
    out = []
    prev = ""
    for ch in text:
        low = ch.lower()
        if low in _TABLE:
            lat = _TABLE[low]
            # «е» в начале слова и после гласной -> ye (Елизавета -> Yelizaveta)
            if low == "е" and (not prev.isalpha() or prev.lower() in _VOWELS):
                lat = "ye"
            if low == "й" and prev.lower() in ("и", "ы"):
                lat = "y"
            if ch.isupper() and lat:
                lat = lat[0].upper() + lat[1:]
            out.append(lat)
        else:
            out.append(ch)
        prev = ch
    return "".join(out)
