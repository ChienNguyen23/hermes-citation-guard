"""Build bench/refs_sample.json: a seeded random sample of real journal references taken from the
structured reference lists (JATS <mixed-citation>/<element-citation>) of PubMed Central open-access
articles. Only bibliographic facts are kept (authors, year, title, journal, volume, pages, DOI).

Usage: python3 -I bench/build_sample.py <folder of PMC .xml files> [n=100] [seed=7] [out.json] [exclude.json]
  exclude.json: an earlier sample whose source articles must not be reused (held-out test set).
"""
import html
import json
import random
import re
import sys
from pathlib import Path


def text(x: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", x or ""))).strip()


def one(tag: str, block: str) -> str:
    m = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", block, re.S)
    return text(m.group(1)) if m else ""


def refs_of(xml: str, source: str):
    for block in re.findall(r"<ref\b[^>]*>(.*?)</ref>", xml, re.S):
        if 'publication-type="journal"' not in block:
            continue
        doi = re.search(r'<pub-id pub-id-type="doi">(.*?)</pub-id>', block, re.S)
        authors = re.findall(r"<surname>(.*?)</surname>\s*(?:<given-names>(.*?)</given-names>)?", block, re.S)
        year = re.search(r"<year[^>]*>\s*((?:19|20)\d{2})", block)
        title = one("article-title", block).rstrip(".")
        if not (doi and authors and year and len(title.split()) >= 4):
            continue
        yield {
            "authors": [[text(s), text(g)] for s, g in authors[:8]],
            "n_authors": len(authors),
            "year": int(year.group(1)),
            "title": title,
            "journal": one("source", block),
            "volume": one("volume", block),
            "fpage": one("fpage", block),
            "lpage": one("lpage", block),
            "doi": text(doi.group(1)),
            "from": source,
        }


def main():
    folder = Path(sys.argv[1])
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    rng = random.Random(int(sys.argv[3]) if len(sys.argv) > 3 else 7)
    files = sorted(folder.glob("*.xml"))
    if len(sys.argv) > 5:
        used = {r["from"] for r in json.loads(Path(sys.argv[5]).read_text(encoding="utf-8"))}
        files = [f for f in files if f.stem not in used]
    rng.shuffle(files)
    pool, seen = [], set()
    for f in files:
        refs = list(refs_of(f.read_text(encoding="utf-8", errors="replace"), f.stem))
        rng.shuffle(refs)
        for r in refs[:2]:  # at most 2 per article: spread across journals and fields
            if r["doi"].lower() not in seen:
                seen.add(r["doi"].lower())
                pool.append(r)
        if len(pool) >= n:
            break
    out = Path(sys.argv[4]) if len(sys.argv) > 4 else Path(__file__).with_name("refs_sample.json")
    out.write_text(json.dumps(pool[:n], ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(pool[:n])} references from {len({r['from'] for r in pool[:n]})} articles -> {out}")


if __name__ == "__main__":
    main()
