"""Script to verify database connectivity from the command line."""

import os
import sys

# Ensure backend is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.core.config import get_settings
from app.db.session import check_database_connection


def main() -> int:
    settings = get_settings()
    print(f"Connecting to database configured in environment ({settings.environment})...")
    is_healthy, message, details = check_database_connection()

    if is_healthy:
        print(f"[SUCCESS] {message}")
        print(f"Dialect: {details.get('dialect')}")
        print(f"Latency: {details.get('latency_ms')} ms")
        return 0
    else:
        print(f"[FAILURE] {message}")
        print(f"Diagnostic details: {details}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
