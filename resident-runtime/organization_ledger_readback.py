#!/usr/bin/env python3
"""Read the organization ledger back and verify it, independently of the writer.

A transition is on the ledger only once this readback walks it. The evidence
of an append is this readback, not the writer's own report. This opens the
ledger at the location it is given (for the designated ref, a fresh fetch of
``refs/stegverse/organization-ledger``). It walks the chain from HEAD and
checks, for every receipt:

- its ``receipt_sha256`` is the digest of its own body;
- ``previous_receipt_sha256`` names the receipt before it, ending at genesis
  (null), so the chain has no gaps and no forks;
- the source receipt it consumed is retained under ``source-receipts/`` and
  verifies against its own body.

Given an ingress result (``--ingress-result``), it also checks that the
result's organization receipt is in the chain and that its predecessor state
is the submitted manifest digest.

Current-state basis
-------------------
``current_state`` is taken only from the head receipt's ``current_state``
object. The shape is the Current-state basis section of contract blob
``8f7d4cc2127325b155e1a91edfe531b5737f7c75`` (``git hash-object`` of that
file). A complete basis has exactly these fields:

- ``state_id``: an identifier matching ``^[A-Za-z0-9._:-]{1,128}$``
- ``as_of`` and ``fresh_until``: UTC timestamps ``YYYY-MM-DDTHH:MM:SSZ`` that
  ``datetime.strptime`` accepts. The basis is fresh when ``as_of <=
  fresh_until``.
- ``declared_transition_element``: ``transition_id`` matching ``T-`` plus
  three digits, and ``fixture_ref`` matching
  ``fixtures/transition-elements/<name>.json`` where ``<name>`` is an
  identifier. Both are copied from the head receipt. This repository does
  not contain that fixture tree, so the path is checked as a pattern and the
  declaration is copied; the fixture file is not opened and its
  ``transition_id`` is not compared here.
- ``declared_role_escalation``: ``role_id``, ``from_role``, and ``to_role``,
  each an identifier, copied from the head receipt. ``from_role`` and
  ``to_role`` must differ.
- ``satisfied_scope_ids``: a prefix of the manifest scope, one of ``[]``,
  ``["transition_element"]``, or
  ``["transition_element", "role_escalation"]``.
- ``basis_digest``: the lowercase hex SHA-256 of the UTF-8 bytes of
  ``json.dumps`` of ``current_state`` with ``basis_digest`` removed
  (``sort_keys`` true, separators ``,`` and ``:``, ``ensure_ascii`` false).

A missing required field is ``BASIS_ABSENT``. A present field with the wrong
type or pattern, or an unparseable time, is ``BASIS_UNKNOWN``. A later
``as_of`` is ``BASIS_STALE``. A digest mismatch, a satisfaction list that is
not one of those prefixes, or indistinguishable roles is
``BASIS_CONTRADICTORY``. In each of those cases ``basis_established`` is
false and ``current_state`` is null. Nothing is copied from an earlier
receipt. An empty ledger has ``current_state`` null and no digest.

Stdlib and ``git`` only. Authority and time stay constraint variables. This
readback records no transition, so it has no disposition.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ledger_store import HEAD_KEY, open_store, parse_locator, receipt_key, source_key  # noqa: E402

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
IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
TRANSITION_ID = re.compile(r"^T-[0-9]{3}$")
FIXTURE_REF = re.compile(r"^fixtures/transition-elements/[A-Za-z0-9._:-]{1,128}\.json$")
UTC_TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
SCOPE_PREFIXES = (
    (),
    ("transition_element",),
    ("transition_element", "role_escalation"),
)
TRANSITION_ELEMENT_FIELDS = ("fixture_ref", "transition_id")
ROLE_ESCALATION_FIELDS = ("from_role", "role_id", "to_role")


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha(value):
    return "sha256:" + hashlib.sha256(canon(value)).hexdigest()


class ReadbackRefused(Exception):
    def __init__(self, failed_predicate, detail):
        super().__init__(failed_predicate + ": " + detail)
        self.failed_predicate = failed_predicate
        self.detail = detail


def self_verifies(document):
    body = {key: value for key, value in document.items() if key != "receipt_sha256"}
    return document.get("receipt_sha256") == sha(body)


def basis_digest(subject):
    """Lowercase hex SHA-256 of current_state with basis_digest removed.

    The preimage is ``json.dumps`` of the other six fields, sorted keys,
    separators ``,`` and ``:``, ``ensure_ascii`` false. No ``sha256:`` prefix.
    """
    payload = {name: subject[name] for name in BASIS_INPUT_FIELDS}
    preimage = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(preimage.encode("utf-8")).hexdigest()


def _incomplete(reason, absent):
    return {
        "basis_established": False,
        "current_state": None,
        "current_state_fields_absent": list(absent),
        "current_state_reason": reason,
    }


def _parse_utc(value):
    if not isinstance(value, str) or UTC_TIMESTAMP.fullmatch(value) is None:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def _copy_transition_element(value):
    """The declaration's transition element, or None when the shape is wrong."""
    if not isinstance(value, dict) or set(value) != set(TRANSITION_ELEMENT_FIELDS):
        return None
    transition_id = value.get("transition_id")
    fixture_ref = value.get("fixture_ref")
    if not isinstance(transition_id, str) or TRANSITION_ID.fullmatch(transition_id) is None:
        return None
    if not isinstance(fixture_ref, str) or FIXTURE_REF.fullmatch(fixture_ref) is None:
        return None
    return {"transition_id": transition_id, "fixture_ref": fixture_ref}


def _copy_role_escalation(value):
    """The declaration's role escalation, or None when an identifier is wrong."""
    if not isinstance(value, dict) or set(value) != set(ROLE_ESCALATION_FIELDS):
        return None
    copied = {}
    for name in ROLE_ESCALATION_FIELDS:
        item = value.get(name)
        if not isinstance(item, str) or IDENTIFIER.fullmatch(item) is None:
            return None
        copied[name] = item
    return copied


def project_current_state(head_receipt):
    """The head receipt's current_state, or a contract reason it is not a basis.

    ``declared_transition_element`` and ``declared_role_escalation`` are
    copied only from this receipt. A missing one is not invented. Nothing is
    copied from an older receipt, the clock, or a default.
    """
    declared = head_receipt.get("current_state") if isinstance(head_receipt, dict) else None
    if not isinstance(declared, dict):
        return _incomplete("BASIS_ABSENT", BASIS_INPUT_FIELDS)
    missing = [name for name in BASIS_INPUT_FIELDS if name not in declared or declared[name] is None]
    if missing:
        return _incomplete("BASIS_ABSENT", missing)
    unknown = sorted(name for name in declared if name not in BASIS_INPUT_FIELDS and name != "basis_digest")
    if unknown:
        return _incomplete("BASIS_UNKNOWN", unknown)
    if not isinstance(declared["state_id"], str) or IDENTIFIER.fullmatch(declared["state_id"]) is None:
        return _incomplete("BASIS_UNKNOWN", ["state_id"])
    as_of = _parse_utc(declared["as_of"])
    fresh_until = _parse_utc(declared["fresh_until"])
    bad_times = [name for name, parsed in (("as_of", as_of), ("fresh_until", fresh_until)) if parsed is None]
    if bad_times:
        return _incomplete("BASIS_UNKNOWN", bad_times)
    element = _copy_transition_element(declared["declared_transition_element"])
    if element is None:
        return _incomplete("BASIS_UNKNOWN", ["declared_transition_element"])
    role = _copy_role_escalation(declared["declared_role_escalation"])
    if role is None:
        return _incomplete("BASIS_UNKNOWN", ["declared_role_escalation"])
    scopes = declared["satisfied_scope_ids"]
    if not isinstance(scopes, list) or any(not isinstance(item, str) for item in scopes):
        return _incomplete("BASIS_UNKNOWN", ["satisfied_scope_ids"])
    if "basis_digest" in declared and (
            not isinstance(declared["basis_digest"], str) or HEX_SHA256.fullmatch(declared["basis_digest"]) is None):
        return _incomplete("BASIS_UNKNOWN", ["basis_digest"])
    if as_of > fresh_until:
        return _incomplete("BASIS_STALE", ["as_of"])
    contradictory = []
    if role["from_role"] == role["to_role"]:
        contradictory.append("declared_role_escalation")
    if tuple(scopes) not in SCOPE_PREFIXES:
        contradictory.append("satisfied_scope_ids")
    if contradictory:
        return _incomplete("BASIS_CONTRADICTORY", contradictory)
    subject = {
        "state_id": declared["state_id"],
        "as_of": declared["as_of"],
        "fresh_until": declared["fresh_until"],
        "declared_transition_element": element,
        "declared_role_escalation": role,
        "satisfied_scope_ids": list(scopes),
    }
    state = dict(subject)
    state["basis_digest"] = basis_digest(subject)
    if "basis_digest" in declared and declared["basis_digest"] != state["basis_digest"]:
        return _incomplete("BASIS_CONTRADICTORY", ["basis_digest"])
    return {
        "basis_established": True,
        "current_state": state,
        "current_state_fields_absent": [],
        "current_state_reason": "HEAD_RECEIPT_CURRENT_STATE",
    }


def empty_ledger_report(location):
    """An empty ledger. No state id, no times, no scopes, and no digest."""
    return {
        "schema": "stegverse.organization-ledger-readback/v1",
        "location": location,
        "ledger_empty": True,
        "head_present": False,
        "chain_length": 0,
        "chain_verifies": False,
        "current_state": None,
        "basis_established": False,
        "current_state_reason": "ORGANIZATION_LEDGER_EMPTY",
        "authority_constraint": "NONE_READBACK_ONLY",
        "time_constraint": "HEARTBEAT_EPOCH",
        "disposition_is_transition_evidence": False,
    }


def readback(location, ingress_result=None, manifest=None):
    store = open_store(parse_locator(location))
    head = store.get(HEAD_KEY)
    if head is None:
        raise ReadbackRefused("ORGANIZATION_LEDGER_HEAD_PRESENT", "no HEAD at " + location)
    chain, cursor, seen = [], head.get("receipt_sha256"), set()
    while cursor is not None:
        if cursor in seen:
            raise ReadbackRefused("ORGANIZATION_LEDGER_CHAIN_IS_ACYCLIC", cursor)
        seen.add(cursor)
        receipt = store.get(receipt_key(cursor))
        if receipt is None:
            raise ReadbackRefused("ORGANIZATION_LEDGER_CHAIN_IS_COMPLETE", "missing " + cursor)
        if receipt.get("receipt_sha256") != cursor or not self_verifies(receipt):
            raise ReadbackRefused("ORGANIZATION_RECEIPT_VERIFIES_AGAINST_ITS_BODY", cursor)
        source_digest = receipt.get("source_transition_sha256")
        source = store.get(source_key(source_digest)) if source_digest else None
        if source is None or source.get("receipt_sha256") != source_digest or not self_verifies(source):
            raise ReadbackRefused("SOURCE_RECEIPT_RETAINED_AND_VERIFIES", str(source_digest))
        chain.append(receipt)
        cursor = receipt.get("previous_receipt_sha256")
    chain.reverse()
    result = {
        "schema": "stegverse.organization-ledger-readback/v1",
        "location": location,
        "ledger_empty": False,
        "head_present": True,
        "chain_verifies": True,
        "head_receipt_sha256": head["receipt_sha256"],
        "chain_length": len(chain),
        "genesis_receipt_sha256": chain[0]["receipt_sha256"],
        "chain": [{"receipt_sha256": item["receipt_sha256"],
                   "previous_receipt_sha256": item.get("previous_receipt_sha256"),
                   "source_transition_id": item.get("source_transition_id"),
                   "predecessor_org_state_sha256": item.get("predecessor_org_state_sha256"),
                   "successor_org_state_sha256": item.get("successor_org_state_sha256")} for item in chain],
        "every_receipt_verifies_against_its_body": True,
        "every_source_receipt_retained_and_verifies": True,
        "authority_constraint": "NONE_READBACK_ONLY",
        "time_constraint": "HEARTBEAT_EPOCH",
        "disposition_is_transition_evidence": False,
    }
    result.update(project_current_state(chain[-1]))
    if ingress_result is not None:
        refused = ingress_result.get("refusal_organization_receipt_sha256") is not None
        digest = ingress_result.get("refusal_organization_receipt_sha256" if refused
                                    else "organization_receipt_sha256")
        receipt = next((item for item in chain if item["receipt_sha256"] == digest), None)
        if receipt is None:
            raise ReadbackRefused("INGRESS_RECEIPT_IS_IN_THE_ORGANIZATION_LEDGER", str(digest))
        if refused:
            manifest_state = sha(manifest) if manifest is not None else None
        else:
            manifest_state = "sha256:" + str(ingress_result.get("canonical_manifest_sha256"))
        if not manifest_state or receipt.get("predecessor_org_state_sha256") != manifest_state:
            raise ReadbackRefused("RECEIPT_IS_BOUND_TO_THE_SUBMITTED_MANIFEST", str(manifest_state))
        result.update({
            "ingress_refused": refused,
            "ingress_organization_receipt_sha256": digest,
            "ingress_receipt_in_chain": True,
            "ingress_receipt_bound_to_manifest": True,
            "canonical_manifest_sha256": ingress_result.get("canonical_manifest_sha256"),
            "organization_transition_id": ingress_result.get("organization_transition_id"),
        })
    return result


def _repository_emitter():
    """The repository ledger's own chain walker."""
    import importlib.util
    emit = Path(__file__).resolve().parents[1] / ".stegverse/transition-ledger/emit.py"
    spec = importlib.util.spec_from_file_location("repository_ledger_readback_emit", emit)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def repository_readback(location, namespace, organization_result, ingress_result=None):
    """Read the repository chain kept in ``namespace`` of the same ref."""
    emitter = _repository_emitter()
    locator = parse_locator(location)
    repository_location = str(locator) + ":" + namespace
    store = open_store(repository_location)
    organization_store = open_store(locator)
    head = store.get(HEAD_KEY)
    if head is None:
        raise ReadbackRefused("REPOSITORY_LEDGER_HEAD_PRESENT", "no HEAD at " + repository_location)
    try:
        walked = list(emitter.chain(store, head))
    except emitter.RepoLedgerChainBreak as exc:
        raise ReadbackRefused("REPOSITORY_CHAIN_VERIFIES", str(exc)) from None
    walked.reverse()
    for receipt in walked:
        retained = organization_store.get(source_key(receipt["receipt_sha256"]))
        if retained != receipt:
            raise ReadbackRefused("REPOSITORY_RECEIPT_RETAINED_IN_ORGANIZATION_LEDGER",
                                  receipt["receipt_sha256"])
    result = {
        "location": repository_location,
        "namespace": namespace,
        "head_receipt_sha256": head["receipt_sha256"],
        "chain_length": len(walked),
        "genesis_receipt_sha256": walked[0]["receipt_sha256"],
        "chain": [{"receipt_sha256": item["receipt_sha256"],
                   "previous_receipt_sha256": item.get("previous_receipt_sha256"),
                   "transition_id": item.get("transition_id"),
                   "transition_class": item.get("transition_class")} for item in walked],
        "every_receipt_verifies_against_its_body": True,
        "every_receipt_retained_in_organization_ledger": True,
        "replay_read_organization_receipts": False,
    }
    if ingress_result is not None:
        refused = ingress_result.get("refusal_organization_receipt_sha256") is not None
        digest = ingress_result.get("refusal_repository_receipt_sha256" if refused
                                    else "repository_receipt_sha256")
        organization_digest = organization_result["ingress_organization_receipt_sha256"]
        consumed = organization_store.get(receipt_key(organization_digest)) or {}
        if digest is None and ingress_result.get("answered_from_ledger") is True:
            digest = consumed.get("source_transition_sha256")
        if not any(item["receipt_sha256"] == digest for item in walked):
            raise ReadbackRefused("INGRESS_REPOSITORY_RECEIPT_IS_IN_THE_REPOSITORY_LEDGER", str(digest))
        if consumed.get("source_transition_sha256") != digest:
            raise ReadbackRefused("ORGANIZATION_RECEIPT_CONSUMES_THE_REPOSITORY_RECEIPT", str(digest))
        result.update({"ingress_repository_receipt_sha256": digest,
                       "ingress_repository_receipt_in_chain": True,
                       "consumed_by_organization_receipt_sha256": organization_digest})
    return result


def recorded_for(location, canonical_manifest_sha256):
    """The verified receipt already binding this manifest state, or None.

    An empty ledger returns None. It does not mint a receipt.
    """
    try:
        verified = readback(location)
    except ReadbackRefused as exc:
        if exc.failed_predicate == "ORGANIZATION_LEDGER_HEAD_PRESENT":
            return None
        raise
    state = "sha256:" + canonical_manifest_sha256
    return next((item["receipt_sha256"] for item in verified["chain"]
                 if item["predecessor_org_state_sha256"] == state), None)


def _failure(location, exc):
    return {
        "schema": "stegverse.organization-ledger-readback/v1",
        "location": location,
        "ledger_empty": False,
        "chain_verifies": False,
        "current_state": None,
        "basis_established": False,
        "failure_code": "ORGANIZATION_LEDGER_READBACK_FAILED",
        "failed_predicate": getattr(exc, "failed_predicate", "ORGANIZATION_LEDGER_READS_BACK"),
        "required_evidence_or_repair": getattr(exc, "detail", str(exc)),
        "retry_entrypoint": RETRY,
        "next_attempt": "fetch the ledger ref again and rerun the readback; nothing is written by it",
        "authority_constraint": "NONE_READBACK_ONLY",
        "time_constraint": "HEARTBEAT_EPOCH",
        "disposition_is_transition_evidence": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--location", required=True, help="ledger locator, e.g. git+<repo>#<ref>")
    parser.add_argument("--ingress-result", type=Path, default=None)
    parser.add_argument("--manifest", type=Path, default=None,
                        help="the submitted manifest; binds a recorded refusal to it")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--recorded-for", metavar="CANONICAL_MANIFEST_SHA256", default=None,
                        help="print the receipt already binding this manifest state, or nothing")
    parser.add_argument("--repository-namespace", default=None,
                        help="also read back the repository chain kept in this subtree of the same ref")
    args = parser.parse_args(argv)
    if args.recorded_for:
        print(recorded_for(args.location, args.recorded_for) or "")
        return 0
    ingress = json.loads(args.ingress_result.read_text(encoding="utf-8")) if args.ingress_result else None
    manifest = json.loads(args.manifest.read_text(encoding="utf-8")) if args.manifest else None
    try:
        result, code = readback(args.location, ingress, manifest), 0
        if args.repository_namespace:
            result["repository_ledger"] = repository_readback(
                args.location, args.repository_namespace, result, ingress)
    except ReadbackRefused as exc:
        if exc.failed_predicate == "ORGANIZATION_LEDGER_HEAD_PRESENT" and ingress is None:
            result, code = empty_ledger_report(args.location), 0
        else:
            result, code = _failure(args.location, exc), 1
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        result, code = _failure(args.location, exc), 1
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
