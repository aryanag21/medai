from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Measurement

EXTREME_THRESHOLDS: dict[str, dict[str, float]] = {
    "heart_rate": {
        "tachycardia": 100.0,
        "extreme_tachycardia": 130.0,
        "bradycardia": 50.0,
        "extreme_bradycardia": 40.0,
    },
    "oxygen_saturation": {
        "hypoxemia": 95.0,
        "severe_hypoxemia": 90.0,
    },
    "body_temperature": {
        "fever": 100.4,
        "high_fever": 102.5,
        "hypothermia": 95.0,
    },
    "blood_pressure_systolic": {
        "hypertension_stage_1": 130.0,
        "hypertension_stage_2": 140.0,
        "hypertensive_crisis": 180.0,
        "hypotension": 90.0,
    },
    "blood_pressure_diastolic": {
        "hypertension_stage_1": 80.0,
        "hypertension_stage_2": 90.0,
        "hypertensive_crisis": 120.0,
        "hypotension": 60.0,
    },
    "respiratory_rate": {
        "tachypnea": 20.0,
        "bradypnea": 12.0,
    },
}


async def calculate_vital_trends(
    db: AsyncSession,
    patient_id: UUID,
    window_hours: int = 24,
) -> dict[str, Any]:
    """Calculates statistical health trends, anomaly detections, and clinical suggestions
    over the specified lookback window (default 24 hours).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)

    query = (
        select(Measurement)
        .where(
            Measurement.patient_id == patient_id,
            Measurement.accepted == True,  # noqa: E712
            Measurement.timestamp >= cutoff,
        )
        .order_by(Measurement.timestamp.asc())
    )
    result = await db.execute(query)
    measurements = list(result.scalars().all())

    by_type: dict[str, list[Measurement]] = {}
    for m in measurements:
        by_type.setdefault(m.type, []).append(m)

    trends_data: dict[str, Any] = {}
    extreme_alerts: list[str] = []
    clinical_recommendations: list[str] = []

    for vital_type, samples in by_type.items():
        if not samples:
            continue

        values = [s.value for s in samples]
        unit = samples[-1].unit
        cur_val = samples[-1].value
        min_val = min(values)
        max_val = max(values)
        avg_val = round(sum(values) / len(values), 1)

        # Direction trend: compare first half of window to second half
        direction = "stable"
        if len(values) >= 4:
            mid = len(values) // 2
            first_half_avg = sum(values[:mid]) / mid
            second_half_avg = sum(values[mid:]) / (len(values) - mid)
            delta = second_half_avg - first_half_avg
            if abs(delta) >= (0.05 * avg_val):
                direction = "rising" if delta > 0 else "falling"

        trends_data[vital_type] = {
            "current": cur_val,
            "min": min_val,
            "max": max_val,
            "avg": avg_val,
            "unit": unit,
            "sample_count": len(samples),
            "trend_direction": direction,
            "latest_timestamp": samples[-1].timestamp.isoformat(),
        }

        # Check for extreme anomalies
        if vital_type == "heart_rate":
            if max_val >= EXTREME_THRESHOLDS["heart_rate"]["extreme_tachycardia"]:
                extreme_alerts.append(f"Heart rate spiked to an extreme {int(max_val)} bpm (average is {int(avg_val)} bpm).")
                clinical_recommendations.append("High heart rate spike detected (>130 bpm). Sit, hydrate, and practice deep breathing. Seek emergency care if accompanied by chest pain or dizziness.")
            elif max_val >= EXTREME_THRESHOLDS["heart_rate"]["tachycardia"]:
                extreme_alerts.append(f"Elevated heart rate noticed (peak {int(max_val)} bpm, average {int(avg_val)} bpm).")
            if min_val <= EXTREME_THRESHOLDS["heart_rate"]["extreme_bradycardia"]:
                extreme_alerts.append(f"Heart rate dropped to very low {int(min_val)} bpm.")

        elif vital_type == "oxygen_saturation":
            if min_val < EXTREME_THRESHOLDS["oxygen_saturation"]["severe_hypoxemia"]:
                extreme_alerts.append(f"CRITICAL: Blood oxygen dropped to {int(min_val)}% (normal is 95–100%).")
                clinical_recommendations.append("Severe oxygen drop detected (<90%). Sit upright immediately, avoid exertion, and seek prompt in-person medical evaluation.")
            elif min_val < EXTREME_THRESHOLDS["oxygen_saturation"]["hypoxemia"]:
                extreme_alerts.append(f"Low blood oxygen noticed: dipped to {int(min_val)}% (average is {int(avg_val)}%).")
                clinical_recommendations.append("Mild oxygen dip (<95%). Rest sitting upright rather than lying flat to maximize lung expansion.")

        elif vital_type in ("body_temperature", "temperature"):
            if max_val >= EXTREME_THRESHOLDS["body_temperature"]["high_fever"]:
                extreme_alerts.append(f"High fever recorded at {max_val}°{unit}.")
                clinical_recommendations.append("High fever detected. Use Paracetamol 500–650mg or Ibuprofen 400mg with water and stay well hydrated. Seek evaluation if fever persists >48h.")
            elif max_val >= EXTREME_THRESHOLDS["body_temperature"]["fever"]:
                extreme_alerts.append(f"Fever recorded at {max_val}°{unit}.")

        elif vital_type == "blood_pressure_systolic":
            if max_val >= EXTREME_THRESHOLDS["blood_pressure_systolic"]["hypertensive_crisis"]:
                extreme_alerts.append(f"Urgent: Blood pressure systolic spiked to {int(max_val)} mmHg.")
                clinical_recommendations.append("Systolic blood pressure spike (>180 mmHg). Rest quietly for 5 minutes and re-check. If still elevated, seek immediate medical attention.")
            elif max_val >= EXTREME_THRESHOLDS["blood_pressure_systolic"]["hypertension_stage_2"]:
                extreme_alerts.append(f"Elevated systolic blood pressure (peak {int(max_val)} mmHg).")

    return {
        "window_hours": window_hours,
        "measurement_count": len(measurements),
        "trends": trends_data,
        "extreme_alerts": extreme_alerts,
        "recommendations": list(dict.fromkeys(clinical_recommendations)),  # deduplicate
    }


def format_trends_summary(trends_result: dict[str, Any]) -> str:
    """Formats trends into a concise summary string for LLM clinical prompt injection."""
    trends = trends_result.get("trends", {})
    if not trends:
        return ""

    lines = []
    for vital_type, stats in trends.items():
        name = vital_type.replace("_", " ").title()
        cur = stats["current"]
        u = stats["unit"]
        min_v = stats["min"]
        max_v = stats["max"]
        avg = stats["avg"]
        cnt = stats["sample_count"]
        direction = stats["trend_direction"]
        dir_symbol = "↗" if direction == "rising" else "↘" if direction == "falling" else "→"
        lines.append(f"• {name}: Latest {cur} {u} | 24h Avg {avg} {u} (Range: {min_v}–{max_v}, {cnt} samples, trend {dir_symbol})")

    alerts = trends_result.get("extreme_alerts", [])
    if alerts:
        lines.append("• EXTREME VITAL ALERTS: " + " | ".join(alerts))

    return "\n".join(lines)
