"""Valid no-evidence outcomes for tests concerned with execution boundaries."""

import json


def blocked_handoff(role: str, summary: str = "No verified evidence is available") -> str:
    value = {
        "protocol_version": "1",
        "status": "blocked",
        "summary": summary,
        "limitations": ["No verified evidence is available"],
    }
    if role == "knowledge":
        value.update(definitions=[], unresolved_questions=[])
    elif role == "sql":
        value.update(output_artifact_ids=[])
    elif role == "report":
        value.update(report_artifact_id=None, chart_artifact_ids=[], evidence_artifact_ids=[])
    else:
        raise ValueError(role)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
