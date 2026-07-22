<!--
Title: Conventional Commits, under 70 chars. e.g. feat(store): add ref generation
-->

Closes #

## Summary

<!-- What changed and why. Name the tradeoff if there was one. -->

## Test plan

<!--
Required for anything touching runtime behaviour. Not "CI passes" — numbered,
copy-pasteable steps with concrete expected outcomes. Tick each box and paste
the actual output once it passes; `gh pr edit` the body rather than leaving it
aspirational.

Pure-docs or pure-CI changes: a one-line "no runtime change; verification is CI
alone" is fine, but say it explicitly.
-->

- [ ] **1.**
  ```bash
  ```
  Expected:

- [ ] **2. CI green**

**Pass criteria:**

## PII / privacy check

<!--
This service is the system of record for customer data. Delete this section only
if the change genuinely cannot touch it.
-->

- [ ] No customer fields added to anything the benchhud API projection returns
- [ ] No PII in logs, error messages, or fixtures
- [ ] No real customer data in tests
