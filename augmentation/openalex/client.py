"""Client for the OpenAlex API, used to augment the NASA KG with authorship data."""
import json
import os
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable, Optional

import requests

from augmentation.common.config_reader import AppConfig

OPENALEX_WORKS_URL = "https://api.openalex.org/works"

HTTP_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 1.0

# On-disk marker for a DOI that OpenAlex confirmed it has no work for (negative cache).
_MISS_MARKER = {"__openalex_miss__": True}


class _Miss:
    """Sentinel returned by the cache for a known, confirmed miss."""


MISS = _Miss()


HTTP_TIMEOUT = (10, 60)  # (connect, read) seconds — avoid hanging the full run on a stalled socket


@dataclass
class AuthorRef:
    openalex_id: Optional[str]
    display_name: Optional[str]
    orcid: Optional[str]


@dataclass
class InstitutionRef:
    openalex_id: Optional[str]
    display_name: Optional[str]
    ror: Optional[str]
    country_code: Optional[str]


@dataclass
class AuthorshipRecord:
    author: AuthorRef
    author_position: str
    institutions: list[InstitutionRef] = field(default_factory=list)


def parse_authorships(work: dict) -> list[AuthorshipRecord]:
    """Turn an OpenAlex work's ``authorships[]`` into structured records.

    Authors with no institutions yield an empty ``institutions`` list; authors
    with several institutions on one paper yield one record carrying all of them.
    """
    records: list[AuthorshipRecord] = []
    for authorship in work.get("authorships", []):
        author = authorship.get("author", {})
        institutions = [
            InstitutionRef(
                openalex_id=inst.get("id"),
                display_name=inst.get("display_name"),
                ror=inst.get("ror"),
                country_code=inst.get("country_code"),
            )
            for inst in authorship.get("institutions", [])
        ]
        records.append(
            AuthorshipRecord(
                author=AuthorRef(
                    openalex_id=author.get("id"),
                    display_name=author.get("display_name"),
                    orcid=author.get("orcid"),
                ),
                author_position=authorship.get("author_position"),
                institutions=institutions,
            )
        )
    return records


def normalize_doi(doi: str) -> str:
    """Reduce a DOI to its bare, lowercase form for reliable joining.

    The graph stores DOIs uppercase (e.g. ``10.1111/GCB.15824``) while OpenAlex
    returns lowercase ``https://doi.org/...`` URLs. Both collapse to the same key here.
    """
    if not doi:
        return ""
    doi = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi.org/"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
            break
    return doi


class OpenAlexClient:
    """Fetches OpenAlex works by DOI for the augmentation pipeline."""

    def __init__(self, config: AppConfig, sleep: Callable[[float], None] = time.sleep) -> None:
        self.config = config
        self.email = config.openalex.email
        self.batch_size = config.openalex.batch_size
        self.cache_dir = config.paths.openalex_cache_directory
        rps = config.openalex.requests_per_second
        self._min_interval = 1.0 / rps if rps and rps > 0 else 0.0
        self._sleep = sleep
        self._made_request = False
        self.session = requests.Session()

    def fetch_works_by_dois(self, dois: list[str]) -> dict[str, dict]:
        if not dois:
            return {}

        # Deduplicate while preserving order; serve cache hits, fetch only misses.
        normalized = list(dict.fromkeys(normalize_doi(d) for d in dois))
        results: dict[str, dict] = {}
        misses: list[str] = []
        for doi in normalized:
            cached = self._read_cache(doi)
            if cached is MISS:
                continue  # confirmed-not-in-OpenAlex; don't re-fetch
            if cached is not None:
                results[doi] = cached
            else:
                misses.append(doi)

        for start in range(0, len(misses), self.batch_size):
            chunk = misses[start:start + self.batch_size]
            results.update(self._fetch_batch(chunk))
        return results

    def _cache_path(self, normalized_doi: str) -> str:
        return os.path.join(self.cache_dir, urllib.parse.quote(normalized_doi, safe="") + ".json")

    def _read_cache(self, normalized_doi: str):
        """Return the cached work dict, the ``MISS`` sentinel, or ``None`` if uncached."""
        path = self._cache_path(normalized_doi)
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                # Treat a corrupt/unreadable cache file as uncached so the run self-heals.
                return None
            if isinstance(data, dict) and data.get("__openalex_miss__"):
                return MISS
            return data
        return None

    def _write_cache(self, normalized_doi: str, work: dict) -> None:
        os.makedirs(self.cache_dir, exist_ok=True)
        with open(self._cache_path(normalized_doi), "w") as f:
            json.dump(work, f)

    def _fetch_batch(self, normalized_dois: list[str]) -> dict[str, dict]:
        params = {
            "filter": "doi:" + "|".join(normalized_dois),
            "per-page": self.batch_size,
            "select": "id,doi,authorships",
        }
        if self.email:
            params["mailto"] = self.email

        resp = self._get_with_retry(params)

        batch: dict[str, dict] = {}
        for w in resp.json().get("results", []):
            if w.get("doi"):
                norm = normalize_doi(w["doi"])
                batch[norm] = w
                self._write_cache(norm, w)

        # Negative-cache: this was a successful response, so any requested DOI that
        # didn't come back is confirmed absent from OpenAlex — record it so future
        # runs don't re-request it.
        for doi in normalized_dois:
            if doi not in batch:
                self._write_cache(doi, _MISS_MARKER)
        return batch

    def _get_with_retry(self, params: dict) -> requests.Response:
        """GET with throttling, retrying transient errors (429/5xx/timeout) with backoff."""
        last_exc: Optional[Exception] = None
        for attempt in range(MAX_RETRIES):
            if self._made_request and self._min_interval:
                self._sleep(self._min_interval)
            self._made_request = True
            try:
                resp = self.session.get(OPENALEX_WORKS_URL, params=params, timeout=HTTP_TIMEOUT)
                resp.raise_for_status()
                return resp
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                retryable = isinstance(e, (requests.Timeout, requests.ConnectionError)) or status in HTTP_RETRY_STATUSES
                if not retryable or attempt == MAX_RETRIES - 1:
                    raise
                last_exc = e
                self._sleep(BACKOFF_BASE_SECONDS * (2 ** attempt))  # exponential backoff
        # Unreachable: the loop above always returns or raises. Guard against a None raise.
        raise last_exc or RuntimeError(f"Exhausted {MAX_RETRIES} retries without a stored exception")
