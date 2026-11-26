# acq-onboarding

Salesforce DevOps portfolio project: a gated release pipeline plus a read-only onboarding agent for moving an acquired company's accounts, contacts and cases into a Customer Success org.

Full plan: `../05-salesforce/14-acq-onboarding-roadmap.md`. Schedule: `../05-salesforce/15-technical-round-60min-5-day-plan.md`.

## Status (Day 1, Nov 24)
- [x] SFDX project skeleton, scratch org definition
- [x] `External_Id__c`, `Source_System__c` (Account, Contact, Case), `Source_Priority__c` (Case)
- [x] Permission set `Acquisition_Onboarding` (field access only)
- [x] `CasePriorityNormalizer`, `CaseSourceTrigger`, tests
- [x] Deployed to a scratch org; 6/6 tests pass; org-wide coverage 100% (Nov 24)
- [ ] Second deploy reports no changes (idempotency not yet checked)

## Day 2
- [x] `scripts/compliance_gate.py` (tested locally against good and bad inputs)
- [x] `scripts/check_coverage.py`
- [x] `.github/workflows/pr.yml`, `deploy.yml` (written, never run; `sf` flags unverified against current docs)
- [ ] JWT certificate, connected app in both orgs, GitHub secrets
- [ ] Repo on GitHub, branch protection on `main`
- [ ] Clean PR goes green and deploys; `ModifyAllData` PR goes red

Only claim what is checked off.
