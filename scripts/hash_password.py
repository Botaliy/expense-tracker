"""Generate a bcrypt hash for AUTH_PASSWORD_HASH in .env.

Usage:
    uv run python scripts/hash_password.py "your-password"
"""

import sys

sys.path.insert(0, ".")

from app.auth import hash_password  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/hash_password.py <password>")
        raise SystemExit(1)
    print(hash_password(sys.argv[1]))


if __name__ == "__main__":
    main()
