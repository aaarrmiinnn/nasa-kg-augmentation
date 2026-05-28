# NASA Knowledge Graph Augmentation

Augmentation pipeline for the [NASA EOSDIS Knowledge Graph](https://huggingface.co/datasets/nasa-gesdisc/nasa-eo-knowledge-graph). Adds new node types (Author, Institution) and relationships (AUTHORED_BY, AFFILIATED_WITH) using [OpenAlex](https://openalex.org/) as the data source.

Built alongside [edgraph](https://github.com/nasa/edgraph), NASA's knowledge graph ingestion pipeline.

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
