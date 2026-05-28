# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is the **nasa-kg-augmentation** project — a workspace for augmenting the NASA EOSDIS Knowledge Graph. It contains `edgraph/`, a fork of [nasa/edgraph](https://github.com/nasa/edgraph) (forked to `aaarrmiinnn/edgraph`), which is the upstream pipeline that builds a Neo4j knowledge graph from NASA CMR, publication, and GCMD data. The exported graph is published on [Hugging Face](https://huggingface.co/datasets/nasa-gesdisc/nasa-eo-knowledge-graph).

**Key constraint**: Do NOT push changes to the `upstream` remote (nasa/edgraph). The `origin` remote points to the fork (`aaarrmiinnn/edgraph`). Augmentation code lives in this outer repo, separate from edgraph.

**Goal**: Augment the knowledge graph (new data of existing types + new node/relationship types) and publish updated exports to Hugging Face in the same formats: `graph.json` (JSONL), `graph.graphml`, `graph.cypher`.

## edgraph Architecture

### Ingestion Pipeline

All ingest scripts live in `edgraph/src/graph_ingest/ingest_scripts/` and follow a common pattern:
1. Class with `__init__` (loads config, logger, Neo4j driver)
2. `set_uniqueness_constraint()` — Cypher constraint creation
3. `process_files(batch_size=100)` — reads source data, batches, calls `session.execute_write()`
4. `add_data(tx, batch)` — Cypher MERGE/CREATE queries
5. `main()` entry point

**Execution order matters**: nodes before edges, edges before graph algorithms. Orchestrated by `edgraph/src/graph_ingest/ingest.sh`.

### Shared Modules (`edgraph/src/graph_ingest/common/`)

- `core.py` — `generate_uuid_from_doi()`, `generate_uuid_from_name()`, `find_json_files()`
- `config_reader.py` — Loads `config.json`, supports env var overrides (`NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`)
- `neo4j_driver.py` / `dbconfig.py` — Neo4j driver factories
- `logger_setup.py` — Per-script file logging to `/app/logs`

### Config

`edgraph/src/graph_ingest/config/config.json` — database credentials + paths to data files. Environment variables override database settings.

### Data Files (Git LFS)

`edgraph/src/graph_ingest/data/` — CSVs (DOIs, GCMD keywords), JSONs (publications), and `collection_metadata/` directory. All tracked via Git LFS.

### Graph Schema

**7 node types**: Dataset, Publication, Platform, Instrument, DataCenter, Project, ScienceKeyword

**9 relationship types**: CITES, USES_DATASET, HAS_APPLIEDRESEARCHAREA, HAS_SCIENCEKEYWORD, HAS_PLATFORM, HAS_DATASET, OF_PROJECT, HAS_INSTRUMENT, HAS_SUBCATEGORY

All nodes use `globalId` (UUID5) as the primary key. UUIDs generated from DOI or shortName.

## Commands

### Build & Run (Docker)

```bash
cd edgraph
docker-compose up --build          # Build and start Neo4j + ingest pipeline
docker-compose down                # Stop everything
```

Neo4j Browser: http://localhost:7474

### Testing

Tests use **pytest** from `edgraph/src/` as working directory. Markers: `unit`, `integration`, `e2e`.

```bash
# All tests via Docker
cd edgraph && docker-compose -f docker-compose.test.yml up --build

# Run locally (requires Neo4j running)
cd edgraph/src
python -m pytest graph_ingest/tests/unit/ -v                          # Unit tests
python -m pytest graph_ingest/tests/integration/ -v                   # Integration tests
python -m pytest graph_ingest/tests/unit/test_dataset_ingestion.py -v # Single file
python -m pytest graph_ingest/tests/unit/test_core.py::TestCore::test_generate_uuid_from_doi -v  # Single test
python -m pytest --cov=graph_ingest.ingest_scripts --cov-report=term-missing  # With coverage
```

### Running Individual Ingest Scripts

```bash
cd edgraph/src
python -m graph_ingest.ingest_scripts.ingest_node_dataset
```

## Git Remotes (edgraph/)

- `origin` → `https://github.com/aaarrmiinnn/edgraph.git` (fork, safe to push)
- `upstream` → `https://github.com/nasa/edgraph.git` (DO NOT push)

## HuggingFace Output Formats

The augmented graph must be exportable in these formats to match the existing HF dataset:
- **graph.json** — JSONL, one JSON object per line. Nodes: `{"type":"node","id":"...","labels":["Dataset"],"properties":{...}}`. Relationships: `{"type":"relationship","start":{...},"end":{...},"label":"CITES",...}`
- **graph.graphml** — XML-based, compatible with Gephi/Cytoscape
- **graph.cypher** — Neo4j Cypher CREATE/MERGE statements
