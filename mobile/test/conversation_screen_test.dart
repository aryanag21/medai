import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:medai/screens/conversation_screen.dart';
import 'package:medai/services/api_client.dart';
import 'package:medai/services/speech_playback_service.dart';

/// Bypasses real network/secure-storage calls so this test doesn't need a running backend,
/// a real device keystore, or a microphone — exercises the ux.accessibility text_always_available
/// path (typed text), not the voice/STT path, which needs a real device/browser to verify.
class FakeApiClient extends ApiClient {
  final sentMessages = <String>[];

  /// Popped one at a time by sendMessage(); falls back to a plain non-assessment stub reply
  /// once exhausted, matching the ASK_QUESTION path's shape.
  final List<SendMessageResult> queuedReplies = [];

  /// Empty by default — the "no existing conversation" / fresh-start path.
  List<Map<String, dynamic>> conversationsToResume = [];
  List<Map<String, dynamic>> messagesToResume = [];

  Object? assessmentSpeechError;
  bool createConversationCalled = false;

  @override
  Future<String> createConversation() async {
    createConversationCalled = true;
    return 'fake-conversation-id';
  }

  @override
  Future<List<Map<String, dynamic>>> listConversations() async => conversationsToResume;

  @override
  Future<List<Map<String, dynamic>>> listMessages({required String conversationId}) async => messagesToResume;

  @override
  Future<SendMessageResult> sendMessage({required String conversationId, required String content}) async {
    sentMessages.add(content);
    if (queuedReplies.isNotEmpty) return queuedReplies.removeAt(0);
    return SendMessageResult(content: 'stub reply to: $content', isAssessment: false);
  }

  @override
  Future<Uint8List> fetchAssessmentSpeech({required String conversationId}) async {
    if (assessmentSpeechError != null) throw assessmentSpeechError!;
    return Uint8List.fromList([1, 2, 3]);
  }

  final recordedVitals = <Map<String, dynamic>>[];
  List<Map<String, dynamic>> vitalsToReturn = [];

  @override
  Future<Map<String, dynamic>> addVital({
    required String type,
    required double value,
    required String unit,
    DateTime? timestamp,
  }) async {
    final entry = {
      'type': type,
      'value': value,
      'unit': unit,
      'timestamp': (timestamp ?? DateTime.now()).toIso8601String(),
    };
    recordedVitals.add(entry);
    return entry;
  }

  @override
  Future<List<Map<String, dynamic>>> getVitals() async => vitalsToReturn;
}

class FakeAudioBackend implements AudioBackend {
  final calls = <String>[];
  final _completeController = StreamController<void>.broadcast();
  bool shouldThrowOnPlay = false;

  @override
  bool get isPlaying => calls.isNotEmpty && calls.last == 'play';

  @override
  Future<void> play(Uint8List bytes) async {
    calls.add('play');
    if (shouldThrowOnPlay) throw Exception('simulated playback failure');
  }

  @override
  Future<void> stop() async => calls.add('stop');

  @override
  Stream<void> get onComplete => _completeController.stream;
}

Future<void> _sendText(WidgetTester tester, String text) async {
  await tester.enterText(find.byType(TextField), text);
  await tester.tap(find.byTooltip('Send'));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('typing and sending a message shows both bubbles', (WidgetTester tester) async {
    final fakeApi = FakeApiClient();

    await tester.pumpWidget(MaterialApp(home: ConversationScreen(apiClient: fakeApi)));
    await tester.pumpAndSettle();

    await _sendText(tester, 'I have a headache');

    expect(fakeApi.sentMessages, ['I have a headache']);
    expect(find.text('I have a headache'), findsOneWidget);
    expect(find.text('stub reply to: I have a headache'), findsOneWidget);
  });

  testWidgets('a new conversation is created when none exists to resume', (WidgetTester tester) async {
    final fakeApi = FakeApiClient()..conversationsToResume = [];

    await tester.pumpWidget(MaterialApp(home: ConversationScreen(apiClient: fakeApi)));
    await tester.pumpAndSettle();

    expect(fakeApi.createConversationCalled, isTrue);
    expect(find.byKey(const ValueKey('message_0')), findsNothing); // nothing to show yet — empty history
  });

  testWidgets('ux.session_auto_preserved: an existing conversation is resumed with its history, not recreated',
      (WidgetTester tester) async {
    final fakeApi = FakeApiClient()
      ..conversationsToResume = [
        {'id': 'resumed-conversation-id', 'created_at': '2026-01-01T00:00:00Z', 'updated_at': '2026-01-01T00:00:00Z'},
      ]
      ..messagesToResume = [
        {'id': 'm1', 'role': 'user', 'content': 'I have a headache'},
        {'id': 'm2', 'role': 'assistant', 'content': 'Could you tell me your age?'},
      ];

    await tester.pumpWidget(MaterialApp(home: ConversationScreen(apiClient: fakeApi)));
    await tester.pumpAndSettle();

    expect(fakeApi.createConversationCalled, isFalse);
    expect(find.text('I have a headache'), findsOneWidget);
    expect(find.text('Could you tell me your age?'), findsOneWidget);

    // Resuming and immediately sending uses the resumed conversation, not a new one.
    await _sendText(tester, 'I am 40');
    expect(fakeApi.sentMessages, ['I am 40']);
  });

  testWidgets('a validated Assessment reply is shown as text with a Play control, automatically',
      (WidgetTester tester) async {
    final fakeApi = FakeApiClient()
      ..queuedReplies.add(SendMessageResult(
        content: 'Possible explanations include a tension headache.',
        isAssessment: true,
        assessmentStatus: 'caution',
      ));
    final backend = FakeAudioBackend();

    await tester.pumpWidget(MaterialApp(
      home: ConversationScreen(apiClient: fakeApi, speechPlaybackService: SpeechPlaybackService(backend: backend)),
    ));
    await tester.pumpAndSettle();

    await _sendText(tester, 'just checking in');

    // No separate "get assessment" action exists — the conversation itself produced this.
    expect(find.text('Possible explanations include a tension headache.'), findsOneWidget);
    expect(backend.calls, isEmpty);
    expect(find.byTooltip('Play'), findsOneWidget);
  });

  testWidgets('tapping Play fetches and plays audio, tapping again (or Stop) interrupts it',
      (WidgetTester tester) async {
    final fakeApi = FakeApiClient()
      ..queuedReplies.add(SendMessageResult(content: 'Possible tension headache.', isAssessment: true));
    final backend = FakeAudioBackend();

    await tester.pumpWidget(MaterialApp(
      home: ConversationScreen(apiClient: fakeApi, speechPlaybackService: SpeechPlaybackService(backend: backend)),
    ));
    await tester.pumpAndSettle();
    await _sendText(tester, 'just checking in');

    await tester.tap(find.byTooltip('Play'));
    await tester.pumpAndSettle();

    expect(backend.calls, ['stop', 'play']); // SpeechPlaybackService.play() always stops first
    expect(find.byTooltip('Stop'), findsOneWidget);

    await tester.tap(find.byTooltip('Stop'));
    await tester.pumpAndSettle();

    expect(backend.calls, ['stop', 'play', 'stop']);
    expect(find.byTooltip('Play'), findsOneWidget);
  });

  testWidgets('a TTS fetch failure is shown inline without removing the already-shown text',
      (WidgetTester tester) async {
    final fakeApi = FakeApiClient()
      ..queuedReplies.add(SendMessageResult(content: 'Possible tension headache.', isAssessment: true))
      ..assessmentSpeechError = Exception('TTS_ERROR: engine unavailable');
    final backend = FakeAudioBackend();

    await tester.pumpWidget(MaterialApp(
      home: ConversationScreen(apiClient: fakeApi, speechPlaybackService: SpeechPlaybackService(backend: backend)),
    ));
    await tester.pumpAndSettle();
    await _sendText(tester, 'just checking in');

    await tester.tap(find.byTooltip('Play'));
    await tester.pumpAndSettle();

    // The validated text response is untouched by the playback failure.
    expect(find.text('Possible tension headache.'), findsOneWidget);
    expect(find.textContaining('Could not play audio'), findsOneWidget);
    expect(backend.calls, isEmpty); // fetch failed before any backend playback call
  });

  testWidgets('activating the microphone interrupts any assessment audio currently playing (barge-in)',
      (WidgetTester tester) async {
    final fakeApi = FakeApiClient()
      ..queuedReplies.add(SendMessageResult(content: 'Possible tension headache.', isAssessment: true));
    final backend = FakeAudioBackend();

    await tester.pumpWidget(MaterialApp(
      home: ConversationScreen(apiClient: fakeApi, speechPlaybackService: SpeechPlaybackService(backend: backend)),
    ));
    await tester.pumpAndSettle();
    await _sendText(tester, 'just checking in');
    await tester.tap(find.byTooltip('Play'));
    await tester.pumpAndSettle();
    expect(backend.calls, ['stop', 'play']);

    // Speech recognition isn't available in this widget-test environment (no platform
    // implementation registered), but _toggleListening() stops playback unconditionally,
    // before checking availability — exactly so barge-in works even on the very first tap.
    await tester.tap(find.byTooltip('Speak'));
    await tester.pumpAndSettle();

    expect(backend.calls, ['stop', 'play', 'stop']);
  });

  testWidgets('ux.confirmation_required_when high_risk_recommendation_considered: a modal '
      'confirmation is shown for an emergency/urgent assessment and must be acknowledged',
      (WidgetTester tester) async {
    final fakeApi = FakeApiClient()
      ..queuedReplies.add(SendMessageResult(
        content: 'This requires urgent attention.\n\nSeek emergency care immediately.',
        isAssessment: true,
        assessmentStatus: 'emergency',
        escalation: 'Seek emergency care immediately.',
      ));

    await tester.pumpWidget(MaterialApp(home: ConversationScreen(apiClient: fakeApi)));
    await tester.pumpAndSettle();

    await _sendText(tester, 'I have severe chest pain');

    expect(find.text('Seek emergency care immediately.'), findsWidgets);
    expect(find.text('I understand'), findsOneWidget);

    // Tapping outside must not dismiss it (barrierDismissible: false) — it requires the
    // explicit acknowledgment button.
    await tester.tapAt(const Offset(10, 10));
    await tester.pumpAndSettle();
    expect(find.text('I understand'), findsOneWidget);

    await tester.tap(find.text('I understand'));
    await tester.pumpAndSettle();
    expect(find.text('I understand'), findsNothing);
  });

  testWidgets('a non-high-risk assessment shows no confirmation dialog', (WidgetTester tester) async {
    final fakeApi = FakeApiClient()
      ..queuedReplies.add(SendMessageResult(
        content: 'Possible explanations include a tension headache.',
        isAssessment: true,
        assessmentStatus: 'caution',
      ));

    await tester.pumpWidget(MaterialApp(home: ConversationScreen(apiClient: fakeApi)));
    await tester.pumpAndSettle();

    await _sendText(tester, 'just checking in');

    expect(find.text('I understand'), findsNothing);
  });
}
