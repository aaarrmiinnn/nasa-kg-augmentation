"""Ingest OpenAlex affiliations: Institution nodes + AFFILIATED_WITH edges.

Reads the same OpenAlex works as the Author ingest (served from cache after #1's run),
and MERGEs an ``Institution`` node per OpenAlex institution plus a deduped
``(Author)-[:AFFILIATED_WITH]->(Institution)`` edge — one edge per unique (Author,
Institution) pair across all matched papers. Depends on Author nodes from the Author
ingest; MERGE-by-globalId keeps it order-independent and idempotent.
"""
import argparse
import logging
import os
from typing import Any, Optional

import requests
from tqdm import tqdm

from augmentation.common.config_reader import AppConfig, load_config
from augmentation.common.core import generate_uuid_from_id
from augmentation.common.logger_setup import setup_logger
from augmentation.common.neo4j_driver import get_driver
from augmentation.openalex.client import OpenAlexClient, normalize_doi, parse_authorships


def build_affiliation_rows(work: dict) -> list[dict[str, Any]]:
    """Pure transform: an OpenAlex work -> deduped Author/Institution affiliation rows.

    One row per unique (author, institution) pair within the work. Authorships without
    a resolvable author id, and institutions without an id, are skipped.
    """
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for record in parse_authorships(work):
        if not record.author.openalex_id:
            continue
        author_id = generate_uuid_from_id(record.author.openalex_id)
        for inst in record.institutions:
            if not inst.openalex_id:
                continue
            institution_id = generate_uuid_from_id(inst.openalex_id)
            key = (author_id, institution_id)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "authorId": author_id,
                "institutionId": institution_id,
                "instOpenalexId": inst.openalex_id,
                "name": inst.display_name,
                "ror": inst.ror,
                "country": inst.country_code,
            })
    return rows


class InstitutionIngestor:
    """Ingests Institution nodes and AFFILIATED_WITH edges from OpenAlex into Neo4j."""

    def __init__(self, config: Optional[AppConfig] = None, client: Optional[OpenAlexClient] = None) -> None:
        self.config = config or load_config()
        self.log_directory = self.config.paths.log_directory
        os.makedirs(self.log_directory, exist_ok=True)
        self.logger: logging.Logger = setup_logger(
            __name__, "ingest_institutions.log", log_directory=self.log_directory,
            level=logging.DEBUG, file_level=logging.INFO,
        )
        self.driver = get_driver(self.config)
        self.client = client or OpenAlexClient(self.config)

    def set_uniqueness_constraint(self) -> None:
        query = "CREATE CONSTRAINT IF NOT EXISTS FOR (i:Institution) REQUIRE i.globalId IS UNIQUE"
        with self.driver.session() as session:
            try:
                session.run(query)
                self.logger.info("Uniqueness constraint on Institution.globalId set successfully.")
            except Exception as e:  # pragma: no cover - defensive, mirrors edgraph
                self.logger.error(f"Failed to create uniqueness constraint: {e}")

    def fetch_publication_dois(self, limit: Optional[int] = None) -> list[dict[str, str]]:
        query = (
            "MATCH (p:Publication) WHERE p.doi IS NOT NULL AND p.doi <> '' "
            "RETURN p.globalId AS globalId, p.doi AS doi"
        )
        if limit is not None:
            query += " LIMIT $limit"
        with self.driver.session() as session:
            result = session.run(query, limit=limit)
            return [{"globalId": r["globalId"], "doi": r["doi"]} for r in result]

    def add_affiliations_batch(self, tx: Any, rows: list[dict[str, Any]]) -> None:
        query = """
        UNWIND $rows AS row
        MERGE (a:Author {globalId: row.authorId})
        MERGE (i:Institution {globalId: row.institutionId})
          SET i.openalexId = row.instOpenalexId, i.name = row.name, i.ror = row.ror, i.country = row.country
        MERGE (a)-[:AFFILIATED_WITH]->(i)
        """
        tx.run(query, rows=rows)

    def process_publications(self, batch_size: int = 100, limit: Optional[int] = None) -> dict[str, int]:
        publications = self.fetch_publication_dois(limit=limit)
        pub_by_doi = {norm: p["globalId"] for p in publications if (norm := normalize_doi(p["doi"]))}
        self.logger.info(f"Fetched {len(publications)} publications with DOIs.")

        stats = {"publications": len(publications), "matched": 0, "rows": 0, "failed_batches": 0}
        rows: list[dict[str, Any]] = []

        with self.driver.session() as session:
            dois = list(pub_by_doi.keys())
            for start in tqdm(range(0, len(dois), batch_size), desc="Ingesting affiliations", unit="batch"):
                chunk = dois[start:start + batch_size]
                try:
                    works = self.client.fetch_works_by_dois(chunk)
                except requests.RequestException as e:
                    self.logger.warning(f"Skipping batch at offset {start} after fetch failure: {e}")
                    stats["failed_batches"] += 1
                    continue
                for norm_doi, work in works.items():
                    if pub_by_doi.get(norm_doi) is None:
                        continue
                    stats["matched"] += 1
                    rows.extend(build_affiliation_rows(work))
                if rows:
                    session.execute_write(self.add_affiliations_batch, rows)
                    stats["rows"] += len(rows)
                    rows = []

        self.logger.info(f"Institution ingest stats: {stats}")
        return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest OpenAlex Institution nodes + AFFILIATED_WITH edges.")
    parser.add_argument("--limit", type=int, default=None, help="Max publications to process (for demos).")
    parser.add_argument("--batch-size", type=int, default=100, help="Publications per OpenAlex/Neo4j batch.")
    args = parser.parse_args()

    ingestor = InstitutionIngestor()
    ingestor.set_uniqueness_constraint()
    stats = ingestor.process_publications(batch_size=args.batch_size, limit=args.limit)
    print(f"Institution ingest complete: {stats}")


if __name__ == "__main__":
    main()
