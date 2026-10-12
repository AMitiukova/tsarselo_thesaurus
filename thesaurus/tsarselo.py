"""Разбор страниц tsarselo.ru: раздел -> статья -> текст и вложенные ссылки.

Разметка сайта может меняться, поэтому основной текст ищется по списку
типичных селекторов (Drupal / WordPress / generic), а если ни один не
сработал — берётся блок с наибольшим количеством текста в абзацах.
Ссылки на документы (.pdf, .docx, .doc, .rtf, .odt) тоже обходятся: из них
извлекается текст.
"""

import io
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

from .fetcher import normalize_url
from .store import norm_key

log = logging.getLogger(__name__)

SITE_HOSTS = {"tsarselo.ru", "www.tsarselo.ru"}

CONTENT_SELECTORS = [
    "[itemprop=articleBody]",
    ".field--name-body",
    ".field-name-body",
    ".node__content",
    ".node-content",
    ".entry-content",
    ".post-content",
    ".article-content",
    ".article__body",
    "article .content",
    "article",
    "#content .content",
    ".region-content",
    "main",
    "#content",
]
NOISE_SELECTORS = [
    "script", "style", "noscript", "nav", "footer", "header", "aside", "form",
    ".breadcrumb", ".breadcrumbs", ".comments", "#comments", ".comment",
    ".pager", ".pagination", ".share", ".social", ".links.inline", ".node__links",
    ".field--name-field-tags", ".tags", ".menu", ".sidebar",
]
# Пути, которые не являются статьями (разделы, теги, служебные страницы).
EXCLUDED_PATH_PREFIXES = [
    "/разделы", "/taxonomy", "/tags", "/user", "/search", "/node/add",
    "/comment", "/admin", "/filter", "/rss", "/contact", "/sites/default/files/styles",
]
DOC_EXTENSIONS = (".pdf", ".docx", ".doc", ".rtf", ".odt", ".txt")
SKIP_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".mp3",
                   ".mp4", ".avi", ".zip", ".rar", ".xml")
NEXT_PAGE_TEXT = {"следующая", "следующая ›", "›", "»", "далее", "next", "вперёд", "next ›"}


@dataclass
class Article:
    url: str
    title: str
    text: str
    links: list = field(default_factory=list)   # [(url, anchor_text)]
    is_document: bool = False


def is_site_url(url):
    return urlsplit(url).netloc.lower() in SITE_HOSTS


def is_article_url(url):
    if not is_site_url(url):
        return False
    path = unquote(urlsplit(url).path).lower()
    if path in ("", "/"):
        return False
    if any(path.startswith(p) for p in EXCLUDED_PATH_PREFIXES):
        return False
    if path.endswith(SKIP_EXTENSIONS):
        return False
    return True


def _soup(html):
    return BeautifulSoup(html, "lxml")


def _title(soup):
    for sel in ["article h1", "main h1", "h1.page-title", "h1.title", "h1"]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            return el.get_text(" ", strip=True)
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].strip()
    if soup.title:
        return re.split(r"\s[|—–-]\s", soup.title.get_text(strip=True))[0]
    return ""


def _content_node(soup):
    for sel in NOISE_SELECTORS:
        for el in soup.select(sel):
            el.decompose()
    for sel in CONTENT_SELECTORS:
        nodes = soup.select(sel)
        if nodes:
            node = max(nodes, key=lambda n: len(n.get_text(strip=True)))
            if len(node.get_text(strip=True)) > 200:
                return node
    # Fallback: родитель с наибольшим объёмом текста в <p>.
    best, best_len = None, 0
    for p in soup.find_all("p"):
        parent = p.parent
        size = sum(len(x.get_text(strip=True)) for x in parent.find_all("p", recursive=False))
        if size > best_len:
            best, best_len = parent, size
    return best or soup.body or soup


def _node_text(node):
    # Блочные элементы разделяем переводами строки, чтобы не склеивать предложения.
    for br in node.find_all("br"):
        br.replace_with("\n")
    parts = []
    for el in node.find_all(["p", "h2", "h3", "h4", "h5", "li", "blockquote",
                             "figcaption", "td", "dd", "dt"]):
        if el.find(["p", "li", "td"]):   # вложенные контейнеры пропускаем
            continue
        t = el.get_text(" ", strip=True)
        if t:
            parts.append(t)
    text = "\n".join(parts) if parts else node.get_text("\n", strip=True)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


def parse_article(url, html):
    soup = _soup(html)
    title = _title(soup)
    node = _content_node(soup)
    links = []
    seen = set()
    for a in node.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = normalize_url(urljoin(url, href))
        if absolute == normalize_url(url) or absolute in seen:
            continue
        if not is_article_url(absolute):
            continue
        seen.add(absolute)
        links.append((absolute, a.get_text(" ", strip=True)))
    return Article(url=url, title=title, text=_node_text(node), links=links)


def find_article_link(section_url, html, article_title):
    """Ищет на странице раздела ссылку с заданным заголовком.

    Возвращает (url статьи | None, url следующей страницы | None).
    """
    soup = _soup(html)
    target = norm_key(article_title)
    exact, partial = None, None
    for a in soup.find_all("a", href=True):
        text = norm_key(a.get_text(" ", strip=True))
        if not text:
            continue
        url = normalize_url(urljoin(section_url, a["href"]))
        if text == target:
            exact = exact or url
        elif target in text and len(text) < len(target) + 40:
            partial = partial or url
    nxt = None
    link_next = soup.find("a", rel="next") or soup.select_one(".pager__item--next a, .pager-next a, li.next a")
    if not link_next:
        for a in soup.find_all("a", href=True):
            if a.get_text(strip=True).lower() in NEXT_PAGE_TEXT:
                link_next = a
                break
    if link_next:
        nxt = normalize_url(urljoin(section_url, link_next["href"]))
    return exact or partial, nxt


# ------------------------------------------------------------- документы
def document_text(url, content):
    path = unquote(urlsplit(url).path).lower()
    try:
        if path.endswith(".docx"):
            import docx  # python-docx
            d = docx.Document(io.BytesIO(content))
            return "\n".join(p.text for p in d.paragraphs if p.text.strip())
        if path.endswith(".pdf"):
            from pdfminer.high_level import extract_text
            return extract_text(io.BytesIO(content))
        if path.endswith(".odt"):
            from odf import teletype
            from odf.opendocument import load
            from odf.text import P
            d = load(io.BytesIO(content))
            return "\n".join(teletype.extractText(p) for p in d.getElementsByType(P))
        if path.endswith(".txt"):
            return content.decode("utf-8", errors="replace")
        if path.endswith((".doc", ".rtf")):
            tool = shutil.which("antiword") if path.endswith(".doc") else shutil.which("unrtf")
            if not tool:
                log.warning("нет %s для %s — пропуск", "antiword" if path.endswith(".doc") else "unrtf", url)
                return ""
            with tempfile.NamedTemporaryFile(suffix=path[-4:]) as tmp:
                tmp.write(content)
                tmp.flush()
                args = [tool, tmp.name] if tool.endswith("antiword") else [tool, "--text", tmp.name]
                return subprocess.run(args, capture_output=True, text=True, timeout=60).stdout
    except ImportError as exc:
        log.warning("для %s нужна библиотека: %s", url, exc)
    except Exception as exc:
        log.warning("не удалось прочитать документ %s: %s", url, exc)
    return ""


def load_page(fetcher, url):
    """Загружает HTML-статью или документ и возвращает Article (или None)."""
    content, ctype = fetcher.get_bytes(url)
    if content is None:
        return None
    path = unquote(urlsplit(url).path).lower()
    if path.endswith(DOC_EXTENSIONS) or (ctype and "html" not in ctype and "text/plain" not in ctype
                                         and ("pdf" in ctype or "word" in ctype or "opendocument" in ctype)):
        name = unquote(path.rsplit("/", 1)[-1]).rsplit(".", 1)[0].replace("_", " ")
        return Article(url=url, title=name, text=document_text(url, content), is_document=True)
    return parse_article(url, content.decode("utf-8", errors="replace"))
