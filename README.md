# Ops Agent (In Progress)

**Autonomous business operations engine for food-service retailers.**

Ops Agent is a headless, multi-tenant backend system that forecasts demand, manages inventory, generates supplier orders, schedules staff, and detects anomalies — progressively earning autonomous execution rights through tracked performance. It replaces the operational overhead that keeps small and mid-size food-service operators dependent on spreadsheets, gut-feel ordering, and reactive staffing.

The system is POS-agnostic, vertically templated, and designed to run unattended in a containerized environment. CLI control surface and structured email reports — no GUI dependency.

---

## Architecture

```
PRODUCERS (announce)                          CONSUMERS (decide/act)
─────────────────────                         ──────────────────────
scheduler           ── day-open/close,        inventory engine (BOM explosion + ledger)
                       checkpoints, weekly    forecast engine       → quantile forecasts + metrics
poller               ── coarse wake-ups       ordering engine       → proposals / autonomous actions
pos-sync             ── sale/labor events     scheduling engine     → CP-SAT schedule proposals
weather              ── forecast snapshots    anomaly engine        → deduplicated, explained alerts
CLI                  ── operator actions      learning engine       → correction factors, weekly refit
                       (counts, approvals,    notifier              → email via outbox;
                       receipts, feedback,                            re-validates autonomy bounds
                       grants)                                        independently

                    Redis Streams — tenant_id in every event envelope
                    PostgreSQL — state, ledgers, provenance, metrics
```

**Core design principle:** producers announce facts, consumers make decisions. No judgment lives in a producer. Every consumer is idempotent — replay the same events and get the same outcome.

### Key Architectural Decisions

- **Forecast model:** top-down 3-layer hierarchy. A Poisson/Tweedie GLM predicts daily totals from weather and calendar features. Exponentially-decayed share vectors decompose totals to SKU level. Intraday profiles distribute across hours. Direct per-SKU models are rejected — a single location produces ~1 observation per series per day; top-down pooling is the only approach that works at this data scale.

- **Reordering:** analytic order-up-to policy on forecast quantiles over irregular supplier delivery horizons. The forecast's prediction intervals *are* the safety stock — no separate heuristic, no RL, no optimizer.

- **Scheduling:** OR-Tools CP-SAT with soft coverage constraints. Coverage shortfalls are native slack variables with diagnostic re-solves for root-cause reporting.

- **Anomaly detection:** forecast residuals + deterministic hard rules. Templated natural-language evidence sentences rendered from computed values. No unsupervised models.

- **Autonomy:** deterministic state machine with per-supplier capability scoping. Bounds enforced independently at the proposer and the executor. Spend ledger, novelty gate, no payment credentials. The system's only real-world actuator is email.

- **Learning:** bounded correction factors with exponential decay and clamped safety rails. A daily fast path (bias corrections) and a weekly slow path (model refit gated by rolling-origin backtest with champion/challenger promotion). Every factor change is a dated, reversible audit row.

### Multi-Tenancy

Tenant scoping via `tenant_id` on every table and a required context object in every repository call. Per-tenant config resolution through a 3-layer cascade: tenant overrides → vertical template defaults → global defaults with deep-merge semantics.

### POS Integration

POS-agnostic adapter pattern. The system defines an internal sale event schema; any POS system plugs in via a thin adapter that translates its API response into the internal format. Adding a new POS integration requires one adapter module — zero changes to forecasting, inventory, ordering, or anomaly detection.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| Database | PostgreSQL 16 (SQLAlchemy 2.0 + Alembic) |
| Event bus | Redis Streams |
| Validation / Config | Pydantic v2 |
| Forecasting | scikit-learn (Poisson/Tweedie GLM), LightGBM (challenger) |
| Scheduling | Google OR-Tools CP-SAT |
| Weather data | Open-Meteo |
| CLI | Typer + Rich |
| Email | smtplib + Jinja2, transactional outbox pattern |
| Logging | structlog |
| Package management | uv |
| Testing | pytest + time-machine |
| Containerization | Docker Compose |

---

## Data Model

The schema spans 11 domains, all scoped by `tenant_id` with composite unique constraints:

- **Core** — tenants, versioned vertical templates (schema-validated JSONB), sparse tenant config overrides
- **Catalog** — POS-mirrored items and modifiers, BOM lines (sale item → inventory depletion mapping), unmapped-item detection
- **Inventory** — transaction ledger, physical counts with discrepancy tracking, materialized positions (on-hand / on-order)
- **Suppliers / Orders** — supplier profiles with delivery calendars and cutoffs, full purchase order lifecycle with event sourcing
- **Workforce** — employees, certifications, availability rules, schedules, shifts, edit capture with deltas
- **Forecasting** — quantile grid forecasts with model version and feature snapshots, actuals, daily metric rollups including baselines
- **Anomalies** — typed alerts with severity tiers, dedup keys, cooldowns, evidence payloads, operator feedback
- **Autonomy** — per-supplier capability states, decision log with full provenance, spend ledger, promotion/demotion audit trail
- **Learning** — correction factors with clamp bounds, decay, and change history; model registry; backtest results
- **Comms** — email outbox with idempotency keys, render snapshots, retry tracking; per-supplier templates
- **Ops** — heartbeats, sync cursors, job logs

Money and physical quantities use `Decimal` end-to-end in ledgers and orders. The modeling layer converts to float at an explicit boundary module.

---

## Reliability

This system manages real inventory and sends real supplier orders. Reliability is non-negotiable.

- **Transactional outbox** — database row written first (with idempotency key), sender marks sent on successful dispatch. Crash-safe with no duplicate supplier orders. Global dry-run mode is default-on until explicit go-live.
- **Executor-side re-validation** — the notifier independently re-checks autonomy bounds, spend ledger, and novelty gates before sending. A bug upstream cannot cause an out-of-bounds action.
- **Heartbeats + self-monitoring** — every producer/consumer writes heartbeats; staleness raises tier-1 alerts. Daily summary includes system health.
- **Dead-man's switch** — an independent process monitors summary delivery and escalates on silence.
- **Idempotent consumers** — replay produces identical state. Enables safe crash recovery and powers the backtest/replay harness.
- **POS reconciliation** — nightly comparison of ingested totals vs POS-reported totals catches silent sync drift.

---

## Earned Autonomy

The system starts in propose-only mode for every capability and earns autonomous execution rights per supplier through tracked performance metrics: proposal count, approval rate, edit magnitude, rejection streaks, downstream outcomes, and reversal rate.

Promotion is asymmetric by design. When configurable performance gates pass, the system proposes its own promotion with evidence attached — the operator grants via CLI. Demotion is automatic on performance regression. Revocation is immediate at any time.

Structural safety: no payment credentials, executor-enforced spend caps, novelty escalation, idempotent outbox. The worst reachable state is bounded excess inventory within configured caps.

---

## Virtual Clock & Replay

All time flows through an injectable clock module. The scheduler supports a virtual-time mode that replays historical dates through the production engine pipeline. This powers:

- **Backtesting** — rolling-origin evaluation of forecast models against held-out history
- **Hindsight reports** — retroactive analysis over client historical data as an onboarding deliverable
- **Integration testing** — simulated full operational weeks with zero manual intervention
- **Time-dependent logic testing** — day-close, weekly refit, autonomy promotion without calendar delay

---

## Project Structure

```
src/
├── adapters/           # POS adapter interface (ABC) + implementations
├── clock.py            # Injectable clock (virtual time support)
├── consumers/          # Redis Streams consumers (event-driven engines)
├── db/
│   ├── models.py       # SQLAlchemy models (all tenant-scoped)
│   └── session.py      # Engine + session factory
├── logging.py          # structlog configuration
├── producers/          # Event producers (scheduler, poller)
├── schemas/            # Pydantic models (events, config, validation)
├── scripts/            # Seeding, data generation, maintenance
└── services/           # Business logic layer
    ├── config_service.py    # 3-layer config resolution with deep merge
    ├── template_service.py  # Template versioning
    └── tenant_service.py    # Tenant lifecycle

alembic/                # Database migrations
docker-compose.yml      # PostgreSQL + Redis
```

---

## Getting Started

### Prerequisites

- Python 3.12+
- Docker and Docker Compose
- [uv](https://github.com/astral-sh/uv)

### Setup

```bash
# Start services
docker compose up -d

# Install dependencies
uv sync

# Run migrations
uv run alembic upgrade head

# Seed development data
uv run python -m src.scripts.seed
```

### Running Tests

```bash
uv run pytest
```

### CLI

```bash
# Tenant management
uv run python -m src.cli tenant create --name "Shop" --location "City"

# Inventory operations
uv run python -m src.cli count enter
uv run python -m src.cli delivery receive

# Ordering
uv run python -m src.cli proposals list
uv run python -m src.cli proposals approve <id>

# Autonomy
uv run python -m src.cli autonomy status
uv run python -m src.cli autonomy grant <supplier-id>

# Diagnostics
uv run python -m src.cli explain <decision-id>
uv run python -m src.cli metrics forecast
uv run python -m src.cli factors list
```

---

## Configuration

The system uses environment variables for infrastructure configuration:

| Variable | Description | Default |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string | — |
| `REDIS_URL` | Redis connection string | — |
| `SMTP_HOST` | SMTP relay host | — |
| `SMTP_PORT` | SMTP relay port | `587` |
| `SMTP_USER` | SMTP authentication user | — |
| `SMTP_PASSWORD` | SMTP authentication password | — |
| `DRY_RUN` | Suppress all outbound email | `true` |
| `LOG_LEVEL` | structlog output level | `INFO` |

Business configuration (inventory parameters, anomaly thresholds, autonomy bounds, scheduling rules) lives in the vertical template system and is managed per-tenant through the CLI.

---

## Coming Soon...

### Operator Dashboard
Web-based interface replacing the CLI as the primary control surface. Real-time inventory positions, forecast visualizations, proposal approval workflows, anomaly feeds, and autonomy status — all read/write against the existing engine API. The architecture is headless by design; the dashboard is a skin, not a restructure.

### Multi-Location Intelligence
Organization layer above tenants for operators managing 3–12 locations. Consolidated supplier ordering with volume-break optimization across sites. Cross-location staff floating (shared availability + certification awareness). Pooled demand forecasting — a new product launched at one location primes predictions at the others instead of cold-starting. Unified reporting across the full portfolio.

### Self-Service Onboarding
Web-based signup flow with POS OAuth integration. Tenant provisioning, historical data backfill, and automated hindsight report generation — the current CLI onboarding sequence exposed as a guided workflow. Includes POS credential management and template selection.

### Additional POS Integrations
Adapter implementations for Clover, Toast, Lightspeed, and other major POS platforms. Each integration is a single adapter module translating vendor API responses into the internal sale event schema. Zero changes to downstream engines.

### Vertical Expansion
New vertical templates for adjacent food-service categories: bakeries, frozen yogurt, juice bars, coffee shops. Each template carries category-specific defaults (inventory parameters, shrinkage priors, demand profiles, scheduling patterns) while sharing the same engine infrastructure.

### Cross-Tenant Model Pooling
Global forecasting models trained across the full tenant base. At sufficient scale (50+ tenants), cross-tenant pooling enables neural forecasters that outperform per-tenant statistical models — particularly for new locations with limited history.

### Supplier Portal Integration
Direct API and portal integrations for suppliers that don't accept email orders. Automated PDF purchase order generation. Inbound order confirmation parsing.

---

## License

Proprietary. All rights reserved.