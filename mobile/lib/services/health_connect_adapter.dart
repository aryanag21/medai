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
  HealthConnectReading({required this.type, required this.value, required this.unit, required this.timestamp});

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
}

/// The real, concrete DeviceAdapter for Google Health Connect (Phase 10).
///
/// This is deliberately NOT a Python class in backend/app/providers/devices/, even though
/// that's where architecture.provider_interfaces' DeviceAdapter is defined — see that file's
/// docstring. Health Connect's data lives in an OS-level store on the user's own device, gated
/// by that device's own runtime permission grants; only this app process, running on that
/// device, can ever read it. This class is vital_system.device_pipeline's
/// DEVICE -> DEVICE_ADAPTER step, turning raw Health Connect samples into normalized readings;
/// HealthSyncService (health_sync_service.dart) POSTs the result to /vitals/sync — the
/// VITAL_SERVICE step this adapter's read() cannot reach on its own, since only the backend can
/// validate/persist against the canonical patient state.
class HealthConnectAdapter {
  final Health _health = Health();

  // vital_system.initial_measurements this adapter can source from Health Connect. Steps,
  // sleep, etc. exist in Health Connect but aren't in initial_measurements, so aren't read here.
  static const _typeMap = <HealthDataType, String>{
    HealthDataType.HEART_RATE: 'heart_rate',
    HealthDataType.BLOOD_OXYGEN: 'oxygen_saturation',
    HealthDataType.BLOOD_PRESSURE_SYSTOLIC: 'blood_pressure_systolic',
    HealthDataType.BLOOD_PRESSURE_DIASTOLIC: 'blood_pressure_diastolic',
    HealthDataType.BODY_TEMPERATURE: 'body_temperature',
    HealthDataType.RESPIRATORY_RATE: 'respiratory_rate',
    HealthDataType.WEIGHT: 'weight',
  };

  // backend/app/vitals/validation.py's EXPECTED_UNITS — what each reading must be converted to
  // before the backend ever sees it.
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

  /// Prompts the OS-level Health Connect permission dialog for every vital type this adapter
  /// can read. Must succeed (or the user must grant it) before readRecent() can return data —
  /// Health Connect silently returns nothing for ungranted types rather than erroring, so this
  /// is a separate, explicit step rather than folded into readRecent().
  Future<bool> requestPermissions() async {
    await _ensureAvailable();
    return _health.requestAuthorization(_typeMap.keys.toList());
  }

  /// Reads real Health Connect samples from [lookback] ago to now and returns them normalized
  /// into the backend's vocabulary/units. Never fabricates a reading Health Connect didn't
  /// actually provide, and never silently converts a type it doesn't recognise — an unmapped
  /// HealthDataType or non-numeric value is simply skipped, not guessed at.
  Future<List<HealthConnectReading>> readRecent({Duration lookback = const Duration(days: 7)}) async {
    await _ensureAvailable();
    final now = DateTime.now();
    final points = await _health.getHealthDataFromTypes(
      types: _typeMap.keys.toList(),
      startTime: now.subtract(lookback),
      endTime: now,
    );

    final readings = <HealthConnectReading>[];
    for (final point in points) {
      final vitalType = _typeMap[point.type];
      if (vitalType == null) continue;
      final value = point.value;
      if (value is! NumericHealthValue) continue;

      readings.add(
        HealthConnectReading(
          type: vitalType,
          value: convertHealthConnectValue(vitalType, value.numericValue.toDouble()),
          unit: _expectedUnit[vitalType]!,
          timestamp: point.dateFrom,
        ),
      );
    }
    return readings;
  }
}
