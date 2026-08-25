---
name: manage-model-evolution-experiments
description: Plan or conclude research experiments tracked by Model Evolution. Use when a user asks to define, prepare, or plan a focused experiment, or to interpret evidence and conclude an existing experiment in a project configured with `.model-evolution/project.yaml`. Keep planning and conclusion as separate invocations. Never execute the experiment, training, evaluation, corpus generation, or other expensive research command.
---

# Manage Model Evolution Experiments

Use one stable experiment identity from research question through conclusion while
leaving schema-v2 study, run, and module records as implementation details.

## Select one mode

- For a new research question or requests to define, prepare, or plan an
  experiment, read and follow [references/planning.md](references/planning.md).
- For requests to interpret results or conclude an existing experiment, read
  and follow [references/concluding.md](references/concluding.md).
- If the requested mode is unclear, ask whether the user wants to plan a new
  experiment or conclude an existing one. Do not perform both in one invocation.

## Establish project context

Before either mode:

1. Find and read the project instructions, including `AGENTS.md` when present.
2. Find `.model-evolution/project.yaml`. Stop if the project is not initialized.
3. Treat project-specific architecture, terminology, safety, and resource rules
   as authoritative over this general workflow.
4. Use the supported `model-evolution experiment` CLI. Do not direct users to
   internal study, run, or module commands.

## Preserve the execution boundary

Never run:

- `model-evolution experiment run`;
- training or evaluation jobs;
- corpus or dataset generation;
- large inference, rendering, or benchmark commands; or
- a project command whose purpose is to produce experiment evidence.

The planning workflow may implement the smallest code and focused tests needed
to make an experiment runnable. It must stop after recording the plan and hand
the exact execution command to the repository owner. The conclusion workflow
must stop when evidence is missing rather than generating it.

## Keep records meaningful

Create one experiment definition before execution and one conclusion after the
owner has produced evidence. Do not turn mechanical implementation steps into
research records. Do not migrate, rename, or rewrite historical records.
