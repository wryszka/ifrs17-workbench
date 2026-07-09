# DEMO_RUN — IFRS 17 Workbench (~20 minutes)

**Quick links (dev workspace `fevm-lr-dev-aws-us`):**
App: https://ifrs17-workbench-7474656169654171.aws.databricksapps.com ·
Hub tile: https://actuarial-workbench-7474656169654171.aws.databricksapps.com ·
Genie space `01f17b806f8c1065951e64f25bc74376` · dashboard `01f17b7f0e4618818bcd3e6e24f656fb`

**The pinned hero numbers** (byte-identical after every reset — quote them with confidence):
PROP-2026-REM loss component **€1,427,522.92** · PROP-2025-REM **€529,715.94** ·
CLT-2025-NSP CSM unlock **€1,642,225.40** leaving **€692,373.17** closing CSM ·
GL break **€412,340.00** cleared by journal MJ-2026Q2-001 · flood event gross **€16.8m**.

**Audience:** CFO / CRO / Head of IFRS 17 reporting / actuaries / finance change.
**Presenter:** you do NOT need to be an IFRS 17 practitioner. The in-app **Learn** page is your
prep (ten slides + glossary + Q&A armour). Present the *process*; let the workbench present the
*standard*. **Never defend an actuarial choice** — use the deflection at the bottom verbatim.

**Pre-flight (2 min):** app RUNNING · `ifrs17_98_smoke_test` last run ALL PASS · cache toggle
CACHED · no drifted file in landing (Cockpit shows a green Day 1–10 board, Day 9 sign-off
in progress).

The spine (same three beats as every Bricksurance workbench):
**all data together → see & govern → safely automate.**

---

## Beat 0 · Home (1 min)

"Bricksurance SE, a synthetic European P&C insurer, at its Q2 2026 close — reporting date
30 June 2026. Everything you'll see is computed, on this workspace, by governed engines; the
discount curves are real EIOPA publications. This is not a Databricks product — it's what the
close looks like when it lives on one platform."

## Beat 1 · Close Cockpit — the process (3 min)

- The Day 1–10 board: **IFRS 17 and Solvency II on the same calendar** — that's the real BAU
  pain, two regimes in one compressed window.
- Point at the chips: "no PMO typed these — the pipeline and engines write their own status."
- Feeds table: nine source types, five formats, one governed front door.
- The levers (the first wow): **⚠ Inject bad reserving feed → ▶ Rerun the close.** Watch the
  gate BLOCK Day 3 — the drifted actuarial file quarantined, nothing wrong reached measurement.
  Then **♻ Restore → ▶ Rerun** and narrate the timer: *"the full quarter re-produced in
  minutes — feeds to statements. What does a rerun cost you today?"*
  (If short on time, inject/rerun only and restore after the meeting — reset fixes everything.)

## Beat 2 · The cohort that turned onerous — the centrepiece (5 min)

Contract Groups → the red rows → **PROP-2026-REM**.

1. The June 2026 floods hit the property book. Claims themselves = **past service** → LIC.
2. The actuaries re-based flood frequency: **assumption v2, approved by the Chief Actuary on
   3 July, effective this run** — point at the registry row.
3. The §57 onerous test (run EVERY quarter, not just at year-end — show the headroom
   trend on Contract Groups): FCF for remaining coverage now exceeds the carrying LRC →
   **loss component, straight to P&L**.
4. The AoC panel: experience vs assumption vs finance — *"this split is the auditor
   conversation, and it's computed, not narrated."*
5. Reinsurance card: gross loss component, **loss-recovery component** on the quota share,
   net. One screen.
6. Drill to source: the actual flood claims. *"P&L charge → test → approved assumption →
   source claims. Four clicks."*
7. Optional: the **✳ Explain this movement** button — the agent narrates the numbers the
   engines computed. Say once: **"LLMs narrate, SQL decides."**

## Beat 3 · The CSM waterfall + the rate what-if (3 min)

Cohort selector → **CLT-2025-NSP**.

- The B96-ordered waterfall with paragraph references. This quarter: a casualty-inflation
  assumption (v2) unlocked ~€1.6m of CSM — visible, not fatal, and release still happens
  last, on coverage units.
- Discount & Assumptions → **+100 bps what-if**: every PV re-computed live in seconds; the
  P&L/OCI legs move — **and the CSM doesn't**, because accretion is locked-in. *"That's the
  detail your actuaries will check. It's right."*
- Mention: base curves are the real EIOPA files (UFR 3.30, LLP 20 — the published parameters);
  the illiquidity premium is a versioned assumption, because IFRS 17 rates are not Solvency II
  rates.

## Beat 4 · Results, recon, sign-off — see & govern (5 min)

- **Results**: the §80 face (insurance REVENUE — never premium), §101 roll-forward that foots
  to the balance sheet **because it's derived from the postings**, §104 by component, RA at a
  disclosed CL-75. Download the board pack (Excel — connect to Excel, don't replace it).
- **Reconciliation**: the €412,340.00 break (a SAP cost-centre slip), found, journal-cleared,
  residual zero. Journal identities are **masked by Unity Catalog itself** — the app's service
  principal is deliberately outside the finance-controllers group.
- **Sign-off & Audit** (the second wow): generate the certificate (evidence snapshot +
  SHA-256), then **Auditor mode → Reproduce**: the signed number re-read from pinned Delta
  versions, identical. *"Audit reperformance that takes weeks becomes a click. The audit trail
  is a join, not a project."*

## Beat 5 · IFRS 17 AI + close (2 min)

- Ask the supervisor: *"Why did the 2026 property cohort turn onerous?"* — show the **tool
  trace**: it called the onerous test, the AvE, the assumption history. Every interaction is
  in the governed activity log; the agents sit in an SS1/23-shaped model register with use
  constraints.
- Close: *"One platform: the feeds, the gate, the engines, the books, the statements, the
  evidence — and the same FCF data is already lined up for Solvency II. That's the point."*

---

## Q&A armour

The full card lives in-app (Learn page, bottom). The one rule: methodology questions get the
deflection, **verbatim**:

> "The platform is methodology-agnostic — your actuaries own the assumptions and methods; the
> workbench makes them versioned, governed and reproducible. What you're seeing is one
> illustrative configuration."

Hard questions you WILL get (short answers in-app): RA calibration · why EIOPA curves ·
transition balances · coverage-unit judgement · PAA eligibility · discounted PAA LIC ·
reinsurance held scope · AI hallucination (it can't cite what it didn't call — tool trace +
activity log) · IFRS 9 / tax / multi-GAAP (roadmap panel) · "is this an actuarial engine?"
(no — it consumes reserving output as a governed feed; it replaces the glue, not the engines).

## Reset

Sidebar **↺ Reset demo** (or job `ifrs17_99_reset`): removes any injected drift, clears the
narration cache, regenerates the world (seed 42), full-refreshes the medallion and re-runs the
close. Heroes come back byte-identical. Safe any time; takes ~15 minutes.
