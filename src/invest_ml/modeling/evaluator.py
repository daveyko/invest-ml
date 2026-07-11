"""Model evaluation metrics computation."""

from invest_ml.db.models.modeling import ModelRun


def evaluate_model(model_run: ModelRun, test_snapshots: list, test_labels: list) -> dict:
    """Compute evaluation metrics (AUC, precision, recall, etc.).

    TODO: implement metric computation against held-out test split.
    """
    raise NotImplementedError("TODO: implement model evaluation")
