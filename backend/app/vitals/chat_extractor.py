import re
from datetime import datetime, timezone

from app.vitals.schema import NormalizedMeasurement


def extract_vitals_from_chat(text: str) -> list[NormalizedMeasurement]:
    """Extracts valid vital sign measurements mentioned conversationally in a chat message.

    Extracts:
        - Blood Pressure (systolic & diastolic in mmHg)
        - Heart Rate (bpm)
        - Oxygen Saturation (SpO2 %)
        - Body Temperature (normalized to Fahrenheit)
        - Respiratory Rate (breaths/min)
        - Weight (normalized to kg)
    """
    text_lower = text.lower().strip()
    measurements: list[NormalizedMeasurement] = []
    now = datetime.now(timezone.utc)

    # 1. Blood Pressure (e.g., "120/80", "bp: 120/80", "blood pressure 130 over 85 mmHg")
    bp_match = re.search(
        r"\b(?:bp|blood\s+pressure)?\s*:?\s*(\d{2,3})\s*(?:/|\s+over\s+)\s*(\d{2,3})\s*(?:mmhg)?\b",
        text_lower,
    )
    if bp_match:
        try:
            sys_val = float(bp_match.group(1))
            dia_val = float(bp_match.group(2))
            if 60 <= sys_val <= 260 and 30 <= dia_val <= 160 and sys_val > dia_val:
                measurements.append(
                    NormalizedMeasurement(
                        type="blood_pressure_systolic",
                        value=sys_val,
                        unit="mmHg",
                        timestamp=now,
                        source="manual",
                        quality="good",
                    )
                )
                measurements.append(
                    NormalizedMeasurement(
                        type="blood_pressure_diastolic",
                        value=dia_val,
                        unit="mmHg",
                        timestamp=now,
                        source="manual",
                        quality="good",
                    )
                )
        except (ValueError, IndexError):
            pass

    # 2. Heart Rate / Pulse (e.g., "heart rate 72", "pulse is 80", "hr: 75", "72 bpm")
    hr_match = re.search(
        r"\b(?:heart\s+rate|pulse|hr)\s*(?:is|was|of|:)?\s*(\d{2,3})\s*(?:bpm)?\b",
        text_lower,
    )
    if not hr_match:
        hr_match = re.search(r"\b(\d{2,3})\s*bpm\b", text_lower)
    if hr_match:
        try:
            hr_val = float(hr_match.group(1))
            if 35 <= hr_val <= 240:
                measurements.append(
                    NormalizedMeasurement(
                        type="heart_rate",
                        value=hr_val,
                        unit="bpm",
                        timestamp=now,
                        source="manual",
                        quality="good",
                    )
                )
        except (ValueError, IndexError):
            pass

    # 3. Oxygen Saturation / SpO2 (e.g., "spo2 98%", "oxygen 97", "o2 level 99%")
    spo2_match = re.search(
        r"\b(?:spo2|oxygen|o2|oximeter)\s*(?:level|saturation|sat|is|was|:)?\s*(\d{2,3})\s*%?\b",
        text_lower,
    )
    if not spo2_match:
        spo2_match = re.search(r"\b(\d{2,3})\s*%\s*(?:spo2|oxygen|o2)\b", text_lower)
    if spo2_match:
        try:
            spo2_val = float(spo2_match.group(1))
            if 70 <= spo2_val <= 100:
                measurements.append(
                    NormalizedMeasurement(
                        type="oxygen_saturation",
                        value=spo2_val,
                        unit="%",
                        timestamp=now,
                        source="manual",
                        quality="good",
                    )
                )
        except (ValueError, IndexError):
            pass

    # 4. Body Temperature (e.g., "temp 98.6", "temperature 100.4 F", "temp is 37.5 C")
    temp_match = re.search(
        r"\b(?:temperature|temp)\s*(?:is|was|of|:)?\s*(\d{2,3}(?:\.\d+)?)\s*(?:°\s*)?(f|c|fahrenheit|celsius)?\b",
        text_lower,
    )
    if not temp_match:
        temp_match = re.search(r"\b(\d{2,3}(?:\.\d+)?)\s*(?:°\s*|deg\s*)([fc])\b", text_lower)

    if temp_match:
        try:
            temp_val = float(temp_match.group(1))
            unit_hint = temp_match.group(2) if len(temp_match.groups()) > 1 else None

            # Detect Celsius vs Fahrenheit
            if unit_hint in ["c", "celsius"] or (34.0 <= temp_val <= 43.0 and unit_hint not in ["f", "fahrenheit"]):
                # Convert Celsius to Fahrenheit
                temp_f = round((temp_val * 9.0 / 5.0) + 32.0, 1)
            else:
                temp_f = round(temp_val, 1)

            if 85.0 <= temp_f <= 112.0:
                measurements.append(
                    NormalizedMeasurement(
                        type="body_temperature",
                        value=temp_f,
                        unit="F",
                        timestamp=now,
                        source="manual",
                        quality="good",
                    )
                )
        except (ValueError, IndexError):
            pass

    # 5. Respiratory Rate (e.g., "respiratory rate 18", "breaths 16")
    rr_match = re.search(
        r"\b(?:respiratory\s+rate|breathing\s+rate|rr)\s*(?:is|was|of|:)?\s*(\d{1,2})\b",
        text_lower,
    )
    if rr_match:
        try:
            rr_val = float(rr_match.group(1))
            if 8 <= rr_val <= 60:
                measurements.append(
                    NormalizedMeasurement(
                        type="respiratory_rate",
                        value=rr_val,
                        unit="breaths/min",
                        timestamp=now,
                        source="manual",
                        quality="good",
                    )
                )
        except (ValueError, IndexError):
            pass

    # 6. Weight (e.g., "weight 72 kg", "weight is 160 lbs", "75kg")
    wt_match = re.search(
        r"\b(?:weight)\s*(?:is|was|of|:)?\s*(\d{2,3}(?:\.\d+)?)\s*(kg|kgs|kilos|lbs|pounds)?\b",
        text_lower,
    )
    if wt_match:
        try:
            wt_val = float(wt_match.group(1))
            unit_str = wt_match.group(2)
            if unit_str in ["lbs", "pounds"]:
                wt_kg = round(wt_val * 0.45359237, 1)
            else:
                wt_kg = round(wt_val, 1)

            if 20.0 <= wt_kg <= 400.0:
                measurements.append(
                    NormalizedMeasurement(
                        type="weight",
                        value=wt_kg,
                        unit="kg",
                        timestamp=now,
                        source="manual",
                        quality="good",
                    )
                )
        except (ValueError, IndexError):
            pass

    return measurements
