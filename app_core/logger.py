import logging
import json
import sys
from datetime import datetime

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage()
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)

import threading

_log_local = threading.local()

class ThreadLocalCallbackHandler(logging.Handler):
    def emit(self, record):
        cb = getattr(_log_local, "log_callback", None)
        if cb:
            msg = self.format(record)
            cb(msg)

def setup_logger(name="lambadaclips"):
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        
        # Stdout JSON handler
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setFormatter(JSONFormatter())
        logger.addHandler(stdout_handler)
        
        # Thread-local callback handler (for jobs)
        cb_handler = ThreadLocalCallbackHandler()
        # Simple format for UI callbacks
        cb_handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(cb_handler)
        
    return logger

logger = setup_logger()
