@IsTest
private class CasePriorityNormalizerTest {

    @IsTest
    static void mapsKnownValues() {
        System.assertEquals('High', CasePriorityNormalizer.normalize('P0'));
        System.assertEquals('High', CasePriorityNormalizer.normalize('P1'));
        System.assertEquals('High', CasePriorityNormalizer.normalize('Urgent'));
        System.assertEquals('High', CasePriorityNormalizer.normalize('sev1'));
        System.assertEquals('Medium', CasePriorityNormalizer.normalize('P2'));
        System.assertEquals('Medium', CasePriorityNormalizer.normalize('Normal'));
        System.assertEquals('Low', CasePriorityNormalizer.normalize('P3'));
        System.assertEquals('Low', CasePriorityNormalizer.normalize('sev4'));
        System.assertEquals('Low', CasePriorityNormalizer.normalize('low'));
    }

    @IsTest
    static void ignoresCaseAndWhitespace() {
        System.assertEquals('High', CasePriorityNormalizer.normalize('  URGENT '));
        System.assertEquals('Low', CasePriorityNormalizer.normalize(' Sev3'));
    }

    @IsTest
    static void nullBlankAndUnknownGetDefault() {
        System.assertEquals('Medium', CasePriorityNormalizer.normalize(null));
        System.assertEquals('Medium', CasePriorityNormalizer.normalize(''));
        System.assertEquals('Medium', CasePriorityNormalizer.normalize('   '));
        System.assertEquals('Medium', CasePriorityNormalizer.normalize('banana'));
        System.assertEquals(false, CasePriorityNormalizer.isKnown('banana'));
        System.assertEquals(false, CasePriorityNormalizer.isKnown(null));
        System.assertEquals(true, CasePriorityNormalizer.isKnown('p1'));
    }

    @IsTest
    static void bulkInsert200NormalizesAll() {
        String[] raws = new String[]{ 'P0', 'P2', 'P4', 'weird', null };
        String[] expected = new String[]{ 'High', 'Medium', 'Low', 'Medium', 'Medium' };
        List<Case> cases = new List<Case>();
        for (Integer i = 0; i < 200; i++) {
            cases.add(new Case(
                Subject = 'Bulk ' + i,
                Origin = 'Web',
                Source_System__c = 'AcquiredCo',
                Source_Priority__c = raws[Math.mod(i, raws.size())]
            ));
        }

        Test.startTest();
        insert cases;
        Test.stopTest();

        Map<Id, Case> saved = new Map<Id, Case>([SELECT Id, Subject, Priority FROM Case WHERE Id IN :cases]);
        System.assertEquals(200, saved.size());
        for (Integer i = 0; i < 200; i++) {
            System.assertEquals(expected[Math.mod(i, expected.size())], saved.get(cases[i].Id).Priority,
                'Wrong priority for row ' + i);
        }
    }

    @IsTest
    static void coreCasesAreUntouched() {
        Case c = new Case(Subject = 'Core case', Origin = 'Web',
                          Source_System__c = 'Core', Source_Priority__c = 'P0', Priority = 'Low');
        insert c;
        System.assertEquals('Low', [SELECT Priority FROM Case WHERE Id = :c.Id].Priority);
    }

    @IsTest
    static void updateRenormalizesOnlyWhenSourcePriorityChanges() {
        Case c = new Case(Subject = 'Update me', Origin = 'Web',
                          Source_System__c = 'AcquiredCo', Source_Priority__c = 'P3');
        insert c;
        System.assertEquals('Low', [SELECT Priority FROM Case WHERE Id = :c.Id].Priority);

        // Unrelated change: a manual Priority edit must survive.
        c.Priority = 'High';
        update c;
        System.assertEquals('High', [SELECT Priority FROM Case WHERE Id = :c.Id].Priority);

        // Source priority changes: re-normalize.
        c.Source_Priority__c = 'sev2';
        update c;
        System.assertEquals('Medium', [SELECT Priority FROM Case WHERE Id = :c.Id].Priority);
    }
}
