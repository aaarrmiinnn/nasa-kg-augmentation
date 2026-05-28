import pytest
from unittest.mock import patch, MagicMock
from augmentation.common.neo4j_driver import get_driver
from augmentation.common.config_reader import AppConfig, DatabaseConfig, PathsConfig, OpenAlexConfig


class TestGetDriver:
    def test_creates_driver_from_config(self):
        config = AppConfig(
            database=DatabaseConfig(uri="bolt://test:7687", user="testuser", password="testpass"),
            paths=PathsConfig(openalex_cache_directory="/tmp", log_directory="/tmp"),
            openalex=OpenAlexConfig(),
        )
        with patch("augmentation.common.neo4j_driver.GraphDatabase.driver") as mock_driver:
            mock_driver.return_value = MagicMock()
            driver = get_driver(config)
            mock_driver.assert_called_once_with(
                "bolt://test:7687", auth=("testuser", "testpass")
            )

    def test_loads_default_config_when_none(self):
        with patch("augmentation.common.neo4j_driver.load_config") as mock_load, \
             patch("augmentation.common.neo4j_driver.GraphDatabase.driver") as mock_driver:
            mock_load.return_value = AppConfig(
                database=DatabaseConfig(uri="bolt://default:7687", user="neo4j", password="test"),
                paths=PathsConfig(openalex_cache_directory="/tmp", log_directory="/tmp"),
                openalex=OpenAlexConfig(),
            )
            mock_driver.return_value = MagicMock()
            get_driver()
            mock_load.assert_called_once()
            mock_driver.assert_called_once_with(
                "bolt://default:7687", auth=("neo4j", "test")
            )
