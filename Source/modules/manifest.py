import json
from pathlib import Path

from modules.storage_query import get_block_device_size_bytes


class ManifestError(Exception):
    """Raised when a case manifest cannot be read or validated."""


REQUIRED_MANIFEST_FIELDS = ("session_id", "created_at", "status")


def _atomic_write_json(manifest_path, manifest):
    manifest_path = Path(manifest_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = manifest_path.with_name(f"{manifest_path.name}.tmp")

    try:
        temp_path.write_text(
            json.dumps(manifest, indent=4) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(manifest_path)
    except OSError as error:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise ManifestError(
            f"Case manifest could not be written: {error}"
        ) from error


def _manifest_is_populated(manifest):
    if any(key in manifest for key in ("device", "assessment", "destination")):
        return True

    if manifest.get("case_contact"):
        return True

    if manifest.get("intake"):
        return True

    return False


def write_initial_case_manifest(session):
    """
    Write the minimal case manifest for a brand-new recovery session only.

    This must not be used after assessment, device, destination, or intake
    data has been persisted. Refuses to overwrite a populated manifest.
    """

    manifest_path = Path(session.recovery_path) / "case.json"

    if manifest_path.is_file():
        try:
            existing = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as error:
            raise ManifestError(
                f"Existing case manifest could not be read: {manifest_path}"
            ) from error

        if isinstance(existing, dict) and _manifest_is_populated(existing):
            raise ManifestError(
                "Refusing to overwrite populated case manifest with "
                "initial manifest."
            )

    manifest = {
        "session_id": session.session_id,
        "case_name": session.case_name,
        "created_at": session.created_at.isoformat(),
        "status": session.status,
    }

    _atomic_write_json(manifest_path, manifest)


def write_case_manifest(session, device, assessment, intake=None):
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
            "size_bytes": get_block_device_size_bytes(device.path),
            "transport": device.transport,
            "filesystem": device.filesystem,
            "role": device.role,
        },
        "assessment": {
            "decision": assessment.decision.status,
            "reason": assessment.decision.reason,
            "risk": assessment.decision.risk,
            "confidence": assessment.decision.confidence,
        },
        "case_contact": intake["case_contact"] if intake else {},
        "intake": intake["intake"] if intake else {},
    }

    if getattr(session, "completed_at", None):
        manifest["completed_at"] = session.completed_at

    if getattr(session, "recovery_outcome", None):
        manifest["recovery_outcome"] = session.recovery_outcome

    if session.destination_device:
        manifest["destination"] = {
            "path": session.destination_device.path,
            "model": session.destination_device.model,
            "serial": session.destination_device.serial,
            "size": session.destination_device.size,
            "size_bytes": get_block_device_size_bytes(
                session.destination_device.path
            ),
            "transport": session.destination_device.transport,
            "filesystem": session.destination_device.filesystem,
            "role": session.destination_device.role,
        }

    _atomic_write_json(manifest_path, manifest)


def _path_under_root(path, root):
    path = Path(path).resolve()
    root = Path(root).resolve()

    try:
        return path.is_relative_to(root)
    except AttributeError:
        return str(path).startswith(str(root))


def read_case_manifest(case_path, *, permitted_roots=None):
    """
    Read and validate a case manifest from the given case directory.

    Read-only. Does not repair or rewrite the manifest.
    """

    case_path = Path(case_path).resolve()
    manifest_path = case_path / "case.json"

    if permitted_roots is not None:
        if not any(
            _path_under_root(case_path, root)
            for root in permitted_roots
        ):
            raise ManifestError(
                "Case path is outside permitted recovery roots."
            )

    if not manifest_path.is_file():
        raise ManifestError(f"case.json not found: {manifest_path}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ManifestError(
            f"case.json is malformed: {manifest_path}"
        ) from error
    except OSError as error:
        raise ManifestError(
            f"case.json could not be read: {manifest_path}"
        ) from error

    if not isinstance(manifest, dict):
        raise ManifestError("case.json must contain a JSON object.")

    for field in REQUIRED_MANIFEST_FIELDS:
        if field not in manifest:
            raise ManifestError(
                f"case.json is missing required field: {field}"
            )

    if manifest["session_id"] != case_path.name:
        raise ManifestError(
            "case.json session_id does not match the case directory name."
        )

    return manifest
