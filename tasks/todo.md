# Public Experiment Lifecycle Tasks

## Task 1: Document the contract

**Acceptance criteria:**

- [ ] The four CLI commands and four Python functions are documented.
- [ ] The experiment-to-study/run mapping and compatibility guarantees are explicit.

**Verification:** Review rendered Markdown and run the documentation examples through CLI tests.

## Task 2: Specify behavior with failing tests

**Acceptance criteria:**

- [ ] Public exports include all four experiment functions.
- [ ] Tests cover plan, run, conclude, load/show, configured adapter selection, and legacy CLI compatibility.

**Verification:** Focused tests fail because the new interface does not yet exist.

## Task 3: Implement the lifecycle

**Acceptance criteria:**

- [ ] New functions compose existing records without schema changes.
- [ ] New CLI commands call the shared public functions without `--adapter`.
- [ ] Existing commands and tests still pass.

**Verification:** Focused tests and `make check` pass.

## Task 4: Verify the package artifact

**Acceptance criteria:**

- [ ] Wheel and source distribution build successfully.
- [ ] The wheel installs into an isolated environment.
- [ ] Installed Python imports and CLI command parsing work.

**Verification:** `make build` plus isolated installed-artifact smoke tests pass.

## Task 5: Review, commit, and push

**Acceptance criteria:**

- [ ] Five-axis review has no unresolved required findings.
- [ ] Focused commits contain no unrelated changes or secrets.
- [ ] Commits are pushed to the existing private repository without tags or releases.

**Verification:** Clean worktree and remote branch contains the new commits.
