#!/usr/bin/env python3
"""Main-merge transfer: a merge on main is recorded on this organization's own ledger.

A commit that lands on ``refs/heads/main`` is a state transition within this
organization (org-contract.json organization_scope_rule
EVERY_STATE_TRANSITION_OCCURRING_WITHIN_THE_ORGANIZATION_EMITS_AN_ORGANIZATION_RECEIPT).
The push that lands it triggers .github/workflows/organization-ledger-merge-transfer.yml,
which appends one repository receipt and one organization receipt through the
existing two-level append (resident-runtime/intr_destination_ingress.py::_append,
over .stegverse/transition-ledger/emit.py and
resident-runtime/aggregate_repo_transition.py on resident-runtime/ledger_store.py)
under the shared ORGANIZATION_LEDGER_LOCK (concurrency group organization-ledger).

A transfer is VERIFIED only when this organization's ledger holds a sealed
organization receipt whose boundary evidence names this operation, this
repository and the merge commit, and whose repository receipt (by
repo_receipt_sha256) is sealed and carries the same merge commit and
repository in its evidence. Absent or unsealed evidence is FAIL_CLOSED
MERGE_TRANSFER_RECEIPT_NOT_LEDGERED; present, sealed evidence that contradicts
the merge is DENY MERGE_TRANSFER_RECEIPT_LINK_UNVERIFIED; both carry six fields.

The epoch is not read from a clock and is not a dispatch input: it is the
supplied epoch on this ledger's head receipt (hb_reference.epoch, an int >= 32
with derived_from_clock exactly false), the head the append compares against.
A ledger with no head, an unsealed head or a clock-derived head epoch is
FAIL_CLOSED HB_EPOCH_SOURCE_UNOBSERVED and nothing is written.

Pure apart from the ledger store and, in the CLI only, ``git cat-file``.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]


def _module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ingress = _module("intr_destination_ingress_for_merge_transfer", "resident-runtime/intr_destination_ingress.py")

OPERATION = "ORGANIZATION_MAIN_MERGE_TRANSFER"
TRANSITION_CLASS = OPERATION
PROFILE_ID = "main-merge-transfer"
EVIDENCE_SCHEMA = "stegverse.main-merge-transfer-evidence/v1"
RESULT_SCHEMA = "stegverse.main-merge-transfer-result/v1"
PENDING_SCHEMA = "stegverse.main-merge-transfer-pending/v1"
MAIN_REF = "refs/heads/main"
GOAL = "StegVerse-org/.github#162 (INTERLOCK-INTR-RUNTIME-INDEPENDENT-PROTOCOL-001)"
ENTRYPOINT = ".github/workflows/organization-ledger-merge-transfer.yml (push to main)"
SIX_FIELDS = ("failure_code", "failed_predicate", "required_evidence_or_repair",
              "retry_entrypoint", "owning_existing_goal", "next_attempt")

ALLOW, DENY, FAIL_CLOSED = "ALLOW", "DENY", "FAIL_CLOSED"
NOT_LEDGERED = "MERGE_TRANSFER_RECEIPT_NOT_LEDGERED"
LINK_UNVERIFIED = "MERGE_TRANSFER_RECEIPT_LINK_UNVERIFIED"
EPOCH_UNOBSERVED = "HB_EPOCH_SOURCE_UNOBSERVED"
FACTS_INVALID = "MERGE_COMMIT_FACTS_INVALID"
COLLISION = "MERGE_TRANSFER_RECEIPT_COLLISION"
WRITER_REFUSED = "LEDGER_WRITER_REFUSED"

SHA40 = re.compile(r"[0-9a-f]{40}")
PR_SUBJECT = re.compile(r"Merge pull request #([0-9]+) from \S+")

sha = ingress.sha


def repository() -> str:
    return ingress.repository_ledger.C["repository"]


def refusal(code: str, predicate: str, repair: str, next_attempt: str, *, disposition: str = FAIL_CLOSED,
            **extra: Any) -> dict[str, Any]:
    out = {"schema": RESULT_SCHEMA, "disposition": disposition, "failure_code": code,
           "failed_predicate": predicate, "required_evidence_or_repair": repair,
           "retry_entrypoint": ENTRYPOINT, "owning_existing_goal": GOAL, "next_attempt": next_attempt}
    out.update(extra)
    return out


# -- commit facts -----------------------------------------------------------

def commit_facts(commit: str, git_dir: Path | str = ROOT) -> dict[str, Any]:
    """tree, parents and subject of one commit, read from the object itself (git cat-file)."""
    raw = subprocess.run(["git", "-C", str(git_dir), "cat-file", "commit", commit],
                         check=True, capture_output=True, text=True).stdout
    header, _, message = raw.partition("\n\n")
    tree, parents = None, []
    for line in header.splitlines():
        key, _, value = line.partition(" ")
        if key == "tree":
            tree = value
        elif key == "parent":
            parents.append(value)
    full = subprocess.run(["git", "-C", str(git_dir), "rev-parse", "--verify", commit + "^{commit}"],
                          check=True, capture_output=True, text=True).stdout.strip()
    return {"merge_commit": full, "tree": tree, "parents": parents,
            "subject": message.splitlines()[0] if message else ""}


def facts_failure(facts: Any) -> str | None:
    if not isinstance(facts, Mapping):
        return "commit facts are not an object"
    if not isinstance(facts.get("merge_commit"), str) or not SHA40.fullmatch(facts["merge_commit"]):
        return "merge_commit is not 40 lowercase hex"
    if not isinstance(facts.get("tree"), str) or not SHA40.fullmatch(facts["tree"]):
        return "tree is not 40 lowercase hex"
    parents = facts.get("parents")
    if not isinstance(parents, list) or not parents or not all(isinstance(p, str) and SHA40.fullmatch(p) for p in parents):
        return "parents is not a non-empty list of 40 lowercase hex"
    if not isinstance(facts.get("subject"), str):
        return "subject is not text"
    return None


def merge_evidence(repo: str, facts: Mapping[str, Any]) -> dict[str, Any]:
    """The repository receipt's evidence: the merge commit, bound to this repository and main."""
    match = PR_SUBJECT.fullmatch(facts["subject"]) if len(facts["parents"]) == 2 else None
    core = {"schema": EVIDENCE_SCHEMA, "repository": repo, "ref": MAIN_REF,
            "merge_commit": facts["merge_commit"], "tree": facts["tree"],
            "parents": list(facts["parents"]),
            "merge_kind": "MERGE_COMMIT" if len(facts["parents"]) == 2 else "FIRST_PARENT_COMMIT",
            # Declared by the merge commit's own subject; the verifier binds on merge_commit and repository only.
            "pull_request": int(match.group(1)) if match else None,
            "pull_request_source": "MERGE_COMMIT_SUBJECT" if match else None}
    return {**core, "payload_sha256": sha(core), "disposition": ALLOW,
            "communication_id": PROFILE_ID + ":" + repo + "@" + facts["merge_commit"]}


# -- ledger reads -----------------------------------------------------------

def _store(module: Any) -> Any:
    root = module.lr() if hasattr(module, "lr") else module.ledger_root()
    return store_module().open_store(root)


def org_store() -> Any:
    return _store(ingress.organization_ledger)


def repo_store() -> Any:
    return _store(ingress.repository_ledger)


def _get(store: Any, key: str) -> Any:
    try:
        return store.get(key)
    except (FileNotFoundError, KeyError):
        return None


def sealed(receipt: Any) -> bool:
    return (isinstance(receipt, Mapping) and isinstance(receipt.get("receipt_sha256"), str)
            and sha({k: v for k, v in receipt.items() if k != "receipt_sha256"}) == receipt["receipt_sha256"])


def _receipt(store: Any, digest: Any) -> Any:
    if not isinstance(digest, str):
        return None
    return _get(store, store_module().receipt_key(digest))


def store_module() -> Any:
    """resident-runtime/ledger_store.py, as the repository writer (emit.py) loaded it."""
    return ingress.repository_ledger.ledger_store


def head_epoch(store: Any) -> tuple[int | None, dict[str, Any] | None]:
    """The supplied epoch on the ledger head receipt, or a FAIL_CLOSED refusal."""
    def unobserved(detail: str) -> tuple[None, dict[str, Any]]:
        return None, refusal(EPOCH_UNOBSERVED, "MERGE_TRANSFER_EPOCH_IS_THE_LEDGER_HEAD_SUPPLIED_EPOCH",
                             "the organization ledger head receipt must be sealed and carry hb_reference.epoch, "
                             "an int >= 32 with derived_from_clock exactly false; no clock is read",
                             "re-run the push's merge-transfer workflow once the head carries a supplied epoch; "
                             "nothing was written", detail=detail)
    head = _get(store, store_module().HEAD_KEY)
    if not isinstance(head, Mapping):
        return unobserved("the organization ledger has no HEAD")
    receipt = _receipt(store, head.get("receipt_sha256"))
    if not sealed(receipt):
        return unobserved("the organization ledger head receipt is absent or unsealed")
    reference = receipt.get("hb_reference")
    epoch = reference.get("epoch") if isinstance(reference, Mapping) else None
    if type(epoch) is not int or epoch < 32 or reference.get("derived_from_clock") is not False:
        return unobserved("head hb_reference " + repr(reference) + " is not a supplied epoch")
    return epoch, None


def org_chain(store: Any) -> Iterable[Any]:
    """Organization receipts from HEAD back through previous_receipt_sha256."""
    head = _get(store, store_module().HEAD_KEY)
    digest = head.get("receipt_sha256") if isinstance(head, Mapping) else None
    seen = set()
    while isinstance(digest, str) and digest not in seen:
        seen.add(digest)
        receipt = _receipt(store, digest)
        yield digest, receipt
        if not isinstance(receipt, Mapping):
            return
        digest = receipt.get("previous_receipt_sha256")


def _boundary(receipt: Mapping[str, Any]) -> Mapping[str, Any]:
    value = receipt.get("boundary_evidence", receipt.get("evidence"))
    return value if isinstance(value, Mapping) else {}


def verify_merge_transfer(repo: str, merge_commit: str, organization: Any = None,
                          repository_store: Any = None) -> dict[str, Any]:
    """VERIFIED (ALLOW) only when the org ledger receipt exists, is sealed and links to the merge commit.

    Absent means FAIL_CLOSED, contradicted means DENY (as in the epoch rule):
      FAIL_CLOSED MERGE_TRANSFER_RECEIPT_NOT_LEDGERED: no organization receipt names this operation, repository and
        merge commit; or a receipt on the chain, or the named repository receipt, is absent or unsealed.
      DENY MERGE_TRANSFER_RECEIPT_LINK_UNVERIFIED: the evidence is present and sealed but contradicts the merge: the
        organization receipt names another repository, or its repository receipt names another merge commit,
        repository, ref or evidence schema, or does not carry the evidence digest it claims.
    """
    organization = organization if organization is not None else org_store()
    repository_store = repository_store if repository_store is not None else repo_store()
    base = {"repository": repo, "merge_commit": merge_commit, "operation": OPERATION}

    def not_ledgered(detail: str, **extra: Any) -> dict[str, Any]:
        return refusal(NOT_LEDGERED, "MAIN_MERGE_HAS_AN_ORGANIZATION_LEDGER_RECEIPT",
                       "a sealed organization receipt on this organization's ledger with boundary_evidence.operation "
                       + OPERATION + ", repository " + repo + " and merge_commit " + merge_commit
                       + ", linked to a sealed repository receipt carrying the same merge",
                       "the next push to main runs " + ENTRYPOINT + ", which appends every listed pending merge "
                       "(.stegverse/merge-transfer/pending.json) and the pushed range; verify again after it reads back",
                       **base, transfer="UNVERIFIED", detail=detail, **extra)

    for digest, receipt in org_chain(organization):
        if not sealed(receipt) or receipt["receipt_sha256"] != digest:
            return not_ledgered("organization receipt " + digest + " on the chain is absent or its seal does not verify",
                                organization_receipt_sha256=digest)
        boundary = _boundary(receipt)
        if boundary.get("operation") != OPERATION or boundary.get("merge_commit") != merge_commit \
                or boundary.get("repository") != repo:
            continue
        linked = receipt.get("repo_receipt_sha256")
        repo_receipt = _receipt(repository_store, linked)
        if not sealed(repo_receipt):
            return not_ledgered("repository receipt " + str(linked) + " is absent or unsealed",
                                organization_receipt_sha256=digest)
        evidence = repo_receipt.get("evidence")
        evidence = evidence if isinstance(evidence, Mapping) else {}
        contradictions = []
        named = receipt.get("repository", receipt.get("source_repository"))
        if named != repo:
            contradictions.append("organization receipt names repository " + repr(named))
        if repo_receipt["receipt_sha256"] != linked:
            contradictions.append("repository receipt at " + str(linked) + " is sealed as " + repo_receipt["receipt_sha256"])
        for field, want in (("schema", EVIDENCE_SCHEMA), ("merge_commit", merge_commit), ("repository", repo),
                            ("ref", MAIN_REF)):
            if evidence.get(field) != want:
                contradictions.append("repository receipt evidence " + field + " is " + repr(evidence.get(field)))
        if evidence.get("payload_sha256") != sha({k: v for k, v in evidence.items()
                                                  if k not in ("payload_sha256", "disposition", "communication_id")}):
            contradictions.append("repository receipt evidence does not carry the digest it claims")
        if contradictions:
            return refusal(LINK_UNVERIFIED, "ORGANIZATION_RECEIPT_LINKS_TO_THE_MERGE_COMMIT",
                           "the organization receipt's repository receipt must carry merge_commit, repository and ref "
                           "equal to the merge being verified; this ledger records evidence that contradicts it",
                           "inspect organization receipt " + digest + " and repository receipt " + str(linked)
                           + "; nothing was verified and nothing is appended over a contradiction",
                           disposition=DENY, **base, transfer="UNVERIFIED", organization_receipt_sha256=digest,
                           repository_receipt_sha256=linked, detail="; ".join(contradictions))
        return {"schema": RESULT_SCHEMA, "disposition": ALLOW, "transfer": "VERIFIED", **base,
                "organization_receipt_sha256": digest,
                "repository_receipt_sha256": repo_receipt["receipt_sha256"],
                "pull_request": evidence.get("pull_request"),
                "hb_epoch": (receipt.get("hb_reference") or {}).get("epoch")}
    return not_ledgered("no organization receipt names " + OPERATION + " for " + repo + "@" + merge_commit)


# -- append -----------------------------------------------------------------

def append_merge_transfer(facts: Mapping[str, Any]) -> dict[str, Any]:
    """Append the merge's repository and organization receipts, or refuse with six fields; never a partial write."""
    repo = repository()
    bad = facts_failure(facts)
    if bad is not None:
        return refusal(FACTS_INVALID, "MERGE_COMMIT_FACTS_ARE_READ_FROM_THE_COMMIT_OBJECT",
                       "pass the commit's tree, parents and subject as git cat-file reads them",
                       "re-run on the pushed commit; nothing was written", repository=repo, detail=bad)
    epoch, failed = head_epoch(org_store())
    if failed is not None:
        return {**failed, "repository": repo, "merge_commit": facts["merge_commit"]}
    evidence = merge_evidence(repo, facts)
    try:
        appended, replayed = ingress._append(
            TRANSITION_CLASS, facts["merge_commit"], evidence, epoch, operation=OPERATION, profile_id=PROFILE_ID,
            boundary_extra={"repository": repo, "merge_commit": facts["merge_commit"]})
    except ValueError as exc:
        code = COLLISION if str(exc) == "ledger_receipt_collision" else WRITER_REFUSED
        return refusal(code, "ONE_MERGE_COMMIT_BINDS_ONE_TRANSFER_RECEIPT",
                       "the ledger already binds this merge commit to different evidence, or the writer refused: " + str(exc),
                       "inspect the recorded receipt; nothing was written", repository=repo,
                       merge_commit=facts["merge_commit"], detail=str(exc))
    except SystemExit as exc:
        found = ingress.ledger_writer_refusal(exc, ENTRYPOINT, GOAL) if hasattr(ingress, "ledger_writer_refusal") else None
        if found is None and not isinstance(exc.code, str):
            raise
        record = found[0] if found else refusal(WRITER_REFUSED, "LEDGER_WRITER_ADMITS_THE_APPEND",
                                                "repair what the ledger writer refused (" + str(exc.code) + ")",
                                                "re-run once repaired; nothing was written")
        return {**record, "schema": RESULT_SCHEMA, "repository": repo, "merge_commit": facts["merge_commit"]}
    return {"schema": RESULT_SCHEMA, "disposition": ALLOW, "operation": OPERATION, "repository": repo,
            "merge_commit": facts["merge_commit"], "pull_request": evidence["pull_request"], "hb_epoch": epoch,
            "replayed": replayed,
            "repository_receipt_sha256": appended["repository_receipt"]["receipt_sha256"],
            "organization_receipt_sha256": appended["organization_receipt"]["receipt_sha256"]}


# -- which commits a push carries ------------------------------------------

def pending(path: Path | str) -> list[str]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if document.get("schema") != PENDING_SCHEMA or document.get("repository") != repository():
        raise SystemExit("FAIL_CLOSED: PENDING_MANIFEST_INVALID " + str(path))
    commits = [row["merge_commit"] for row in document["merges"]]
    if not all(isinstance(c, str) and SHA40.fullmatch(c) for c in commits):
        raise SystemExit("FAIL_CLOSED: PENDING_MANIFEST_INVALID " + str(path))
    return commits


def _git(*args: str, git_dir: Path | str = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(git_dir), *args], capture_output=True, text=True)


def pushed_commits(before: str, after: str, pending_path: Path | str | None, git_dir: Path | str = ROOT) -> list[str]:
    """Pending merges reachable from ``after`` (oldest first), then the pushed first-parent range."""
    out: list[str] = []
    for commit in pending(pending_path) if pending_path else []:
        if _git("merge-base", "--is-ancestor", commit, after, git_dir=git_dir).returncode != 0:
            raise SystemExit("FAIL_CLOSED: PENDING_MERGE_NOT_ON_MAIN " + commit)
        out.append(commit)
    zero = before is None or not SHA40.fullmatch(before or "") or set(before) == {"0"}
    if zero or _git("merge-base", "--is-ancestor", before, after, git_dir=git_dir).returncode != 0:
        pushed = [after]
    else:
        listed = _git("rev-list", "--first-parent", "--reverse", before + ".." + after, git_dir=git_dir)
        if listed.returncode != 0:
            raise SystemExit("FAIL_CLOSED: PUSHED_RANGE_UNREADABLE " + listed.stderr.strip())
        pushed = listed.stdout.split()
    for commit in pushed:
        if commit not in out:
            out.append(commit)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("commits", help="the commits a push to main carries")
    c.add_argument("--before", default="")
    c.add_argument("--after", required=True)
    c.add_argument("--pending")
    for name in ("append", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--commit", action="append", default=[])
        p.add_argument("--commits-file")
        p.add_argument("--out")
        if name == "verify":
            p.add_argument("--expect", choices=(ALLOW, FAIL_CLOSED), default=ALLOW)
    args = parser.parse_args(argv)
    if args.command == "commits":
        print("\n".join(pushed_commits(args.before, args.after, args.pending)))
        return 0
    commits = list(args.commit)
    if args.commits_file:
        commits += Path(args.commits_file).read_text(encoding="utf-8").split()
    if not commits:
        print(json.dumps(refusal(FACTS_INVALID, "A_PUSH_NAMES_ITS_COMMITS", "name at least one commit",
                                 "re-run with --commit or --commits-file; nothing was done"), indent=2))
        return 1
    results = []
    for commit in commits:
        if args.command == "append":
            results.append(append_merge_transfer(commit_facts(commit)))
        else:
            results.append(verify_merge_transfer(repository(), commit_facts(commit)["merge_commit"]))
    text = json.dumps(results, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    expected = args.expect if args.command == "verify" else ALLOW
    for result in results:
        if result["disposition"] != expected:
            return 1
        if expected == FAIL_CLOSED and not all(result.get(f) for f in SIX_FIELDS):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
