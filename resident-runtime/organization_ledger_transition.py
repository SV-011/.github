#!/usr/bin/env python3
"""Apply one committed organization transition request.

Action is by manifest. The manifest is a JSON document committed under
``.stegverse/transition-requests/``. This module does not build an SDK
manifest: that builder is not in this repository. A request that asks for
``build_manifest`` or a prebuilt SDK ``manifest`` fails closed.

The request names the repository transition and, when it declares one, the
current-state fields ``state_id``, ``as_of``, ``fresh_until``,
``declared_transition_element``, ``declared_role_escalation``, and
``satisfied_scope_ids``. ``as_of`` and ``fresh_until`` must be
``YYYY-MM-DDTHH:MM:SSZ``. Those fields are copied onto the organization
receipt. A missing ``declared_*`` field is not derived here. Authority and
time stay constraint variables. A disposition on the resulting receipt is
evidence that the append happened.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
REQUEST_SCHEMA = "stegverse.organization-transition-request/v1"
SHA256_REF = re.compile(r"^sha256:[0-9a-f]{64}$")

_emit_spec = importlib.util.spec_from_file_location(
    "repository_ledger", ROOT / ".stegverse/transition-ledger/emit.py")
repository_ledger = importlib.util.module_from_spec(_emit_spec)
_emit_spec.loader.exec_module(repository_ledger)

_org_spec = importlib.util.spec_from_file_location(
    "organization_ledger", ROOT / "resident-runtime/aggregate_repo_transition.py")
organization_ledger = importlib.util.module_from_spec(_org_spec)
_org_spec.loader.exec_module(organization_ledger)


class TransitionRequestRefused(Exception):
    def __init__(self, failed_predicate: str, detail: str) -> None:
        super().__init__(failed_predicate + ": " + detail)
        self.failed_predicate = failed_predicate
        self.detail = detail


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise TransitionRequestRefused("TRANSITION_REQUEST_FIELD_PRESENT", name)
    return value


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or not SHA256_REF.match(value):
        raise TransitionRequestRefused("TRANSITION_REQUEST_STATE_IS_A_SHA256_DIGEST", name)
    return value


def validate_request(document: Mapping[str, Any]) -> dict[str, Any]:
    """Return the request this repository can append, or refuse it.

    SDK manifest construction is not a primitive here.
    """
    if not isinstance(document, Mapping):
        raise TransitionRequestRefused("TRANSITION_REQUEST_IS_AN_OBJECT", type(document).__name__)
    if "build_manifest" in document or "manifest" in document:
        raise TransitionRequestRefused(
            "SDK_MANIFEST_BUILDER_NOT_PRESENT",
            "this repository appends the committed transition fields; it does not build or admit an SDK manifest")
    if document.get("schema") != REQUEST_SCHEMA:
        raise TransitionRequestRefused("TRANSITION_REQUEST_SCHEMA", str(document.get("schema")))
    standing = document.get("standing")
    if not isinstance(standing, dict) or not standing:
        raise TransitionRequestRefused("TRANSITION_REQUEST_STANDING_PRESENT", "standing")
    _text(standing.get("mode"), "standing.mode")
    _text(standing.get("node_ref"), "standing.node_ref")
    evidence = document.get("evidence", {})
    if not isinstance(evidence, dict):
        raise TransitionRequestRefused("TRANSITION_REQUEST_EVIDENCE_IS_AN_OBJECT", "evidence")
    current = document.get("current_state")
    if current is not None:
        current = organization_ledger.normalize_current_state(current)
    hb_epoch = document.get("hb_epoch")
    if hb_epoch is not None and (not isinstance(hb_epoch, int) or isinstance(hb_epoch, bool)):
        raise TransitionRequestRefused("TRANSITION_REQUEST_HB_EPOCH_IS_AN_INTEGER", "hb_epoch")
    return {
        "schema": REQUEST_SCHEMA,
        "request_id": _text(document.get("request_id"), "request_id"),
        "owning_existing_goal": _text(document.get("owning_existing_goal"), "owning_existing_goal"),
        "standing": standing,
        "transition_id": _text(document.get("transition_id"), "transition_id"),
        "transition_class": _text(document.get("transition_class"), "transition_class"),
        "org_transition_class": _text(document.get("org_transition_class"), "org_transition_class"),
        "predecessor_state_sha256": _digest(document.get("predecessor_state_sha256"), "predecessor_state_sha256"),
        "successor_state_sha256": _digest(document.get("successor_state_sha256"), "successor_state_sha256"),
        "authority_constraint": _text(document.get("authority_constraint"), "authority_constraint"),
        "evidence": evidence,
        "current_state": current,
        "hb_epoch": hb_epoch,
    }


def validate_tree(directory: Path) -> list[str]:
    """Validate every committed request. An empty directory is valid."""
    if not directory.is_dir():
        return []
    names = []
    for path in sorted(directory.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        validate_request(document)
        names.append(str(path))
    return names


def apply_request(document: Mapping[str, Any]) -> dict[str, Any]:
    """Append the request to the repository chain and the organization chain.

    The ledger roots come from the environment. A missing root fails closed
    inside the emitters. Nothing here chooses a default location.
    """
    request = validate_request(document)
    evidence = dict(request["evidence"])
    evidence["request_id"] = request["request_id"]
    evidence["owning_existing_goal"] = request["owning_existing_goal"]
    evidence["standing"] = request["standing"]
    if request["current_state"] is not None:
        evidence["current_state"] = request["current_state"]
    repository_receipt = repository_ledger.append(
        request["transition_id"], request["transition_class"],
        request["predecessor_state_sha256"], request["successor_state_sha256"],
        evidence, request["authority_constraint"], hb_epoch=request["hb_epoch"],
        idempotent_on=tuple(sorted(evidence)))
    organization_receipt = organization_ledger.append(
        repository_receipt, request["org_transition_class"],
        request["predecessor_state_sha256"], request["successor_state_sha256"],
        {"request_id": request["request_id"],
         "owning_existing_goal": request["owning_existing_goal"],
         "transition_id": request["transition_id"]},
        request["authority_constraint"],
        hb_epoch=repository_receipt["hb_reference"]["epoch"],
        idempotent=True,
        current_state=request["current_state"])
    return {
        "schema": "stegverse.organization-ledger-transition-result/v1",
        "request_id": request["request_id"],
        "repository_receipt_sha256": repository_receipt["receipt_sha256"],
        "organization_receipt_sha256": organization_receipt["receipt_sha256"],
        "source_retained": organization_receipt["source_transition_sha256"] == repository_receipt["receipt_sha256"],
        "current_state_recorded": organization_receipt.get("current_state"),
        "authority_constraint": request["authority_constraint"],
        "time_constraint": "HEARTBEAT_EPOCH",
        "disposition_is_transition_evidence": True,
        "production_receipt_claimed": False,
    }


def _refusal(exc: Exception) -> dict[str, Any]:
    return {
        "schema": "stegverse.organization-ledger-transition-result/v1",
        "chain_verifies": False,
        "failed_predicate": getattr(exc, "failed_predicate", "TRANSITION_REQUEST_APPLIES"),
        "required_evidence_or_repair": getattr(exc, "detail", str(exc)),
        "retry_entrypoint": "resident-runtime/organization_ledger_transition.py",
        "next_attempt": "correct the committed transition request and push it again; nothing was appended for this request",
        "consequence_committed": False,
        "authority_constraint": "NONE_REFUSAL_ONLY",
        "time_constraint": "HEARTBEAT_EPOCH",
        "disposition_is_transition_evidence": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--request", type=Path, default=None)
    parser.add_argument("--validate-tree", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        if args.validate_tree is not None:
            names = validate_tree(args.validate_tree)
            print(json.dumps({"validated": names}, sort_keys=True))
            return 0
        if args.request is None:
            raise TransitionRequestRefused("TRANSITION_REQUEST_PATH_PRESENT", "--request")
        document = json.loads(args.request.read_text(encoding="utf-8"))
        result = apply_request(document)
    except (TransitionRequestRefused, ValueError, OSError) as exc:
        print(json.dumps(_refusal(exc), sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
