"""Repository for feature_definitions, feature_set_definitions, and feature_set_members."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from invest_ml.db.models.features import FeatureDefinition, FeatureSetDefinition, FeatureSetMember
from invest_ml.db.models.ingestion import IngestionRun

logger = logging.getLogger(__name__)


class FeatureRegistryRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    # ── Ingestion run ──────────────────────────────────────────────────────────

    def create_ingestion_run(
        self,
        *,
        source: str,
        source_uri: str,
        started_at: datetime,
    ) -> IngestionRun:
        run = IngestionRun(
            source=source,
            source_uri=source_uri,
            started_at=started_at,
            status="running",
        )
        self._s.add(run)
        self._s.flush()
        return run

    def succeed_ingestion_run(
        self,
        run_id: UUID,
        *,
        entities_checked: int = 0,
        entities_changed: int = 0,
        extra_metadata: dict | None = None,
    ) -> None:
        self._s.execute(
            update(IngestionRun)
            .where(IngestionRun.run_id == run_id)
            .values(
                status="succeeded",
                completed_at=datetime.now(tz=UTC),
                entities_checked=entities_checked,
                entities_changed=entities_changed,
                run_metadata=extra_metadata or {},
            )
        )

    def fail_ingestion_run(self, run_id: UUID, *, error: str) -> None:
        self._s.execute(
            update(IngestionRun)
            .where(IngestionRun.run_id == run_id)
            .values(
                status="failed",
                completed_at=datetime.now(tz=UTC),
                error=error,
            )
        )

    # ── Feature definitions ────────────────────────────────────────────────────

    def find_feature_definition(
        self, feature_name: str, feature_version: str
    ) -> FeatureDefinition | None:
        return self._s.execute(
            select(FeatureDefinition).where(
                FeatureDefinition.feature_name == feature_name,
                FeatureDefinition.feature_version == feature_version,
            )
        ).scalar_one_or_none()

    def create_feature_definition(self, row: dict) -> FeatureDefinition:
        fd = FeatureDefinition(**row)
        self._s.add(fd)
        self._s.flush()
        return fd

    # ── Feature set definitions ────────────────────────────────────────────────

    def find_feature_set(
        self, name: str, version: str
    ) -> FeatureSetDefinition | None:
        return self._s.execute(
            select(FeatureSetDefinition).where(
                FeatureSetDefinition.name == name,
                FeatureSetDefinition.version == version,
            )
        ).scalar_one_or_none()

    def create_feature_set(self, row: dict) -> FeatureSetDefinition:
        fsd = FeatureSetDefinition(**row)
        self._s.add(fsd)
        self._s.flush()
        return fsd

    # ── Feature set members ────────────────────────────────────────────────────

    def count_feature_set_members(self, feature_set_id: UUID) -> int:
        rows = self._s.execute(
            select(FeatureSetMember).where(
                FeatureSetMember.feature_set_id == feature_set_id
            )
        ).scalars().all()
        return len(rows)

    def create_feature_set_members(
        self,
        members: list[dict],
    ) -> int:
        if not members:
            return 0
        self._s.execute(
            pg_insert(FeatureSetMember).on_conflict_do_nothing(
                index_elements=["feature_set_id", "feature_definition_id"]
            ),
            members,
        )
        return len(members)
