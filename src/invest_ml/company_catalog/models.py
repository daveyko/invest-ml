"""Result types for the CompanyCatalogService."""

from dataclasses import dataclass, field


@dataclass
class CompanyCatalogResult:
    """Aggregate statistics from one catalog refresh run."""

    companies_seen: int = 0
    companies_inserted: int = 0
    companies_updated: int = 0
    securities_inserted: int = 0
    securities_updated: int = 0
    sic_classifications_inserted: int = 0
    parse_warnings: list[str] = field(default_factory=list)
    malformed_records: int = 0
