import json
from pathlib import Path


def write_case_manifest(session, device, assessment):
    """
    Write the case manifest for a recovery session.
    """

    manifest_path = Path(session.recovery_path) / "case.json"

    manifest = {
        "session_id": session.session_id,
        "case_name": session.case_name,
        "created_at": session.created_at.isoformat(),
        "status": session.status,
           "device": {
        "path": device.path,
        "model": device.model,
        "serial": device.serial,
        "size": device.size,
        "transport": device.transport,
        "filesystem": device.filesystem,
        "role": device.role
    },

    "assessment": {
        "decision": assessment.decision.status,
        "reason": assessment.decision.reason,
        "risk": assessment.decision.risk,
        "confidence": assessment.decision.confidence
    }
    }

    with open(manifest_path, "w") as file:
        json.dump(manifest, file, indent=4)