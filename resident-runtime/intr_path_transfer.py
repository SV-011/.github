#!/usr/bin/env python3
"""InTr mesh path transfer: a declared path (source organization to destination
organization, per declared capability) is exercised by a real transfer
transition on main, and leaves its own sealed path receipt on the ledger.

Declared paths: every ordered pair of distinct organizations whose destination
documents conform to data/intr-protocol/v1/protocol.json in the pinned mesh
snapshot (tests/fixtures/intr-protocol/destinations/manifest.json), for the one
declared capability, intr-destination. A path is addressed
``<SOURCE>-><DESTINATION>:intr-destination``.

Transition: a transfer declared in .stegverse/intr-path-transfer/transfers.json
lands on main. After the push's main-merge transfer is ledgered
(.github/workflows/organization-ledger-merge-transfer.yml), the writer
.github/workflows/intr-path-transfer.yml appends, for each declared transfer
whose source is this organization and that is not yet ledgered, one repository
receipt and one organization receipt through the existing two-level append
(resident-runtime/intr_destination_ingress.py::_append), operation
INTR_MESH_PATH_TRANSFER. The repository receipt's evidence
(stegverse.intr-path-transfer-evidence/v1) carries the InTr message (it must
conform to the protocol), the derived destination address, and the link to the
transfer: the pushed merge commit and the seal of its main-merge transfer
organization receipt. No dispatch, schedule, clock or cross-organization call.

Verification, per path, has two halves:
  source half: a sealed organization receipt (operation INTR_MESH_PATH_TRANSFER,
    path_id, communication_id) linked to a sealed repository receipt whose
    evidence names the same path and message, and whose merge-transfer link
    resolves to a sealed ORGANIZATION_MAIN_MERGE_TRANSFER receipt on the same
    chain for the same commit;
  destination half: the destination organization's own ledger holds a sealed
    ORGANIZATION_INTR_DESTINATION_INGRESS receipt for the same communication_id,
    linked to its sealed repository receipt. That readback needs a carrier that
    delivers the message to the destination and a TV/TVC-issued cross-organization
    read credential; neither exists here, so it is FAIL_CLOSED class B unless a
    readback is supplied.
ALLOW only when both halves verify. Absent or unsealed evidence is FAIL_CLOSED;
present, sealed evidence that contradicts the path is DENY. Six fields each.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]


def _module(name: str, relative: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mt = _module("merge_transfer_for_path_transfer", "resident-runtime/merge_transfer.py")
intr = _module("intr_protocol_for_path_transfer", "org-runtime/intr_protocol.py")
ingress = mt.ingress

OPERATION = "INTR_MESH_PATH_TRANSFER"
TRANSITION_CLASS = OPERATION
PROFILE_ID = "intr-mesh-path-transfer"
CAPABILITY = "intr-destination"
EVIDENCE_SCHEMA = "stegverse.intr-path-transfer-evidence/v1"
RESULT_SCHEMA = "stegverse.intr-path-transfer-result/v1"
DECLARATION_SCHEMA = "stegverse.intr-path-transfer-declaration/v1"
DESTINATION_OPERATION = "ORGANIZATION_INTR_DESTINATION_INGRESS"
GOAL = "StegVerse-org/.github#162"
ENTRYPOINT = (".github/workflows/intr-path-transfer.yml (after Organization Ledger Main Merge "
              "Transfer completes on a push to main)")
SNAPSHOT = ROOT / "tests" / "fixtures" / "intr-protocol" / "destinations" / "manifest.json"
DECLARATION = ROOT / ".stegverse" / "intr-path-transfer" / "transfers.json"
SIX_FIELDS = mt.SIX_FIELDS
ALLOW, DENY, FAIL_CLOSED = "ALLOW", "DENY", "FAIL_CLOSED"

NOT_LEDGERED = "INTR_PATH_RECEIPT_NOT_LEDGERED"
LINK_UNVERIFIED = "INTR_PATH_RECEIPT_LINK_UNVERIFIED"
CREDENTIAL_NOT_ISSUED = "INTR_PATH_CROSS_ORG_READ_CREDENTIAL_NOT_ISSUED"
DESTINATION_NOT_LEDGERED = "INTR_PATH_DESTINATION_RECEIPT_NOT_LEDGERED"
DESTINATION_LINK_UNVERIFIED = "INTR_PATH_DESTINATION_RECEIPT_LINK_UNVERIFIED"
TRANSFER_NOT_LEDGERED = "INTR_PATH_TRANSFER_COMMIT_NOT_LEDGERED"
PATH_UNDECLARED = "INTR_PATH_UNDECLARED"
MESSAGE_REFUSED = "INTR_PATH_MESSAGE_NONCONFORMANT"

sha = ingress.sha
sealed = mt.sealed


def repository() -> str:
    return mt.repository()


def organization() -> str:
    return repository().split("/")[0]


def refusal(disposition: str, code: str, predicate: str, repair: str, next_attempt: str,
            *, entrypoint: str = ENTRYPOINT, **extra: Any) -> dict[str, Any]:
    out = {"schema": RESULT_SCHEMA, "disposition": disposition, "failure_code": code,
           "failed_predicate": predicate, "required_evidence_or_repair": repair,
           "retry_entrypoint": entrypoint, "owning_existing_goal": GOAL, "next_attempt": next_attempt}
    out.update(extra)
    return out


# -- declared paths ----------------------------------------------------------

def path_id(source: str, destination: str) -> str:
    return source + "->" + destination + ":" + CAPABILITY


def declared_paths(manifest_path: Path | str = SNAPSHOT) -> list[dict[str, Any]]:
    """Ordered pairs of distinct organizations whose destination documents conform (ALLOW)."""
    report = intr.conform_mesh(manifest_path)
    contract = intr.load_contract()
    conformant = sorted(r["subject"]["organization"] for r in report["results"] if r["disposition"] == ALLOW)
    return [{"path_id": path_id(s, d), "source": s, "destination": d, "capability": CAPABILITY,
             "destination_address": intr.destination_address(d, contract)}
            for s in conformant for d in conformant if s != d]


def load_declaration(path: Path | str = DECLARATION) -> list[dict[str, Any]]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if document.get("schema") != DECLARATION_SCHEMA or document.get("source_repository") != repository():
        raise SystemExit("FAIL_CLOSED: INTR_PATH_DECLARATION_INVALID " + str(path))
    return list(document["transfers"])


def message(path: Mapping[str, Any], communication_id: str, transfer_commit: str) -> dict[str, Any]:
    payload_schema = next(p["equals"] for p in intr.load_contract()["destination_capability"]["binding_predicates"]
                          if p["id"] == "BINDING_PAYLOAD_SCHEMA")
    return {"message_class": "ecosystem.communication", "communication_id": communication_id,
            "body": {"schema": payload_schema, "path_id": path["path_id"], "transfer_commit": transfer_commit}}


def path_evidence(path: Mapping[str, Any], communication_id: str, transfer_commit: str,
                  merge_transfer_receipt: str) -> dict[str, Any]:
    msg = message(path, communication_id, transfer_commit)
    core = {"schema": EVIDENCE_SCHEMA, "repository": repository(), "path_id": path["path_id"],
            "source_organization": path["source"], "destination_organization": path["destination"],
            "capability": path["capability"], "destination_address": path["destination_address"],
            "message": msg, "message_sha256": sha(msg), "transfer_commit": transfer_commit,
            "merge_transfer_organization_receipt_sha256": merge_transfer_receipt}
    return {**core, "payload_sha256": sha(core), "disposition": ALLOW, "communication_id": communication_id}


def _payload_ok(evidence: Mapping[str, Any]) -> bool:
    return evidence.get("payload_sha256") == sha({k: v for k, v in evidence.items()
                                                  if k not in ("payload_sha256", "disposition", "communication_id")})


# -- source half --------------------------------------------------------------

def _chain(store: Any) -> list[tuple[str, Any]]:
    return list(mt.org_chain(store))


def verify_source(path: Mapping[str, Any], communication_id: str, organization_store: Any = None,
                  repository_store: Any = None) -> dict[str, Any]:
    organization_store = organization_store if organization_store is not None else mt.org_store()
    repository_store = repository_store if repository_store is not None else mt.repo_store()
    base = {"path_id": path["path_id"], "communication_id": communication_id, "half": "source"}

    def not_ledgered(detail: str) -> dict[str, Any]:
        return refusal(FAIL_CLOSED, NOT_LEDGERED, "PATH_HAS_A_SOURCE_LEDGER_RECEIPT",
                       "a sealed organization receipt on " + path["source"] + "'s ledger with boundary_evidence.operation "
                       + OPERATION + ", path_id " + path["path_id"] + " and communication_id " + communication_id
                       + ", linked to a sealed repository receipt and to the transfer's main-merge transfer receipt",
                       "receipts appear only after the transfer declaration lands on " + path["source"]
                       + "/.github main and " + ENTRYPOINT + " appends it; verify again after it reads back",
                       **base, detail=detail)

    chain = _chain(organization_store)
    by_digest = {d: r for d, r in chain}
    for digest, receipt in chain:
        if not sealed(receipt) or receipt["receipt_sha256"] != digest:
            return not_ledgered("organization receipt " + digest + " on the chain is absent or unsealed")
        boundary = mt._boundary(receipt)
        if boundary.get("operation") != OPERATION or boundary.get("path_id") != path["path_id"] \
                or boundary.get("communication_id") != communication_id:
            continue
        linked = receipt.get("repo_receipt_sha256")
        repo_receipt = mt._receipt(repository_store, linked)
        if not sealed(repo_receipt):
            return not_ledgered("repository receipt " + str(linked) + " is absent or unsealed")
        evidence = repo_receipt.get("evidence") if isinstance(repo_receipt.get("evidence"), Mapping) else {}
        problems = []
        if repo_receipt["receipt_sha256"] != linked:
            problems.append("repository receipt at " + str(linked) + " is sealed as " + repo_receipt["receipt_sha256"])
        for field, want in (("schema", EVIDENCE_SCHEMA), ("path_id", path["path_id"]),
                            ("source_organization", path["source"]), ("destination_organization", path["destination"]),
                            ("capability", path["capability"]), ("destination_address", path["destination_address"]),
                            ("communication_id", communication_id)):
            if evidence.get(field) != want:
                problems.append("evidence " + field + " is " + repr(evidence.get(field)))
        if not _payload_ok(evidence):
            problems.append("evidence does not carry the digest it claims")
        msg = evidence.get("message")
        if sha(msg) != evidence.get("message_sha256") or not isinstance(msg, Mapping) \
                or msg.get("communication_id") != communication_id:
            problems.append("message does not match its digest or communication_id")
        elif intr.conform_message(msg, path["destination"])["disposition"] != ALLOW:
            problems.append("message does not conform to the InTr protocol")
        transfer = by_digest.get(evidence.get("merge_transfer_organization_receipt_sha256"))
        tb = mt._boundary(transfer) if isinstance(transfer, Mapping) else {}
        if not sealed(transfer) or tb.get("operation") != mt.OPERATION \
                or tb.get("merge_commit") != evidence.get("transfer_commit") or tb.get("repository") != repository():
            problems.append("merge-transfer link " + str(evidence.get("merge_transfer_organization_receipt_sha256"))
                            + " is not a sealed main-merge transfer receipt for " + str(evidence.get("transfer_commit")))
        if problems:
            return refusal(DENY, LINK_UNVERIFIED, "PATH_RECEIPT_LINKS_TO_THE_TRANSFER",
                           "the path receipt's repository receipt must carry this path, message and address and link "
                           "the sealed main-merge transfer receipt of its transfer commit; the ledger contradicts it",
                           "inspect organization receipt " + digest + "; nothing was verified", **base,
                           organization_receipt_sha256=digest, detail="; ".join(problems))
        return {"schema": RESULT_SCHEMA, "disposition": ALLOW, "half": "source", "path_id": path["path_id"],
                "communication_id": communication_id, "organization_receipt_sha256": digest,
                "repository_receipt_sha256": linked, "transfer_commit": evidence["transfer_commit"],
                "merge_transfer_organization_receipt_sha256": evidence["merge_transfer_organization_receipt_sha256"]}
    return not_ledgered("no organization receipt names " + OPERATION + " for " + path["path_id"] + " / " + communication_id)


# -- destination half --------------------------------------------------------

def path_ingress():
    return _module("intr_path_ingress_for_path_transfer", "resident-runtime/intr_path_ingress.py")


def verify_destination(path: Mapping[str, Any], communication_id: str, source: Mapping[str, Any] | None = None,
                       readback: Any = None) -> dict[str, Any]:
    """The destination ledger's ingress receipts for this path, linking the source receipts by seal.

    The destination is another organization's internal repository: without a supplied readback
    (stegverse.intr-path-ledger-readback/v1) it is FAIL_CLOSED INTR_PATH_CROSS_ORG_READ_CREDENTIAL_NOT_ISSUED."""
    pi = path_ingress()
    refused = None
    if readback is None and source is not None and source.get("disposition") == ALLOW:
        # the destination ledger, read through the TV/TVC issuance transition (resident-runtime/intr_path_tvc_read.py)
        readback, refused = pi.tvc_readback(path["path_id"], communication_id, path["destination"] + "/.github")
    if readback is None or source is None or source.get("disposition") != ALLOW:
        base = refused or pi.inclusion_refusal(path["destination"] + "/.github", path["path_id"], communication_id)
        if base.get("failure_code") != pi.CREDENTIAL_NOT_ISSUED:
            return {**base, "half": "destination"}
        return {**base, "half": "destination", "failed_predicate": "PATH_HAS_A_DESTINATION_LEDGER_RECEIPT",
                "required_evidence_or_repair":
                    "the destination's sealed " + DESTINATION_OPERATION + " receipt for this path, read from "
                    + path["destination"] + "/.github refs/stegverse/organization-ledger through the TV/TVC issuance "
                    "transition (" + pi.tvc_read.TVC_TRANSITION + ", scope " + path["path_id"] + "#destination_readback; "
                    "GITHUB_TOKEN reads only this repository; no bot holds a credential). The receipt itself exists "
                    "only after the inbound row lands on the destination's main"}
    link = {"path_id": path["path_id"], "communication_id": communication_id,
            "source_organization_receipt_sha256": source["organization_receipt_sha256"],
            "source_repository_receipt_sha256": source["repository_receipt_sha256"]}
    if not isinstance(readback, Mapping) or not pi._included(readback, readback.get("organization_receipt") or {}):
        return pi.verify_destination_pair(None, None, link, path["destination"] + "/.github")
    return pi.verify_destination_pair(readback.get("organization_receipt"), readback.get("repository_receipt"), link,
                                      path["destination"] + "/.github")


def verify_path(path: Mapping[str, Any], communication_id: str | None, organization_store: Any = None,
                repository_store: Any = None, destination_readback: Any = None) -> dict[str, Any]:
    """ALLOW only when both halves verify; otherwise the first non-ALLOW half, with six fields."""
    if communication_id is None or path["source"] != organization():
        source = refusal(FAIL_CLOSED, NOT_LEDGERED, "PATH_HAS_A_SOURCE_LEDGER_RECEIPT",
                         "class B (missing capability) when the source is another organization: install the path-transfer "
                         "transition in " + path["source"] + "/.github and declare a transfer for " + path["path_id"],
                         "receipts appear only after a transfer for this path lands on " + path["source"]
                         + "/.github main and its writer appends it", path_id=path["path_id"], half="source",
                         communication_id=communication_id,
                         classification="B" if path["source"] != organization() else None,
                         detail="no transfer for this path is declared in this organization")
    else:
        source = verify_source(path, communication_id, organization_store, repository_store)
    destination = verify_destination(path, communication_id or "", source, destination_readback)
    halves = {"source": source["disposition"], "destination": destination["disposition"]}
    if source["disposition"] == ALLOW and destination["disposition"] == ALLOW:
        return {"schema": RESULT_SCHEMA, "disposition": ALLOW, "path_id": path["path_id"], "path": "VERIFIED",
                "communication_id": communication_id, "halves": halves, "source": source, "destination": destination}
    first = source if source["disposition"] != ALLOW else destination
    return {**first, "path": "UNVERIFIED", "halves": halves, "path_id": path["path_id"]}


# -- append -------------------------------------------------------------------

def append_transfers(transfer_commit: str, declaration: Path | str = DECLARATION,
                     manifest: Path | str = SNAPSHOT) -> list[dict[str, Any]]:
    paths = {p["path_id"]: p for p in declared_paths(manifest)}
    repo = repository()
    verified = mt.verify_merge_transfer(repo, transfer_commit)
    if verified["disposition"] != ALLOW:
        return [refusal(FAIL_CLOSED, TRANSFER_NOT_LEDGERED, "PATH_TRANSFER_RIDES_A_LEDGERED_MAIN_MERGE",
                        "the transfer commit " + transfer_commit + " must have a VERIFIED main-merge transfer receipt "
                        "before a path receipt links it (" + str(verified.get("failure_code")) + ")",
                        "re-run after the push's main-merge transfer is ledgered; nothing was written",
                        transfer_commit=transfer_commit)]
    epoch, failed = mt.head_epoch(mt.org_store())
    if failed is not None:
        return [{**failed, "schema": RESULT_SCHEMA, "transfer_commit": transfer_commit}]
    results = []
    for row in load_declaration(declaration):
        pid, cid = row.get("path_id"), row.get("communication_id")
        path = paths.get(pid)
        if path is None or path["source"] != organization():
            results.append(refusal(FAIL_CLOSED, PATH_UNDECLARED, "TRANSFER_NAMES_A_DECLARED_PATH_FROM_THIS_ORGANIZATION",
                                   "declare transfers only for conformant paths whose source is " + organization(),
                                   "correct " + str(DECLARATION.relative_to(ROOT)) + "; nothing was written for this row",
                                   path_id=pid, communication_id=cid))
            continue
        existing = verify_source(path, cid)
        if existing["disposition"] == ALLOW:
            results.append({**existing, "replayed": True})
            continue
        if existing["disposition"] == DENY:
            results.append(existing)
            continue
        evidence = path_evidence(path, cid, transfer_commit, verified["organization_receipt_sha256"])
        conform = intr.conform_message(evidence["message"], path["destination"])
        if conform["disposition"] != ALLOW:
            results.append(refusal(FAIL_CLOSED, MESSAGE_REFUSED, "PATH_MESSAGE_CONFORMS_TO_THE_PROTOCOL",
                                   conform["required_evidence_or_repair"], "correct the message; nothing was written",
                                   path_id=pid, communication_id=cid))
            continue
        try:
            appended, replayed = ingress._append(TRANSITION_CLASS, pid + "@" + cid, evidence, epoch,
                                                 operation=OPERATION, profile_id=PROFILE_ID,
                                                 boundary_extra={"path_id": pid, "repository": repo,
                                                                 "destination_organization": path["destination"],
                                                                 "transfer_commit": transfer_commit})
        except (ValueError, SystemExit) as exc:
            results.append(refusal(FAIL_CLOSED, mt.WRITER_REFUSED, "LEDGER_WRITER_ADMITS_THE_APPEND",
                                   "repair what the ledger writer refused: " + str(exc),
                                   "re-run once repaired; nothing was written for this row", path_id=pid,
                                   communication_id=cid))
            continue
        results.append({"schema": RESULT_SCHEMA, "disposition": ALLOW, "half": "source", "path_id": pid,
                        "communication_id": cid, "transfer_commit": transfer_commit, "hb_epoch": epoch,
                        "replayed": replayed,
                        "organization_receipt_sha256": appended["organization_receipt"]["receipt_sha256"],
                        "repository_receipt_sha256": appended["repository_receipt"]["receipt_sha256"]})
    return results


# -- real state ----------------------------------------------------------------

def real_state(declaration: Path | str = DECLARATION, manifest: Path | str = SNAPSHOT,
               organization_store: Any = None, repository_store: Any = None) -> dict[str, Any]:
    transfers = {row["path_id"]: row["communication_id"] for row in load_declaration(declaration)}
    rows = []
    for path in declared_paths(manifest):
        result = verify_path(path, transfers.get(path["path_id"]), organization_store, repository_store)
        rows.append({"path_id": path["path_id"], "capability": path["capability"],
                     "disposition": result["disposition"], "failure_code": result.get("failure_code"),
                     "failed_predicate": result.get("failed_predicate"), "halves": result["halves"],
                     "classification": result.get("classification")})
    counts = {d: sum(r["disposition"] == d for r in rows) for d in (ALLOW, DENY, FAIL_CLOSED)}
    return {"schema": "stegverse.intr-path-transfer-real-state/v1", "repository": repository(),
            "counts": counts, "paths": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("paths")
    a = sub.add_parser("append")
    a.add_argument("--transfer-commit", required=True)
    a.add_argument("--out")
    v = sub.add_parser("verify")
    v.add_argument("--out")
    v.add_argument("--expect-source", choices=(ALLOW, FAIL_CLOSED))
    args = parser.parse_args(argv)
    if args.command == "paths":
        print(json.dumps(declared_paths(), indent=2))
        return 0
    if args.command == "append":
        result: Any = append_transfers(args.transfer_commit)
    else:
        result = real_state()
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    if args.command == "append":
        return 0 if all(r["disposition"] == ALLOW for r in result) else 1
    if args.expect_source:
        mine = [p for p in result["paths"] if p["path_id"].startswith(organization() + "->")]
        return 0 if mine and all(p["halves"]["source"] == args.expect_source for p in mine) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
