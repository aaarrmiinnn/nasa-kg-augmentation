"""Orchestrate the full OpenAlex augmentation over the whole publication corpus.

Runs the Author ingest then the Institution ingest, sharing one OpenAlex client so the
on-disk cache is reused: the first pass populates the cache while writing Authors +
AUTHORED_BY; the second pass reads entirely from cache (no API calls) while writing
Institutions + AFFILIATED_WITH. Resumable — a re-run serves cached hits and known misses
from disk. Prints a coverage report at the end.

Does NOT publish anything to Hugging Face.
"""
import argparse
from typing import Optional

from augmentation.common.config_reader import load_config
from augmentation.common.neo4j_driver import get_driver
from augmentation.ingest_scripts.ingest_authors import AuthorIngestor
from augmentation.ingest_scripts.ingest_institutions import InstitutionIngestor


def format_coverage(author_stats: dict, institution_stats: dict, graph_counts: dict) -> str:
    """Render a human-readable coverage report from ingest stats + final graph counts."""
    pubs = author_stats.get("publications", 0)
    matched = author_stats.get("matched", 0)
    pct = (matched / pubs * 100) if pubs else 0.0
    return (
        "OpenAlex augmentation coverage\n"
        f"  publications scanned : {pubs}\n"
        f"  matched in OpenAlex  : {matched} ({pct:.1f}%)\n"
        f"  Author nodes         : {graph_counts.get('authors', 0)}\n"
        f"  Institution nodes    : {graph_counts.get('institutions', 0)}\n"
        f"  AUTHORED_BY edges    : {graph_counts.get('authored_by', 0)}\n"
        f"  AFFILIATED_WITH edges: {graph_counts.get('affiliated_with', 0)}"
    )


def graph_counts(driver) -> dict:
    with driver.session() as session:
        return session.run(
            """
            CALL { MATCH (a:Author) RETURN count(a) AS authors }
            CALL { MATCH (i:Institution) RETURN count(i) AS institutions }
            CALL { MATCH (:Publication)-[r:AUTHORED_BY]->(:Author) RETURN count(r) AS authored_by }
            CALL { MATCH (:Author)-[r:AFFILIATED_WITH]->(:Institution) RETURN count(r) AS affiliated_with }
            RETURN authors, institutions, authored_by, affiliated_with
            """
        ).single().data()


def run(batch_size: int = 100, limit: Optional[int] = None) -> str:
    config = load_config()
    author = AuthorIngestor(config)
    institutions = InstitutionIngestor(config, client=author.client)  # share client + cache

    author.set_uniqueness_constraint()
    institutions.set_uniqueness_constraint()

    a_stats = author.process_publications(batch_size=batch_size, limit=limit)
    i_stats = institutions.process_publications(batch_size=batch_size, limit=limit)

    report = format_coverage(a_stats, i_stats, graph_counts(get_driver(config)))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the full OpenAlex augmentation (Authors + Institutions).")
    parser.add_argument("--limit", type=int, default=None, help="Max publications to process (omit for the full corpus).")
    parser.add_argument("--batch-size", type=int, default=100, help="Publications per OpenAlex/Neo4j batch.")
    args = parser.parse_args()

    report = run(batch_size=args.batch_size, limit=args.limit)
    print(report)


if __name__ == "__main__":
    main()
