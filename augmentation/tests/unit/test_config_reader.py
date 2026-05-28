import json
import os
import pytest
from augmentation.common.config_reader import load_config, AppConfig, DatabaseConfig, PathsConfig, OpenAlexConfig


class TestLoadConfig:
    def test_loads_from_default_path(self):
        config = load_config()
        assert isinstance(config, AppConfig)
        assert isinstance(config.database, DatabaseConfig)
        assert isinstance(config.paths, PathsConfig)
        assert isinstance(config.openalex, OpenAlexConfig)

    def test_loads_from_custom_path(self, tmp_path):
        config_data = {
            "database": {"uri": "bolt://custom:7687", "user": "admin", "password": "secret"},
            "paths": {"openalex_cache_directory": "/tmp/cache", "log_directory": "/tmp/logs"},
            "openalex": {"email": "test@example.com", "requests_per_second": 5, "batch_size": 25},
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config_data))

        config = load_config(str(config_file))
        assert config.database.uri == "bolt://custom:7687"
        assert config.database.user == "admin"
        assert config.paths.openalex_cache_directory == "/tmp/cache"
        assert config.openalex.email == "test@example.com"
        assert config.openalex.requests_per_second == 5

    def test_env_var_overrides_database(self, tmp_path, monkeypatch):
        config_data = {
            "database": {"uri": "bolt://default:7687", "user": "neo4j", "password": "test"},
            "paths": {"openalex_cache_directory": "/tmp/cache", "log_directory": "/tmp/logs"},
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config_data))

        monkeypatch.setenv("NEO4J_URI", "bolt://override:7687")
        monkeypatch.setenv("NEO4J_USER", "override_user")
        monkeypatch.setenv("NEO4J_PASSWORD", "override_pass")

        config = load_config(str(config_file))
        assert config.database.uri == "bolt://override:7687"
        assert config.database.user == "override_user"
        assert config.database.password == "override_pass"

    def test_env_var_overrides_openalex_email(self, tmp_path, monkeypatch):
        config_data = {
            "database": {"uri": "bolt://localhost:7687", "user": "neo4j", "password": "test"},
            "paths": {"openalex_cache_directory": "/tmp/cache", "log_directory": "/tmp/logs"},
            "openalex": {"email": "", "requests_per_second": 10, "batch_size": 50},
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config_data))

        monkeypatch.setenv("OPENALEX_EMAIL", "polite@nasa.gov")
        config = load_config(str(config_file))
        assert config.openalex.email == "polite@nasa.gov"

    def test_missing_openalex_section_uses_defaults(self, tmp_path):
        config_data = {
            "database": {"uri": "bolt://localhost:7687", "user": "neo4j", "password": "test"},
            "paths": {"openalex_cache_directory": "/tmp/cache", "log_directory": "/tmp/logs"},
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config_data))

        config = load_config(str(config_file))
        assert config.openalex.email == ""
        assert config.openalex.requests_per_second == 10
        assert config.openalex.batch_size == 50
