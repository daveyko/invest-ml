"""Model training entry point.

The ML model type and hyperparameters are not yet chosen.
This module will wrap sklearn/xgboost/lightgbm or similar once decided.
"""

from invest_ml.db.models.modeling import ModelRun, TrainingDataset


def train_model(
    dataset: TrainingDataset,
    model_name: str,
    model_version: str,
    hyperparameters: dict,
    artifact_dir: str,
    git_sha: str,
) -> ModelRun:
    """Train a model on dataset and write the artifact to artifact_dir.

    TODO: implement model training, serialization, and artifact hashing.
    """
    raise NotImplementedError("TODO: implement model training")
