"""Crawl the citation network outward from our Datasets to grow the KG.

Hop-1: publications that cite our Datasets        -> (Publication)-[:USES_DATASET]->(Dataset)
Hop-2: publications that cite those publications   -> (Publication)-[:CITES]->(Publication)

New Publication nodes are keyed by UUID5 of their (lowercase) DOI and deduped against the
existing graph via an in-memory {normalized_doi -> globalId} index (existing nodes were keyed
on the UPPERCASE DOI, while OpenAlex returns lowercase — so we can't rely on recomputing the
key). After the crawl, run_augmentation.py augments all new pubs (it processes every pub w/ DOI).

RESUME is first-class and uses the graph as the checkpoint:
  - a Dataset gets `citersFetched=true` once its Hop-1 citers are ingested
  - a hop-1 Publication gets `citersFetched=true` once its Hop-2 citers are ingested
A restart processes only nodes still missing the flag. The crawl also stops gracefully when
OpenAlex's remaining daily quota nears zero, so it can resume the next day.

Does NOT publish to Hugging Face.
"""
import argparse
import logging
import os
import uuid
from typing import Any, Optional

from tqdm import tqdm

from augmentation.common.config_reader import AppConfig, load_config
from augmentation.common.logger_setup import setup_logger
from augmentation.common.neo4j_driver import get_driver
from augmentation.openalex.client import OpenAlexClient, normalize_doi

SOURCE = "citation-crawl"


def publication_row(work: dict, hop: int, global_id: str) -> Optional[dict[str, Any]]:
    """Pure transform: an OpenAlex work + its resolved globalId -> a Publication-node row.

    Returns None if the work has no DOI (we key publications by DOI, so undoi'd works are skipped).
    """
    norm = normalize_doi(work.get("doi") or "")
    if not norm:
        return None
    year = work.get("publication_year")
    return {
        "globalId": global_id,
        "doi": norm,
        "title": work.get("title") or work.get("display_name") or "",
        "year": str(year) if year is not None else "",
        "source": SOURCE,
        "crawlHop": hop,
    }


class CitationCrawler:
    def __init__(self, config: Optional[AppConfig] = None, client: Optional[OpenAlexClient] = None,
                 stop_at_remaining: int = 50) -> None:
        self.config = config or load_config()
        self.log_directory = self.config.paths.log_directory
        os.makedirs(self.log_directory, exist_ok=True)
        self.logger: logging.Logger = setup_logger(
            __name__, "crawl_citations.log", log_directory=self.log_directory,
            level=logging.DEBUG, file_level=logging.INFO,
        )
        self.driver = get_driver(self.config)
        self.client = client or OpenAlexClient(self.config)
        self.stop_at_remaining = stop_at_remaining
        self._pub_index: dict[str, str] = {}  # normalized_doi -> existing globalId

    # ---- dedup index ---------------------------------------------------------
    def load_publication_index(self) -> None:
        """Map every existing Publication's normalized DOI to its globalId (for dedup)."""
        self._pub_index = {}
        with self.driver.session() as session:
            for r in session.run(
                "MATCH (p:Publication) WHERE p.doi IS NOT NULL AND p.doi <> '' "
                "RETURN p.doi AS doi, p.globalId AS globalId"
            ):
                norm = normalize_doi(r["doi"])
                if norm:
                    self._pub_index[norm] = r["globalId"]
        self.logger.info(f"Loaded {len(self._pub_index)} existing publications into dedup index.")

    def resolve_global_id(self, normalized_doi: str) -> str:
        """Existing pub's globalId if known, else a deterministic UUID5 of the lowercase DOI."""
        existing = self._pub_index.get(normalized_doi)
        if existing:
            return existing
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, normalized_doi))

    def rows_from_citing_works(self, citing_works: list[dict], hop: int) -> list[dict[str, Any]]:
        """Build deduped Publication rows for a set of citing works (skips works without a DOI)."""
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for w in citing_works:
            norm = normalize_doi(w.get("doi") or "")
            if not norm or norm in seen:
                continue
            seen.add(norm)
            row = publication_row(w, hop, self.resolve_global_id(norm))
            if row:
                rows.append(row)
        return rows

    # ---- writes --------------------------------------------------------------
    def _write_uses_dataset(self, tx: Any, rows: list[dict], dataset_global_id: str) -> None:
        tx.run(
            """
            UNWIND $rows AS row
            MERGE (p:Publication {globalId: row.globalId})
              ON CREATE SET p.doi = row.doi, p.title = row.title, p.year = row.year,
                            p.source = row.source, p.crawlHop = row.crawlHop
            WITH p
            MATCH (d:Dataset {globalId: $datasetId})
            MERGE (p)-[:USES_DATASET]->(d)
            """, rows=rows, datasetId=dataset_global_id,
        )

    def _write_cites(self, tx: Any, rows: list[dict], cited_global_id: str) -> None:
        tx.run(
            """
            UNWIND $rows AS row
            MERGE (c:Publication {globalId: row.globalId})
              ON CREATE SET c.doi = row.doi, c.title = row.title, c.year = row.year,
                            c.source = row.source, c.crawlHop = row.crawlHop
            WITH c
            MATCH (cited:Publication {globalId: $citedId})
            MERGE (c)-[:CITES]->(cited)
            """, rows=rows, citedId=cited_global_id,
        )

    def _mark_done(self, tx: Any, label: str, global_id: str) -> None:
        assert label in ("Dataset", "Publication")  # label is interpolated into Cypher; never untrusted
        tx.run(f"MATCH (n:{label} {{globalId: $id}}) SET n.citersFetched = true", id=global_id)

    # ---- quota ---------------------------------------------------------------
    def _quota_exhausted(self) -> bool:
        rem = self.client.rate_limit_remaining
        if rem is not None and rem <= self.stop_at_remaining:
            self.logger.warning(f"Stopping: OpenAlex daily quota near limit (remaining={rem}). Resume later.")
            return True
        return False

    # ---- hops ----------------------------------------------------------------
    def _process_dataset(self, session: Any, global_id: str, doi: str) -> int:
        """Hop-1 for ONE dataset: fetch citers, write USES_DATASET, mark done. Returns rows written."""
        written = 0
        work = self.client.fetch_work_by_doi(doi, select="id,cited_by_count")
        if work and work.get("id") and (work.get("cited_by_count") or 0) > 0:
            rows = self.rows_from_citing_works(self.client.fetch_citing_works(work["id"]), hop=1)
            if rows:
                session.execute_write(self._write_uses_dataset, rows, global_id)
                written = len(rows)
        session.execute_write(self._mark_done, "Dataset", global_id)
        return written

    def _process_publication(self, session: Any, global_id: str, doi: str) -> int:
        """Hop-2 for ONE publication: fetch citers, write CITES, mark done. Returns rows written."""
        written = 0
        work = self.client.fetch_work_by_doi(doi, select="id,cited_by_count")
        if work and work.get("id") and (work.get("cited_by_count") or 0) > 0:
            rows = self.rows_from_citing_works(self.client.fetch_citing_works(work["id"]), hop=2)
            if rows:
                session.execute_write(self._write_cites, rows, global_id)
                written = len(rows)
        session.execute_write(self._mark_done, "Publication", global_id)
        return written

    def hop1(self) -> dict[str, int]:
        """Datasets -> citing publications (USES_DATASET). Resumable via Dataset.citersFetched."""
        with self.driver.session() as session:
            datasets = [
                {"globalId": r["globalId"], "doi": r["doi"]}
                for r in session.run(
                    "MATCH (d:Dataset) WHERE d.doi IS NOT NULL AND d.doi <> '' AND d.doi <> 'N/A' "
                    "AND d.citersFetched IS NULL RETURN d.globalId AS globalId, d.doi AS doi"
                )
            ]
        stats = {"datasets": len(datasets), "with_citers": 0, "uses_dataset_rows": 0, "stopped": 0}
        self.logger.info(f"Hop-1: {len(datasets)} datasets pending.")
        with self.driver.session() as session:
            for d in tqdm(datasets, desc="Hop-1 datasets", unit="ds"):
                if self._quota_exhausted():
                    stats["stopped"] = 1
                    break
                written = self._process_dataset(session, d["globalId"], d["doi"])
                if written:
                    stats["with_citers"] += 1
                    stats["uses_dataset_rows"] += written
        self.logger.info(f"Hop-1 stats: {stats}")
        return stats

    def hop2(self) -> dict[str, int]:
        """Hop-1 pubs (anything citing a dataset) -> their citers (CITES). Resumable via citersFetched."""
        with self.driver.session() as session:
            frontier = [
                {"globalId": r["globalId"], "doi": r["doi"]}
                for r in session.run(
                    "MATCH (p:Publication)-[:USES_DATASET]->(:Dataset) "
                    "WHERE p.doi IS NOT NULL AND p.doi <> '' AND p.citersFetched IS NULL "
                    "RETURN DISTINCT p.globalId AS globalId, p.doi AS doi"
                )
            ]
        stats = {"frontier": len(frontier), "expanded": 0, "cites_rows": 0, "stopped": 0}
        self.logger.info(f"Hop-2: {len(frontier)} frontier publications pending.")
        with self.driver.session() as session:
            for p in tqdm(frontier, desc="Hop-2 pubs", unit="pub"):
                if self._quota_exhausted():
                    stats["stopped"] = 1
                    break
                written = self._process_publication(session, p["globalId"], p["doi"])
                if written:
                    stats["expanded"] += 1
                    stats["cites_rows"] += written
        self.logger.info(f"Hop-2 stats: {stats}")
        return stats

    def crawl(self) -> dict[str, Any]:
        self.load_publication_index()
        h1 = self.hop1()
        if h1.get("stopped"):
            return {"hop1": h1, "hop2": None, "note": "stopped in hop-1 (quota); resume to continue"}
        self.load_publication_index()  # refresh: hop-1 added new pubs that hop-2 may re-encounter
        h2 = self.hop2()
        result = {"hop1": h1, "hop2": h2}
        if h2.get("stopped"):
            result["note"] = "stopped in hop-2 (quota); resume to continue"
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl citations from datasets (hop-1 + hop-2), resumable.")
    parser.add_argument("--stop-at-remaining", type=int, default=50,
                        help="Stop gracefully when OpenAlex daily quota remaining drops to this.")
    args = parser.parse_args()
    crawler = CitationCrawler(stop_at_remaining=args.stop_at_remaining)
    result = crawler.crawl()
    print(f"Citation crawl result: {result}")


if __name__ == "__main__":
    main()
