"""Обход статьи и вложенных ссылок, наполнение тезауруса."""

import logging
from collections import deque
from urllib.parse import unquote

from .fetcher import normalize_url
from .tsarselo import find_article_link, load_page

log = logging.getLogger(__name__)


def locate_article(fetcher, section_url, title, max_pages=50):
    url = normalize_url(section_url)
    for _ in range(max_pages):
        html = fetcher.get_text(url)
        if html is None:
            return None
        found, nxt = find_article_link(url, html, title)
        if found:
            return found
        if not nxt or nxt == url:
            return None
        url = nxt
    return None


def crawl(fetcher, start_url, depth=1):
    """BFS: статья + ссылки на статьи до глубины depth. Выдаёт (Article, уровень)."""
    queue = deque([(normalize_url(start_url), 0)])
    seen = {normalize_url(start_url)}
    while queue:
        url, level = queue.popleft()
        art = load_page(fetcher, url)
        if art is None:
            continue
        log.info("[%d] %s — %s (ссылок: %d)", level, art.title, url, len(art.links))
        yield art, level
        if level < depth:
            for link, _ in art.links:
                if link not in seen:
                    seen.add(link)
                    queue.append((link, level + 1))


def process_article(thesaurus, extractor, art, site, stats):
    source = {"url": unquote(art.url), "title": art.title}
    new_ids = set()

    # Сам заголовок статьи — объект тезауруса (определение — первое предложение).
    head = extractor.classify_title(art.title, art.text[:400])
    if head:
        entry, is_new = thesaurus.add(site, head.section, head.name,
                                      role=head.role_type, keywords=head.keywords,
                                      form=head.form, source=source, definitional=True)
        entry.setdefault("article_url", unquote(art.url))
        if is_new:
            new_ids.add(entry["id"])
    else:
        stats["unclassified_titles"].append({"title": art.title, "url": art.url})

    for m in extractor.extract(art.text):
        entry, is_new = thesaurus.add(site, m.section, m.name, role=m.role_type,
                                      keywords=m.keywords, form=m.form, source=source)
        if is_new:
            new_ids.add(entry["id"])
    return new_ids
