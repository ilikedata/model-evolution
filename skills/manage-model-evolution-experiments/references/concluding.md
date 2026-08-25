# Conclude an Experiment

## 1. Load the planned contract and evidence

Run:

```bash
model-evolution --json experiment show <experiment-id>
```

Read the original claim, success gate, falsification gate, method, associated
run attempts, and referenced durable artifacts. Apply project-specific artifact
inspection instructions when present.

Stop without concluding when no run evidence exists, an attempt is still
running, required artifacts are missing, or the evidence cannot be tied to the
planned configuration and source revision. Never execute a command to fill the
gap during conclusion mode.

## 2. Judge against the original gates

Compare observed evidence directly with the planned gates. Do not move the
target after seeing results, substitute an easier metric, or promote process
completion into scientific success.

Choose one outcome:

- `supported` when the predefined success evidence is present;
- `rejected` when the predefined falsification evidence is present; or
- `inconclusive` when execution completed but the evidence cannot decide the
  claim.

A failed process is only a rejected claim when the failure itself tests the
claim. Otherwise record it as inconclusive and explain the execution failure.

When an overall result combines distinct capabilities, record typed component
outcomes as `supported`, `rejected`, `inconclusive`, or `not_diagnostic`.
Identify reusable components from evidence; never label them merely
"promising."

## 3. Author one conclusion

Edit the canonical experiment definition at
`model-evolution/studies/<experiment-id>.md`:

- set `status: concluded`;
- add `conclusion.outcome`, `conclusion.confidence`, and the internal run IDs in
  `conclusion.evidence`;
- add concise `Observations`, `Conclusion`, and `Next action` sections; and
- preserve the original claim, gates, method, and design.

Reference durable records and artifacts rather than terminal output. Do not
rewrite prior runs, modules, assessments, or historical definitions.

Validate and commit the conclusion once:

```bash
model-evolution experiment conclude <experiment-id>
```

Report the outcome, confidence, decisive evidence, reusable or rejected
components, and the next action. Do not begin a follow-up experiment in the
same invocation.
