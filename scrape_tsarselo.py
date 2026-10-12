#!/usr/bin/env python3
"""Сбор тезауруса с tsarselo.ru.

Пример:
    python scrape_tsarselo.py --article "Александровский дворец" --depth 1 \
        --output data/thesaurus.json

Если файл --output уже существует, новые объекты добавляются к нему
(дубли не создаются, нумерация продолжается).
"""

import argparse
import logging
import sys

from thesaurus.extractor import Extractor
from thesaurus.fetcher import Fetcher
from thesaurus.pipeline import crawl, locate_article, process_article
from thesaurus.store import Thesaurus
from thesaurus.wiki import WikiLinker

SITE_ID = 2  # tsarselo.ru
DEFAULT_SECTION = "https://www.tsarselo.ru/разделы/адреса"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--section-url", default=DEFAULT_SECTION, help="страница раздела со списком статей")
    ap.add_argument("--article", default="Александровский дворец", help="заголовок статьи в разделе")
    ap.add_argument("--article-url", help="прямая ссылка на статью (вместо поиска в разделе)")
    ap.add_argument("--depth", type=int, default=1, help="глубина обхода вложенных ссылок (1 = только ссылки из статьи)")
    ap.add_argument("--output", default="data/thesaurus.json")
    ap.add_argument("--cache-dir", default=".cache")
    ap.add_argument("--delay", type=float, default=1.0, help="пауза между запросами, сек")
    ap.add_argument("--offline", action="store_true", help="только из кэша, без сети")
    ap.add_argument("--no-wiki", action="store_true", help="не искать кандидатов в Wikidata")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")
    log = logging.getLogger("scrape")

    fetcher = Fetcher(args.cache_dir, delay=args.delay, offline=args.offline)
    article_url = args.article_url or locate_article(fetcher, args.section_url, args.article)
    if not article_url:
        log.error("статья «%s» не найдена в разделе %s", args.article, args.section_url)
        return 1
    log.info("статья: %s", article_url)

    thesaurus = Thesaurus.load(args.output)
    extractor = Extractor()
    stats = {"unclassified_titles": [], "pages": []}
    new_ids = set()
    for art, level in crawl(fetcher, article_url, depth=args.depth):
        stats["pages"].append({"url": art.url, "title": art.title, "level": level,
                               "document": art.is_document, "chars": len(art.text)})
        new_ids |= process_article(thesaurus, extractor, art, SITE_ID, stats)
    thesaurus.finalize_roles()

    if not args.no_wiki:
        log.info("поиск кандидатов в Wikidata для %d новых объектов...", len(new_ids))
        thesaurus.link_wiki(WikiLinker(fetcher), only_new_ids=new_ids, log=log)

    thesaurus.save(args.output, extra_meta={
        "last_run": {
            "site": SITE_ID,
            "start_article": article_url,
            "depth": args.depth,
            "pages": stats["pages"],
            "new_entries": len(new_ids),
            "unclassified_titles": stats["unclassified_titles"],
        }
    })
    log.info("готово: %d новых, всего %d → %s", len(new_ids), len(thesaurus.entries), args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
