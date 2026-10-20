# InTr mesh path ingress (destination half)

Installs the destination side of the InTr mesh path transfer defined in
StegVerse-org/.github `docs/INTR_PATH_TRANSFER_V1.md` (goal StegVerse-org/.github#162). The module and workflow are
byte-identical to StegVerse-org/.github `resident-runtime/intr_path_ingress.py` and
`.github/workflows/intr-path-ingress.yml`.

- **Transition.** A row of `.stegverse/intr-path-transfer/inbound.json` (`stegverse.intr-path-inbound/v1`)
  landing on main. It carries the path_id, the communication_id and copies of the source organization's sealed
  `INTR_MESH_PATH_TRANSFER` organization and repository receipts.
- **Writer.** `.github/workflows/intr-path-ingress.yml` runs after this push's main-merge transfer is ledgered
  (workflow_run, no dispatch). `resident-runtime/intr_path_ingress.py consume` checks the carried pair, then
  appends through `resident-runtime/intr_destination_ingress.py` `_append` an `ORGANIZATION_INTR_DESTINATION_INGRESS`
  receipt (`stegverse.intr-path-ingress-evidence/v1`) carrying both source seals. Then it publishes the ref by
  fast-forward and verifies from a fresh fetch.
- **Refusals** (six fields each):
  - FAIL_CLOSED `INTR_PATH_SOURCE_RECEIPT_NOT_CARRIED`
  - DENY `INTR_PATH_CARRIED_RECEIPT_CONTRADICTED`
  - FAIL_CLOSED `INTR_PATH_DESTINATION_RECEIPT_NOT_LEDGERED`
  - DENY `INTR_PATH_DESTINATION_RECEIPT_LINK_UNVERIFIED`
- **Cross-organization read.** The carried pair's inclusion in the source ledger cannot be read with
  `GITHUB_TOKEN`, which is repository-scoped, and the repositories are internal. It is recorded FAIL_CLOSED
  `INTR_PATH_CROSS_ORG_READ_CREDENTIAL_NOT_ISSUED` (class B, retry: StegVerse-Labs/TVC least-privilege issuance).
  The end-to-end ALLOW (`end-to-end --source-readback --destination-readback`) needs both ledgers read back.

This change has no inbound rows. A row carrying real source receipts can be added only after the
StegVerse-org/.github path-transfer writer has appended them (StegVerse-org/.github#209).
