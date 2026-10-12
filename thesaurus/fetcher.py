"""HTTP-загрузчик с дисковым кэшем и паузой между запросами."""

import hashlib
import logging
import time
from pathlib import Path
from urllib.parse import quote, unquote, urldefrag, urlsplit, urlunsplit

import requests

log = logging.getLogger(__name__)

USER_AGENT = "TsarseloThesaurusBot/0.1 (museum media archive; research use)"


def normalize_url(url):
    """Убирает якорь и приводит путь к единому percent-encoding (кириллица)."""
    url, _ = urldefrag(url)
    parts = urlsplit(url)
    path = quote(unquote(parts.path), safe="/:@!$&'()*+,;=-._~")
    query = quote(unquote(parts.query), safe="=&/:@!$'()*+,;-._~")
    netloc = parts.netloc.lower()
    return urlunsplit((parts.scheme.lower() or "https", netloc, path or "/", query, ""))


class Fetcher:
    def __init__(self, cache_dir=".cache", delay=1.0, offline=False, timeout=30):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        self.offline = offline
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._last = 0.0

    def _cache_path(self, url):
        return self.cache_dir / hashlib.sha1(url.encode("utf-8")).hexdigest()

    def get_bytes(self, url, params=None):
        """Возвращает (содержимое, content-type) или (None, None) при ошибке."""
        key = url if not params else url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        path = self._cache_path(key)
        meta = path.with_suffix(".type")
        if path.exists():
            return path.read_bytes(), (meta.read_text() if meta.exists() else "")
        if self.offline:
            log.warning("offline: нет в кэше %s", key)
            return None, None

        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(3):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                self._last = time.monotonic()
                if resp.status_code == 404:
                    log.warning("404 %s", url)
                    return None, None
                resp.raise_for_status()
                ctype = resp.headers.get("Content-Type", "")
                path.write_bytes(resp.content)
                meta.write_text(ctype)
                return resp.content, ctype
            except requests.RequestException as exc:
                log.warning("ошибка загрузки %s (попытка %d): %s", url, attempt + 1, exc)
                time.sleep(2 ** attempt)
        return None, None

    def get_text(self, url, params=None):
        content, _ = self.get_bytes(url, params)
        if content is None:
            return None
        return content.decode("utf-8", errors="replace")

    def get_json(self, url, params=None):
        import json

        text = self.get_text(url, params)
        if text is None:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return None
