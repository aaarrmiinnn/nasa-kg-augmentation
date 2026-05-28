import os
import logging
from logging import Logger


def setup_logger(
    name: str,
    log_filename: str,
    log_directory: str = "logs",
    level: int = logging.DEBUG,
    file_level: int = logging.WARNING,
) -> Logger:
    """
    Set up and return a logger with file and console handlers.
    Unlike edgraph's version, log_directory is configurable (not hardcoded to /app/logs).
    """
    os.makedirs(log_directory, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        file_handler = logging.FileHandler(os.path.join(log_directory, log_filename))
        file_handler.setLevel(file_level)
        formatter = logging.Formatter("%(asctime)s|%(name)s|%(levelname)s|%(message)s")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger
