"""Настройка логов: файл + stdout, без утечки ключей (ключи не логируются)."""
import logging

LOG_FILE = "block_ai_poster.log"


def setup_logging(level: int = logging.INFO, log_file: str = LOG_FILE) -> None:
    handlers = [logging.StreamHandler()]
    try:
        handlers.insert(0, logging.FileHandler(log_file, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=handlers,
    )
