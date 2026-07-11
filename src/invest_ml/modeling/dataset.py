"""Build the logical training dataset definition and row memberships."""

from invest_ml.db.models.modeling import TrainingDataset, TrainingDatasetRow


def build_dataset(
    name: str,
    version: str,
    universe_id: str,
    feature_set_id: str,
    target_spec_id: str,
    snapshots: list,
    labels: list,
    build_config: dict,
) -> tuple[TrainingDataset, list[TrainingDatasetRow]]:
    """Pair feature snapshots with matured labels and assign train/val/test splits.

    TODO: implement snapshot-label joining, split assignment, and content_hash.
    """
    raise NotImplementedError("TODO: implement dataset construction")
