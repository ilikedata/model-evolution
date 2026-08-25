---
status: planned
references: []
design:
  dataset_id: <dataset-id>
  config: <tracked-config-path>
  inherited_modules: []
  # For a training experiment, uncomment and complete:
  # experiment_mode: proof
  # preflight:
  #   real_data_audited: true
  #   label_distribution: {<label>: <observed-count>}
  #   trivial_baseline: {<metric>: <value>}
  #   tiny_overfit: {records: <count>, passed: true}
  #   focused_verification: <exact-command>
---

# <Experiment title>

## Claim

<One specific claim.>

## Basis

<Existing evidence that makes the claim plausible.>

## Expected evidence

<One measurable success gate with its baseline and target magnitude.>

## Falsification

<One observable result that rejects the claim.>

## Method

<The smallest controlled comparison that distinguishes success, rejection, and
inconclusive evidence.>
