from datetime import datetime
from pathlib import Path


class AuditLogError(Exception):
    """Raised when audit.log cannot be read."""


# Stable result codes returned by log_event. Not displayed to operators.
CODE_AUDIT_LOG_WRITTEN = "AUDIT_LOG_WRITTEN"
CODE_AUDIT_LOG_WRITE_FAILED = "AUDIT_LOG_WRITE_FAILED"

# Optional presentation-boundary sink for logging failures. ECHO never prints.
# The orchestrator may register a handler to make failures operator-visible.
_failure_handler = None


def set_log_failure_handler(handler):
    """
    Register (or clear with None) a callback invoked when an audit-log write
    fails. The handler receives the log_event failure result dict.

    ECHO does not print. Presentation of the failure is the caller's
    responsibility at the orchestrator/presentation boundary.
    """

    global _failure_handler
    _failure_handler = handler


def _notify_failure(result):
    handler = _failure_handler

    if handler is None:
        return

    # A failing handler must never abort the recovery workflow either.
    try:
        handler(result)
    except Exception:
        pass


def read_audit_log(recovery_path):
    """
    Read persisted audit-log lines for a recovery case.

    Read-only. Does not parse or interpret log entries.
    """

    log_path = Path(recovery_path) / "audit.log"

    if not log_path.is_file():
        return []

    try:
        content = log_path.read_text(encoding="utf-8")
    except OSError as error:
        raise AuditLogError(
            f"audit.log could not be read: {log_path}"
        ) from error
    except UnicodeDecodeError as error:
        raise AuditLogError(
            f"audit.log is malformed: {log_path}"
        ) from error

    if not content:
        return []

    return content.splitlines()


def log_event(session, module, level, event):
    """
    Write an audit event to the recovery session log.

    ECHO records what happened. ECHO does not decide, assess, or recover.

    Fail-safe: a filesystem or encoding failure while opening, writing,
    flushing, or closing audit.log never raises and never interrupts the
    caller's recovery workflow. ECHO does not create directories and never
    writes anywhere other than the case audit.log.

    Returns a result dict:
      {"success": True, "code": "AUDIT_LOG_WRITTEN"}
      {"success": False, "code": "AUDIT_LOG_WRITE_FAILED",
       "recovery_path": <str>, "detail": <str>}
    """

    log_path = Path(session.recovery_path) / "audit.log"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{timestamp} [{module}][{level}] {event}\n"

    try:
        with open(log_path, "a", encoding="utf-8") as file:
            file.write(line)
            file.flush()
    except (OSError, UnicodeError) as error:
        result = {
            "success": False,
            "code": CODE_AUDIT_LOG_WRITE_FAILED,
            "recovery_path": str(session.recovery_path),
            "detail": str(error),
        }
        _notify_failure(result)
        return result

    return {
        "success": True,
        "code": CODE_AUDIT_LOG_WRITTEN,
    }


def log_info(session, module, event):
    return log_event(session, module, "INFO", event)


def log_warning(session, module, event):
    return log_event(session, module, "WARNING", event)


def log_error(session, module, event):
    return log_event(session, module, "ERROR", event)


def log_critical(session, module, event):
    return log_event(session, module, "CRITICAL", event)


def log_operator(session, module, event):
    return log_event(session, module, "OPERATOR", event)
