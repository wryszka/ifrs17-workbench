# DECISIONS — IFRS 17 Workbench

Reverse-chronological, self-contained decision narratives with a verification note each, plus a
shared **gotchas** section. Companion to `docs/REVIEW/REVIEW_REPORT.md` (the 8-agent panel).

---

## 2026-09-22 · 8-agent review panel + remediation

Ran the full Bricksurance 8-agent review panel (BUILD_AND_REVIEW.md §7 v2.2) after an external
implementation review flagged six priority defects. Panel confirmed all six and added more. Report:
`docs/REVIEW/REVIEW_REPORT.md`; shareable rendering:
https://docs.google.com/document/d/1U8xirFj6eKHV3cC8YJdpTOYIyP4GfkCF1eMd3YTADEY/edit

**Fixed this pass (app/config/security/docs — no hero-number impact; verify with a redeploy + preflight):**
- **Auditor reproduce (B1)** now reads the signed figure from the certificate and re-reads the table
  `TIMESTAMP AS OF` the sign-off time, comparing signed-vs-pinned and exposing whether the book moved.
  (Was comparing the current value to `max(version)` of the same table → always matched.)
- **Approver/signer identity (B4/security)** derived from the authenticated Databricks Apps user
  (`X-Forwarded-Email`), never the request body; enum-validated workstream/decision.
- **Sign-off enforcement (M3)** — server-side `_close_ready()` gate (close gate green + recon tied + no
  failed runs) + prerequisite workstream approvals before CFO sign-off / certificate.
- **Least privilege / masking (B4/security)** — `grant_app_sp.py` now revokes SELECT on the unmasked base
  `gov_journal` and grants only the masked `gov_journal_secure` view to the app SP.
- **Rate what-if (M1)** — signs cash flows by `cf_type` (premiums as inflows); on-screen claim narrowed.
- **SQL reliability (M4 / current-Databricks)** — `sql.py` now uses short `wait_timeout` +
  `on_wait_timeout=CONTINUE` + backoff polling + a `status.state` check; a failed statement raises
  instead of returning `[]`; `query_many` logs failures instead of silently swallowing them.
- **`/api/preflight`** health endpoint (warms the warehouse, reports readiness) + narration cache is now
  an UPSERT and its key is versioned by the close's data version (a rerun invalidates stale narration).
- **Serverless env v5 → v6** across all job resources; **paragraph refs** corrected in 04d (§44 for the
  CSM roll-forward, B96(a-d)/B119 for the movements).
- **GMM loss component (B2)** — the CSM-exhaustion loss component is now written (appended) to
  `gld_loss_component`; zero across the surviving-CSM hero book, but the machinery is wired, not dead code.
- **Docs** — new `docs/DEMO_QA.md` (tab 2); DEMO_RUN reframing of the reproduce/sign-off beats and timing.

**Verification:** app + engine Python and `grant_app_sp.py` compile clean; a close rerun + `98_smoke_test`
all-PASS on DEV validates B2 preserves the byte-stable heroes (see run log). App fixes verified via
`/api/preflight` + a real reproduce/sign-off turn on the deployed app.

**Deferred (hero-number-moving or larger — labelled + roadmapped, disclosed in `DEMO_QA.md`):**
- **M2** reinsurance-held full roll-forward (ceded premium → service expense so a settled treaty runs to
  zero) and **M5** acquisition-recovery revenue **presentation** line — both change disclosed P&L/BS/revenue
  figures, so they need a deliberate re-pin of heroes + cascade into DEMO_RUN/operator doc/memory. Held for
  an explicit go.
- **7.6** technical-account (CSM/LRC) GL tie-out; **5.4** parameter-binding migration across SQL endpoints
  (hardening — no trivial exploit today as `esc()` doubles quotes and `PERIOD` is constant); **8.4**
  responsive breakpoints ≤900/≤820px; **3.2** close-timing (pre-run/async vs the 3-min cockpit beat).

## 2026-09-22 · Reporting date is calendar-anchored (reset does NOT roll dates forward)

Deliberate divergence from the sibling-workbench principle "reset rolls dates to today." IFRS 17 closes
are calendar-anchored: the demo is the **Q2 2026 close, as at 30 June 2026**, and the bundled EIOPA
publications are locked-in per cohort year-end. Reset re-produces the same anchored close deterministically
(seed 42) rather than moving the calendar. **Verification:** heroes are byte-identical after reset.

---

## Gotchas (once-bitten, inherited across siblings)

- `write_engine` **overwrites** (`engine_common.py`). A second engine must never `write_engine` a table
  another engine owns — append with an idempotent DELETE first (see 04d → `gld_loss_component`).
- `gld_close_status` MERGE / `cockpit()` does `v[0]` on non-list panels — do **not** add extra keys to a
  `query_many` result dict; keep the shape `{key: rows}`.
- Nested f-strings with a backslash-escaped quote break on older Python — build the fragment on its own line.
- Statement Execution: a single long `wait_timeout` masks a cold warehouse as empty data — poll on
  PENDING/RUNNING and check `status.state`.
- `CREATE OR REPLACE FUNCTION` revokes EXECUTE; MERGE INSERT needs an explicit column list on serverless.
- Don't VACUUM — time travel is auditor mode (and now the reproduce endpoint depends on retained history).
- Don't run two `ifrs17_quarter_close` runs concurrently (they race the DLT update); DABs jobs queue by
  default — cancel stragglers before rerunning.
