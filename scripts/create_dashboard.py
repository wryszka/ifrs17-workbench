#!/usr/bin/env python3
"""Create/update + publish the 'IFRS 17 — Close View' Lakeview dashboard (embedded in the app).

Usage: python3 scripts/create_dashboard.py [profile] [warehouse_id] [catalog] [schema] [dashboard_id]
Prints DASHBOARD_ID at the end. Also writes dashboards/ifrs17_close.lvdash.json for the repo.
Mirrors underwriting-workbench/scripts/create_dashboard.py (charts spec version 3).
"""
import json
import pathlib
import sys

from databricks.sdk import WorkspaceClient

prof = sys.argv[1] if len(sys.argv) > 1 else "DEV"
wh = sys.argv[2] if len(sys.argv) > 2 else "a3b61648ea4809e3"
cat = sys.argv[3] if len(sys.argv) > 3 else "lr_dev_aws_us_catalog"
sch = sys.argv[4] if len(sys.argv) > 4 else "ifrs17_workbench"
existing_id = sys.argv[5] if len(sys.argv) > 5 else None
F = f"{cat}.{sch}"

DATASETS = [
    {"name": "ds_isr", "displayName": "Insurance service result by quarter",
     "queryLines": [f"SELECT close_period, amount FROM {F}.gld_pnl_statement WHERE line_item = 'Insurance service result' ORDER BY close_period"]},
    {"name": "ds_revenue", "displayName": "Insurance revenue by component",
     "queryLines": [f"SELECT close_period, component, ROUND(SUM(amount),0) amount FROM {F}.gld_insurance_revenue GROUP BY 1,2 ORDER BY 1,2"]},
    {"name": "ds_groups", "displayName": "Groups at the current close",
     "queryLines": [f"SELECT group_id, measurement_model, ROUND(bs_lrc_total,0) bs_lrc_total, ROUND(csm,0) csm, ROUND(loss_component,0) loss_component, onerous FROM {F}.gld_cohort_360 ORDER BY bs_lrc_total DESC"]},
    {"name": "ds_headroom", "displayName": "Onerous headroom trend",
     "queryLines": [f"SELECT close_period, group_id, ROUND(headroom,0) headroom FROM {F}.gld_onerous_test WHERE group_id LIKE 'PROP%' OR group_id LIKE 'MOT%' ORDER BY close_period"]},
    {"name": "ds_lic", "displayName": "LIC closing by portfolio",
     "queryLines": [f"SELECT close_period, portfolio_id, ROUND(SUM(amount),0) lic_closing FROM {F}.gld_lic_rollforward WHERE step='closing' GROUP BY 1,2 ORDER BY 1,2"]},
    {"name": "ds_csm", "displayName": "CSM closing by group",
     "queryLines": [f"SELECT close_period, group_id, ROUND(SUM(amount),0) csm_closing FROM {F}.gld_csm_rollforward WHERE step='closing' GROUP BY 1,2 ORDER BY 1,2"]},
]


def widget(name, ds, x, y, title, wtype="bar", color=None, w=3, h=6, x0=0, y0=0):
    fields = [{"name": x, "expression": f"`{x}`"}, {"name": y, "expression": f"`{y}`"}]
    enc = {"x": {"fieldName": x, "scale": {"type": "categorical"}},
           "y": {"fieldName": y, "scale": {"type": "quantitative"}}}
    if color:
        fields.append({"name": color, "expression": f"`{color}`"})
        enc["color"] = {"fieldName": color, "scale": {"type": "categorical"}}
    return {"widget": {"name": name, "queries": [{"name": "main_query", "query": {
                "datasetName": ds, "fields": fields, "disaggregated": True}}],
            "spec": {"version": 3, "widgetType": wtype, "encodings": enc,
                     "frame": {"title": title, "showTitle": True}}},
            "position": {"x": x0, "y": y0, "width": w, "height": h}}


def table(name, ds, title, w=6, h=8, x0=0, y0=0):
    return {"widget": {"name": name, "queries": [{"name": "main_query", "query": {
                "datasetName": ds, "fields": [], "disaggregated": True}}],
            "spec": {"version": 1, "widgetType": "table", "encodings": {},
                     "frame": {"title": title, "showTitle": True}}},
            "position": {"x": x0, "y": y0, "width": w, "height": h}}


layout = [
    widget("w_isr", "ds_isr", "close_period", "amount", "Insurance service result by quarter (EUR)", "bar", None, 3, 6, 0, 0),
    widget("w_rev", "ds_revenue", "close_period", "amount", "Insurance revenue by component", "bar", "component", 3, 6, 3, 0),
    widget("w_csm", "ds_csm", "close_period", "csm_closing", "CSM closing by group (GMM)", "line", "group_id", 3, 6, 0, 6),
    widget("w_head", "ds_headroom", "close_period", "headroom", "§57 onerous headroom trend (PAA groups)", "line", "group_id", 3, 6, 3, 6),
    widget("w_lic", "ds_lic", "close_period", "lic_closing", "LIC closing by portfolio (discounted + RA)", "bar", "portfolio_id", 3, 6, 0, 12),
    table("t_groups", "ds_groups", "Groups at the current close", 3, 6, 3, 12),
]
dash = {"datasets": DATASETS,
        "pages": [{"name": "main", "displayName": "IFRS 17 — Close View",
                   "layout": layout, "pageType": "PAGE_TYPE_CANVAS"}]}

out_path = pathlib.Path(__file__).resolve().parents[1] / "dashboards" / "ifrs17_close.lvdash.json"
out_path.parent.mkdir(exist_ok=True)
out_path.write_text(json.dumps(dash, indent=1))
print("wrote", out_path)

w = WorkspaceClient(profile=prof)
from databricks.sdk.service.dashboards import Dashboard

ser = json.dumps(dash)
if existing_id:
    d = w.lakeview.update(existing_id, Dashboard(display_name="IFRS 17 Close View — Bricksurance SE",
                                                 serialized_dashboard=ser, warehouse_id=wh))
else:
    d = w.lakeview.create(Dashboard(display_name="IFRS 17 Close View — Bricksurance SE",
                                    serialized_dashboard=ser, warehouse_id=wh,
                                    parent_path=f"/Workspace/Users/{w.current_user.me().user_name}"))
w.lakeview.publish(d.dashboard_id, embed_credentials=True, warehouse_id=wh)
print("DASHBOARD_ID:", d.dashboard_id)
