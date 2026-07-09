#!/usr/bin/env python3
"""Grant the app service principal everything it needs — one command after (re)creating the app.

Usage: python3 scripts/grant_app_sp.py [profile] [catalog] [schema] [warehouse_id] [genie_space_id] [dashboard_id]
Auto-discovers the app SP from the app, all ifrs17 serving endpoints, and the close/levers/reset jobs.
Idempotent. Mirrors docs/DEPLOY.md. NOTE: the SP is deliberately NOT added to
ifrs17_finance_controllers — the UC masking demo depends on that.
"""
import sys

from databricks.sdk import WorkspaceClient

prof = sys.argv[1] if len(sys.argv) > 1 else "DEV"
cat = sys.argv[2] if len(sys.argv) > 2 else "lr_dev_aws_us_catalog"
sch = sys.argv[3] if len(sys.argv) > 3 else "ifrs17_workbench"
wh = sys.argv[4] if len(sys.argv) > 4 else "a3b61648ea4809e3"
genie = sys.argv[5] if len(sys.argv) > 5 else ""
dash = sys.argv[6] if len(sys.argv) > 6 else ""

w = WorkspaceClient(profile=prof)
app = w.apps.get("ifrs17-workbench")
sp = app.service_principal_client_id
print(f"app SP: {sp}")


def sql(stmt):
    r = w.statement_execution.execute_statement(statement=stmt, warehouse_id=wh, wait_timeout="50s")
    st = r.status.state.value if r.status else "?"
    print(("✓" if st == "SUCCEEDED" else f"✗ {st}"), stmt[:90])


sql(f"GRANT USE CATALOG ON CATALOG {cat} TO `{sp}`")
sql(f"GRANT USE SCHEMA, SELECT, EXECUTE, MODIFY ON SCHEMA {cat}.{sch} TO `{sp}`")
sql(f"GRANT READ VOLUME, WRITE VOLUME ON VOLUME {cat}.{sch}.ifrs17_files TO `{sp}`")

eps = [e for e in w.serving_endpoints.list()
       if e.name.startswith("ifrs17-") or (e.name.startswith("agents_") and sch in e.name)]
for e in eps:
    try:
        w.api_client.do("PATCH", f"/api/2.0/permissions/serving-endpoints/{w.serving_endpoints.get(e.name).id}",
                        body={"access_control_list": [{"service_principal_name": sp, "permission_level": "CAN_QUERY"}]})
        print("✓ CAN_QUERY", e.name)
    except Exception as ex:  # noqa: BLE001
        print("✗", e.name, str(ex)[:80])

for sub in ("ifrs17_99_reset", "ifrs17_quarter_close", "ifrs17_demo_levers"):
    job = next((j for j in w.jobs.list(limit=100) if sub in (j.settings.name or "")), None)
    if job:
        w.api_client.do("PATCH", f"/api/2.0/permissions/jobs/{job.job_id}",
                        body={"access_control_list": [{"service_principal_name": sp, "permission_level": "CAN_MANAGE_RUN"}]})
        print(f"✓ CAN_MANAGE_RUN {sub}", job.job_id)

for obj, oid, lvl in (("genie", genie, "CAN_RUN"), ("dashboards", dash, "CAN_READ")):
    if not oid:
        continue
    try:
        w.api_client.do("PATCH", f"/api/2.0/permissions/{obj}/{oid}",
                        body={"access_control_list": [{"service_principal_name": sp, "permission_level": lvl}]})
        print(f"✓ {lvl} {obj} {oid}")
    except Exception as ex:  # noqa: BLE001
        print("✗", obj, str(ex)[:80])
print("done — restart the app once so endpoint-name caches refresh")
