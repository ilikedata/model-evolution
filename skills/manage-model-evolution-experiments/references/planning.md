# Plan an Experiment

## 1. Discover the existing evidence

1. Run `model-evolution --json status` and inspect relevant prior experiments
   with `model-evolution --json experiment show <experiment-id>`.
2. Inspect the code, datasets, configurations, tests, and prior artifacts that
   bear directly on the question.
3. Discover the repository's focused-test and affected-subsystem commands.
4. State assumptions and resolve contradictions before writing the definition.

## 2. Define one trustworthy question

Write exactly one primary research question. Record:

- a specific claim;
- why existing evidence makes it plausible;
- one measurable success gate, including baseline and target magnitude;
- one falsification gate that can reject the claim; and
- the smallest controlled comparison that distinguishes the outcomes.

Avoid subjective gates such as "looks better" or "promising." Use observable
metrics, bounded qualitative rubrics, or explicit pass/fail conditions.

For training experiments, audit before preparing meaningful compute:

- real sample construction and leakage risks;
- observed label or target balance;
- trivial and non-learning baselines; and
- a tiny-real-data overfit check using the exact objective.

Shape-only or synthetic-only checks do not prove that a learning experiment is
ready. If required preflight evidence does not already exist, stop and tell the
owner exactly what evidence is missing; do not generate it in this invocation.
Record training experiments as `proof` or `scaled` only with complete,
internally consistent preflight evidence.

## 3. Prepare the smallest runnable path

Copy [../assets/experiment-definition.md](../assets/experiment-definition.md)
to `model-evolution/studies/<experiment-id>.md` and replace every placeholder.
Record the tracked dataset, configuration, baseline, and inherited modules that
the experiment actually uses.

When implementation is necessary:

1. Reuse existing data, training, evaluation, and command infrastructure.
2. Write the focused behavior test first, implement the minimum path, then run
   that test and one affected-subsystem smoke check.
3. Do not add generalized frameworks, unrelated refactors, speculative
   production hardening, or abstractions for hypothetical follow-up work.
4. Stop and report the reason before expanding beyond roughly ten minutes of
   implementation, 200 new production lines, or three production files.
5. Do not run the complete suite unless project instructions or the owner
   require it, or immediately before an authorized push or release.

Documentation-only changes do not require model tests unless they alter a
parsed or validated artifact.

## 4. Record and hand off

Validate and commit the completed definition once:

```bash
model-evolution experiment plan model-evolution/studies/<experiment-id>.md
```

Report the experiment ID, question, gates, focused verification performed, and
any unresolved risk. Then give the owner this exact execution handoff:

```bash
model-evolution experiment run <experiment-id>
```

Do not run the handoff command. Do not create interim research records for the
implementation steps.
