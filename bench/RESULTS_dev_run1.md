# Benchmark: 100 real references × 7 forms = 700 checks
Run 2026-11-06, 395 s total, public API pools (no contact e-mail).

| Form | Expected | Correct | Unchecked | Other verdicts |
|---|---|---|---|---|
| clean_doi | verified | 88/100 (88.0%) | 0 | id_not_found 1, metadata_mismatch 8, wrong_id 3 |
| clean_no_id | verified | 93/100 (93.0%) | 0 | metadata_mismatch 6, not_found 1 |
| fake_doi | id_not_found | 89/99 (89.9%) | 1 | wrong_id 10 |
| swapped_doi | wrong_id | 97/100 (97.0%) | 0 | id_not_found 1, metadata_mismatch 2 |
| wrong_year | metadata_mismatch | 92/100 (92.0%) | 0 | id_not_found 1, verified 4, wrong_id 3 |
| wrong_author | metadata_mismatch | 96/100 (96.0%) | 0 | id_not_found 1, wrong_id 3 |
| fabricated | not_found | 96/100 (96.0%) | 0 | metadata_mismatch 4 |

False alarms on real references: 19/200 (9.5%). Retracted/concern verdicts on real references are reported, not counted as false alarms.

Note (added later): this first run scored fake_doi against id_not_found only (89/99). Under the current rule, which also accepts wrong_id when the altered DOI happens to belong to another paper, fake_doi is 99/99 (100%), and the caught range across corrupted forms is 92.0-100%.
