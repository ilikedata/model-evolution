# Changelog

All notable user-facing changes are documented here.

## [Unreleased]

### Added

- Preferred `experiment plan`, `experiment run`, `experiment conclude`, and
  `experiment show` lifecycle commands using the project-configured adapter.
- Top-level `plan_experiment`, `execute_experiment`, `conclude_experiment`, and
  `load_experiment` Python functions over existing schema-v2 records.
- Top-level adapter SDK helpers for records, Git guards, timestamps, downloads,
  and uploads.
- Repository-owned `manage-model-evolution-experiments` Codex skill for
  planning and concluding tracked experiments without executing them.

## [0.1.0] - 2026-08-25

### Added

- Git-backed Markdown records for studies, datasets, runs, modules, and assessments.
- Immutable local and Google Cloud Storage artifact management.
- Schema-v2 validation, migration, lineage, status, and evidence workflows.
- Installable project adapters through the `model_evolution.adapters` entry-point group.
- `model-evolution` command-line interface and supported Python library exports.
