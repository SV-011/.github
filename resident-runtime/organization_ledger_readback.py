#!/usr/bin/env python3
"""Read the organization ledger back from the location the caller supplies.

A workflow run reads ``refs/stegverse/organization-ledger`` from a fresh
fetch. The readback walks the organization chain and the repository subtree of
that same ref and checks each receipt against its own body. Given an
intr-destination closure result, it checks that the closure's repository
receipt, organization receipt, and ``communication_id`` are on those chains.
Given a transition-request result, it checks that the request's two receipts
are on those chains.

Current-state basis
-------------------
``current_state`` is taken only from the organization head receipt's
``current_state`` object. The shape is the Current-state basis section of
``Admissible-Existence/TT`` commit ``ba3cca71b6928cf739ab6b321c939860fd1edbb1``
``docs/SV_011_NEXT_REQUIRED_CAPABILITY_DERIVATION_CONTRACT.md`` (git blob
``8f7d4cc2127325b155e1a91edfe531b5737f7c75``). A complete basis has exactly:

- ``state_id``: identifier ``^[A-Za-z0-9._:-]{1,128}$``
- ``as_of`` and ``fresh_until``: ``YYYY-MM-DDTHH:MM:SSZ``; fresh when
  ``as_of <= fresh_until``
- ``declared_transition_element``: ``transition_id`` (``T-`` and three
  digits) and ``fixture_ref`` (``fixtures/transition-elements/<name>.json``,
  ``<name>`` in ``[a-z0-9.-]+``, the TT engine's pattern)
- ``declared_role_escalation``: ``role_id``, ``from_role``, ``to_role``,
  identifiers, with ``from_role != to_role``
- ``satisfied_scope_ids``: a duplicate-free prefix of
  ``["transition_element", "role_escalation"]``
- ``basis_digest``: lowercase hex SHA-256 of the UTF-8 ``json.dumps`` of
  ``current_state`` without ``basis_digest`` (``sort_keys=True``,
  ``separators=(",", ":")``, ``ensure_ascii=False``). No ``sha256:`` prefix.

The fixture file named by ``fixture_ref`` lives in the TT tree, not in this
repository. The readback checks the path pattern and copies the declaration.
It does not open the fixture or compare its ``transition_id``; the TT engine
does that against its own root, and a missing fixture there is TT's
``BASIS_UNKNOWN``. The readback reports this as ``fixture_ref_resolution``.

A head receipt without ``current_state`` is ``BASIS_ABSENT`` with
``current_state_detail`` ``HEAD_RECEIPT_DECLARES_NO_CURRENT_STATE``. A missing
field is ``BASIS_ABSENT``. A wrong type or pattern, an unparseable time, or a
scope id outside the manifest scope is ``BASIS_UNKNOWN``. A later ``as_of`` is
``BASIS_STALE``. Equal roles, a duplicated or non-prefix scope list, or a
digest that does not match is ``BASIS_CONTRADICTORY``. In each case
``basis_established`` is false and ``current_state`` is null. Nothing is
copied from an earlier receipt, the clock, or a default.

Observation only
----------------
A readback records no transition, so its output carries no disposition. A
disposition is evidence that a state transition occurred, and it stays inside
the receipt that recorded that transition. The output reports two things
separately: ``verification`` (the chains read back and each receipt verifies
against its own body) and the current-state basis (``basis_established``,
``current_state_reason``). A verified chain whose head declares no basis
reports ``BASIS_ABSENT``; that is never turned into a current state.

The readback does not select admission, routing, or execution. Authority and
time stay constraint variables. This module does not claim an authentic
production receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

SOURCE = Path(__file__).resolve().parents[1]
LEDGER_REF = "refs/stegverse/organization-ledger"
LEDGER_REPOSITORY = "SV-011/.github"
RETRY = "resident-runtime/organization_ledger_readback.py::main"

# The six fields covered by basis_digest. basis_digest itself is the seventh.
BASIS_INPUT_FIELDS = (
    "state_id",
    "as_of",
    "fresh_until",
    "declared_transition_element",
    "declared_role_escalation",
    "satisfied_scope_ids",
)
MANIFEST_SCOPE_IDS = ("transition_element", "role_escalation")
IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
TRANSITION_ID = re.compile(r"^T-[0-9]{3}$")
FIXTURE_REF = re.compile(r"^fixtures/transition-elements/[a-z0-9.-]+\.json$")
UTC_TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
TRANSITION_ELEMENT_FIELDS = ("fixture_ref", "transition_id")
ROLE_ESCALATION_FIELDS = ("from_role", "role_id", "to_role")
FIXTURE_REF_RESOLUTION = {
    "checked_here": "PATH_PATTERN_ONLY",
    "fixture_opened": False,
    "resolved_by": "Admissible-Existence/TT engine/sv011_next_required_capability.py against its own root",
    "missing_fixture_at_that_root": "BASIS_UNKNOWN",
    "fixture_transition_id_mismatch_at_that_root": "BASIS_CONTRADICTORY",
}

_spec = importlib.util.spec_from_file_location(
    "ledger_store_readback", SOURCE / "resident-runtime/ledger_store.py")
ledger_store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ledger_store)


def canon(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canon(value)).hexdigest()


def _body_sha(document: Mapping[str, Any]) -> str:
    body = {key: value for key, value in document.items() if key != "receipt_sha256"}
    return sha(body)


def basis_digest(subject: Mapping[str, Any]) -> str:
    """Lowercase hex SHA-256 of current_state with basis_digest removed."""
    payload = {key: value for key, value in subject.items() if key != "basis_digest"}
    preimage = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(preimage.encode("utf-8")).hexdigest()


def _incomplete(reason: str, fields: list[str] | tuple[str, ...], detail: str) -> dict[str, Any]:
    return {
        "basis_established": False,
        "current_state": None,
        "current_state_reason": reason,
        "current_state_detail": detail,
        "current_state_fields": list(fields),
        "fixture_ref_resolution": dict(FIXTURE_REF_RESOLUTION),
    }


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or UTC_TIMESTAMP.fullmatch(value) is None:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def _copy_transition_element(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict) or set(value) != set(TRANSITION_ELEMENT_FIELDS):
        return None
    transition_id = value.get("transition_id")
    fixture_ref = value.get("fixture_ref")
    if not isinstance(transition_id, str) or TRANSITION_ID.fullmatch(transition_id) is None:
        return None
    if not isinstance(fixture_ref, str) or FIXTURE_REF.fullmatch(fixture_ref) is None:
        return None
    return {"transition_id": transition_id, "fixture_ref": fixture_ref}


def _copy_role_escalation(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict) or set(value) != set(ROLE_ESCALATION_FIELDS):
        return None
    copied = {}
    for name in ROLE_ESCALATION_FIELDS:
        item = value.get(name)
        if not isinstance(item, str) or IDENTIFIER.fullmatch(item) is None:
            return None
        copied[name] = item
    return copied


def project_current_state(head_receipt: Any) -> dict[str, Any]:
    """The head receipt's current_state, or the contract reason it is not a basis.

    Every value comes from this one receipt. A missing declaration is not
    filled in from an older receipt, the clock, or a default.
    """
    if not isinstance(head_receipt, dict) or "current_state" not in head_receipt:
        return _incomplete("BASIS_ABSENT", BASIS_INPUT_FIELDS, "HEAD_RECEIPT_DECLARES_NO_CURRENT_STATE")
    declared = head_receipt["current_state"]
    if not isinstance(declared, dict):
        return _incomplete("BASIS_UNKNOWN", ["current_state"], "CURRENT_STATE_IS_NOT_AN_OBJECT")
    missing = [name for name in BASIS_INPUT_FIELDS if declared.get(name) is None]
    if missing:
        return _incomplete("BASIS_ABSENT", missing, "CURRENT_STATE_FIELD_ABSENT")
    unknown = sorted(name for name in declared if name not in BASIS_INPUT_FIELDS and name != "basis_digest")
    if unknown:
        return _incomplete("BASIS_UNKNOWN", unknown, "CURRENT_STATE_FIELD_OUTSIDE_THE_CONTRACT")
    if not isinstance(declared["state_id"], str) or IDENTIFIER.fullmatch(declared["state_id"]) is None:
        return _incomplete("BASIS_UNKNOWN", ["state_id"], "STATE_ID_PATTERN")
    as_of = _parse_utc(declared["as_of"])
    fresh_until = _parse_utc(declared["fresh_until"])
    bad_times = [name for name, parsed in (("as_of", as_of), ("fresh_until", fresh_until)) if parsed is None]
    if bad_times:
        return _incomplete("BASIS_UNKNOWN", bad_times, "TIMESTAMP_FORMAT")
    if "basis_digest" in declared and (
            not isinstance(declared["basis_digest"], str)
            or HEX_SHA256.fullmatch(declared["basis_digest"]) is None):
        return _incomplete("BASIS_UNKNOWN", ["basis_digest"], "BASIS_DIGEST_FORMAT")
    element = _copy_transition_element(declared["declared_transition_element"])
    if element is None:
        return _incomplete("BASIS_UNKNOWN", ["declared_transition_element"], "TRANSITION_ELEMENT_SHAPE_OR_FIXTURE_REF_PATTERN")
    role = _copy_role_escalation(declared["declared_role_escalation"])
    if role is None:
        return _incomplete("BASIS_UNKNOWN", ["declared_role_escalation"], "ROLE_ESCALATION_SHAPE")
    scopes = declared["satisfied_scope_ids"]
    if not isinstance(scopes, list) or any(not isinstance(item, str) for item in scopes):
        return _incomplete("BASIS_UNKNOWN", ["satisfied_scope_ids"], "SATISFIED_SCOPE_IDS_SHAPE")
    if any(item not in MANIFEST_SCOPE_IDS for item in scopes):
        return _incomplete("BASIS_UNKNOWN", ["satisfied_scope_ids"], "SCOPE_ID_OUTSIDE_THE_MANIFEST_SCOPE")
    if as_of > fresh_until:
        return _incomplete("BASIS_STALE", ["as_of", "fresh_until"], "AS_OF_AFTER_FRESH_UNTIL")
    if role["from_role"] == role["to_role"]:
        return _incomplete("BASIS_CONTRADICTORY", ["declared_role_escalation"], "FROM_ROLE_EQUALS_TO_ROLE")
    if len(scopes) != len(set(scopes)) or list(scopes) != list(MANIFEST_SCOPE_IDS[:len(scopes)]):
        return _incomplete("BASIS_CONTRADICTORY", ["satisfied_scope_ids"], "SATISFIED_SCOPE_IDS_NOT_A_MANIFEST_PREFIX")
    state = {
        "state_id": declared["state_id"],
        "as_of": declared["as_of"],
        "fresh_until": declared["fresh_until"],
        "declared_transition_element": element,
        "declared_role_escalation": role,
        "satisfied_scope_ids": list(scopes),
    }
    state["basis_digest"] = basis_digest(state)
    if "basis_digest" in declared and declared["basis_digest"] != state["basis_digest"]:
        return _incomplete("BASIS_CONTRADICTORY", ["basis_digest"], "BASIS_DIGEST_MISMATCH")
    return {
        "basis_established": True,
        "current_state": state,
        "current_state_reason": "HEAD_RECEIPT_CURRENT_STATE",
        "current_state_detail": None,
        "current_state_fields": [],
        "fixture_ref_resolution": dict(FIXTURE_REF_RESOLUTION),
    }


def empty_ledger_report(location: str) -> dict[str, Any]:
    """No HEAD at the location: no state id, no times, no scopes, no digest."""
    return {
        "schema": "stegverse.organization-ledger-readback/v1",
        "location": location,
        "ledger_empty": True,
        "readback": "OBSERVATION_ONLY",
        "records_no_transition": True,
        "verification": {"organization_chain_verifies": False, "repository_chain_verifies": False},
        "chain_length": 0,
        "authority_constraint": "NONE_READBACK_ONLY",
        "time_constraint": "HEARTBEAT_EPOCH",
        "production_receipt_claimed": False,
        **_incomplete("BASIS_ABSENT", BASIS_INPUT_FIELDS, "ORGANIZATION_LEDGER_EMPTY"),
    }


class ReadbackRefused(Exception):
    def __init__(self, failed_predicate: str, detail: str) -> None:
        super().__init__(failed_predicate + ": " + detail)
        self.failed_predicate = failed_predicate
        self.detail = detail


def _load(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ReadbackRefused("LEDGER_CONTRACT_IS_AN_OBJECT", str(path))
    return document


def contracts(workspace: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(workspace)
    return (_load(root / ".stegverse/transition-ledger/contract.json"),
            _load(root / ".stegverse/transition-ledger/org-contract.json"))


def designated_locators(workspace: Path) -> dict[str, Any]:
    """The published ref and the repository subtree, taken from the contracts.

    The returned locators name ``workspace``. Calling this does not open a
    store and does not append.
    """
    contract, org = contracts(workspace)
    location = contract.get("repository_ledger_location")
    location = location if isinstance(location, dict) else {}
    ref = org.get("organization_ledger_ref")
    namespace = location.get("namespace")
    problems = []
    if org.get("organization") != "SV-011" or org.get("repository") != LEDGER_REPOSITORY:
        problems.append("organization")
    if org.get("organization_ledger_repository") != LEDGER_REPOSITORY:
        problems.append("organization_ledger_repository")
    if contract.get("repository") != LEDGER_REPOSITORY or location.get("repository") != LEDGER_REPOSITORY:
        problems.append("repository")
    if ref != LEDGER_REF or location.get("ref") != LEDGER_REF:
        problems.append("ref")
    if location.get("new_ledger_root") is not False:
        problems.append("new_ledger_root")
    if namespace != "repository-ledger/" + LEDGER_REPOSITORY:
        problems.append("namespace")
    closure = org.get("diagnostic_self_closure")
    if not isinstance(closure, dict) or closure.get("correlates_on") != "communication_id":
        problems.append("diagnostic_self_closure")
    if problems:
        raise ReadbackRefused("LEDGER_CONTRACT_NAMES_THIS_ORGANIZATION", ",".join(problems))
    root = Path(workspace).resolve()
    return {
        "ref": ref,
        "namespace": namespace,
        "organization_locator": "git+" + str(root) + "#" + str(ref),
        "repository_locator": "git+" + str(root) + "#" + str(ref) + ":" + str(namespace),
        "authority_constraint": "NONE_LOCATOR_ONLY",
        "time_constraint": "HEARTBEAT_EPOCH",
        "records_no_transition": True,
        "production_receipt_claimed": False,
    }


def _repository_location(location: str, namespace: str) -> str:
    locator = ledger_store.parse_locator(location)
    if isinstance(locator, ledger_store.GitLocator):
        if locator.namespace:
            raise ReadbackRefused("ORGANIZATION_LOCATION_IS_THE_REF_ROOT", str(locator))
        return str(locator) + ":" + namespace
    return str(Path(locator) / namespace)


def _verify_chain(store: Any, schema: str, required: list[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    head = store.get(ledger_store.HEAD_KEY)
    if not isinstance(head, dict) or not isinstance(head.get("receipt_sha256"), str):
        raise ReadbackRefused("LEDGER_HEAD_PRESENT", "no HEAD at the supplied location")
    chain: list[dict[str, Any]] = []
    cursor = head["receipt_sha256"]
    seen: set[str] = set()
    while cursor is not None:
        if cursor in seen:
            raise ReadbackRefused("LEDGER_CHAIN_IS_ACYCLIC", str(cursor))
        seen.add(cursor)
        receipt = store.get(ledger_store.receipt_key(cursor))
        if not isinstance(receipt, dict):
            raise ReadbackRefused("LEDGER_CHAIN_IS_COMPLETE", "missing " + str(cursor))
        if receipt.get("schema") != schema:
            raise ReadbackRefused("RECEIPT_SCHEMA_MATCHES_THE_CONTRACT", str(cursor))
        if receipt.get("receipt_sha256") != cursor or _body_sha(receipt) != cursor:
            raise ReadbackRefused("RECEIPT_VERIFIES_AGAINST_ITS_BODY", str(cursor))
        missing = [field for field in required if field not in receipt]
        if missing:
            raise ReadbackRefused("RECEIPT_HAS_THE_CONTRACT_FIELDS", ",".join(missing))
        if receipt.get("disposition_is_transition_evidence") is not True:
            raise ReadbackRefused("DISPOSITION_IS_TRANSITION_EVIDENCE", str(cursor))
        if not isinstance(receipt.get("authority_constraint"), str):
            raise ReadbackRefused("AUTHORITY_IS_A_CONSTRAINT_VARIABLE", str(cursor))
        if receipt.get("time_constraint") != "HEARTBEAT_EPOCH":
            raise ReadbackRefused("TIME_IS_A_CONSTRAINT_VARIABLE", str(cursor))
        if receipt.get("production_receipt_claimed") is not False:
            raise ReadbackRefused("PRODUCTION_RECEIPT_CLAIM_IS_ABSENT", str(cursor))
        chain.append(receipt)
        cursor = receipt.get("previous_receipt_sha256")
    chain.reverse()
    return head, chain


def _communication_id(receipt: Mapping[str, Any]) -> Any:
    evidence = receipt.get("evidence")
    if isinstance(evidence, dict):
        return evidence.get("communication_id")
    return None


def _transition_result_in_chains(transition_result: Mapping[str, Any],
                                 repo_chain: list[dict[str, Any]],
                                 org_chain: list[dict[str, Any]]) -> dict[str, Any]:
    """Check that one applied transition request is on both chains."""
    request_id = transition_result.get("request_id")
    repo_sha = transition_result.get("repository_receipt_sha256")
    org_sha = transition_result.get("organization_receipt_sha256")
    if not isinstance(request_id, str) or not request_id:
        raise ReadbackRefused("TRANSITION_RESULT_CARRIES_REQUEST_ID", repr(request_id))
    repository_receipt = next((item for item in repo_chain if item.get("receipt_sha256") == repo_sha), None)
    organization_receipt = next((item for item in org_chain if item.get("receipt_sha256") == org_sha), None)
    if repository_receipt is None:
        raise ReadbackRefused("REQUEST_REPOSITORY_RECEIPT_IS_IN_THE_REPOSITORY_LEDGER", str(repo_sha))
    if organization_receipt is None:
        raise ReadbackRefused("REQUEST_ORGANIZATION_RECEIPT_IS_IN_THE_ORGANIZATION_LEDGER", str(org_sha))
    if organization_receipt.get("repo_receipt_sha256") != repo_sha:
        raise ReadbackRefused("ORGANIZATION_RECEIPT_PRESERVES_REPOSITORY_RECEIPT", str(org_sha))
    for receipt, predicate in ((repository_receipt, "REPOSITORY_RECEIPT_CARRIES_REQUEST_ID"),
                               (organization_receipt, "ORGANIZATION_RECEIPT_CARRIES_REQUEST_ID")):
        evidence = receipt.get("evidence")
        if not isinstance(evidence, dict) or evidence.get("request_id") != request_id:
            raise ReadbackRefused(predicate, request_id)
    return {
        "request_id": request_id,
        "request_repository_receipt_sha256": repo_sha,
        "request_organization_receipt_sha256": org_sha,
        "request_receipts_in_chain": True,
        "request_receipt_is_head": org_chain[-1].get("receipt_sha256") == org_sha,
    }


def readback(location: str, ingress_result: Mapping[str, Any] | None = None,
             repository_namespace: str | None = None,
             transition_result: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Verify the chains at ``location`` and, when given, a closure or request result.

    The organization head receipt's ``current_state`` is projected against the
    TT current-state basis. A location with no HEAD and no result to check is
    an empty ledger, reported as such.
    """
    contract, org = contracts(SOURCE)
    expected_namespace = (contract.get("repository_ledger_location") or {}).get("namespace")
    if repository_namespace != expected_namespace:
        raise ReadbackRefused("REPOSITORY_NAMESPACE_MATCHES_THE_CONTRACT", str(repository_namespace))
    org_required = list(org.get("required_fields") or [])
    repo_required = list(contract.get("required_fields") or [])
    organization_store = ledger_store.open_store(ledger_store.parse_locator(location))
    repository_store = ledger_store.open_store(_repository_location(location, repository_namespace))
    if (ingress_result is None and transition_result is None
            and not isinstance(organization_store.get(ledger_store.HEAD_KEY), dict)):
        return empty_ledger_report(location)
    org_head, org_chain = _verify_chain(
        organization_store, "stegverse.organization-transition-receipt/v1", org_required)
    repo_head, repo_chain = _verify_chain(
        repository_store, "stegverse.repo-transition-receipt/v1", repo_required)
    repo_digests = {receipt["receipt_sha256"] for receipt in repo_chain}
    for receipt in org_chain:
        if receipt.get("repo_receipt_sha256") not in repo_digests:
            raise ReadbackRefused("ORGANIZATION_RECEIPT_PRESERVES_REPOSITORY_RECEIPT",
                                  str(receipt.get("receipt_sha256")))
    result: dict[str, Any] = {
        "schema": "stegverse.organization-ledger-readback/v1",
        "location": location,
        "readback": "OBSERVATION_ONLY",
        "records_no_transition": True,
        "verification": {
            "organization_chain_verifies": True,
            "repository_chain_verifies": True,
            "every_organization_receipt_preserves_a_repository_receipt": True,
        },
        "head_receipt_sha256": org_head["receipt_sha256"],
        "chain_length": len(org_chain),
        "genesis_receipt_sha256": org_chain[0]["receipt_sha256"],
        "every_receipt_verifies_against_its_body": True,
        "authority_constraint": "NONE_READBACK_ONLY",
        "time_constraint": "HEARTBEAT_EPOCH",
        "production_receipt_claimed": False,
        "repository_ledger": {
            "namespace": repository_namespace,
            "head_receipt_sha256": repo_head["receipt_sha256"],
            "chain_length": len(repo_chain),
            "genesis_receipt_sha256": repo_chain[0]["receipt_sha256"],
            "every_receipt_verifies_against_its_body": True,
            "replay_read_organization_receipts": False,
        },
        "ledger_empty": False,
        "head_receipt_declares_current_state": "current_state" in org_chain[-1],
    }
    result.update(project_current_state(org_chain[-1]))
    if transition_result is not None:
        result.update(_transition_result_in_chains(transition_result, repo_chain, org_chain))
    if ingress_result is None:
        return result
    communication_id = ingress_result.get("communication_id")
    repo_sha = ingress_result.get("repository_receipt_sha256")
    org_sha = ingress_result.get("organization_receipt_sha256")
    if not isinstance(communication_id, str) or not communication_id.strip():
        raise ReadbackRefused("CLOSURE_CARRIES_COMMUNICATION_ID", repr(communication_id))
    repository_receipt = next((receipt for receipt in repo_chain
                               if receipt.get("receipt_sha256") == repo_sha), None)
    organization_receipt = next((receipt for receipt in org_chain
                                 if receipt.get("receipt_sha256") == org_sha), None)
    if repository_receipt is None:
        raise ReadbackRefused("REPOSITORY_RECEIPT_IS_IN_THE_REPOSITORY_LEDGER", str(repo_sha))
    if organization_receipt is None:
        raise ReadbackRefused("ORGANIZATION_RECEIPT_IS_IN_THE_ORGANIZATION_LEDGER", str(org_sha))
    if organization_receipt.get("repo_receipt_sha256") != repository_receipt.get("receipt_sha256"):
        raise ReadbackRefused("ORGANIZATION_RECEIPT_PRESERVES_REPOSITORY_RECEIPT", str(org_sha))
    if _communication_id(repository_receipt) != communication_id:
        raise ReadbackRefused("REPOSITORY_RECEIPT_CARRIES_COMMUNICATION_ID", str(communication_id))
    if _communication_id(organization_receipt) != communication_id:
        raise ReadbackRefused("ORGANIZATION_RECEIPT_CARRIES_COMMUNICATION_ID", str(communication_id))
    # The closure result is the writer's report. Its disposition must be the
    # one the receipts recorded; the readback does not require any value.
    for receipt, predicate in ((repository_receipt, "CLOSURE_RESULT_MATCHES_REPOSITORY_RECEIPT_EVIDENCE"),
                               (organization_receipt, "CLOSURE_RESULT_MATCHES_ORGANIZATION_RECEIPT_EVIDENCE")):
        evidence = receipt.get("evidence") if isinstance(receipt.get("evidence"), dict) else {}
        if "disposition" in ingress_result and evidence.get("disposition") != ingress_result.get("disposition"):
            raise ReadbackRefused(predicate, str(receipt.get("receipt_sha256")))
    result.update({
        "closure_result_matches_receipt_evidence": True,
        "communication_id": communication_id,
        "repository_receipt_sha256": repo_sha,
        "organization_receipt_sha256": org_sha,
        "communication_id_on_repository_receipt": True,
        "communication_id_on_organization_receipt": True,
        "organization_receipt_preserves_repository_receipt": True,
        "ingress_repository_receipt_in_chain": True,
        "ingress_organization_receipt_in_chain": True,
    })
    return result


def _refusal(location: str, exc: BaseException) -> dict[str, Any]:
    return {
        "schema": "stegverse.organization-ledger-readback/v1",
        "location": location,
        "readback": "OBSERVATION_ONLY",
        "records_no_transition": True,
        "verification": {"organization_chain_verifies": False, "repository_chain_verifies": False},
        "basis_established": False,
        "current_state": None,
        "failure_code": "ORGANIZATION_LEDGER_READBACK_FAILED",
        "failed_predicate": getattr(exc, "failed_predicate", "ORGANIZATION_LEDGER_READS_BACK"),
        "detail": getattr(exc, "detail", str(exc)),
        "retry_entrypoint": RETRY,
        "next_attempt": "fetch the ledger ref again and rerun the readback; the readback writes nothing",
        "authority_constraint": "NONE_READBACK_ONLY",
        "time_constraint": "HEARTBEAT_EPOCH",
        "production_receipt_claimed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--location", help="ledger locator, for example git+<repository>#<ref>")
    parser.add_argument("--ingress-result", type=Path, default=None)
    parser.add_argument("--repository-namespace", default=None)
    parser.add_argument("--transition-result", type=Path, default=None,
                        help="result of organization_ledger_transition.py for a committed request")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--designated-locators", type=Path, default=None,
                        help="print the published ref locators for this checkout and write nothing")
    args = parser.parse_args()
    location = args.location or ""
    try:
        if args.designated_locators is not None:
            result, code = designated_locators(args.designated_locators), 0
        else:
            if not args.location:
                parser.error("--location is required")
            ingress = None
            if args.ingress_result is not None:
                ingress = json.loads(args.ingress_result.read_text(encoding="utf-8"))
            transition = None
            if args.transition_result is not None:
                transition = json.loads(args.transition_result.read_text(encoding="utf-8"))
            result, code = readback(args.location, ingress, args.repository_namespace, transition), 0
    except (ReadbackRefused, ValueError, RuntimeError, FileNotFoundError, OSError) as exc:
        result, code = _refusal(location, exc), 1
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
