# CLAUDE.md — IFRS 17 Workbench (Bricksurance SE)

A **synthetic demo**: the quarterly IFRS 17 close for the fictional P&C-weighted insurer
**Bricksurance SE**, run end-to-end on Databricks. Publicly publishable, not client-specific.
Audience: CFO / CRO / Head of IFRS 17 Reporting, demoed by non-insurance-technical SAs/AEs.

Mirrors the Bricksurance workbench family — `underwriting-workbench` (framework template),
`reinsurance_workbench` (conventions origin), `claims_workbench` (ingestion/agents),
`lifecast` (close chain + run-audit + EIOPA patterns), `solvency-ii-qrt-demo-pnc`
(approvals/certificate). **Mirror, don't invent.**

## Naming — hard rule

- Schema: always `ifrs17_workbench` (single schema; medallion via table prefixes, not schemas).
- Tables: `brz_` / `slv_` / `gld_`; reference `ref_`; governance `gov_`; cache `cache_`.
- Volume: `${catalog}.ifrs17_workbench.ifrs17_files`.
- Jobs/pipelines: `ifrs17_` prefix. UC functions: `fn_*`. Models: `model_*`, alias `champion`.
- App: `ifrs17-workbench` (hyphen exception — apps need URL-safe names).
- Underscores only in UC identifiers. Sentence case in all UI copy. **No "WOW" branding.**

## Portability — hard rule

- **One edit to move workspaces: the `catalog` bundle variable** (default `lr_dev_aws_us_catalog`).
- No hardcoded workspace URLs/IDs. `${var.catalog}` → job/pipeline params → widgets →
  `FQ = f"{catalog}.{schema}"`. App reads env vars only (`app.yaml`).
- All serverless: DLT `serverless: true`; jobs pin `environment_version "5"` via job-level
  `environments` + `environment_key` per task. No clusters. Scale-to-zero everywhere.

## Engine determinism — hard rule

- Group counts are tiny (~40–60): **all measurement math runs in driver-side pandas**, `round(2)`,
  deterministic sort before every write. Heroes are byte-identical after `99_reset` (seed 42).
- **CSM/roll-forward math never lives in DLT.** DLT (brz/slv + mart MVs) and the batch engines
  write **disjoint table sets**.
- The 8 reconciliation identities in `98_smoke_test` are the QA spec — all must PASS before
  anyone demos: CSM tie-out; §100–103 roll-forward → balance sheet; revenue decomposition;
  LIC roll-forward; BS = Σ(LRC+LIC); subledger→GL zero diff; IFIE P&L/OCI split + OCI roll;
  LC & RA roll-forwards tie to P&L.

## Measurement scope (what the engines actually do)

- PAA: Motor / Property / Liability (annual coverage). LRC roll-forward; LIC **discounted + RA +
  unwind**; §57 facts-and-circumstances onerous test (GMM-style FCF inside the PAA book) → loss
  component. **No CSM on PAA cohorts — ever.**
- GMM: Commercial long-tail + Construction decennial. B96-ordered CSM roll-forward (opening →
  new business → locked-in accretion → experience adj (current service → P&L) → future-service
  FCF changes → FX → **release last** via coverage units). Legacy run-off book = LIC-only (no CSM).
- Discounting: real EIOPA curves (bundled `data/eiopa/`, provenance in `data/eiopa/PROVENANCE.md`)
  + illiquidity premium bps per portfolio (versioned assumption). Locked-in per cohort inception
  year-end; current at reporting date. OCI disaggregation option for GMM.
- Reinsurance held: property quota share with **loss-recovery component** (simplified, disclosed).
- Insurance revenue is never written premium; the P&L face is the §80 shape. Groups are fixed at
  initial recognition and never re-bucketed.
- Disclosed simplifications live in the Learn page "Coverage & Roadmap" panel. OUT of scope
  (roadmap, acknowledged in-app): VFA, transition, multi-GAAP, tax, IFRS 9.

## The app — hard rule

- Thin FastAPI + **self-contained `app/dist/index.html`** (vanilla JS, hash routing, no npm).
- Theme: Bricksurance design system verbatim (slate #1e293b/#0f172a sidebar, blue #2563eb/#60a5fa,
  white 12px cards, RAG pills, violet narration, amber About-this-demo, green Learn tile,
  amber CACHED / emerald LIVE toggle).
- Every panel calls a real UC fn / table / endpoint / Genie. **No business logic in the app.**
- **LLMs narrate, SQL decides.** Cache wraps narration only (sha256 → `cache_agent_responses`).
- Interactive pages never hit scale-to-zero ML endpoints (batch-score into gold).

## Gotchas (inherited, verified across siblings)

- `CREATE OR REPLACE FUNCTION` revokes EXECUTE → 06 creates fns once; reset never recreates.
- UC scalar fns must be provably one row (pre-aggregate + `named_struct`); COMMENT not tags.
- FM endpoint for ai_query: `databricks-claude-sonnet-4-5` (sonnet-5 fails).
- `agents.deploy` truncates endpoint names to 63 chars → resolve by substring.
- Table tags blocked by workspace tag policy → TBLPROPERTIES.
- Reset re-warm chunked under the Apps gateway timeout. Don't VACUUM (time travel = auditor mode).
- `databricks bundle deploy` may need unsandboxed Bash (keychain exit 45).
- Genie space ≤ ~30 tables.

## Everything must be real

Every number on screen is computed by a real engine run on the live workspace. No mocks of
platform behaviour, no hardcoded results in the app. Push to `wryszka/ifrs17-workbench`
(public) after each phase.
