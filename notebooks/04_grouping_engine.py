# Databricks notebook source
# MAGIC %md
# MAGIC # 04 · Grouping engine — §16–24 units of account
# MAGIC
# MAGIC Portfolio → profitability bucket → annual cohort, **fixed at initial recognition and never
# MAGIC re-bucketed**. The initial-recognition profitability test runs on governed inputs only:
# MAGIC pricing loss ratios from `gov_assumption_registry`, actual acquisition ratios from the
# MAGIC landed policy book, expense ratios from the allocation feed. A group later turning onerous
# MAGIC raises a **loss component inside the group** (04c) — it does not move buckets.
# MAGIC
# MAGIC Buckets: `ONE` onerous at initial recognition · `NSP` no significant possibility of
# MAGIC becoming onerous (comfortably profitable, CR ≤ 86% on the group threshold) · `REM` remaining.

# COMMAND ----------

# MAGIC %run ./engine_common

# COMMAND ----------

asm = load_assumptions()
pricing_lr = asm["pricing_loss_ratio"][1]

book = pdf(f"""
    SELECT portfolio_id, cohort_year, COUNT(*) policies, ROUND(SUM(total_premium),2) gwp,
           ROUND(SUM(acq_cost),2) acq_cash, MIN(inception_date) first_inception
    FROM {FQ}.slv_policy GROUP BY 1, 2""")

# expense ratio per portfolio: attributable expense over the window / GWP over the window
expense = pdf(f"""SELECT portfolio_id, ROUND(SUM(attributable_expense),2) att_expense
                  FROM {FQ}.slv_expense GROUP BY 1""").set_index("portfolio_id")["att_expense"]
gwp_total = book.groupby("portfolio_id")["gwp"].sum()
exp_ratio = {p: round(float(expense.get(p, 0.0)) / float(gwp_total.get(p, 1.0)), 4)
             for p in gwp_total.index}

ports = pdf(f"SELECT * FROM {FQ}.ref_portfolio").set_index("portfolio_id")

rows, mapping = [], []
for _, r in book.sort_values(["portfolio_id", "cohort_year"]).iterrows():
    port, cy = r["portfolio_id"], int(r["cohort_year"])
    model = ports.loc[port, "measurement_model"]
    acq_ratio = round(float(r["acq_cash"]) / float(r["gwp"]), 4)
    cr = round(pricing_lr[port] + exp_ratio.get(port, 0.16) + acq_ratio, 4)
    bucket = "ONE" if cr > 1.0 else ("NSP" if cr <= 0.86 else "REM")
    gid = f"{port}-{cy}-{bucket}"
    rows.append(dict(group_id=gid, portfolio_id=port, cohort_year=cy, profitability_bucket=bucket,
                     measurement_model=model, recognition_cr=cr,
                     locked_in_curve_date=LOCKED_IN_CURVE[cy],
                     recognition_date=str(r["first_inception"]),
                     policies=int(r["policies"]), gwp=float(r["gwp"]), acq_cash=float(r["acq_cash"])))

# Run-off: LIC-only groups from the landed claims (no policies, no LRC, no CSM — ever).
ro = pdf(f"""SELECT CAST(SUBSTRING(accident_quarter,1,4) AS INT) accident_year,
                    ROUND(SUM(paid_to_date + case_reserve),2) ultimate, COUNT(*) claims
             FROM {FQ}.slv_claim WHERE portfolio_id='RO' GROUP BY 1""")
for _, r in ro.sort_values("accident_year").iterrows():
    ay = int(r["accident_year"])
    rows.append(dict(group_id=f"RO-{ay}-LIC", portfolio_id="RO", cohort_year=ay,
                     profitability_bucket="LIC", measurement_model="LIC_ONLY", recognition_cr=None,
                     locked_in_curve_date=None, recognition_date=f"{ay}-01-01",
                     policies=0, gwp=0.0, acq_cash=0.0))

groups = pd.DataFrame(rows)
write_engine(groups, "gld_contract_groups",
             "group_id string, portfolio_id string, cohort_year int, profitability_bucket string, "
             "measurement_model string, recognition_cr double, locked_in_curve_date string, "
             "recognition_date string, policies int, gwp double, acq_cash double",
             "IFRS 17 groups (§16-24): portfolio × profitability bucket × annual cohort, FIXED at initial "
             "recognition. Buckets: ONE onerous / NSP no-significant-possibility / REM remaining. "
             "A group turning onerous later raises a loss component inside the group — never a re-bucketing.")

# policy → group map
gmap = pdf(f"SELECT policy_id, portfolio_id, cohort_year FROM {FQ}.slv_policy")
gid_lookup = groups.set_index(["portfolio_id", "cohort_year"])["group_id"].to_dict()
gmap["group_id"] = gmap.apply(lambda x: gid_lookup.get((x["portfolio_id"], int(x["cohort_year"]))), axis=1)
write_engine(gmap[["policy_id", "group_id", "portfolio_id", "cohort_year"]],
             "gld_group_policy_map",
             "policy_id string, group_id string, portfolio_id string, cohort_year int",
             "Every policy allocated to exactly one IFRS 17 group.")

assert not groups[groups["profitability_bucket"] == "ONE"].shape[0], \
    "no cohort should be onerous AT INITIAL RECOGNITION in this book (onerous arises later, in 04c)"
assert set(groups[groups["portfolio_id"] == "MOT"]["profitability_bucket"]) == {"REM"}, "motor is thin-margin REM"
assert set(groups[groups["portfolio_id"].isin(["CLT", "DEC"])]["profitability_bucket"]) == {"NSP"}

log_run("grouping_engine", ["slv_policy", "slv_expense", "slv_claim", "gov_assumption_registry"],
        {"pricing_loss_ratio": 1}, ["gld_contract_groups", "gld_group_policy_map"],
        note=f"{len(groups)} groups; buckets computed on pricing basis + landed acq/expense")
set_status(5, "Measurement", "in_progress", "groups fixed; engines running", "grouping_engine")
display(spark.table(f"{FQ}.gld_contract_groups"))
