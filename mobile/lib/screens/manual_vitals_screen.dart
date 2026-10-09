import 'package:flutter/material.dart';

import '../services/api_client.dart';

/// Screen for manual vital signs entry and inspection.
///
/// vital_system: when Health Connect is unavailable or permissions are denied,
/// manual entry allows the user to record vitals into the canonical patient state
/// via POST /vitals (app/api/vitals.py).
class ManualVitalsScreen extends StatefulWidget {
  const ManualVitalsScreen({super.key, this.apiClient});

  final ApiClient? apiClient;

  @override
  State<ManualVitalsScreen> createState() => _ManualVitalsScreenState();
}

class _ManualVitalsScreenState extends State<ManualVitalsScreen> {
  late final ApiClient _apiClient = widget.apiClient ?? ApiClient();

  final _hrController = TextEditingController();
  final _spo2Controller = TextEditingController();
  final _bpSysController = TextEditingController();
  final _bpDiaController = TextEditingController();
  final _tempController = TextEditingController();
  final _respController = TextEditingController();
  final _weightController = TextEditingController();

  bool _isCelsius = false;
  bool _saving = false;
  bool _loadingHistory = false;
  List<Map<String, dynamic>> _vitalsHistory = [];
  String? _statusMessage;

  @override
  void initState() {
    super.initState();
    _fetchVitals();
  }

  @override
  void dispose() {
    _hrController.dispose();
    _spo2Controller.dispose();
    _bpSysController.dispose();
    _bpDiaController.dispose();
    _tempController.dispose();
    _respController.dispose();
    _weightController.dispose();
    super.dispose();
  }

  Future<void> _fetchVitals() async {
    setState(() => _loadingHistory = true);
    try {
      final vitals = await _apiClient.getVitals();
      if (mounted) {
        setState(() {
          _vitalsHistory = vitals;
          _loadingHistory = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => _loadingHistory = false);
    }
  }

  Future<void> _saveVitals() async {
    final messenger = ScaffoldMessenger.of(context);
    final hrText = _hrController.text.trim();
    final spo2Text = _spo2Controller.text.trim();
    final sysText = _bpSysController.text.trim();
    final diaText = _bpDiaController.text.trim();
    final tempText = _tempController.text.trim();
    final respText = _respController.text.trim();
    final weightText = _weightController.text.trim();

    if (hrText.isEmpty &&
        spo2Text.isEmpty &&
        sysText.isEmpty &&
        diaText.isEmpty &&
        tempText.isEmpty &&
        respText.isEmpty &&
        weightText.isEmpty) {
      messenger.showSnackBar(
        const SnackBar(content: Text('Please enter at least one vital measurement.')),
      );
      return;
    }

    setState(() {
      _saving = true;
      _statusMessage = null;
    });

    int savedCount = 0;
    final errors = <String>[];
    final now = DateTime.now().toUtc();

    Future<void> recordOne({
      required String type,
      required double value,
      required String unit,
    }) async {
      try {
        await _apiClient.addVital(
          type: type,
          value: value,
          unit: unit,
          timestamp: now,
        );
        savedCount++;
      } catch (e) {
        errors.add('$type: $e');
      }
    }

    try {
      if (hrText.isNotEmpty) {
        final val = double.tryParse(hrText);
        if (val == null || val <= 0 || val > 400) {
          errors.add('Heart rate must be between 1 and 400 bpm.');
        } else {
          await recordOne(type: 'heart_rate', value: val, unit: 'bpm');
        }
      }

      if (spo2Text.isNotEmpty) {
        final val = double.tryParse(spo2Text);
        if (val == null || val <= 0 || val > 100) {
          errors.add('SpO2 must be between 1 and 100%.');
        } else {
          await recordOne(type: 'oxygen_saturation', value: val, unit: '%');
        }
      }

      if (sysText.isNotEmpty) {
        final val = double.tryParse(sysText);
        if (val == null || val <= 0 || val > 400) {
          errors.add('Systolic BP must be between 1 and 400 mmHg.');
        } else {
          await recordOne(type: 'blood_pressure_systolic', value: val, unit: 'mmHg');
        }
      }

      if (diaText.isNotEmpty) {
        final val = double.tryParse(diaText);
        if (val == null || val <= 0 || val > 300) {
          errors.add('Diastolic BP must be between 1 and 300 mmHg.');
        } else {
          await recordOne(type: 'blood_pressure_diastolic', value: val, unit: 'mmHg');
        }
      }

      if (tempText.isNotEmpty) {
        final val = double.tryParse(tempText);
        if (val == null) {
          errors.add('Invalid temperature reading.');
        } else {
          // Backend expects Fahrenheit (70 - 115 F).
          final fahrenheit = _isCelsius ? (val * 9 / 5 + 32) : val;
          if (fahrenheit < 70 || fahrenheit > 115) {
            errors.add('Temperature must be between 70°F and 115°F (21°C - 46°C).');
          } else {
            await recordOne(type: 'body_temperature', value: double.parse(fahrenheit.toStringAsFixed(1)), unit: 'F');
          }
        }
      }

      if (respText.isNotEmpty) {
        final val = double.tryParse(respText);
        if (val == null || val <= 0 || val > 100) {
          errors.add('Respiratory rate must be between 1 and 100 breaths/min.');
        } else {
          await recordOne(type: 'respiratory_rate', value: val, unit: 'breaths/min');
        }
      }

      if (weightText.isNotEmpty) {
        final val = double.tryParse(weightText);
        if (val == null || val <= 0 || val > 700) {
          errors.add('Weight must be between 1 and 700 kg.');
        } else {
          await recordOne(type: 'weight', value: val, unit: 'kg');
        }
      }

      if (savedCount > 0) {
        _hrController.clear();
        _spo2Controller.clear();
        _bpSysController.clear();
        _bpDiaController.clear();
        _tempController.clear();
        _respController.clear();
        _weightController.clear();
        await _fetchVitals();
      }

      if (mounted) {
        setState(() {
          _saving = false;
          if (errors.isEmpty) {
            _statusMessage = 'Successfully saved $savedCount vital measurement(s)!';
          } else {
            _statusMessage = 'Saved $savedCount. Issues: ${errors.join("; ")}';
          }
        });
        messenger.showSnackBar(
          SnackBar(
            content: Text(
              errors.isEmpty
                  ? 'Saved $savedCount vital measurement(s) to your profile.'
                  : 'Saved $savedCount. Issues encountered.',
            ),
            backgroundColor: errors.isEmpty ? Colors.green.shade700 : Colors.amber.shade900,
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _saving = false;
          _statusMessage = 'Error saving vitals: $e';
        });
        messenger.showSnackBar(
          SnackBar(content: Text('Error saving vitals: $e'), backgroundColor: Colors.red),
        );
      }
    }
  }

  String _formatVitalLabel(String type) {
    switch (type) {
      case 'heart_rate':
        return 'Heart Rate';
      case 'oxygen_saturation':
        return 'Oxygen Saturation';
      case 'blood_pressure_systolic':
        return 'BP Systolic';
      case 'blood_pressure_diastolic':
        return 'BP Diastolic';
      case 'body_temperature':
        return 'Body Temperature';
      case 'respiratory_rate':
        return 'Respiratory Rate';
      case 'weight':
        return 'Weight';
      default:
        return type;
    }
  }

  IconData _formatVitalIcon(String type) {
    switch (type) {
      case 'heart_rate':
        return Icons.favorite;
      case 'oxygen_saturation':
        return Icons.air;
      case 'blood_pressure_systolic':
      case 'blood_pressure_diastolic':
        return Icons.speed;
      case 'body_temperature':
        return Icons.thermostat;
      case 'respiratory_rate':
        return Icons.waves;
      case 'weight':
        return Icons.monitor_weight_outlined;
      default:
        return Icons.health_and_safety;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Manual Vitals Entry'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: 'Refresh Current Vitals',
            onPressed: _loadingHistory ? null : _fetchVitals,
          ),
        ],
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Card(
              elevation: 0,
              color: Theme.of(context).colorScheme.primaryContainer.withAlpha(80),
              child: const Padding(
                padding: EdgeInsets.all(16.0),
                child: Row(
                  children: [
                    Icon(Icons.info_outline, size: 24),
                    SizedBox(width: 12),
                    Expanded(
                      child: Text(
                        'Enter any available measurements below. They will be saved to your health assessment profile.',
                        style: TextStyle(fontSize: 13),
                      ),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),

            // Heart Rate & SpO2 row
            Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _hrController,
                    decoration: const InputDecoration(
                      labelText: 'Heart Rate',
                      hintText: '72',
                      suffixText: 'bpm',
                      prefixIcon: Icon(Icons.favorite, color: Colors.redAccent),
                      border: OutlineInputBorder(),
                    ),
                    keyboardType: TextInputType.number,
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: TextField(
                    controller: _spo2Controller,
                    decoration: const InputDecoration(
                      labelText: 'SpO2 (Oxygen)',
                      hintText: '98',
                      suffixText: '%',
                      prefixIcon: Icon(Icons.air, color: Colors.blueAccent),
                      border: OutlineInputBorder(),
                    ),
                    keyboardType: TextInputType.number,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 14),

            // Blood Pressure
            Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _bpSysController,
                    decoration: const InputDecoration(
                      labelText: 'BP Systolic',
                      hintText: '120',
                      suffixText: 'mmHg',
                      prefixIcon: Icon(Icons.speed, color: Colors.purpleAccent),
                      border: OutlineInputBorder(),
                    ),
                    keyboardType: TextInputType.number,
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: TextField(
                    controller: _bpDiaController,
                    decoration: const InputDecoration(
                      labelText: 'BP Diastolic',
                      hintText: '80',
                      suffixText: 'mmHg',
                      border: OutlineInputBorder(),
                    ),
                    keyboardType: TextInputType.number,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 14),

            // Temperature with °F / °C toggle
            Row(
              children: [
                Expanded(
                  flex: 3,
                  child: TextField(
                    controller: _tempController,
                    decoration: InputDecoration(
                      labelText: 'Body Temperature',
                      hintText: _isCelsius ? '37.0' : '98.6',
                      suffixText: _isCelsius ? '°C' : '°F',
                      prefixIcon: const Icon(Icons.thermostat, color: Colors.orangeAccent),
                      border: const OutlineInputBorder(),
                    ),
                    keyboardType: const TextInputType.numberWithOptions(decimal: true),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  flex: 2,
                  child: SegmentedButton<bool>(
                    segments: const [
                      ButtonSegment(value: false, label: Text('°F')),
                      ButtonSegment(value: true, label: Text('°C')),
                    ],
                    selected: {_isCelsius},
                    onSelectionChanged: (set) => setState(() => _isCelsius = set.first),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 14),

            // Respiratory Rate & Weight
            Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _respController,
                    decoration: const InputDecoration(
                      labelText: 'Respiratory Rate',
                      hintText: '16',
                      suffixText: '/min',
                      prefixIcon: Icon(Icons.waves, color: Colors.teal),
                      border: OutlineInputBorder(),
                    ),
                    keyboardType: TextInputType.number,
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: TextField(
                    controller: _weightController,
                    decoration: const InputDecoration(
                      labelText: 'Weight',
                      hintText: '70',
                      suffixText: 'kg',
                      prefixIcon: Icon(Icons.monitor_weight_outlined, color: Colors.brown),
                      border: OutlineInputBorder(),
                    ),
                    keyboardType: const TextInputType.numberWithOptions(decimal: true),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 20),

            if (_statusMessage != null) ...[
              Text(
                _statusMessage!,
                style: TextStyle(
                  color: _statusMessage!.contains('Error') || _statusMessage!.contains('Issues')
                      ? Colors.amber.shade900
                      : Colors.green.shade800,
                  fontWeight: FontWeight.w500,
                ),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 12),
            ],

            FilledButton.icon(
              icon: _saving
                  ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                  : const Icon(Icons.save),
              label: Text(_saving ? 'Saving Vitals...' : 'Save Vitals'),
              onPressed: _saving ? null : _saveVitals,
              style: FilledButton.styleFrom(padding: const EdgeInsets.symmetric(vertical: 14)),
            ),

            const SizedBox(height: 32),
            const Divider(),
            const SizedBox(height: 12),

            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                const Text(
                  'Current Patient Vitals',
                  style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
                ),
                if (_loadingHistory)
                  const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
              ],
            ),
            const SizedBox(height: 12),

            if (_vitalsHistory.isEmpty && !_loadingHistory)
              const Center(
                child: Padding(
                  padding: EdgeInsets.symmetric(vertical: 24.0),
                  child: Text(
                    'No vital measurements recorded yet.',
                    style: TextStyle(color: Colors.grey),
                  ),
                ),
              ),

            ..._vitalsHistory.map((v) {
              final type = v['type'] as String? ?? 'vital';
              final value = v['value'];
              final unit = v['unit'] as String? ?? '';
              final updated = v['updated_at'] as String? ?? '';
              DateTime? parsedTime;
              try {
                parsedTime = DateTime.parse(updated).toLocal();
              } catch (_) {}

              return Card(
                margin: const EdgeInsets.only(bottom: 8.0),
                child: ListTile(
                  leading: CircleAvatar(
                    backgroundColor: Theme.of(context).colorScheme.primaryContainer,
                    child: Icon(_formatVitalIcon(type), color: Theme.of(context).colorScheme.primary, size: 20),
                  ),
                  title: Text(_formatVitalLabel(type)),
                  subtitle: parsedTime != null
                      ? Text('Recorded: ${parsedTime.year}-${parsedTime.month.toString().padLeft(2, '0')}-${parsedTime.day.toString().padLeft(2, '0')} ${parsedTime.hour.toString().padLeft(2, '0')}:${parsedTime.minute.toString().padLeft(2, '0')}')
                      : null,
                  trailing: Text(
                    '$value $unit',
                    style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
                  ),
                ),
              );
            }),
          ],
        ),
      ),
    );
  }
}
