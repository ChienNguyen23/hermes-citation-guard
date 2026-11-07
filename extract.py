"""Find checkable citations in free text.

Pure functions, no network. A "citation" here is one reference entry: a line or
list item that carries a DOI / PMID / arXiv id, or that looks like a full
bibliographic entry (authors + year + title + journal signals). Inline mentions
such as "Smith et al. (2020) showed..." carry no title and are not checkable,
so they are skipped on purpose.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"|]+)", re.I)   # < > allowed: SICI DOIs "...(2004)018<0101:LTOMPC>2.0.CO;2"
PMID_RE = re.compile(
    r"(?:\bPMID\s*:?\s*|pubmed\.ncbi\.nlm\.nih\.gov/|ncbi\.nlm\.nih\.gov/pubmed/)(\d{4,9})\b", re.I)
ARXIV_RE = re.compile(
    r"(?:\barXiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", re.I)
URL_RE = re.compile(r"https?://\S+")
YEAR_RE = re.compile(r"\b(19\d{2}|20[0-2]\d)\b")
HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*|__)?\s*(references|reference list|bibliography|works cited|"
    r"literature cited|sources|citations|further reading|tài liệu tham khảo)\s*:?\s*(?:\*\*|__)?\s*:?\s*$",
    re.I)
MARKER_RE = re.compile(r"^\s*(?:\[\d{1,3}\]|\d{1,3}[.)]|[-*•‣◦])\s+")
JOURNAL_SIGNAL_RE = re.compile(
    r"\d+\s*\(\s*\d+[A-Za-z]?\s*\)|;\s*\d+|:\s*[eE]?\d+\s*[-–]\s*\d+|\bpp?\.\s*\d|\bvol\.?\s*\d|"
    r"\bdoi\b|\bjournal\b|\bproceedings\b|\bconference\b|\bpress\b|\bpublisher\b|\b\d+\s*,\s*\d+\s*[-–]\s*\d+",
    re.I)
AUTHOR_START_RE = re.compile(
    r"^[^\W\d_][\w'’\-]+(?:\s+[^\W\d_][\w'’\-]+){0,2}"
    r"(?:,?\s+(?:[A-Z]\.\s*){1,3}|\s+[A-Z]{1,3}\b|,\s+[A-Z]|\s+et al|,\s+[^\W\d_]+\s+[^\W\d_])",
    re.U)

_TRAILING = ".,;:'\"*_`>»”’"


def _clean_doi(doi: str) -> str:
    # "](" ends the link text of a markdown link: [10.1/x](https://doi.org/10.1/x)
    doi = re.split(r"</|<(?:br|a|span|sup|sub|em|i|b|strong)\b|\]\(", doi.strip(), maxsplit=1, flags=re.I)[0]
    while doi and doi[-1] in _TRAILING:
        doi = doi[:-1]
    # Strip unbalanced closing brackets (markdown links), keep balanced ones:
    # 10.1016/S0140-6736(20)30183-5 must survive intact.
    for open_c, close_c in (("(", ")"), ("[", "]"), ("{", "}"), ("<", ">")):
        while doi.endswith(close_c) and doi.count(close_c) > doi.count(open_c):
            doi = doi[:-1]
            while doi and doi[-1] in _TRAILING:
                doi = doi[:-1]
    return doi


_LIGATURES = str.maketrans({"đ": "d", "Đ": "D", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "ß": "ss",
                            "ø": "o", "Ø": "O", "ł": "l", "Ł": "L", "ı": "i", "þ": "th", "ð": "d"})


def ascii_fold(text: str) -> str:
    # NFKD does not decompose these letters ("LEUKÆMIA" must compare equal to "leukaemia")
    text = text.translate(_LIGATURES)
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", ascii_fold(text).lower())


STOP = {"a", "an", "the", "of", "in", "on", "for", "and", "to", "with", "by", "at", "from", "as",
        "is", "are", "its", "vs", "versus", "among", "between", "into", "via", "or", "do", "does"}


def content_tokens(text: str) -> list[str]:
    return [t for t in tokens(text) if t not in STOP]


def _strip_marker(line: str) -> str:
    return MARKER_RE.sub("", line, count=1).strip()


def _strip_markdown(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+(?:\([^)\s]*\))?[^)\s]*)\)", r"\1 \2", text)
    text = text.replace("**", "").replace("__", "")
    return re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"\1", text)   # *italic* (never touch _ : DOIs use it)


_EMPH_TITLE = re.compile(r"\*\*([^*\n]{12,300}?)\*\*|__([^_\n]{12,300}?)__|[“\"]([^”\"\n]{12,400})[”\"]")
_AUTHOR_YEAR = re.compile(r"\bet al\b|[A-Z][\w'’\-]+,?\s*\(?(?:19|20)\d{2}\b|[A-Z][\w'’\-]+ (?:and|&) [A-Z][\w'’\-]+")


def _emphasized_title(line: str) -> Optional[str]:
    m = _EMPH_TITLE.search(line)
    if not m:
        return None
    cand = next(g for g in m.groups() if g).strip(" .:,")
    return cand if len(cand.split()) >= 3 else None


def find_ids(text: str) -> dict:
    out: dict = {}
    m = DOI_RE.search(text)
    if m:
        out["doi"] = _clean_doi(m.group(1))
    m = PMID_RE.search(text)
    if m:
        out["pmid"] = m.group(1)
    m = ARXIV_RE.search(text)
    if m:
        out["arxiv"] = m.group(1)
    return out


def looks_like_reference(line: str) -> bool:
    """A full bibliographic entry, not prose that happens to mention a year."""
    body = _strip_marker(_strip_markdown(line))
    words = body.split()
    if len(words) < 6 or len(words) > 120:
        return False
    if not YEAR_RE.search(body):
        return False
    authorish = bool(AUTHOR_START_RE.match(body)) or " et al" in body
    if authorish and (JOURNAL_SIGNAL_RE.search(body) or re.search(r"[“\"][^”\"]{15,}[”\"]", body)):
        return True
    # No volume/pages (online-first, preprints, "Br J Radiol. 2016:20160060"): a Vancouver author
    # block followed by a title sentence and something after it, or an APA "Surname, I. (2020)." opening.
    m = _VANCOUVER_AUTHORS.match(body)
    if m and m.end() < len(body) and body[:m.end()].rstrip().endswith("."):   # "Smith J, Lee K." closes the list
        segs = _segments(body[m.end():])
        if len(segs) >= 2 and len(segs[0].split()) >= 4:
            return True
    if re.match(r"^[^\W\d_][\w'’\-]+,\s+(?:[A-Z]\.\s*){1,3}.{0,200}?\(\s*(?:19|20)\d{2}[a-z]?\)\s*\.", body):
        return True
    # How agents list papers: "- **Title** (Vaswani et al., 2017), NeurIPS" / "1. “Title” — Smith & Lee, 2020"
    title = _emphasized_title(line)
    return bool(MARKER_RE.match(line) and title and not _authorish(title)
                and _AUTHOR_YEAR.search(line.replace(title, " ")))


def _split_entries(lines: list[str]) -> list[str]:
    """Group a reference section into entries (numbered, bulleted, or one per paragraph)."""
    entries: list[str] = []
    has_markers = any(MARKER_RE.match(ln) for ln in lines if ln.strip())
    buf: list[str] = []
    for ln in lines:
        if not ln.strip():
            if buf and not has_markers:
                entries.append(" ".join(buf))
                buf = []
            continue
        if has_markers and MARKER_RE.match(ln) and buf:
            entries.append(" ".join(buf))
            buf = []
        buf.append(ln.strip())
        if not has_markers and len(buf) == 1 and (find_ids(ln) or looks_like_reference(ln)):
            # one-entry-per-line lists without blank lines between them
            entries.append(" ".join(buf))
            buf = []
    if buf:
        entries.append(" ".join(buf))
    return [e for e in entries if len(e.split()) >= 3]


def _segments(entry: str) -> list[str]:
    """Split an entry into sentence-like segments. A piece that starts in lower case continues
    the previous one ("E. coli"), as do common abbreviations ("vs.", "No.")."""
    raw = re.split(r"(?<=[.?!])\s+", entry)
    segs: list[str] = []
    for piece in raw:
        if segs and (piece[:1].islower()
                     or re.search(r"(?:\bvs|\be\.g|\bi\.e|\bno|\bvol|\bpp|\bSt|\bDr|\bMr|\bMs)\.$", segs[-1], re.I)):
            segs[-1] += " " + piece
        else:
            segs.append(piece)
    return [s.strip() for s in segs if s.strip()]


# Vancouver / AMA author block: "Smith J, van der Berg AB, Lee K, et al." ending in ". "
# a name word starts with a capital letter, or is a surname particle ("van der Berg AB"); otherwise a
# title ending in a capital letter ("Effects of vitamin D. Am J Clin Nutr") reads as one more author
_NAME_WORD = r"(?:[^\W\d_a-z][\w'’\-]*|(?:van|der|den|de|von|da|di|le|la|du|del|dos|das|ten|ter|bin|al|el)\b)"
_VANCOUVER_AUTHORS = re.compile(
    rf"^\s*(?:{_NAME_WORD}(?:[ -]{_NAME_WORD}){{0,2}}\s+[A-Z]{{1,4}}(?:,\s*|\s*\.\s+|\s+(?=et\s+al)))+"
    r"(?:et\s+al\.?\s*)?", re.U)


def _authorish(seg: str) -> bool:
    if " et al" in seg:
        return True
    parts = [p.strip() for p in re.split(r",|;|&|\band\b", seg) if p.strip()]
    if not parts:
        return False
    short = [p for p in parts if len(p.split()) <= 4]
    initials = [p for p in parts if re.search(r"(?:^|\s)[A-Z](?:\.|\b)|\b[A-Z]{2,3}$", p)]
    if len(short) >= 0.8 * len(parts) and len(initials) >= 0.5 * len(parts):
        return True
    # full names without initials: "Ken Hayashi, Koichi Yoshimura, Hiroyuki Kondo"
    return len(parts) >= 2 and all(
        2 <= len(p.split()) <= 4 and all(w[:1].isupper() and w.lower() not in _NAME_STOP for w in p.split())
        for p in parts)


_NAME_STOP = {"and", "of", "the", "in", "for", "with", "on", "a", "an", "to", "by", "from", "at", "vs",
              "versus", "after", "among", "during", "between", "via", "under", "over"}


def guess_title(entry: str) -> Optional[str]:
    emph = _emphasized_title(entry)
    if emph and not _authorish(emph):
        return emph
    e = URL_RE.sub(" ", _strip_markdown(entry))
    e = DOI_RE.sub(" ", e)
    e = PMID_RE.sub(" ", e)
    e = re.sub(r"\b(?:doi|PMID|PMCID|arXiv)\s*:?\s*", " ", e, flags=re.I)
    e = _strip_marker(e)
    m = re.search(r"[“\"]([^”\"]{12,400})[”\"]", e)
    if m and len(m.group(1).split()) >= 3:  # a quoted word inside a title ("“polytraumatism”") is not the title
        return m.group(1).strip(" .,")
    m = re.search(r"\(\s*(?:19|20)\d{2}[a-z]?(?:,[^)]*)?\)\s*[.,]?\s+(.+?[.?!])(?:\s|$)", e)
    if m and len(m.group(1).split()) >= 3:
        return m.group(1).strip(" .")
    m = _VANCOUVER_AUTHORS.match(e)
    if m and m.end() < len(e):
        segs = _segments(e[m.end():])
        # a person's initial inside the title ("The toxins of William B. Coley and ...") is not a full stop
        # (only when the next piece reads as the sentence going on, with a long lower-case word such as
        # "treatment"; a journal name "Am J Clin Nutr" / "Journal of Nutrition" is not merged in)
        while len(segs) >= 2 and re.search(r"\b[A-Z]\.?$", segs[0]) and len(segs[0].split()) >= 3 \
                and segs[1][:1].isupper() and not re.search(r"\d", segs[1]) \
                and re.search(r"\b(?!with\b|from\b)[a-z]{4,}\b", segs[1]):
            segs[:2] = [segs[0].rstrip(".") + ". " + segs[1]]
        if segs and len(segs[0].split()) >= 2:
            return segs[0].strip(" .")
    for seg in _segments(e):
        words = seg.split()
        # "Pan, Y., et al., Proposing re-classification of ...": the title follows "et al.," in the same segment
        m = re.search(r"\bet\s+al\.?\s*,\s*(.+)$", seg)
        if m and len(m.group(1).split()) >= 3:
            return m.group(1).strip(" .")
        if len(words) < 3 or _authorish(seg):
            continue
        digits = sum(ch.isdigit() for ch in seg)
        if digits > 0.2 * len(seg):
            continue
        return seg.strip(" .")
    return None


def guess_year(entry: str, title: Optional[str] = None) -> Optional[int]:
    e = DOI_RE.sub(" ", entry)
    e = URL_RE.sub(" ", e)
    if title:  # "...Survey 2003 to 2010. Ann Intern Med. 2014;160:..." -> the year is not in the title
        e = e.replace(title, " ")
    m = re.search(r"\(\s*((?:19|20)\d{2})[a-z]?\s*[),]", e)
    if m:
        return int(m.group(1))
    m = re.search(r"\b((?:19|20)\d{2})[a-z]?\s*[;:]", e)          # Vancouver "2014;160:293"
    if m:
        return int(m.group(1))
    m = re.search(r"\b((?:19|20)\d{2})[a-z]?\b(?=\s*(?:[;:(.,]|$|\s+[A-Z][a-z]{2}\b))", e)
    if m:
        return int(m.group(1))
    m = YEAR_RE.search(e)
    return int(m.group(1)) if m else None


def guess_first_author(entry: str) -> Optional[str]:
    emph = _emphasized_title(entry)
    # a bold author block ("**Rajkomar A, Oren E, et al.** Title ...") is not a title-first entry
    if emph and not _authorish(emph) and _strip_marker(entry).lstrip("*_“\"").startswith(emph[:10]):
        # title-first form ("**Title** (Vaswani et al., 2017)"): the author sits after the title
        rest = entry.replace(emph, " ")
        m = re.search(r"([A-Z][\w'’\-]+)(?=\s+et al|,?\s*\(?(?:19|20)\d{2}\b|,?\s+(?:and|&)\s|,\s+[A-Z])", rest)
        return m.group(1) if m else None
    body = _strip_marker(_strip_markdown(entry))
    # initials first ("B. I. Alzghoul, ...", "R D G Theakston (1995)"): the surname follows them
    m = re.match(r"(?:[A-Z]\.\s*|[A-Z]\s+){1,3}([^\W\d_][\w'’\-]{1,})", body, re.U)
    if m:
        return m.group(1)
    m = re.match(r"([^\W\d_][\w'’\-]*)", body, re.U)
    if not m:
        return None
    word = m.group(1)
    if word.lower() in {"the", "a", "an", "in", "on", "world", "national", "american", "european",
                        "international", "who", "anonymous"}:
        return None
    if word.lower() in _PROSE_OPENERS:
        # a sentence that carries a reference ("If you want..., I can swap in Adams, Henry et al.
        # (2022) ...") : take the author list wherever it sits, never the sentence's first word
        m = re.search(r"\b([A-Z][\w'’\-]+)(?=(?:,\s*[A-Z][\w'’\-]+)*,?\s+et\s+al\b|,?\s*\((?:19|20)\d{2}\))", body)
        return m.group(1) if m else None
    return word


_PROSE_OPENERS = {"if", "you", "we", "i", "see", "also", "for", "this", "these", "here", "note", "and", "or",
                  "it", "they", "as", "to", "from", "by", "with", "consider", "alternatively", "another",
                  "plus", "optionally", "additionally", "finally", "then", "but", "however"}


def _bib_fields(body: str) -> dict:
    fields: dict = {}
    i, n = 0, len(body)
    while i < n:
        m = re.compile(r"\s*,?\s*([A-Za-z][\w\-]*)\s*=\s*").match(body, i)
        if not m:
            break
        name, i = m.group(1).lower(), m.end()
        if i < n and body[i] == "{":
            depth, j = 0, i
            while j < n:
                depth += body[j] == "{"
                depth -= body[j] == "}"
                j += 1
                if depth == 0:
                    break
            value, i = body[i + 1:j - 1], j
        elif i < n and body[i] == '"':
            j = body.find('"', i + 1)
            j = n if j < 0 else j
            value, i = body[i + 1:j], j + 1
        else:
            m2 = re.compile(r"[^,}\s]+").match(body, i)
            value = m2.group(0) if m2 else ""
            i = m2.end() if m2 else i + 1
        fields[name] = re.sub(r"\s+", " ", value.replace("{", "").replace("}", "")).strip()
    return fields


def parse_bibtex(text: str, max_items: int = 200) -> list[dict]:
    out: list[dict] = []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]*)\s*,", text):
        if m.group(1).lower() in ("comment", "string", "preamble"):
            continue
        start, depth, j = m.end(), 1, m.end()
        while j < len(text) and depth:
            depth += text[j] == "{"
            depth -= text[j] == "}"
            j += 1
        f = _bib_fields(text[start:j - 1])
        authors = [a.strip() for a in re.split(r"\s+and\s+", f.get("author", "")) if a.strip()]
        first = None
        if authors:
            a0 = authors[0]
            first = a0.split(",")[0].strip() if "," in a0 else a0.split()[-1]
        cit = {"label": f"[{m.group(2)}]" if m.group(2) else f"#{len(out) + 1}",
               "raw": f"{f.get('author', '')} ({f.get('year', '')}). {f.get('title', '')}. "
                      f"{f.get('journal') or f.get('booktitle') or f.get('publisher') or ''}".strip()[:600]}
        if f.get("doi"):
            cit["doi"] = _clean_doi(re.sub(r"^https?://(dx\.)?doi\.org/", "", f["doi"], flags=re.I))
        if f.get("pmid"):
            cit["pmid"] = re.sub(r"\D", "", f["pmid"])
        eprint = f.get("eprint") or ""
        if eprint and ("arxiv" in (f.get("archiveprefix", "") + f.get("eprinttype", "")).lower()
                       or re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", eprint)):
            cit["arxiv"] = re.sub(r"v\d+$", "", eprint)
        if f.get("title"):
            cit["title"] = f["title"]
        if re.fullmatch(r"(?:19|20)\d{2}", f.get("year", "")):
            cit["year"] = int(f["year"])
        if first:
            cit["first_author"] = first
        if cit.get("title") or any(k in cit for k in ("doi", "pmid", "arxiv")):
            out.append(cit)
        if len(out) >= max_items:
            break
    return out


def extract_citations(text: str, max_items: int = 40) -> list[dict]:
    """Return citation dicts: {label, raw, doi?, pmid?, arxiv?, title?, year?, first_author?}."""
    text = text or ""
    lines = text.splitlines()
    entries: list[str] = []
    seen_spans: set[int] = set()

    # 1) reference sections
    i = 0
    while i < len(lines):
        if HEADING_RE.match(lines[i]):
            j = i + 1
            block: list[str] = []
            while j < len(lines) and not re.match(r"^\s*#{1,6}\s|^\s*(?:---|\*\*\*|___)\s*$", lines[j]):
                block.append(lines[j])
                j += 1
            entries.extend(_split_entries(block))
            seen_spans.update(range(i, j))
            i = j
        else:
            i += 1

    # 2) loose lines elsewhere that carry an id or look like a full reference
    for idx, ln in enumerate(lines):
        if idx in seen_spans or not ln.strip():
            continue
        if find_ids(ln) or looks_like_reference(ln):
            entries.append(ln.strip())

    out: list[dict] = []
    seen_keys: set[str] = set()
    for raw in entries:
        ids = find_ids(raw)
        cit = {"raw": raw[:600]}
        cit.update(ids)
        m = re.match(r"^\s*\[(\d{1,3})\]|^\s*(\d{1,3})[.)]\s", raw)
        if m:
            cit["label"] = f"[{m.group(1) or m.group(2)}]"
        title = guess_title(raw)
        if title:
            cit["title"] = title
        year = guess_year(raw, title)
        if year:
            cit["year"] = year
        author = guess_first_author(raw)
        if author:
            cit["first_author"] = author
        if not (ids or title):
            continue
        # same work cited twice with identical details = one check; any differing detail is kept
        key = "|".join([ids.get("doi") or ids.get("pmid") or ids.get("arxiv") or "",
                        " ".join(tokens(title or "")[:12]), str(year or ""), author or ""]).lower()
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(cit)
        if len(out) >= max_items:
            break
    for n, cit in enumerate(out, 1):
        cit.setdefault("label", f"#{n}")
    return out
