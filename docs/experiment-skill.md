# Model Evolution experiment skill

The `manage-model-evolution-experiments` Codex skill provides a repeatable
workflow for planning and concluding research tracked by Model Evolution. It
uses one user-facing experiment identity while leaving schema-v2 study, run,
and module records as implementation and compatibility details.

The skill has two separate modes:

- plan one focused experiment and hand execution to the repository owner; or
- conclude an existing experiment from durable evidence already produced.

It never executes an experiment, training, evaluation, dataset generation,
large inference, or another command whose purpose is to produce research
evidence.

## Prerequisites

Before using the skill, the research project must:

1. be a Git repository with Model Evolution installed;
2. contain `.model-evolution/project.yaml`;
3. select its adapter in that project configuration; and
4. have the relevant datasets, configurations, and prior records available.

The skill is maintained in this repository but is not installed by the
`model-evolution` Python package.

## Install for all projects

Clone this repository to a stable local path, then symlink the skill into the
user-scoped Codex skill directory:

```bash
mkdir -p ~/.agents/skills
ln -s /absolute/path/to/model-evolution/skills/manage-model-evolution-experiments \
  ~/.agents/skills/manage-model-evolution-experiments
```

Codex supports symlinked skill directories. A user-scoped skill is available
in every repository. If it does not appear immediately, restart Codex.

To make the skill available only in one consuming repository, place or symlink
the directory at:

```text
<consuming-repository>/.agents/skills/manage-model-evolution-experiments
```

Keep a single installed copy with this name. Codex does not merge skills that
declare the same name at multiple scopes.

To update a symlinked installation, pull changes in the Model Evolution clone;
no Python package reinstall is required. See the official
[Codex skill documentation](https://learn.chatgpt.com/docs/build-skills) for
skill discovery locations and invocation behavior.

## Plan an experiment

Invoke the skill explicitly from the initialized research project:

```text
Use $manage-model-evolution-experiments to plan an experiment testing whether
<specific change> improves <specific metric>. Do not execute it.
```

The planning workflow:

1. reads project instructions, including `AGENTS.md` when present;
2. inspects Model Evolution status, relevant prior experiments, code, data,
   configuration, and focused tests;
3. defines one claim, one measurable success gate, one falsification gate,
   and the smallest controlled comparison;
4. prepares only the minimum code and focused verification needed to make the
   experiment runnable;
5. records the canonical definition with
   `model-evolution experiment plan`; and
6. stops and gives the owner the exact command:

```bash
model-evolution experiment run <experiment-id>
```

The skill must not run that command.

### Training preflight

For a new training experiment, planning requires existing evidence for the
real-data audit, observed target distribution, trivial baseline, and a passing
tiny-real-data overfit check using the exact objective. Synthetic-only or
shape-only checks are insufficient.

If required preflight evidence is absent or internally inconsistent, the skill
stops and reports the gap. It does not run training or manufacture evidence to
make the plan pass.

## Execute as the repository owner

Execution is deliberately outside the skill. Review the definition and run
the handed-off command yourself:

```bash
model-evolution experiment run <experiment-id>
```

The command uses the adapter selected by `.model-evolution/project.yaml`; no
user-facing `--adapter` argument is required. Wait for the run and its durable
artifacts to complete before asking the skill to conclude the experiment.

## Conclude an experiment

Use a separate invocation after execution:

```text
Use $manage-model-evolution-experiments to conclude experiment
<experiment-id>.
```

The conclusion workflow:

1. loads the original definition and run attempts with
   `model-evolution --json experiment show <experiment-id>`;
2. verifies that completed evidence is tied to the planned configuration and
   source revision;
3. judges the result against the original success and falsification gates;
4. records `supported`, `rejected`, or `inconclusive` without moving the
   thresholds after seeing the result; and
5. commits the conclusion with
   `model-evolution experiment conclude <experiment-id>`.

It stops without concluding if a run is still active, required evidence is
missing, or provenance cannot be verified. It does not generate missing
evidence or begin a follow-up experiment in the same invocation.

## Project instructions take precedence

The skill is general across Model Evolution projects. A consuming project's
`AGENTS.md` remains authoritative for its architecture, terminology, focused
test commands, safety limits, and resource constraints. Project instructions
may narrow the workflow further, but should not ask this skill to cross its
no-execution boundary.

## Record compatibility

The skill authors the existing canonical records; it does not introduce a new
experiment schema:

| User-facing phase | Existing record behavior |
| --- | --- |
| Plan | Authors and commits one schema-v2 study definition. |
| Execute | Owner command creates internal run attempts through the configured adapter. |
| Conclude | Updates the same study with its evidence-bound conclusion. |
| Show | Loads the study and its associated internal runs as one experiment view. |

Historical studies, runs, modules, assessments, schemas, and Git records are
not migrated, renamed, or rewritten.

## Troubleshooting

- **The skill is not listed:** confirm that the installed directory contains
  `SKILL.md`, is under `~/.agents/skills` or the repository's
  `.agents/skills`, and restart Codex.
- **The project is not initialized:** run `model-evolution init` and commit the
  project configuration before planning research.
- **Planning stops at preflight:** produce the missing evidence outside the
  skill, then invoke planning again.
- **Conclusion stops without writing:** finish the run or restore the required
  durable artifacts and provenance; do not substitute terminal output.
- **The adapter cannot be loaded:** install the consuming project's adapter
  package and ensure its entry-point name matches the adapter selected in
  `.model-evolution/project.yaml`.
