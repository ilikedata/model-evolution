# ADR-001: Keep Project Adapters Outside the Core Package

## Status

Accepted

## Date

2026-08-25

## Context

Model Evolution began inside Latent Arborist. Its record, lineage, storage, and
evidence machinery is reusable, but dataset generation, training execution,
and checkpoint inspection import Latent Arborist, PyTorch, and TensorBoard.
Shipping those imports in the core distribution would make every consumer pay
for one project's runtime and would reverse the desired dependency direction.

## Decision

The core package defines a small `ProjectAdapter` protocol and discovers
implementations from the `model_evolution.adapters` Python entry-point group.
Each consuming project owns its adapter and declares Model Evolution as a
dependency. Registration names must equal the adapter's `name` attribute.

## Alternatives Considered

### Bundle first-party adapters in the core distribution

Rejected because the core would inherit project-specific dependencies and
release coupling.

### Configure shell commands instead of Python adapters

Rejected for the initial release because subprocess result contracts and error
semantics are less direct than the working Python service boundary.

### Exclude execution from Model Evolution

Rejected because lifecycle-controlled execution and complete failed/interrupted
records are part of the tool's central value.

## Consequences

- The base package remains model-framework independent.
- Adapter packages must track the public protocol and register entry points.
- Integration behavior is tested in consuming projects as well as through core
  adapter-contract tests.
- Installing a consuming project makes its adapter available to the shared CLI.
