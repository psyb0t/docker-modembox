import json
import logging
import os
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler

import uvicorn

from .config import Config
from .server import create_app


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {
                "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
                "level": record.levelname,
                "file": record.filename,
                "line": record.lineno,
                "func": record.funcName,
                "msg": record.getMessage(),
                **{key: getattr(record, key) for key in ("modem", "kind") if hasattr(record, key)},
            }
        )


def main():
    os.umask(0o077)
    config = Config.from_env()
    config.data.mkdir(mode=0o700, parents=True, exist_ok=True)
    handlers = [
        logging.StreamHandler(),
        RotatingFileHandler(config.data / "modembox.log", maxBytes=1_048_576, backupCount=3),
    ]
    for handler in handlers:
        handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=handlers)
    logging.getLogger("fastmcp").setLevel(logging.WARNING)
    logging.getLogger("mcp").setLevel(logging.WARNING)
    uvicorn.run(
        create_app(config),
        host="0.0.0.0",
        port=8000,
        workers=1,
        ws_max_size=2048,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
