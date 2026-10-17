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
| `organization_ledger_readback.py` | refused readback (no disposition; observation only) | top level |
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

Not applied, recorded as six-field `FAIL_CLOSED`:

- B8 (N1), the self-closure manifest class.
  - `failure_code`: `ORGANIZATION_ROLE_CONFORMANCE_NOT_DECLARED`
  - `failed_predicate`: `SELF_CLOSURE_IS_DRIVEN_BY_A_COMMITTED_MANIFEST`
  - `required_evidence_or_repair`: the InTr hop lane agrees to replace the typed dispatch inputs it chains on (StegVerse-Labs/.github#2897; INDEX H30 holds the dispatch and H35 holds merges). Then `.stegverse/self-closure-requests/<packet_id>.json` drives job `transition`.
  - `retry_entrypoint`: `.github/workflows/organization-ledger-transition.yml` job `transition`
  - `owning_existing_goal`: `ORG-FED-SV-011-001`
  - `next_attempt`: the same change that removes the N1 register entry
- B4, B6 and B7 stay six-field handoffs to other lanes and owners.
