# Agent documentation index

Read the first five documents in order. Each has one job.

| Document | Purpose | Update when |
|---|---|---|
| [AGENTS.md](../../AGENTS.md) | Canonical owner rules and working method | A rule or workflow changes |
| [HANDOFF.md](HANDOFF.md) | Current work, evidence, blockers and exact next action | Work finishes or is handed over |
| [PLAN.md](PLAN.md) | Work packages, file scopes, instruments and gates | Evidence changes priorities/scope |
| [TESTING.md](TESTING.md) | Test layers, harness rules and the promotion gate | An oracle, protocol or gate changes |
| [RESULTS.md](RESULTS.md) | Evidence ledger with provenance and limitations | An identifiable run finishes |
| [TRAINING.md](TRAINING.md) | Fine-tune audit, pipeline bugs and the design for the next attempt | A training run finishes or the design changes |
| [Architecture](../ARCHITECTURE.md) | Implemented runtime boundaries and coupling | Responsibilities change |

`CLAUDE.md` imports `AGENTS.md`; it is not another rule source. Current status belongs
in HANDOFF, run artifacts in the ledger, implementation details in code. Do not copy
benchmark totals into each document.

## Reference, not current state

- [Local model tuning](ON_DEVICE_TUNING.md)
- [Platform parity](PLATFORM_PARITY.md)
- [Experimental GUI models](GUI_MODELS.md)
- [Router tools](../../scripts/router_README.md)
- [Arch training history](ARCH_TRAINING_HANDOFF.md)

These may describe older behavior. Check source and the current contract before
using a command. Legacy `live_*` suites are not an acceptance gate: several grade on
replies and their default cases touch private apps.

## Historical records

- [Previous checkpoint, preserved 2026-11-05](HANDOFF_ARCHIVE_2026-11-05.md)
- [October checkpoints](HANDOFF_ARCHIVE_2026-10.md)
- [Earlier architecture/platform handoff](CLAUDE_HANDOFF.md)

Historical results are unrevalidated under the current protocol. Missing `.data`
artifacts prove neither that a job ran nor that it passed. Archived commands and
next steps are not active instructions.
