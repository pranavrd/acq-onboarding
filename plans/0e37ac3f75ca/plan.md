# Onboarding plan `0e37ac3f75ca`

Model: `claude-sonnet-5-5` | tool calls: 18 | tokens in/out: 69058/8477

Read-only plan. Nothing was written to any org.

## Needs a human (8)

- **mapping** `accounts.tier`: no target field (confidence 0.9)
- **mapping** `accounts.legacy_health_score`: no target field (confidence 0.85)
- **mapping** `cases.status`: low confidence (confidence 0.7)
- **mapping** `cases.created_date`: low confidence (confidence 0.7)
- **data_quality** `accounts.csv (5 rows: ACQ-A-00186, ACQ-A-00030, ACQ-A-00102, ACQ-A-00195, ACQ-A-00012)`: duplicate acq_account_id (count = distinct ids that appear more than once)
- **data_quality** `accounts.csv (56 rows: ACQ-A-00492, ACQ-A-00100, ACQ-A-00310, ACQ-A-00241, ACQ-A-00166, ACQ-A-00309, ACQ-A-00175, ACQ-A-00066, ACQ-A-00213, ACQ-A-00101)`: blank tier
- **data_quality** `cases.csv (8 rows: ACQ-K-00067, ACQ-K-00317, ACQ-K-00384, ACQ-K-00389, ACQ-K-00494, ACQ-K-00514, ACQ-K-00660, ACQ-K-00664)`: blank subject
- **data_quality** `cases.csv (8 rows: ACQ-K-00015, ACQ-K-00160, ACQ-K-00174, ACQ-K-00175, ACQ-K-00180, ACQ-K-00322, ACQ-K-00351, ACQ-K-00388)`: unrecognized priority (the Apex normalizer defaults these to Medium)

## Field mappings

| Source | Target | Transform | Confidence | Reasoning |
|---|---|---|---|---|
| `accounts.acq_account_id` | `Account.External_Id__c` | none (Source_System__c='AcquiredCo') | 0.95 | External ID field for upsert/relationship keys. |
| `accounts.company_name` | `Account.Name` | trim, collapse double spaces; fix casing/'The'/trailing 'INC.' only for display, not matching | 0.95 | Direct name mapping; blank names rejected. |
| `accounts.industry` | `Account.Industry` | none; all 8 values (Media, Finance, Healthcare, Manufacturing, Energy, Retail, Transportation, Technology) verified in picklist | 0.93 | Values verified against PicklistValueInfo. |
| `accounts.tier` | `None` | none | 0.9 | Org has no customer tier field; 11 messy values (T2, Tier 1, VIP, strategic, ...) with blanks. Proposed new field Customer_Tier__c; tier scheme needs human normalization. |
| `accounts.website` | `Account.Website` | none | 0.95 | Direct. |
| `accounts.billing_city` | `Account.BillingCity` | none | 0.93 | Component of BillingAddress compound field. |
| `accounts.billing_country` | `Account.BillingCountry` | none (ISO codes US/AU/SG/IN; check if org uses state/country picklists) | 0.85 | Component of BillingAddress. |
| `accounts.annual_revenue` | `Account.AnnualRevenue` | parse to number; non-positive/non-numeric left null | 0.93 | Currency field. |
| `accounts.employee_count` | `Account.NumberOfEmployees` | integer | 0.93 | Direct. |
| `accounts.legacy_health_score` | `None` | none | 0.85 | No equivalent field in org; legacy scoring semantics unknown, so no new field proposed. |
| `contacts.acq_contact_id` | `Contact.External_Id__c` | none (Source_System__c='AcquiredCo') | 0.95 | External ID. |
| `contacts.acq_account_id` | `Contact.AccountId` | lookup Account by External_Id__c; for deduped accounts point to existing CORE account | 0.9 | Parent relationship. |
| `contacts.first_name` | `Contact.FirstName` | none | 0.97 | Direct. |
| `contacts.last_name` | `Contact.LastName` | none | 0.97 | Direct. |
| `contacts.email` | `Contact.Email` | none; blank left null | 0.95 | Direct. |
| `contacts.phone` | `Contact.Phone` | none | 0.93 | Direct. |
| `contacts.title` | `Contact.Title` | none | 0.95 | Direct. |
| `cases.acq_case_id` | `Case.External_Id__c` | none (Source_System__c='AcquiredCo') | 0.95 | External ID. |
| `cases.acq_account_id` | `Case.AccountId` | lookup Account by External_Id__c; deduped accounts resolve to existing CORE account | 0.9 | Parent relationship. |
| `cases.subject` | `Case.Subject` | none | 0.95 | Direct; blanks flagged. |
| `cases.priority` | `Case.Source_Priority__c` | load raw value; trigger normalizes to High/Medium/Low (unknown -> Medium) | 0.9 | Per instructions the trigger normalizes Source_Priority__c into Priority; raw values include Urgent, P0-P3, sev1-3, Critical, Normal, Low and junk. |
| `cases.status` | `Case.Status` | open->New, in_progress->Working, escalated->Escalated, pending_customer->Working (needs human confirmation; no matching picklist value) | 0.7 | Org Status values: New, Working, Escalated, Closed. pending_customer has no equivalent, so mapping is a guess. |
| `cases.created_date` | `Case.CreatedDate` | date to datetime; requires Create Audit Fields permission | 0.7 | Preserves history, but needs audit-field setting enabled; otherwise lost. Note dates in file are in the future (2026). |

## Duplicates of existing accounts (24)

- `ACQ-A-00019` -> `CORE-00031` (0.97): 'The Eagle Foods LLC.' equals 'Eagle Foods LLC' after normalization.
- `ACQ-A-00006` -> `CORE-00047` (0.97): Double space only.
- `ACQ-A-00013` -> `CORE-00036` (0.97): Double space only.
- `ACQ-A-00014` -> `CORE-00005` (0.97): Double space only.
- `ACQ-A-00017` -> `CORE-00027` (0.97): 'The' prefix and trailing period.
- `ACQ-A-00015` -> `CORE-00046` (0.96): Uppercase with redundant ', INC.' suffix.
- `ACQ-A-00007` -> `CORE-00017` (0.96): Uppercase with ', INC.' suffix.
- `ACQ-A-00002` -> `CORE-00048` (0.96): Uppercase with ', INC.' suffix.
- `ACQ-A-00011` -> `CORE-00016` (0.95): CORPORATION vs Corp plus ', INC.' suffix.
- `ACQ-A-00024` -> `CORE-00023` (0.96): Uppercase with ', INC.' suffix.
- `ACQ-A-00010` -> `CORE-00011` (0.97): Double space only.
- `ACQ-A-00020` -> `CORE-00010` (0.95): CORPORATION vs Corp plus ', INC.'.
- `ACQ-A-00003` -> `CORE-00007` (0.96): Uppercase with ', INC.' suffix.
- `ACQ-A-00005` -> `CORE-00041` (0.9): Transposition typo 'aMterials'.
- `ACQ-A-00022` -> `CORE-00008` (0.9): Transposition typo 'nAalytics'.
- `ACQ-A-00009` -> `CORE-00006` (0.9): Typo/space 'Pionee rSystems'.
- `ACQ-A-00016` -> `CORE-00001` (0.9): Transposition 'Quratz'.
- `ACQ-A-00023` -> `CORE-00032` (0.9): Transposition 'Xneon'.
- `ACQ-A-00004` -> `CORE-00014` (0.9): Transposition 'Faclon'.
- `ACQ-A-00025` -> `CORE-00028` (0.9): Transposition 'Garnite'.
- `ACQ-A-00001` -> `CORE-00040` (0.9): Transposition 'Yodner'.
- `ACQ-A-00012` -> `CORE-00012` (0.9): Transposition 'Reatil'. Note this ACQ id also appears duplicated in accounts.csv.
- `ACQ-A-00008` -> `CORE-00043` (0.9): Transposition 'Keystnoe'.
- `ACQ-A-00018` -> `CORE-00029` (0.9): Transposition 'Enregy'.

## Proposed new fields

- `Account.Customer_Tier__c` (Text(40) (or Picklist after tier normalization)): Acquired company's tier (VIP, Gold, T2, Tier 1, etc.) has business value for segmentation and the org has no tier field; values need a human-agreed mapping.

## Data quality issues (9)

- `accounts.csv`: blank company_name (5 rows; e.g. ACQ-A-00297, ACQ-A-00264, ACQ-A-00287, ACQ-A-00172, ACQ-A-00452) -> reject
- `accounts.csv`: duplicate acq_account_id (count = distinct ids that appear more than once) (5 rows; e.g. ACQ-A-00186, ACQ-A-00030, ACQ-A-00102, ACQ-A-00195, ACQ-A-00012) -> needs_human
- `accounts.csv`: annual_revenue is not a positive number (5 rows; e.g. ACQ-A-00100, ACQ-A-00096, ACQ-A-00056, ACQ-A-00161, ACQ-A-00091) -> fix
- `accounts.csv`: blank tier (56 rows; e.g. ACQ-A-00492, ACQ-A-00100, ACQ-A-00310, ACQ-A-00241, ACQ-A-00166, ACQ-A-00309, ACQ-A-00175, ACQ-A-00066, ACQ-A-00213, ACQ-A-00101) -> needs_human
- `contacts.csv`: blank email (15 rows; e.g. ACQ-C-00008, ACQ-C-00041, ACQ-C-00094, ACQ-C-00253, ACQ-C-00283, ACQ-C-00337, ACQ-C-00457, ACQ-C-00474, ACQ-C-00503, ACQ-C-00515) -> fix
- `contacts.csv`: orphaned acq_account_id (no such account in accounts.csv) (15 rows; e.g. ACQ-C-00066, ACQ-C-00147, ACQ-C-00207, ACQ-C-00295, ACQ-C-00409, ACQ-C-00470, ACQ-C-00472, ACQ-C-00539, ACQ-C-00590, ACQ-C-00734) -> reject
- `cases.csv`: blank subject (8 rows; e.g. ACQ-K-00067, ACQ-K-00317, ACQ-K-00384, ACQ-K-00389, ACQ-K-00494, ACQ-K-00514, ACQ-K-00660, ACQ-K-00664) -> needs_human
- `cases.csv`: orphaned acq_account_id (no such account in accounts.csv) (8 rows; e.g. ACQ-K-00231, ACQ-K-00353, ACQ-K-00496, ACQ-K-00497, ACQ-K-00536, ACQ-K-00538, ACQ-K-00602, ACQ-K-00685) -> reject
- `cases.csv`: unrecognized priority (the Apex normalizer defaults these to Medium) (8 rows; e.g. ACQ-K-00015, ACQ-K-00160, ACQ-K-00174, ACQ-K-00175, ACQ-K-00180, ACQ-K-00322, ACQ-K-00351, ACQ-K-00388) -> needs_human

## Notes

All 24 fuzzy candidates were judged real duplicates: they are formatting variants or single transposition typos of existing CORE names, with no lookalike siblings among the candidates. Merge them into the existing CORE accounts rather than creating new ones, and re-point their contacts and cases. Plan: set Source_System__c='AcquiredCo' on all loaded rows (picklist verified: AcquiredCo, Core). Case Status mapping: pending_customer has no org equivalent and is mapped to Working as a guess, so a human should confirm; no source status maps to Closed. Case priority is loaded raw into Source_Priority__c; the 8 junk values (ASAP!!, banana, tbd, ???) will default to Medium and need review. Case created_date requires audit-field permission. Duplicate acq_account_id rows need a survivor decision before load. Orphaned contacts and cases cannot be linked to an account; they are rejected unless the missing accounts are supplied. Case rows for ACQ-A-00012 and similar duplicate ids should be checked after dedupe.
