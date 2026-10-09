import 'package:flutter/material.dart';
import 'package:speech_to_text/speech_to_text.dart' as stt;

import '../services/api_client.dart';
import '../services/speech_playback_service.dart';
import 'manual_vitals_screen.dart';

class ChatEntry {
  ChatEntry({required this.role, required this.text, this.isAssessment = false});
  final String role; // user | assistant
  final String text;

  /// True when this reply is a real validated Assessment (backend/app/api/messages.py sets
  /// `is_assessment` once nothing more is missing to ask) rather than a follow-up question —
  /// the only kind of message this screen offers a Play button for, per voice_pipeline: TTS
  /// must only ever speak text that has passed get_validated_output() on the backend.
  final bool isAssessment;
}

/// The full architecture.canonical_pipeline, driven entirely by this one chat screen
/// (ux.paradigm: "CHAT + VOICE, not FORM + DASHBOARD") — mic/typed input -> STT ->
/// conversation_manager's follow-up questions -> once nothing more is missing, the real
/// EVIDENCE_RETRIEVAL -> CLINICAL_REASONING -> DETERMINISTIC_SAFETY -> OUTPUT_VALIDATION
/// pipeline runs automatically (backend/app/api/messages.py) -> TEXT_RESPONSE, with optional
/// TTS playback for that result (Phase 11/12). There is no separate "get assessment" screen or
/// button — the conversation itself is the interface (ux.user_must_not_be_required_to:
/// manually_construct_a_report).
///
/// Voice is the primary input (ux.voice_first), but per ux.accessibility's
/// text_always_available requirement, typed text is always an equally valid way to send a
/// message — this also makes the flow testable without a live microphone.
class ConversationScreen extends StatefulWidget {
  const ConversationScreen({super.key, this.apiClient, this.speechPlaybackService});

  /// Injectable for widget tests; defaults to a real ApiClient otherwise.
  final ApiClient? apiClient;

  /// Injectable for widget tests; defaults to a real SpeechPlaybackService otherwise.
  final SpeechPlaybackService? speechPlaybackService;

  @override
  State<ConversationScreen> createState() => _ConversationScreenState();
}

class _ConversationScreenState extends State<ConversationScreen> {
  late final ApiClient _apiClient = widget.apiClient ?? ApiClient();
  late final SpeechPlaybackService _playback = widget.speechPlaybackService ?? SpeechPlaybackService();
  final _speech = stt.SpeechToText();
  final _textController = TextEditingController();
  final _messages = <ChatEntry>[];
  final _scrollController = ScrollController();

  String? _conversationId;
  bool _listening = false;
  bool _sending = false;
  bool _speechAvailable = false;
  int? _speakingIndex;
  String? _error;

  @override
  void initState() {
    super.initState();
    _init();
    _playback.onComplete.listen((_) {
      if (mounted) setState(() => _speakingIndex = null);
    });
  }

  Future<void> _init() async {
    try {
      // ux.session_auto_preserved / resume_previous_consultation: resume the most recent
      // conversation rather than always starting a new one, so a patient's consultation
      // survives closing and reopening the app. Historical messages are shown as plain text —
      // whether a past assistant reply was itself a validated Assessment isn't persisted on
      // the Message row (see backend/app/schemas/conversation.py), so Play/high-risk-confirm
      // affordances are only offered for a message received in the current live session.
      final conversations = await _apiClient.listConversations();
      if (conversations.isNotEmpty) {
        _conversationId = conversations.first['id'] as String;
        final history = await _apiClient.listMessages(conversationId: _conversationId!);
        if (mounted) {
          setState(() {
            _messages.addAll(
              history.map((m) => ChatEntry(role: m['role'] as String, text: m['content'] as String)),
            );
          });
        }
        _scrollToBottom();
      } else {
        _conversationId = await _apiClient.createConversation();
      }
    } catch (e) {
      setState(() => _error = 'Could not reach MEDAI backend: $e');
      return;
    }

    // Resumed/created conversation is now usable even if STT setup below hangs or fails —
    // the mic/STT path degrades independently and must never block text availability
    // (ux.accessibility.text_always_available).
    if (mounted) setState(() {});

    try {
      // No platform implementation is registered in a headless/widget-test environment —
      // degrade to "speech unavailable" rather than crashing the whole screen.
      _speechAvailable = await _speech.initialize(
        onError: (error) => setState(() => _error = error.errorMsg),
      );
    } catch (_) {
      _speechAvailable = false;
    }
    if (mounted) setState(() {});
  }

  Future<void> _toggleListening() async {
    // Barge-in (voice_pipeline.rules: "User must be able to interrupt TTS and continue
    // speaking") — any attempt to activate the mic interrupts assessment audio currently
    // playing, unconditionally, before checking whether STT itself is available.
    await _stopSpeaking();

    if (!_speechAvailable) {
      setState(() => _error = 'Speech recognition is not available on this device/browser.');
      return;
    }

    if (_listening) {
      await _speech.stop();
      setState(() => _listening = false);
      return;
    }

    setState(() {
      _listening = true;
      _error = null;
    });
    await _speech.listen(
      onResult: (result) {
        _textController.text = result.recognizedWords;
        if (result.finalResult) {
          setState(() => _listening = false);
        }
      },
    );
  }

  Future<void> _send() async {
    final text = _textController.text.trim();
    if (text.isEmpty || _conversationId == null || _sending) return;

    setState(() {
      _sending = true;
      _error = null;
      _messages.add(ChatEntry(role: 'user', text: text));
    });
    _textController.clear();
    _scrollToBottom();

    SendMessageResult? result;
    try {
      // Phase 12: once nothing more is missing, this reply is a real validated Assessment
      // (architecture.canonical_pipeline run automatically), not a scaffold — the conversation
      // itself is the only interface; there is no separate "get assessment" action to take.
      result = await _apiClient.sendMessage(conversationId: _conversationId!, content: text);
      setState(() => _messages.add(ChatEntry(role: 'assistant', text: result!.content, isAssessment: result.isAssessment)));
    } catch (e) {
      setState(() => _error = 'Could not send message: $e');
    } finally {
      setState(() => _sending = false);
      _scrollToBottom();
    }

    // Shown only once _sending is back to false, so the confirmation dialog is never stuck
    // behind an indefinitely-animating "sending" indicator.
    if (result != null && result.isHighRisk && mounted) {
      await _showHighRiskConfirmation(result.escalation ?? result.content);
    }
  }

  /// ux.confirmation_required_when: high_risk_recommendation_considered. A modal, non-dismiss-
  /// by-tapping-outside dialog — the user must explicitly acknowledge a high-risk result before
  /// continuing, rather than it just scrolling past as an ordinary chat bubble.
  Future<void> _showHighRiskConfirmation(String escalationText) async {
    await showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (context) => AlertDialog(
        icon: const Icon(Icons.warning_amber_rounded),
        title: const Text('Please read carefully'),
        content: Text(escalationText),
        actions: [
          FilledButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('I understand'),
          ),
        ],
      ),
    );
  }

  /// Plays the audio for the assessment message at [index]. Interrupts (barge-in) whatever was
  /// playing before, including a previous tap on this same button — see
  /// SpeechPlaybackService.play(). A fetch/playback failure is shown inline; the message's text
  /// (already on screen) is never affected by it (TTS is optional, never the only channel).
  Future<void> _playAssessment(int index) async {
    setState(() {
      _speakingIndex = index;
      _error = null;
    });
    try {
      final audio = await _apiClient.fetchAssessmentSpeech(conversationId: _conversationId!);
      await _playback.play(audio);
      // _speakingIndex is cleared by the onComplete listener once playback actually finishes,
      // or by _stopSpeaking() on an explicit interrupt — not here, since play() only awaits
      // playback starting, not finishing.
    } catch (e) {
      setState(() {
        _error = 'Could not play audio — text response is still shown above. ($e)';
        _speakingIndex = null;
      });
    }
  }

  Future<void> _stopSpeaking() async {
    await _playback.stop();
    if (mounted) setState(() => _speakingIndex = null);
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 200),
          curve: Curves.easeOut,
        );
      }
    });
  }

  @override
  void dispose() {
    if (_speechAvailable) {
      _speech.stop();
    }
    _playback.stop();
    _textController.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Talk to Health AI'),
        actions: [
          IconButton(
            icon: const Icon(Icons.monitor_heart_outlined),
            tooltip: 'Enter/View Vitals',
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute(
                builder: (_) => ManualVitalsScreen(apiClient: _apiClient),
              ),
            ),
          ),
        ],
      ),
      body: Column(
        children: [
          if (_error != null)
            Container(
              width: double.infinity,
              color: Theme.of(context).colorScheme.errorContainer,
              padding: const EdgeInsets.all(12),
              child: Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.onErrorContainer)),
            ),
          Expanded(
            child: ListView.builder(
              controller: _scrollController,
              padding: const EdgeInsets.all(12),
              itemCount: _messages.length,
              itemBuilder: (context, index) {
                final entry = _messages[index];
                final isUser = entry.role == 'user';
                final isSpeakingThis = _speakingIndex == index;
                return Align(
                  alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
                  child: Container(
                    key: ValueKey('message_$index'),
                    margin: const EdgeInsets.symmetric(vertical: 4),
                    padding: const EdgeInsets.all(12),
                    constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.75),
                    decoration: BoxDecoration(
                      color: isUser
                          ? Theme.of(context).colorScheme.primaryContainer
                          : Theme.of(context).colorScheme.surfaceContainerHighest,
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(entry.text),
                        if (entry.isAssessment) ...[
                          const SizedBox(height: 4),
                          // TTS is optional, never the only channel — the text above is always
                          // shown regardless of whether Play is ever tapped or succeeds.
                          IconButton(
                            key: ValueKey('speak_$index'),
                            tooltip: isSpeakingThis ? 'Stop' : 'Play',
                            icon: Icon(isSpeakingThis ? Icons.stop_circle_outlined : Icons.volume_up_outlined),
                            onPressed: isSpeakingThis ? _stopSpeaking : () => _playAssessment(index),
                          ),
                        ],
                      ],
                    ),
                  ),
                );
              },
            ),
          ),
          SafeArea(
            child: Padding(
              padding: const EdgeInsets.all(8.0),
              child: Row(
                children: [
                  IconButton(
                    tooltip: _listening ? 'Stop listening' : 'Speak',
                    iconSize: 32,
                    icon: Icon(_listening ? Icons.mic : Icons.mic_none),
                    color: _listening ? Theme.of(context).colorScheme.error : null,
                    onPressed: _toggleListening,
                  ),
                  Expanded(
                    child: TextField(
                      controller: _textController,
                      decoration: const InputDecoration(hintText: 'Speak, or type your message'),
                      onSubmitted: (_) => _send(),
                    ),
                  ),
                  IconButton(
                    tooltip: 'Send',
                    icon: _sending
                        ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
                        : const Icon(Icons.send),
                    onPressed: _sending ? null : _send,
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
