#!/usr/bin/env python3
"""``ORGANIZATION_INTR_DESTINATION_INGRESS`` — acknowledge a purpose-neutral diagnostic.

``intr-destination`` is the capability an organization can advertise so a peer
can address it without a purpose-specific route. A diagnostic crossing arrives,
both ledger levels record the disposition, and the binding selects nothing
further.

The payload is an ``ecosystem.communication``. That request class is one this
transport profile answers, and it carries the ``communication_id`` a closure
correlates on. The body schema is ``stegverse.org-intr-destination-diagnostic/v1``.
A body that is not that schema is a refusal, recorded as transition evidence.

The profile name ``InTr:Destination`` is local to this binding. The pinned SDK
subsystem baseline does not declare the profile, and the canonical StegOS
connector registry was not readable from the change that added it, so this
repository does not claim StegOS already named it.

Authority and time are constraint variables. Standing was already required at
ingress. This operation does not re-decide it, and it does not select a
processor. A disposition recorded here is transition evidence. Receipts written
under a caller-supplied ledger root do not claim to be authentic production
receipts. A dispatch supplies the designated organization ledger ref; a test
supplies a redirected root.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]

OPERATION_ID = "ORGANIZATION_INTR_DESTINATION_INGRESS"
REFUSED_CLASS = "ORGANIZATION_INTR_DESTINATION_INGRESS_REFUSED"
OWNER_REPOSITORY = "SV-011/.github"
PROFILE_ID = "intr-destination"
PROFILE_NAME = "InTr:Destination"
OPERATION = "DIAGNOSTIC_CROSSING"
PAYLOAD_SCHEMA = "stegverse.org-intr-destination-diagnostic/v1"
RESULT_SCHEMA = "stegverse.org-intr-destination-result/v1"
COMMUNICATION_CLASS = "ecosystem.communication"
ALLOW = "ALLOW"
DENY = "DENY"
FAIL_CLOSED = "FAIL_CLOSED"
ACK_CONSTRAINT = "NONE_DIAGNOSTIC_ACK_ONLY"
REFUSAL_CONSTRAINT = "NONE_REFUSAL_ONLY"


def _module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


repository_ledger = _module("repo_transition_emit", ".stegverse/transition-ledger/emit.py")
organization_ledger = _module("aggregate_repo_transition",
                              "resident-runtime/aggregate_repo_transition.py")


def canon(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sha(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canon(value)).hexdigest()


def payload_sha(payload: Any) -> str:
    if isinstance(payload, Mapping):
        return sha(dict(payload))
    return sha(payload)


def shape_refusal(payload: Any) -> tuple[str, str] | None:
    """Why this payload is not a diagnostic this operation acknowledges."""
    if not isinstance(payload, Mapping):
        return ("SUBMISSION_IS_AN_ECOSYSTEM_COMMUNICATION", "payload is not an object")
    if payload.get("message_class") != COMMUNICATION_CLASS:
        return ("SUBMISSION_DECLARES_A_RESPONDED_REQUEST_CLASS",
                str(payload.get("message_class")))
    identifier = payload.get("communication_id")
    if not isinstance(identifier, str) or not identifier.strip():
        return ("SUBMISSION_CARRIES_THE_COMMUNICATION_ID_CLOSURE_CORRELATES_ON",
                repr(identifier))
    body = payload.get("body")
    observed = body.get("schema") if isinstance(body, Mapping) else body
    if not isinstance(body, Mapping) or observed != PAYLOAD_SCHEMA:
        return ("SUBMISSION_BODY_DECLARES_THE_BOUND_PAYLOAD_SCHEMA", str(observed))
    return None


def _evidence(payload: Any, packet_id: str, disposition: str, *,
              failed_predicate: str | None = None, detail: str | None = None) -> dict[str, Any]:
    communication_id = payload.get("communication_id") if isinstance(payload, Mapping) else None
    constraint = ACK_CONSTRAINT if disposition == ALLOW else REFUSAL_CONSTRAINT
    record: dict[str, Any] = {
        "profile_id": PROFILE_ID,
        "profile_name": PROFILE_NAME,
        "operation": OPERATION,
        "receiving_operation": OPERATION_ID,
        "owner_repository": OWNER_REPOSITORY,
        "packet_id": packet_id,
        "communication_id": communication_id if isinstance(communication_id, str) else None,
        "payload_sha256": payload_sha(payload),
        "payload_schema": PAYLOAD_SCHEMA,
        "disposition": disposition,
        "disposition_is_transition_evidence": True,
        "authority_constraint": constraint,
        "time_constraint": "HEARTBEAT_EPOCH",
        "selects_routing": False,
        "selects_admission": False,
        "selects_execution": False,
        "selects_processing": False,
        "purpose_specific_route": False,
        "standing_rechecked_here": False,
        "production_receipt_claimed": False,
    }
    if failed_predicate is not None:
        record["failed_predicate"] = failed_predicate
        record["detail"] = detail
    return record


def _result(evidence: Mapping[str, Any], appended: Mapping[str, Any] | None, *,
            replayed: bool, received: bool) -> dict[str, Any]:
    disposition = evidence["disposition"]
    acknowledged = disposition == ALLOW and received
    result = {
        "schema": RESULT_SCHEMA,
        "organization": organization_ledger.C["organization"],
        "receiving_operation": OPERATION_ID,
        "owner_repository": OWNER_REPOSITORY,
        "profile_id": PROFILE_ID,
        "profile_name": PROFILE_NAME,
        "operation": OPERATION,
        "disposition": disposition,
        "disposition_is_transition_evidence": True,
        "received": received,
        "communication_acknowledged": acknowledged,
        "communication_id": evidence.get("communication_id"),
        "payload_schema": PAYLOAD_SCHEMA,
        "payload_sha256": evidence["payload_sha256"],
        "transition_replayed": replayed,
        "standing_rechecked_here": False,
        "selects_processing": False,
        "purpose_specific_route": False,
        "selects_routing": False,
        "selects_admission": False,
        "selects_execution": False,
        "authority_constraint": evidence["authority_constraint"],
        "time_constraint": "HEARTBEAT_EPOCH",
        "production_receipt_claimed": False,
    }
    if evidence.get("failed_predicate") is not None:
        result["failed_predicate"] = evidence["failed_predicate"]
        result["detail"] = evidence.get("detail")
    if appended is None:
        result["repository_receipt_observed"] = False
        result["organization_receipt_observed"] = False
        result["refusal_recorded"] = False
        return result
    repository_receipt = appended["repository_receipt"]
    organization_receipt = appended["organization_receipt"]
    result.update({
        "repository_receipt_observed": True,
        "organization_receipt_observed": True,
        "repository_receipt_sha256": repository_receipt["receipt_sha256"],
        "organization_receipt_sha256": organization_receipt["receipt_sha256"],
        "organization_receipt_preserves_repository_receipt":
            organization_receipt["repo_receipt_sha256"] == repository_receipt["receipt_sha256"],
        "refusal_recorded": disposition != ALLOW,
    })
    return result


def _plain_token(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(name + " is required")
    token = value.strip()
    if len(token) > 240 or any(ord(character) < 32 or ord(character) > 126 for character in token):
        raise SystemExit(name + " is not a printable token")
    return token


def diagnostic_payload(communication_id: str) -> dict[str, Any]:
    """The diagnostic this organization acknowledges on a self-closure."""
    return {
        "message_class": COMMUNICATION_CLASS,
        "communication_id": communication_id,
        "subject": "purpose-neutral mesh diagnostic",
        "body": {"schema": PAYLOAD_SCHEMA, "probe": "diagnostic-self-closure"},
    }


def self_closure(communication_id: str, *, registry: Mapping[str, Any], packet_id: str,
                 hb_epoch: int | None = None) -> dict[str, Any]:
    """Acknowledge this organization's own diagnostic and append both receipts.

    ``_append`` writes the repository receipt and the organization receipt that
    preserves it, both carrying ``communication_id``. The roots are the
    caller-supplied locators. A dispatch passes the designated organization
    ledger ref, with the repository chain in that ref's subtree. A redirected
    root, including a test, writes that supplied store. This function does not
    claim an authentic production receipt.
    """
    identifier = _plain_token(communication_id, "communication_id")
    packet = _plain_token(packet_id, "packet_id")
    return receive(diagnostic_payload(identifier), registry=registry, packet_id=packet,
                   hb_epoch=hb_epoch)


def _append(transition_class: str, packet_id: str, evidence: Mapping[str, Any],
            hb_epoch: int | None, *, operation: str = OPERATION_ID, profile_id: str = PROFILE_ID,
            boundary_extra: Mapping[str, Any] | None = None,
            hb_provenance: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], bool]:
    """Append at both levels. A replay of the same packet returns the recorded pair.

    ``operation``, ``profile_id`` and ``boundary_extra`` name another operation
    on the same two-level append (resident-runtime/merge_transfer.py); their
    defaults are this ingress, so its receipts are unchanged.

    ``hb_provenance`` records where a carried epoch came from (merge_transfer:
    the organization ledger head) on both receipts' ``hb_reference``; a supplied
    epoch carries none.

    The repository locator and the organization locator are caller-supplied.
    On the published ref they are the same git ref: the organization chain at
    the root and the repository chain in its subtree. Both receipts carry
    ``communication_id``.
    """
    predecessor = evidence["payload_sha256"]
    successor = sha({"disposition": evidence["disposition"], "packet_id": packet_id,
                     "communication_id": evidence.get("communication_id"),
                     "payload_sha256": evidence["payload_sha256"]})
    transition_id = transition_class + ":" + packet_id
    store = repository_ledger.ledger_store.open_store(repository_ledger.lr())
    head = store.get(repository_ledger.ledger_store.HEAD_KEY)
    prior = repository_ledger.recorded(store, head, transition_id, transition_class)
    repository_receipt = repository_ledger.append(
        transition_id, transition_class, predecessor, successor, dict(evidence),
        "NONE", hb_epoch=hb_epoch, hb_provenance=hb_provenance,
        idempotent_on=("communication_id", "payload_sha256", "disposition"))
    # The organization receipt references the epoch this call was supplied, never
    # one read back from the repository receipt: a replayed repository receipt may
    # carry an older or clock-derived epoch.
    organization_receipt = organization_ledger.append(
        repository_receipt, "REPO_STATE_PROPAGATION", predecessor, successor,
        {"operation": operation, "profile_id": profile_id,
         "disposition": evidence["disposition"],
         "communication_id": evidence.get("communication_id"), **dict(boundary_extra or {})},
        "NONE", hb_epoch=hb_epoch, idempotent=True, hb_provenance=hb_provenance)
    return ({"repository_receipt": repository_receipt,
             "organization_receipt": organization_receipt}, prior is not None)


def _recorded(disposition: str, payload: Any, packet_id: str, hb_epoch: int | None, *,
              failed_predicate: str | None = None, detail: str | None = None) -> dict[str, Any]:
    evidence = _evidence(payload, packet_id, disposition,
                         failed_predicate=failed_predicate, detail=detail)
    transition_class = OPERATION_ID if disposition == ALLOW else REFUSED_CLASS
    try:
        appended, replayed = _append(transition_class, packet_id, evidence, hb_epoch)
    except ValueError as exc:
        # A store-side collision is a fault writing our own state, not this packet's: it propagates.
        if str(exc) != "ledger_receipt_collision" or getattr(exc, "ledger_write_fault", False) is True:
            raise
        collided = _evidence(payload, packet_id, FAIL_CLOSED,
                             failed_predicate="ONE_PACKET_ID_BINDS_ONE_DIAGNOSTIC",
                             detail=str(exc))
        return _result(collided, None, replayed=False, received=False)
    return _result(evidence, appended, replayed=replayed, received=disposition == ALLOW)


def receive(payload: Any, *, registry: Mapping[str, Any], standing: Mapping[str, Any] | None = None,
            packet_id: str = "organization-intr-destination",
            hb_epoch: int | None = None, mesh_root: Path | None = None) -> dict[str, Any]:
    """Acknowledge one diagnostic, or record the refusal.

    ``standing`` and ``mesh_root`` are accepted because the capability resolver
    passes them to every receiving operation. Standing was required at ingress.
    A diagnostic does not leave, so the mesh is not used.
    """
    del standing, mesh_root
    if not isinstance(registry, Mapping):
        return _recorded(DENY, payload, packet_id, hb_epoch,
                         failed_predicate="CAPABILITY_MAP_IS_A_MAPPING",
                         detail="registry is not an object")
    refusal = shape_refusal(payload)
    if refusal is not None:
        return _recorded(DENY, payload, packet_id, hb_epoch,
                         failed_predicate=refusal[0], detail=refusal[1])
    return _recorded(ALLOW, payload, packet_id, hb_epoch)


CLI_EPOCH_RETRY_ENTRYPOINT = ("resident-runtime/intr_destination_ingress.py --hb-epoch <digits >= 32> "
                              "(or STEGVERSE_HB_EPOCH)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--self-closure", action="store_true",
                        help="acknowledge this organization's own diagnostic and append both receipts")
    parser.add_argument("--communication-id", default=os.environ.get("STEGVERSE_COMMUNICATION_ID"))
    parser.add_argument("--payload", type=Path, default=None)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--packet-id", default=os.environ.get("STEGVERSE_PACKET_ID"))
    # Read as text and checked by the shared rule: argparse's int() would admit
    # "+33", " 33" or a fullwidth "３３" as 33.
    parser.add_argument("--hb-epoch", type=str, default=None,
                        help="the supplied heartbeat epoch: ASCII digits >= 32 (or STEGVERSE_HB_EPOCH)")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    # A set variable is a supplied value, "" included; only an unset one is absent.
    from_env = os.environ.get("STEGVERSE_HB_EPOCH") if "STEGVERSE_HB_EPOCH" in os.environ else None
    hb_epoch = args.hb_epoch if args.hb_epoch is not None else from_env
    refusal = repository_ledger.kernel.writer_epoch_refusal(
        hb_epoch, retry_entrypoint=CLI_EPOCH_RETRY_ENTRYPOINT)
    if refusal is None and args.hb_epoch is not None and from_env is not None:
        # Both inputs present: they must name the same epoch, or nothing is written.
        other = repository_ledger.kernel.supplied_epoch_failure(from_env)
        if other is not None or int(from_env) != int(args.hb_epoch):
            refusal = repository_ledger.kernel.writer_epoch_refusal(None, retry_entrypoint=CLI_EPOCH_RETRY_ENTRYPOINT)
            refusal["failure_code"] = "HB_EPOCH_CONFLICT"
            refusal["detail"] = ("--hb-epoch " + repr(args.hb_epoch) + " and STEGVERSE_HB_EPOCH "
                                 + repr(from_env) + " are both supplied and differ")
    if refusal is not None:
        print(json.dumps(refusal, sort_keys=True))
        return 2
    if args.self_closure:
        if not args.communication_id or not args.packet_id:
            parser.error("--self-closure requires communication_id and packet_id")
        result = self_closure(args.communication_id, registry=registry,
                              packet_id=args.packet_id, hb_epoch=hb_epoch)
    else:
        if args.payload is None:
            parser.error("--payload is required")
        result = receive(
            json.loads(args.payload.read_text(encoding="utf-8")),
            registry=registry,
            packet_id=args.packet_id or "organization-intr-destination",
            hb_epoch=hb_epoch)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("disposition") == ALLOW else 1


if __name__ == "__main__":
    sys.exit(main())
