"""Ingest OpenAlex authorship into the NASA KG: Author nodes + AUTHORED_BY edges.

Reads existing ``Publication`` DOIs from Neo4j, fetches the matching OpenAlex works,
and MERGEs an ``Author`` node per OpenAlex author plus a ``(Publication)-[:AUTHORED_BY]->(Author)``
edge carrying the author's position. Follows the edgraph ingestor pattern.
"""
import argparse
import logging
import os
from typing import Any, Optional

from tqdm import tqdm

from augmentation.common.config_reader import AppConfig, load_config
from augmentation.common.core import generate_uuid_from_id
from augmentation.common.logger_setup import setup_logger
from augmentation.common.neo4j_driver import get_driver
from augmentation.openalex.client import OpenAlexClient, normalize_doi, parse_authorships


def build_author_rows(pub_global_id: str, work: dict) -> list[dict[str, Any]]:
    """Pure transform: an OpenAlex work + its publication's globalId -> AUTHORED_BY rows.

    One row per authorship that has a resolvable OpenAlex author id. Each row carries
    everything needed to MERGE the Author node and the AUTHORED_BY edge.
    """
    rows: list[dict[str, Any]] = []
    for record in parse_authorships(work):
        if not record.author.openalex_id:
            continue
        rows.append({
            "pubId": pub_global_id,
            "authorId": generate_uuid_from_id(record.author.openalex_id),
            "openalexId": record.author.openalex_id,
            "name": record.author.display_name,
            "orcid": record.author.orcid,
            "authorPosition": record.author_position,
        })
    return rows


class AuthorIngestor:
    """Ingests Author nodes and AUTHORED_BY edges from OpenAlex into Neo4j."""

    def __init__(self, config: Optional[AppConfig] = None, client: Optional[OpenAlexClient] = None) -> None:
        self.config = config or load_config()
        self.log_directory = self.config.paths.log_directory
        os.makedirs(self.log_directory, exist_ok=True)
        self.logger: logging.Logger = setup_logger(
            __name__, "ingest_authors.log", log_directory=self.log_directory,
            level=logging.DEBUG, file_level=logging.INFO,
        )
        self.driver = get_driver(self.config)
        self.client = client or OpenAlexClient(self.config)

    def set_uniqueness_constraint(self) -> None:
        query = "CREATE CONSTRAINT IF NOT EXISTS FOR (a:Author) REQUIRE a.globalId IS UNIQUE"
        with self.driver.session() as session:
            try:
                session.run(query)
                self.logger.info("Uniqueness constraint on Author.globalId set successfully.")
            except Exception as e:  # pragma: no cover - defensive, mirrors edgraph
                self.logger.error(f"Failed to create uniqueness constraint: {e}")

    def fetch_publication_dois(self, limit: Optional[int] = None) -> list[dict[str, str]]:
        """Return ``[{globalId, doi}]`` for publications that carry a DOI."""
        query = (
            "MATCH (p:Publication) WHERE p.doi IS NOT NULL AND p.doi <> '' "
            "RETURN p.globalId AS globalId, p.doi AS doi"
        )
        if limit is not None:
            query += " LIMIT $limit"
        with self.driver.session() as session:
            result = session.run(query, limit=limit)
            return [{"globalId": r["globalId"], "doi": r["doi"]} for r in result]

    def add_authors_batch(self, tx: Any, rows: list[dict[str, Any]]) -> None:
        query = """
        UNWIND $rows AS row
        MATCH (p:Publication {globalId: row.pubId})
        MERGE (a:Author {globalId: row.authorId})
          SET a.openalexId = row.openalexId, a.name = row.name, a.orcid = row.orcid
        MERGE (p)-[r:AUTHORED_BY]->(a)
          SET r.authorPosition = row.authorPosition
        """
        tx.run(query, rows=rows)

    def process_publications(self, batch_size: int = 100, limit: Optional[int] = None) -> dict[str, int]:
        """Match publications to OpenAlex works and write Author nodes + AUTHORED_BY edges.

        Returns a small stats dict (publications seen, matched, edge rows written).
        """
        publications = self.fetch_publication_dois(limit=limit)
        # Join key: normalized DOI -> publication globalId. Drop any DOI that normalizes
        # to empty (e.g. whitespace-only) so it can't act as a wildcard match key.
        pub_by_doi = {
            norm: p["globalId"]
            for p in publications
            if (norm := normalize_doi(p["doi"]))
        }
        self.logger.info(f"Fetched {len(publications)} publications with DOIs.")

        stats = {"publications": len(publications), "matched": 0, "rows": 0}
        rows: list[dict[str, Any]] = []

        with self.driver.session() as session:
            dois = list(pub_by_doi.keys())
            for start in tqdm(range(0, len(dois), batch_size), desc="Ingesting authors", unit="batch"):
                chunk = dois[start:start + batch_size]
                works = self.client.fetch_works_by_dois(chunk)
                for norm_doi, work in works.items():
                    pub_id = pub_by_doi.get(norm_doi)
                    if pub_id is None:
                        continue
                    stats["matched"] += 1
                    rows.extend(build_author_rows(pub_id, work))
                if rows:
                    session.execute_write(self.add_authors_batch, rows)
                    stats["rows"] += len(rows)
                    rows = []

        self.logger.info(f"Author ingest stats: {stats}")
        return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest OpenAlex Author nodes + AUTHORED_BY edges.")
    parser.add_argument("--limit", type=int, default=None, help="Max publications to process (for demos).")
    parser.add_argument("--batch-size", type=int, default=100, help="Publications per OpenAlex/Neo4j batch.")
    args = parser.parse_args()

    ingestor = AuthorIngestor()
    ingestor.set_uniqueness_constraint()
    stats = ingestor.process_publications(batch_size=args.batch_size, limit=args.limit)
    print(f"Author ingest complete: {stats}")


if __name__ == "__main__":
    main()
