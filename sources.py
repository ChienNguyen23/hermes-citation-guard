"""Registry lookups: doi.org (any DOI agency), Crossref, PubMed E-utilities, arXiv.

Stdlib only. Every call is read-only, metadata-only, and goes to a public
scholarly API. No user text is sent anywhere except the reference strings
themselves (title / DOI / PMID). Responses are cached on disk so a re-check of
the same bibliography costs no network at all.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from .extract import content_tokens

VERSION = "0.1.0"
USER_AGENT = f"citation-guard/{VERSION} (Hermes Agent plugin; +https://github.com/ChienNguyen23/hermes-citation-guard)"
CACHE_TTL_S = 7 * 24 * 3600
CACHE_MAX = 20000


class NotFound(Exception):
    """The registry answered: this identifier does not exist."""


class NetError(Exception):
    """We could not get an answer (timeout, 5xx, rate limit). Never treated as 'fake'."""


class _HostGate:
    """Per-host minimum interval + concurrency cap, so we stay inside public API etiquette."""

    def __init__(self, min_interval: float, concurrency: int):
        self.min_interval = min_interval
        self.sem = threading.Semaphore(concurrency)
        self.lock = threading.Lock()
        self.next_at = 0.0

    def __enter__(self):
        self.sem.acquire()
        with self.lock:
            now = time.monotonic()
            wait = self.next_at - now
            self.next_at = max(now, self.next_at) + self.min_interval
        if wait > 0:
            time.sleep(wait)
        return self

    def __exit__(self, *exc):
        self.sem.release()


# Limits measured from Crossref's own response headers (Nov 2026):
#   public pool  : /works?query  1 req/s, concurrency 1 · /works/{doi} 5 req/s, concurrency 1
#   polite pool  : /works?query  3 req/s, concurrency 3 (requests carrying a contact mailto)
_GATES = {
    "crossref_list": _HostGate(1.05, 1),
    "crossref_single": _HostGate(0.22, 1),
    "doi.org": _HostGate(0.1, 4),
    "eutils.ncbi.nlm.nih.gov": _HostGate(0.4, 1),    # NCBI: 3 req/s without an API key; stay under it
    "export.arxiv.org": _HostGate(3.0, 1),           # arXiv API etiquette: one call every 3 s
}
_DEFAULT_GATE = _HostGate(0.2, 2)
_POLITE = {"crossref_list": _HostGate(0.35, 3), "crossref_single": _HostGate(0.12, 3)}


# A host that rate-limits (429) or refuses us (403, or a 200 "Access Denied" page as NCBI serves
# after a redirect) is not called again for BACKOFF_S: hammering a host that already said no is
# how a machine gets blocked. Its references come back `unchecked`, never `fabricated`.
BACKOFF_S = 600
_BACKOFF_UNTIL: dict = {}


def _back_off(host: str) -> None:
    _BACKOFF_UNTIL[host] = time.monotonic() + BACKOFF_S


def _backing_off(host: str) -> bool:
    return _BACKOFF_UNTIL.get(host, 0.0) > time.monotonic()


def _is_json(body: str) -> bool:
    try:
        json.loads(body)
        return True
    except ValueError:
        return False


def _gate_for(url: str, polite: bool) -> _HostGate:
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if host == "api.crossref.org":
        key = "crossref_list" if parsed.path.rstrip("/") == "/works" else "crossref_single"
        return (_POLITE if polite else _GATES)[key]
    return _GATES.get(host, _DEFAULT_GATE)


SEVERE_NOTICES = ("retraction", "withdrawal", "removal", "partial_retraction")


class Cache:
    def __init__(self, path: Optional[Path] = None):
        self.path = path
        self.lock = threading.Lock()
        self.data: dict = {}
        self.dirty = False
        if path and path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}

    def get(self, key: str):
        with self.lock:
            hit = self.data.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL_S:
            return hit[1]
        return None

    def put(self, key: str, value):
        with self.lock:
            self.data[key] = [time.time(), value]
            self.dirty = True

    def flush(self):
        if not (self.path and self.dirty):
            return
        with self.lock:
            items = sorted(self.data.items(), key=lambda kv: kv[1][0])[-CACHE_MAX:]
            self.data = dict(items)
            tmp = self.path.with_suffix(".tmp")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
            self.dirty = False


class Client:
    def __init__(self, cache: Optional[Cache] = None, mailto: str = "", timeout: float = 8.0,
                 ncbi_api_key: str = ""):
        self.cache = cache or Cache(None)
        self.mailto = mailto.strip()
        self.timeout = timeout
        self.ncbi_api_key = ncbi_api_key.strip()

    # ---------- transport ----------
    def _get(self, url: str, accept: str = "application/json") -> str:
        key = accept + " " + url
        hit = self.cache.get(key)
        if hit is not None:
            if hit == "__404__":
                raise NotFound(url)
            if "json" not in accept or _is_json(hit):   # an error page cached by an older version: refetch
                return hit
        host = urllib.parse.urlparse(url).hostname or ""
        if _backing_off(host):
            raise NetError(f"{host} rate-limited or refused an earlier request; not retried for now")
        gate = _gate_for(url, polite=bool(self.mailto))
        last: Exception = NetError(url)
        for attempt in range(2):
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
            try:
                with gate:
                    with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                        body = resp.read().decode("utf-8", "replace")
                if "json" in accept and not _is_json(body):
                    # an error or block page served with 200: never cache it, never parse it
                    if re.search(r"access denied|blocked|too many requests|rate limit", body[:4000], re.I):
                        _back_off(host)
                        raise NetError(f"{host} refused the request (rate-limited or blocked)")
                    last = NetError(f"{host} returned a non-JSON page")
                    if attempt == 0:
                        time.sleep(1.0)
                        continue
                    raise last
                self.cache.put(key, body)
                return body
            except urllib.error.HTTPError as e:
                if e.code in (404, 410):
                    self.cache.put(key, "__404__")
                    raise NotFound(url) from None
                last = NetError(f"HTTP {e.code} from {host}")
                if e.code == 403 or (e.code == 429 and attempt == 1):
                    _back_off(host)
                    raise last from None
                if e.code in (429, 500, 502, 503, 504) and attempt == 0:
                    try:
                        wait = min(5.0, float(e.headers.get("Retry-After") or 2))
                    except (TypeError, ValueError):
                        wait = 2.0
                    time.sleep(wait)
                    continue
                raise last from None
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = NetError(f"{type(e).__name__}: {e}")
                if attempt == 0:
                    time.sleep(0.5)
                    continue
        raise last

    def _crossref_url(self, path: str, params: Optional[dict] = None) -> str:
        params = dict(params or {})
        if self.mailto:
            params["mailto"] = self.mailto
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        return "https://api.crossref.org" + path + q

    # ---------- normalizers ----------
    @staticmethod
    def _years_from(obj: dict, *fields: str) -> list[int]:
        years = []
        for f in fields:
            parts = ((obj.get(f) or {}).get("date-parts") or [[None]])[0]
            if parts and parts[0]:
                try:
                    years.append(int(parts[0]))
                except (TypeError, ValueError):
                    pass
        return sorted(set(years))

    @classmethod
    def _norm_csl(cls, d: dict, source: str) -> dict:
        title = d.get("title")
        if isinstance(title, list):
            title = title[0] if title else ""
        subtitle = d.get("subtitle")
        if isinstance(subtitle, list):
            subtitle = subtitle[0] if subtitle else ""
        if subtitle and title and subtitle.lower() not in title.lower():
            title = f"{title.rstrip(' :')}: {subtitle}"   # Crossref often splits "Main: Subtitle"
        container = d.get("container-title")
        if isinstance(container, list):
            container = container[0] if container else ""
        authors = []
        for a in d.get("author") or []:
            name = " ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("literal") or a.get("name") or ""
            if name:
                authors.append({"family": a.get("family") or name.split()[-1], "full": name})
        # never "created": that is when the DOI was deposited, often years after publication
        years = cls._years_from(d, "issued", "published-print", "published-online", "published")
        rec = {
            "source": source,
            "doi": (d.get("DOI") or "").lower() or None,
            "title": re.sub(r"<[^>]+>", "", title or "").strip(),
            "authors": authors,
            "years": years,
            "journal": container or "",
            "type": d.get("type") or "",
        }
        if source == "crossref":
            # Crossref merges publisher notices and the Retraction Watch database into `updated-by`.
            rec["notices"] = [{
                "type": (u.get("type") or "").lower().replace("-", "_"),
                "notice_doi": u.get("DOI"),
                "year": (((u.get("updated") or {}).get("date-parts") or [[None]])[0] or [None])[0],
                "source": u.get("source"),
            } for u in d.get("updated-by") or []]
        return rec

    # ---------- lookups ----------
    def crossref_work(self, doi: str) -> dict:
        body = self._get(self._crossref_url("/works/" + urllib.parse.quote(doi, safe="/:;()<>[]")))
        try:
            return self._norm_csl(json.loads(body).get("message") or {}, "crossref")
        except (json.JSONDecodeError, AttributeError) as e:
            raise NetError(f"Crossref returned an unexpected body for {doi}") from e

    def doi(self, doi: str) -> dict:
        """Crossref first (metadata + retraction notices in one call); any other registration
        agency (DataCite, mEDRA, JaLC...) through doi.org content negotiation."""
        try:
            rec = self.crossref_work(doi)
            if rec.get("title") or rec.get("authors"):
                return rec
        except NotFound:
            pass
        url = "https://doi.org/" + urllib.parse.quote(doi, safe="/:;()<>[]")
        body = self._get(url, accept="application/vnd.citationstyles.csl+json")
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            raise NetError(f"doi.org returned non-JSON for {doi}") from e
        rec = self._norm_csl(data, "doi.org")
        rec["doi"] = rec["doi"] or doi.lower()
        return rec

    def crossref_search(self, query: str, rows: int = 5) -> list[dict]:
        url = self._crossref_url("/works", {"query.bibliographic": query[:400], "rows": str(rows),
                                             "select": "DOI,title,author,issued,published-print,"
                                                       "published-online,container-title,type,score,updated-by,subtitle"})
        data = json.loads(self._get(url))
        out = []
        for it in (data.get("message") or {}).get("items") or []:
            rec = self._norm_csl(it, "crossref")
            rec["score"] = it.get("score")
            out.append(rec)
        return out

    def _eutils(self, tool: str, params: dict) -> str:
        params = dict(params)
        params.update({"tool": "citation-guard", "retmode": params.get("retmode", "json")})
        if self.ncbi_api_key:
            params["api_key"] = self.ncbi_api_key
        if self.mailto:
            params["email"] = self.mailto
        return self._get(f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/{tool}.fcgi?" + urllib.parse.urlencode(params))

    def pubmed(self, pmid: str) -> dict:
        data = json.loads(self._eutils("esummary", {"db": "pubmed", "id": pmid}))
        r = (data.get("result") or {}).get(str(pmid))
        if not r or r.get("error"):
            raise NotFound(f"PMID {pmid}")
        doi = next((a.get("value") for a in r.get("articleids") or [] if a.get("idtype") == "doi"), None)
        years = sorted({int(x) for x in re.findall(r"\b((?:19|20)\d{2})\b",
                                                   f"{r.get('pubdate', '')} {r.get('epubdate', '')}")})
        authors = []
        for a in r.get("authors") or []:
            name = a.get("name") or ""
            if name and a.get("authtype", "Author") == "Author":
                authors.append({"family": name.split()[0], "full": name})
        return {
            "source": "pubmed",
            "pmid": str(pmid),
            "doi": (doi or "").lower() or None,
            "title": (r.get("title") or "").strip(),
            "authors": authors,
            "years": years,
            "journal": r.get("fulljournalname") or r.get("source") or "",
            "type": ", ".join(r.get("pubtype") or []),
            "retracted": "Retracted Publication" in (r.get("pubtype") or []),
        }

    def pubmed_title_search(self, title: str, limit: int = 3) -> list[str]:
        words = [w for w in content_tokens(title) if len(w) > 2][:10]
        if len(words) < 3:
            return []
        term = " AND ".join(f"{w}[ti]" for w in words)
        data = json.loads(self._eutils("esearch", {"db": "pubmed", "term": term, "retmax": str(limit),
                                                   "sort": "relevance"}))
        return ((data.get("esearchresult") or {}).get("idlist")) or []

    _ATOM = {"a": "http://www.w3.org/2005/Atom"}

    @classmethod
    def _norm_arxiv_entry(cls, entry) -> Optional[dict]:
        ns = cls._ATOM
        title = re.sub(r"\s+", " ", entry.findtext("a:title", default="", namespaces=ns)).strip()
        entry_id = entry.findtext("a:id", default="", namespaces=ns)
        if not title or title.lower() == "error" or "api/errors" in entry_id:
            return None
        authors = []
        for a in entry.findall("a:author", ns):
            name = (a.findtext("a:name", default="", namespaces=ns) or "").strip()
            if name:
                authors.append({"family": name.split()[-1], "full": name})
        published = entry.findtext("a:published", default="", namespaces=ns)
        m = re.search(r"arxiv\.org/abs/(\S+?)(?:v\d+)?$", entry_id.strip())
        return {"source": "arxiv", "arxiv": m.group(1) if m else entry_id, "doi": None, "title": title,
                "authors": authors, "years": [int(published[:4])] if published[:4].isdigit() else [],
                "journal": "arXiv", "type": "preprint"}

    def _arxiv_query(self, params: dict) -> list[dict]:
        body = self._get("https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params),
                         accept="application/atom+xml")
        try:
            root = ET.fromstring(body)
        except ET.ParseError as e:
            raise NetError("arXiv returned malformed XML") from e
        return [r for r in (self._norm_arxiv_entry(e) for e in root.findall("a:entry", self._ATOM)) if r]

    def arxiv(self, arxiv_id: str) -> dict:
        found = self._arxiv_query({"id_list": arxiv_id})
        if not found:
            raise NotFound(f"arXiv {arxiv_id}")
        return found[0]

    def arxiv_title_search(self, title: str, limit: int = 3) -> list[dict]:
        words = [w for w in content_tokens(title) if len(w) > 2][:10]
        if len(words) < 3:
            return []
        return self._arxiv_query({"search_query": " AND ".join(f"ti:{w}" for w in words),
                                  "max_results": str(limit)})
