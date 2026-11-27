# Load decisions (Nov 27, 2026)

Answers to the four open questions from the first approved-plan review (plan `0e37ac3f75ca`, PR #5). The load step (`execute.py`, Day 4) implements these. Every one is reported in the reconciliation output, never applied silently.

## Decided

1. **Case status `pending_customer`** loads as **Working**. The "waiting on customer" meaning is lost; the reconciliation report counts how many cases it affected. No metadata change.
2. **Customer tier**: add **`Account.Customer_Tier__c`, Text(40)**, and load the raw source value as-is (blank stays blank). Normalizing into a picklist waits for a business-approved value list. This is a metadata change, so it ships as its own PR through the pipeline before any load. The permission set grants access to it.
3. **Duplicate source ids** (an `acq_account_id` shared by two companies): **reject every row sharing the id**, and reject contacts and cases that point at it with the reason `parent rejected`. Nothing can say which row is right, and an upsert would let the later one silently overwrite the earlier.
4. **Near-duplicates of existing Core accounts**: **merge** by skipping the insert and re-parenting the acquired contacts and cases to the existing Core account (matched through its `External_Id__c`). The existing account is not modified. A duplicate the matcher missed loads as a new account (the plan found 24 of 25 seeded ones).

## Assumed, not asked (change them here if wrong)

- `open` loads as **New**, `in_progress` as **Working**, `escalated` as **Escalated**. The plan said `open -> New/Working`.
- Blank `company_name`, orphaned `acq_account_id`, and blank case `subject` rows are **rejected** with the reason in the report.
- Unrecognized priorities (`banana`, `???`, ...) **load** and the Apex trigger defaults them to Medium. They are listed in the report.
- Non-numeric or non-positive `annual_revenue` loads as blank, listed in the report. Blank `email` loads as blank.
- `Source_System__c` is `AcquiredCo` on everything the load creates, so a load can be found and reversed.
