import 'package:flutter/material.dart';
import '../services/api_client.dart';

class DiagnosticHistoryScreen extends StatefulWidget {
  const DiagnosticHistoryScreen({super.key, this.apiClient});

  final ApiClient? apiClient;

  @override
  State<DiagnosticHistoryScreen> createState() => _DiagnosticHistoryScreenState();
}

class _DiagnosticHistoryScreenState extends State<DiagnosticHistoryScreen> {
  late final ApiClient _apiClient = widget.apiClient ?? ApiClient();
  List<Map<String, dynamic>> _records = [];
  bool _loading = true;
  String? _error;
  String _filter = 'all'; // 'all' | 'active' | 'resolved'

  @override
  void initState() {
    super.initState();
    _loadHistory();
  }

  Future<void> _loadHistory() async {
    setState(() {
      _loading = true;
      _error = null;
    });

    try {
      final statusParam = _filter == 'all' ? null : _filter;
      final data = await _apiClient.getDiagnosticHistory(status: statusParam);
      if (mounted) {
        setState(() {
          _records = data;
          _loading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = 'Could not load diagnostic history: $e';
          _loading = false;
        });
      }
    }
  }

  Future<void> _toggleStatus(Map<String, dynamic> record) async {
    final recordId = record['id'] as String;
    final currentStatus = record['status'] as String? ?? 'active';
    final newStatus = currentStatus == 'active' ? 'resolved' : 'active';

    final confirm = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(newStatus == 'resolved' ? 'Mark as Resolved?' : 'Reactivate Issue?'),
        content: Text(
          newStatus == 'resolved'
              ? 'Has this symptom or health issue completely resolved?\n\n'
                'Marking it resolved will let the AI know it is no longer persistent.'
              : 'Reopen this diagnostic consultation as an active persistent issue?',
        ),
        actions: [
          TextButton(onPressed: () => Navigator.of(ctx).pop(false), child: const Text('Cancel')),
          FilledButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            child: Text(newStatus == 'resolved' ? 'Mark Resolved' : 'Reactivate'),
          ),
        ],
      ),
    );

    if (confirm != true) return;

    final messenger = ScaffoldMessenger.of(context);
    try {
      await _apiClient.updateDiagnosticStatus(recordId: recordId, status: newStatus);
      if (mounted) {
        messenger.showSnackBar(
          SnackBar(content: Text('Diagnosis updated to $newStatus.')),
        );
      }
      await _loadHistory();
    } catch (e) {
      if (mounted) {
        messenger.showSnackBar(
          SnackBar(content: Text('Failed to update status: $e')),
        );
      }
    }
  }

  String _formatDateTime(String? isoString) {
    if (isoString == null) return '';
    final dt = DateTime.tryParse(isoString)?.toLocal();
    if (dt == null) return '';
    final hour = dt.hour.toString().padLeft(2, '0');
    final min = dt.minute.toString().padLeft(2, '0');
    final months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return '${months[dt.month - 1]} ${dt.day}, $hour:$min';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Diagnostic History & Vitals'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: 'Refresh',
            onPressed: _loadHistory,
          ),
        ],
      ),
      body: Column(
        children: [
          // Filter segmented bar
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16.0, vertical: 8.0),
            child: SegmentedButton<String>(
              segments: const [
                ButtonSegment(value: 'all', label: Text('All')),
                ButtonSegment(value: 'active', label: Text('Active')),
                ButtonSegment(value: 'resolved', label: Text('Resolved')),
              ],
              selected: {_filter},
              onSelectionChanged: (set) {
                setState(() => _filter = set.first);
                _loadHistory();
              },
            ),
          ),

          if (_error != null)
            Container(
              margin: const EdgeInsets.all(16),
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: Colors.red.shade50,
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: Colors.red.shade200),
              ),
              child: Row(
                children: [
                  Icon(Icons.error_outline, color: Colors.red.shade700),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(_error!, style: TextStyle(color: Colors.red.shade800, fontSize: 13)),
                  ),
                ],
              ),
            ),

          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : _records.isEmpty
                    ? Center(
                        child: Padding(
                          padding: const EdgeInsets.all(32.0),
                          child: Column(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Icon(Icons.medical_information_outlined, size: 64, color: Colors.grey.shade400),
                              const SizedBox(height: 16),
                              Text(
                                _filter == 'all'
                                    ? 'No Diagnostic Consultations Yet'
                                    : 'No ${_filter.toUpperCase()} Diagnoses',
                                style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
                              ),
                              const SizedBox(height: 8),
                              Text(
                                'Talk to Health AI about any symptoms or issues. Every consultation and its snapshot of vitals will be saved here automatically.',
                                textAlign: TextAlign.center,
                                style: TextStyle(fontSize: 13, color: Colors.grey.shade600),
                              ),
                            ],
                          ),
                        ),
                      )
                    : RefreshIndicator(
                        onRefresh: _loadHistory,
                        child: ListView.builder(
                          padding: const EdgeInsets.all(16),
                          itemCount: _records.length,
                          itemBuilder: (context, index) {
                            final record = _records[index];
                            return _buildDiagnosticCard(record);
                          },
                        ),
                      ),
          ),
        ],
      ),
    );
  }

  Widget _buildDiagnosticCard(Map<String, dynamic> record) {
    final title = record['title'] as String? ?? 'Consultation Diagnosis';
    final chiefComplaint = record['chief_complaint'] as String? ?? '';
    final assessment = record['assessment'] as String? ?? '';
    final status = record['status'] as String? ?? 'active';
    final symptoms = (record['symptoms'] as List<dynamic>? ?? []).map((e) => e.toString()).toList();
    final vitalsSnapshot = record['vitals_snapshot'] as Map<String, dynamic>? ?? {};
    final createdAt = record['created_at'] as String?;
    final isActive = status == 'active';

    return Card(
      elevation: 2,
      margin: const EdgeInsets.only(bottom: 16),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      child: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Header: Title & Status Chip
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        title,
                        style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        _formatDateTime(createdAt),
                        style: TextStyle(fontSize: 11, color: Colors.grey.shade600),
                      ),
                    ],
                  ),
                ),
                GestureDetector(
                  onTap: () => _toggleStatus(record),
                  child: Chip(
                    label: Text(
                      isActive ? 'Active' : 'Resolved',
                      style: TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.bold,
                        color: isActive ? Colors.green.shade800 : Colors.grey.shade800,
                      ),
                    ),
                    backgroundColor: isActive ? Colors.green.shade50 : Colors.grey.shade200,
                    side: BorderSide(color: isActive ? Colors.green.shade300 : Colors.grey.shade400),
                    avatar: Icon(
                      isActive ? Icons.check_circle_outline : Icons.task_alt,
                      size: 14,
                      color: isActive ? Colors.green.shade800 : Colors.grey.shade700,
                    ),
                    padding: const EdgeInsets.symmetric(horizontal: 4),
                  ),
                ),
              ],
            ),
            const Divider(height: 20),

            // Chief Complaint
            if (chiefComplaint.isNotEmpty) ...[
              Text(
                'Chief Complaint:',
                style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: Colors.grey.shade700),
              ),
              const SizedBox(height: 2),
              Text(
                chiefComplaint,
                style: const TextStyle(fontSize: 13, fontStyle: FontStyle.italic),
              ),
              const SizedBox(height: 12),
            ],

            // Symptoms Evaluated
            if (symptoms.isNotEmpty) ...[
              Wrap(
                spacing: 6,
                runSpacing: 4,
                children: symptoms
                    .map(
                      (s) => Container(
                        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                        decoration: BoxDecoration(
                          color: Theme.of(context).colorScheme.primaryContainer.withAlpha(80),
                          borderRadius: BorderRadius.circular(6),
                        ),
                        child: Text(
                          s,
                          style: TextStyle(
                            fontSize: 11,
                            color: Theme.of(context).colorScheme.onPrimaryContainer,
                            fontWeight: FontWeight.w500,
                          ),
                        ),
                      ),
                    )
                    .toList(),
              ),
              const SizedBox(height: 12),
            ],

            // Vitals at Time of Diagnosis
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: Colors.blueGrey.shade50,
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: Colors.blueGrey.shade100),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Icon(Icons.monitor_heart, size: 14, color: Colors.blueGrey.shade700),
                      const SizedBox(width: 4),
                      Text(
                        'Vitals Recorded at Time of Diagnosis:',
                        style: TextStyle(
                          fontSize: 11,
                          fontWeight: FontWeight.bold,
                          color: Colors.blueGrey.shade800,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 6),
                  if (vitalsSnapshot.isEmpty)
                    Text(
                      'No vitals were recorded on device during this consultation.',
                      style: TextStyle(fontSize: 11, color: Colors.grey.shade600, fontStyle: FontStyle.italic),
                    )
                  else
                    Wrap(
                      spacing: 8,
                      runSpacing: 6,
                      children: [
                        if (vitalsSnapshot.containsKey('heart_rate'))
                          _buildVitalBadge(
                            icon: Icons.favorite,
                            label: 'HR',
                            value: '${(vitalsSnapshot['heart_rate']['value'] as num).toInt()} bpm',
                            color: Colors.redAccent,
                          ),
                        if (vitalsSnapshot.containsKey('oxygen_saturation'))
                          _buildVitalBadge(
                            icon: Icons.air,
                            label: 'SpO2',
                            value: '${(vitalsSnapshot['oxygen_saturation']['value'] as num).toInt()}%',
                            color: Colors.blueAccent,
                          ),
                        if (vitalsSnapshot.containsKey('body_temperature') || vitalsSnapshot.containsKey('temperature'))
                          _buildVitalBadge(
                            icon: Icons.thermostat,
                            label: 'Temp',
                            value: '${vitalsSnapshot['body_temperature']?['value'] ?? vitalsSnapshot['temperature']?['value']}°F',
                            color: Colors.deepOrangeAccent,
                          ),
                        if (vitalsSnapshot.containsKey('blood_pressure_systolic'))
                          _buildVitalBadge(
                            icon: Icons.speed,
                            label: 'BP',
                            value: '${(vitalsSnapshot['blood_pressure_systolic']['value'] as num).toInt()} mmHg',
                            color: Colors.purpleAccent,
                          ),
                      ],
                    ),
                ],
              ),
            ),
            const SizedBox(height: 12),

            // Clinical Assessment & Guidance
            Text(
              'Clinical Guidance & Care Plan:',
              style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: Colors.grey.shade700),
            ),
            const SizedBox(height: 4),
            Text(
              assessment,
              style: const TextStyle(fontSize: 13, height: 1.35),
            ),

            const SizedBox(height: 12),
            // Footer status toggle button
            Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                TextButton.icon(
                  icon: Icon(isActive ? Icons.check_circle_outline : Icons.replay, size: 16),
                  label: Text(
                    isActive ? 'Mark Issue as Resolved' : 'Reactivate Issue',
                    style: const TextStyle(fontSize: 12),
                  ),
                  onPressed: () => _toggleStatus(record),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildVitalBadge({
    required IconData icon,
    required String label,
    required String value,
    required Color color,
  }) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: color.withAlpha(80)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 13, color: color),
          const SizedBox(width: 4),
          Text(
            '$label: ',
            style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: color),
          ),
          Text(
            value,
            style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w600),
          ),
        ],
      ),
    );
  }
}
