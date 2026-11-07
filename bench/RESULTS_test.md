# Benchmark (refs_test.json): 80 real references × 7 forms = 560 checks
fake_doi counts id_not_found or wrong_id as correct (an altered DOI can belong to another paper).
Run 2026-11-07, re-scored with the current code from the cached registry answers of the 2026-11-06 cold run (1 s; the cold run took about 3 min, public API pools, no contact e-mail).

| Form | Expected | Correct | Unchecked | Other verdicts |
|---|---|---|---|---|
| clean_doi | verified | 80/80 (100.0%) | 0 | – |
| clean_no_id | verified | 80/80 (100.0%) | 0 | – |
| fake_doi | id_not_found | 80/80 (100.0%) | 0 | – |
| swapped_doi | wrong_id | 79/80 (98.8%) | 0 | metadata_mismatch 1 |
| wrong_year | metadata_mismatch | 80/80 (100.0%) | 0 | – |
| wrong_author | metadata_mismatch | 80/80 (100.0%) | 0 | – |
| fabricated | not_found | 80/80 (100.0%) | 0 | – |

False alarms on real references: 0/160 (0.0%). Retracted/concern verdicts on real references are reported, not counted as false alarms.
Not recognised as a reference at all (silently skipped, counted as incorrect above): 0/560.
