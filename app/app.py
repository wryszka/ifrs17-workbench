"""IFRS 17 Workbench — thin FastAPI backend. Presentation only: every panel reads a real
engine table / UC function / serving endpoint / Genie. No measurement logic lives here.

Full endpoint surface built in P6; this stub serves the SPA + health so the app deploys early.
"""
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse

from server import config

app = FastAPI(title="IFRS 17 Workbench")

DIST = os.path.join(os.path.dirname(__file__), "dist")


@app.get("/api/health")
def health():
    return {"ok": True, "catalog": config.CATALOG, "schema": config.SCHEMA}


@app.get("/")
def index():
    return FileResponse(os.path.join(DIST, "index.html"))
