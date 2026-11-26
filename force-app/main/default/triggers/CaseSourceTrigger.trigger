trigger CaseSourceTrigger on Case (before insert, before update) {
    // All logic lives in the class so it is testable and bulk-safe.
    CasePriorityNormalizer.apply(Trigger.new, Trigger.oldMap);
}
