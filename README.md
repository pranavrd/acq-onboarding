# acq-onboarding

Salesforce DevOps portfolio project: a gated release pipeline plus a read-only onboarding agent for moving an acquired company's accounts, contacts and cases into a Customer Success org.

Full plan: `../05-salesforce/14-acq-onboarding-roadmap.md`. Schedule: `../05-salesforce/15-technical-round-60min-5-day-plan.md`.

## Status (Day 1, Nov 24)
- [x] SFDX project skeleton, scratch org definition
- [x] `External_Id__c`, `Source_System__c` (Account, Contact, Case), `Source_Priority__c` (Case)
- [x] Permission set `Acquisition_Onboarding` (field access only)
- [x] `CasePriorityNormalizer`, `CaseSourceTrigger`, tests
- [x] Deployed to a scratch org; 6/6 tests pass; org-wide coverage 100% (Nov 24)
- [x] Second deploy reports "No changes to deploy" (idempotent, Nov 25)

## Day 2 (Nov 25)
- [x] `scripts/compliance_gate.py` (tested locally and in CI)
- [x] `scripts/check_coverage.py` (tested in CI)
- [x] `.github/workflows/pr.yml`: JWT login, scratch org, deploy, Apex tests, coverage gate, Code Analyzer all green in CI
- [x] `.github/workflows/deploy.yml`: JWT login to prod, check-only validate, quick deploy green in CI
- [x] JWT via External Client Apps in both orgs, GitHub secrets set
- [x] Repo on GitHub; ruleset on `main` requires a PR and the `validate` check
- [x] Clean PR went green and merged
- [x] `ModifyAllData` PR failed at the compliance gate, merge blocked, then closed

## Gotchas hit (good interview material)
- `sf` CLI crashed on Node 20.20; fixed by using Node 22 in CI.
- Dev Hub authenticated through an External Client App can't be replicated by scratch org signup; CI sets `SF_SCRATCH_SIGNUP_CONNECTED_APP=PlatformCLI` and a callback URL for creation only.
- Classic branch protection API returns 404 for rulesets; use `/rules/branches/main`.

Only claim what is checked off.
