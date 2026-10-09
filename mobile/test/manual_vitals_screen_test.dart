import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:medai/screens/manual_vitals_screen.dart';
import 'conversation_screen_test.dart';

void main() {
  testWidgets('ManualVitalsScreen renders fields and saves vitals via ApiClient', (WidgetTester tester) async {
    final fakeApi = FakeApiClient();
    fakeApi.vitalsToReturn = [
      {
        'type': 'heart_rate',
        'value': 72.0,
        'unit': 'bpm',
        'updated_at': '2026-10-09T06:00:00Z',
      }
    ];

    await tester.pumpWidget(
      MaterialApp(
        home: ManualVitalsScreen(apiClient: fakeApi),
      ),
    );
    await tester.pumpAndSettle();

    // Verify existing vital is rendered
    expect(find.text('Manual Vitals Entry'), findsOneWidget);
    expect(find.text('Heart Rate'), findsWidgets);
    expect(find.text('72.0 bpm'), findsOneWidget);

    // Enter a new SpO2 value
    final spo2Field = find.widgetWithText(TextField, 'SpO2 (Oxygen)');
    expect(spo2Field, findsOneWidget);
    await tester.enterText(spo2Field, '99');

    // Tap Save Vitals
    final saveButton = find.widgetWithText(FilledButton, 'Save Vitals');
    expect(saveButton, findsOneWidget);
    await tester.tap(saveButton);
    await tester.pumpAndSettle();

    // Verify vital was recorded via fakeApi
    expect(fakeApi.recordedVitals.length, 1);
    expect(fakeApi.recordedVitals.first['type'], 'oxygen_saturation');
    expect(fakeApi.recordedVitals.first['value'], 99.0);
    expect(fakeApi.recordedVitals.first['unit'], '%');
  });
}
