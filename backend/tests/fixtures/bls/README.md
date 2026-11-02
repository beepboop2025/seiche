# BLS Public Data API fixture

`cpi-2025-2026.json` is the BLS Public Data API v2 response of 2026-11-02 to
one POST to `https://api.bls.gov/publicAPI/v2/timeseries/data/` for series
`CUSR0000SA0E` and `CUSR0000SA0L1E`, years 2025 and 2026, without a key. It is
used by `backend/tests/test_bls_cpi.py`.

Source: U.S. Bureau of Labor Statistics, Consumer Price Index. BLS data are in
the public domain (`https://www.bls.gov/opub/copyright-information.htm`). BLS.gov
cannot vouch for the data or analyses derived from these data after the data
have been retrieved from BLS.gov.
