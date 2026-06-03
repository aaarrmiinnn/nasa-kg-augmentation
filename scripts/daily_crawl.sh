#!/usr/bin/env bash
# Daily citation-crawl resume launcher (intended to be invoked by launchd/cron once per day).
# Resolves the project root from this script's location, ensures Neo4j is up, then runs the
# lock-guarded daily driver, which resumes the crawl and auto-augments when the crawl completes.
#
# OPENALEX_EMAIL is read from the environment (set it in the launchd plist / cron env) so no
# personal email is committed to the repo.
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

# Make sure the local Neo4j is running (survives reboots); ignore failure if already up.
(cd edgraph && docker compose up -d neo4j) >/dev/null 2>&1 || true

# shellcheck disable=SC1091
source venv/bin/activate
exec python -u -m augmentation.ingest_scripts.daily_crawl
