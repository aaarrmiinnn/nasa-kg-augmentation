import os
import logging
import pytest
from augmentation.common.logger_setup import setup_logger


class TestSetupLogger:
    def test_creates_logger(self, tmp_path):
        logger = setup_logger("test_logger", "test.log", log_directory=str(tmp_path))
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test_logger"

    def test_creates_log_directory(self, tmp_path):
        log_dir = str(tmp_path / "newdir")
        setup_logger("test_dir", "test.log", log_directory=log_dir)
        assert os.path.isdir(log_dir)

    def test_creates_log_file_on_write(self, tmp_path):
        logger = setup_logger(
            "test_file_logger", "test.log",
            log_directory=str(tmp_path),
            file_level=logging.DEBUG,
        )
        logger.warning("test message")
        # Flush handlers
        for handler in logger.handlers:
            handler.flush()
        assert os.path.exists(os.path.join(str(tmp_path), "test.log"))

    def test_no_duplicate_handlers(self, tmp_path):
        logger1 = setup_logger("dup_test", "dup.log", log_directory=str(tmp_path))
        handler_count = len(logger1.handlers)
        logger2 = setup_logger("dup_test", "dup.log", log_directory=str(tmp_path))
        assert len(logger2.handlers) == handler_count
        assert logger1 is logger2
