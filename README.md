# invest-ml

Point-in-time public-company investing ML pipeline backed by Dagster, SQLAlchemy, and PostgreSQL.

---

## Universe mental model

```
All companies            SEC submissions — lightweight metadata for every CIK
       │
Candidate universe       Current US-listed, operating companies worth profiling
       │                 (Nasdaq / NYSE / NYSE American, recent filing, no ETF/SPAC/shell)
       │
       ├──► Training universe    Broad companies with sufficient financial + market history
       │                         Used for model training.  Typically thousands of companies.
       │
       └──► Scoring universe     Narrower AI/crypto/software/semiconductor/fintech/
                                 automation/networking/data-center/power companies,
                                 plus always_include tickers (NVDA, MSFT, AMZN, …)
```

### Why four layers?

* **All companies** → cheap; store only lightweight SEC metadata (name, CIK, SIC, ticker).
* **Candidate** → filter to investable, US-listed, operating companies before profiling.
* **Training** → ensure data quality (annual periods, price history, metric coverage, liquidity).
  Broad to reduce hindsight selection bias.
* **Scoring** → restrict predictions to the sectors where the model has an edge.

---

## Architecture

```
all SEC companies
→ lightweight company catalog
→ CompanyFacts data profiles        (lightweight scan, no raw payload stored)
→ candidate universe
→ market profiles                   (price/liquidity scan per candidate security)
→ training universe / scoring universe
→ selected raw CompanyFacts         (only universe members get deep persistence)
→ flattened XBRL facts
→ canonical metrics                 (point-in-time: available_at ≤ as_of_date)
→ prices
→ immutable point-in-time feature snapshots
→ labels                            (realized future returns — may use future data)
→ frozen dataset membership
→ trained models
→ predictions
```

### Persistence boundaries

| Stage | Storage |
|-------|---------|
| All companies | `companies`, `securities` |
| Broad profiling | `company_data_profiles`, `company_market_profiles` |
| Universe membership | `universe_definitions`, `universe_memberships` |
| Selected financials | `raw_source_versions`, `xbrl_facts`, `canonical_metrics` |
| Prices | `price_bars` |
| Features | `feature_definitions`, `feature_set_definitions`, `feature_snapshots` |
| Labels | `target_specs`, `labels` |
| Datasets | `training_datasets`, `training_dataset_rows` |
| Models | `model_runs`, `predictions` |

**Why profiling precedes heavy persistence:**  
The full CompanyFacts archive contains ~10,000+ companies.  Storing raw payloads
and XBRL facts for all of them before knowing which companies are worth training
on would be expensive and would couple storage to the profiling stage.  Instead,
a lightweight scan produces a `CompanyDataProfile` row (a few hundred bytes per
company) which is then used to gate universe membership.  Only after a company
passes universe selection does its raw payload get written to `raw_source_versions`.

---

## Point-in-time leakage rules

1. All financial data selection uses `available_at ≤ as_of_date` (not today's date).
2. Feature snapshots are **immutable** after creation.
3. If upstream data is corrected, a **new snapshot** is inserted with a new `source_lineage_hash`.
4. A changed feature formula requires a new `feature_version`.
5. A changed feature set member list requires a new `feature_set_version`.
6. **Labels may use future data**; feature construction may not.
7. Dataset rows point to exact immutable feature snapshot and label IDs.
8. Parquet artifacts are optional materializations — the DB rows are the source of truth.
9. Filtering is based on data quality and investability, **not** hindsight business quality.

---

## Feature / feature-set / snapshot / dataset / model version lineage

```
FeatureDefinition (name="return_6m", version="v1")
    │
FeatureSetDefinition (name="invest_ml_v1", version="v1", members=[…])
    │   content_hash over sorted members list — guards against silent drift
    │
FeatureSnapshot (company_id, security_id, as_of_date, feature_set_id, source_lineage_hash)
    │   immutable — new upstream data → new row, not UPDATE
    │
TrainingDataset (rows → FeatureSnapshot + Label pairs)
    │   content_hash over sorted row IDs
    │
ModelRun (trained_at, artifact_hash, status: candidate → promoted)
    │
Prediction (probability, prediction_date)
```

---

## Local setup

```bash
# 1. Install dependencies
uv sync

# 2. Copy and fill in .env
cp .env.example .env
# Edit DATABASE_URL and SEC_USER_AGENT

# 3. Create the PostgreSQL database
createdb invest_ml

# 4. Run migrations
uv run alembic upgrade head

# 5. Start Dagster UI
DAGSTER_HOME=.dagster uv run dagster dev

# 6. Run tests
uv run pytest

# 7. Lint
uv run ruff check .

# 8. Type check
uv run mypy src
```

---

## Project tree

```
invest-ml/
├── src/invest_ml/
│   ├── definitions.py            # Dagster Definitions entry point
│   ├── utils.py                  # Hashing + point-in-time guards
│   │
│   ├── defs/
│   │   ├── resources.py          # PostgresResource, SecBulkResource, ArtifactStoreResource
│   │   ├── jobs.py               # 5 define_asset_job definitions
│   │   ├── schedules.py          # 5 ScheduleDefinition (all STOPPED)
│   │   └── assets/
│   │       ├── discovery.py      # company_catalog → scoring_universe
│   │       ├── financials.py     # selected_companyfacts_raw → canonical_metrics
│   │       ├── market.py         # price_bars
│   │       ├── features.py       # feature_registry, feature_snapshots
│   │       └── modeling.py       # matured_labels → current_predictions
│   │
│   ├── config/
│   │   ├── settings.py           # Pydantic Settings
│   │   └── loaders.py            # YAML loaders
│   │
│   ├── db/
│   │   ├── base.py               # DeclarativeBase
│   │   ├── session.py            # build_engine, session_scope
│   │   ├── models/               # 21 SQLAlchemy ORM models
│   │   └── repositories/
│   │       ├── protocols.py      # Protocol interfaces (12 repositories)
│   │       └── sqlalchemy.py     # SQLAlchemy implementations (all NotImplementedError)
│   │
│   ├── sec/                      # SEC EDGAR service stubs
│   ├── market/                   # Market data service stubs
│   ├── universe/                 # Universe builder stubs
│   ├── features/                 # Feature builder stubs
│   └── modeling/                 # Label / dataset / trainer / scorer stubs
│
├── configs/
│   ├── sic_buckets_v1.yaml       # SIC → model_bucket mapping
│   ├── universe_v1.yaml          # Candidate / training / scoring criteria
│   ├── canonical_metrics_v1.yaml # XBRL tag → canonical metric mapping
│   ├── features_v1.yaml          # 18 individual features + 1 feature set
│   └── target_v1.yaml            # 12-month / 15% return threshold
│
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_initial_schema.py  # All 21 tables
│
├── tests/
│   ├── test_definitions.py       # Dagster Definitions load + asset/job/schedule checks
│   ├── test_models.py            # SQLAlchemy metadata + DDL compile + constraint checks
│   ├── test_configs.py           # YAML validation + cross-file consistency
│   └── test_utils.py             # Hashing + point-in-time guard tests
│
├── var/                          # Gitignored local artifacts
│   ├── raw/
│   ├── datasets/
│   └── models/
│
├── .env.example
├── alembic.ini
└── pyproject.toml
```

---

## Asset graph

```
company_catalog ──► companyfacts_data_profiles ──► candidate_universe
                                                           │
                                              ┌────────────┤
                                              │            │
                                    company_market_profiles│
                                              │            │
                                    training_universe   scoring_universe
                                              │
                              ┌───────────────┤
                              │               │
                  selected_companyfacts_raw   price_bars
                              │               │
                          xbrl_facts          │
                              │               │
                      canonical_metrics       │
                              │               │
                              └───────┬───────┘
                                      │
                        feature_registry ──► feature_snapshots
                                                    │
                                      ┌─────────────┤
                                      │             │
                              matured_labels   (→ training_dataset → trained_model)
                                                          │
                                                 current_predictions
                                                 (uses scoring_universe + feature_snapshots)
```

---

## Commands

```bash
uv sync                          # Install all dependencies
uv run alembic upgrade head      # Apply migrations (requires live DB)
uv run dagster dev               # Open Dagster UI at http://localhost:3000
uv run pytest                    # Run test suite (no DB required)
uv run ruff check .              # Lint
uv run mypy src                  # Type check
```

---

## Intentionally unimplemented (placeholders)

Every service module raises `NotImplementedError` at runtime. The definitions and tests all pass without a database or network connection.

| Module | First implementation task |
|--------|--------------------------|
| `sec/client.py` | Streaming download of submissions.zip |
| `sec/archive.py` | ZIP extraction of per-CIK JSON files |
| `sec/profiler.py` | CompanyFacts tag scanning → CompanyDataProfile |
| `sec/parser.py` | CompanyFacts JSON → XbrlFact rows |
| `sec/normalizer.py` | XbrlFacts → CanonicalMetric with TTM aggregation |
| `market/client.py` | Choose provider (yfinance / Polygon / Tiingo) |
| `market/profiler.py` | Dollar volume + missing-day ratio from price bars |
| `universe/builder.py` | Filter companies against YAML criteria |
| `features/definitions.py` | YAML → FeatureDefinition DB rows |
| `features/builder.py` | Per-feature calculation with assert_no_lookahead |
| `modeling/labels.py` | Realized return calculation from price bars |
| `modeling/dataset.py` | Snapshot–label joining + split assignment |
| `modeling/trainer.py` | ML algorithm not yet chosen |
| `modeling/evaluator.py` | AUC / precision / recall on held-out split |
| `modeling/scorer.py` | Load artifact, produce probabilities |
| `db/repositories/sqlalchemy.py` | All 12 repository methods |

**First recommended implementation task:**  
`sec/client.py` + `sec/archive.py` → download and extract submissions.zip → `sec/profiler.py` → populate `companies`, `securities`, and `company_data_profiles`.
