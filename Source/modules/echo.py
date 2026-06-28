from datetime import datetime
from pathlib import Path


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