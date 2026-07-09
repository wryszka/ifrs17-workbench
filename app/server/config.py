"""Config — all portability via env vars (set in app.yaml). No hardcoded catalog/schema/IDs."""
import os
from functools import lru_cache

from databricks.sdk import WorkspaceClient


def _flag(name, default=True):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


CATALOG = os.getenv("CATALOG_NAME", "lr_dev_aws_us_catalog")
SCHEMA = os.getenv("SCHEMA_NAME", "ifrs17_workbench")
WAREHOUSE_ID = os.getenv("WAREHOUSE_ID", "a3b61648ea4809e3")
USE_CACHE = _flag("USE_CACHE", True)
GENIE_SPACE_ID = os.getenv("GENIE_SPACE_ID", "")
DASHBOARD_ID = os.getenv("DASHBOARD_ID", "")
FM_ENDPOINT = os.getenv("FM_ENDPOINT", "databricks-claude-sonnet-4-5")
RESET_JOB_SUBSTR = os.getenv("RESET_JOB_SUBSTR", "ifrs17_99_reset")
CLOSE_JOB_SUBSTR = os.getenv("CLOSE_JOB_SUBSTR", "ifrs17_quarter_close")
LEVERS_JOB_SUBSTR = os.getenv("LEVERS_JOB_SUBSTR", "ifrs17_demo_levers")
HUB_APP_URL = os.getenv("HUB_APP_URL", "")

# Agent / model endpoints are resolved by substring at runtime (dev-prefix safe).
EP_MOVEMENT_SUBSTR = os.getenv("EP_MOVEMENT_SUBSTR", "ifrs17-movement")
EP_DISCLOSURE_SUBSTR = os.getenv("EP_DISCLOSURE_SUBSTR", "ifrs17-disclosure")
EP_EVIDENCE_SUBSTR = os.getenv("EP_EVIDENCE_SUBSTR", "ifrs17-evidence")
EP_BRIEF_SUBSTR = os.getenv("EP_BRIEF_SUBSTR", "ifrs17-brief")
EP_AGENT_SUBSTR = os.getenv("EP_AGENT_SUBSTR", "ifrs17_agent")  # the REAL tool-calling supervisor

CACHE_TABLE = f"{CATALOG}.{SCHEMA}.cache_agent_responses"

ROLE_SUBSTR = {
    "movement_narrator": EP_MOVEMENT_SUBSTR,
    "disclosure_drafter": EP_DISCLOSURE_SUBSTR,
    "audit_evidence": EP_EVIDENCE_SUBSTR,
    "cfo_brief": EP_BRIEF_SUBSTR,
}


def fqn(table: str) -> str:
    return f"{CATALOG}.{SCHEMA}.{table}"


@lru_cache(maxsize=1)
def get_workspace_client() -> WorkspaceClient:
    return WorkspaceClient()


@lru_cache(maxsize=16)
def resolve_endpoint(substr: str) -> str:
    try:
        names = [e.name for e in get_workspace_client().serving_endpoints.list()]
        for n in names:
            if substr in n:
                return n
        if substr == EP_AGENT_SUBSTR:
            # agents.deploy auto-names `agents_<catalog>-<schema>-<model>` TRUNCATED to 63 chars —
            # the model name may be cut, so match on the schema instead.
            for n in names:
                if n.startswith("agents_") and SCHEMA in n:
                    return n
    except Exception:
        pass
    return substr


def workspace_host() -> str:
    h = os.getenv("DATABRICKS_HOST", "")
    if not h:
        try:
            h = get_workspace_client().config.host or ""
        except Exception:
            h = ""
    h = h.rstrip("/")
    if h and not h.startswith("http"):
        h = "https://" + h
    return h
