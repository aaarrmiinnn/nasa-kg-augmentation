from neo4j import GraphDatabase, Driver
from augmentation.common.config_reader import AppConfig, load_config


def get_driver(config: AppConfig = None) -> Driver:
    """
    Get a Neo4j driver instance from config.
    Falls back to loading config from the default config file.
    """
    if config is None:
        config = load_config()

    return GraphDatabase.driver(
        config.database.uri,
        auth=(config.database.user, config.database.password),
    )
