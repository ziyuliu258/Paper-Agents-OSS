import logging
import sys

_FMT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATE_FMT = "%H:%M:%S"


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_FMT, datefmt=_DATE_FMT))
        logger.addHandler(handler)
    logger.setLevel(level)
    return logger
