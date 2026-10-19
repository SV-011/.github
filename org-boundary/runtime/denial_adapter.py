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
    envelope = envelope if isinstance(envelope, dict) else {}  # a malformed envelope is still denied, not raised
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

# Data-Continuation/.github#10's process_boundary exit codes.
WRITE_FAULT_EXIT = 75   # PROCESS_BOUNDARY_WRITE_FAULT: our own write faulted; non-terminal, nothing durable
INPUT_FAULT_EXIT = 65   # PROCESS_BOUNDARY_INPUT_FAULT: the envelope itself is bad; terminal

def write_fault(detail: str) -> dict:
    """Six fields, non-terminal: no denial receipt is issued, so the request stays pending and the next run
    processes it once. An own-state fault never becomes a DENY."""
    return {
        "disposition": "FAIL_CLOSED", "failure_code": "PROCESS_BOUNDARY_WRITE_FAULT",
        "failed_predicate": "OUT_PATH_WRITABLE", "detail": detail,
        "required_evidence_or_repair": "a writable working directory for the boundary processor",
        "retry_entrypoint": "org-boundary/runtime/denial_adapter.py::process (the same envelope)",
        "owning_existing_goal": "INTERLOCK-INTR-RUNTIME-INDEPENDENT-PROTOCOL-001",
        "next_attempt": "re-run with the same envelope; nothing durable was written",
        "terminal": False, "derived_from_clock": False, "consequence_committed": False,
    }

def process(envelope: dict) -> dict:
    """0 is ALLOW; 65 (input fault) and the processor's other refusals are a terminal DENY with its receipt;
    75, or an OSError of our own, is a non-terminal WRITE_FAULT with no receipt."""
    try:
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
    except OSError as exc:
        return {"decision":"FAIL_CLOSED","fault":write_fault(type(exc).__name__ + ": " + str(exc))}
    reason = (p.stderr or p.stdout or "boundary-rejected").strip().splitlines()[-1]
    if p.returncode == WRITE_FAULT_EXIT:
        return {"decision":"FAIL_CLOSED","fault":write_fault(reason)}
    denied = {"decision":"DENY","denial_receipt":denial_receipt(envelope, reason)}
    if p.returncode == INPUT_FAULT_EXIT:
        try:
            emitted = json.loads(reason)
        except ValueError:
            emitted = {"failed_predicate": "INPUT_READABLE_JSON_OBJECT", "detail": reason}
        denied["terminal_record"] = {**emitted, "disposition": "FAIL_CLOSED",
                                     "failure_code": "FRAME_INGEST_FAILED",
                                     "boundary_failure_code": "PROCESS_BOUNDARY_INPUT_FAULT",
                                     "derived_from_clock": False, "consequence_committed": False}
    return denied
