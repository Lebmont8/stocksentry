# StockSentry

A read-only research tool for Coinbase-issued B20 tokenized-stock reference data on Base. Python 3.12 standard library; no wallet, trading or background service.

## What it does
- Bounded, complete-pagination HTTPS snapshots with raw-response SHA-256 receipts
- Exact Decimal calculations, metadata/multiplier comparisons and holdings reference coverage
- Deterministic offline JSON analysis and escaped HTML reports
- Fail-closed handling of stale, incomplete or inconsistent inputs

## Run
~~~sh
python3 -B -m unittest -v test_stocksentry
python3 -B stocksentry.py capture --out snapshot-NEW.json
python3 -B stocksentry.py analyze snapshot-NEW.json --out research-NEW.json --html report-NEW.html
~~~
Capture explicitly contacts the fixed public reference API. Analysis and unit tests are offline. Output files are write-once: choose unused names.

A reference NAV is not an executable price or a guaranteed redemption amount. Snapshot hashes prove local replay identity, not source consensus. No on-chain execution, eligibility, share ownership or investment return is claimed.

MIT licensed. Original implementation developed with AI assistance.

## Continuous integration
A reference GitHub Actions workflow is included at ci/github-actions.yml. To activate it in your own repository, place it under .github/workflows/. Its presence as a reference does not mean hosted CI is enabled or has passed.
