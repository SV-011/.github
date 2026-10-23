# SV-011 experiment 1: minimality report (entity package v0)

The package is `package-manifest.v0.json` + three Admissible-Existence files, kept byte-exact
under `components/` and checked by git blob sha1, + the stdlib bootstrap `sv011_entity/`.

| # | Role | Component (repo@commit:path, blob) | Governance property it supplies | What breaks without it |
|---|------|------------------------------------|---------------------------------|------------------------|
| 1 | admissibility_predicates | `Admissible-Existence/GTG@57b72748:formal/predicates.py` (b0e2cb2d) | GTG-A1..A8 executable predicates. A transition is ALLOW only if all eight hold (state + candidate named, viable successor, established standing, scope/horizon/proof path, governed consequence, uncertainty within threshold and no threatened viability, no purpose inversion, non-authority boundary). | Nothing decides ALLOW, so no transition can be admitted. Bootstrap: `SV011_ENTITY_COMPONENT_UNDECLARED` / `_ABSENT` / `_DIGEST_MISMATCH` / `_UNUSABLE` (if `PREDICATES` lacks A1..A8). The entity stops; it never falls back to "allow". |
| 2 | disposition_vocabulary | `Admissible-Existence/GTG@57b72748:schemas/gtg-governance-disposition-v1.schema.json` (4d412fe4) | Closed disposition set (`ALLOW, DENY, DEFER, TRANSFORM, TERMINATE, FAIL_CLOSED`) and the one required `execution_*` flag fixed at `false` that every disposition carries. | No closed set to check outputs against, and no constant non-execution flag. Bootstrap FAIL_CLOSED as above; at run time any disposition outside the set gives `SV011_ENTITY_DISPOSITION_OUTSIDE_VOCABULARY`. |
| 3 | transition_receipt_rule | `Admissible-Existence/AE@fdece170:.stegverse/transition-ledger/contract.json` (927e140e) | Append-only receipts; receipt schema `stegverse.repo-transition-receipt/v1`; required fields incl. `predecessor_state_sha256`, `successor_state_sha256`, `evidence`, `observed_at`, `previous_receipt_sha256`; local replay rule (replay needs no org/ecosystem replay). | No receipt shape or chaining rule, so a transition could not be recorded or replayed. Bootstrap FAIL_CLOSED; the receipt builder also refuses (`SV011_ENTITY_RECEIPT_FIELDS_UNSATISFIED`) if a contract field is unmet. |

Evidence (tests in `tests/test_entity_governance.py`, class `MinimalityEachComponentRequired`):
dropping each component from the manifest, deleting each file, and altering each file by one byte
all give FAIL_CLOSED with six fields and write no receipt. Declaring an extra component the bootstrap
does not use is also FAIL_CLOSED (`SV011_ENTITY_COMPONENT_UNUSED`), so the manifest cannot grow silently.

Considered, not included:
- `Admissible-Existence/TT@ba3cca71:engine/sv011_next_required_capability.py`: a scope-specific derivation bound to TT's own data manifest; it adds no governance property the three above lack.
- `Admissible-Existence/AE@fdece170:.stegverse/transition-ledger/emit.py`: writes to a host ledger root and reads the wall clock. The entity uses the contract only; time is a supplied input.
- AE#100 "56-path kit" (candidate only): not located, see record below.
- KV / cvk files: they belong to experiment 2.

## Not-present records (FAIL_CLOSED, six fields)

1. `SV011_ENTITY_SIX_FIELD_RECORD_AE_COMPONENT_NOT_PRESENT`
   - failed_predicate: a standalone AE component defines the six-field non-ALLOW record.
   - required_evidence_or_repair: an AE file outside org-ledger/InTr paths that defines it. Code search shows it only in `Admissible-Existence/.github` resident-runtime org-ledger/InTr files, which are excluded. Until then the package defines it as `stegverse.sv011.entity-disposition/v0` (`sv011_entity/dispositions.py`).
   - retry_entrypoint: `gh search code required_evidence_or_repair --owner Admissible-Existence`
   - owning_existing_goal: StegVerse-org/.github#167; SV-011/.github#68
   - next_attempt: swap in the AE component as a fourth manifest entry once it exists.
2. `SV011_ENTITY_AE100_KIT_NOT_LOCATED`
   - failed_predicate: the AE#100 56-path kit can be read at a pinned sha.
   - required_evidence_or_repair: its repo@sha. `Admissible-Existence/AE` has no #100. `Admissible-Existence/.github#100` is the B-P31 epoch carry (27 files, org-ledger code), not a kit.
   - retry_entrypoint: `gh pr view 100 -R Admissible-Existence/<repo> --json files`
   - owning_existing_goal: StegVerse-org/.github#167; SV-011/.github#68
   - next_attempt: compare its paths against the three roles. Add a path only if it supplies a property the entity lacks.
3. `SV011_ENTITY_PROPOSAL_GENERATOR_NOT_PRESENT`
   - failed_predicate: an admitted AE component generates assistant proposals (model/inference).
   - required_evidence_or_repair: such a component at repo@sha. None was found. In v0 the entity "proposes" by governed evaluation of a declared action request. A future assistant model only authors requests and gets no extra path to a transition.
   - retry_entrypoint: `python3 -m sv011_entity run --package . --record <dir> --request <req.json>`
   - owning_existing_goal: StegVerse-org/.github#167; SV-011/.github#68
   - next_attempt: experiment 2 / assistant package, which plugs in as a request author.

Non-claims: the GTG predicates are reference semantics over the evidence a request declares. The entity does not verify external reality, has no model, takes no network or credential action, and has no InTr or org-ledger dependency (those are optional hook points, not prerequisites).
