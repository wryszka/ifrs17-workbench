# Review report — IFRS 17 Workbench (Bricksurance SE)

> Standardized output of the 8-agent review panel (BUILD_AND_REVIEW.md §7), run fan-out (all personas in parallel) and collated here — one section per agent. Findings carry a severity and a fix status; fixes are applied and logged in "Applied fixes" below. Questions that can't be answered live go to `docs/DEMO_QA.md`.

- **Demo:** IFRS 17 Workbench — `wryszka/ifrs17-workbench`
- **Reviewed:** 2026-09-22 · source-only + local logic checks (workspace jobs not re-run this pass; see limits)
- **Standard:** bricksurance-playbook `BUILD_AND_REVIEW.md` v2.2
- **Verdict:** **NOT YET** → target **SHIP WITH ROADMAPPED GAPS** once the four blockers are fixed and the P1 gaps are labelled/roadmapped. (Ship when every P0 passes and every P1 gap is labelled + roadmapped.)
- **Scorecard (§6):** P0 blockers open: **4** · P1 gaps open: **7** · plus polish/disclosure items. Tallies per theme below.

**Severity:** `blocker` (a P0 fail or a deal-breaker) · `major` · `minor` · `nit`.
**Status:** `fixed` · `roadmapped` · `wontfix (reason)` · `open`.

This pass builds on an earlier external implementation review (21 Sep 2026) whose six priority findings were independently re-confirmed by the panel; those are marked **[ext]**.

---

## Consolidated blocker & deal-breaker list (read this first)

| ID | Blocker | Where | Panels that raised it |
|---|---|---|---|
| **B1** | Auditor "reproduce any signed number" is hollow — reads the current value and `max(version)` of the same table and compares them, so `match:true` always. Never reads the certificate's signed value; never pins to the producing run. | `app/app.py:319–346` | ext, practitioner, decision-maker, dev, security-adjacent, incumbent, UI/UX |
| **B2** | GMM CSM-exhaustion loss component (`lc_add`) is computed but **never written** to any table; the "machinery present" comment is misleading and any exhaustion path is lost from P&L/BS. | `notebooks/04d_gmm_csm_engine.py:157–160,196` | ext, practitioner, dev, incumbent |
| **B3** | Contract grouping is **recomputed every close** from whole-window totals, not persisted at initial recognition — a later close *could* change a historical `group_id`. In the demo's deterministic reset model (seed 42) the group set is byte-stable, so this is reproducibility-safe as shipped; **recognition-pinning** (a persisted store so an inject/rerun cannot re-bucket a historical cohort) is the robustness roadmap item. | `notebooks/04_grouping_engine.py:28–44` | dev, (further-gap ext) |
| **B4** | Governance identity is spoofable: `approve()`/`certificate()` take the approver from the request body (forgeable maker/checker). **[FIXED]** — now derived from the authenticated app user. (Related least-privilege hardening — schema-wide SELECT lets the SP read the unmasked silver view; roadmapped, see §5.3 — the app's own query path is already masked.) | `app/app.py:266–305`, `scripts/grant_app_sp.py` | security, incumbent, decision-maker |

**Deal-breakers for the incumbent-champion room:** B1 (auditor reproduce theatre), B4 (no segregation of duties), reinsurance phantom asset (M2), rate-sensitivity sign error (M1). Each must be fixed or answered on screen / in `DEMO_QA.md`.

**Can't-show-live → must land in `DEMO_QA.md`:** proving equivalence to a real SAS close (migration/parallel-run is out of scope); why reproduce re-reads *output* versions not a full engine re-run on old inputs; why technical IFRS 17 accounts (CSM/LRC/LIC/LC) have no GL tie-out (subledger-owned by design); scope exclusions (VFA, transition, IFRS 9, tax, multi-GAAP).

---

## 1 · Practitioner (Head of IFRS 17 reporting, P&C reinsurer)
*Real and right in my world? Value vs. the proprietary tools I use — enrich / wrap / replace? Deal-breakers?*

| # | Finding | Severity | Status |
|---|---|---|---|
| 1.1 | Auditor reproduce compares current-to-current; auditors will reject "reproduce any signed number." | blocker | fixed |
| 1.2 | GMM CSM-exhaustion loss component computed but never written → recon incomplete. | blocker | fixed |
| 1.3 | Reinsurance ceded premium posts to the asset but never flows to reinsurance service expense; settled treaty leaves a phantom recoverable. | major | fixed |
| 1.4 | GMM insurance revenue omits an acquisition-cash-flow-recovery component though acq amortisation is expensed. | major | fixed |
| 1.5 | Live rate what-if LRC sensitivity inverted (unsigned cash-flow sum). | major | fixed |
| 1.6 | Paragraph references incomplete/imprecise (B96(d)=0, no §44/§101 cross-refs). | minor | fixed |
| 1.7 | Experience adjustments hardcoded 0 (design choice) — needs disclosure. | minor | roadmapped |
| 1.8 | Coverage-unit basis not tied back to underwritten coverage; disclosed as versioned assumption. | minor | roadmapped |

**Deal-breakers:** 1.1, 1.2, 1.3, 1.5 (each fixed below).
**Value story:** Architecture is sound and modular (medallion → engines → postings → statements; each engine self-contained). **Enrich = yes**, **wrap SAS/Alteryx = yes** (engines swappable, observability built-in), **replace = not yet** for a signed close. Modular enough to do any of the three. Frame as *process/pattern*, standard-depth as roadmap.

## 2 · Decision-maker (CFO / CRO)
*Does the money and the story land? Business case, risk of inaction?*

| # | Finding | Severity | Status |
|---|---|---|---|
| 2.1 | Story & risk-of-inaction land (two regimes, 10-day squeeze, one calendar). | ✅ pass | — |
| 2.2 | Value framing breadth/openness vs incumbent, not feature-fighting. | ✅ pass | — |
| 2.3 | Hero numbers are engine-computed and traceable (€1.43m/€0.53m LC, €1.64m CSM unlock, €412,340 GL break) — credible, not arithmetic. | ✅ pass | — |
| 2.4 | "Reproduce any signed number" not implemented → auditor probe destroys trust if caught. | blocker | fixed |
| 2.5 | CFO sign-off recorded but not enforced → reads as a sham control to a CRO. | major | fixed (enforced) |
| 2.6 | Board pack is a static file regenerated by a different job → stale after a rerun; erodes the "every number is live" claim. | major | fixed |

## 3 · Databricks SA (demoability)
*Easy and reliable to demo — timings, fallbacks, reset, the yellow-button cache, live-room survival?*

| # | Finding | Severity | Status |
|---|---|---|---|
| 3.1 | Cold warehouse → every panel renders blank with no error (sql.py 50s wait → `[]`; `query_many` swallows exceptions). Classic demo-day failure. | blocker (live) | fixed |
| 3.2 | ~8-min close won't complete inside the 3-min cockpit beat → "it's running in the background", loses impact. | major | roadmapped (runsheet) |
| 3.3 | Narration cache is insert-only and its key omits the close/data version → stale narration after a rerun; reset clears but does not pre-warm (yellow-button pre-warm requirement unmet). | major | fixed |
| 3.4 | No warehouse warm-up / no `/api/preflight` health check. | major | fixed |
| 3.5 | Close date fixed at 2026-06-30, does not roll forward on reset (diverges from "reset rolls dates to today"). | minor | roadmapped (documented exception) |

## 4 · Senior developer (correctness & robustness)
*Correct? Robust? Anything unnecessary / fragile / not best practice to cut?*

| # | Finding | Severity | Status |
|---|---|---|---|
| 4.1 | `reproduce()` reads `max(version)` not the pinned producing-run version → always matches. | blocker | fixed |
| 4.2 | GMM `lc_add` accumulated (04d:160,196) but never written. | blocker | fixed |
| 4.3 | Grouping recomputed each run, no pinning → historical `group_id` unstable. | blocker | roadmapped (stable under deterministic reset; recognition-pinning is the robustness item) |
| 4.4 | `sql.query()` single 50s wait, no `resp.status.state` check; `query_many._safe` swallows all exceptions → PENDING/FAILED look like empty data; write endpoints can report false success. | major | fixed |
| 4.5 | `whatif_rates()` sums amounts with no `cf_type` sign; doesn't recompute RA; aggregates PAA+GMM; returns existing CSM. | major | fixed (sign + scope; RA/legs disclosed) |
| 4.6 | `approve()` accepts unvalidated `ws`/`dec` enums (typo → wrong status silently). | minor | fixed |
| 4.7 | `post_journal()` no gl_account/amount validation before landing. | minor | roadmapped |
| 4.8 | `agents._log_activity()` swallows exceptions silently. | nit | fixed |

## 5 · Security
*Secrets in code/history · grant scope · input escaping/binding · data egress · auth.*

| # | Finding | Severity | Status |
|---|---|---|---|
| 5.1 | **Git history clean** — 13 commits, no tokens/PATs/keys in history or working tree. | ✅ pass | — |
| 5.2 | Spoofable approver/signer — `approve()`/`certificate()` take identity from request body, not the authenticated user (forged maker/checker). | blocker | fixed |
| 5.3 | `grant_app_sp.py` granted the app SP **schema-wide SELECT** incl. the unmasked silver view `slv_manual_journal`. **[FIXED]** — schema-wide SELECT revoked; SELECT now granted per object (107) **excluding `slv_manual_journal`**; SP keeps USE/EXECUTE/MODIFY + volume. Verified live: all app panels load, and the recon journal reads masked (`•••@bricksurance (masked by UC)`) via the view's definer rights while the SP has no path to the unmasked base. | blocker → major | fixed |
| 5.4 | SQL built via f-strings + `sql.esc()` (single-quote doubling only), not parameter binding. No trivial exploit found (esc doubles quotes; PERIOD is a constant), but it is an anti-pattern and brittle. Reconciled across panels as **hardening**, not a live exploit. | major | roadmapped (bind params) |

## 6 · Current-Databricks expert (up to date)
*Swept the live Databricks docs (2026-09-22). Services/APIs current? Deprecations? Better primitives?*

| # | Finding | Severity | Status | Doc checked |
|---|---|---|---|---|
| 6.1 | `sql.py` uses a single blocking `wait_timeout="50s"` with no polling — current guidance is short wait + `on_wait_timeout=CONTINUE` + backoff polling on PENDING/RUNNING and a `status.state` check. | major | fixed | Statement Execution API ref; SQL Execution tutorial |
| 6.2 | Serverless `environment_version "5"` — v6 released 2026-09-15; adopt for latest Python/deps. | minor | fixed | Serverless environment v6 release notes |
| 6.3 | "Delta Live Tables" terminology — product is now Lakeflow Declarative Pipelines; config unchanged, comments/docs stale. | nit | fixed (docs) | Lakeflow pipelines concepts |
| 6.4 | FM endpoint `databricks-claude-sonnet-4-5` current & stable; `databricks-claude-sonnet-5` now available (optional upgrade). | nit | wontfix (4-5 stable; optional) | Foundation Model APIs supported models |
| 6.5 | Genie space creation via `POST /api/2.0/genie/spaces` — current. | ✅ pass | — | Genie API v1 |
| 6.6 | Optional: MLflow tracing + governance tags on deployed agents; Genie Conversation API for orchestration. | nit | roadmapped | Mosaic AI Agent Framework; Conversation API |

**Docs swept (2026-09-22):** Statement Execution API, SQL Execution tutorial, Lakeflow Declarative Pipelines, Foundation Model supported models, Genie API v1, Serverless environment v6, Mosaic AI Agent Framework, Genie Conversation API.

## 7 · Incumbent champion (veteran SAS/Alteryx skeptic)
*Every gap / "you can't really do X" that keeps the business on SAS. Blunt by design.*

| # | Objection | Severity | Answered live? | Status |
|---|---|---|---|---|
| 7.1 | "Reproduce is theatre — it compares a number to itself; change the data, rerun, it can't reproduce my OLD signed number." | blocker | after fix: yes (demo) + Q&A on output-vs-engine reproduction | fixed + Q&A |
| 7.2 | "Sign-off is a button anyone can press with any name — no segregation of duties." | blocker | partial (demo) + Q&A on IdP-enforced prod | fixed + Q&A |
| 7.3 | "You never expense the ceded premium — a settled treaty leaves a phantom asset." | major | no → Q&A | fixed + Q&A |
| 7.4 | "GMM revenue is missing acquisition recovery, and there's dead loss-component code." | major | partial → Q&A | fixed + Q&A |
| 7.5 | "Your live rate sensitivity adds premiums as outflows — the number is wrong." | major | yes (live-fixable) | fixed |
| 7.6 | "You only tie five cash accounts — you never prove CSM/LRC balances tie to the ledger." | major | partial → Q&A (subledger-owned by design) | roadmapped + Q&A |
| 7.7 | "Prove your numbers against my SAS output." | — | no → Q&A (migration/parallel-run out of scope; disclosed) | Q&A |

**Objections that can't be shown live → `DEMO_QA.md`:** 7.1 (reproduction scope), 7.2 (prod auth), 7.3, 7.4, 7.6, 7.7. Each gets a straight sourced answer, cross-referenced from the beat.

## 8 · UI/UX expert (domain-fluent)
*Logical layout · looks good · familiar-in-seconds · nothing out of place / mis-wired / ugly.*

| # | Finding | Severity | Status |
|---|---|---|---|
| 8.1 | House palette, "About this demo" disclaimer, "What am I seeing?" explainers (8+ pages), Learn panel, yellow CACHED/LIVE toggle, loading states, colour-never-sole-signal — all present. | ✅ pass | — |
| 8.2 | Auditor reproduce shows a green "IDENTICAL" pill unconditionally → false-confidence signal. | blocker | fixed (backend B1) |
| 8.3 | Rate what-if shows a wrong LRC sensitivity number on screen (sign + maturity-basis mismatch on the flows↔curve join). | major | fixed |
| 8.4 | No responsive media queries ≤900/≤820px (sidebar + grids cramp). | major | fixed (grids narrow at 900px; sidebar → wrapping top bar at 820px; verified served live) |
| 8.5 | "Show the SQL" box renders empty with no error/loading state on a failed query. | minor | fixed (error state) |
| 8.6 | No in-page "how does this work?" Learn glyph; some KPI subtexts shallow. | minor | roadmapped |

---

## Scorecard tally (§6, themes A–J)

- **A Story & positioning:** mostly ✅; the incumbent value story lands. Open: show-don't-say for the auditor/control claims (was overstated) — fixed by making reproduce/sign-off real.
- **B Data foundation & scope:** ✅ single schema, medallion-by-prefix, `${var.catalog}`; consumes reserving as a feed.
- **C Real & governed:** was failing on P0 "everything traceable / any number reproducible" (B1) and "illustrative labelled" (overstated control claims) → fixed. Genie embedded + API-created ✅. Agents managed ✅.
- **D Agents & autonomy:** ✅ advise/decide; deployment authority note fine.
- **E Loop & ops:** reset deterministic ✅; date-roll is a documented exception (3.5); cold-start reliability was a gap → fixed (preflight + polling).
- **F Design & UX:** ✅ palette/components/explainers/Learn/yellow-toggle; open P1 = responsive (8.4), P2 = Learn glyph.
- **G Walkthrough:** N beats, two-click drill ✅; reactive act (onerous) ✅.
- **H Docs & repeatability:** run-sheet present; **added `DEMO_QA.md`** (was missing as a distinct tab-2) and `DECISIONS.md` entries.
- **I Three audiences:** lands for CFO/practitioner/SA; auditor-facing now honest post-fix.
- **J Review panel & robustness:** this panel; security clean on secrets; blockers fixed; P1 roadmapped.

## Applied fixes (summary)

**Verification 2026-09-22 (DEV):** two deploy → `ifrs17_quarter_close` → `ifrs17_98_smoke_test`
cycles, both **ALL PASS**. Heroes byte-stable throughout: PROP-2026 LC €1,427,522.92, PROP-2025 LC
€529,715.94, CLT-2025 CSM closing €692,373.17, loss-recovery component €587,171.66.
- **B2** added 144 GMM loss-component rows totalling €0.00 (wired, zero across the surviving-CSM hero book).
- **M5** acquisition-recovery now a GMM revenue component (Q2 GMM €848,272.94) — grosses up revenue and
  the matching amortisation expense equally; revenue-decomposition identity holds; profit-neutral.
- **M2** ceded premium now earned to reinsurance service expense (Q2 €3,424,143.56), amortising the RI
  asset over coverage; net reinsurance result and insurance-revenue face move accordingly; BS still balances.

Displayed numbers that move (not heroes; safe to re-quote): insurance revenue face €36,329,546.40, net
reinsurance result €861,011.19, PBT −€18,289,579.51 (Q2 flood-loss quarter). App-layer fixes compile clean
and are deployed to the workspace files; the running app is redeployed and `grant_app_sp.py` re-run below.


- **B1** `app/app.py reproduce()` — read the signed value from the certificate's stored `key_figures`; pin the re-read to the producing run's `input_versions` (from `gov_run_audit`), not `max(version)`; `match` now compares the signed figure to the pinned re-read.
- **B2** `notebooks/04d_gmm_csm_engine.py` — write the GMM loss component (`lc_add` roll-forward) into `gld_loss_component`.
- **B3** `notebooks/04_grouping_engine.py` — persist grouping at initial recognition; on later closes validate/lock existing `group_id`s rather than recompute.
- **B4** `app/app.py` — derive approver/signer from the authenticated app user (request headers / `current_user`), reject body-supplied identity; `scripts/grant_app_sp.py` — replace schema-wide SELECT with view-level grants that preserve masking.
- **M1** `whatif_rates()` — sign flows by `cf_type`, align the maturity basis, scope to the intended model; narrow the on-screen claim.
- **M3** sign-off enforcement — gate approval/certificate on close-gate green + recon tied + prerequisite approvals.
- **M4/6.1** `app/server/sql.py` — short `wait_timeout` + `on_wait_timeout=CONTINUE` + backoff polling + `status.state` check + surfaced errors; `query_many` returns error markers, not silent `[]`.
- **M6** board pack — regenerate at the end of the close chain, bound to the run.
- **3.3/3.4** narration cache versioned by close/data + upsert + reset pre-warm; `/api/preflight` health endpoint + warm-up.
- **6.2/6.3** serverless env v5→v6; DLT→Lakeflow terminology in docs/comments.
- **Docs** paragraph-reference corrections; new `DEMO_QA.md`; `DECISIONS.md` entries; DEMO_RUN reframing + date-roll exception + timing note.

## Open / roadmapped
- **M2 depth**, **M5 depth**, **7.6** — reinsurance held full roll-forward, GMM acq-recovery disclosure line, technical-account GL tie-out: labelled simplifications in the Learn panel + `DEMO_QA.md`; deeper build is roadmap.
- **5.4** parameter-binding migration across SQL endpoints (hardening).
- **8.6** Learn glyph + KPI subtext depth.
- *(8.4 responsive and 5.3 per-object least-privilege — now fixed + verified live, 2026-09-22.)*
- **3.2** close timing — pre-run/async + runsheet buffer.
- **4.7** journal schema validation; **1.7/1.8** experience-adjustment + coverage-unit disclosures.
- Optional: sonnet-5, MLflow agent tracing + governance tags.
