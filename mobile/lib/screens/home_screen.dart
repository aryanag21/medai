import 'dart:io' show Platform;

import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';

import '../services/api_client.dart';
import '../services/health_connect_adapter.dart';
import '../services/health_sync_service.dart';
import 'conversation_screen.dart';

/// Entry screen. Screens for onboarding/consent/profile/history/allergies/medications
/// (Phase 1) and the rest of ux.screens are added incrementally under lib/screens/ as their
/// backing APIs land — see IMPLEMENTATION_PLAN.md.
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final _healthSyncService = HealthSyncService();
  bool _syncing = false;

  bool get _healthConnectSupported => !kIsWeb && Platform.isAndroid;

  @override
  void initState() {
    super.initState();
    ApiClient.getServerUrl();
  }

  Future<void> _showServerUrlDialog() async {
    final currentUrl = await ApiClient.getServerUrl();
    final controller = TextEditingController(text: currentUrl);

    if (!mounted) return;
    await showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Backend Server URL'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Enter your Cloudflare tunnel or backend URL (e.g. https://...trycloudflare.com):',
              style: TextStyle(fontSize: 13),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: controller,
              decoration: const InputDecoration(
                border: OutlineInputBorder(),
                hintText: 'https://...',
                labelText: 'Server URL',
              ),
              keyboardType: TextInputType.url,
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () async {
              final newUrl = controller.text.trim();
              if (newUrl.isNotEmpty) {
                await ApiClient.setServerUrl(newUrl);
                if (mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    SnackBar(content: Text('Server URL set to: $newUrl')),
                  );
                }
              }
              if (ctx.mounted) Navigator.of(ctx).pop();
            },
            child: const Text('Save'),
          ),
        ],
      ),
    );
  }

  Future<void> _syncHealthData() async {
    setState(() => _syncing = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      final granted = await _healthSyncService.requestPermissions();
      if (!granted) {
        messenger.showSnackBar(
          const SnackBar(content: Text('Health Connect permission denied — enter vitals manually instead.')),
        );
        return;
      }
      final result = await _healthSyncService.sync();
      messenger.showSnackBar(
        SnackBar(
          content: Text('Synced ${result.synced} reading(s): ${result.accepted} accepted, ${result.rejected} rejected.'),
        ),
      );
    } on HealthConnectUnavailable catch (e) {
      messenger.showSnackBar(SnackBar(content: Text('Health Connect unavailable (${e.reason}) — enter vitals manually.')));
    } finally {
      if (mounted) setState(() => _syncing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('MEDAI'),
        actions: [
          IconButton(
            icon: const Icon(Icons.settings),
            tooltip: 'Server Settings',
            onPressed: _showServerUrlDialog,
          ),
        ],
      ),
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(24.0),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Text(
                'MEDAI — Multimodal AI Health Assessment & Clinical Decision Support System\n\n'
                'This is a prototype. It is not a diagnostic system, a prescription service, '
                'or an emergency service.',
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 24),
              // ux.primary_action
              FilledButton.icon(
                icon: const Icon(Icons.mic),
                label: const Text('Talk to Health AI'),
                onPressed: () => Navigator.of(context).push(
                  MaterialPageRoute(builder: (_) => const ConversationScreen()),
                ),
              ),
              if (_healthConnectSupported) ...[
                const SizedBox(height: 12),
                OutlinedButton.icon(
                  icon: _syncing
                      ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.sync),
                  label: const Text('Sync Health Connect data'),
                  onPressed: _syncing ? null : _syncHealthData,
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
