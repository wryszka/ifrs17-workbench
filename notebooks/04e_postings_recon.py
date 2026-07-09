# Databricks notebook source
# MAGIC %md
# MAGIC # 04e · Subledger postings + GL reconciliation
# MAGIC
# MAGIC Every roll-forward movement posts a **balanced Dr/Cr pair** through `ref_coa_mapping` →
# MAGIC `gld_subledger_postings` (ΣDr = ΣCr every period, asserted). The recon compares the
# MAGIC subledger against the SAP-shaped GL on the accounts both sides carry: cash legs tie by
# MAGIC definition of a working feed; the ONE break — €412,340.00 of attributable claims-ops cost
# MAGIC misbooked to other opex in Q2 2026 — is found here and cleared by the approved manual
# MAGIC journal. Technical IFRS 17 accounts (LRC/LC/LIC/CSM) are subledger-owned: the workbench
# MAGIC IS the system of record for those.

# COMMAND ----------

# MAGIC %run ./engine_common

# COMMAND ----------

cash = pdf(f"SELECT * FROM {FQ}.gld_cash_measures")
lrc = pdf(f"SELECT * FROM {FQ}.gld_lrc_paa_rollforward")
lc = pdf(f"SELECT * FROM {FQ}.gld_loss_component")
lic = pdf(f"SELECT * FROM {FQ}.gld_lic_rollforward")
rev_gmm = pdf(f"SELECT * FROM {FQ}.gld_revenue_gmm")
ri = pdf(f"SELECT * FROM {FQ}.gld_ri_held")
oci = pdf(f"SELECT * FROM {FQ}.gld_discount_impact")
expense = pdf(f"SELECT * FROM {FQ}.slv_expense")
groups = pdf(f"SELECT group_id, portfolio_id, measurement_model FROM {FQ}.gld_contract_groups")
gmap = groups.set_index("group_id")["measurement_model"]

P = []  # (period, ref, posting_key, account, dr_cr, amount)


def post(period, ref, key, amount, dr, cr):
    """One balanced pair. Negative amounts flip the legs so postings stay positive."""
    amount = round(float(amount), 2)
    if amount == 0.0:
        return
    if amount < 0:
        amount, dr, cr = -amount, cr, dr
    P.append((period, ref, key, dr, "DR", amount))
    P.append((period, ref, key, cr, "CR", amount))


for _, r in cash.iterrows():
    lbl, gid = r["close_period"], r["group_id"]
    post(lbl, gid, "premiums_received", r["premiums_received"], "1000", "2100")
    post(lbl, gid, "acq_cashflows_paid", r["acq_paid"], "2100", "1000")

# PAA revenue (earned) + acquisition amortisation
for _, r in lrc[lrc["step"] == "insurance_revenue"].iterrows():
    post(r["close_period"], r["group_id"], "insurance_revenue", -r["amount"], "2100", "4000")
for _, r in lrc[lrc["step"] == "acq_amortisation"].iterrows():
    post(r["close_period"], r["group_id"], "acq_amortisation", r["amount"], "5020", "2100")

# GMM revenue (components sum) — LRC released as revenue
gmm_rev = rev_gmm.groupby(["group_id", "close_period"])["amount"].sum().reset_index()
for _, r in gmm_rev.iterrows():
    post(r["close_period"], r["group_id"], "insurance_revenue", r["amount"], "2100", "4000")
gmm_acq = cash[cash["group_id"].map(gmap) == "GMM"]
for _, r in gmm_acq.iterrows():
    post(r["close_period"], r["group_id"], "acq_amortisation", r["acq_amortised"], "5020", "2100")

# loss component
for _, r in lc[lc["step"] == "recognised_in_period"].iterrows():
    post(r["close_period"], r["group_id"], "loss_component_recognised", r["amount"], "5010", "2110")
for _, r in lc[lc["step"] == "reversed_in_period"].iterrows():
    post(r["close_period"], r["group_id"], "loss_component_reversed", -r["amount"], "2110", "5010")

# LIC movements. The run-off book's window-start balance is an OPENING position, not P&L —
# it posts against opening equity (3000), never against insurance service expenses.
for _, r in lic.iterrows():
    key = f"{r['portfolio_id']}-{r['accident_year']}"
    if r["step"] == "brought_forward_window_start":
        post(r["close_period"], key, "lic_brought_forward", r["amount"], "3000", "2200")
    elif r["step"] in ("incurred_current_service", "past_service_changes"):
        post(r["close_period"], key, "claims_incurred", r["amount"], "5000", "2200")
    elif r["step"] in ("unwind_ifie", "rate_change_ifie"):
        post(r["close_period"], key, "ifie_lic", r["amount"], "6000", "2200")
    elif r["step"] == "claims_paid":
        post(r["close_period"], key, "claims_paid", -r["amount"], "2200", "1000")

# GMM OCI remeasurement (current vs locked-in on the LRC)
for _, r in oci.iterrows():
    post(r["close_period"], r["group_id"], "oci_remeasurement", r["oci_in_period"], "3900", "2100")

# expenses (attributable per portfolio; non-attributable once per period)
for _, r in expense.iterrows():
    post(r["period"], r["portfolio_id"], "expenses_attributable", r["attributable_expense"], "5040", "1000")
for lbl, amt in expense.groupby("period")["non_attributable_expense"].max().items():
    post(lbl, "GROUP", "expenses_nonattributable", amt, "7000", "1000")

# reinsurance held
ri_flows = {"premium_ceded": ("2300", "1000"), "commission_income": ("1000", "5100"),
            "recoveries_on_paid": ("1000", "2300")}
for _, r in ri.iterrows():
    if r["component"] in ri_flows:
        dr, cr = ri_flows[r["component"]]
        post(r["close_period"], r["treaty_id"], f"ri_{r['component']}", abs(r["amount"]), dr, cr)
# recoverable + loss-recovery are BALANCES → post period deltas
for comp in ("recoverable_on_lic", "loss_recovery_component"):
    bal = ri[ri["component"] == comp].sort_values("close_period")
    prev = 0.0
    for _, r in bal.iterrows():
        post(r["close_period"], r["treaty_id"], f"ri_{comp}_delta", r["amount"] - prev, "2300", "5100")
        prev = r["amount"]

postings = pd.DataFrame(P, columns=["close_period", "reference", "posting_key", "gl_account", "dr_cr", "amount"])
coa = pdf(f"SELECT gl_account, MAX(gl_account_name) gl_account_name FROM {FQ}.ref_coa_mapping GROUP BY 1") \
    .set_index("gl_account")["gl_account_name"]
postings["gl_account_name"] = postings["gl_account"].map(coa).fillna(
    postings["gl_account"].map({"3000": "Opening equity adjustment (window start)"})).fillna("(technical)")
write_engine(postings, "gld_subledger_postings",
             "close_period string, reference string, posting_key string, gl_account string, dr_cr string, "
             "amount double, gl_account_name string",
             "IFRS 17 subledger: every engine movement as a balanced Dr/Cr pair. ΣDr = ΣCr every period "
             "(asserted in the smoke test). The workbench is the system of record for the technical accounts.")

bal_check = postings.groupby(["close_period", "dr_cr"])["amount"].sum().unstack()
assert (abs(bal_check["DR"] - bal_check["CR"]) < 0.02).all(), f"postings unbalanced:\n{bal_check}"
print("postings balanced across", len(bal_check), "periods ✓")

# COMMAND ----------

# MAGIC %md ## Trial-balance reconciliation — subledger vs SAP GL

# COMMAND ----------

gl = pdf(f"SELECT * FROM {FQ}.slv_gl_balance")
jrn = pdf(f"SELECT * FROM {FQ}.slv_manual_journal WHERE status = 'approved'")

sub_net = postings.copy()
sub_net["signed"] = sub_net.apply(lambda r: r["amount"] if r["dr_cr"] == "DR" else -r["amount"], axis=1)
sub_by = sub_net.groupby(["close_period", "gl_account"])["signed"].sum().round(2)

# recon scope: the accounts both sides carry (cash legs + expense split); GL memo accounts map
# to the subledger's originating movements
RECON = [  # (label, gl_account, subledger metric)
    ("Premiums received", "4900", lambda lbl: sub_net[(sub_net["close_period"] == lbl) & (sub_net["posting_key"] == "premiums_received") & (sub_net["dr_cr"] == "DR")]["amount"].sum()),
    ("Claims paid", "5900", lambda lbl: -sub_net[(sub_net["close_period"] == lbl) & (sub_net["posting_key"] == "claims_paid") & (sub_net["dr_cr"] == "DR")]["amount"].sum()),
    ("Acquisition cash flows", "5950", lambda lbl: -sub_net[(sub_net["close_period"] == lbl) & (sub_net["posting_key"] == "acq_cashflows_paid") & (sub_net["dr_cr"] == "DR")]["amount"].sum()),
    ("Attributable expenses", "5040", lambda lbl: -sub_net[(sub_net["close_period"] == lbl) & (sub_net["posting_key"] == "expenses_attributable") & (sub_net["dr_cr"] == "DR")]["amount"].sum()),
    ("Other operating expenses", "7000", lambda lbl: -sub_net[(sub_net["close_period"] == lbl) & (sub_net["posting_key"] == "expenses_nonattributable") & (sub_net["dr_cr"] == "DR")]["amount"].sum()),
]
gl_ix = gl.set_index(["period", "gl_account"])["movement_eur"]

rows = []
for lbl in QL:
    for label, acct, fn in RECON:
        sub_amt = round(float(fn(lbl)), 2)
        gl_amt = round(float(gl_ix.get((lbl, acct), 0.0)), 2)
        j = jrn[(jrn["period"] == lbl) & ((jrn["gl_account_dr"] == acct) | (jrn["gl_account_cr"] == acct))]
        j_adj = 0.0
        journal_id = None
        for _, jr in j.iterrows():
            # GL convention: expenses are negative movements. A DR reclass onto this account makes
            # it more negative; a CR relieves it.
            j_adj += -float(jr["amount_eur"]) if jr["gl_account_dr"] == acct else float(jr["amount_eur"])
            journal_id = jr["journal_id"]
        gl_adjusted = round(gl_amt + j_adj, 2)
        diff = round(sub_amt - gl_adjusted, 2)
        raw_diff = round(sub_amt - gl_amt, 2)
        rows.append(dict(close_period=lbl, recon_item=label, gl_account=acct,
                         subledger_amount=sub_amt, gl_amount=gl_amt, raw_difference=raw_diff,
                         journal_adjustment=round(j_adj, 2), journal_id=journal_id,
                         residual=diff, status="tied" if abs(diff) < 0.05 else "INVESTIGATE"))

recon = pd.DataFrame(rows)
write_engine(recon, "gld_trial_balance_recon",
             "close_period string, recon_item string, gl_account string, subledger_amount double, "
             "gl_amount double, raw_difference double, journal_adjustment double, journal_id string, "
             "residual double, status string",
             "Subledger vs SAP GL on the accounts both sides carry. Cash legs tie to the cent; the one "
             "Q2 2026 break (attributable claims-ops cost misbooked to other opex) is cleared by the "
             "approved reclass journal MJ-2026Q2-001. Technical accounts are subledger-owned (no recon target).")

bad = recon[(recon["close_period"] == CLOSE_PERIOD) & (recon["status"] != "tied")]
print("Q2 2026 recon after journals:", "ALL TIED ✓" if bad.empty else bad.to_string())

set_status(6, "Postings & recon", "done" if bad.empty else "blocked",
           "subledger posted balanced; GL recon tied (1 reclass journal applied)" if bad.empty
           else f"residuals: {bad['recon_item'].tolist()}", "postings_recon")
log_run("postings_recon_engine",
        ["gld_cash_measures", "gld_lrc_paa_rollforward", "gld_loss_component", "gld_lic_rollforward",
         "gld_revenue_gmm", "gld_ri_held", "gld_discount_impact", "slv_expense", "slv_gl_balance",
         "slv_manual_journal"],
        {}, ["gld_subledger_postings", "gld_trial_balance_recon"],
        note="ΣDr=ΣCr asserted; Q2 2026 recon tied post-journal")
print("04e complete")
