"""Seeded error-injection benchmark on real references.

Every real reference is checked as written (should verify) and in five corrupted forms that mirror
how LLM-made references go wrong. Expected verdicts:

  clean_doi      real reference with its DOI              -> verified
  clean_no_id    real reference, DOI removed              -> verified (found by title search)
  fake_doi       last characters of the DOI altered       -> id_not_found
  swapped_doi    DOI of a different paper attached        -> wrong_id
  wrong_year     year shifted by 3                        -> metadata_mismatch
  wrong_author   first author replaced                    -> metadata_mismatch
  fabricated     title spliced from two other papers,     -> not_found
                 no DOI

Usage: python3 -I bench/run_bench.py [sample.json] [limit]      (network; ~7 min for 100 references)
"""
import json
import random
import string
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
import _load  # noqa: E402

cg = _load.load()
HERE = Path(__file__).parent
# fake_doi: an altered DOI sometimes exists and belongs to another paper -> wrong_id is equally correct
ACCEPT = {"fake_doi": {"id_not_found", "wrong_id"}}
EXPECT = {"clean_doi": "verified", "clean_no_id": "verified", "fake_doi": "id_not_found",
          "swapped_doi": "wrong_id", "wrong_year": "metadata_mismatch", "wrong_author": "metadata_mismatch",
          "fabricated": "not_found"}


def vancouver(r, *, doi=True, year=None, first=None, title=None):
    names = [[first or r["authors"][0][0], r["authors"][0][1]]] + r["authors"][1:]
    au = ", ".join(f"{s} {''.join(w[0] for w in g.replace('.', ' ').replace('-', ' ').split())}".strip()
                   for s, g in names[:6])
    if r["n_authors"] > 6:
        au += ", et al"
    pages = r["fpage"] + (f"-{r['lpage']}" if r["lpage"] else "")
    s = f"{au}. {title or r['title']}. {r['journal']}. {year or r['year']};{r['volume']}:{pages}."
    if doi:
        s += f" doi:{doi if isinstance(doi, str) else r['doi']}"
    return s


def corrupt_doi(doi, rng):
    head, tail = doi[:-4], doi[-4:]
    alphabet = string.digits if tail.isdigit() else string.digits + string.ascii_lowercase
    while True:
        new = "".join(rng.choice(alphabet) if c.isalnum() else c for c in tail)
        if new != tail:
            return head + new


def build_cases(refs, seed=11):
    rng = random.Random(seed)
    cases = []
    n = len(refs)
    for i, r in enumerate(refs):
        other = refs[(i + 1) % n]
        third = refs[(i + 7) % n] if n > 8 else refs[(i + 2) % n]
        cases += [
            ("clean_doi", vancouver(r)),
            ("clean_no_id", vancouver(r, doi=False)),
            ("fake_doi", vancouver(r, doi=corrupt_doi(r["doi"], rng))),
            ("swapped_doi", vancouver(r, doi=other["doi"])),
            ("wrong_year", vancouver(r, year=r["year"] + 3 if r["year"] + 3 <= 2026 else r["year"] - 3)),
            ("wrong_author", vancouver(r, first=next(x["authors"][0][0] for x in refs[i + 2:] + refs
                                                     if x["authors"][0][0].lower() not in
                                                     {a[0].lower() for a in r["authors"]}))),
        ]
        a, b = other["title"].split(), third["title"].split()
        spliced = " ".join(a[: max(2, len(a) // 2)] + b[len(b) // 2:])
        cases.append(("fabricated", vancouver(r, doi=False, title=spliced)))
    return cases


def main():
    sample = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "refs_sample.json"
    refs = json.loads(sample.read_text(encoding="utf-8"))
    if len(sys.argv) > 2:
        refs = refs[: int(sys.argv[2])]
    tag = sample.stem.replace("refs_", "")
    cases = build_cases(refs)
    client = cg.sources.Client(cg.sources.Cache(HERE / ".bench_cache.json"), timeout=15)
    t0 = time.time()
    rows = []
    for k in range(0, len(cases), 50):  # batches keep the cache flushing and show progress
        chunk = cases[k:k + 50]
        found = [cg.extract.extract_citations("1. " + text) for _, text in chunk]
        cits = [dict(f[0], label=f"{kind}#{k + j}") for j, ((kind, _), f) in enumerate(zip(chunk, found)) if f]
        res = iter(cg.verify.check_all(cits, client, workers=4)["results"])
        # a reference the extractor does not recognise is a failure of the plugin, recorded as such
        res = [next(res) if f else {"status": "not_extracted", "issues": ["not recognised as a reference"]}
               for f in found]
        for (kind, text), r in zip(chunk, res):
            rows.append({"kind": kind, "expect": EXPECT[kind], "got": r["status"], "cited": text,
                         "issues": r.get("issues"), "registry": r.get("registry"), "suggestion": r.get("suggestion")})
        print(f"{len(rows)}/{len(cases)} done, {time.time() - t0:.0f}s", flush=True)
    (HERE / f"results_{tag}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = [f"# Benchmark ({sample.name}): {len(refs)} real references × 7 forms = {len(rows)} checks",
             "fake_doi counts id_not_found or wrong_id as correct (an altered DOI can belong to another paper).",
             f"Run {time.strftime('%Y-%m-%d')}, {time.time() - t0:.0f} s total, public API pools (no contact e-mail).",
             "", "| Form | Expected | Correct | Unchecked | Other verdicts |", "|---|---|---|---|---|"]
    for kind, want in EXPECT.items():
        sub = [r for r in rows if r["kind"] == kind]
        ok = sum(r["got"] in ACCEPT.get(kind, {want}) for r in sub)
        unk = sum(r["got"] == "unchecked" for r in sub)
        other: dict = {}
        for r in sub:
            if r["got"] not in ACCEPT.get(kind, {want}) | {"unchecked"}:
                other[r["got"]] = other.get(r["got"], 0) + 1
        checked = len(sub) - unk
        pct = f"{100 * ok / checked:.1f}%" if checked else "n/a"
        lines.append(f"| {kind} | {want} | {ok}/{checked} ({pct}) | {unk} | "
                     + (", ".join(f"{k} {v}" for k, v in sorted(other.items())) or "–") + " |")
    clean = [r for r in rows if r["kind"] in ("clean_doi", "clean_no_id") and r["got"] != "unchecked"]
    false_alarm = [r for r in clean if r["got"] not in ("verified", "retracted", "concern", "not_extracted")]
    missed = [r for r in rows if r["got"] == "not_extracted"]
    lines += ["", f"False alarms on real references: {len(false_alarm)}/{len(clean)} "
                  f"({100 * len(false_alarm) / max(1, len(clean)):.1f}%). "
                  "Retracted/concern verdicts on real references are reported, not counted as false alarms.",
              f"Not recognised as a reference at all (silently skipped, counted as incorrect above): {len(missed)}/{len(rows)}."]
    (HERE / f"RESULTS_{tag}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
