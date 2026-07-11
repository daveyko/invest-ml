"""Dagster schedule definitions.

All schedules are STOPPED by default.  They are scaffolding that documents
the intended cadence but must not fire until the underlying assets are
implemented.  Enable each schedule in the Dagster UI after verifying the
corresponding job runs cleanly end-to-end.

Timezone: America/New_York throughout.
"""

from dagster import DefaultScheduleStatus, ScheduleDefinition

from invest_ml.defs.jobs import (
    feature_scoring_job,
    market_refresh_job,
    model_training_job,
    sec_discovery_job,
    selected_financials_job,
)

_TZ = "America/New_York"
_STOPPED = DefaultScheduleStatus.STOPPED

# SEC publishes the nightly archive around midnight ET.
# Run at 06:00 ET to give the archive time to stabilise.
sec_discovery_schedule = ScheduleDefinition(
    name="sec_discovery_schedule",
    cron_schedule="0 6 * * *",
    job=sec_discovery_job,
    execution_timezone=_TZ,
    default_status=_STOPPED,
    description="[SCAFFOLDING] Daily SEC submissions refresh at 06:00 ET.",
)

# Financial normalisation runs after SEC discovery completes.
selected_financials_schedule = ScheduleDefinition(
    name="selected_financials_schedule",
    cron_schedule="0 8 * * *",
    job=selected_financials_job,
    execution_timezone=_TZ,
    default_status=_STOPPED,
    description="[SCAFFOLDING] Daily financial normalisation at 08:00 ET.",
)

# US markets close at 16:00 ET; price bars are available ~30 min later.
# Run market refresh at 17:00 ET on weekdays.
market_refresh_schedule = ScheduleDefinition(
    name="market_refresh_schedule",
    cron_schedule="0 17 * * 1-5",
    job=market_refresh_job,
    execution_timezone=_TZ,
    default_status=_STOPPED,
    description="[SCAFFOLDING] Weekday market refresh at 17:00 ET after market close.",
)

# Feature scoring runs after market refresh.
feature_scoring_schedule = ScheduleDefinition(
    name="feature_scoring_schedule",
    cron_schedule="0 18 * * 1-5",
    job=feature_scoring_job,
    execution_timezone=_TZ,
    default_status=_STOPPED,
    description="[SCAFFOLDING] Weekday feature snapshot + scoring at 18:00 ET.",
)

# Monthly retraining on the first Sunday of each month at 02:00 ET.
model_training_schedule = ScheduleDefinition(
    name="model_training_schedule",
    cron_schedule="0 2 1-7 * 0",
    job=model_training_job,
    execution_timezone=_TZ,
    default_status=_STOPPED,
    description="[SCAFFOLDING] Monthly model retraining (first Sunday at 02:00 ET).",
)

all_schedules = [
    sec_discovery_schedule,
    selected_financials_schedule,
    market_refresh_schedule,
    feature_scoring_schedule,
    model_training_schedule,
]
