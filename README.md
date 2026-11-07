# citation-guard

**Catch fabricated, mismatched and retracted references before anyone relies on them.**

Language models invent references that look right. In one audit, 55% of the citations ChatGPT-3.5
produced for short literature reviews did not exist, and 18% of GPT-4's; among the real ones, 43% and
24% carried substantive errors ([Walters & Wilder 2023](https://doi.org/10.1038/s41598-023-41032-5)).
Newer models are better, not immune, and a single fake reference in a grant, a manuscript or a
clinical note is expensive.

`citation-guard` checks every reference against the registries that actually issue the identifiers:
doi.org, Crossref, PubMed and arXiv. It is deterministic: no LLM calls, no API keys, Python standard
library only.

What the reply guard appended to an answer containing ten references (real output, `tests/fixtures_mixed.md`):

```markdown
---
**Citation Guard** checked 10 reference(s) against Crossref, PubMed and arXiv in 10.2 s: 6 problem(s), 4 verified.
- ✗ **retracted**: 5. Wakefield AJ, Murch SH, Anthony A, et al. Ileal-lymphoid-nodular hyperplasia, non-specific colitis, and ...
  - retraction notice 10.1016/s0140-6736(10)60175-4 (2010) via Retraction Watch
- ✗ **identifier does not exist**: 6. Smith J, Lee K. Deep learning for early detection of sepsis in emergency departments. Nat Med. 2022;28(4...
  - DOI 10.1038/s41591-022-99999-x does not exist
- ✗ **identifier points to a different work**: 7. Brown T, Mann B, Ryder N, et al. Language models are few-shot learners. Advances in Neural Information P...
  - DOI 10.1038/s41586-021-03819-2 resolves to a different work
- ? **not found in Crossref, PubMed or arXiv**: 8. Nguyen T, Patel R, Garcia M. Quantum-enhanced transformer models reduce hospital readmissions: a multice...
  - no matching record in Crossref, PubMed or arXiv (fabricated, or not indexed: book, local journal, report)
- ⚠ **details differ from the registry**: 9. Hassabis D, Kohli P, Senior A. Highly accurate protein structure prediction with AlphaFold. Nature. 2018...
  - year: cited 2018, registry 2021
  - authors: cited 'Hassabis' et al., registry has 'Jumper J' et al.
- ✗ **retracted**: 10. Mehra MR, Desai SS, Ruschitzka F, Patel AN. Hydroxychloroquine or chloroquine with or without a macroli...
  - retraction notice 10.1016/s0140-6736(20)31324-6 (2020) via Retraction Watch
_Do not rely on flagged references until you have checked them yourself._
```

## What it adds to Hermes

| Surface | What it does |
|---|---|
| **Reply guard** (`transform_llm_output` hook) | Every final reply that contains references (a DOI, PMID, arXiv id, or a full bibliographic entry) is checked. If anything fails, a short footer like the one above is **appended**. The reply itself is never rewritten. Clean replies pass through untouched. To stay fast it skips the closest-record search; the tool and `/citecheck` include it. |
| **Tool** `verify_citations` | The model can check a bibliography before presenting it. Returns structured JSON per reference: status, issues, the registry record, and the closest real record when the cited one is wrong. |
| **Command** `/citecheck <file or text>` | Full report for a manuscript or reference list: `.md`, `.txt`, `.bib` (BibTeX) or `.docx`. A relative path is resolved against the session's working directory (`ctx.session_cwd()`); when in doubt pass an absolute path or `~/...`. |

## Verdicts

| Status | Meaning |
|---|---|
| ✗ `id_not_found` | The DOI / PMID / arXiv id does not exist in its registry. |
| ✗ `wrong_id` | The id exists but belongs to a different work (the classic hallucination: a real DOI glued to an invented title). The closest real record for the cited title is suggested when one exists. |
| ✗ `retracted` | The work has a retraction, withdrawal or removal notice (Crossref update notices, which include the Retraction Watch database; PubMed "Retracted Publication"). |
| ? `not_found` | No id was given and no matching record exists in Crossref, PubMed or arXiv. Either fabricated, or not indexed (books, local journals, reports): check by hand. |
| ⚠ `metadata_mismatch` | The work exists, but the year or first author differs from what was cited. |
| ⚠ `concern` | The work carries an expression of concern. |
| · `unchecked` | A registry could not be reached in time. **Never** reported as fake. |
| ✓ `verified` | Title, year and first author agree with the registry. |

How a reference is matched: identifiers are resolved first (Crossref, then doi.org content
negotiation for DataCite / mEDRA / other agencies; PubMed E-utilities; arXiv API). Without an id, the
title is searched in Crossref, then PubMed, then arXiv; a record is accepted only when the title
agrees (token similarity ≥ 0.85), and records that also agree on first author and year win. A year
within ±1 counts as agreeing (online-first vs print).

## Install

```bash
hermes plugins install ChienNguyen23/hermes-citation-guard
hermes plugins enable citation-guard
```

Start a new session. Check with `hermes plugins show citation-guard` or `/plugins`.

## Settings

All optional, under `plugins.entries.citation-guard.settings` (or the Desktop Plugins tab):

| Key | Default | |
|---|---|---|
| `guard_replies` | `true` | Check references in every final reply. |
| `announce_clean` | `false` | Also append a one-line note when every reference verified. |
| `guard_timeout_s` | `15` | Time budget for one reply; references not checked in time are reported as not checked. |
| `request_timeout_s` | `8` | Timeout for one registry request. |
| `max_items` | `40` | Maximum references checked per reply or tool call. |
| `contact_email` | `""` | Sent to Crossref and NCBI as the polite-pool contact (their etiquette; it raises Crossref's limit from 1 to 3 searches per second). Used for nothing else. |
| `ncbi_api_key` | `""` | Optional free NCBI API key, sent only to NCBI. NCBI then counts requests per key instead of per IP (10/s instead of 3/s); the plugin keeps its own pace. |

## Benchmark

Real journal references taken from the structured reference lists of PubMed Central open-access
articles. Each reference is checked as written (with and without its DOI) and in five corrupted forms
that mirror how generated references go wrong: an altered DOI, the DOI of a different paper, the year
shifted by 3, the first author replaced, and a title spliced from two other papers with no DOI.

Two disjoint samples: `bench/refs_dev.json` (100 references from 50 articles) was used to find and fix
bugs; `bench/refs_test.json` (80 references from 40 *other* articles) was held out and run once before
the last round of fixes. All runs used the public API pools (no contact e-mail).

| Run | False alarms on real references | Corrupted references caught (by form) |
|---|---|---|
| Dev, first run ([table](bench/RESULTS_dev_run1.md)) | 19/200 (9.5%) | 92.0 – 100% |
| **Held-out, before the last fixes** ([table](bench/RESULTS_test_heldout_run1.md)) | **4/160 (2.5%)** | **97.5 – 100%** |
| Dev, final code ([table](bench/RESULTS_dev.md)) | 1/200 (0.5%) | 99 – 100% |
| Held-out, final code ([table](bench/RESULTS_test.md)) | 0/160 (0%) | 98.8 – 100% |

The held-out row before the last fixes is the honest estimate for references the code has never seen.
The bugs it exposed, and that are now fixed and covered by tests: ligatures ("LEUKÆMIA" vs
"leukemia"), a year inside the title ("Survey 2003 to 2010") taken as the publication year, and entries
without volume or pages not being recognised.

The one remaining "false alarm" in the dev set is not one: that PMC article cites Hedges et al. 2008
with the DOI of a different 2015 paper (`10.11646/zootaxa.3986.2.1`). The plugin flags it as
`wrong_id`, which is correct.

Two fabricated dev references with very short titles now come back `unchecked` instead of
`not_found`: PubMed is not searched for titles under four content words, so "not found in Crossref,
PubMed or arXiv" would overstate what was asked. A network or coverage gap is never reported as fake.

Reproduce: `python3 -I bench/run_bench.py bench/refs_test.json` (about 3 minutes cold).

## Disclosure

What you would want to know before installing:

- **Network calls**: `api.crossref.org`, `doi.org`, `eutils.ncbi.nlm.nih.gov`, `export.arxiv.org`, all
  read-only public metadata APIs. Only the reference itself is sent (its DOI / PMID / arXiv id, title,
  first author and year), never the rest of the conversation. Requests carry the User-Agent
  `citation-guard/<version>` and, only if you set `contact_email`, that address.
- **Rate limits**: requests are paced to each registry's limits (Crossref, as reported in its own
  response headers: 1 search/s and 5 lookups/s without a contact address, 3 searches/s with one;
  NCBI kept under 3 requests/s without an API key; arXiv one call every 3 s). A registry that
  answers 429/403, or serves an "Access Denied" page, is not called again for 10 minutes; its
  references come back `unchecked`. Error pages are never cached. Several Hermes processes on one
  machine share an IP but not a pacing budget, so heavy use should set `ncbi_api_key`.
- **Local data**: a cache of registry answers (7 days) in the plugin's data directory
  (`<hermes home>/plugin-data/citation-guard/registry_cache.json`). Delete it any time.
- **Files**: `/citecheck <path>` reads the file you name. Nothing else outside the plugin's data
  directory is read.
- **No** shell commands, credentials, telemetry, background services or LLM calls. Lookups still in
  flight when a reply's time budget ends finish in the background and only warm the cache.

## Limits

- Inline author–year mentions without a title ("Smith et al. (2020) showed…") are not checkable and
  are skipped.
- `not_found` cannot tell a fabricated reference from a real one that no registry indexes. It says so.
- It checks that a reference exists and matches; it does not judge whether the paper supports the
  sentence that cites it.

## Tests

```bash
python3 -I tests/test_offline.py     # 70 offline tests: extraction, BibTeX, every verdict, the hook
python3 -I bench/run_bench.py        # live benchmark (network, a few minutes)

# inside a real Hermes install: tool via the tool registry, hook via invoke_hook, /citecheck via the
# plugin command table (17 checks, network, no LLM needed)
HERMES_HOME=<home> <hermes-venv>/bin/python tests/hermes_integration.py
```

`hermes plugins validate . --install-deps` passes all checks (manifest, capability match, security
scan "safe", no core override) and `hermes plugins doctor` loads and registers the plugin.

MIT licensed.
