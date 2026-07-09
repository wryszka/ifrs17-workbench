# Databricks notebook source
# MAGIC %md
# MAGIC # 00 · Setup + synthetic truth model — Bricksurance SE IFRS 17
# MAGIC
# MAGIC Deterministic (seed=42) synthetic universe for the IFRS 17 Workbench, anchored to the
# MAGIC **Q2 2026 close** (reporting date 2026-06-30 — matching the bundled real EIOPA curve).
# MAGIC
# MAGIC Design: the generator first fixes **group-level quarterly truth** (GWP, loss ratios,
# MAGIC writing patterns, the June 2026 flood event, the hero outcomes), then `00b_landing_files`
# MAGIC scatters that truth into policy/claim/transaction/projection **source-system files** whose
# MAGIC sums tie back exactly (largest-remainder allocation in cents). The measurement engines see
# MAGIC only the landed files — everything downstream is computed for real, yet the hero numbers
# MAGIC are byte-stable on every reset.
# MAGIC
# MAGIC Writes: schema, volumes, `ref_*` reference tables, `gov_assumption_registry` seed,
# MAGIC `gen_*` internal truth tables (inputs to 00b — not demo surfaces).

# COMMAND ----------

dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "ifrs17_workbench")
dbutils.widgets.text("seed", "42")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
SEED = int(dbutils.widgets.get("seed"))
FQ = f"{catalog}.{schema}"

import datetime
import random

random.seed(SEED)

REPORTING_DATE = datetime.date(2026, 6, 30)          # Q2 2026 close — matches EIOPA_RFR_20260630
QUARTERS = [(y, q) for y in (2024, 2025, 2026) for q in (1, 2, 3, 4)][:10]  # 2024Q1..2026Q2
CLOSE_PERIOD = "2026Q2"

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {FQ}")
for vol in ("ifrs17_files",):
    spark.sql(f"CREATE VOLUME IF NOT EXISTS {FQ}.{vol}")
VOL = f"/Volumes/{catalog}/{schema}/ifrs17_files"
for sub in ("landing/policy_admin", "landing/claims", "landing/claim_transactions",
            "landing/actuarial_projections", "landing/gl_trial_balance", "landing/reinsurance",
            "landing/fx_rates", "landing/expense_allocation", "eiopa", "staging",
            "packs", "checkpoints"):
    dbutils.fs.mkdirs(f"{VOL}/{sub}")

print(f"target = {FQ}  seed={SEED}  reporting_date={REPORTING_DATE}")


def write(rows, name, ddl, layer, comment=""):
    df = spark.createDataFrame(rows, ddl)
    df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{FQ}.{name}")
    spark.sql(f"ALTER TABLE {FQ}.{name} SET TBLPROPERTIES ('layer'='{layer}', 'demo'='ifrs17_workbench')")
    if comment:
        spark.sql(f"COMMENT ON TABLE {FQ}.{name} IS '{comment}'")
    print(f"  {name}: {df.count()} rows")

# COMMAND ----------

# MAGIC %md ## Portfolios — the Bricksurance SE P&C book
# MAGIC PAA for annual-coverage lines; GMM for multi-year lines (fail PAA eligibility);
# MAGIC the legacy book is in run-off = LIC only (no remaining coverage, no CSM).

# COMMAND ----------

# portfolio_id, name, measurement_model, coverage_months, settlement, currency,
# coverage_unit_basis, ilp_bps (illiquidity premium — a versioned assumption, also in the registry)
PORTFOLIOS = [
    ("MOT",  "Motor Retail",                 "PAA",      12,  "short",  "EUR", "vehicle_years",        50),
    ("PROP", "Property Homeowners & SME",    "PAA",      12,  "short",  "EUR", "sum_insured_years",    50),
    ("LIAB", "General Liability SME",        "PAA",      12,  "long",   "EUR", "policy_years",         60),
    ("CLT",  "Commercial Engineering & Casualty (3y)", "GMM", 36, "long", "EUR", "sum_insured_coverage", 75),
    ("DEC",  "Construction Decennial (10y)", "GMM",     120,  "long",   "EUR", "sum_insured_coverage", 85),
    ("RO",   "Legacy Commercial (run-off)",  "LIC_ONLY",  0,  "long",   "EUR", "n_a",                  60),
]
write([(p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7]) for p in PORTFOLIOS],
      "ref_portfolio",
      "portfolio_id string, portfolio_name string, measurement_model string, coverage_months int, "
      "settlement_profile string, currency string, coverage_unit_basis string, illiquidity_premium_bps int",
      "reference",
      "IFRS 17 portfolios (contracts subject to similar risks, managed together). Measurement model "
      "per portfolio: PAA (annual coverage), GMM (multi-year), LIC_ONLY (run-off — no remaining coverage). "
      "Illiquidity premium bps = versioned assumption, see gov_assumption_registry.")

# COMMAND ----------

# MAGIC %md ## Cohort truth — GWP, ratios, writing patterns, the hero design
# MAGIC Annual cohorts per §22. Profitability buckets fixed at initial recognition (never re-bucketed):
# MAGIC motor = no-significant-possibility (thin margin), everything else = remaining.
# MAGIC The **June 2026 Central European floods** (synthetic event) drive hero 2: the actuarial
# MAGIC assumption `flood_freq_property` moves v1→v2 at the Q2 2026 run, pushing the forward
# MAGIC property loss ratio to 78% — the §57 onerous test then bites on the 2025 + 2026 cohorts.

# COMMAND ----------

# (portfolio, cohort_year) -> gwp_eur, policy_count, acq_ratio, attr_expense_ratio,
#   lr_v1 (pricing/plan loss ratio), lr_v2 (post-flood-assumption forward LR, PROP only),
#   writing_pattern (share of GWP written per quarter of the cohort year)
COHORTS = {
    # Combined ratios at initial recognition (lr1 + exp + acq): MOT ≈ 98.5% (thin), PROP ≈ 90-92%,
    # LIAB ≈ 94%, CLT ≈ 85%, DEC ≈ 81%. §16 buckets follow: comfortably profitable (CR ≤ 88%) =
    # "no significant possibility of becoming onerous" (NSP); the rest = "remaining" (REM).
    ("MOT", 2024): dict(gwp=52_000_000, n=11800, acq=0.10, exp=0.165, lr1=0.71, lr2=None, wp=[.26, .25, .24, .25]),
    ("MOT", 2025): dict(gwp=55_500_000, n=12300, acq=0.10, exp=0.165, lr1=0.72, lr2=None, wp=[.26, .25, .24, .25]),
    ("MOT", 2026): dict(gwp=29_000_000, n=6300,  acq=0.10, exp=0.165, lr1=0.72, lr2=None, wp=[.52, .48, 0, 0]),
    # PROP 2025 deliberately H2-weighted (growth push) so material coverage remains at 2026-06-30.
    ("PROP", 2024): dict(gwp=34_000_000, n=8600, acq=0.14, exp=0.16, lr1=0.60, lr2=None, wp=[.25, .25, .25, .25]),
    ("PROP", 2025): dict(gwp=38_500_000, n=9400, acq=0.14, exp=0.16, lr1=0.61, lr2=0.78, wp=[.16, .18, .30, .36]),
    ("PROP", 2026): dict(gwp=21_500_000, n=5100, acq=0.14, exp=0.16, lr1=0.62, lr2=0.78, wp=[.50, .50, 0, 0]),
    ("LIAB", 2024): dict(gwp=15_500_000, n=2600, acq=0.14, exp=0.17, lr1=0.63, lr2=None, wp=[.28, .24, .24, .24]),
    ("LIAB", 2025): dict(gwp=16_200_000, n=2700, acq=0.14, exp=0.17, lr1=0.63, lr2=None, wp=[.28, .24, .24, .24]),
    ("LIAB", 2026): dict(gwp=8_400_000,  n=1350, acq=0.14, exp=0.17, lr1=0.64, lr2=None, wp=[.52, .48, 0, 0]),
    # GMM lines: multi-year, premium in annual installments (contract boundary includes them).
    ("CLT", 2024): dict(gwp=27_000_000, n=310, acq=0.12, exp=0.15, lr1=0.58, lr2=None, wp=[.30, .24, .22, .24]),
    ("CLT", 2025): dict(gwp=30_000_000, n=340, acq=0.12, exp=0.15, lr1=0.59, lr2=0.67, wp=[.30, .24, .22, .24]),
    ("CLT", 2026): dict(gwp=16_000_000, n=175, acq=0.12, exp=0.15, lr1=0.59, lr2=None, wp=[.55, .45, 0, 0]),
    ("DEC", 2024): dict(gwp=18_000_000, n=210, acq=0.13, exp=0.16, lr1=0.52, lr2=None, wp=[.25, .25, .25, .25]),
    ("DEC", 2025): dict(gwp=19_500_000, n=225, acq=0.13, exp=0.16, lr1=0.52, lr2=None, wp=[.25, .25, .25, .25]),
    ("DEC", 2026): dict(gwp=10_200_000, n=115, acq=0.13, exp=0.16, lr1=0.53, lr2=None, wp=[.52, .48, 0, 0]),
}
# NOTE on CLT 2025 lr2: a +8% future-service deterioration lands at the Q2 2026 reserving run
# (casualty inflation) — enough to UNLOCK the CSM visibly without exhausting it (scripted moment B).

# §16 profitability buckets at initial recognition (fixed forever): NSP = no significant
# possibility of becoming onerous (comfortably profitable); REM = remaining. Thin-margin motor
# is REM — it COULD become onerous; that is the correct reading of the standard.
BUCKET = {"MOT": "REM", "PROP": "REM", "LIAB": "REM", "CLT": "NSP", "DEC": "NSP"}

rows = []
for (port, yr), c in sorted(COHORTS.items()):
    rows.append((port, yr, f"{port}-{yr}-{BUCKET[port]}", BUCKET[port], float(c["gwp"]), c["n"],
                 c["acq"], c["exp"], c["lr1"], c["lr2"] if c["lr2"] else c["lr1"],
                 c["wp"][0], c["wp"][1], c["wp"][2], c["wp"][3]))
write(rows, "gen_truth_cohort",
      "portfolio_id string, cohort_year int, group_id string, profitability_bucket string, gwp double, "
      "policy_count int, acq_ratio double, expense_ratio double, lr_v1 double, lr_v2 double, "
      "wq1 double, wq2 double, wq3 double, wq4 double",
      "internal", "Generator truth (internal): cohort-level targets that 00b scatters into source files.")

# Run-off book: accident-era LIC groups (no LRC). Ultimate + paid-to-date fixed by truth.
RO_ERAS = [  # accident_year, ultimate, share already paid at 2023-12-31 (engine start)
    (2019, 21_000_000, 0.78), (2020, 24_500_000, 0.66), (2021, 19_800_000, 0.55), (2022, 17_200_000, 0.41),
]
write([(f"RO-{y}-LIC", "RO", y, float(u), sp) for y, u, sp in RO_ERAS],
      "gen_truth_runoff",
      "group_id string, portfolio_id string, accident_year int, ultimate double, paid_share_at_start double",
      "internal", "Generator truth (internal): run-off accident-era ultimates for the LIC-only exhibit.")

# COMMAND ----------

# MAGIC %md ## Accident-quarter truth — attritional + large + the flood event
# MAGIC Ultimates per (portfolio, accident quarter) that claims files and reserving projections
# MAGIC both derive from. Payment development patterns per line (quarterly shares).

# COMMAND ----------

PAY_PATTERN = {  # development pattern: share of ultimate paid in dev quarter 0,1,2,...
    "MOT":  [0.42, 0.26, 0.13, 0.08, 0.05, 0.03, 0.02, 0.01],
    "PROP": [0.52, 0.30, 0.10, 0.05, 0.03],
    "LIAB": [0.08, 0.14, 0.15, 0.14, 0.12, 0.10, 0.08, 0.03, 0.03, 0.03, 0.03, 0.02, 0.02, 0.02, 0.006, 0.004],
    "CLT":  [0.10, 0.15, 0.15, 0.13, 0.11, 0.09, 0.07, 0.03, 0.03, 0.03, 0.03, 0.02, 0.02, 0.02, 0.006, 0.004],
    "DEC":  [0.05, 0.08, 0.10, 0.11, 0.11, 0.10, 0.09, 0.08, 0.07, 0.06, 0.05, 0.04, 0.03, 0.02, 0.005, 0.005],
    "RO":   [0.06, 0.10, 0.12, 0.12, 0.11, 0.10, 0.09, 0.08, 0.07, 0.05, 0.04, 0.03, 0.015, 0.015, 0.01, 0.01],
}
write([(p, i, s) for p, pat in sorted(PAY_PATTERN.items()) for i, s in enumerate(pat)],
      "gen_pay_pattern", "portfolio_id string, dev_quarter int, paid_share double",
      "internal", "Generator truth (internal): claim payment development patterns by line.")

SEASON = {"MOT": [1.05, 0.95, 0.98, 1.02], "PROP": [1.10, 0.90, 0.95, 1.05],
          "LIAB": [1.0, 1.0, 1.0, 1.0], "CLT": [1.0, 1.0, 1.0, 1.0], "DEC": [1.0, 1.0, 1.0, 1.0]}

# The synthetic June 2026 flood event (property book only): gross incurred, on top of attritional.
FLOOD_EVENT = dict(portfolio="PROP", accident_quarter="2026Q2", gross_ultimate=16_800_000,
                   event_name="June 2026 Central European floods (synthetic event)")


def q_label(y, q):
    return f"{y}Q{q}"


def earned_in_quarter(port, cohort, ay, aq):
    """Premium earned in accident quarter (ay,aq) from a cohort written per wp — straight-line
    over coverage_months. Used only to shape attritional ultimates (truth), engines recompute
    earning bottom-up from policy dates."""
    cov_m = dict((p[0], p[3]) for p in PORTFOLIOS)[port]
    if cov_m == 0:
        return 0.0
    c = COHORTS[(port, cohort)]
    total = 0.0
    for wq in range(4):
        w = c["wp"][wq]
        if w == 0:
            continue
        # written mid-quarter: coverage from (cohort, wq, mid) for cov_m months
        start_m = (cohort - 2000) * 12 + wq * 3 + 1.5
        end_m = start_m + cov_m
        aq_start = (ay - 2000) * 12 + (aq - 1) * 3
        aq_end = aq_start + 3
        overlap = max(0.0, min(end_m, aq_end) - max(start_m, aq_start))
        total += c["gwp"] * w * overlap / cov_m
    return total


acc_rows = []
for port in ("MOT", "PROP", "LIAB", "CLT", "DEC"):
    for (ay, aq) in QUARTERS:
        earned = sum(earned_in_quarter(port, cy, ay, aq) for cy in (2024, 2025, 2026)
                     if (port, cy) in COHORTS)
        if earned <= 0:
            continue
        lr = COHORTS[(port, 2025)]["lr1"]  # attritional shaped on plan LR
        season = SEASON[port][aq - 1]
        attr = round(earned * lr * season, 2)
        flood = FLOOD_EVENT["gross_ultimate"] if (port == FLOOD_EVENT["portfolio"]
                                                  and q_label(ay, aq) == FLOOD_EVENT["accident_quarter"]) else 0.0
        acc_rows.append((port, q_label(ay, aq), round(earned, 2), attr, float(flood)))
write(acc_rows, "gen_truth_accident",
      "portfolio_id string, accident_quarter string, earned_premium double, attritional_ultimate double, "
      "event_ultimate double",
      "internal", "Generator truth (internal): accident-quarter ultimates (attritional + June 2026 flood event).")

# COMMAND ----------

# MAGIC %md ## Close calendar — IFRS 17 and Solvency II on the SAME Day 1–10 timetable
# MAGIC The head-of-reporting reality: both regimes compress into the same working days.

# COMMAND ----------

CAL = [
    (1, "Data feeds", "Policy admin, claims, GL extracts land", "Finance data ops", "IFRS17"),
    (2, "Data feeds", "Reinsurance, FX, expense allocation land", "Finance data ops", "IFRS17"),
    (3, "DQ & gates", "Expectations pass, quarantine clear, close gate opens", "Finance data ops", "IFRS17"),
    (3, "Actuarial projections", "Reserving run delivered (LIC + LRC scopes)", "Reserving", "IFRS17"),
    (4, "Actuarial projections", "Assumption changes approved and versioned", "Chief Actuary", "IFRS17"),
    (5, "Measurement", "PAA / GMM engines run — CSM, LC, RA, discounting", "Group reporting", "IFRS17"),
    (6, "Postings & recon", "Subledger posted, GL reconciled, journals approved", "Financial control", "IFRS17"),
    (7, "Disclosures", "Statements + §100–103 roll-forwards produced", "Group reporting", "IFRS17"),
    (7, "Solvency II", "TP feeds reuse the same FCF data (QRT S.19/S.25 prep)", "Capital reporting", "SII"),
    (8, "Review & AoC", "Analysis of change reviewed with actuarial", "Head of reporting", "IFRS17"),
    (9, "Sign-off", "CFO sign-off + certificate", "CFO", "IFRS17"),
    (10, "Group submission", "Board pack + group consolidation submission; SII QRTs filed", "Group reporting", "BOTH"),
]
write([(CLOSE_PERIOD, d, ws, task, owner, regime) for d, ws, task, owner, regime in CAL],
      "ref_close_calendar",
      "close_period string, working_day int, workstream string, task string, owner_role string, regime string",
      "reference", "The Day 1-10 close timetable — IFRS 17 and Solvency II deliverables on one calendar.")

# COMMAND ----------

# MAGIC %md ## Chart-of-accounts mapping — every roll-forward step posts balanced Dr/Cr

# COMMAND ----------

COA = [
    # posting_key, gl_account, gl_account_name, statement, disclosure_line
    ("premiums_received",      "1000", "Cash and equivalents",                 "BS",  "cash"),
    ("premiums_received_lrc",  "2100", "LRC excluding loss component",         "BS",  "lrc_excl_lc"),
    ("insurance_revenue",      "4000", "Insurance revenue",                    "PL",  "insurance_revenue"),
    ("revenue_lrc_release",    "2100", "LRC excluding loss component",         "BS",  "lrc_excl_lc"),
    ("claims_paid",            "1000", "Cash and equivalents",                 "BS",  "cash"),
    ("claims_paid_lic",        "2200", "Liability for incurred claims",        "BS",  "lic"),
    ("claims_incurred_ise",    "5000", "Insurance service expenses — incurred claims", "PL", "ise_incurred"),
    ("claims_incurred_lic",    "2200", "Liability for incurred claims",        "BS",  "lic"),
    ("acq_cashflows_paid",     "1000", "Cash and equivalents",                 "BS",  "cash"),
    ("acq_cashflows_lrc",      "2100", "LRC excluding loss component",         "BS",  "lrc_excl_lc"),
    ("acq_amortisation_ise",   "5020", "Insurance service expenses — acquisition amortisation", "PL", "ise_acq"),
    ("acq_amortisation_lrc",   "2100", "LRC excluding loss component",         "BS",  "lrc_excl_lc"),
    ("loss_component_ise",     "5010", "Insurance service expenses — loss component", "PL", "ise_lc"),
    ("loss_component_lrc",     "2110", "Loss component of the LRC",            "BS",  "loss_component"),
    ("ra_change_ise",          "5030", "Insurance service expenses — risk adjustment change", "PL", "ise_ra"),
    ("ra_change_lic",          "2200", "Liability for incurred claims",        "BS",  "lic"),
    ("ifie_unwind_pl",         "6000", "Insurance finance expenses — P&L",     "PL",  "ifie_pl"),
    ("ifie_unwind_lic",        "2200", "Liability for incurred claims",        "BS",  "lic"),
    ("ifie_oci",               "3900", "Insurance finance reserve — OCI",      "BS",  "oci_reserve"),
    ("ifie_oci_lrc",           "2100", "LRC excluding loss component",         "BS",  "lrc_excl_lc"),
    ("ri_recovery_pl",         "5100", "Net expenses from reinsurance held",   "PL",  "ri_net"),
    ("ri_recovery_asset",      "2300", "Reinsurance contract held assets",     "BS",  "ri_asset"),
    ("ri_premium_paid",        "1000", "Cash and equivalents",                 "BS",  "cash"),
    ("ri_premium_asset",       "2300", "Reinsurance contract held assets",     "BS",  "ri_asset"),
    ("expenses_paid_cash",     "1000", "Cash and equivalents",                 "BS",  "cash"),
    ("expenses_attributable",  "5040", "Insurance service expenses — attributable expenses", "PL", "ise_expenses"),
    ("expenses_nonattributable", "7000", "Other operating expenses",           "PL",  "other_opex"),
    ("lic_brought_forward",      "3000", "Opening equity adjustment (window start)", "BS", "equity"),
]
write([(k, a, n, s, d) for k, a, n, s, d in COA], "ref_coa_mapping",
      "posting_key string, gl_account string, gl_account_name string, statement string, disclosure_line string",
      "reference", "Posting keys: every engine roll-forward step maps to balanced Dr/Cr GL accounts.")

# COMMAND ----------

# MAGIC %md ## Risk adjustment parameters + assumption registry (versioned, approved)
# MAGIC RA = confidence-level method at CL 75% (lognormal on PV outflows, CoV per line).
# MAGIC `flood_freq_property` v2 is the hero assumption: approved 2026-07-03, effective the
# MAGIC Q2 2026 reserving run — the drill lands on this exact record.

# COMMAND ----------

RA = [("MOT", 0.07), ("PROP", 0.11), ("LIAB", 0.16), ("CLT", 0.15), ("DEC", 0.19), ("RO", 0.14)]
write([(p, cov, 0.75, "confidence_level_lognormal") for p, cov in RA],
      "ref_ra_params", "portfolio_id string, cov double, confidence_level double, method string",
      "reference", "Risk adjustment: confidence-level method, CL 75%, lognormal quantile on PV outflows. Illustrative configuration.")

ASM = [
    # assumption_id, version, portfolio, value_json, effective_from_run, source, approved_by, approved_at, status
    ("flood_freq_property", 1, "PROP", '{"forward_loss_ratio": 0.62, "basis": "pricing 2025 flood freq 1-in-18yr"}',
     "2024Q1", "Reserving Q4 2023 deep dive", "K. Verhoeven (Chief Actuary)", "2023-12-15", "superseded"),
    ("flood_freq_property", 2, "PROP", '{"forward_loss_ratio": 0.78, "basis": "June 2026 flood experience; freq re-basis to 1-in-9yr"}',
     "2026Q2", "June 2026 event review + portfolio re-underwriting study", "K. Verhoeven (Chief Actuary)", "2026-07-03", "active"),
    ("casualty_inflation_clt", 1, "CLT", '{"future_claims_uplift": 0.00}',
     "2024Q1", "Pricing basis", "K. Verhoeven (Chief Actuary)", "2023-12-15", "superseded"),
    ("casualty_inflation_clt", 2, "CLT", '{"future_claims_uplift": 0.08, "cohorts": [2025]}',
     "2026Q2", "Social/casualty inflation study Q2 2026", "K. Verhoeven (Chief Actuary)", "2026-07-02", "active"),
    ("illiquidity_premium", 1, "ALL", '{"MOT": 50, "PROP": 50, "LIAB": 60, "CLT": 75, "DEC": 85, "RO": 60}',
     "2024Q1", "Group methodology paper IFRS17-DR-004", "Group CFO methodology committee", "2023-11-30", "active"),
    ("pricing_loss_ratio", 1, "ALL", '{"MOT": 0.72, "PROP": 0.61, "LIAB": 0.63, "CLT": 0.59, "DEC": 0.52}',
     "2024Q1", "Pricing basis 2024-2026 plans (initial-recognition profitability test input)",
     "Group pricing committee", "2023-12-15", "active"),
    ("risk_adjustment_cl", 1, "ALL", '{"confidence_level": 0.75, "method": "lognormal CoV per line"}',
     "2024Q1", "Group methodology paper IFRS17-RA-002", "Group CFO methodology committee", "2023-11-30", "active"),
    ("coverage_unit_basis", 1, "ALL", '{"CLT": "sum insured x remaining coverage", "DEC": "sum insured x remaining coverage"}',
     "2024Q1", "Group methodology paper IFRS17-CU-001", "Group CFO methodology committee", "2023-11-30", "active"),
    ("paa_eligibility", 1, "ALL", '{"MOT": "coverage <= 12m", "PROP": "coverage <= 12m", "LIAB": "coverage <= 12m"}',
     "2024Q1", "Transition working paper (asserted annually)", "Group reporting", "2023-11-30", "active"),
]
write([(a, v, p, j, e, s, ap, datetime.date.fromisoformat(at), st) for a, v, p, j, e, s, ap, at, st in ASM],
      "gov_assumption_registry",
      "assumption_id string, version int, portfolio_id string, value_json string, effective_from_run string, "
      "source string, approved_by string, approved_at date, status string",
      "governance",
      "Versioned, approved actuarial/methodology assumptions. Every engine run records the versions it "
      "used (gov_run_audit) — the drill from a P&L movement lands on an exact approved record here.")

# COMMAND ----------

# MAGIC %md ## Reinsurance held (truth) + FX
# MAGIC One 30% property quota share (drives the loss-recovery component when PROP goes onerous)
# MAGIC and one cat XL (data-only; visibly does NOT cover the onerous exposure question).

# COMMAND ----------

TREATIES = [
    ("QS-PROP-2025", "quota_share", "PROP", 0.30, 0.28, "2025-01-01", "2026-12-31",
     "Munich Re Syndicate (synthetic)", "30% QS on Property Homeowners & SME, commission 28%"),
    ("XL-CAT-2026", "cat_xl", "PROP,MOT", 0.0, 0.0, "2026-01-01", "2026-12-31",
     "Bricksurance Re (synthetic)", "Cat XL EUR 25m xs EUR 20m — event basis; June 2026 floods below attachment"),
]
write([(t[0], t[1], t[2], t[3], t[4], datetime.date.fromisoformat(t[5]), datetime.date.fromisoformat(t[6]), t[7], t[8])
       for t in TREATIES],
      "gen_truth_treaties",
      "treaty_id string, treaty_type string, portfolios string, cession_pct double, commission_pct double, "
      "inception date, expiry date, counterparty string, description string",
      "internal", "Generator truth (internal): reinsurance held programme.")

# COMMAND ----------

# MAGIC %md ## Done — hand over to 00b (landing files) and the DLT pipeline

# COMMAND ----------

print("00 complete.")
print("Truth tables: gen_truth_cohort, gen_truth_accident, gen_truth_runoff, gen_pay_pattern, gen_truth_treaties")
print("Reference:    ref_portfolio, ref_close_calendar, ref_coa_mapping, ref_ra_params")
print("Governance:   gov_assumption_registry")
