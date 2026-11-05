# Benchmark (refs_dev.json): 100 real references × 7 forms = 700 checks
fake_doi counts id_not_found or wrong_id as correct (an altered DOI can belong to another paper).
Run 2026-11-06, 142 s total, public API pools (no contact e-mail).

| Form | Expected | Correct | Unchecked | Other verdicts |
|---|---|---|---|---|
| clean_doi | verified | 99/100 (99.0%) | 0 | wrong_id 1 |
| clean_no_id | verified | 100/100 (100.0%) | 0 | – |
| fake_doi | id_not_found | 100/100 (100.0%) | 0 | – |
| swapped_doi | wrong_id | 100/100 (100.0%) | 0 | – |
| wrong_year | metadata_mismatch | 99/100 (99.0%) | 0 | wrong_id 1 |
| wrong_author | metadata_mismatch | 99/100 (99.0%) | 0 | wrong_id 1 |
| fabricated | not_found | 99/100 (99.0%) | 0 | metadata_mismatch 1 |

False alarms on real references: 1/200 (0.5%). Retracted/concern verdicts on real references are reported, not counted as false alarms.
Not recognised as a reference at all (silently skipped, counted as incorrect above): 0/700.
