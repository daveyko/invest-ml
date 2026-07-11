# Import all model modules so SQLAlchemy registers them on Base.metadata before
# Alembic or DDL compilation runs.  Order matters for FK resolution.
from invest_ml.db.models import (  # noqa: F401
    ingestion,
    company,
    classification,
    profiling,
    universe,
    financials,
    market,
    features,
    modeling,
)
