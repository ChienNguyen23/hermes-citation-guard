"""Decide, per citation, whether the cited work exists and matches what was written.

Deterministic rules, no LLM. Network failures always become `unchecked`, never a
claim that something is fake.

Statuses (most to least severe):
  id_not_found       the DOI / PMID / arXiv id does not exist in its registry
  wrong_id           the id exists but belongs to a different work
  retracted          the work exists and has a retraction / withdrawal notice
  not_found          no id given, and no matching record in Crossref or PubMed
                     (fabricated, or not indexed: books, local journals, grey literature)
  metadata_mismatch  the work exists; year or first author differs from the citation
  concern            the work carries an expression of concern
  unchecked          the registries could not be reached in time
  verified           the work exists and title, year and first author agree
"""

from __future__ import annotations

import concurrent.futures as cf
import time
from difflib import SequenceMatcher
from typing import Optional

from .extract import ascii_fold, content_tokens, tokens
from .sources import Client, NetError, NotFound

TITLE_OK = 0.85        # title match needed to accept a registry record found by search
TITLE_SUGGEST = 0.9    # stricter: only then do we offer it as "closest real record"

SEVERITY = ["id_not_found", "wrong_id", "retracted", "not_found", "metadata_mismatch", "concern",
            "unchecked", "verified"]
PROBLEM = {"id_not_found", "wrong_id", "retracted", "not_found", "metadata_mismatch", "concern"}


_content = content_tokens


def title_similarity(cited: str, reg: str) -> float:
    """Word-level agreement between a cited title and a registry title.

    Word-level, not character-level: "Genetic aspects of pediatric B-cell leukemia" and "Genetic
    alterations of pediatric leukemia" share most characters but are different papers. A cited title
    that stops before the registry's subtitle is fine (authors drop subtitles); a registry title that
    is only the opening words of a much longer cited title is not (that is how invented titles grow
    out of real short ones)."""
    tc, tr = _content(cited or ""), _content(reg or "")
    if not tc or not tr:
        return 0.0
    # US/UK spelling and small typos: leukemia/leukaemia, tumor/tumour, behavior/behaviour
    reg_set = set(tr)
    tc = [t if t in reg_set or len(t) < 5 else
          next((r for r in reg_set if len(r) >= 5 and SequenceMatcher(None, t, r).ratio() >= 0.85), t)
          for t in tc]
    jacc = len(set(tc) & set(tr)) / len(set(tc) | set(tr))
    seq = SequenceMatcher(None, tc, tr, autojunk=False).ratio()
    prefix = 0.0
    if len(tc) >= 3 and tr[: len(tc)] == tc:
        prefix = 0.9                      # cited = registry title minus its subtitle
    elif len(tr) >= 4 and tc[: len(tr)] == tr and len(tr) >= 0.7 * len(tc):
        prefix = 0.9                      # registry lacks a short tail the citation has
    return max(jacc, seq, prefix)


def containment(reg_title: str, raw: str) -> float:
    reg = set(_content(reg_title or ""))
    if len(reg) < 3:
        return 0.0
    return len(reg & set(tokens(raw or ""))) / len(reg)


def title_score(cit: dict, rec: dict) -> float:
    """Title agreement in [0, 1]. Containment (registry title found inside the raw citation) rescues
    entries whose title we could not cut out cleanly, but only for registry titles that are not much
    shorter than the cited one: a 4-word record like "Nuclear factor kappa B" must not match a
    20-word title that happens to contain those words."""
    cited, reg = cit.get("title") or "", rec.get("title") or ""
    sim = title_similarity(cited, reg)
    n_reg, n_cited = len(set(_content(reg))), len(set(_content(cited)))
    if n_reg >= 4 and (not n_cited or n_reg >= 0.6 * n_cited):
        sim = max(sim, containment(reg, cit.get("raw", "")))
    return sim


def _name_match(cited: str, full: str) -> str:
    """'exact' / 'spelling' (one-letter-ish variant: Eniko/Enikov, Bennett/Bennet) / ''."""
    c = tokens(cited)                    # "Kendall-Tackett" -> ["kendall", "tackett"]
    f = tokens(full)
    if c and all(t in f for t in c):
        return "exact"
    joined = "".join(c)
    for t in f + ["".join(f[i:i + 2]) for i in range(len(f) - 1)]:
        if len(t) >= 4 and SequenceMatcher(None, joined, t).ratio() >= 0.85:
            return "spelling"
    return ""


def _author_check(cit: dict, rec: dict) -> Optional[str]:
    """None if fine/unknown, else an issue string. Spelling variants are reported by _author_note."""
    cited = cit.get("first_author")
    authors = rec.get("authors") or []
    if not cited or not authors or _name_match(cited, authors[0]["full"]):
        return None
    if any(_name_match(cited, a["full"]) for a in authors[1:6]):
        return f"first author: cited '{cited}', registry lists '{authors[0]['full']}' first"
    return f"authors: cited '{cited}' et al., registry has '{authors[0]['full']}' et al."


def _author_note(cit: dict, rec: dict) -> Optional[str]:
    cited, authors = cit.get("first_author"), rec.get("authors") or []
    if cited and authors and _name_match(cited, authors[0]["full"]) == "spelling":
        return f"author spelling: cited '{cited}', registry '{authors[0]['full']}'"
    return None


def _known_agreement(cit: dict, rec: dict) -> tuple[int, int]:
    """(how many of first author / year are known on both sides, how many of those agree)."""
    known = agree = 0
    if cit.get("first_author") and rec.get("authors"):
        known += 1
        agree += _author_check(cit, rec) is None
    if cit.get("year") and rec.get("years"):
        known += 1
        agree += _year_check(cit, rec) is None
    return known, agree


def _year_check(cit: dict, rec: dict) -> Optional[str]:
    cy, years = cit.get("year"), rec.get("years") or []
    if not cy or not years:
        return None
    if any(abs(cy - y) <= 1 for y in years):
        return None
    return f"year: cited {cy}, registry {years[0] if len(years) == 1 else '/'.join(map(str, years))}"


def _brief(rec: dict) -> dict:
    authors = rec.get("authors") or []
    out = {
        "title": rec.get("title"),
        "first_author_family": authors[0]["family"] if authors else None,
        "n_authors": len(authors) or None,
        "first_authors": [a["full"] for a in authors[:3]],
        "year": (rec.get("years") or [None])[0],
        "journal": rec.get("journal") or None,
        "doi": rec.get("doi"),
    }
    if rec.get("pmid"):
        out["pmid"] = rec["pmid"]
    if rec.get("arxiv"):
        out["arxiv"] = rec["arxiv"]
    if rec.get("doi"):
        out["url"] = "https://doi.org/" + rec["doi"]
    elif rec.get("pmid"):
        out["url"] = f"https://pubmed.ncbi.nlm.nih.gov/{rec['pmid']}/"
    elif rec.get("arxiv"):
        out["url"] = f"https://arxiv.org/abs/{rec['arxiv']}"
    return {k: v for k, v in out.items() if v}


def _agrees(cit: dict, rec: dict) -> int:
    """How many of (first author, year) agree: 0, 1 or 2. Unknown counts as agreeing."""
    return (_author_check(cit, rec) is None) + (_year_check(cit, rec) is None)


def _search(cit: dict, client: Client) -> Optional[dict]:
    """Best registry record for a citation without a usable id, or None.

    A candidate must match the title (score >= TITLE_OK). Among those, records that also agree on
    first author and year win; we keep searching the next registry while agreement is not full."""
    title = cit.get("title") or ""
    query = title if len(_content(title)) >= 3 else cit["raw"]
    best, best_key = None, (-1, 0.0)

    def consider(rec: dict) -> None:
        nonlocal best, best_key
        s = title_score(cit, rec)
        if s < TITLE_OK:
            return
        key = (_agrees(cit, rec), s)
        if key > best_key:
            best, best_key = rec, key

    for rec in client.crossref_search(query, rows=8):
        consider(rec)
    if best_key[0] == 2:
        return best
    # Generic titles ("Leukemia in benzene workers") are shared by several papers: a second pass
    # with first author and year finds the one actually cited.
    extra = " ".join(str(x) for x in (cit.get("first_author"), cit.get("year")) if x)
    if extra and query != cit["raw"]:
        for rec in client.crossref_search(f"{query} {extra}", rows=5):
            consider(rec)
        if best_key[0] == 2:
            return best
    if len(_content(title)) >= 4:
        for pmid in client.pubmed_title_search(title, limit=3):
            try:
                consider(client.pubmed(pmid))
            except NotFound:
                continue
        if best_key[0] < 2:
            for rec in client.arxiv_title_search(title, limit=3):
                consider(rec)
    return best


def _retraction(rec: dict, client: Client, result: dict) -> None:
    notices = rec.get("notices")
    unchecked = False
    if notices is None and rec.get("doi"):
        try:  # records from PubMed / doi.org: one Crossref lookup brings the notices
            notices = client.crossref_work(rec["doi"]).get("notices")
        except NotFound:
            notices = []
        except (NetError, ValueError):
            unchecked = True
    notices = notices or []
    severe = [n for n in notices if n["type"] in ("retraction", "withdrawal", "removal", "partial_retraction")]
    concern = [n for n in notices if "concern" in n["type"]]
    title = (rec.get("title") or "").strip().upper()
    if severe:
        n = severe[0]
        result["status"] = "retracted"
        result["issues"].append(f"{n['type'].replace('_', ' ')} notice {n['notice_doi']}"
                                + (f" ({n['year']})" if n.get("year") else "")
                                + (" via Retraction Watch" if n.get("source") == "retraction-watch" else ""))
    elif rec.get("retracted") or title.startswith(("RETRACTED", "WITHDRAWN", "RETRACTION:")):
        result["status"] = "retracted"
        result["issues"].append("PubMed lists it as a Retracted Publication" if rec.get("retracted")
                                else "the registry title marks it as retracted")
    elif unchecked:
        result["issues"].append("retraction status could not be checked")
    elif concern and result["status"] in ("verified", "metadata_mismatch"):
        n = concern[0]
        result["status"] = "concern"
        result["issues"].append(f"expression of concern {n['notice_doi']}")


def check_one(cit: dict, client: Client, check_retraction: bool = True, suggest: bool = True) -> dict:
    """suggest=False skips the extra search for the closest real record (keeps the reply guard fast)."""
    result = {"label": cit.get("label"), "cited": cit["raw"][:240], "status": "unchecked", "issues": []}
    rec, missing = None, []
    try:
        for kind, getter in (("doi", client.doi), ("pmid", client.pubmed), ("arxiv", client.arxiv)):
            ident = cit.get(kind)
            if not ident or rec is not None:
                continue
            try:
                rec = getter(ident)
                rec["_via"] = kind
            except NotFound:
                missing.append(f"{kind.upper() if kind != 'arxiv' else 'arXiv'} {ident} does not exist")

        if rec is None and missing:
            result["status"] = "id_not_found"
            result["issues"].extend(missing)
            real = _search(cit, client) if (suggest and cit.get("title")) else None
            if real and _agrees(cit, real) >= 1 and title_score(cit, real) >= TITLE_SUGGEST:
                result["suggestion"] = _brief(real)
                result["issues"].append("a work with this title exists under a different identifier")
            return result

        if rec is not None:
            has_text = len(_content(cit.get("title") or "")) >= 4 or len(_content(cit["raw"])) >= 8
            score = title_score(cit, rec)
            known, agree = _known_agreement(cit, rec)
            # Different work: the title clearly differs, unless first author AND year both match
            # (then it is the same paper under a variant / truncated registry title); or the title
            # only half-matches and neither author nor year matches.
            different = (score < 0.5 and not (known == 2 and agree == 2)) or \
                        (score < TITLE_OK and known >= 1 and agree == 0)
            if has_text and different:
                result["status"] = "wrong_id"
                result["issues"].append(f"{rec['_via'].upper() if rec['_via'] != 'arxiv' else 'arXiv'} "
                                        f"{cit[rec['_via']]} resolves to a different work")
                result["registry"] = _brief(rec)
                real = _search(cit, client) if (suggest and cit.get("title")) else None
                if (real and real.get("doi") != rec.get("doi") and _agrees(cit, real) >= 1
                        and title_score(cit, real) >= TITLE_SUGGEST):
                    result["suggestion"] = _brief(real)
                return result
            if not has_text:
                result["issues"].append("identifier exists; no title given to compare")
            elif score < TITLE_OK and not cit.get("title") and known == 2 and agree == 2:
                # no title was cited (a reference inside a sentence, say), so there is nothing to
                # compare; identifier + first author + year all agreeing is the evidence
                result["issues"].append("identifier, first author and year agree; no title given to compare")
            elif score < TITLE_OK:
                result["issues"].append(f"title differs from registry: '{rec.get('title')}'")
        else:
            rec = _search(cit, client)
            if rec is None:
                result["status"] = "not_found"
                result["issues"].append("no matching record in Crossref, PubMed or arXiv "
                                         "(fabricated, or not indexed: book, local journal, report)")
                return result

        result["registry"] = _brief(rec)
        for issue in (_year_check(cit, rec), _author_check(cit, rec), _author_note(cit, rec)):
            if issue:
                result["issues"].append(issue)
        hard = [i for i in result["issues"] if i.startswith(("year", "authors", "first author", "title differs"))]
        result["status"] = "metadata_mismatch" if hard else "verified"
        if check_retraction:
            _retraction(rec, client, result)
        return result
    except NetError as e:
        result["status"] = "unchecked"
        result["issues"].append(f"registry unreachable: {e}")
        return result
    except Exception as e:  # never let one odd record break the batch
        result["status"] = "unchecked"
        result["issues"].append(f"internal error: {type(e).__name__}: {e}")
        return result


def check_all(citations: list[dict], client: Client, check_retraction: bool = True,
              time_budget: Optional[float] = None, workers: int = 4, suggest: bool = True) -> dict:
    t0 = time.monotonic()
    results: list[Optional[dict]] = [None] * len(citations)
    pool = cf.ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="citation-guard")
    futs = {pool.submit(check_one, c, client, check_retraction, suggest): i for i, c in enumerate(citations)}
    done, pending = cf.wait(futs, timeout=time_budget)
    for f in done:
        results[futs[f]] = f.result()
    for f in pending:
        i = futs[f]
        results[i] = {"label": citations[i].get("label"), "cited": citations[i]["raw"][:240],
                      "status": "unchecked", "issues": ["time budget exceeded"]}
    # Do not block the agent on stragglers: queued checks are cancelled, running ones finish
    # in the background and still warm the cache for the next turn.
    pool.shutdown(wait=False, cancel_futures=True)
    client.cache.flush()
    counts: dict = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {
        "checked": len(results),
        "seconds": round(time.monotonic() - t0, 1),
        "counts": {k: counts[k] for k in SEVERITY if k in counts},
        "problems": sum(counts.get(k, 0) for k in PROBLEM),
        "results": results,
    }
