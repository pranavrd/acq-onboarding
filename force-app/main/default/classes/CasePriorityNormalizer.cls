/**
 * Maps the acquired company's priority strings (P0/P1/P2, Urgent, sev1, ...)
 * to the standard Case Priority values (High, Medium, Low).
 *
 * Rules:
 *  - Matching is case-insensitive and ignores surrounding whitespace.
 *  - Blank/null input returns DEFAULT_PRIORITY.
 *  - Unknown input also returns DEFAULT_PRIORITY. Never fail a load over a priority string.
 *  - No SOQL and no DML here, so it is safe to call from a trigger with 200 records.
 */
public with sharing class CasePriorityNormalizer {

    public static final String SOURCE_ACQUIRED = 'AcquiredCo';
    public static final String HIGH = 'High';
    public static final String MEDIUM = 'Medium';
    public static final String LOW = 'Low';
    public static final String DEFAULT_PRIORITY = MEDIUM;

    private static final Map<String, String> PRIORITY_MAP = new Map<String, String>{
        'p0' => HIGH, 'p1' => HIGH, 'sev0' => HIGH, 'sev1' => HIGH,
        'urgent' => HIGH, 'critical' => HIGH, 'blocker' => HIGH, 'high' => HIGH,
        'p2' => MEDIUM, 'sev2' => MEDIUM, 'normal' => MEDIUM, 'medium' => MEDIUM,
        'p3' => LOW, 'p4' => LOW, 'sev3' => LOW, 'sev4' => LOW,
        'minor' => LOW, 'trivial' => LOW, 'low' => LOW
    };

    /** Normalize one raw string. */
    public static String normalize(String raw) {
        if (String.isBlank(raw)) {
            return DEFAULT_PRIORITY;
        }
        String mapped = PRIORITY_MAP.get(raw.trim().toLowerCase());
        return mapped == null ? DEFAULT_PRIORITY : mapped;
    }

    /** True if the raw string is one we recognize (used by tests and reconciliation). */
    public static Boolean isKnown(String raw) {
        return !String.isBlank(raw) && PRIORITY_MAP.containsKey(raw.trim().toLowerCase());
    }

    /**
     * Called from CaseSourceTrigger. Only touches AcquiredCo cases.
     * On update, only re-normalizes when Source_Priority__c actually changed.
     * oldMap is null on insert.
     */
    public static void apply(List<Case> cases, Map<Id, Case> oldMap) {
        for (Case c : cases) {
            if (c.Source_System__c != SOURCE_ACQUIRED) {
                continue;
            }
            Boolean isUpdate = oldMap != null && c.Id != null && oldMap.containsKey(c.Id);
            if (isUpdate && c.Source_Priority__c == oldMap.get(c.Id).Source_Priority__c) {
                continue;
            }
            c.Priority = normalize(c.Source_Priority__c);
        }
    }
}
