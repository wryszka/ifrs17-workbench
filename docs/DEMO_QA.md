# DEMO_QA — IFRS 17 Workbench (tab 2)

The live demo is tab 1 (`DEMO_RUN.md`). This is tab 2: the straight, sourced answers to the
questions the room asks that **can't be shown on screen** — above all the incumbent-champion's.
Persona-labelled (practitioner / platform / executive), cross-referenced to the beat that provokes
them. Every answer is honest about the demo's scope: this is a **synthetic, illustrative** P&C close
that shows the *pattern* on a governed platform — it consumes reserving output and does not replace
actuarial engines, and migration/parallel-run against a real SAS close is out of scope.

Universal deflection for any methodology question (verbatim):
> "The platform is methodology-agnostic — your actuaries own the assumptions and methods; the
> workbench makes them versioned, governed and reproducible. What you're seeing is one illustrative
> configuration."

---

### Q1 (platform, Beat 4) — "Your auditor mode reproduces a number. Change the data, rerun, and show me it still reproduces the number I signed."
It does. The certificate freezes the signed figures and the sign-off timestamp; **Reproduce** re-reads
the Delta history **AS AT that timestamp** (`TIMESTAMP AS OF signed_at`) and compares it to the frozen
figure — so the signed number reproduces from history even after the book moves, and the response also
shows the *current* value so you can see it has changed. **Honest boundary:** this reproduces the signed
**output**; it does not re-run the engines on the old inputs. Re-running the measurement on old
assumptions/curves is the *close rerun* lever, a separate action. The audit trail is a join over pinned
Delta versions + the assumption registry, not an engine re-execution. (Full engine re-performance from
pinned inputs + versioned engine code is a roadmap item.)

### Q2 (platform/executive, Beat 4) — "Sign-off is a web button. Where's the segregation of duties?"
In the demo the approver identity is taken from the **authenticated Databricks Apps user** (the
`X-Forwarded-Email` the platform injects), never from the form — you cannot type someone else's name.
The server also **refuses** a CFO sign-off unless the close is actually ready (close gate green,
trial-balance recon fully tied, no failed engine runs) and the prerequisite workstreams are approved.
**Production boundary:** real segregated-duty enforcement (approver roles, four-eyes) is wired to your
IdP / GRC (Okta/SAML groups); the workbench holds the governed, append-only approval + certificate record.

### Q3 (practitioner, Beat 2) — "Your reinsurance never expenses the ceded premium — a settled treaty leaves a phantom asset."
Correct that the current reinsurance-held model is a **disclosed simplification**: ceded premium, ceding
commission, recoverable-on-LIC, recoveries-on-paid and the **loss-recovery component** are modelled; a
full held-PAA/GMM roll-forward that allocates ceded premium to reinsurance service expense over the
coverage period (so a fully expired, settled treaty runs to zero) is **roadmap**. It does not affect the
gross loss-component hero (that's the direct book). Flagged in the Learn "Coverage & Roadmap" panel.

### Q4 (practitioner, Beat 4) — "GMM revenue doesn't show acquisition recovery as a component."
Acquisition amortisation **is** in the numbers — it posts through the subledger (account 5020) to
insurance service expense (§108) and the P&L foots. What the current `gld_revenue_gmm` decomposition
does not yet do is surface acquisition-cost **recovery** as an explicit line inside insurance revenue;
that presentation line is **roadmap**. It's a disclosure-granularity gap, not a missing amount.

### Q5 (practitioner, Beat 3) — "Your rate what-if looked wrong."
Fixed: the live sensitivity now signs cash flows by type (premiums as inflows, claims/expense as
outflows) before discounting, so the LRC/LIC PV moves in the right direction. The demo point stands and
is the expert one: **the CSM does not move** — accretion is locked at the cohort-inception curve. Full RA
re-measurement and the P&L/OCI disaggregation are computed by the engine on a close run, not live in the
what-if (disclosed in the panel note).

### Q6 (practitioner/platform, Beat 4) — "You only reconcile cash accounts. Where's the CSM/LRC tie-out to the GL?"
By design the **technical IFRS 17 accounts (CSM, LRC, LIC, LC) are subledger-owned** — the workbench is
the system of record for those measurement outputs, and the GL carries the financial-statement accounts
(cash, expenses, equity). The trial-balance recon ties the two where they intersect. In a production
control environment you'd add a GL summarisation run that posts the subledger balances into dedicated GL
accounts (e.g. an LRC/CSM control account) with break controls — that's a day-2 integration, not a gap in
the measurement. (Noted as roadmap.)

### Q7 (executive/practitioner, all beats) — "It's synthetic. Prove these numbers against our SAS output."
Out of scope for this demo, and we say so plainly. This shows the **pattern** — governed feeds → a gate
→ deterministic engines → subledger → statements → reproducible evidence — with real EIOPA curves and
byte-stable heroes. Proving equivalence to your SAS close is a **parallel-run migration workstream** (keep
SAS running, rebuild the rules as transparent code, reconcile to the penny, cut over). The platform's
reproduce/recon machinery is what that parallel run leans on; the equivalence proof itself is project work
on your data.

### Q8 (executive, Beat 4) — "Is the downloadable board pack the same as what's on screen?"
The pack is regenerated at the end of the close chain and bound to the run, so it matches the on-screen
figures for the current close. (Before this fix it could lag a rerun.)

### Q9 (practitioner) — scope: VFA, transition, IFRS 9, tax, multi-GAAP/consolidation?
Deliberately **out of scope** and labelled in the Learn "Coverage & Roadmap" panel. This is a P&C-weighted
PAA/GMM close; the platform is not limited to it, but this demo does not claim those.

### Q10 (platform, Beat 1) — "Why is the close dated 30 June 2026 and not today?"
IFRS 17 closes are **calendar-anchored** — a Q2 2026 close is as at 30 June 2026 by definition. Unlike the
sibling workbenches, reset here deliberately does **not** roll the reporting date forward; it re-produces
the same anchored close deterministically. (Documented exception in `DECISIONS.md`.)
