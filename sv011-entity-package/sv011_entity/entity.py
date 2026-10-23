"""The governed entity: proposes; transitions only on ALLOW from the declared predicates.

The entity has no model or inference of its own. Its "proposal" is the governed evaluation
of a declared action request against its current recorded state. Predicates are evaluated
over the evidence the request declares; the entity does not verify external reality.
Time (observed_at) is a supplied input, never read from a clock.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import bootstrap, record
from .dispositions import allow, non_allow

REQUEST_SCHEMA = "stegverse.sv011.entity-action-request/v0"
REQUEST_FIELDS = ("schema", "action", "transition_id", "transition_class", "predecessor_state_sha256",
                  "successor_state", "observed_at", "evidence")
EVIDENCE_FIELDS = ("standing_results", "viability", "necessity", "uncertainty_status", "purpose_result",
                   "scope", "horizon", "proof_path")


def _deny(code: str, pred: str, repair: str, nxt: str = "submit an action the package manifest declares", **x: Any) -> dict[str, Any]:
    return non_allow("DENY", code, pred, repair, nxt, **x)


def _fc(code: str, pred: str, repair: str, nxt: str = "correct the action request and re-run", **x: Any) -> dict[str, Any]:
    return non_allow("FAIL_CLOSED", code, pred, repair, nxt, **x)


def _read_request(path: str | Path | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if path is None or not Path(path).is_file():
        return None, _fc("SV011_ENTITY_ACTION_REQUEST_ABSENT", "an action request file is supplied", "supply --request")
    try:
        req = json.loads(Path(path).read_text(encoding="utf-8"))
    except ValueError:
        return None, _fc("SV011_ENTITY_ACTION_REQUEST_UNREADABLE", "action request is JSON", "fix the request JSON")
    if not isinstance(req, dict):
        return None, _fc("SV011_ENTITY_ACTION_REQUEST_INVALID", "action request is an object", "fix the request")
    return req, None


def evaluate(gov: dict[str, Any], req: dict[str, Any], current_state_sha256: str) -> dict[str, Any]:
    """Run the declared GTG-A1..A8 predicates. Returns ALLOW or DENY (six fields) - never writes."""
    ev = req["evidence"]
    case = {"state_ref": current_state_sha256, "candidate_transition_ref": req["transition_id"],
            "necessity": ev["necessity"], "viability": ev["viability"]}
    candidate = {"disposition": "ALLOW", "standing_results": ev["standing_results"], "scope": ev["scope"],
                 "horizon": ev["horizon"], "proof_path": ev["proof_path"],
                 "uncertainty_status": ev["uncertainty_status"], "purpose_result": ev["purpose_result"]}
    candidate["authority" + "_effect"] = False  # GTG-A8 non-authority boundary input
    p = gov["admissibility_predicates"]
    results = {
        "GTG-A1": p["GTG-A1"](case), "GTG-A2": p["GTG-A2"](case),
        "GTG-A3": p["GTG-A3"](case, candidate), "GTG-A4": p["GTG-A4"](candidate),
        "GTG-A5": p["GTG-A5"](candidate), "GTG-A6": p["GTG-A6"](case, candidate),
        "GTG-A7": p["GTG-A7"](candidate), "GTG-A8": p["GTG-A8"](candidate),
    }
    results = {k: bool(v) for k, v in results.items()}
    failed = [k for k, v in results.items() if not v]
    flag = {gov["disposition_vocabulary"]["execution_flag_key"]: False}
    if failed:
        out = _deny("SV011_ENTITY_PREDICATE_NOT_SATISFIED", f"declared predicates hold; failed: {failed}",
                    "supply evidence under which every failed predicate holds",
                    "re-propose with repaired evidence", predicate_results=results, **flag)
    else:
        out = allow(predicate_results=results, **flag)
    if out["disposition"] not in gov["disposition_vocabulary"]["dispositions"]:
        return _fc("SV011_ENTITY_DISPOSITION_OUTSIDE_VOCABULARY", "disposition is in the declared vocabulary", "restore the vocabulary")
    return out


def run(package_dir: str | Path, record_dir: str | Path, request_path: str | Path | None) -> dict[str, Any]:
    gov, bad = bootstrap.load(package_dir)
    if bad:
        return bad
    manifest = gov["manifest"]
    req, bad = _read_request(request_path)
    if bad:
        return bad
    if req.get("action") not in manifest["declared_actions"]:
        return _deny("SV011_ENTITY_ACTION_UNDECLARED", f"action in manifest declared_actions {manifest['declared_actions']}",
                     "use a declared action; the entity performs no undeclared action")
    rdir = Path(record_dir)
    chain, bad = record.read_all(rdir)
    if bad:
        return bad
    if req["action"] == "VERIFY":
        return record.verify(rdir)
    unknown = sorted(set(req) - set(REQUEST_FIELDS))
    missing = sorted(set(REQUEST_FIELDS) - set(req))
    if unknown or missing or req["schema"] != REQUEST_SCHEMA:
        return _fc("SV011_ENTITY_ACTION_REQUEST_SHAPE", f"request is {REQUEST_SCHEMA} with exactly {list(REQUEST_FIELDS)}; "
                   f"unknown {unknown}, missing {missing}", "fix the request fields")
    ev = req["evidence"]
    if not isinstance(ev, dict) or sorted(ev) != sorted(EVIDENCE_FIELDS):
        return _fc("SV011_ENTITY_EVIDENCE_SHAPE", f"evidence has exactly {list(EVIDENCE_FIELDS)}", "fix the evidence fields")
    if not isinstance(req["observed_at"], str) or not req["observed_at"].strip():
        return _fc("SV011_ENTITY_TIME_NOT_SUPPLIED", "observed_at is a supplied non-empty string", "supply observed_at")
    if req["transition_class"] not in manifest["declared_transition_classes"]:
        return _deny("SV011_ENTITY_TRANSITION_CLASS_UNDECLARED",
                     f"transition_class in {manifest['declared_transition_classes']}", "use a declared transition class")
    successor_sha = record.sha256_of(req["successor_state"])
    prior = next((r for r in chain if r["transition_id"] == req["transition_id"]), None)
    if prior is not None:
        if prior["successor_state_sha256"] == successor_sha and prior["predecessor_state_sha256"] == req["predecessor_state_sha256"]:
            return allow(transition="NONE", idempotent_replay=True, receipt_sha256=prior["receipt_sha256"])
        return _deny("SV011_ENTITY_TRANSITION_ID_REUSED", "transition_id is not reused for a different transition",
                     "use a new transition_id")
    if (req["transition_class"] == "GENESIS") != (not chain):
        return _deny("SV011_ENTITY_GENESIS_POSITION", "GENESIS is the first and only first transition",
                     "use GENESIS only on an empty record and ENTITY_STATE_UPDATE afterwards")
    current = chain[-1]["successor_state_sha256"] if chain else record.NULL_STATE_SHA256
    if req["predecessor_state_sha256"] != current:
        return _fc("SV011_ENTITY_STATE_STALE", f"predecessor_state_sha256 == current state {current}",
                   "re-propose against the current recorded state", "read current state via VERIFY and resubmit")
    proposal = evaluate(gov, req, current)
    proposal["proposal"] = {"transition_id": req["transition_id"], "predecessor_state_sha256": current,
                            "successor_state_sha256": successor_sha}
    if req["action"] == "PROPOSE" or proposal["disposition"] != "ALLOW":
        proposal["transitions_taken"] = 0
        return proposal
    rule = gov["transition_receipt_rule"]
    body = {
        "schema": rule["receipt_schema"],
        "repository": manifest["entity_id"],
        "transition_id": req["transition_id"],
        "transition_class": req["transition_class"],
        "predecessor_state_sha256": current,
        "successor_state_sha256": successor_sha,
        "evidence": {"request_evidence_sha256": record.sha256_of(ev), "predicate_results": proposal["predicate_results"],
                     "package_manifest_sha256": gov["manifest_sha256"]},
        "authority" + "_effect": "NONE",
        "observed_at": req["observed_at"],
        "disposition": "ALLOW",
    }
    missing_fields = [f for f in rule["required_fields"] if f not in body and f != "previous_receipt_sha256"]
    if missing_fields:
        return _fc("SV011_ENTITY_RECEIPT_FIELDS_UNSATISFIED", f"receipt carries contract required_fields; missing {missing_fields}",
                   "extend the receipt builder to the declared contract")
    receipt = record.append(rdir, chain, body, req["successor_state"])
    return allow(transition="APPLIED", transitions_taken=1, receipt_sha256=receipt["receipt_sha256"],
                 predicate_results=proposal["predicate_results"])
