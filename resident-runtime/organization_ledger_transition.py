#!/usr/bin/env python3
"""Apply one committed organization transition request.

Action is by manifest. The manifest is a JSON document committed under
``.stegverse/transition-requests/``. This module does not build an SDK
manifest: that builder is not in this repository. A request that asks for
``build_manifest`` or a prebuilt SDK ``manifest`` fails closed.

The request names the repository transition and, when it declares one, a
``current_state`` in the shape of contract blob
``8f7d4cc2127325b155e1a91edfe531b5737f7c75``: ``state_id``, ``as_of``,
``fresh_until``, ``declared_transition_element`` (``transition_id`` and
``fixture_ref``), ``declared_role_escalation`` (``role_id``, ``from_role``,
``to_role``), and ``satisfied_scope_ids``. Those values are copied onto the
organization receipt. A missing ``declared_*`` field is not derived here.

Storage follows the ledger layout already on ``refs/stegverse/organization-ledger``:
the repository receipt is appended to ``repository-ledger/SV-011/.github`` on
that ref, and the organization receipt names it in ``repo_receipt_sha256``.
Existing receipts are not rewritten.
Authority and time stay constraint variables. A disposition on the resulting
receipt is evidence that the append happened.

Constraint set and route (DEFAULT in force (fail-closed))
--------------------------------------------------------------
``constraint_set.node_standing`` replaces the top-level ``standing`` (B1,
option B); ``manifested_route`` names the route bound in
``.stegverse/transition-ledger/org-contract.json`` (B5). Both are recorded on
the repository receipt; the route is also recorded on the organization receipt.

Dispositions
------------
An applied request records ``disposition: ALLOW`` in the evidence of both
receipts and in the result, as evidence that the append occurred. A request
that declares its own ``evidence.disposition`` is refused. A refused request is
``FAIL_CLOSED`` with the six fields of the Organization Role conformance
standard (``resident-runtime/non_allow.py``).

Refused requests are recorded (decided from invariants, 2026-10-18): every
action is a manifest-bound attempted state transition with a disposition
(StegVerse-org/.github docs/ORGANIZATION_ROLE_RUNTIME_REALITY_DEPLOYMENT.md,
Conformance standard item 1), and this repository's ingress already records
its refusals (``intr_destination_ingress.py``, ``refusal_recorded``), as the
shared component C6 does (StegVerse-Labs/.github#3225). A refused request that
is a JSON object, with both ledger roots bound, appends a refusal pair: a
repository receipt and an organization receipt of class
``ORGANIZATION_TRANSITION_REQUEST_REFUSAL`` whose predecessor and successor are
both the digest of the submitted request (the attempted transition left the
state unchanged), carrying ``disposition: FAIL_CLOSED`` and the six fields. The
organization head's ``current_state`` is carried onto the refusal receipt
unchanged, since the state did not change. Re-submitting the same refused
request returns the receipts already recorded. A document that is not a JSON
object, or unbound ledger roots, leave no receipt; ``refusal_recorded_reason``
says which. Revert: drop ``record_refusal`` from ``main``.
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

_non_allow_spec = importlib.util.spec_from_file_location(
    "non_allow_transition", ROOT / "resident-runtime/non_allow.py")
non_allow = importlib.util.module_from_spec(_non_allow_spec)
_non_allow_spec.loader.exec_module(non_allow)

APPLIED = "ALLOW"
REFUSED = "FAIL_CLOSED"
REFUSAL_FAILURE_CODE = "ORGANIZATION_TRANSITION_REQUEST_REFUSED"
RETRY_ENTRYPOINT = (".stegverse/transition-requests/ (push to main; "
                    ".github/workflows/organization-ledger-transition.yml job request-transition)")


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


# DEFAULT in force (fail-closed) (B1, constraint_set Option B; see
# docs/ORGANIZATION_ROLE_CONFORMANCE.md "Defaults in force (fail-closed)").
# The node standing vocabulary follows StegVerse-org/.github@c483d441
# docs/CANONICAL_NODE_INGRESS_CONTRACT_001.json standing_modes: the predecessor
# key is required, null only with ESTABLISH_GENESIS. VERIFY_EXISTING needs the
# SDK successor_predecessor_binding, which is not in this repository, so it is
# refused rather than checked.
REQUEST_FIELDS = (
    "schema", "request_id", "owning_existing_goal", "purpose", "constraint_set", "manifested_route",
    "transition_id", "transition_class", "org_transition_class", "predecessor_state_sha256",
    "successor_state_sha256", "authority_constraint", "evidence", "current_state", "hb_epoch",
)
NODE_STANDING_MODES = ("ESTABLISH_GENESIS", "VERIFY_EXISTING")
DECLARED_VALUES = ("satisfies", "does_not_satisfy")
DECLARED_KEYS = ("authority", "time")
ROUTE_FIELDS = ("capability_profile_id", "route_id", "destination_repository",
                "destination_operation", "destination_ref")
CONTRACT = ROOT / ".stegverse/transition-ledger/org-contract.json"


def validate_constraint_set(value: Any) -> dict[str, Any]:
    """constraint_set.node_standing plus an optional declared block, or a refusal."""
    if not isinstance(value, dict):
        raise TransitionRequestRefused("TRANSITION_REQUEST_CONSTRAINT_SET_PRESENT", "constraint_set")
    outside = sorted(name for name in value if name not in ("node_standing", "declared"))
    if outside:
        raise TransitionRequestRefused("TRANSITION_REQUEST_FIELD_OUTSIDE_THE_CONTRACT",
                                       ",".join("constraint_set." + name for name in outside))
    standing = value.get("node_standing")
    if not isinstance(standing, dict):
        raise TransitionRequestRefused("TRANSITION_REQUEST_CONSTRAINT_SET_PRESENT", "constraint_set.node_standing")
    outside = sorted(name for name in standing if name not in ("mode", "node_ref", "predecessor"))
    if outside:
        raise TransitionRequestRefused("TRANSITION_REQUEST_FIELD_OUTSIDE_THE_CONTRACT",
                                       ",".join("constraint_set.node_standing." + name for name in outside))
    mode = standing.get("mode")
    if mode not in NODE_STANDING_MODES:
        raise TransitionRequestRefused("NODE_STANDING_MODE_IS_DECLARED", "constraint_set.node_standing.mode")
    _text(standing.get("node_ref"), "constraint_set.node_standing.node_ref")
    if "predecessor" not in standing:
        raise TransitionRequestRefused("NODE_STANDING_PREDECESSOR_KEY_PRESENT",
                                       "constraint_set.node_standing.predecessor")
    if mode == "VERIFY_EXISTING":
        raise TransitionRequestRefused(
            "PREDECESSOR_BINDING_PRIMITIVE_NOT_PRESENT",
            "VERIFY_EXISTING needs the SDK successor_predecessor_binding, which this repository does not hold")
    if standing["predecessor"] is not None:
        raise TransitionRequestRefused("NODE_STANDING_GENESIS_PREDECESSOR_IS_NULL",
                                       "constraint_set.node_standing.predecessor")
    result = {"node_standing": {"mode": mode, "node_ref": standing["node_ref"], "predecessor": None}}
    if "declared" in value:
        declared = value["declared"]
        if not isinstance(declared, dict) or any(
                name not in DECLARED_KEYS or declared[name] not in DECLARED_VALUES for name in declared):
            raise TransitionRequestRefused("CONSTRAINT_SET_DECLARED_VALUES", "constraint_set.declared")
        # Stored as declared input only; nothing here verifies it.
        result["declared"] = dict(declared)
    return result


def bound_route() -> dict[str, Any]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    return dict(contract["transition_request_intake"]["manifested_route_binding"])


def validate_manifested_route(value: Any) -> dict[str, Any]:
    """DEFAULT in force (fail-closed) (B5): the request names the bound route."""
    if not isinstance(value, dict):
        raise TransitionRequestRefused("REQUEST_NAMES_A_MANIFESTED_CAPABILITY_AND_ROUTE", "manifested_route")
    route = {name: _text(value.get(name), "manifested_route." + name) for name in ROUTE_FIELDS}
    if sorted(value) != sorted(ROUTE_FIELDS):
        raise TransitionRequestRefused("TRANSITION_REQUEST_FIELD_OUTSIDE_THE_CONTRACT", "manifested_route")
    binding = {name: bound_route().get(name) for name in ROUTE_FIELDS}
    if route != binding:
        raise TransitionRequestRefused("ROUTE_NOT_BOUND_IN_THIS_ORGANIZATION", json.dumps(route, sort_keys=True))
    return route


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
    if "standing" in document:
        raise TransitionRequestRefused(
            "TRANSITION_REQUEST_STANDING_REPLACED_BY_CONSTRAINT_SET",
            "top-level standing moved to constraint_set.node_standing (DEFAULT in force (fail-closed), B1)")
    outside = sorted(name for name in document if name not in REQUEST_FIELDS)
    if outside:
        raise TransitionRequestRefused("TRANSITION_REQUEST_FIELD_OUTSIDE_THE_CONTRACT", ",".join(outside))
    constraint_set = validate_constraint_set(document.get("constraint_set"))
    route = validate_manifested_route(document.get("manifested_route"))
    evidence = document.get("evidence", {})
    if not isinstance(evidence, dict):
        raise TransitionRequestRefused("TRANSITION_REQUEST_EVIDENCE_IS_AN_OBJECT", "evidence")
    if "disposition" in evidence:
        raise TransitionRequestRefused(
            "TRANSITION_REQUEST_DOES_NOT_DECLARE_ITS_OWN_DISPOSITION",
            "evidence.disposition is written by the append as evidence that it occurred; remove it from the request")
    current = document.get("current_state")
    if current is not None:
        try:
            current = organization_ledger.normalize_current_state(current)
        except ValueError as exc:
            raise TransitionRequestRefused("TRANSITION_REQUEST_CURRENT_STATE_IS_A_BASIS", str(exc)) from None
    hb_epoch = document.get("hb_epoch")
    if hb_epoch is not None and (not isinstance(hb_epoch, int) or isinstance(hb_epoch, bool)):
        raise TransitionRequestRefused("TRANSITION_REQUEST_HB_EPOCH_IS_AN_INTEGER", "hb_epoch")
    return {
        "schema": REQUEST_SCHEMA,
        "request_id": _text(document.get("request_id"), "request_id"),
        "owning_existing_goal": _text(document.get("owning_existing_goal"), "owning_existing_goal"),
        "constraint_set": constraint_set,
        "manifested_route": route,
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
        try:
            validate_request(json.loads(path.read_text(encoding="utf-8")))
        except TransitionRequestRefused as exc:
            raise TransitionRequestRefused(exc.failed_predicate, str(path) + ": " + exc.detail) from None
        except ValueError as exc:
            raise TransitionRequestRefused("TRANSITION_REQUEST_IS_JSON", str(path) + ": " + str(exc)) from None
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
    evidence["constraint_set"] = request["constraint_set"]
    evidence["manifested_route"] = request["manifested_route"]
    evidence["disposition"] = APPLIED
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
         "transition_id": request["transition_id"],
         "manifested_route": request["manifested_route"],
         "disposition": APPLIED},
        request["authority_constraint"],
        hb_epoch=repository_receipt["hb_reference"]["epoch"],
        idempotent=True,
        current_state=request["current_state"])
    return {
        "schema": "stegverse.organization-ledger-transition-result/v1",
        "disposition": APPLIED,
        "request_id": request["request_id"],
        "owning_existing_goal": request["owning_existing_goal"],
        "repository_receipt_sha256": repository_receipt["receipt_sha256"],
        "organization_receipt_sha256": organization_receipt["receipt_sha256"],
        "organization_receipt_preserves_repository_receipt":
            organization_receipt["repo_receipt_sha256"] == repository_receipt["receipt_sha256"],
        "current_state_recorded": organization_receipt.get("current_state"),
        "authority_constraint": request["authority_constraint"],
        "time_constraint": "HEARTBEAT_EPOCH",
        "disposition_is_transition_evidence": True,
        "production_receipt_claimed": False,
    }


def _declared_goal(document: Any) -> str:
    """The request's own owning goal when it names one, else this intake's goal."""
    if isinstance(document, Mapping):
        goal = document.get("owning_existing_goal")
        if isinstance(goal, str) and goal.strip():
            return goal
    return non_allow.LEDGER_GOAL


def _refusal(exc: Exception, document: Any = None) -> dict[str, Any]:
    predicate = getattr(exc, "failed_predicate", "TRANSITION_REQUEST_APPLIES")
    detail = getattr(exc, "detail", str(exc))
    request_id = document.get("request_id") if isinstance(document, Mapping) else None
    return {
        "schema": "stegverse.organization-ledger-transition-result/v1",
        **non_allow.result(
            REFUSED,
            failure_code=REFUSAL_FAILURE_CODE,
            failed_predicate=predicate,
            required_evidence_or_repair=detail,
            retry_entrypoint=RETRY_ENTRYPOINT,
            owning_existing_goal=_declared_goal(document),
            next_attempt="correct the committed transition request and push it again; nothing was appended for this request"),
        "request_id": request_id if isinstance(request_id, str) else None,
        "chain_verifies": False,
        "consequence_committed": False,
        "refusal_recorded": False,
        "refusal_recorded_reason": "REFUSED_BEFORE_THE_APPEND_NO_RECEIPT_WRITTEN",
        "authority_constraint": "NONE_REFUSAL_ONLY",
        "time_constraint": "HEARTBEAT_EPOCH",
        "disposition_is_transition_evidence": False,
        "production_receipt_claimed": False,
    }


REFUSAL_TRANSITION_CLASS = "ORGANIZATION_TRANSITION_REQUEST_REFUSAL"


def record_refusal(document: Any, refusal: dict[str, Any]) -> dict[str, Any]:
    """Append the refused attempt as a FAIL_CLOSED receipt pair; state unchanged."""
    if not isinstance(document, Mapping):
        return dict(refusal, refusal_recorded_reason="REQUEST_IS_NOT_A_JSON_OBJECT_NO_RECEIPT_WRITTEN")
    if not (os.environ.get("STEGVERSE_ORG_LEDGER_ROOT") and os.environ.get("STEGVERSE_REPO_LEDGER_ROOT")):
        return dict(refusal, refusal_recorded_reason="LEDGER_ROOTS_NOT_BOUND_NO_RECEIPT_WRITTEN")
    request_sha = organization_ledger.sha(dict(document))
    request_id = document.get("request_id") if isinstance(document.get("request_id"), str) else None
    transition_id = "REFUSAL:" + request_sha
    six = {name: refusal[name] for name in non_allow.SIX_FIELDS}
    evidence = {"disposition": REFUSED, "request_id": request_id, "request_sha256": request_sha, **six}
    try:
        repository_receipt = repository_ledger.append(
            transition_id, REFUSAL_TRANSITION_CLASS, request_sha, request_sha, evidence,
            "NONE_REFUSAL_ONLY", idempotent_on=tuple(sorted(evidence)))
        store = organization_ledger.ledger_store.open_store(organization_ledger.ledger_root())
        head = store.get(organization_ledger.ledger_store.HEAD_KEY)
        head_receipt = (store.get(organization_ledger.ledger_store.receipt_key(head["receipt_sha256"]))
                        if isinstance(head, dict) and isinstance(head.get("receipt_sha256"), str) else None)
        carried = head_receipt.get("current_state") if isinstance(head_receipt, dict) else None
        organization_receipt = organization_ledger.append(
            repository_receipt, REFUSAL_TRANSITION_CLASS, request_sha, request_sha,
            {"request_id": request_id, "request_sha256": request_sha, "transition_id": transition_id,
             "disposition": REFUSED, "failure_code": six["failure_code"],
             "failed_predicate": six["failed_predicate"]},
            "NONE_REFUSAL_ONLY", hb_epoch=repository_receipt["hb_reference"]["epoch"],
            idempotent=True, current_state=carried)
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        return dict(refusal, refusal_recorded_reason="REFUSAL_APPEND_FAILED: " + str(exc))
    return dict(
        refusal,
        refusal_recorded=True,
        refusal_recorded_reason="ATTEMPTED_TRANSITION_RECORDED_STATE_UNCHANGED",
        request_sha256=request_sha,
        refusal_repository_receipt_sha256=repository_receipt["receipt_sha256"],
        refusal_organization_receipt_sha256=organization_receipt["receipt_sha256"],
        current_state_carried=carried is not None,
        disposition_is_transition_evidence=True,
        next_attempt=("correct the committed transition request and push it again; the refusal is recorded at "
                      + organization_receipt["receipt_sha256"] + " and the requested consequence was not committed"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--request", type=Path, default=None)
    parser.add_argument("--validate-tree", type=Path, default=None)
    args = parser.parse_args(argv)
    document: Any = None
    try:
        if args.validate_tree is not None:
            names = validate_tree(args.validate_tree)
            print(json.dumps({"validated": names}, sort_keys=True))
            return 0
        if args.request is None:
            raise TransitionRequestRefused("TRANSITION_REQUEST_PATH_PRESENT", "--request")
        document = json.loads(args.request.read_text(encoding="utf-8"))
        result = apply_request(document)
    except (TransitionRequestRefused, ValueError, OSError, RuntimeError) as exc:
        refusal = _refusal(exc, document)
        if args.request is not None and args.validate_tree is None:
            refusal = record_refusal(document, refusal)
        print(json.dumps(refusal, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
