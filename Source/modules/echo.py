from datetime import datetime
from pathlib import Path


class AuditLogError(Exception):
    """Raised when audit.log cannot be read."""


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

    ECHO records what happened.
    ECHO does not decide, assess, or recover.
    """

    log_path = Path(session.recovery_path) / "audit.log"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(log_path, "a") as file:
        file.write(f"{timestamp} [{module}][{level}] {event}\n")

def log_info(session, module, event):
    log_event(session, module, "INFO", event)


def log_warning(session, module, event):
    log_event(session, module, "WARNING", event)


def log_error(session, module, event):
    log_event(session, module, "ERROR", event)


def log_critical(session, module, event):
    log_event(session, module, "CRITICAL", event)

def log_operator(session, module, event):
    log_event(session, module, "OPERATOR", event)