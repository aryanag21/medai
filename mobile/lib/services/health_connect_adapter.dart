import 'package:health/health.dart';

/// vital_system.rules: "If device fails: return {status: unavailable, reason: DEVICE_ERROR}" —
/// raised instead of returning fabricated/empty-but-successful data.
class HealthConnectUnavailable implements Exception {
  HealthConnectUnavailable(this.reason);
  final String reason;

  @override
  String toString() => 'HealthConnectUnavailable: $reason';
}

// Health Connect reports body temperature in Celsius (HealthDataUnit.DEGREE_CELSIUS); the
// backend's canonical unit is Fahrenheit (app/safety/vital_rules.py's sourced thresholds and
// app/vitals/validation.py's EXPECTED_UNITS are both in F). Every other supported type Health
// Connect reports in the unit the backend already expects (bpm, %, mmHg, breaths/min, kg) — no
// conversion needed, verified against the installed package's own default-unit table
// (lib/src/heath_data_types.dart). A top-level function, not a private method, so it's directly
// unit-testable without touching the Health Connect SDK itself.
double convertHealthConnectValue(String vitalType, double rawValue) {
  if (vitalType == 'body_temperature') {
    return rawValue * 9 / 5 + 32;
  }
  return rawValue;
}

/// The wire shape POST /vitals/sync expects — see backend/app/schemas/vital.py's
/// VitalSyncReading. `type`/`unit` are already in the backend's canonical vocabulary
/// (app/vitals/schema.py's VitalType, app/vitals/validation.py's EXPECTED_UNITS), converted
/// here rather than left for the server to guess.
class HealthConnectReading {
  HealthConnectReading({
    required this.type,
    required this.value,
    required this.unit,
    required this.timestamp,
  });

  final String type;
  final double value;
  final String unit;
  final DateTime timestamp;

  Map<String, dynamic> toJson() => {
        'type': type,
        'value': value,
        'unit': unit,
        'timestamp': timestamp.toUtc().toIso8601String(),
      };

  factory HealthConnectReading.fromJson(Map<String, dynamic> json) => HealthConnectReading(
        type: json['type'] as String,
        value: (json['value'] as num).toDouble(),
        unit: json['unit'] as String,
        timestamp: DateTime.parse(json['timestamp'] as String),
      );
}

/// Deduplicates and downsamples dense high-frequency readings from companion apps like Nothing X
/// or wearable watches. Continuous metrics (heart rate, respiratory rate) are aggregated into
/// 1-minute buckets (taking the mean value), while discrete readings (BP, SpO2, temp) are kept intact.
List<HealthConnectReading> bucketAndDeduplicateReadings(List<HealthConnectReading> raw) {
  if (raw.isEmpty) return [];

  // Filter out non-positive values and corrupt future timestamps
  final nowLimit = DateTime.now().add(const Duration(minutes: 5));
  final valid = raw.where((r) => r.value > 0 && r.timestamp.isBefore(nowLimit)).toList();
  valid.sort((a, b) => a.timestamp.compareTo(b.timestamp));

  final processed = <HealthConnectReading>[];

  // Group continuous vitals into 1-minute buckets
  final continuousBuckets = <String, Map<String, List<double>>>{};
  final continuousTimestamps = <String, Map<String, DateTime>>{};
  final continuousUnits = <String, String>{};
  final seenDiscrete = <String>{};

  for (final r in valid) {
    if (r.type == 'heart_rate' || r.type == 'respiratory_rate') {
      continuousUnits[r.type] = r.unit;
      final bucketKey =
          '${r.timestamp.year}-${r.timestamp.month.toString().padLeft(2, '0')}-${r.timestamp.day.toString().padLeft(2, '0')} ${r.timestamp.hour.toString().padLeft(2, '0')}:${r.timestamp.minute.toString().padLeft(2, '0')}';
      final typeBuckets = continuousBuckets.putIfAbsent(r.type, () => {});
      final values = typeBuckets.putIfAbsent(bucketKey, () => []);
      values.add(r.value);

      final typeTimes = continuousTimestamps.putIfAbsent(r.type, () => {});
      typeTimes.putIfAbsent(
        bucketKey,
        () => DateTime.utc(
          r.timestamp.year,
          r.timestamp.month,
          r.timestamp.day,
          r.timestamp.hour,
          r.timestamp.minute,
        ),
      );
    } else {
      // Discrete vitals: deduplicate identical type + timestamp
      final key = '${r.type}_${r.timestamp.millisecondsSinceEpoch}';
      if (!seenDiscrete.contains(key)) {
        seenDiscrete.add(key);
        processed.add(r);
      }
    }
  }

  // Aggregate continuous readings
  for (final typeEntry in continuousBuckets.entries) {
    final type = typeEntry.key;
    final unit = continuousUnits[type] ?? '';
    for (final bucketEntry in typeEntry.value.entries) {
      final bucketKey = bucketEntry.key;
      final vals = bucketEntry.value;
      if (vals.isEmpty) continue;
      final avg = vals.reduce((a, b) => a + b) / vals.length;
      final roundedAvg = (avg * 10).round() / 10.0;
      final timestamp = continuousTimestamps[type]![bucketKey]!;
      processed.add(
        HealthConnectReading(
          type: type,
          value: roundedAvg,
          unit: unit,
          timestamp: timestamp,
        ),
      );
    }
  }

  // Chronological sort
  processed.sort((a, b) => a.timestamp.compareTo(b.timestamp));
  return processed;
}

/// The real, concrete DeviceAdapter for Google Health Connect (Phase 10).
class HealthConnectAdapter {
  final Health _health = Health();

  static const _typeMap = <HealthDataType, String>{
    HealthDataType.HEART_RATE: 'heart_rate',
    HealthDataType.BLOOD_OXYGEN: 'oxygen_saturation',
    HealthDataType.BLOOD_PRESSURE_SYSTOLIC: 'blood_pressure_systolic',
    HealthDataType.BLOOD_PRESSURE_DIASTOLIC: 'blood_pressure_diastolic',
    HealthDataType.BODY_TEMPERATURE: 'body_temperature',
    HealthDataType.RESPIRATORY_RATE: 'respiratory_rate',
    HealthDataType.WEIGHT: 'weight',
  };

  static const _expectedUnit = <String, String>{
    'heart_rate': 'bpm',
    'oxygen_saturation': '%',
    'blood_pressure_systolic': 'mmHg',
    'blood_pressure_diastolic': 'mmHg',
    'body_temperature': 'F',
    'respiratory_rate': 'breaths/min',
    'weight': 'kg',
  };

  Future<void> _ensureAvailable() async {
    final status = await _health.getHealthConnectSdkStatus();
    if (status == HealthConnectSdkStatus.sdkUnavailableProviderUpdateRequired) {
      try {
        await _health.installHealthConnect();
      } catch (_) {}
      throw HealthConnectUnavailable('Health Connect update required. Please install or update it from Google Play.');
    }
    if (status != HealthConnectSdkStatus.sdkAvailable) {
      throw HealthConnectUnavailable('Health Connect is unavailable on this device (${status?.name ?? 'unknown'}).');
    }
  }

  /// Prompts the OS-level Health Connect permission dialog for every vital type this adapter can read.
  Future<bool> requestPermissions() async {
    await _ensureAvailable();
    return _health.requestAuthorization(_typeMap.keys.toList());
  }

  /// Reads real Health Connect samples in the specified [startTime] to [endTime] window,
  /// automatically deduplicated and downsampled for network and storage efficiency.
  Future<List<HealthConnectReading>> readRange({
    required DateTime startTime,
    required DateTime endTime,
  }) async {
    await _ensureAvailable();
    final points = await _health.getHealthDataFromTypes(
      types: _typeMap.keys.toList(),
      startTime: startTime,
      endTime: endTime,
    );

    final rawReadings = <HealthConnectReading>[];
    for (final point in points) {
      final vitalType = _typeMap[point.type];
      if (vitalType == null) continue;
      final value = point.value;
      if (value is! NumericHealthValue) continue;

      rawReadings.add(
        HealthConnectReading(
          type: vitalType,
          value: convertHealthConnectValue(vitalType, value.numericValue.toDouble()),
          unit: _expectedUnit[vitalType]!,
          timestamp: point.dateFrom,
        ),
      );
    }

    return bucketAndDeduplicateReadings(rawReadings);
  }

  /// Reads real Health Connect samples from [lookback] ago to now.
  Future<List<HealthConnectReading>> readRecent({Duration lookback = const Duration(days: 1)}) async {
    final now = DateTime.now();
    return readRange(startTime: now.subtract(lookback), endTime: now);
  }
}
