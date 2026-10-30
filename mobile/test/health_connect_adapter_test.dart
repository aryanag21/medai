import 'package:flutter_test/flutter_test.dart';
import 'package:medai/services/health_connect_adapter.dart';

void main() {
  group('convertHealthConnectValue', () {
    test('converts body temperature from Celsius to Fahrenheit', () {
      // 37.0C is normal body temperature, ~98.6F — the backend's canonical unit.
      expect(convertHealthConnectValue('body_temperature', 37.0), closeTo(98.6, 0.01));
    });

    test('leaves other vital types unchanged (already in the backend-expected unit)', () {
      expect(convertHealthConnectValue('heart_rate', 72.0), 72.0);
      expect(convertHealthConnectValue('oxygen_saturation', 85.0), 85.0);
      expect(convertHealthConnectValue('blood_pressure_systolic', 120.0), 120.0);
      expect(convertHealthConnectValue('weight', 70.0), 70.0);
    });
  });

  group('HealthConnectReading.toJson and fromJson', () {
    test('serializes with an ISO-8601 UTC timestamp matching backend/app/schemas/vital.py', () {
      final reading = HealthConnectReading(
        type: 'heart_rate',
        value: 72.0,
        unit: 'bpm',
        timestamp: DateTime.utc(2026, 6, 1, 12, 30),
      );
      final json = reading.toJson();
      expect(json['type'], 'heart_rate');
      expect(json['value'], 72.0);
      expect(json['unit'], 'bpm');
      expect(json['timestamp'], '2026-06-01T12:30:00.000Z');

      final deserialized = HealthConnectReading.fromJson(json);
      expect(deserialized.type, reading.type);
      expect(deserialized.value, reading.value);
      expect(deserialized.unit, reading.unit);
      expect(deserialized.timestamp, reading.timestamp);
    });
  });

  group('bucketAndDeduplicateReadings', () {
    test('aggregates dense Nothing X heart rate samples into 1-minute buckets', () {
      final t1 = DateTime.utc(2026, 6, 1, 10, 0, 5);
      final t2 = DateTime.utc(2026, 6, 1, 10, 0, 20);
      final t3 = DateTime.utc(2026, 6, 1, 10, 0, 45);
      final t4 = DateTime.utc(2026, 6, 1, 10, 1, 10);

      final raw = [
        HealthConnectReading(type: 'heart_rate', value: 70.0, unit: 'bpm', timestamp: t1),
        HealthConnectReading(type: 'heart_rate', value: 80.0, unit: 'bpm', timestamp: t2),
        HealthConnectReading(type: 'heart_rate', value: 90.0, unit: 'bpm', timestamp: t3),
        HealthConnectReading(type: 'heart_rate', value: 75.0, unit: 'bpm', timestamp: t4),
      ];

      final processed = bucketAndDeduplicateReadings(raw);
      expect(processed.length, 2);

      // Minute 10:00 average: (70 + 80 + 90) / 3 = 80.0
      expect(processed[0].timestamp, DateTime.utc(2026, 6, 1, 10, 0));
      expect(processed[0].value, 80.0);

      // Minute 10:01 average: 75.0
      expect(processed[1].timestamp, DateTime.utc(2026, 6, 1, 10, 1));
      expect(processed[1].value, 75.0);
    });

    test('preserves discrete measurements and filters non-positive values', () {
      final t1 = DateTime.utc(2026, 6, 1, 10, 0);
      final raw = [
        HealthConnectReading(type: 'blood_pressure_systolic', value: 120.0, unit: 'mmHg', timestamp: t1),
        HealthConnectReading(type: 'blood_pressure_systolic', value: 120.0, unit: 'mmHg', timestamp: t1), // dup
        HealthConnectReading(type: 'heart_rate', value: -5.0, unit: 'bpm', timestamp: t1), // invalid
      ];

      final processed = bucketAndDeduplicateReadings(raw);
      expect(processed.length, 1);
      expect(processed[0].type, 'blood_pressure_systolic');
      expect(processed[0].value, 120.0);
    });
  });
}
