# Fictional Payment Service Release Demo

This repository is synthetic training data for release-readiness demonstrations.
It describes a fictional payment service only. It contains no real payment
records, customers, credentials, incidents, or proprietary material.

The release workflow deliberately has two checks on a release branch:

- `blocking-suite` runs a small, idempotent local migration test and passes.
- `advisory-synthetic` deliberately fails so a release reviewer can demonstrate
  a documented risk decision changing `NEEDS_DECISION` to `READY`.

The migration stores only the constant marker `fictional-payment-schema-v1` in
an on-disk SQLite database selected by an explicit command-line argument.
