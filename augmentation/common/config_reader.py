import json
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DatabaseConfig:
    uri: str
    user: str
    password: str


@dataclass
class PathsConfig:
    openalex_cache_directory: str
    log_directory: str


@dataclass
class OpenAlexConfig:
    email: str = ""
    requests_per_second: int = 10
    batch_size: int = 50


@dataclass
class AppConfig:
    database: DatabaseConfig
    paths: PathsConfig
    openalex: OpenAlexConfig = field(default_factory=OpenAlexConfig)


def load_config(config_path: Optional[str] = None) -> AppConfig:
    """
    Load configuration from a JSON file, with environment variable overrides.
    Defaults to config/config.json relative to this file.
    """
    if config_path is None:
        config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'config.json')

    with open(config_path, 'r') as f:
        raw = json.load(f)

    db = raw['database']
    db['uri'] = os.getenv('NEO4J_URI', db['uri'])
    db['user'] = os.getenv('NEO4J_USER', db['user'])
    db['password'] = os.getenv('NEO4J_PASSWORD', db['password'])

    paths = raw['paths']
    paths['openalex_cache_directory'] = os.getenv(
        'OPENALEX_CACHE_DIR', paths['openalex_cache_directory']
    )
    paths['log_directory'] = os.getenv('LOG_DIRECTORY', paths['log_directory'])

    openalex_raw = raw.get('openalex', {})
    openalex_raw['email'] = os.getenv('OPENALEX_EMAIL', openalex_raw.get('email', ''))

    return AppConfig(
        database=DatabaseConfig(**db),
        paths=PathsConfig(**paths),
        openalex=OpenAlexConfig(**openalex_raw),
    )
