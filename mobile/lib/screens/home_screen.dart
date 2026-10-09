import 'dart:io' show Platform;

import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';

import '../services/api_client.dart';
import '../services/health_connect_adapter.dart';
import '../services/health_sync_service.dart';
import 'conversation_screen.dart';
import 'manual_vitals_screen.dart';

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
  bool? _serverOnline;

  bool get _healthConnectSupported => !kIsWeb && Platform.isAndroid;

  @override
  void initState() {
    super.initState();
    _initConnection();
  }

  Future<void> _initConnection() async {
    await ApiClient.getServerUrl();
    await _checkConnection();
  }

  Future<void> _checkConnection() async {
    final online = await ApiClient.testConnection();
    if (mounted) {
      setState(() => _serverOnline = online);
    }
  }

  void _openManualVitals() {
    Navigator.of(context).push(
      MaterialPageRoute(builder: (_) => const ManualVitalsScreen()),
    );
  }

  Future<void> _promptManualVitalsFallback(String reason) async {
    if (!mounted) return;
    await showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Vitals Connection Issue'),
        content: Text(
          '$reason\n\nWould you like to enter your vital signs manually instead?',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('Later'),
          ),
          FilledButton.icon(
            icon: const Icon(Icons.edit_note),
            label: const Text('Enter Vitals Manually'),
            onPressed: () {
              Navigator.of(ctx).pop();
              _openManualVitals();
            },
          ),
        ],
      ),
    );
  }

  Future<void> _showServerUrlDialog() async {
    final currentUrl = await ApiClient.getServerUrl();
    final controller = TextEditingController(text: currentUrl);
    bool testing = false;
    String? testResult;
    bool? testSuccess;

    if (!mounted) return;
    await showDialog(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (context, setDialogState) => AlertDialog(
          title: const Row(
            children: [
              Icon(Icons.cloud_sync, size: 24),
              SizedBox(width: 8),
              Text('Server Connection'),
            ],
          ),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'Connected Cloudflare tunnel / backend server URL:',
                  style: TextStyle(fontSize: 13),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: controller,
                  decoration: const InputDecoration(
                    border: OutlineInputBorder(),
                    hintText: 'https://...trycloudflare.com',
                    labelText: 'Server URL',
                  ),
                  keyboardType: TextInputType.url,
                ),
                const SizedBox(height: 12),
                Row(
                  children: [
                    OutlinedButton.icon(
                      icon: testing
                          ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2))
                          : const Icon(Icons.network_check, size: 18),
                      label: const Text('Test Connection'),
                      onPressed: testing
                          ? null
                          : () async {
                              setDialogState(() {
                                testing = true;
                                testResult = null;
                                testSuccess = null;
                              });
                              final target = controller.text.trim();
                              final ok = await ApiClient.testConnection(target.isNotEmpty ? target : null);
                              setDialogState(() {
                                testing = false;
                                testSuccess = ok;
                                testResult = ok ? 'Connected successfully! (status: 200 OK)' : 'Failed to reach server. Check URL.';
                              });
                            },
                    ),
                    const Spacer(),
                    TextButton(
                      onPressed: () {
                        controller.text = ApiClient.defaultBaseUrl;
                        setDialogState(() {
                          testResult = null;
                          testSuccess = null;
                        });
                      },
                      child: const Text('Reset Default'),
                    ),
                  ],
                ),
                if (testResult != null) ...[
                  const SizedBox(height: 8),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                    decoration: BoxDecoration(
                      color: testSuccess == true ? Colors.green.withAlpha(30) : Colors.red.withAlpha(30),
                      borderRadius: BorderRadius.circular(6),
                    ),
                    child: Row(
                      children: [
                        Icon(
                          testSuccess == true ? Icons.check_circle : Icons.error_outline,
                          size: 16,
                          color: testSuccess == true ? Colors.green : Colors.red,
                        ),
                        const SizedBox(width: 6),
                        Expanded(
                          child: Text(
                            testResult!,
                            style: TextStyle(
                              fontSize: 12,
                              color: testSuccess == true ? Colors.green.shade800 : Colors.red.shade800,
                              fontWeight: FontWeight.w500,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ],
            ),
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
                  await _checkConnection();
                  if (mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      SnackBar(content: Text('Server URL set to: ${ApiClient.activeBaseUrl}')),
                    );
                  }
                }
                if (ctx.mounted) Navigator.of(ctx).pop();
              },
              child: const Text('Save'),
            ),
          ],
        ),
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
          SnackBar(
            content: const Text('Health Connect permission denied — enter vitals manually instead.'),
            action: SnackBarAction(label: 'Enter Manually', onPressed: _openManualVitals),
            duration: const Duration(seconds: 5),
          ),
        );
        await _promptManualVitalsFallback('Health Connect permission was denied by device settings.');
        return;
      }
      final result = await _healthSyncService.sync();
      messenger.showSnackBar(
        SnackBar(
          content: Text('Synced ${result.synced} reading(s): ${result.accepted} accepted, ${result.rejected} rejected.'),
        ),
      );
    } on HealthConnectUnavailable catch (e) {
      messenger.showSnackBar(
        SnackBar(
          content: Text('Health Connect unavailable (${e.reason}).'),
          action: SnackBarAction(label: 'Enter Manually', onPressed: _openManualVitals),
          duration: const Duration(seconds: 5),
        ),
      );
      await _promptManualVitalsFallback('Health Connect is unavailable on this device: ${e.reason}');
    } catch (e) {
      messenger.showSnackBar(
        SnackBar(
          content: Text('Sync failed: $e'),
          action: SnackBarAction(label: 'Enter Manually', onPressed: _openManualVitals),
          duration: const Duration(seconds: 5),
        ),
      );
      await _promptManualVitalsFallback('Failed to connect to Health Connect: $e');
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
          // Live connection status indicator button
          IconButton(
            icon: _serverOnline == null
                ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                : Icon(
                    _serverOnline == true ? Icons.cloud_done : Icons.cloud_off,
                    color: _serverOnline == true ? Colors.green : Colors.redAccent,
                  ),
            tooltip: _serverOnline == true ? 'Server Connected' : 'Server Disconnected — Tap to configure',
            onPressed: _showServerUrlDialog,
          ),
          IconButton(
            icon: const Icon(Icons.settings),
            tooltip: 'Server Settings',
            onPressed: _showServerUrlDialog,
          ),
        ],
      ),
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24.0),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              // Server status banner if offline
              if (_serverOnline == false) ...[
                Container(
                  margin: const EdgeInsets.only(bottom: 20),
                  padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                  decoration: BoxDecoration(
                    color: Colors.red.withAlpha(25),
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: Colors.redAccent.withAlpha(100)),
                  ),
                  child: Row(
                    children: [
                      const Icon(Icons.warning_amber_rounded, color: Colors.redAccent),
                      const SizedBox(width: 10),
                      const Expanded(
                        child: Text(
                          'Cannot reach Cloudflare server. Tap settings to configure or retry.',
                          style: TextStyle(fontSize: 12, color: Colors.redAccent),
                        ),
                      ),
                      TextButton(
                        onPressed: _showServerUrlDialog,
                        child: const Text('Settings'),
                      ),
                    ],
                  ),
                ),
              ],

              const Text(
                'MEDAI — Multimodal AI Health Assessment & Clinical Decision Support System\n\n'
                'This is a prototype. It is not a diagnostic system, a prescription service, '
                'or an emergency service.',
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 28),

              // ux.primary_action
              SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  icon: const Icon(Icons.mic),
                  label: const Text('Talk to Health AI'),
                  style: FilledButton.styleFrom(padding: const EdgeInsets.symmetric(vertical: 14)),
                  onPressed: () => Navigator.of(context).push(
                    MaterialPageRoute(builder: (_) => const ConversationScreen()),
                  ),
                ),
              ),
              const SizedBox(height: 16),

              // Vitals section
              SizedBox(
                width: double.infinity,
                child: FilledButton.tonalIcon(
                  icon: const Icon(Icons.edit_note),
                  label: const Text('Enter Vitals Manually'),
                  style: FilledButton.styleFrom(padding: const EdgeInsets.symmetric(vertical: 14)),
                  onPressed: _openManualVitals,
                ),
              ),

              if (_healthConnectSupported) ...[
                const SizedBox(height: 12),
                SizedBox(
                  width: double.infinity,
                  child: OutlinedButton.icon(
                    icon: _syncing
                        ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                        : const Icon(Icons.sync),
                    label: const Text('Sync Health Connect data'),
                    style: OutlinedButton.styleFrom(padding: const EdgeInsets.symmetric(vertical: 14)),
                    onPressed: _syncing ? null : _syncHealthData,
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

