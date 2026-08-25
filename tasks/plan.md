# Implementation Plan: Public Experiment Lifecycle

## Overview

Add one supported experiment lifecycle over the existing schema-v2 study and
run records. The change is additive: existing commands, Python APIs, records,
schemas, and adapter entry points remain unchanged.

## Architecture Decisions

- A public experiment ID is the existing study ID. This preserves every
  canonical record and gives users one stable identifier across the lifecycle.
- Experiment definitions remain schema-v2 Markdown study records. Associated
  runs and modules are implementation and compatibility details.
- Execution resolves the adapter from `.model-evolution/project.yaml`; the new
  interface does not accept an adapter argument.
- Public functions return an experiment view containing the definition and all
  associated run attempts without persisting a new record kind.

## Task List

1. Document the supported CLI, Python signatures, return shape, and compatibility guarantees.
2. Add failing contract tests for public exports, lifecycle behavior, and CLI parsing/dispatch.
3. Implement the experiment view and lifecycle wrappers, then wire the CLI commands.
4. Run focused and full checks, build distributions, install the wheel in isolation, and smoke test it.
5. Review the diff across correctness, simplicity, architecture, security, and performance; commit and push.

## Risks and Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Experiment ID conflicts with generated run IDs | High | Define the experiment ID as the existing stable study ID. |
| New interface changes persisted data | High | Compose existing study/run records at read time; add no schema or migration. |
| Adapter selection drifts between CLI and Python | Medium | Resolve only from `ProjectConfig.adapter` in the shared Python function. |
| Installed artifact omits the new module | Medium | Build a wheel, install it into a clean virtual environment, and smoke test imports and CLI help. |

## Open Questions

None. The requested additive compatibility boundary determines the design.
