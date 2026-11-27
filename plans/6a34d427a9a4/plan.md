# Onboarding plan `6a34d427a9a4`

Model: `claude-sonnet-5-5` | tool calls: 16 | tokens in/out: 76742/8288

Read-only plan. Nothing was written to any org.

## Needs a human (6)

- **mapping** `accounts.legacy_health_score`: no target field (confidence 0.8)
- **mapping** `cases.status`: low confidence (confidence 0.6)
- **mapping** `cases.created_date`: low confidence (confidence 0.65)
- **data_quality** `accounts.csv (5 rows: ACQ-A-00186, ACQ-A-00030, ACQ-A-00102, ACQ-A-00195, ACQ-A-00012)`: duplicate acq_account_id (count = distinct ids that appear more than once)
- **data_quality** `cases.csv (8 rows: ACQ-K-00067, ACQ-K-00317, ACQ-K-00384, ACQ-K-00389, ACQ-K-00494, ACQ-K-00514, ACQ-K-00660, ACQ-K-00664)`: blank subject
- **data_quality** `cases.csv (8 rows: ACQ-K-00015, ACQ-K-00160, ACQ-K-00174, ACQ-K-00175, ACQ-K-00180, ACQ-K-00322, ACQ-K-00351, ACQ-K-00388)`: unrecognized priority (the Apex normalizer defaults these to Medium)

## Field mappings

| Source | Target | Transform | Confidence | Reasoning |
|---|---|---|---|---|
| `accounts.acq_account_id` | `Account.External_Id__c` | none (Source_System__c='AcquiredCo' set as constant) | 0.95 | External ID field for upsert; Source_System__c picklist has AcquiredCo. |
| `accounts.company_name` | `Account.Name` | trim, collapse double spaces; blank rows rejected | 0.97 | Direct name mapping. |
| `accounts.industry` | `Account.Industry` | none | 0.93 | All 8 distinct values (Media, Finance, Healthcare, Manufacturing, Energy, Retail, Transportation, Technology) verified present in the Industry picklist. |
| `accounts.tier` | `Account.Customer_Tier__c` | none; keep raw value | 0.95 | Customer_Tier__c holds raw acquired tier (Text 40). Values are messy (T2, Tier 1, VIP, strategic...) so not normalized. |
| `accounts.website` | `Account.Website` | none | 0.95 | URL field. |
| `accounts.billing_city` | `Account.BillingCity` | none | 0.93 | Standard billing address component (BillingAddress compound field exists). |
| `accounts.billing_country` | `Account.BillingCountry` | none (ISO 2-letter codes; check country/state picklist settings) | 0.85 | Standard component; codes like US/AU/SG/IN may need validation if state/country picklists are enabled. |
| `accounts.annual_revenue` | `Account.AnnualRevenue` | cast to number; non-positive/non-numeric set null | 0.92 | Currency field. |
| `accounts.employee_count` | `Account.NumberOfEmployees` | cast to integer | 0.93 | Number(8,0); values observed fit. |
| `accounts.legacy_health_score` | `None` | none | 0.8 | No field in org for a legacy health score; not loaded unless a new field is approved. |
| `contacts.acq_contact_id` | `Contact.External_Id__c` | none; Source_System__c='AcquiredCo' | 0.93 | Org has External_Id__c on Contact per instructions (Contact fields not queried directly). |
| `contacts.acq_account_id` | `Contact.AccountId` | resolve via Account.External_Id__c (or matched Core account after dedupe) | 0.9 | Lookup by external id; orphans cannot resolve. |
| `contacts.first_name` | `Contact.FirstName` | none | 0.96 | Standard field. |
| `contacts.last_name` | `Contact.LastName` | none | 0.96 | Standard field. |
| `contacts.email` | `Contact.Email` | none; blanks left null | 0.95 | Standard field. |
| `contacts.phone` | `Contact.Phone` | none | 0.93 | Standard field. |
| `contacts.title` | `Contact.Title` | none | 0.95 | Standard field. |
| `cases.acq_case_id` | `Case.External_Id__c` | none; Source_System__c='AcquiredCo' | 0.95 | External ID on Case verified. |
| `cases.acq_account_id` | `Case.AccountId` | resolve via Account.External_Id__c (or matched Core account) | 0.9 | Lookup resolution. |
| `cases.subject` | `Case.Subject` | none; blank rows need a decision | 0.95 | Direct. |
| `cases.priority` | `Case.Source_Priority__c` | copy raw value; trigger normalizes to High/Medium/Low, unknown -> Medium and flagged | 0.9 | Per instructions trigger normalizes from Source_Priority__c; do not write Priority directly. |
| `cases.status` | `Case.Status` | open->New, in_progress->Working, escalated->Escalated, pending_customer->NEEDS DECISION (no matching value; Working is a tentative choice) | 0.6 | Case Status picklist verified: New, Escalated, Working, Closed. pending_customer (191 rows) has no clean equivalent, so overall confidence is low. |
| `cases.created_date` | `Case.CreatedDate` | date -> datetime; requires 'Set Audit Fields' permission on load | 0.65 | CreatedDate is only writable with audit-field permission; otherwise it would need a custom field. Dates are in the future (2026), so check them too. |

## Duplicates of existing accounts (24)

- `ACQ-A-00019` -> `CORE-00031` (0.95): 'The Eagle Foods LLC.' vs 'Eagle Foods LLC': only a prefix and punctuation differ.
- `ACQ-A-00006` -> `CORE-00047` (0.97): Double space only.
- `ACQ-A-00013` -> `CORE-00036` (0.97): Double space only.
- `ACQ-A-00014` -> `CORE-00005` (0.97): Double space only.
- `ACQ-A-00017` -> `CORE-00027` (0.95): 'The' prefix and period.
- `ACQ-A-00015` -> `CORE-00046` (0.93): Uppercase with duplicated ', INC.' suffix.
- `ACQ-A-00007` -> `CORE-00017` (0.93): Uppercase plus ', INC.' suffix.
- `ACQ-A-00002` -> `CORE-00048` (0.93): Uppercase plus ', INC.' suffix.
- `ACQ-A-00011` -> `CORE-00016` (0.92): CORPORATION vs Corp, uppercase, extra INC.
- `ACQ-A-00024` -> `CORE-00023` (0.93): Uppercase plus ', INC.' suffix.
- `ACQ-A-00010` -> `CORE-00011` (0.97): Double space only.
- `ACQ-A-00020` -> `CORE-00010` (0.92): CORPORATION vs Corp, uppercase, extra INC.
- `ACQ-A-00003` -> `CORE-00007` (0.93): Uppercase plus INC suffix.
- `ACQ-A-00005` -> `CORE-00041` (0.9): Transposition typo 'aMterials'.
- `ACQ-A-00022` -> `CORE-00008` (0.9): Transposition typo 'nAalytics'.
- `ACQ-A-00009` -> `CORE-00006` (0.9): Typo with space in 'Pionee rSystems'.
- `ACQ-A-00016` -> `CORE-00001` (0.9): Transposition typo 'Quratz'.
- `ACQ-A-00023` -> `CORE-00032` (0.9): Transposition typo 'Xneon'.
- `ACQ-A-00004` -> `CORE-00014` (0.9): Transposition typo 'Faclon'.
- `ACQ-A-00025` -> `CORE-00028` (0.9): Transposition typo 'Garnite'.
- `ACQ-A-00001` -> `CORE-00040` (0.88): Transposition typo 'Yodner'.
- `ACQ-A-00012` -> `CORE-00012` (0.88): Typo 'Reatil'. This acq id is also duplicated within accounts.csv, so confirm.
- `ACQ-A-00008` -> `CORE-00043` (0.9): Transposition typo 'Keystnoe'.
- `ACQ-A-00018` -> `CORE-00029` (0.88): Transposition typo 'Enregy'.

## Proposed new fields

- `Account.Legacy_Health_Score__c` (Number(3,0)): Acquired company's health score (0-100) has no home in the org. It may be useful for retention and churn analysis, but business value needs confirmation.

## Data quality issues (9)

- `accounts.csv`: blank company_name (5 rows; e.g. ACQ-A-00297, ACQ-A-00264, ACQ-A-00287, ACQ-A-00172, ACQ-A-00452) -> reject
- `accounts.csv`: duplicate acq_account_id (count = distinct ids that appear more than once) (5 rows; e.g. ACQ-A-00186, ACQ-A-00030, ACQ-A-00102, ACQ-A-00195, ACQ-A-00012) -> needs_human
- `accounts.csv`: annual_revenue is not a positive number (5 rows; e.g. ACQ-A-00100, ACQ-A-00096, ACQ-A-00056, ACQ-A-00161, ACQ-A-00091) -> fix
- `accounts.csv`: blank tier (56 rows; e.g. ACQ-A-00492, ACQ-A-00100, ACQ-A-00310, ACQ-A-00241, ACQ-A-00166, ACQ-A-00309, ACQ-A-00175, ACQ-A-00066, ACQ-A-00213, ACQ-A-00101) -> fix
- `contacts.csv`: blank email (15 rows; e.g. ACQ-C-00008, ACQ-C-00041, ACQ-C-00094, ACQ-C-00253, ACQ-C-00283, ACQ-C-00337, ACQ-C-00457, ACQ-C-00474, ACQ-C-00503, ACQ-C-00515) -> fix
- `contacts.csv`: orphaned acq_account_id (no such account in accounts.csv) (15 rows; e.g. ACQ-C-00066, ACQ-C-00147, ACQ-C-00207, ACQ-C-00295, ACQ-C-00409, ACQ-C-00470, ACQ-C-00472, ACQ-C-00539, ACQ-C-00590, ACQ-C-00734) -> reject
- `cases.csv`: blank subject (8 rows; e.g. ACQ-K-00067, ACQ-K-00317, ACQ-K-00384, ACQ-K-00389, ACQ-K-00494, ACQ-K-00514, ACQ-K-00660, ACQ-K-00664) -> needs_human
- `cases.csv`: orphaned acq_account_id (no such account in accounts.csv) (8 rows; e.g. ACQ-K-00231, ACQ-K-00353, ACQ-K-00496, ACQ-K-00497, ACQ-K-00536, ACQ-K-00538, ACQ-K-00602, ACQ-K-00685) -> reject
- `cases.csv`: unrecognized priority (the Apex normalizer defaults these to Medium) (8 rows; e.g. ACQ-K-00015, ACQ-K-00160, ACQ-K-00174, ACQ-K-00175, ACQ-K-00180, ACQ-K-00322, ACQ-K-00351, ACQ-K-00388) -> needs_human

## Notes

Verified against the org: Case Status picklist (New, Escalated, Working, Closed), Industry (all 8 source values valid), Source_System__c (Core, AcquiredCo). The 24 fuzzy matches against the 50 Core accounts all look like real duplicates (case, suffix, whitespace or transposition differences). I did not find lookalikes, such as Redwood Labs vs Redwood Analytics, among the candidates. Matched accounts should be merged or linked to the Core record rather than created again, and their contacts and cases should be re-parented to the Core account. Open decisions: (1) cases with status pending_customer (191) have no matching Case Status value. Options are map to Working or add a picklist value. (2) Priority values banana, tbd, ???, ASAP!! default to Medium and are flagged for review. Raw values are kept in Source_Priority__c. (3) Case created_date values are in 2026 and need a check, and loading them needs audit-field permission. (4) Duplicate acq_account_ids in accounts.csv need a survivor rule. (5) Contact and Case fields beyond Case were not described directly. I assumed the standard fields and the External_Id__c fields that the instructions say exist on Contact. Blank tier rows load with Customer_Tier__c empty. Blank email contacts load without an email.
