import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'package:uuid/uuid.dart';

/// Talks to the MEDAI backend (see backend/app/api/*.py).
class ApiClient {
  ApiClient({String? baseUrl, http.Client? client})
      : baseUrl = baseUrl ?? _activeBaseUrl,
        _httpClient = client ?? http.Client();

  static const String _defaultBaseUrl = 'https://norm-looksmart-toronto-widescreen.trycloudflare.com';
  static String _activeBaseUrl = _defaultBaseUrl;
  static const _serverUrlKey = 'medai_server_url';

  static String get defaultBaseUrl => _defaultBaseUrl;
  static String get activeBaseUrl => _activeBaseUrl;

  final http.Client _httpClient;

  /// Reads any custom server URL saved in secure storage, updating _activeBaseUrl.
  static Future<String> getServerUrl() async {
    const storage = FlutterSecureStorage();
    final saved = await storage.read(key: _serverUrlKey);
    if (saved != null && saved.trim().isNotEmpty) {
      final trimmed = saved.trim().replaceAll(RegExp(r'/+$'), '');
      if (trimmed == 'https://dvds-canvas-testament-milk.trycloudflare.com' ||
          trimmed == 'http://10.0.2.2:8000' ||
          trimmed == 'http://localhost:8000') {
        _activeBaseUrl = _defaultBaseUrl;
        await storage.write(key: _serverUrlKey, value: _defaultBaseUrl);
      } else {
        _activeBaseUrl = trimmed;
      }
    } else {
      _activeBaseUrl = _defaultBaseUrl;
    }
    return _activeBaseUrl;
  }

  /// Sets and persists a new server URL.
  static Future<void> setServerUrl(String url) async {
    var cleaned = url.trim();
    if (cleaned.isNotEmpty && !cleaned.startsWith('http://') && !cleaned.startsWith('https://')) {
      cleaned = 'https://$cleaned';
    }
    cleaned = cleaned.replaceAll(RegExp(r'/+$'), '');
    _activeBaseUrl = cleaned.isNotEmpty ? cleaned : _defaultBaseUrl;
    const storage = FlutterSecureStorage();
    await storage.write(key: _serverUrlKey, value: _activeBaseUrl);
  }

  /// Tests connectivity to the backend health endpoint.
  static Future<bool> testConnection([String? testUrl]) async {
    try {
      var target = (testUrl ?? _activeBaseUrl).trim();
      if (!target.startsWith('http://') && !target.startsWith('https://')) {
        target = 'https://$target';
      }
      target = target.replaceAll(RegExp(r'/+$'), '');
      final response = await http
          .get(Uri.parse('$target/healthz'))
          .timeout(const Duration(seconds: 8));
      return response.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  final String baseUrl;
  final _storage = const FlutterSecureStorage();
  static const _accessTokenKey = 'medai_access_token';
  static const _refreshTokenKey = 'medai_refresh_token';
  static const _deviceEmailKey = 'medai_device_email';

  /// Executes an HTTP request with automatic retry for transient socket drops/resets.
  Future<http.Response> _executeWithRetry(
    Future<http.Response> Function() requestFn, {
    int maxRetries = 3,
  }) async {
    int attempts = 0;
    while (true) {
      attempts++;
      try {
        final response = await requestFn().timeout(const Duration(seconds: 30));
        return response;
      } on SocketException catch (e) {
        if (attempts >= maxRetries) {
          throw ApiException('Network connection failed: $e. Check server connection.');
        }
        await Future.delayed(Duration(milliseconds: 600 * attempts));
      } on TimeoutException {
        if (attempts >= maxRetries) {
          throw ApiException('Server request timed out after 30 seconds.');
        }
        await Future.delayed(Duration(milliseconds: 600 * attempts));
      } on http.ClientException catch (e) {
        if (attempts >= maxRetries) {
          throw ApiException('Connection dropped by server or network ($e).');
        }
        await Future.delayed(Duration(milliseconds: 600 * attempts));
      } catch (e) {
        if (e is ApiException) rethrow;
        if (attempts >= maxRetries) {
          throw ApiException('Unexpected network error: $e');
        }
        await Future.delayed(Duration(milliseconds: 600 * attempts));
      }
    }
  }

  Future<String> _ensureAccessToken() async {
    final existing = await _storage.read(key: _accessTokenKey);
    if (existing != null) return existing;
    return _bootstrapDeviceAccount();
  }

  Future<String> _bootstrapDeviceAccount() async {
    var email = await _storage.read(key: _deviceEmailKey);
    final password = const Uuid().v4();

    if (email == null) {
      email = 'device-${const Uuid().v4()}@example.com';
      await _storage.write(key: _deviceEmailKey, value: email);
    }

    final response = await _executeWithRetry(() => _httpClient.post(
          Uri.parse('$baseUrl/auth/register'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({'email': email, 'password': password}),
        ));

    if (response.statusCode != 201) {
      throw ApiException('failed to bootstrap device account: ${response.statusCode} ${response.body}');
    }

    final tokens = jsonDecode(response.body) as Map<String, dynamic>;
    await _storage.write(key: _accessTokenKey, value: tokens['access_token'] as String);
    await _storage.write(key: _refreshTokenKey, value: tokens['refresh_token'] as String);
    return tokens['access_token'] as String;
  }

  Future<Map<String, String>> _authHeaders() async {
    final token = await _ensureAccessToken();
    return {'Authorization': 'Bearer $token', 'Content-Type': 'application/json'};
  }

  Future<http.Response> _authedGet(Uri uri) async {
    return _executeWithRetry(() async {
      var response = await _httpClient.get(uri, headers: await _authHeaders());
      if (response.statusCode == 401) {
        await _storage.delete(key: _accessTokenKey);
        response = await _httpClient.get(uri, headers: await _authHeaders());
      }
      return response;
    });
  }

  Future<http.Response> _authedPost(Uri uri, {Object? body}) async {
    return _executeWithRetry(() async {
      var response = await _httpClient.post(uri, headers: await _authHeaders(), body: body);
      if (response.statusCode == 401) {
        await _storage.delete(key: _accessTokenKey);
        response = await _httpClient.post(uri, headers: await _authHeaders(), body: body);
      }
      return response;
    });
  }

  Future<http.Response> _authedPatch(Uri uri, {Object? body}) async {
    return _executeWithRetry(() async {
      var response = await _httpClient.patch(uri, headers: await _authHeaders(), body: body);
      if (response.statusCode == 401) {
        await _storage.delete(key: _accessTokenKey);
        response = await _httpClient.patch(uri, headers: await _authHeaders(), body: body);
      }
      return response;
    });
  }

  Future<String> createConversation() async {
    final response = await _authedPost(Uri.parse('$baseUrl/conversations'));
    if (response.statusCode != 201) {
      throw ApiException('failed to create conversation: ${response.statusCode} ${response.body}');
    }
    return (jsonDecode(response.body) as Map<String, dynamic>)['id'] as String;
  }

  Future<List<Map<String, dynamic>>> listConversations() async {
    final response = await _authedGet(Uri.parse('$baseUrl/conversations'));
    if (response.statusCode != 200) {
      throw ApiException('failed to list conversations: ${response.statusCode} ${response.body}');
    }
    return (jsonDecode(response.body) as List).cast<Map<String, dynamic>>();
  }

  Future<List<Map<String, dynamic>>> listMessages({required String conversationId}) async {
    final response = await _authedGet(
      Uri.parse('$baseUrl/messages').replace(queryParameters: {'conversation_id': conversationId}),
    );
    if (response.statusCode != 200) {
      throw ApiException('failed to list messages: ${response.statusCode} ${response.body}');
    }
    return (jsonDecode(response.body) as List).cast<Map<String, dynamic>>();
  }

  Future<SendMessageResult> sendMessage({required String conversationId, required String content}) async {
    final response = await _authedPost(
      Uri.parse('$baseUrl/messages'),
      body: jsonEncode({'conversation_id': conversationId, 'content': content}),
    );
    if (response.statusCode != 201) {
      throw ApiException('failed to send message: ${response.statusCode} ${response.body}');
    }
    final body = jsonDecode(response.body) as Map<String, dynamic>;
    return SendMessageResult(
      content: (body['assistant_message'] as Map<String, dynamic>)['content'] as String,
      isAssessment: body['is_assessment'] as bool? ?? false,
      assessmentStatus: body['assessment_status'] as String?,
      escalation: body['escalation'] as String?,
    );
  }

  Future<String> registerDevice({required String deviceType, String? label}) async {
    final response = await _authedPost(
      Uri.parse('$baseUrl/devices'),
      body: jsonEncode(
        label != null
            ? {'device_type': deviceType, 'label': label}
            : {'device_type': deviceType},
      ),
    );
    if (response.statusCode != 201) {
      throw ApiException('failed to register device: ${response.statusCode} ${response.body}');
    }
    return (jsonDecode(response.body) as Map<String, dynamic>)['id'] as String;
  }

  Future<List<Map<String, dynamic>>> listDevices() async {
    final response = await _authedGet(Uri.parse('$baseUrl/devices'));
    if (response.statusCode != 200) {
      throw ApiException('failed to list devices: ${response.statusCode} ${response.body}');
    }
    return (jsonDecode(response.body) as List).cast<Map<String, dynamic>>();
  }

  /// Sends a batch of already-normalized readings to POST /vitals/sync.
  /// Uses a safe chunk size of 50 to prevent payload timeouts and socket aborts.
  Future<Map<String, dynamic>> syncVitals({
    required String deviceId,
    required List<Map<String, dynamic>> readings,
    void Function(int synced, int total)? onProgress,
  }) async {
    if (readings.isEmpty) {
      return {'synced': 0, 'accepted': 0, 'rejected': 0, 'results': []};
    }

    const chunkSize = 50;
    int totalSynced = 0;
    int totalAccepted = 0;
    int totalRejected = 0;
    final allResults = <Map<String, dynamic>>[];

    for (var i = 0; i < readings.length; i += chunkSize) {
      final chunk = readings.sublist(i, (i + chunkSize).clamp(0, readings.length));
      final response = await _authedPost(
        Uri.parse('$baseUrl/vitals/sync'),
        body: jsonEncode({'device_id': deviceId, 'readings': chunk}),
      );
      if (response.statusCode != 200) {
        throw ApiException('failed to sync vitals chunk: ${response.statusCode} ${response.body}');
      }
      final body = jsonDecode(response.body) as Map<String, dynamic>;
      totalSynced += (body['synced'] as int? ?? 0);
      totalAccepted += (body['accepted'] as int? ?? 0);
      totalRejected += (body['rejected'] as int? ?? 0);
      if (body['results'] is List) {
        allResults.addAll((body['results'] as List).cast<Map<String, dynamic>>());
      }
      onProgress?.call(totalSynced, readings.length);
    }

    return {
      'synced': totalSynced,
      'accepted': totalAccepted,
      'rejected': totalRejected,
      'results': allResults,
    };
  }

  /// Fetches the latest recorded sync timestamp from the server.
  Future<DateTime?> getLatestSyncTimestamp() async {
    try {
      final response = await _authedGet(Uri.parse('$baseUrl/vitals/sync/latest'));
      if (response.statusCode == 200) {
        final body = jsonDecode(response.body) as Map<String, dynamic>;
        final ts = body['latest_timestamp'] as String?;
        if (ts != null) return DateTime.tryParse(ts);
      }
    } catch (_) {}
    return null;
  }

  /// Fetches 24-hour vital trends, min/max/avg, and extreme anomaly alerts.
  Future<Map<String, dynamic>> getVitalTrends({int windowHours = 24}) async {
    final response = await _authedGet(
      Uri.parse('$baseUrl/vitals/trends').replace(queryParameters: {'window_hours': windowHours.toString()}),
    );
    if (response.statusCode != 200) {
      throw ApiException('Failed to fetch vital trends: ${response.statusCode}');
    }
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  Future<Uint8List> fetchAssessmentSpeech({required String conversationId}) async {
    final response = await _authedPost(
      Uri.parse('$baseUrl/assessment/speech'),
      body: jsonEncode({'conversation_id': conversationId}),
    );
    if (response.statusCode != 200) {
      throw ApiException('failed to fetch assessment speech: ${response.statusCode} ${response.body}');
    }
    return response.bodyBytes;
  }

  Future<Map<String, dynamic>> addVital({
    required String type,
    required double value,
    required String unit,
    DateTime? timestamp,
  }) async {
    final response = await _authedPost(
      Uri.parse('$baseUrl/vitals'),
      body: jsonEncode({
        'type': type,
        'value': value,
        'unit': unit,
        if (timestamp != null) 'timestamp': timestamp.toUtc().toIso8601String(),
      }),
    );
    if (response.statusCode != 201) {
      String detail;
      try {
        final decoded = jsonDecode(response.body);
        detail = decoded is Map && decoded.containsKey('detail')
            ? decoded['detail'].toString()
            : response.body;
      } catch (_) {
        detail = response.body;
      }
      throw ApiException('Failed to record vital: $detail');
    }
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  Future<List<Map<String, dynamic>>> getVitals() async {
    final response = await _authedGet(Uri.parse('$baseUrl/vitals'));
    if (response.statusCode != 200) {
      throw ApiException('Failed to fetch vitals: ${response.statusCode}');
    }
    return (jsonDecode(response.body) as List).cast<Map<String, dynamic>>();
  }

  Future<List<Map<String, dynamic>>> getDiagnosticHistory({String? status}) async {
    final uri = Uri.parse('$baseUrl/diagnostics').replace(
      queryParameters: status != null ? {'status': status} : null,
    );
    final response = await _authedGet(uri);
    if (response.statusCode != 200) {
      throw ApiException('Failed to fetch diagnostic history: ${response.statusCode}');
    }
    return (jsonDecode(response.body) as List).cast<Map<String, dynamic>>();
  }

  Future<Map<String, dynamic>> updateDiagnosticStatus({
    required String recordId,
    required String status,
  }) async {
    final response = await _authedPatch(
      Uri.parse('$baseUrl/diagnostics/$recordId/status'),
      body: jsonEncode({'status': status}),
    );
    if (response.statusCode != 200) {
      throw ApiException('Failed to update diagnostic status: ${response.statusCode}');
    }
    return jsonDecode(response.body) as Map<String, dynamic>;
  }
}

class ApiException implements Exception {
  ApiException(this.message);
  final String message;

  @override
  String toString() => 'ApiException: $message';
}

class SendMessageResult {
  SendMessageResult({required this.content, required this.isAssessment, this.assessmentStatus, this.escalation});

  final String content;
  final bool isAssessment;
  final String? assessmentStatus;
  final String? escalation;

  bool get isHighRisk => assessmentStatus == 'urgent' || assessmentStatus == 'emergency';
}
