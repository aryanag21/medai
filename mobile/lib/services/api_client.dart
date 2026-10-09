import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'package:uuid/uuid.dart';

/// Talks to the MEDAI backend (see backend/app/api/*.py).
///
/// Phase 1's real auth/onboarding screens aren't built yet (see mobile/README.md), so this
/// bootstraps a per-device account automatically on first use: no login form, no typed
/// credentials — consistent with ux.user_must_not_be_required_to. This is temporary scaffolding
/// for testing the Phase 2 conversation pipeline, not a real authentication UX; it is replaced
/// once Phase 1's mobile screens (consent, onboarding, login) are built.
class ApiClient {
  ApiClient({String? baseUrl}) : baseUrl = baseUrl ?? _activeBaseUrl;

  static const String _defaultBaseUrl = 'https://cia-kathy-travelers-distributor.trycloudflare.com';
  static String _activeBaseUrl = _defaultBaseUrl;
  static const _serverUrlKey = 'medai_server_url';

  static String get defaultBaseUrl => _defaultBaseUrl;
  static String get activeBaseUrl => _activeBaseUrl;

  /// Reads any custom server URL saved in secure storage, updating _activeBaseUrl.
  static Future<String> getServerUrl() async {
    const storage = FlutterSecureStorage();
    final saved = await storage.read(key: _serverUrlKey);
    if (saved != null && saved.trim().isNotEmpty) {
      final trimmed = saved.trim().replaceAll(RegExp(r'/+$'), '');
      // If the saved URL is a stale tunnel URL or emulator default, reset to the current active default.
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
          .timeout(const Duration(seconds: 6));
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

  Future<String> _ensureAccessToken() async {
    final existing = await _storage.read(key: _accessTokenKey);
    if (existing != null) return existing;
    return _bootstrapDeviceAccount();
  }

  Future<String> _bootstrapDeviceAccount() async {
    var email = await _storage.read(key: _deviceEmailKey);
    final password = const Uuid().v4();

    if (email == null) {
      // example.com is IANA-reserved for documentation/testing use, unlike .local (a reserved
      // mDNS TLD the backend's EmailStr validator rejects outright, found live on Android:
      // "The part after the @-sign is a special-use or reserved name").
      email = 'device-${const Uuid().v4()}@example.com';
      await _storage.write(key: _deviceEmailKey, value: email);
    }

    final response = await http.post(
      Uri.parse('$baseUrl/auth/register'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'email': email, 'password': password}),
    );

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
    var response = await http.get(uri, headers: await _authHeaders());
    if (response.statusCode == 401) {
      await _storage.delete(key: _accessTokenKey);
      response = await http.get(uri, headers: await _authHeaders());
    }
    return response;
  }

  Future<http.Response> _authedPost(Uri uri, {Object? body}) async {
    var response = await http.post(uri, headers: await _authHeaders(), body: body);
    if (response.statusCode == 401) {
      await _storage.delete(key: _accessTokenKey);
      response = await http.post(uri, headers: await _authHeaders(), body: body);
    }
    return response;
  }

  Future<String> createConversation() async {
    final response = await _authedPost(Uri.parse('$baseUrl/conversations'));
    if (response.statusCode != 201) {
      throw ApiException('failed to create conversation: ${response.statusCode} ${response.body}');
    }
    return (jsonDecode(response.body) as Map<String, dynamic>)['id'] as String;
  }

  /// ux.session_auto_preserved / resume_previous_consultation — ordered newest-first by the
  /// backend (backend/app/api/conversations.py), so `.first` is "the conversation to resume".
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

  /// Sends [content] as a user message. As of Phase 12, the reply is either the next
  /// follow-up question, or — once nothing more is missing — a real validated Assessment
  /// (architecture.canonical_pipeline's EVIDENCE_RETRIEVAL through OUTPUT_VALIDATION, run
  /// automatically; see backend/app/api/messages.py). [SendMessageResult.isAssessment]
  /// distinguishes the two so the UI can offer TTS playback and, for a high-risk result, a
  /// confirmation prompt (ux.confirmation_required_when: high_risk_recommendation_considered).
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

  /// Registers a device (see backend/app/api/devices.py) and returns its id. Used by
  /// HealthSyncService to get a device_id to sync Health Connect readings under.
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

  /// Sends a batch of already-normalized readings to POST /vitals/sync
  /// (backend/app/api/vitals.py) and returns the per-reading accept/reject outcome.
  Future<Map<String, dynamic>> syncVitals({
    required String deviceId,
    required List<Map<String, dynamic>> readings,
  }) async {
    final response = await _authedPost(
      Uri.parse('$baseUrl/vitals/sync'),
      body: jsonEncode({'device_id': deviceId, 'readings': readings}),
    );
    if (response.statusCode != 200) {
      throw ApiException('failed to sync vitals: ${response.statusCode} ${response.body}');
    }
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  /// Re-runs the same validated pipeline as [sendMessage]'s assessment turn and returns WAV
  /// audio of its result, from POST /assessment/speech (backend/app/api/assessment.py) —
  /// Phase 11's voice_pipeline VALIDATED_TEXT -> TTS step. A genuinely optional companion:
  /// a 503 here (TTS engine unavailable) must never prevent the text response — already shown
  /// via [sendMessage] — from being available.
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

  /// Records a manual vital measurement directly to POST /vitals (backend/app/api/vitals.py).
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

  /// Fetches current patient vitals from GET /vitals (backend/app/api/vitals.py).
  Future<List<Map<String, dynamic>>> getVitals() async {
    final response = await _authedGet(Uri.parse('$baseUrl/vitals'));
    if (response.statusCode != 200) {
      throw ApiException('Failed to fetch vitals: ${response.statusCode}');
    }
    return (jsonDecode(response.body) as List).cast<Map<String, dynamic>>();
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

  /// ux.confirmation_required_when: high_risk_recommendation_considered.
  bool get isHighRisk => assessmentStatus == 'urgent' || assessmentStatus == 'emergency';
}
