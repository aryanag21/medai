import 'dart:io' show Platform;

import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';

import '../services/api_client.dart';
import '../services/health_connect_adapter.dart';
import '../services/health_sync_service.dart';
import 'conversation_screen.dart';
import 'diagnostic_history_screen.dart';
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
  String? _syncStatusMessage;
  bool? _serverOnline;
  Map<String, dynamic>? _vitalTrends;
  DateTime? _lastSyncTime;
  bool _loadingTrends = false;

  bool get _healthConnectSupported => !kIsWeb && Platform.isAndroid;

  @override
  void initState() {
    super.initState();
    _initConnection();
  }

  Future<void> _initConnection() async {
    await ApiClient.getServerUrl();
    await _checkConnection();
    await _loadVitalsOverview();
  }

  Future<void> _checkConnection() async {
    final online = await ApiClient.testConnection();
    if (mounted) {
      setState(() => _serverOnline = online);
    }
  }

  Future<void> _loadVitalsOverview() async {
    setState(() => _loadingTrends = true);
    try {
      final lastSync = await _healthSyncService.getLastSyncTime();
      final trends = await _healthSyncService.getLocalTrends();
      if (mounted) {
        setState(() {
          _lastSyncTime = lastSync;
          _vitalTrends = trends;
          _loadingTrends = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => _loadingTrends = false);
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
    setState(() {
      _syncing = true;
      _syncStatusMessage = 'Connecting to Health Connect...';
    });
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
      final result = await _healthSyncService.sync(
        onProgress: (status) {
          if (mounted) setState(() => _syncStatusMessage = status);
        },
      );
      messenger.showSnackBar(
        SnackBar(
          content: Text(
            result.message ??
                'Synced ${result.synced} reading(s): ${result.accepted} accepted, ${result.rejected} rejected.',
          ),
        ),
      );
      await _loadVitalsOverview();
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
      if (mounted) {
        setState(() {
          _syncing = false;
          _syncStatusMessage = null;
        });
      }
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
                          'Cannot reach MEDAI server. Tap settings to configure or retry.',
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
              const SizedBox(height: 12),

              // Diagnostic History & Vitals Log
              SizedBox(
                width: double.infinity,
                child: FilledButton.tonalIcon(
                  icon: const Icon(Icons.history_edu),
                  label: const Text('Diagnostic History & Vitals Log'),
                  style: FilledButton.styleFrom(padding: const EdgeInsets.symmetric(vertical: 14)),
                  onPressed: () => Navigator.of(context).push(
                    MaterialPageRoute(builder: (_) => const DiagnosticHistoryScreen()),
                  ),
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
                    label: Text(_syncing ? (_syncStatusMessage ?? 'Syncing...') : 'Sync Health Connect data'),
                    style: OutlinedButton.styleFrom(padding: const EdgeInsets.symmetric(vertical: 14)),
                    onPressed: _syncing ? null : _syncHealthData,
                  ),
                ),
                if (_syncStatusMessage != null && _syncing) ...[
                  const SizedBox(height: 6),
                  Text(
                    _syncStatusMessage!,
                    style: TextStyle(fontSize: 12, color: Theme.of(context).colorScheme.primary),
                    textAlign: TextAlign.center,
                  ),
                ],
              ],

              // 24-Hour Vitals Summary and Extreme Alerts
              _buildTrendsAndAlertsCard(),
            ],
          ),
        ),
      ),
    );
  }

  String _formatTime(DateTime dt) {
    final local = dt.toLocal();
    final hour = local.hour.toString().padLeft(2, '0');
    final min = local.minute.toString().padLeft(2, '0');
    return '${local.month}/${local.day} $hour:$min';
  }

  Widget _buildTrendsAndAlertsCard() {
    if (_vitalTrends == null && !_loadingTrends) {
      return const SizedBox.shrink();
    }

    if (_loadingTrends && _vitalTrends == null) {
      return const Padding(
        padding: EdgeInsets.symmetric(vertical: 20.0),
        child: Center(child: CircularProgressIndicator()),
      );
    }

    final rawTrends = _vitalTrends?['trends'] as Map<String, dynamic>? ?? {};
    final alerts = (_vitalTrends?['extreme_alerts'] as List<dynamic>? ?? []).map((e) => e.toString()).toList();
    final recommendations = (_vitalTrends?['recommendations'] as List<dynamic>? ?? []).map((e) => e.toString()).toList();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const SizedBox(height: 20),
        // Extreme Alerts Banner
        if (alerts.isNotEmpty) ...[
          Container(
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: Colors.red.shade50,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: Colors.red.shade300),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(Icons.warning_amber_rounded, color: Colors.red.shade700, size: 22),
                    const SizedBox(width: 8),
                    Text(
                      'Vital Alert Detected',
                      style: TextStyle(
                        color: Colors.red.shade800,
                        fontWeight: FontWeight.bold,
                        fontSize: 15,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                for (final alert in alerts)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 4),
                    child: Text(
                      '• $alert',
                      style: TextStyle(color: Colors.red.shade900, fontSize: 13),
                    ),
                  ),
                if (recommendations.isNotEmpty) ...[
                  const Divider(height: 16),
                  Row(
                    children: [
                      Icon(Icons.lightbulb_outline, color: Colors.orange.shade800, size: 18),
                      const SizedBox(width: 6),
                      Text(
                        'Clinical Recommendation',
                        style: TextStyle(
                          color: Colors.orange.shade900,
                          fontWeight: FontWeight.w600,
                          fontSize: 13,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 4),
                  for (final rec in recommendations)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 2),
                      child: Text(
                        '• $rec',
                        style: TextStyle(color: Colors.brown.shade800, fontSize: 12),
                      ),
                    ),
                ],
              ],
            ),
          ),
          const SizedBox(height: 16),
        ],

        // Vitals Overview Card
        Card(
          elevation: 1,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
          child: Padding(
            padding: const EdgeInsets.all(16.0),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    const Row(
                      children: [
                        Icon(Icons.insights, size: 20, color: Colors.blueAccent),
                        SizedBox(width: 8),
                        Text(
                          '24-Hour Vitals & Trends',
                          style: TextStyle(fontWeight: FontWeight.bold, fontSize: 15),
                        ),
                      ],
                    ),
                    IconButton(
                      icon: const Icon(Icons.refresh, size: 18),
                      tooltip: 'Refresh trends',
                      onPressed: _loadingTrends ? null : _loadVitalsOverview,
                    ),
                  ],
                ),
                if (_lastSyncTime != null)
                  Text(
                    'Last synced: ${_formatTime(_lastSyncTime!)}',
                    style: TextStyle(fontSize: 11, color: Colors.grey.shade600),
                  ),
                const SizedBox(height: 12),
                if (rawTrends.isEmpty)
                  Text(
                    'No vital measurements recorded yet. Sync with Health Connect or enter vitals manually above.',
                    style: TextStyle(fontSize: 12, color: Colors.grey.shade700, fontStyle: FontStyle.italic),
                  )
                else
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      if (rawTrends.containsKey('heart_rate'))
                        _buildMetricChip(
                          icon: Icons.favorite,
                          label: 'Heart Rate',
                          value: '${(rawTrends['heart_rate']['current'] as num).toInt()} bpm',
                          subtitle: 'avg ${(rawTrends['heart_rate']['avg'] as num).toInt()} (${(rawTrends['heart_rate']['min'] as num).toInt()}–${(rawTrends['heart_rate']['max'] as num).toInt()})',
                          trend: rawTrends['heart_rate']['trend_direction'] as String?,
                          color: Colors.redAccent,
                        ),
                      if (rawTrends.containsKey('oxygen_saturation'))
                        _buildMetricChip(
                          icon: Icons.air,
                          label: 'SpO2',
                          value: '${(rawTrends['oxygen_saturation']['current'] as num).toInt()}%',
                          subtitle: 'min ${(rawTrends['oxygen_saturation']['min'] as num).toInt()}%',
                          trend: rawTrends['oxygen_saturation']['trend_direction'] as String?,
                          color: Colors.blueAccent,
                        ),
                      if (rawTrends.containsKey('body_temperature') || rawTrends.containsKey('temperature'))
                        _buildMetricChip(
                          icon: Icons.thermostat,
                          label: 'Temp',
                          value: '${rawTrends['body_temperature']?['current'] ?? rawTrends['temperature']?['current']}°F',
                          subtitle: 'avg ${rawTrends['body_temperature']?['avg'] ?? rawTrends['temperature']?['avg']}°F',
                          trend: (rawTrends['body_temperature']?['trend_direction'] ?? rawTrends['temperature']?['trend_direction']) as String?,
                          color: Colors.deepOrangeAccent,
                        ),
                      if (rawTrends.containsKey('blood_pressure_systolic'))
                        _buildMetricChip(
                          icon: Icons.speed,
                          label: 'BP',
                          value: '${(rawTrends['blood_pressure_systolic']['current'] as num).toInt()} mmHg',
                          subtitle: 'peak ${(rawTrends['blood_pressure_systolic']['max'] as num).toInt()}',
                          color: Colors.purpleAccent,
                        ),
                    ],
                  ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildMetricChip({
    required IconData icon,
    required String label,
    required String value,
    required String subtitle,
    String? trend,
    required Color color,
  }) {
    IconData? trendIcon;
    Color? trendColor;
    if (trend == 'rising') {
      trendIcon = Icons.arrow_upward;
      trendColor = Colors.orange;
    } else if (trend == 'falling') {
      trendIcon = Icons.arrow_downward;
      trendColor = Colors.blue;
    }

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: color.withAlpha(20),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: color.withAlpha(60)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, size: 16, color: color),
              const SizedBox(width: 4),
              Text(
                label,
                style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: color),
              ),
              if (trendIcon != null) ...[
                const SizedBox(width: 4),
                Icon(trendIcon, size: 14, color: trendColor),
              ],
            ],
          ),
          const SizedBox(height: 2),
          Text(
            value,
            style: const TextStyle(fontSize: 15, fontWeight: FontWeight.bold),
          ),
          Text(
            subtitle,
            style: TextStyle(fontSize: 10, color: Colors.grey.shade700),
          ),
        ],
      ),
    );
  }
}

