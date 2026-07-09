# IFRS 17 Workbench — Bricksurance SE

The quarterly IFRS 17 close, end-to-end on Databricks: multi-source ingestion → governed
medallion with DQ gates → real PAA/GMM measurement engines (CSM roll-forward, §57 onerous
test, loss component, risk adjustment, real EIOPA discount curves) → subledger postings
reconciled to GL → §80 statements and §100–103 disclosures → approvals + sign-off certificate —
with an auditable "reproduce this number as-at" trail and AI agents that narrate (never decide).

**About this demo** — Bricksurance SE is a fictional insurer; every policy, claim and balance is
synthetic. The EIOPA risk-free curves are real publications (`data/eiopa/PROVENANCE.md`). The
measurement configuration is illustrative — the platform is methodology-agnostic; nothing here
is accounting or actuarial advice.

## Install (one edit)

```bash
databricks bundle deploy -t dev            # workspace fevm-lr-dev-aws-us, profile DEV
# elsewhere: databricks bundle deploy -t dev --var catalog=<your_catalog>
```

Then run jobs in order: `ifrs17_00_setup` → DLT `ifrs17_medallion` → `ifrs17_quarter_close`
(details in docs/DEPLOY.md once built).

Status: **under construction** — build phases tracked in the repo history.
