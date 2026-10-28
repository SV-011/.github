#!/usr/bin/env python3
"""Append the organization receipt that consumes one repository receipt.

The root comes from ``STEGVERSE_ORG_LEDGER_ROOT``. A plain path is a
directory. ``git+<repository>#<ref>`` addresses a git ref. The organization
receipt preserves the repository receipt digest. It does not select admission,
routing, or execution. Authority and time stay constraint variables. The
disposition on the evidence is transition evidence.

A receipt written here does not claim to be an authentic production receipt.
Callers that redirect the root, including tests, are writing a supplied store.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
C = json.loads((ROOT / ".stegverse/transition-ledger/org-contract.json").read_text(encoding="utf-8"))
REPOSITORY_CONTRACT = json.loads((ROOT / ".stegverse/transition-ledger/contract.json").read_text(encoding="utf-8"))
ENTRYPOINT = "resident-runtime/aggregate_repo_transition.py append"

_kernel_spec = importlib.util.spec_from_file_location("kernel", ROOT / "org-kernel/kernel.py")
kernel = importlib.util.module_from_spec(_kernel_spec)
_kernel_spec.loader.exec_module(kernel)

_store_spec = importlib.util.spec_from_file_location(
    "ledger_store", ROOT / "resident-runtime/ledger_store.py")
ledger_store = importlib.util.module_from_spec(_store_spec)
_store_spec.loader.exec_module(ledger_store)


def canon(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha(value: Any) -> str:
    raw = value if isinstance(value, (bytes, bytearray)) else canon(value)
    return "sha256:" + hashlib.sha256(bytes(raw)).hexdigest()


def ledger_root():
    """The caller-supplied organization ledger locator. A missing root fails closed."""
    configured = os.getenv("STEGVERSE_ORG_LEDGER_ROOT")
    if not configured:
        raise ValueError("ledger_location_required_from_caller: STEGVERSE_ORG_LEDGER_ROOT")
    return ledger_store.parse_locator(configured)


def repository_ledger_root():
    """The caller-supplied repository ledger locator the source receipt must be on."""
    configured = os.getenv("STEGVERSE_REPO_LEDGER_ROOT")
    if not configured:
        raise ValueError("ledger_location_required_from_caller: STEGVERSE_REPO_LEDGER_ROOT")
    return ledger_store.parse_locator(configured)


def _not_designated(detail: str) -> ledger_store.CommittedRequestRefused:
    return ledger_store.CommittedRequestRefused(
        "SOURCE_LEDGER_NOT_DESIGNATED", "SOURCE_LEDGER_IS_THIS_REPOSITORYS_DESIGNATED_LEDGER", detail,
        "the repository ledger is this repository's own: on a git ref, the organization ledger's repository "
        "(realpath) and ref with the contract namespace; its receipts name " + REPOSITORY_CONTRACT["repository"],
        ENTRYPOINT)


CONTRACT_NAMESPACE = REPOSITORY_CONTRACT["repository_ledger_location"]["namespace"]
LEDGER_VARIABLES = ("STEGVERSE_ORG_LEDGER_ROOT", "STEGVERSE_REPO_LEDGER_ROOT")


def _caller_chosen(detail: str) -> ledger_store.CommittedRequestRefused:
    return ledger_store.CommittedRequestRefused(
        "CALLER_CHOSEN_SOURCE_LEDGER_REJECTED", "SOURCE_LEDGER_IS_THE_CONTRACT_DESIGNATED_SUBTREE_OF_THE_TARGET",
        detail,
        "no override: the source ledger is the contract-designated subtree (" + CONTRACT_NAMESPACE + ") of the "
        "organization ledger this call writes; an override is admissible only as a verified same-kind pair of "
        + " and ".join(LEDGER_VARIABLES) + " whose organization root is that ledger (realpath)",
        ENTRYPOINT)


def _realpath(location: Any) -> Any:
    if isinstance(location, ledger_store.GitLocator):
        return ledger_store.GitLocator(Path(os.path.realpath(location.repository)), location.ref, location.namespace)
    return Path(os.path.realpath(location))


def _store_location(store: Any) -> Any:
    """The realpath location of an organization store a caller passed as ``store=``."""
    if isinstance(store, ledger_store.GitLedgerStore):
        return _realpath(ledger_store.GitLocator(store.repository, store.ref, store.namespace))
    if isinstance(store, ledger_store.Store):
        return _realpath(store.root)
    raise _caller_chosen("store= is a " + type(store).__name__ + ", not a ledger store")


def _designated_subtree(organization: Any) -> Any:
    """The contract-designated repository ledger inside an organization ledger (CALLER_CHOSEN_SOURCE_LEDGER_REJECTED)."""
    if isinstance(organization, ledger_store.GitLocator):
        if organization.namespace is not None:
            raise _caller_chosen("organization ledger " + str(organization) + " is itself a subtree")
        return ledger_store.GitLocator(organization.repository, organization.ref, CONTRACT_NAMESPACE)
    return organization / CONTRACT_NAMESPACE


def resolve_ledgers(store: Any = None) -> tuple[Any, Any]:
    """(the organization ledger written, the repository ledger the source must be on).

    The source ledger is the contract-designated subtree of the organization
    ledger written, never a caller's choice. An environment override is
    admissible only as a same-kind pair: on a git ref the repository ledger is the
    organization ledger's own repository (realpath), ref and the contract
    namespace; a directory pair holds no tracked files. ``store=`` must be the
    organization ledger the pair names (realpath), and its repository ledger
    must be that store's designated subtree. A lone override, a mixed pair or a
    disagreeing ``store=`` is refused before any write.
    """
    supplied = {name: os.getenv(name) for name in LEDGER_VARIABLES}
    present = [name for name, value in supplied.items() if value]
    if len(present) == 1:
        raise _caller_chosen("lone override: only " + present[0] + " is set")
    if not present:
        if store is None:
            raise ValueError("ledger_location_required_from_caller: " + " and ".join(LEDGER_VARIABLES))
        organization = _store_location(store)
        if not isinstance(organization, ledger_store.GitLocator):
            raise _caller_chosen("a directory store= without the verified pair designates no repository ledger")
        return store, ledger_store.open_store(_designated_subtree(organization))
    organization = ledger_store.parse_locator(supplied["STEGVERSE_ORG_LEDGER_ROOT"])
    repository = ledger_store.parse_locator(supplied["STEGVERSE_REPO_LEDGER_ROOT"])
    for location in (organization, repository):
        ledger_store.refuse_tracked_ledger_root(location, ENTRYPOINT)
    if isinstance(organization, ledger_store.GitLocator) != isinstance(repository, ledger_store.GitLocator):
        raise _caller_chosen("mixed pair: " + str(organization) + " and " + str(repository))
    if isinstance(organization, ledger_store.GitLocator):
        if _realpath(repository) != _realpath(_designated_subtree(organization)):
            raise _caller_chosen("repository ledger " + str(repository) + " is not "
                                 + str(_designated_subtree(organization)))
    if store is None:
        return ledger_store.open_store(organization), ledger_store.open_store(repository)
    if _store_location(store) != _realpath(organization):
        raise _caller_chosen("store= " + str(_store_location(store)) + " is not the organization ledger "
                             + str(organization) + " the pair names")
    if _realpath(repository) != _realpath(_designated_subtree(_store_location(store))):
        raise _caller_chosen("repository ledger " + str(repository) + " is not the designated subtree of store= "
                             + str(_store_location(store)))
    return store, ledger_store.open_store(repository)


def designated_source_store() -> Any:
    """This repository's own ledger, never a caller-chosen one (SOURCE_LEDGER_OVERRIDE_LAUNDERS_FABRICATED_RECEIPT)."""
    return resolve_ledgers()[1]


def _read(store: Any, key: str) -> Any:
    """One stored document; a corrupt one is terminal, an unreadable store is a retry (row 9)."""
    try:
        document = store.get(key)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ledger_store.CommittedRequestRefused(
            "SOURCE_LEDGER_RECEIPT_CORRUPT", "SOURCE_LEDGER_DOCUMENTS_ARE_JSON_OBJECTS", key + ": " + str(exc),
            "repair the repository ledger document at " + key, ENTRYPOINT) from None
    except (OSError, RuntimeError) as exc:
        raise ledger_store.CommittedRequestRefused(
            "SOURCE_LEDGER_READ_FAULT_RETRY", "SOURCE_LEDGER_IS_READABLE", key + ": " + str(exc),
            "a readable repository ledger", ENTRYPOINT, terminal=False) from None
    if document is not None and not isinstance(document, dict):
        raise ledger_store.CommittedRequestRefused(
            "SOURCE_LEDGER_RECEIPT_CORRUPT", "SOURCE_LEDGER_DOCUMENTS_ARE_JSON_OBJECTS",
            key + " holds a " + type(document).__name__, "repair the repository ledger document at " + key,
            ENTRYPOINT)
    return document


def _source_on_ledger(source_receipt: Mapping[str, Any], source_store: Any) -> None:
    """The source receipt is on the repository ledger, reachable from HEAD, byte-for-byte.

    Unconditional: it runs for every source receipt, whether or not it carries a
    committed_request (OFF_LEDGER_SOURCE_RECEIPT_WITHOUT_BINDING_ACCEPTED). The
    ledger and the receipt must be this repository's own (row 8); a corrupt
    document on the walk is refused, not raised (row 9).
    """
    own = REPOSITORY_CONTRACT["repository"]
    if source_receipt.get("repository") != own:
        raise _not_designated("source receipt names repository " + repr(source_receipt.get("repository")))
    digest = source_receipt["receipt_sha256"]
    head = _read(source_store, ledger_store.HEAD_KEY)
    if head is not None and head.get("repository") != own:
        raise _not_designated("repository ledger HEAD names " + repr(head.get("repository")))
    cursor, seen = (head or {}).get("receipt_sha256"), set()
    while isinstance(cursor, str) and cursor not in seen:
        seen.add(cursor)
        receipt = _read(source_store, ledger_store.receipt_key(cursor))
        if receipt is None:
            break
        if cursor == digest:
            if receipt != dict(source_receipt) or sha({k: v for k, v in receipt.items()
                                                       if k != "receipt_sha256"}) != digest:
                break
            return
        cursor = receipt.get("previous_receipt_sha256")
    raise ledger_store.CommittedRequestRefused(
        "SOURCE_RECEIPT_NOT_ON_LEDGER", "SOURCE_RECEIPT_IS_ON_THE_REPOSITORY_LEDGER_REACHABLE_FROM_HEAD",
        "source receipt " + str(digest) + " is not the receipt the repository ledger holds on its chain",
        "aggregate only a sealed repository receipt that the repository ledger chain reaches from HEAD",
        "resident-runtime/aggregate_repo_transition.py append")


def _chain(target: ledger_store.Store, head: Mapping[str, Any] | None):
    cursor = (head or {}).get("receipt_sha256") if isinstance(head, Mapping) else None
    while cursor:
        receipt = target.get(ledger_store.receipt_key(cursor))
        if not isinstance(receipt, dict):
            return
        yield receipt
        cursor = receipt.get("previous_receipt_sha256")


def _recorded(target: ledger_store.Store, head: Mapping[str, Any] | None,
              repo_receipt_sha256: str) -> dict[str, Any] | None:
    for receipt in _chain(target, head):
        if receipt.get("repo_receipt_sha256") == repo_receipt_sha256:
            return receipt
    return None


def append(source_receipt: Mapping[str, Any], org_transition_class: str,
           predecessor_state: str, successor_state: str, boundary_evidence: Mapping[str, Any],
           authority_constraint: str, hb_epoch: int | None = None,
           store: ledger_store.Store | None = None, idempotent: bool = False) -> dict[str, Any]:
    if not isinstance(source_receipt, Mapping) or not isinstance(source_receipt.get("receipt_sha256"), str):
        raise ValueError("source_receipt_required")
    # before any write: the source must be on the repository ledger first, unconditionally,
    # then no committed_request may ride in the evidence or the source (V-P11 rows 5 and 3)
    # the source ledger is derived from the ledger written, never chosen by the caller (row 8)
    target, source_store = resolve_ledgers(store)
    _source_on_ledger(source_receipt, source_store)
    ledger_store.refuse_injected_committed_request("resident-runtime/aggregate_repo_transition.py append",
                                                   boundary_evidence=boundary_evidence,
                                                   source_receipt=source_receipt)
    target.initialize()
    expected_head = target.get(ledger_store.HEAD_KEY)
    head = expected_head if isinstance(expected_head, dict) else None
    previous = (head or {}).get("receipt_sha256")
    source_sha = source_receipt["receipt_sha256"]
    if idempotent:
        prior = _recorded(target, head, source_sha)
        if prior is not None:
            if prior.get("org_transition_class") != org_transition_class:
                raise ValueError("ledger_receipt_collision")
            return prior
    body = {
        "schema": "stegverse.organization-transition-receipt/v1",
        "organization": C["organization"],
        "repository": C["repository"],
        "org_transition_class": org_transition_class,
        "repo_receipt_sha256": source_sha,
        "predecessor_state_sha256": predecessor_state,
        "successor_state_sha256": successor_state,
        "evidence": dict(boundary_evidence),
        "authority_constraint": authority_constraint,
        "time_constraint": "HEARTBEAT_EPOCH",
        "disposition_is_transition_evidence": True,
        "production_receipt_claimed": False,
        "hb_reference": kernel.hb_reference(epoch=hb_epoch) if hb_epoch is not None else kernel.hb_reference(),
        "previous_receipt_sha256": previous,
    }
    digest = sha(body)
    receipt = {**body, "receipt_sha256": digest}
    new_head = {
        "schema": "stegverse.ledger-head/v1",
        "organization": C["organization"],
        "receipt_sha256": digest,
    }
    if target.append_transaction(
            ledger_store.receipt_key(digest), receipt,
            head if isinstance(head, dict) else None, new_head):
        return receipt
    raise RuntimeError("ledger_append_lost_comparison")
