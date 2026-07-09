# EIOPA risk-free rate term structures — provenance

Real, unmodified monthly publications from EIOPA (European Insurance and Occupational
Pensions Authority), downloaded 2026-07-09 from
https://www.eiopa.europa.eu/tools-and-data/risk-free-interest-rate-term-structures_en
(`EIOPA_RFR_<yyyymmdd>.zip` → `*_Term_Structures.xlsx`).

| File | Used as |
|---|---|
| `EIOPA_RFR_20231231_Term_Structures.xlsx` | Locked-in base curve, 2024 annual cohorts |
| `EIOPA_RFR_20241231_Term_Structures.xlsx` | Locked-in base curve, 2025 annual cohorts |
| `EIOPA_RFR_20251231_Term_Structures.xlsx` | Locked-in base curve, 2026 annual cohorts |
| `EIOPA_RFR_20260331_Term_Structures.xlsx` | Current curve, prior quarter (Q1 2026) |
| `EIOPA_RFR_20260630_Term_Structures.xlsx` | Current curve, reporting date (Q2 2026) |

The demo reads the `RFR_spot_no_VA` tab (EUR column) — annual-compounded spot rates,
maturities 1–150y — plus the published UFR / LLP / CRA parameters for display.

IFRS 17 discount rates in the demo = EIOPA risk-free spot + an **illiquidity premium in bps
per portfolio** held as a versioned, approved assumption in `gov_assumption_registry`.
That composition is an illustrative configuration, not methodology advice: real entities own
their own bottom-up/top-down construction. Locked-in curves use the cohort-inception year-end
publication (disclosed simplification: annual cohorts take the preceding year-end curve).
