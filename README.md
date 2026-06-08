# NASA Knowledge Graph Augmentation

Augmentation pipeline for the [NASA EOSDIS Knowledge Graph](https://huggingface.co/datasets/nasa-gesdisc/nasa-eo-knowledge-graph), using [OpenAlex](https://openalex.org/) (CC0) as the data source. It adds:

- New node types: `Author`, `Institution`
- New relationships: `AUTHORED_BY`, `AFFILIATED_WITH`
- A citation crawl that expands the graph from publications citing our datasets (hop 1) to publications citing those (hop 2), growing `Publication`, `USES_DATASET`, and `CITES`
- Derived edges computed from the graph itself: `CO_USED_WITH` and `WORKS_WITH_DATASET`

This is the work behind the published v2.0.0 dataset. Built alongside [edgraph](https://github.com/nasa/edgraph), NASA's knowledge graph ingestion pipeline.

## Setup

```bash
# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install with dev dependencies
pip install -e ".[dev]"
```

## Configuration

Edit `augmentation/config/config.json` or use environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `NEO4J_URI` | Neo4j Bolt URI | `bolt://localhost:7687` |
| `NEO4J_USER` | Neo4j username | `neo4j` |
| `NEO4J_PASSWORD` | Neo4j password | `test` |
| `OPENALEX_EMAIL` | Email for OpenAlex polite pool (faster rate limit) | `` |
| `OPENALEX_CACHE_DIR` | Directory for cached OpenAlex responses | `data/openalex_cache` |
| `LOG_DIRECTORY` | Directory for log files | `logs` |

## Pipeline scripts

All under `augmentation/`. Run from the repo root with the venv active and Neo4j reachable.

| Script | Purpose |
|--------|---------|
| `openalex/client.py` | OpenAlex API client: batch DOI lookup, on-disk cache, rate-limit and retry, citation lookup |
| `ingest_scripts/ingest_authors.py` | `Author` nodes and `AUTHORED_BY` edges from OpenAlex authorship |
| `ingest_scripts/ingest_institutions.py` | `Institution` nodes and deduped `AFFILIATED_WITH` edges |
| `ingest_scripts/run_augmentation.py` | Orchestrates the author and institution passes over all publications, with a coverage report |
| `ingest_scripts/crawl_citations.py` | Citation crawl (hop 1 datasets to citers, hop 2 citers of citers). Resumable via `citersFetched` flags |
| `ingest_scripts/compute_derived_edges.py` | Builds derived edges `CO_USED_WITH` and `WORKS_WITH_DATASET` (idempotent, batched) |
| `ingest_scripts/daily_crawl.py` | Daily driver: resumes the crawl, runs augmentation when complete, then refreshes derived edges. Lock-guarded and resumable |

Typical run order: `run_augmentation` to add authors and institutions, then `crawl_citations` to expand publications, then `compute_derived_edges`. `daily_crawl` chains these for an automated, resumable refresh.

### Publishing

Exports are produced with APOC (`apoc.export.cypher.all`, `apoc.export.json.all`, `apoc.export.graphml.all`) into `graph.cypher`, `graph.json`, and `graph.graphml`, matching the Hugging Face dataset formats. See `docs/edge-naming.md` for the edge-naming convention.

## Testing

```bash
# Run all tests
python -m pytest augmentation/tests/ -v

# Run unit tests only
python -m pytest augmentation/tests/unit/ -v

# Run a single test file
python -m pytest augmentation/tests/unit/test_config_reader.py -v

# Run with coverage
python -m pytest augmentation/tests/ --cov=augmentation --cov-report=term-missing
```

## Project Structure

```
nasa-kg-augmentation/
├── augmentation/           # Augmentation pipeline code
│   ├── common/             # Shared utilities (config, logging, Neo4j driver, UUID generation)
│   ├── config/             # Configuration files
│   ├── ingest_scripts/     # Node and edge ingestion scripts
│   ├── data/               # Data files and caches
│   └── tests/              # Unit and integration tests
├── edgraph/                # Fork of nasa/edgraph (upstream pipeline)
├── pyproject.toml          # Project configuration and dependencies
└── CLAUDE.md               # AI assistant instructions
```
