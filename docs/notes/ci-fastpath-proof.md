# CI fast-path proof (docs-only PR)

This file exists to prove one thing: a diff that touches only documentation must
report every required context green without re-running the 3,077-test suite.

Classes are defined in `standards/fleet/CI-RUNNER.md` §8:
`**/*.md`, `docs/**`, `data/competitors/**` are documentation; `.forgejo/**` and
everything else still run the full pipeline.

Expected: `CI / changes` prints `code=false`, `CI / test` reports success in
seconds with the decision in its log, and `CI / security`, `CI / lint`,
`CI / structure-gate` still run.
