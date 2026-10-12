"""Офлайн-тесты на синтетических страницах (разметка в стиле Drupal).

Запуск: python -m pytest tests -q
"""

import json

import pytest

from thesaurus.extractor import Extractor
from thesaurus.fetcher import normalize_url
from thesaurus.pipeline import crawl, locate_article, process_article
from thesaurus.store import Thesaurus, norm_key
from thesaurus.translit import transliterate
from thesaurus.tsarselo import parse_article

BASE = "https://www.tsarselo.ru"
SECTION = BASE + "/разделы/адреса"

PAGES = {
    SECTION: """<html><body><main><h1>Адреса</h1>
      <div class="view-content">
        <a href="/адреса/екатерининский-дворец">Екатерининский дворец</a>
        <a href="/адреса/александровский-дворец">Александровский дворец</a>
      </div></main></body></html>""",
    BASE + "/адреса/александровский-дворец": """<html><head><title>Александровский дворец | Царское Село</title></head>
      <body><nav><a href="/разделы/адреса">Адреса</a></nav>
      <article><h1>Александровский дворец</h1>
      <div class="field--name-body">
        <p>Александровский дворец — дворец в Царском Селе, построенный в 1792—1796 годах
        по проекту архитектора <a href="/персоны/кваренги">Джакомо Кваренги</a>.
        Императрица Екатерина II подарила его своему внуку великому князю Александру Павловичу.</p>
        <p>В Белом зале и Китайском театре бывал Николай II. Рядом находится
        <a href="/адреса/александровский-парк">Александровский парк</a>.</p>
        <p>Здесь хранилась картина «Вид на Камеронову галерею». Хранитель Сергей Иванович Ростовцев
        составил опись. Позже Кваренги вспоминал о работе в Пушкине.</p>
        <p><a href="https://ru.wikipedia.org/wiki/X">внешняя</a>
        <a href="/taxonomy/term/5">тег</a></p>
      </div></article></body></html>""",
    BASE + "/персоны/кваренги": """<html><body><article><h1>Джакомо Кваренги</h1>
      <div class="field--name-body"><p>Джакомо Кваренги (1744—1817) — итальянский архитектор,
      работавший в России. Он построил Александровский дворец и Концертный зал в Царском Селе.
      Его творчество высоко ценил император Александр I. Текст для объёма страницы, чтобы
      блок был больше двухсот символов и выбирался селектором.</p></div></article></body></html>""",
    BASE + "/адреса/александровский-парк": """<html><body><article><h1>Александровский парк</h1>
      <div class="field--name-body"><p>Александровский парк — парк в городе Пушкине. В парке
      стоят Китайская деревня и павильон «Арсенал». Через парк течёт река Кузьминка.
      Текст для объёма страницы, чтобы блок был больше двухсот символов и выбирался
      селектором контента.</p></div></article></body></html>""",
}
PAGES = {normalize_url(k): v for k, v in PAGES.items()}


class FakeFetcher:
    def get_text(self, url, params=None):
        return PAGES.get(normalize_url(url))

    def get_bytes(self, url, params=None):
        html = self.get_text(url)
        return (html.encode("utf-8"), "text/html") if html else (None, None)


@pytest.fixture(scope="module")
def extractor():
    return Extractor()


def run(extractor, depth=1):
    f = FakeFetcher()
    url = locate_article(f, SECTION, "Александровский дворец")
    th = Thesaurus()
    stats = {"unclassified_titles": []}
    for art, _ in crawl(f, url, depth=depth):
        process_article(th, extractor, art, 2, stats)
    th.finalize_roles()
    return th


def by_name(th, name):
    for e in th.entries.values():
        if norm_key(e["name_ru"]) == norm_key(name) or norm_key(name) in map(norm_key, e["aliases"]):
            return e
    raise AssertionError(f"нет {name}: {[e['name_ru'] for e in th.entries.values()]}")


def test_parse_links_filters_external_and_taxonomy():
    url = normalize_url(BASE + "/адреса/александровский-дворец")
    art = parse_article(url, PAGES[url])
    assert art.title == "Александровский дворец"
    assert len(art.links) == 2
    assert all("tsarselo.ru" in u for u, _ in art.links)


def test_thesaurus_content(extractor):
    th = run(extractor)
    q = by_name(th, "Джакомо Кваренги")
    assert q["section"] == "P" and q["role_type"] == "архитектор"
    assert "Кваренги" in q["aliases"] or q["name_ru"] == "Джакомо Кваренги"
    assert by_name(th, "Екатерина II")["role_type"] == "императрица"
    assert by_name(th, "Александр Павлович")["role_type"] == "великий князь"
    assert by_name(th, "Сергей Иванович Ростовцев")["role_type"] == "хранитель"
    assert by_name(th, "Николай II")["role_type"] == "историческая личность"

    palace = by_name(th, "Александровский дворец")
    assert palace["section"] == "A" and palace["role_type"] == "архитектурный объект"
    assert by_name(th, "Китайский театр")["section"] == "A"
    assert by_name(th, "Белый зал")["role_type"] == "интерьер"
    assert by_name(th, "Вид на Камеронову галерею")["role_type"] == "живопись"
    assert by_name(th, "Арсенал")["section"] == "A"

    assert by_name(th, "Царское Село")["section"] == "G"
    assert by_name(th, "Александровский парк")["role_type"] == "парк/сад"

    # без дублей
    keys = [(e["section"], norm_key(e["name_ru"])) for e in th.entries.values()]
    assert len(keys) == len(set(keys))


def test_ids_and_reload(extractor, tmp_path):
    th = run(extractor)
    ids = sorted(th.entries)
    assert all(i[0] == "2" and i[1] in "PAG" and len(i) == 7 for i in ids)
    assert "2P00001" in ids and "2A00001" in ids and "2G00001" in ids
    path = tmp_path / "t.json"
    th.save(path)
    n = len(th.entries)

    # повторный прогон поверх сохранённого — новых записей нет
    th2 = Thesaurus.load(path)
    entry, is_new = th2.add(2, "P", "Кваренги")
    assert not is_new
    entry, is_new = th2.add(1, "P", "Чарльз Камерон", role="архитектор")
    assert is_new and entry["id"] == "1P00001"
    assert len(th2.entries) == n + 1
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["entries"][0]["wiki"]["status"] == "not_searched"


def test_translit():
    assert transliterate("Александровский дворец") == "Aleksandrovskiy dvorets"
    assert transliterate("Елизавета") == "Yelizaveta"


def test_wiki_candidates_mocked():
    from thesaurus.wiki import WikiLinker

    class WikiFetcher:
        def get_json(self, url, params):
            if params["action"] == "wbsearchentities":
                return {"search": [{"id": "Q1"}, {"id": "Q2"}]}
            return {"entities": {
                "Q1": {"labels": {"ru": {"value": "Кваренги"}},
                       "descriptions": {"ru": {"value": "фамилия"}}},
                "Q2": {"labels": {"ru": {"value": "Джакомо Кваренги"},
                                  "en": {"value": "Giacomo Quarenghi"}},
                       "descriptions": {"ru": {"value": "итальянский архитектор"}},
                       "sitelinks": {"ruwiki": {"title": "Кваренги, Джакомо"}}},
            }}

    th = Thesaurus()
    th.add(2, "P", "Джакомо Кваренги", role="архитектор")
    th.link_wiki(WikiLinker(WikiFetcher()))
    e = th.entries["2P00001"]
    assert e["wiki"]["status"] == "candidate" and e["wiki"]["verified"] is False
    assert e["wiki"]["candidates"][0]["qid"] == "Q2"
    assert e["name_en"] == "Giacomo Quarenghi" and e["name_en_source"] == "wikidata_candidate"
