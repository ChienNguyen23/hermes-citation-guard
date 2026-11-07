"""Offline tests: extraction, BibTeX, verdict logic against a fake registry, hook behaviour.

Run:  python3 -I tests/test_offline.py      (no network, no Hermes needed)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _load  # noqa: E402

cg = _load.load()
ex, vf, src = cg.extract, cg.verify, cg.sources
HERE = Path(__file__).parent
FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


# ------------------------------------------------------------------ extraction
cits = ex.extract_citations((HERE / "fixtures_mixed.md").read_text())
check("extract: 10 entries", len(cits) == 10, len(cits))
expect_titles = {
    "[1]": "Highly accurate protein structure prediction with AlphaFold",
    "[2]": "Attention is all you need",
    "[3]": "Joining a conversation: the problem/gap/hook heuristic",
    "[5]": "Ileal-lymphoid-nodular hyperplasia, non-specific colitis, and pervasive developmental disorder in children",
    "[8]": "Quantum-enhanced transformer models reduce hospital readmissions: a multicenter randomized trial",
}
by = {c["label"]: c for c in cits}
for lab, t in expect_titles.items():
    check(f"extract: title {lab}", by.get(lab, {}).get("title") == t, by.get(lab, {}).get("title"))
check("extract: Lancet DOI with parentheses kept", by["[10]"].get("doi") == "10.1016/S0140-6736(20)31180-6")
check("extract: APA year", by["[4]"].get("year") == 2004)
check("extract: PMID", by["[3]"].get("pmid") == "26346497")
check("extract: arXiv", by["[2]"].get("arxiv") == "1706.03762")

md = "See [the paper](https://doi.org/10.1016/S0140-6736(20)31180-6)."
check("extract: DOI inside markdown link", ex.find_ids(md).get("doi") == "10.1016/S0140-6736(20)31180-6",
      ex.find_ids(md))
for prose in ["Smith et al. (2020) showed that sleep improves memory in 300 older adults living alone.",
              "In 2019 the team released version 3, which cut latency by 40% across all regions we tested."]:
    check("extract: prose is not a reference", not ex.looks_like_reference(prose), prose[:40])
agent_list = """Here are key papers:

- **Attention Is All You Need** (Vaswani et al., 2017), NeurIPS — introduced the Transformer.
- **Language Models are Few-Shot Learners** — Brown et al., 2020.
- “Deep residual learning for image recognition” by He, Zhang, Ren & Sun (2016).
- Transformers changed NLP in 2017 and are now everywhere in industry and research.
"""
al = ex.extract_citations(agent_list)
check("extract: agent-style list, 3 papers", len(al) == 3, [c.get("title") for c in al])
check("extract: bold title", al and al[0].get("title") == "Attention Is All You Need", al and al[0].get("title"))
check("extract: author from agent list", al and al[0].get("first_author") in (None, "Vaswani"), al and al[0])
check("extract: APA italic journal is not the title",
      ex.guess_title("Smith, J., & Lee, K. (2019). Sleep and memory in older adults. *Journal of Aging Research*, 12(3), 45-67.")
      == "Sleep and memory in older adults")
for no_vol in ["Rizwan MS, Ahmad G, Ashraf D. Systemic risk: The impact of COVID-19. Finance Research Letters. 2020.",
               "Turner J, Zeniou A. Technique and outcome of post-mastectomy chest wall radiotherapy. Br J Radiol. 2016:20160060.",
               "Rizwan, M. S., & Ahmad, G. (2020). Systemic risk: The impact of COVID-19. Finance Research Letters."]:
    check("extract: reference without volume/pages", ex.looks_like_reference(no_vol), no_vol[:40])
for prose in ["Smith J said we should meet again in 2021 to discuss the results with everyone.",
              "Lee K, our analyst, reviewed it. The 2024 numbers look fine overall for the quarter."]:
    check("extract: prose with a name is not a reference", not ex.looks_like_reference(prose), prose[:40])
check("ligature folds: LEUKÆMIA == leukaemia", ex.tokens("LEUKÆMIA IN BENZENE WORKERS") == ["leukaemia", "in", "benzene", "workers"])
yt = ex.extract_citations("1. Denniston M, Jiles R. Chronic hepatitis C virus infection in the United States, "
                          "National Health and Nutrition Examination Survey 2003 to 2010. Ann Intern Med. 2014;160:293-300.")
check("US/UK spelling matches", vf.title_similarity("Leukemia in benzene workers", "LEUKÆMIA IN BENZENE WORKERS") >= 0.85)
check("year inside the title is not the publication year", yt and yt[0].get("year") == 2014, yt and yt[0].get("year"))
check("extract: nothing in plain prose",
      ex.extract_citations("The meeting is on 12 May 2026. Bring the 2025 budget.") == [])

bib = """@article{jumper2021,
  title = {Highly accurate protein structure prediction with {AlphaFold}},
  author = {Jumper, John and Evans, Richard},
  journal = {Nature}, year = {2021}, doi = {10.1038/s41586-021-03819-2}}
@inproceedings{vaswani2017, title="Attention is all you need", author="Ashish Vaswani and Noam Shazeer",
  booktitle={NeurIPS}, year=2017, eprint={1706.03762}, archivePrefix={arXiv}}"""
b = ex.parse_bibtex(bib)
check("bibtex: 2 entries", len(b) == 2, len(b))
check("bibtex: braces stripped from title", b[0].get("title") == "Highly accurate protein structure prediction with AlphaFold")
check("bibtex: first author 'Last, First'", b[0].get("first_author") == "Jumper")
check("bibtex: first author 'First Last'", b[1].get("first_author") == "Vaswani")
check("bibtex: arXiv eprint", b[1].get("arxiv") == "1706.03762")


# ------------------------------------------------------------------ verdicts (fake registry)
def rec(title, fam, year, doi=None, notices=None, **kw):
    r = {"source": "crossref", "doi": doi, "title": title, "authors": [{"family": fam, "full": f"{fam} A"}],
         "years": [year], "journal": "J", "type": "journal-article", "notices": notices or []}
    r.update(kw)
    return r


class FakeClient:
    def __init__(self):
        self.cache = src.Cache(None)
        self.by_doi = {
            "10.1234/real": rec("Sleep duration and mortality in older adults", "Kim", 2019, "10.1234/real"),
            "10.1234/retracted": rec("Vitamin X cures everything", "Fraud", 2015, "10.1234/retracted",
                                  notices=[{"type": "retraction", "notice_doi": "10.1234/notice", "year": 2017,
                                            "source": "retraction-watch"}]),
        }
        self.search_pool = list(self.by_doi.values())

    def doi(self, d):
        if d.lower() in self.by_doi:
            return dict(self.by_doi[d.lower()])
        raise src.NotFound(d)

    crossref_work = doi

    def pubmed(self, p):
        raise src.NotFound(p)

    def arxiv(self, a):
        raise src.NotFound(a)

    def crossref_search(self, q, rows=5):
        return [dict(r) for r in self.search_pool]

    def pubmed_title_search(self, t, limit=3):
        return []

    def arxiv_title_search(self, t, limit=3):
        return []


class DownClient(FakeClient):
    def doi(self, d):
        raise src.NetError("timeout")

    def crossref_search(self, q, rows=5):
        raise src.NetError("timeout")


fc = FakeClient()


def status_of(line, client=fc):
    c = ex.extract_citations(line)
    assert c, line
    return vf.check_one(c[0], client)


cases = [
    ("verified (DOI)", "1. Kim A, Lee B. Sleep duration and mortality in older adults. J. 2019;1:1-2. doi:10.1234/real", "verified"),
    ("verified (search, no id)", "1. Kim A, Lee B. Sleep duration and mortality in older adults. J. 2019;1:1-2.", "verified"),
    ("id_not_found", "1. Kim A. Sleep duration and mortality in older adults. J. 2019;1:1-2. doi:10.1234/nope", "id_not_found"),
    ("wrong_id", "1. Doe J. Gut microbiome shifts after antibiotic exposure in infants. J. 2019;1:1-2. doi:10.1234/real", "wrong_id"),
    ("metadata_mismatch (year)", "1. Kim A. Sleep duration and mortality in older adults. J. 2012;1:1-2. doi:10.1234/real", "metadata_mismatch"),
    ("metadata_mismatch (author)", "1. Park Q. Sleep duration and mortality in older adults. J. 2019;1:1-2. doi:10.1234/real", "metadata_mismatch"),
    ("retracted", "1. Fraud A. Vitamin X cures everything. J. 2015;1:1-2. doi:10.1234/retracted", "retracted"),
    ("not_found", "1. Roe Z. Quantum cats reduce readmissions in a randomized trial. J. 2023;5:e1-e9.", "not_found"),
]
for name, line, want in cases:
    got = status_of(line)
    check(f"verdict: {name}", got["status"] == want, f"{got['status']} {got['issues']}")

r = status_of("1. Kim A. Sleep duration and mortality in older adults. J. 2019;1:1-2. doi:10.1234/nope")
check("id_not_found suggests the real record", (r.get("suggestion") or {}).get("doi") == "10.1234/real", r.get("suggestion"))
r = status_of("1. Kim A. Sleep duration and mortality in older adults. J. 2019;1:1-2. doi:10.1234/real", DownClient())
check("network down -> unchecked, never 'fake'", r["status"] == "unchecked", r["status"])
r = status_of("1. Roe Z. Quantum cats reduce readmissions in a randomized trial. J. 2023;5:e1-e9.", DownClient())
check("network down (search) -> unchecked", r["status"] == "unchecked", r["status"])

# regressions found by the benchmark (bench/RESULTS.md, first run)
r = vf.check_one({"raw": "Kendall-Tackett K. X. J. 2000.", "first_author": "Kendall-Tackett", "year": 2000,
                  "title": "Sleep duration and mortality in older adults"},
                 type("C", (FakeClient,), {"crossref_search": lambda self, q, rows=5: [
                     rec("Sleep duration and mortality in older adults", "Kendall-Tackett", 2000,
                         authors=[{"family": "Kendall-Tackett", "full": "Kathleen A Kendall-Tackett"}])]})())
check("hyphenated surname matches", r["status"] == "verified", r)
check("spelling variant is a note, not a mismatch",
      vf._name_match("Eniko", "E.T. Enikov") == "spelling" and vf._name_match("Smith", "John Jones") == "")
sici = "French D. Longitudinal tracking. J Strength Cond Res. 2004;18:101-7. doi:10.1519/1533-4287(2004)018<0101:LTOMPC>2.0.CO;2"
check("SICI DOI kept whole", ex.find_ids(sici).get("doi") == "10.1519/1533-4287(2004)018<0101:LTOMPC>2.0.CO;2",
      ex.find_ids(sici))
check("HTML-wrapped DOI trimmed", ex.find_ids("<a href=x>10.1234/abc.5</a>").get("doi") == "10.1234/abc.5")
check("cited title without subtitle matches",
      vf.title_similarity("The pathology of dry eye", "The pathology of dry eye: the interaction between the ocular surface and lacrimal glands") >= 0.85)
check("invented tail on a short real title does not match",
      vf.title_similarity("Short-chain fatty acid inhibitors of in lung development and tumorigenesis", "Short Chain Fatty Acid") < 0.85)
check("word-level, not character-level, similarity",
      vf.title_similarity("Genetic aspects pediatric B-cell acute lymphoblastic leukemia",
                          "Genetic Alterations of Pediatric Acute Lymphoblastic Leukemia") < 0.85)

summary = vf.check_all(ex.extract_citations("\n".join(c[1] for c in cases)), fc)
check("check_all: problem count", summary["problems"] == 6, summary["counts"])

# ------------------------------------------------------------------ hook
cg._client = fc
cg._get_client = lambda: fc  # noqa: E731 - test seam
check("hook: no citations -> None", cg._on_llm_output(response_text="Sure, the meeting is at 3pm.") is None)
clean = "Key paper:\n\n1. Kim A, Lee B. Sleep duration and mortality in older adults. J. 2019;1:1-2. doi:10.1234/real"
check("hook: all verified -> None (reply untouched)", cg._on_llm_output(response_text=clean) is None)
bad = clean + "\n2. Kim A. Sleep duration and mortality in older adults. J. 2019;1:1-2. doi:10.1234/nope"
out = cg._on_llm_output(response_text=bad)
check("hook: problem -> original kept + footer", bool(out) and out.startswith(bad) and "**Citation Guard**" in out)
check("hook: never runs twice", cg._on_llm_output(response_text=out) is None)

# ------------------------------------------------------------------ /citecheck path
class FakeCtx:  # a gateway-served session: its cwd is not this process's cwd
    def session_cwd(self):
        return str(HERE)

    def get_config(self, key, default=None):
        return default

_saved_ctx, cg._ctx = cg._ctx, FakeCtx()
check("command: relative path resolves against the session cwd",
      cg._resolve_user_path("fixtures_mixed.md") == HERE / "fixtures_mixed.md")
check("command: absolute path untouched", cg._resolve_user_path("/tmp/x.md") == Path("/tmp/x.md"))
report = cg._handle_command("fixtures_mixed.md")
check("command: relative file from the session cwd is read", "Citation check: fixtures_mixed.md" in report, report[:120])
cg._ctx = None
check("command: no ctx falls back to process cwd", cg._resolve_user_path("a.md") == Path.cwd() / "a.md")
cg._ctx = _saved_ctx

print(f"\n{'OK' if not FAILS else 'FAILED'}: {len(FAILS)} failure(s)")
sys.exit(1 if FAILS else 0)
