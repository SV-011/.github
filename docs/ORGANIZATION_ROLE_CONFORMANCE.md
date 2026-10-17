# Organization Role conformance: SV-011/.github

Measured 2026-10-17 against `StegVerse-org/.github@c483d441`
`docs/ORGANIZATION_ROLE_RUNTIME_REALITY_DEPLOYMENT.md` (Conformance standard and
Exemption path). This file records how this repository meets that standard. It
does not deploy the Organization Role declaration for SV-011; that declaration
is StegVerse-org's, and an SV-011 declaration is an owner decision.

## The six fields

Standard item 5: "A non-ALLOW result carries `failure_code`, `failed_predicate`,
`required_evidence_or_repair`, `retry_entrypoint`, `owning_existing_goal` and
`next_attempt`; a bare `BLOCKED`, `UNKNOWN` or `NOT_OBSERVED` is not a finding."

`resident-runtime/non_allow.py` builds and checks them. Owning goals are
existing registry tasks only: `SV011-TT-CAPABILITY-MAP-OBSERVATION-001`
(StegVerse-org/.github#68) for the ledger intake and readback, and
`ORG-FED-SV-011-001` (StegVerse-Labs registry) for the intr-destination boundary.

| Surface | Non-ALLOW outcome | Six fields |
| --- | --- | --- |
| `organization_ledger_transition.py` | refused request: `FAIL_CLOSED` `ORGANIZATION_TRANSITION_REQUEST_REFUSED`, nothing appended | top level |
| `organization_ledger_readback.py` | refused readback, or a read holding a declared legacy segment: top-level `FAIL_CLOSED`, exit 1 (DEFAULT, C6 convergence c); a successful read carries no disposition | top level |
| `organization_ledger_readback.py` | basis not established (`BASIS_ABSENT`, `BASIS_UNKNOWN`, `BASIS_STALE`, `BASIS_CONTRADICTORY`) | `current_state_finding` |
| `intr_destination_ingress.py` | `DENY` / `FAIL_CLOSED` diagnostic | result (receipt bytes unchanged) |
| `organization_egress_boundary.py` | emit `DENY`, closure `DENY`, no answer yet `FAIL_CLOSED` `FAR_SIDE_ANSWER_NOT_MATERIALIZED` | result and new records |
| `organization-ledger-transition.yml` | every `FAIL_CLOSED` line it prints | inline JSON |

An applied request records `disposition: ALLOW` in both receipts' evidence as
evidence that the append occurred. A disposition does not satisfy the Authority
or Time constraint; both stay constraint variables.

## Receivers

An absent far-side answer is not a transition and writes nothing. It is
answered `FAIL_CLOSED` with the six fields, with
`receiver_unavailable_disposition: DURABLE_QUEUE_OR_EVENT_EPHEMERAL_MATERIALIZATION`
and `always_on_receiver_required: false` (also declared in
`org-runtime/interlock-intr.json`). Nothing waits on or polls a receiver, host or
external machine.

## Exemptions

`data/organization-role-exemption-register.json` holds the surfaces that do not
yet meet the standard, each as an actionable `FAIL_CLOSED` record with a review
date. The register confers no authority and cannot satisfy a terminal
predicate. As registered on 2026-10-17:

- N1: the dispatch-driven self-closure in `organization-ledger-transition.yml`.
- N8: ledger transport by the workflow token; no TV/TVC ledger operation exists.
- N11 (the request named no manifested capability and route) is repaired by default B5 below and is no longer registered.

`tests/test_organization_role_conformance.py` checks the six fields on every
non-ALLOW path above and checks the register's shape.

## Defaults pending owner confirmation

Applied on 2026-10-17 so that no lane waits on a decision. Each one is marked
`DEFAULT pending owner confirmation`, is listed with its revert path in
`data/organization-role-exemption-register.json`
(`defaults_pending_owner_confirmation`), and is tested in
`tests/test_organization_ledger_defaults.py`. Reverting one does not touch the
others.

| Item | DEFAULT pending owner confirmation | Where |
| --- | --- | --- |
| B1 (#12 item 2) | `constraint_set` Option B: `constraint_set.node_standing` {`mode` `ESTABLISH_GENESIS` or `VERIFY_EXISTING`, `node_ref`, `predecessor`} plus an optional `constraint_set.declared` {`authority`, `time`: `satisfies` or `does_not_satisfy`}, recorded as declared only. A top-level `standing` is refused (`TRANSITION_REQUEST_STANDING_REPLACED_BY_CONSTRAINT_SET`), as is any field outside the request contract. `VERIFY_EXISTING` is `FAIL_CLOSED` `PREDECESSOR_BINDING_PRIMITIVE_NOT_PRESENT`: the SDK `successor_predecessor_binding` is not in this repository. | `organization_ledger_transition.py::validate_constraint_set` |
| B2 | First `current_state` as a request held in `.stegverse/declarations/`, outside the intake, so it appends nothing. T-181 fallback (`fixtures/transition-elements/t-181.receipt-record.json`); TT has no t-011. `SV011-ORG-STATE-0001`, `sv011.context-to-evidence`, context to evidence, scope `[]`, fresh for 7 days. No receipt is claimed. | `.stegverse/declarations/` |
| B3 (N7) | The readback uses `stegverse.organization-ledger-observation/v1`. `stegverse.organization-ledger-readback/v1` stays StegVerse-org's canonical shape (top-level `disposition: ALLOW`), which the SDK reader consumes; no SDK reader accepts this observation shape under either id, so no reader changes behaviour. | `organization_ledger_readback.py` `OBSERVATION_SCHEMA` |
| B5 (N11) | A required `manifested_route` {`capability_profile_id`, `route_id`, `destination_repository`, `destination_operation`, `destination_ref`} equal to the binding declared once in `org-contract.json`; otherwise `ROUTE_NOT_BOUND_IN_THIS_ORGANIZATION`. It is recorded on both receipts. | `validate_manifested_route`; `org-contract.json` `transition_request_intake.manifested_route_binding` |
| B9 | First-cycle build expectation, delta definition (`VERIFIED_NULL`, `OBSERVED_NO_CHANGE`, `UNOBSERVED`; never empty) and return path. Computing the delta and the SDK leg are six-field `FAIL_CLOSED` inside the artifact: no comparator component is admitted, and no SDK route exists (B6). | `.stegverse/build-expectations/sv011-first-cycle.json` |
| H45 (P1 N5) | Source-receipt retention, forward only. A new organization receipt names `source_transition_sha256` and retains the repository receipt under `source-receipts/`, as StegVerse-org's ledger does. The receipt at tip `2cc58cec` (`sha256:3db9008d…`) is declared the legacy pre-retention segment. It is reported as legacy with a six-field `FAIL_CLOSED` `SOURCE_RECEIPT_RETAINED_AND_VERIFIES` finding (`source_receipt_retention.legacy_segment_finding`) and is never re-minted. A later receipt without a verifying retained source refuses the readback. | `aggregate_repo_transition.py::append`; `organization_ledger_readback.py::source_receipt_retention`; `org-contract.json` `source_receipt_retention` |
| H45 / G4 (P1-FC-9) | The legacy segment declaration follows the shared readback C6 rule (P1, C6 at `ebf4aaf`). It carries `waived_predicate` (`SOURCE_RECEIPT_RETAINED_AND_VERIFIES` only), the H45 `six_fields`, and `declaration_sha256` (`sha256:36c43b78…`). The digest is taken over the segment without that member, in C6's `CANONICAL_JSON` (UTF-8, `sort_keys`, separators `(",", ":")`, `ensure_ascii=False`). The readback checks the members and the digest. It then binds the segment to the chain as a contiguous prefix from genesis ending at the HEAD of `ledger_tip_commit` on the ref read. Refusals use C6's failure codes, in C6's order. A ledger holding the declared segment reads back `FAIL_CLOSED` `DECLARED_LEGACY_SEGMENT_NOT_SOURCE_RETAINED`, never ALLOW. The workflow step "Hold the readback FAIL_CLOSED on a declared legacy segment" fails the run on it. Letting that step pass is Rigel's decision. | `organization_ledger_readback.py::validate_legacy_segment`, `source_receipt_retention`; `org-contract.json`; workflow |
| C6 convergence (a) | A segment is bound to a git ledger commit. A copy of the ledger is read only through a `git+` locator: a clone of the published ref, or `tests/fixtures/organization-ledger-2cc58cec.bundle` fetched into one. A directory copy is refused `LEGACY_SEGMENT_TIP_COMMIT_UNVERIFIABLE`. | `organization_ledger_readback.py::_tip_head`; tests |
| C6 convergence (b) | A declared segment applies to every ledger it is read against; the old "applies only when genesis is `receipts[0]`" rule is removed. A chain holding none of the declared receipts is refused `LEGACY_SEGMENT_NAMES_A_RECEIPT_NOT_IN_THE_CHAIN`. Ledgers built from genesis are read with a contract that declares no segment, passed with `--org-contract`. | `organization_ledger_readback.py::source_receipt_retention`, `main --org-contract`; tests |
| C6 convergence (c) | The readback exits 1 on any non-ALLOW. A refusal, or a read holding a declared legacy segment, carries a top-level `disposition: FAIL_CLOSED` and the six fields. A successful read stays an observation under `stegverse.organization-ledger-observation/v1` with no top-level `disposition` (B3), and exits 0. The workflow's readback steps keep the exit status (`\|\| status=1`, written to `*.readback.status`). The Hold step reads the top-level `disposition` and that status, and fails on either. | `organization_ledger_readback.py::readback`, `_refusal`, `main`; workflow |
| C6 parity (1) | The never-waivable set is C6's (`ebf4aaf` `INTEGRITY_PREDICATES`), exactly: `ORGANIZATION_RECEIPT_VERIFIES_AGAINST_ITS_BODY`, `ORGANIZATION_LEDGER_CHAIN_IS_COMPLETE`, `ORGANIZATION_LEDGER_CHAIN_IS_ACYCLIC`, `ORGANIZATION_LEDGER_HEAD_PRESENT`, `REPOSITORY_CHAIN_VERIFIES`, `REPOSITORY_LEDGER_HEAD_PRESENT`, `RECEIPT_SELF_HASH`, `CHAIN_LINKAGE`, `SELF_HASH` and `LINKAGE`. If a `waived_predicate` (one name or a list) names any of them, the read is refused `LEGACY_SEGMENT_WAIVES_SELF_HASH_OR_LINKAGE`. | `organization_ledger_readback.py` `LEGACY_INTEGRITY_PREDICATES` |
| C6 parity (2) | The organization chain uses C6's predicate names: `ORGANIZATION_LEDGER_HEAD_PRESENT`, `ORGANIZATION_LEDGER_CHAIN_IS_ACYCLIC`, `ORGANIZATION_LEDGER_CHAIN_IS_COMPLETE` and `ORGANIZATION_RECEIPT_VERIFIES_AGAINST_ITS_BODY`. The repository chain keeps its own names. `REPOSITORY_NAMESPACE_MATCHES_THE_CONTRACT` is unchanged; C6 is adopting it. | `organization_ledger_readback.py` `ORGANIZATION_CHAIN_PREDICATES` |
| C6 parity (3) | An empty ledger, with no HEAD, is a refusal: exit 1, top-level `FAIL_CLOSED` and the six fields, `failure_code` `ORGANIZATION_LEDGER_HEAD_ABSENT`, `failed_predicate` `ORGANIZATION_LEDGER_HEAD_PRESENT`, and C6's GENESIS `next_attempt`. The empty-ledger observation (`BASIS_ABSENT` / `ORGANIZATION_LEDGER_EMPTY`) is removed. | `organization_ledger_readback.py::_verify_chain` |
| 4b-a (item 4b, ECOSYSTEM_CAPABILITY_ABSENT (a)) | A read of the published ref with no append. Job `readback-published-ref` runs on `pull_request` and `push` with `contents: read`, fetches `refs/stegverse/organization-ledger` into a fresh bare repository, and runs the readback CLI on it, so a live read cites a run id. It never pushes and runs no ingress, intake or append. Its conclusion says whether a conforming observation was recorded (a disposition-free observation, or `FAIL_CLOSED` with the six fields and no undeclared failures). It is not the ledger's disposition, which is printed and written to the step summary. The transition job's Hold step is unchanged. | workflow job `readback-published-ref` |

Not applied, recorded as six-field `FAIL_CLOSED`:

- B8 (N1), the self-closure manifest class.
  - `failure_code`: `ORGANIZATION_ROLE_CONFORMANCE_NOT_DECLARED`
  - `failed_predicate`: `SELF_CLOSURE_IS_DRIVEN_BY_A_COMMITTED_MANIFEST`
  - `required_evidence_or_repair`: the InTr hop lane agrees to replace the typed dispatch inputs it chains on (StegVerse-Labs/.github#2897; INDEX H30 holds the dispatch and H35 holds merges). Then `.stegverse/self-closure-requests/<packet_id>.json` drives job `transition`.
  - `retry_entrypoint`: `.github/workflows/organization-ledger-transition.yml` job `transition`
  - `owning_existing_goal`: `ORG-FED-SV-011-001`
  - `next_attempt`: the same change that removes the N1 register entry
- B4, B6 and B7 stay six-field handoffs to other lanes and owners.

## Open questions for Rigel

- **#12 item 1 vs C6 convergence (DEFAULT pending owner confirmation).** #12 item 1 made the readback observation-only, with no top-level `disposition`. The shared readback C6 exits 1 on any non-ALLOW and carries a top-level `disposition`. The DEFAULT applied here splits the difference:
  - A successful read stays disposition-free under `stegverse.organization-ledger-observation/v1` (B3) and exits 0.
  - A refusal, or a read holding the declared legacy segment, carries a top-level `disposition: FAIL_CLOSED` with the six fields, because the standard requires them, and exits 1.

  Rigel to confirm, or to choose either the strict #12 item 1 form or C6's form, where a successful read is `disposition: ALLOW`.
- **C6 parity (4), not applied: pending owner decision on #12 item 1.** P1 asks that a successful read be `disposition: ALLOW` under `stegverse.organization-ledger-readback/v1`, as C6 has it. P1 recommends rescinding #12 item 1 for the readback result only, and has raised this with Rigel. Until Rigel decides, B3 stays: a successful read is disposition-free under `stegverse.organization-ledger-observation/v1` and exits 0.
- **Option (ii) for the Hold step.** Whether the Hold step passes on a declared legacy segment when the run's own receipt is in the fully verified suffix (`ingress_receipt_in_fully_verified_suffix`). Not implemented.
- **C6 reuse.** C6 is not admitted under StegVerse-Labs/.github `data/reusable-task-components/`, which holds only `RTC-RESIDENT-RENDEZVOUS-010.json`. It is therefore not vendored, and this readback is kept aligned with it. After C6 parity (1) to (3), the only intended difference is the success shape, which is (4) above.
