# DEPLOY — IFRS 17 Workbench

Target: any UC-enabled workspace with serverless. **One edit to move: the `catalog` bundle
variable.** Default target `dev` = `fevm-lr-dev-aws-us` (profile `DEV`), catalog
`lr_dev_aws_us_catalog`, schema `ifrs17_workbench` (fixed).

## Fresh install (in order)

```bash
databricks bundle deploy -t dev

databricks bundle run ifrs17_00_setup -t dev            # truth model + landing files + real EIOPA curves
databricks bundle run ifrs17_medallion -t dev --full-refresh-all
databricks bundle run ifrs17_quarter_close -t dev       # gate → engines → postings → disclosures (~8 min)
databricks bundle run ifrs17_05_ml -t dev               # LIC emergence challenger (train + batch score)
databricks bundle run ifrs17_06_agents -t dev           # UC fn tools → role agents → supervisor → governance → board pack
databricks bundle run ifrs17_98_smoke_test -t dev       # ALL checks must PASS before demoing
```

Then create the Genie space + Lakeview dashboard (scripts/, P7), capture their IDs into
`databricks.yml` target vars + `app/app.yaml`, redeploy, and start the app:

```bash
databricks apps deploy ifrs17-workbench --source-code-path <workspace app path>   # or bundle-managed
databricks apps start ifrs17-workbench
```

## App service-principal grants (imperative, after first app create)

- Schema `ifrs17_workbench`: `USE SCHEMA, SELECT, EXECUTE, MODIFY` (journals/approvals writes)
- Catalog: `USE CATALOG`
- Warehouse: `CAN_USE` (bundle-bound resource)
- Serving endpoints (`ifrs17-*` + the `agents_…ifrs17_agent` supervisor): `CAN_QUERY`
- Jobs `ifrs17_quarter_close`, `ifrs17_demo_levers`, `ifrs17_99_reset`: `CAN_MANAGE_RUN`
- Genie space: `CAN_RUN` · Dashboard: `CAN_READ` (embed needs published-with-credentials)
- Volume `ifrs17_files`: `READ VOLUME, WRITE VOLUME` (packs, journal files)
- Deliberately NOT a member of `ifrs17_finance_controllers` — the UC masking demo depends on it.

## Asset inventory (what the smoke test verifies)

| Kind | Names |
|---|---|
| Jobs | `ifrs17_00_setup`, `ifrs17_quarter_close`, `ifrs17_05_ml`, `ifrs17_06_agents`, `ifrs17_demo_levers`, `ifrs17_98_smoke_test`, `ifrs17_99_reset` |
| Pipeline | `ifrs17_medallion` (+ event log `medallion_event_log`) |
| Volume | `ifrs17_files` (landing/, eiopa/, staging/, packs/, checkpoints/) |
| Tables | ~55 (`ref_*`, `brz_*`, `slv_*`, `gld_*`, `gov_*`, `cache_*`, `smoke_results`) |
| UC functions | 11 × `fn_*` (agent tools — created once; reset never recreates) |
| Models | `model_lic_emergence@champion`, `model_ifrs17_agent`, `ifrs17_agent` |
| Endpoints | `ifrs17-movement/-disclosure/-evidence/-brief` + `agents_…ifrs17_agent` (63-char truncation — resolve by substring) |
| App | `ifrs17-workbench` |

## Known gotchas

All inherited gotchas are honoured in the code (see CLAUDE.md): `CREATE OR REPLACE FUNCTION`
revokes EXECUTE (rerunning `ifrs17_06_agents` requires re-granting + supervisor redeploy);
ai_query FM must be `databricks-claude-sonnet-4-5`; DLT-owned tables take no DML (the close
gate checks quarantined FILES still in landing, so the restore lever is just a file delete);
don't VACUUM (auditor mode = time travel); Genie ≤ ~30 tables.
