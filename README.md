# IFRS 17 Workbench — Bricksurance SE

The **quarterly IFRS 17 close, end to end on Databricks**: nine governed feeds → a quality
gate that visibly blocks a bad actuarial delivery → real PAA/GMM measurement engines
(B96-ordered CSM roll-forward, the §57 onerous test every quarter, risk adjustment at CL-75,
discounting on **real EIOPA curves**) → a balanced subledger reconciled to GL → §80 statements
and §101/§104 disclosures that foot to the balance sheet **by construction** → CFO sign-off
with an evidence certificate — and any signed number reproducible **as-at** via Delta time
travel. AI agents narrate the movements; deterministic SQL decides them.

Sibling of the Bricksurance workbench family (`underwriting-workbench`,
`claims_workbench`, `reinsurance_workbench`, `lifecast`, `solvency-ii-qrt-demo-pnc`), same
framework and design system, launched from the `actuarial-workbench` hub.

**About this demo** — Bricksurance SE is a fictional insurer; every policy, claim and balance
is synthetic. The EIOPA risk-free curves are real, unmodified publications
(`data/eiopa/PROVENANCE.md`). The measurement configuration is illustrative — the platform is
methodology-agnostic, your actuaries own assumptions and methods. Nothing here is accounting
advice, and the workbench **consumes** reserving output (it does not replace actuarial
engines).

## The story (Q2 2026)

- **The blocked close** — a schema-drifted reserving export quarantines at the door and blocks
  Day 3 on the cockpit; restore it and the whole quarter re-produces in minutes.
- **The cohort that turned onerous** — June 2026 floods hit the property book; the approved
  flood re-basis (assumption v2) pushes the 2025/2026 property cohorts through the §57 test:
  loss component to P&L, loss-recovery component on the quota share, drill to the source
  claims in four clicks.
- **The CSM that survived** — a casualty-inflation unlock moves the CLT-2025 waterfall
  visibly without exhausting it; shift rates +100 bps live and watch the CSM *not* move
  (accretion is locked-in).
- **Auditor mode** — reproduce any signed number from pinned Delta versions + assumption
  versions. The audit trail is a join, not a project.

## Install (one edit)

```bash
databricks bundle deploy -t dev     # elsewhere: --var catalog=<your_catalog>
```

then run, in order: `ifrs17_00_setup` → `ifrs17_medallion` (full refresh) →
`ifrs17_quarter_close` → `ifrs17_05_ml` → `ifrs17_06_agents` → `ifrs17_98_smoke_test`
(**all checks must PASS**). Full steps + SP grants + asset inventory: `docs/DEPLOY.md`.
Demo script: `docs/DEMO_RUN.md`. Hard rules: `CLAUDE.md`.

## Layout

```
databricks.yml          # bundle: catalog var = the portability anchor; dev + shared targets
resources/*.yml         # one file per job/pipeline/app (all serverless)
notebooks/              # 00 truth → 00b landing → 01-03 DLT medallion → 03b/03c DQ + gate
                        # → 04* measurement engines → 05 ML → 06* agents → 07 governance
                        # → 12 board pack → 90 levers → 98 smoke (the QA spec) → 99 reset
app/                    # thin FastAPI + self-contained dist/index.html (no npm build)
data/eiopa/             # REAL EIOPA RFR publications (provenance inside)
docs/                   # DEMO_RUN.md, DEPLOY.md
```
