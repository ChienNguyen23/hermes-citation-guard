# Benchmark (refs_test.json): 80 real references × 7 forms = 560 checks
fake_doi counts id_not_found or wrong_id as correct (an altered DOI can belong to another paper).
Run 2026-11-06, 182 s total, public API pools (no contact e-mail).

| Form | Expected | Correct | Unchecked | Other verdicts |
|---|---|---|---|---|
| clean_doi | verified | 78/80 (97.5%) | 0 | metadata_mismatch 2 |
| clean_no_id | verified | 76/80 (95.0%) | 0 | metadata_mismatch 2, not_extracted 2 |
| fake_doi | id_not_found | 79/79 (100.0%) | 1 | – |
| swapped_doi | wrong_id | 79/80 (98.8%) | 0 | metadata_mismatch 1 |
| wrong_year | metadata_mismatch | 80/80 (100.0%) | 0 | – |
| wrong_author | metadata_mismatch | 80/80 (100.0%) | 0 | – |
| fabricated | not_found | 78/80 (97.5%) | 0 | not_extracted 2 |

False alarms on real references: 4/160 (2.5%). Retracted/concern verdicts on real references are reported, not counted as false alarms.
Not recognised as a reference at all (silently skipped, counted as incorrect above): 4/560.
