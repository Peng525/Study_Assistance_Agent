"""Best-effort, admin-only audit storage for learner LLM calls."""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.models import LLMCallLog


logger = logging.getLogger(__name__)
MAX_LLM_CALL_LOGS = 500


def create_call_log(bind, **values) -> int | None:
    """Create one record independently; audit failures must not break chat."""
    audit_db = Session(bind=bind)
    request_id = values.get("request_id", "unknown")
    try:
        if values.get("status") != "running" and "completed_at" not in values:
            values["completed_at"] = datetime.now(timezone.utc)
        record = LLMCallLog(**values)
        audit_db.add(record)
        audit_db.flush()
        stale_ids = [
            row[0]
            for row in (
                audit_db.query(LLMCallLog.id)
                .order_by(LLMCallLog.id.desc())
                .offset(MAX_LLM_CALL_LOGS)
                .all()
            )
        ]
        if stale_ids:
            audit_db.query(LLMCallLog).filter(LLMCallLog.id.in_(stale_ids)).delete(
                synchronize_session=False
            )
        audit_db.commit()
        return record.id
    except Exception:
        audit_db.rollback()
        # Do not attach the database exception: SQLAlchemy errors may contain
        # bound prompt or answer values.
        logger.error("LLM audit create failed request_id=%s", request_id)
        return None
    finally:
        audit_db.close()


def update_call_log(bind, log_id: int | None, **values) -> None:
    """Finish one record without leaking prompt or answer into ordinary logs."""
    if log_id is None:
        return
    audit_db = Session(bind=bind)
    try:
        record = audit_db.get(LLMCallLog, log_id)
        if record is None:
            return
        for key, value in values.items():
            setattr(record, key, value)
        record.completed_at = datetime.now(timezone.utc)
        audit_db.add(record)
        audit_db.commit()
    except Exception:
        audit_db.rollback()
        logger.error("LLM audit update failed log_id=%s", log_id)
    finally:
        audit_db.close()
