#!/usr/bin/env python3
from __future__ import annotations
import json, subprocess, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/"resident-runtime/run_phase5_probe.py"
REQ=ROOT/"resident-runtime/requests"

def invoke(name):
    with tempfile.TemporaryDirectory() as td:
        p=subprocess.run([sys.executable,str(RUN),"--request",str(REQ/name),"--evidence-dir",td,"--runtime-observer","HOSTED_SOURCE_VALIDATION_ONLY"],cwd=ROOT,capture_output=True,text=True)
        if p.returncode != 0: raise SystemExit(p.stderr or p.stdout)
        result=json.loads((Path(td)/(json.loads((REQ/name).read_text())["request_id"]+".json")).read_text())
        return result

def main():
    allow=invoke("phase5-allow.json")
    deny=invoke("phase5-deny.json")
    if allow["result"]["decision"]!="ALLOW": raise SystemExit("allow path failed")
    if len(allow["result"]["execution"].get("receipts",[]))!=5: raise SystemExit("allow receipt chain incomplete")
    if deny["result"]["decision"]!="DENY": raise SystemExit("deny path failed")
    if deny["result"]["denial_receipt"].get("consumed") is not False: raise SystemExit("deny consumed")
    if deny["result"]["denial_receipt"].get("consequence_reachable") is not False: raise SystemExit("deny consequence reachable")
    print("SV011_PHASE5_ORG_BOUNDARY_SOURCE_PASS")
    return 0
if __name__=="__main__": raise SystemExit(main())
