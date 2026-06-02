# Edge-Case Fixture Zoo

15 small CSV fixtures exercising distinct bug classes in the upload → training → KG pipeline. Each is intentionally minimal (10–100 rows) so the full E2E suite stays committable and fast (<5 minutes wall on a warm container).

The harness in `tests/test_e2e.py` glob-loads every `*.csv` here and runs each through the full pipeline: upload → retrain → poll training run → verify memories ≥1.

| Fixture | Purpose | Bug class exercised |
|---|---|---|
| `multi_lang.csv` | Burmese / CJK / Arabic / Hindi / emoji columns. Triggers UTF-8 char-vs-byte length mismatch (`LENGTH()` returns chars, bytes can be 3-4× higher). | KG cell-value extractor classifier (`dash/tools/knowledge_graph.py`), prompt salting cross-tenant collision, Layer 8/9 KG triple emission |
| `decimal_heavy.csv` | 5 numeric cols at 14-digit precision. | PG Decimal serialization, pandas float64 round-trip, NaN-vs-Inf JSON sanitize in dashboards endpoint |
| `constant_columns.csv` | 2 columns with the same value in every row. | dimension catalog "DISTINCT=1" classification, KG noise filter (single-value cols should NOT seed triples), profile null/cardinality stats |
| `nan_inf.csv` | Literal `nan`, `inf`, `-inf`, empty strings in numeric columns. | pandas NaN coercion, `_sanitize_json()` for FastAPI return, statsmodels seasonal_decompose edge case |
| `mixed_types.csv` | One text col with `"1"`, `"$1,000"`, `"NaN"`, `"n/a"`, `"<NULL>"`, `"-"`, real numbers. | Type coercion stress, `_clean_dataframe()` currency/comma/percent stripping, null normalization, "is this dim or measure?" classifier |
| `huge_strings.csv` | Burmese 60-char sentences mixed with 3-char labels (`Y`, `N0`, `Q`). PG `LENGTH()` returns chars but bytes are 180–240 → naïve `LENGTH BETWEEN x AND y` filters break. | KG cell-value extractor must compute `AVG/MAX(LENGTH)` over ALL rows, not `COUNT(DISTINCT)` with BETWEEN filter (see CLAUDE.md "Never use `LENGTH BETWEEN x AND y`" rule) |
| `auto_injected_cols.csv` | Data CSV that already has `_source_file` and `_period` as headers. | Upload pipeline header collision: pipeline auto-stamps these on every promoted row. Test we don't double-stamp or fail-load |
| `empty_columns.csv` | 4 cols, 2 are 100% NULL/empty. | profile column null_pct handling, dimension catalog "skip column with no distinct values", brain-formula generator robustness |
| `single_row.csv` | Exactly 1 data row. | training pipeline `_smart_sample_rows()` (which wants 3 start + 3 mid + 3 end), Q&A SQL verification (CV-F1 etc. need ≥10 rows) |
| `million_row.csv` | Sentinel CSV with "synthetic 1M reference" header note + ~5000 real rows. | Row-count signal hint into LLM rescue path (P2 from CityPharma session), profile streaming over large tables, fail-loud >50K row cap on ML worker |
| `duplicate_pk.csv` | `id` column with duplicate values (each id appears 2-3×). | PK detection edge — auto-detector must NOT mark as primary, contract `load_key` falls through single→composite→period→content_hash, `seed=42` reproducibility |
| `unicode_filename_漢字.csv` | CJK in the filename itself, ASCII contents. | filename → table-name sanitizer, raw-file persistence path under `raw_uploads/`, extraction-plan `source_file` column round-trip |
| `empty.csv` | Header row only, 0 data rows. | "should reject" path — upload pipeline should 400 with a clean error, NOT load an empty table. Test harness allows 400 for `empty.csv` and returns early |
| `mixed_dates.csv` | One col with `2026-01-01`, `01/01/2026`, `Jan 1, 2026`, `2026-01-01T12:00:00Z`, empty, `not a date`. | Date type coercion, "is this a date column?" classifier, Q&A SQL date-cast (`::date`) generator, dimension hierarchy detection on unparseable dates |
| `pii_loaded.csv` | Emails, phone numbers (5 formats), SSN-shape strings, addresses. | PII auto-detect (69 regex patterns in `column_classifier.py`), PII mask strategy chooser, `auto_promote_facts` PII scrub gate (must NOT save SSN into brain) |

## Running

The full harness picks all 15 up automatically:

```bash
make test-edge-cases
# or directly:
pytest tests/test_e2e.py -v --tb=short -m e2e
```

Each fixture goes through: multipart upload to `/api/upload` → `/api/projects/{slug}/retrain` → 5-minute polling on `/api/projects/{slug}/training-runs?limit=1` until `status='done'` → assertion that `memories ≥ 1` and `table_metadata ≥ 1` were populated.

The harness skips automatically when the container isn't reachable (`/api/health` 200 check), so the file can sit in CI as a fast no-op when only unit tests run.

## Adding a new fixture

1. Drop a new `.csv` into this directory.
2. Add a row to the table above describing the bug class it exercises.
3. If the fixture is expected to be rejected at upload, extend the early-return branch in `tests/test_e2e.py` to match it (default: only `empty.csv` is allowed to 400).

Keep fixtures small. The hard cap is roughly 250 KB / fixture — anything larger should be synthesized at test time and referenced as a sentinel like `million_row.csv`.
