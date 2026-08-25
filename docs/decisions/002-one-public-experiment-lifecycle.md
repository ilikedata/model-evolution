# ADR-002: Present One Public Experiment Lifecycle

## Status

Accepted

## Date

2026-08-25

## Context

Model Evolution's schema-v2 implementation separates research intent into a
study, execution attempts into runs, and reusable outputs into modules. That
separation is useful for validation and lineage, but it requires users to move
between several identifiers and commands for one experiment.

Existing projects, including Latent Arborist, already have canonical schema-v2
records. Their paths, IDs, fields, history, and adapter implementations are
compatibility surfaces and must not be migrated or rewritten.

## Decision

Expose one additive public experiment lifecycle. A public experiment ID is the
existing study ID. Its Markdown study record is called the experiment
definition, and its associated run and module records remain internal
implementation and compatibility details.

Planning validates and commits the definition. Running creates one internal
run attempt from the definition and executes it through the adapter selected by
`.model-evolution/project.yaml`. Concluding validates and commits the same
definition after the user records its conclusion. Loading returns a read-time
experiment view containing the definition and associated runs; it does not
persist a new record kind.

The existing `study`, `run`, and module-related Python and CLI surfaces remain
supported. In particular, the legacy `run plan --adapter` command remains
unchanged even though normal experiment commands do not expose `--adapter`.

## Alternatives Considered

### Persist a new experiment record

Rejected because it would introduce another canonical schema, duplicate the
study identity, and require migration or synchronization of existing records.

### Use the generated run ID as the experiment ID

Rejected because retries can create multiple runs for one research question.
The study ID is stable across all attempts and the final conclusion.

### Remove or rename the study and run interfaces

Rejected because they are documented compatibility surfaces and existing
projects depend on their records and commands.

## Consequences

- Users can plan, run, conclude, and inspect work using one stable ID.
- Existing schema-v2 records remain authoritative and unchanged.
- One experiment can expose multiple internal run attempts in its loaded view.
- Adapter choice is project configuration, not a routine lifecycle argument.
- The compatibility interface remains larger than the preferred public
  interface during the `0.x` series.
