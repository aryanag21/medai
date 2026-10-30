import 'dart:convert';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'api_client.dart';
import 'health_connect_adapter.dart';

/// Result of one sync attempt — surfaced to the UI so a rejected reading is never silently
/// dropped from what the user sees, matching vital_system's "never silently drop" rule.
class HealthSyncResult {
  HealthSyncResult({
    required this.synced,
    required this.accepted,
    required this.rejected,
    this.message,
  });
  final int synced;
  final int accepted;
  final int rejected;
  final String? message;
}

/// Ties HealthConnectAdapter (reads real Health Connect data) to ApiClient (POSTs it to the
/// backend's VITAL_SERVICE at /vitals/sync). Automatically tracks the last synced timestamp
/// so incremental syncs only fetch new data since the previous sync, preventing redundant
/// re-processing of thousands of historical records from companion apps like Nothing X.
class HealthSyncService {
  HealthSyncService({ApiClient? apiClient, HealthConnectAdapter? adapter})
      : _apiClient = apiClient ?? ApiClient(),
        _adapter = adapter ?? HealthConnectAdapter();

  final ApiClient _apiClient;
  final HealthConnectAdapter _adapter;
  final _storage = const FlutterSecureStorage();

  static const _deviceIdKey = 'medai_health_connect_device_id';
  static const _lastSyncTimeKey = 'medai_health_connect_last_sync_time';
  static const _cachedTrendsKey = 'medai_cached_vitals_trends';
  static const _localSamplesKey = 'medai_local_vitals_samples';

  Future<String> _ensureDeviceId() async {
    final existing = await _storage.read(key: _deviceIdKey);
    if (existing != null) return existing;

    final deviceId = await _apiClient.registerDevice(deviceType: 'health_connect', label: 'Health Connect');
    await _storage.write(key: _deviceIdKey, value: deviceId);
    return deviceId;
  }

  /// Requests Health Connect permissions for every vital type this adapter reads.
  Future<bool> requestPermissions() async {
    try {
      return await _adapter.requestPermissions();
    } on HealthConnectUnavailable {
      return false;
    }
  }

  /// Returns the last synced timestamp from local storage or backend.
  Future<DateTime?> getLastSyncTime() async {
    final stored = await _storage.read(key: _lastSyncTimeKey);
    if (stored != null) {
      final parsed = DateTime.tryParse(stored);
      if (parsed != null) return parsed;
    }
    // Fall back to querying the backend for the patient's latest recorded timestamp
    try {
      final remoteLatest = await _apiClient.getLatestSyncTimestamp();
      if (remoteLatest != null) {
        await _storage.write(key: _lastSyncTimeKey, value: remoteLatest.toIso8601String());
      }
      return remoteLatest;
    } catch (_) {
      return null;
    }
  }

  /// Reads new Health Connect data since the last sync timestamp (or default 24h lookback for first sync)
  /// and syncs it incrementally to the backend. Chunks large batches and checkpoints progress after each chunk.
  Future<HealthSyncResult> sync({
    Duration defaultLookback = const Duration(hours: 24),
    void Function(String message)? onProgress,
  }) async {
    final now = DateTime.now();
    final lastSync = await getLastSyncTime();

    DateTime startTime;
    if (lastSync == null) {
      startTime = now.subtract(defaultLookback);
    } else {
      // Start 1 millisecond after the last recorded sample to avoid duplicating the exact timestamp
      startTime = lastSync.add(const Duration(milliseconds: 1));
      // Cap at 48 hours lookback to prevent enormous payloads if device was offline for weeks
      final maxLookback = now.subtract(const Duration(hours: 48));
      if (startTime.isBefore(maxLookback)) {
        startTime = maxLookback;
      }
    }

    if (!startTime.isBefore(now)) {
      return HealthSyncResult(
        synced: 0,
        accepted: 0,
        rejected: 0,
        message: 'Already up to date.',
      );
    }

    onProgress?.call('Reading Health Connect wearable data...');
    final readings = await _adapter.readRange(startTime: startTime, endTime: now);
    if (readings.isEmpty) {
      await _storage.write(key: _lastSyncTimeKey, value: now.toIso8601String());
      return HealthSyncResult(
        synced: 0,
        accepted: 0,
        rejected: 0,
        message: 'No new wearable data found in this timeframe.',
      );
    }

    // Persist new readings to local rolling buffer and compute local trends immediately
    await _appendLocalSamples(readings);
    await _computeAndStoreLocalTrends();

    final deviceId = await _ensureDeviceId();
    const chunkSize = 50;
    int totalSynced = 0;
    int totalAccepted = 0;
    int totalRejected = 0;
    final totalBatches = (readings.length / chunkSize).ceil();

    for (var i = 0; i < readings.length; i += chunkSize) {
      final currentBatch = (i ~/ chunkSize) + 1;
      onProgress?.call('Syncing batch $currentBatch of $totalBatches (${readings.length} readings)...');
      final chunk = readings.sublist(i, (i + chunkSize).clamp(0, readings.length));

      try {
        final response = await _apiClient.syncVitals(
          deviceId: deviceId,
          readings: [for (final r in chunk) r.toJson()],
        );

        totalSynced += (response['synced'] as int? ?? 0);
        totalAccepted += (response['accepted'] as int? ?? 0);
        totalRejected += (response['rejected'] as int? ?? 0);

        // Immediate checkpoint: save the highest timestamp of this completed chunk!
        final chunkLatest = chunk.map((r) => r.timestamp).reduce((a, b) => a.isAfter(b) ? a : b);
        await _storage.write(key: _lastSyncTimeKey, value: chunkLatest.toIso8601String());
      } catch (e) {
        // If a batch fails midway, checkpoint has already preserved earlier chunks!
        if (totalSynced > 0) {
          return HealthSyncResult(
            synced: totalSynced,
            accepted: totalAccepted,
            rejected: totalRejected,
            message: 'Partially synced $totalSynced readings. Connection interrupted ($e). Tap to resume.',
          );
        }
        rethrow;
      }
    }

    // Attempt to refresh server-side trends
    try {
      final trends = await _apiClient.getVitalTrends(windowHours: 24);
      await _storage.write(key: _cachedTrendsKey, value: jsonEncode(trends));
    } catch (_) {}

    return HealthSyncResult(
      synced: totalSynced,
      accepted: totalAccepted,
      rejected: totalRejected,
      message: 'Successfully synced $totalSynced reading(s) from Health Connect.',
    );
  }

  /// Appends new readings into a local rolling buffer of recent measurements.
  Future<void> _appendLocalSamples(List<HealthConnectReading> newSamples) async {
    try {
      final existingRaw = await _storage.read(key: _localSamplesKey);
      List<Map<String, dynamic>> list = [];
      if (existingRaw != null) {
        try {
          list = (jsonDecode(existingRaw) as List).cast<Map<String, dynamic>>();
        } catch (_) {}
      }

      for (final r in newSamples) {
        list.add(r.toJson());
      }

      // Keep most recent 500 samples locally to avoid unbounded growth
      if (list.length > 500) {
        list = list.sublist(list.length - 500);
      }

      await _storage.write(key: _localSamplesKey, value: jsonEncode(list));
    } catch (_) {}
  }

  /// Computes offline health trends, statistical min/max/average, and extreme clinical suggestions
  /// directly from locally stored readings.
  Future<Map<String, dynamic>> _computeAndStoreLocalTrends() async {
    try {
      final existingRaw = await _storage.read(key: _localSamplesKey);
      if (existingRaw == null) return {};

      final list = (jsonDecode(existingRaw) as List).cast<Map<String, dynamic>>();
      final readings = list.map((json) => HealthConnectReading.fromJson(json)).toList();

      final trends = calculateLocalTrends(readings);
      await _storage.write(key: _cachedTrendsKey, value: jsonEncode(trends));
      return trends;
    } catch (_) {
      return {};
    }
  }

  /// Retrieves locally cached vital trends for instant offline display and analysis.
  Future<Map<String, dynamic>?> getLocalTrends() async {
    final cached = await _storage.read(key: _cachedTrendsKey);
    if (cached != null) {
      try {
        return jsonDecode(cached) as Map<String, dynamic>;
      } catch (_) {}
    }
    // If not cached, attempt to compute from local samples
    final localComputed = await _computeAndStoreLocalTrends();
    if (localComputed.isNotEmpty) {
      return localComputed;
    }
    // Try fetching from backend
    try {
      final remote = await _apiClient.getVitalTrends(windowHours: 24);
      await _storage.write(key: _cachedTrendsKey, value: jsonEncode(remote));
      return remote;
    } catch (_) {
      return null;
    }
  }
}

/// Standalone, pure Dart calculation of vital statistics, anomalies, and clinical recommendations
Map<String, dynamic> calculateLocalTrends(List<HealthConnectReading> readings) {
  if (readings.isEmpty) return {};

  final byType = <String, List<HealthConnectReading>>{};
  for (final r in readings) {
    byType.putIfAbsent(r.type, () => []).add(r);
  }

  final trendsData = <String, dynamic>{};
  final extremeAlerts = <String>[];
  final recommendations = <String>[];

  for (final entry in byType.entries) {
    final type = entry.key;
    final samples = entry.value;
    if (samples.isEmpty) continue;

    final values = samples.map((s) => s.value).toList();
    final unit = samples.last.unit;
    final curVal = samples.last.value;
    final minVal = values.reduce((a, b) => a < b ? a : b);
    final maxVal = values.reduce((a, b) => a > b ? a : b);
    final avgVal = ((values.reduce((a, b) => a + b) / values.length) * 10).round() / 10.0;

    String direction = 'stable';
    if (values.length >= 4) {
      final mid = values.length ~/ 2;
      final firstAvg = values.sublist(0, mid).reduce((a, b) => a + b) / mid;
      final secondAvg = values.sublist(mid).reduce((a, b) => a + b) / (values.length - mid);
      final delta = secondAvg - firstAvg;
      if (delta.abs() >= (0.05 * avgVal)) {
        direction = delta > 0 ? 'rising' : 'falling';
      }
    }

    trendsData[type] = {
      'current': curVal,
      'min': minVal,
      'max': maxVal,
      'avg': avgVal,
      'unit': unit,
      'sample_count': samples.length,
      'trend_direction': direction,
      'latest_timestamp': samples.last.timestamp.toIso8601String(),
    };

    // Extreme anomaly checks and clinical suggestions
    if (type == 'heart_rate') {
      if (maxVal >= 130) {
        extremeAlerts.add('Heart rate spiked to an extreme ${maxVal.toInt()} bpm (average is ${avgVal.toInt()} bpm).');
        recommendations.add(
            'High heart rate spike detected (>130 bpm). Sit, hydrate, and practice deep breathing. Seek emergency care if accompanied by chest pain or shortness of breath.');
      } else if (maxVal >= 100) {
        extremeAlerts.add('Elevated heart rate noticed (peak ${maxVal.toInt()} bpm, average ${avgVal.toInt()} bpm).');
      }
      if (minVal <= 45) {
        extremeAlerts.add('Heart rate dropped to very low ${minVal.toInt()} bpm.');
        recommendations.add(
            'Low heart rate detected (<45 bpm). If you feel lightheaded, fatigued, or faint, sit down and consult a physician.');
      }
    } else if (type == 'oxygen_saturation') {
      if (minVal < 90) {
        extremeAlerts.add('CRITICAL: Blood oxygen dropped to ${minVal.toInt()}% (normal is 95–100%).');
        recommendations.add(
            'Severe oxygen drop detected (<90%). Sit upright immediately, avoid exertion, and seek prompt in-person medical evaluation.');
      } else if (minVal < 95) {
        extremeAlerts.add('Mild oxygen saturation drop noticed (${minVal.toInt()}%).');
      }
    } else if (type == 'blood_pressure_systolic') {
      if (maxVal >= 180) {
        extremeAlerts.add('CRITICAL: Systolic blood pressure reached ${maxVal.toInt()} mmHg.');
        recommendations.add(
            'Hypertensive crisis level detected (>180 mmHg). Rest quietly for 5 minutes and re-check. If still elevated, seek immediate medical attention.');
      } else if (maxVal >= 140) {
        extremeAlerts.add('Stage 2 hypertension noticed (peak systolic ${maxVal.toInt()} mmHg).');
      }
    } else if (type == 'body_temperature') {
      if (maxVal >= 102.5) {
        extremeAlerts.add('High fever detected (peak ${maxVal.toStringAsFixed(1)}°F).');
        recommendations.add(
            'High fever detected (>102.5°F). Keep hydrated with electrolytes and rest. Seek medical care if lasting more than 48 hours.');
      } else if (maxVal >= 100.4) {
        extremeAlerts.add('Low-grade fever recorded (${maxVal.toStringAsFixed(1)}°F).');
      }
    }
  }

  return {
    'trends': trendsData,
    'extreme_alerts': extremeAlerts,
    'recommendations': recommendations,
    'window_hours': 24,
  };
}
