"""Editable configuration for the SAM bid evaluator, stored in the DB so the
kill-word / allowed-service / excluded-service lists can be managed from the UI
without editing config files or restarting the server.

Ported 1:1 from the sam-septa `eval_config` table onto the hub's SQLAlchemy Base.
The SAM evaluator (server/scrappers/sam/evaluator.py) reads the `kill_word` rows
live on every bid; the `allowed_service` / `excluded_service` rows are the
editable reference catalogue surfaced in the Evaluator Settings panel.
"""

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# Category values (kept identical to the sam-septa routes/eval_config.py).
CATEGORY_KILL_WORD = "kill_word"
CATEGORY_EXCLUDED_SERVICE = "excluded_service"
CATEGORY_ALLOWED_SERVICE = "allowed_service"


class EvalConfig(Base):
    __tablename__ = "eval_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # kill_word | excluded_service | allowed_service
    category: Mapped[str] = mapped_column(String(30), index=True)
    # The actual string, stored lowercase for consistent matching (e.g. "idiq").
    value: Mapped[str] = mapped_column(String(200), index=True)
