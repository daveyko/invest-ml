"""Universe configuration types.

CandidateUniverseConfig is the parsed, validated form of the candidate section
of universe_v1.yaml.  criteria_hash() produces a deterministic SHA-256 so that
the UniverseDefinition row can detect when the config has changed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class CandidateUniverseConfig:
    name: str
    version: str
    supported_exchanges: tuple[str, ...]
    exchange_aliases: dict[str, str]
    allowed_entity_types: tuple[str, ...]
    excluded_exact_entity_types: tuple[str, ...]
    exclude_missing_entity_type: bool
    require_current_ticker: bool
    require_company_data_profile: bool
    recent_filing_months: int
    exclude_missing_recent_filing: bool
    excluded_sic_codes: frozenset[str]
    excluded_name_patterns: tuple[str, ...]
    always_exclude_ciks: frozenset[str]
    always_include_ciks: frozenset[str]
    profile_version: str

    @classmethod
    def from_dict(
        cls, raw: dict, profile_version: str | None = None
    ) -> CandidateUniverseConfig:
        return cls(
            name=raw.get("name", "candidate"),
            version=str(raw.get("version", "v1")),
            supported_exchanges=tuple(raw.get("supported_exchanges", [])),
            exchange_aliases=dict(raw.get("exchange_aliases", {})),
            allowed_entity_types=tuple(raw.get("allowed_entity_types", ["operating"])),
            excluded_exact_entity_types=tuple(raw.get("excluded_exact_entity_types", [])),
            exclude_missing_entity_type=bool(raw.get("exclude_missing_entity_type", True)),
            require_current_ticker=bool(raw.get("require_current_ticker", True)),
            require_company_data_profile=bool(raw.get("require_company_data_profile", True)),
            recent_filing_months=int(raw.get("recent_filing_months", 18)),
            exclude_missing_recent_filing=bool(raw.get("exclude_missing_recent_filing", True)),
            excluded_sic_codes=frozenset(str(s) for s in raw.get("excluded_sic_codes", [])),
            excluded_name_patterns=tuple(raw.get("excluded_name_patterns", [])),
            always_exclude_ciks=frozenset(str(c) for c in raw.get("always_exclude_ciks", [])),
            always_include_ciks=frozenset(str(c) for c in raw.get("always_include_ciks", [])),
            profile_version=profile_version
            or raw.get("profile_version", "companyfacts_profile_v1"),
        )

    def criteria_hash(self) -> str:
        """SHA-256 of a deterministic canonical representation of filter-relevant fields."""
        relevant = {
            "supported_exchanges": sorted(self.supported_exchanges),
            "exchange_aliases": dict(sorted(self.exchange_aliases.items())),
            "allowed_entity_types": sorted(self.allowed_entity_types),
            "excluded_exact_entity_types": sorted(self.excluded_exact_entity_types),
            "exclude_missing_entity_type": self.exclude_missing_entity_type,
            "require_current_ticker": self.require_current_ticker,
            "require_company_data_profile": self.require_company_data_profile,
            "recent_filing_months": self.recent_filing_months,
            "exclude_missing_recent_filing": self.exclude_missing_recent_filing,
            "excluded_sic_codes": sorted(self.excluded_sic_codes),
            "excluded_name_patterns": list(self.excluded_name_patterns),
            "profile_version": self.profile_version,
        }
        return hashlib.sha256(
            json.dumps(relevant, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def to_criteria_dict(self) -> dict:
        """Serialize to a JSONB-safe dict for storage in universe_definitions.criteria."""
        return {
            "name": self.name,
            "version": self.version,
            "supported_exchanges": list(self.supported_exchanges),
            "exchange_aliases": dict(self.exchange_aliases),
            "allowed_entity_types": list(self.allowed_entity_types),
            "excluded_exact_entity_types": list(self.excluded_exact_entity_types),
            "exclude_missing_entity_type": self.exclude_missing_entity_type,
            "require_current_ticker": self.require_current_ticker,
            "require_company_data_profile": self.require_company_data_profile,
            "recent_filing_months": self.recent_filing_months,
            "exclude_missing_recent_filing": self.exclude_missing_recent_filing,
            "excluded_sic_codes": sorted(self.excluded_sic_codes),
            "excluded_name_patterns": list(self.excluded_name_patterns),
            "always_exclude_ciks": sorted(self.always_exclude_ciks),
            "always_include_ciks": sorted(self.always_include_ciks),
            "profile_version": self.profile_version,
        }
