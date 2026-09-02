#!/usr/bin/env python3
from __future__ import annotations
import hashlib, json, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROCESS = ROOT / "org-boundary" / "runtime" / "process_boundary.py"
REGISTRY = ROOT / "org-boundary" / "registry" / "services.json"

def canon(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":")).encode()

def denial_receipt(envelope: dict, reason: str) -> dict:
    subject = {
        "packet_id": envelope.get("packet_id"),
        "destination": envelope.get("destination"),
        "reason": reason
    }
    rid = "boundary-denied-" + hashlib.sha256(canon(subject)).hexdigest()[:24]
    return {
        "schema": "stegverse.sv011-boundary-denial-receipt/v0.1",
        "entity_id": "SV-011",
        "receipt_id": rid,
        "packet_id": envelope.get("packet_id"),
        "decision": "DENY",
        "consumed": False,
        "consequence_reachable": False,
        "reason": reason,
        "authority": {
            "execution_authorized": False,
            "publication_authorized": False,
            "proofs_accepted": False
        }
    }

def process(envelope: dict) -> dict:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        inp, out = td / "in.json", td / "out.json"
        inp.write_text(json.dumps(envelope), encoding="utf-8")
        p = subprocess.run(
            [sys.executable, str(PROCESS), "--envelope", str(inp), "--registry", str(REGISTRY), "--out", str(out)],
            cwd=ROOT, capture_output=True, text=True
        )
        if p.returncode == 0:
            return {"decision":"ALLOW","execution":json.loads(out.read_text(encoding="utf-8"))}
        reason = (p.stderr or p.stdout or "boundary-rejected").strip().splitlines()[-1]
        return {"decision":"DENY","denial_receipt":denial_receipt(envelope, reason)}
