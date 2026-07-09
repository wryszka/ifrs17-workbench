#!/usr/bin/env python3
"""Create the 'IFRS 17 — Ask the Close' Genie space over the close marts. Reproducible.
Usage: python3 scripts/create_genie_space.py [profile] [warehouse_id] [catalog] [schema]
Prints the space_id on success. Uses the genie-rooms skill's GenieSpaceBuilder."""
import json
import pathlib
import subprocess
import sys

prof = sys.argv[1] if len(sys.argv) > 1 else "DEV"
wh = sys.argv[2] if len(sys.argv) > 2 else "a3b61648ea4809e3"
cat = sys.argv[3] if len(sys.argv) > 3 else "lr_dev_aws_us_catalog"
sch = sys.argv[4] if len(sys.argv) > 4 else "ifrs17_workbench"

BUILDER = pathlib.Path.home() / ".vibe/marketplace/plugins/fe-internal-tools/skills/genie-rooms/resources"
sys.path.insert(0, str(BUILDER))
from genie_space_builder import GenieSpaceBuilder  # noqa: E402

fqn = f"{cat}.{sch}"
TITLE = "IFRS 17 — Ask the Close (Bricksurance SE)"
space = GenieSpaceBuilder(
    title=TITLE,
    description=("Natural-language analytics over the IFRS 17 quarterly close: groups and cohorts, CSM "
                 "roll-forwards, the onerous test and loss components, LIC development, statements and "
                 "disclosures, recon status and the close board."),
    warehouse_id=wh,
)
space.set_instructions(
    "You answer questions about a European P&C insurer's IFRS 17 quarterly close (synthetic data; EUR; "
    "current close period 2026Q2; periods look like 2026Q2). TERMINOLOGY: insurance revenue is NEVER "
    "premium; PAA groups have no CSM; groups never re-bucket. gld_cohort_360 is one row per group at the "
    "current close (group_id like PROP-2026-REM; measurement_model PAA/GMM/LIC_ONLY; onerous flag, "
    "headroom, csm, loss_component). gld_csm_rollforward has the B96 CSM steps per GMM group per period. "
    "gld_onerous_test has the quarterly §57 test per PAA group (headroom = LRC carrying - FCF remaining). "
    "gld_lic_rollforward is the liability for incurred claims per portfolio x accident year (steps incl. "
    "incurred_current_service, past_service_changes, unwind_ifie, claims_paid). gld_pnl_statement is the "
    "§80 face; gld_insurance_revenue decomposes revenue; gld_disclosure_lrc_lic is the §101 roll-forward "
    "(components lrc_excl_lc / loss_component / lic). gld_trial_balance_recon has the GL recon incl. the "
    "reclass journal. gld_close_status is the Day 1-10 board. gld_ave_analysis splits experience vs "
    "assumption changes vs finance effects. gov_assumption_registry holds versioned approved assumptions "
    "(flood_freq_property v2 is the 2026Q2 story). Report money in EUR millions unless asked otherwise."
)
TABLES = ["gld_cohort_360", "gld_contract_groups", "gld_csm_rollforward", "gld_loss_component",
          "gld_lrc_paa_rollforward", "gld_lic_rollforward", "gld_onerous_test", "gld_onerous_watch",
          "gld_fcf_summary", "gld_discount_impact", "gld_coverage_units", "gld_insurance_revenue",
          "gld_pnl_statement", "gld_balance_sheet", "gld_disclosure_lrc_lic", "gld_disclosure_by_component",
          "gld_ra_rollforward", "gld_ave_analysis", "gld_ri_held", "gld_trial_balance_recon",
          "gld_sii_crosswalk", "gld_close_status", "gld_feed_sla", "gld_dq_scorecard",
          "gld_book_summary", "gld_claims_triangles", "gld_lic_ultimates",
          "ref_portfolio", "ref_rfr_meta", "gov_assumption_registry"]
assert len(TABLES) <= 30
for t in TABLES:
    space.add_table(f"{fqn}.{t}")
space.validate()

payload = {
    "title": TITLE,
    "description": "IFRS 17 close analytics: groups, CSM, onerous test, LIC, statements, recon, close board.",
    "parent_path": "/Workspace/Users/laurence.ryszka@databricks.com",
    "warehouse_id": wh,
    "serialized_space": space.to_json(),
}
open("/tmp/create_genie_space_i17.json", "w").write(json.dumps(payload))
out = subprocess.run(["databricks", "api", "post", "/api/2.0/genie/spaces", "--profile", prof,
                      "--json", "@/tmp/create_genie_space_i17.json"], capture_output=True, text=True)
print(out.stdout[:800] or out.stderr[:800])
try:
    print("SPACE_ID:", json.loads(out.stdout)["space_id"])
except Exception:  # noqa: BLE001
    pass
