# Security Policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately through GitHub's security
advisory interface for `ilikedata/model-evolution`. Do not open a public issue
until a fix or disclosure plan is available.

Include the affected version, reproduction steps, impact, and any suggested
mitigation. Maintainers will acknowledge a report as soon as practical and
coordinate remediation and disclosure with the reporter.

## Scope

Security-sensitive areas include secret redaction, artifact integrity,
filesystem extraction, Git operations, adapter loading, and cloud storage.
Project adapters execute trusted installed Python code and are not a sandbox.
