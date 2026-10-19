#!/usr/bin/env python3
"""Read the organization ledger back from the location the caller supplies.

A dispatch reads ``refs/stegverse/organization-ledger`` from a fresh fetch.
The readback walks the organization chain and the repository subtree of that
same ref, checks each receipt against its own body, and checks that an
intr-destination closure's repository receipt, organization receipt, and
``communication_id`` are on those chains.

The readback does not select admission, routing, or execution. Authority and
time stay constraint variables. A disposition here is transition evidence.
This module does not claim an authentic production receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Mapping

SOURCE = Path(__file__).resolve().parents[1]
LEDGER_REF = "refs/stegverse/organization-ledger"
LEDGER_REPOSITORY = "SV-011/.github"
RETRY = "resident-runtime/organization_ledger_readback.py::main"

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
        "disposition_is_transition_evidence": True,
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


def readback(location: str, ingress_result: Mapping[str, Any] | None = None,
             repository_namespace: str | None = None) -> dict[str, Any]:
    """Verify the chains at ``location`` and, when given, the closure result."""
    contract, org = contracts(SOURCE)
    expected_namespace = (contract.get("repository_ledger_location") or {}).get("namespace")
    if repository_namespace != expected_namespace:
        raise ReadbackRefused("REPOSITORY_NAMESPACE_MATCHES_THE_CONTRACT", str(repository_namespace))
    org_required = list(org.get("required_fields") or [])
    repo_required = list(contract.get("required_fields") or [])
    organization_store = ledger_store.open_store(ledger_store.parse_locator(location))
    repository_store = ledger_store.open_store(_repository_location(location, repository_namespace))
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
        "disposition": "ALLOW",
        "disposition_is_transition_evidence": True,
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
    }
    if ingress_result is None:
        return result
    if ingress_result.get("disposition") != "ALLOW":
        raise ReadbackRefused("INGRESS_DISPOSITION_IS_ALLOW", str(ingress_result.get("disposition")))
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
    result.update({
        "ingress_disposition": "ALLOW",
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
        "disposition": "FAIL_CLOSED",
        "disposition_is_transition_evidence": True,
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
            result, code = readback(args.location, ingress, args.repository_namespace), 0
    except (ReadbackRefused, ValueError, RuntimeError, FileNotFoundError, OSError) as exc:
        result, code = _refusal(location, exc), 1
    text = json.dumps(result, indent=2, sort_keys=True)
    print(text)
    if args.out is not None:
        try:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text + "\n", encoding="utf-8")
        except OSError as exc:
            print(json.dumps(out_write_refusal(exc), indent=2, sort_keys=True))
            return 1
    return code


def out_write_refusal(exc: OSError) -> dict[str, Any]:
    """Six fields, non-terminal, for a report file that could not be written; the readback itself wrote nothing."""
    return {"disposition": "FAIL_CLOSED", "failure_code": "READBACK_RESULT_WRITE_FAILED",
            "failed_predicate": "OUT_FILE_ACCEPTS_THE_RESULT:" + type(exc).__name__,
            "detail": type(exc).__name__ + ": " + str(exc),
            "required_evidence_or_repair": "an --out path whose directory accepts a file write",
            "retry_entrypoint": "resident-runtime/organization_ledger_readback.py --out <writable path>",
            "owning_existing_goal": "INTERLOCK-INTR-RUNTIME-INDEPENDENT-PROTOCOL-001",
            "next_attempt": "re-run the same invocation; a readback writes nothing",
            "terminal": False, "derived_from_clock": False, "consequence_committed": False}


if __name__ == "__main__":
    sys.exit(main())
