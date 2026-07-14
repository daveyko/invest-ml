"""Generate predictions from a promoted model for the scoring universe."""

from datetime import date

from invest_ml.db.models.modeling import ModelRun, Prediction


def score_universe(
    model_run: ModelRun,
    snapshots: list,
    prediction_date: date,
    artifact_dir: str,
) -> list[Prediction]:
    """Load a model artifact and produce predictions for all provided snapshots.

    TODO: implement artifact loading and probability scoring.
    """
    raise NotImplementedError("TODO: implement model scoring")
