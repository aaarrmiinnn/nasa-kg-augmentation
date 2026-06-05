"""Compute derived edges from the graph's own structure (no external source).

A repeatable pipeline stage — re-running recomputes edges from the current graph, so it stays
correct as the augmentation grows. Derived edges follow docs/edge-naming.md: symmetric
co-occurrence uses CO_<participle>_WITH (one stored direction, a `weight`), and every derived
edge carries `derived: true` so it is never confused with an asserted fact.

Currently builds:
  - (Dataset)-[:CO_USED_WITH {weight, derived}]->(Dataset)  — datasets co-used in the same pubs

Future derived edges (CO_AUTHORED_WITH, WORKS_WITH_DATASET, RESEARCHES) slot in as sibling methods.
Does NOT publish to Hugging Face.
"""
import argparse
import logging
import os
from typing import Any, Optional

from augmentation.common.config_reader import AppConfig, load_config
from augmentation.common.logger_setup import setup_logger
from augmentation.common.neo4j_driver import get_driver

# Datasets co-used in the same publication. Canonicalized to one direction (d1.globalId < d2.globalId),
# weight = number of distinct publications using both. Recomputed in place (append-only graph ->
# weights only grow, so MERGE + absolute SET is idempotent and correct).
CO_USED_WITH_OUTER = """
MATCH (d1:Dataset)<-[:USES_DATASET]-(p:Publication)-[:USES_DATASET]->(d2:Dataset)
WHERE d1.globalId < d2.globalId
  AND ($scope IS NULL OR (d1.globalId IN $scope AND d2.globalId IN $scope))
WITH d1, d2, count(DISTINCT p) AS w
WHERE w >= $minWeight
RETURN d1, d2, w
"""
CO_USED_WITH_INNER = "MERGE (d1)-[r:CO_USED_WITH]->(d2) SET r.weight = w, r.derived = true"

# (Author)-[:WORKS_WITH_DATASET]->(Dataset): an author authored a publication that uses the dataset.
# weight = number of distinct such publications. Directed actor->resource, so no canonicalization.
WORKS_WITH_DATASET_AUTHOR_OUTER = """
MATCH (a:Author)<-[:AUTHORED_BY]-(p:Publication)-[:USES_DATASET]->(d:Dataset)
WHERE ($scope IS NULL OR d.globalId IN $scope)
WITH a, d, count(DISTINCT p) AS w
WHERE w >= $minWeight
RETURN a, d, w
"""
WORKS_WITH_DATASET_AUTHOR_INNER = "MERGE (a)-[r:WORKS_WITH_DATASET]->(d) SET r.weight = w, r.derived = true"

# (Institution)-[:WORKS_WITH_DATASET]->(Dataset): an affiliated author works with the dataset.
# Same edge type as the author case (identical semantics; endpoint label disambiguates).
WORKS_WITH_DATASET_INST_OUTER = """
MATCH (i:Institution)<-[:AFFILIATED_WITH]-(:Author)<-[:AUTHORED_BY]-(p:Publication)-[:USES_DATASET]->(d:Dataset)
WHERE ($scope IS NULL OR d.globalId IN $scope)
WITH i, d, count(DISTINCT p) AS w
WHERE w >= $minWeight
RETURN i, d, w
"""
WORKS_WITH_DATASET_INST_INNER = "MERGE (i)-[r:WORKS_WITH_DATASET]->(d) SET r.weight = w, r.derived = true"


class DerivedEdgeBuilder:
    """Builds/refreshes derived edges. Each method is idempotent and recomputes from the live graph."""

    def __init__(self, config: Optional[AppConfig] = None) -> None:
        self.config = config or load_config()
        self.log_directory = self.config.paths.log_directory
        os.makedirs(self.log_directory, exist_ok=True)
        self.logger: logging.Logger = setup_logger(
            __name__, "compute_derived_edges.log", log_directory=self.log_directory,
            level=logging.DEBUG, file_level=logging.INFO,
        )
        self.driver = get_driver(self.config)

    def _iterate(self, outer: str, inner: str, params: dict, batch_size: int = 1000) -> dict[str, Any]:
        """Run a batched outer/inner write via apoc.periodic.iterate; return its summary."""
        with self.driver.session() as session:
            rec = session.run(
                "CALL apoc.periodic.iterate($outer, $inner, "
                "{batchSize: $batchSize, parallel: false, params: $params}) "
                "YIELD batches, total, errorMessages RETURN batches, total, errorMessages",
                outer=outer, inner=inner, batchSize=batch_size, params=params,
            ).single()
            return {"batches": rec["batches"], "total": rec["total"], "errors": rec["errorMessages"]}

    def compute_co_used_with(self, min_weight: int = 1, rebuild: bool = False,
                             dataset_global_ids: Optional[list[str]] = None) -> dict[str, Any]:
        """(Dataset)-[:CO_USED_WITH]->(Dataset). Pass dataset_global_ids to scope (used by tests)."""
        scope = dataset_global_ids  # None => whole graph

        if rebuild:
            self._iterate(
                "MATCH (d1:Dataset)-[r:CO_USED_WITH]->(d2:Dataset) "
                "WHERE ($scope IS NULL OR (d1.globalId IN $scope AND d2.globalId IN $scope)) RETURN r",
                "DELETE r", {"scope": scope},
            )

        result = self._iterate(CO_USED_WITH_OUTER, CO_USED_WITH_INNER,
                               {"scope": scope, "minWeight": min_weight})
        if result["errors"]:
            self.logger.error(f"CO_USED_WITH errors: {result['errors']}")
        with self.driver.session() as session:
            total_edges = session.run("MATCH (:Dataset)-[r:CO_USED_WITH]->(:Dataset) RETURN count(r) AS c").single()["c"]
        stats = {"pairs_written": result["total"], "batches": result["batches"], "co_used_with_total": total_edges}
        self.logger.info(f"CO_USED_WITH stats: {stats}")
        return stats

    def compute_works_with_dataset(self, min_weight: int = 1, rebuild: bool = False,
                                   dataset_global_ids: Optional[list[str]] = None) -> dict[str, Any]:
        """(Author|Institution)-[:WORKS_WITH_DATASET]->(Dataset). Scope by dataset for tests."""
        scope = dataset_global_ids

        if rebuild:
            self._iterate(
                "MATCH ()-[r:WORKS_WITH_DATASET]->(d:Dataset) "
                "WHERE ($scope IS NULL OR d.globalId IN $scope) RETURN r",
                "DELETE r", {"scope": scope},
            )

        author = self._iterate(WORKS_WITH_DATASET_AUTHOR_OUTER, WORKS_WITH_DATASET_AUTHOR_INNER,
                               {"scope": scope, "minWeight": min_weight})
        inst = self._iterate(WORKS_WITH_DATASET_INST_OUTER, WORKS_WITH_DATASET_INST_INNER,
                             {"scope": scope, "minWeight": min_weight})
        for label, res in (("author", author), ("institution", inst)):
            if res["errors"]:
                self.logger.error(f"WORKS_WITH_DATASET ({label}) errors: {res['errors']}")
        with self.driver.session() as session:
            total_edges = session.run(
                "MATCH ()-[r:WORKS_WITH_DATASET]->(:Dataset) RETURN count(r) AS c").single()["c"]
        stats = {"author_pairs_written": author["total"], "institution_pairs_written": inst["total"],
                 "works_with_dataset_total": total_edges}
        self.logger.info(f"WORKS_WITH_DATASET stats: {stats}")
        return stats

    def build_all(self, min_weight: int = 1, rebuild: bool = False) -> dict[str, Any]:
        """Run every derived-edge computation. Called as the final stage of the augmentation build."""
        return {
            "co_used_with": self.compute_co_used_with(min_weight=min_weight, rebuild=rebuild),
            "works_with_dataset": self.compute_works_with_dataset(min_weight=min_weight, rebuild=rebuild),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute derived edges (CO_USED_WITH, ...).")
    parser.add_argument("--min-weight", type=int, default=1, help="Minimum co-usage count to keep an edge.")
    parser.add_argument("--rebuild", action="store_true", help="Delete derived edges first, then recompute.")
    args = parser.parse_args()
    builder = DerivedEdgeBuilder()
    print(f"Derived edges complete: {builder.build_all(min_weight=args.min_weight, rebuild=args.rebuild)}")


if __name__ == "__main__":
    main()
