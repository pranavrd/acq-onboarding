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

## Day 3 (Nov 26)
- [x] `agent/data/generate.py`: deterministic synthetic exports (500 accounts, 1,000 contacts, 800 cases) with seeded duplicates, bad rows, an unmappable column and messy tiers; ground truth kept out of the agent's reach in `agent/data/truth/`
- [x] `agent/audit.py`: append-only SQLite audit log; UPDATE and DELETE blocked by triggers
- [x] `agent/tools.py`: read-only CSV tools, fuzzy matcher (24/25 seeded duplicates, 0 false positives), plan validation, "needs human" computed in code
- [x] `agent/mcp_bridge.py`: pinned `@salesforce/mcp@0.30.15`, one named org, `data,orgs` toolsets, allowlist of one tool (`run_soql_query`); org, directory and Tooling API arguments are set in code and hidden from the model
- [x] `agent/plan.py`: Messages API loop; every call allowlisted, SELECT-only SOQL, audited; writes `plans/<run_id>/plan.json` + `plan.md`
- [x] Offline tests (42) pass, no API key or network needed; run in CI
- [x] MCP bridge verified live against `dev-scratch`: read-only SOQL works, `DELETE` is denied and audited, 50 Core accounts present
- [x] Live run against `dev-scratch` (Nov 26): Sonnet 5.5, 13 tool calls, 68.5k/7.6k tokens (about $0.21). Dedupe 24/24 correct, 0 wrong, missed 1 seeded duplicate. First version named only 2 of 69 seeded bad rows (it could only sample), and guessed an `On Hold` case status the org does not have
- [x] Fixes from that run: `profile_quality` tool (exact counts and ids, verified against ground truth), picklist-value query hint, structured `count`/`row_ids` on data-quality entries
- [x] Re-run after the fixes (run `0e37ac3f75ca`, about $0.22): mappings 23/23, dedupe precision 100% / recall 96%, data-quality counts 8/8 exact
- [x] Golden-mapping eval (`agent/eval_plan.py`, `agent/evals/golden_mapping.json`): scores a plan against all 23 export columns and the seeded ground truth. Gates: mapping accuracy >= 90%, zero *confident* wrong mappings, dedupe precision >= 95% and recall >= 90%, every data-quality kind exact. Two real plans are kept as fixtures (a baseline that passes, the first weak run that fails); 12 tests mutate them to prove each gate trips
- [ ] Eval run automatically in CI against a live agent run (needs model credentials and a loaded org per PR, about $0.22 and non-deterministic; today the gate logic is tested in CI and the live eval is run by hand: `agent/.venv/bin/python agent/plan.py --org dev-scratch` then `python3 agent/eval_plan.py plans/<run_id>/plan.json`)
- [x] Plan opened as a PR by the agent (`agent/open_pr.py`, `python agent/plan.py --org dev-scratch --open-pr`). Tested against real git (bare remote) and run live once (PR #5, from plan `0e37ac3f75ca`): exactly 2 files, author `planning-agent`, eval report in the body, merge blocked until `validate` passes, checkout untouched, no leftover branch or worktree. Limits, each covered by a test: adds exactly `plans/<run_id>/plan.json` and `plan.md` on a new `plan/<run_id>` branch, built in a throwaway `git worktree` so the developer's checkout is never touched; never force-pushes, never overwrites an existing remote branch, never merges (merging the PR is the human approval). A plan that fails the eval is not proposed unless `--open-pr-anyway`.
- [ ] Plan PR also carries any new-field metadata the plan proposes (roadmap cut-line item; deliberately skipped, the proposed fields are listed in the PR body instead)
- [ ] Safer GitHub credential for the agent: it currently uses whatever `gh` is logged in as. The code limits what it does, the credential does not. A fine-grained token limited to contents and pull requests on this repo would enforce it

Run: `python3 -m venv agent/.venv && agent/.venv/bin/pip install -r agent/requirements.txt`, log in once with `ant auth login` (or put `ANTHROPIC_API_KEY` in `.env`; the SDK uses either), then
`agent/.venv/bin/python agent/plan.py --org dev-scratch`.

## Known tradeoffs
- `@salesforce/mcp` reached end of life on 2026-11-02. It is pinned and still installs. Production would use Salesforce's hosted MCP servers with per-user OAuth.
- The DX MCP server has no read-only mode, so read-only comes from server flags plus the allowlist in `mcp_bridge.py` and `plan.py`.

## Gotchas hit (good interview material)
- `sf` CLI crashed on Node 20.20; fixed by using Node 22 in CI.
- Dev Hub authenticated through an External Client App can't be replicated by scratch org signup; CI sets `SF_SCRATCH_SIGNUP_CONNECTED_APP=PlatformCLI` and a callback URL for creation only.
- Classic branch protection API returns 404 for rulesets; use `/rules/branches/main`.
- Metadata-deployed custom fields have no field-level security for anyone until a permission set grants it. Describe, SOQL and Bulk upsert hide them (error: "does not match an External ID"), while Apex tests still pass because Apex runs in system mode. After creating a scratch org run `sf org assign permset --name Acquisition_Onboarding --target-org dev-scratch`.
- Bulk API wants LF line endings; Python's `csv` writes CRLF unless `lineterminator="\n"`.

Only claim what is checked off.
