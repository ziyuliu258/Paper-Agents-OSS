#!/usr/bin/env python3
"""Generate a hashed verifier for the password-protected server .env runtime mode."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.env_runtime_access import create_env_runtime_password_hash


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate ENV_RUNTIME_PASSWORD_HASH for password-protected server .env mode. "
            "The plaintext password is never written to disk by this script."
        )
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=390000,
        help="PBKDF2-SHA256 iterations (default: 390000).",
    )
    args = parser.parse_args()

    password = getpass.getpass("Enter the .env runtime unlock password: ")
    if not password:
        raise SystemExit("Password cannot be empty.")
    password_confirm = getpass.getpass("Re-enter the password to confirm: ")
    if password_confirm != password:
        raise SystemExit("Passwords did not match.")

    verifier = create_env_runtime_password_hash(
        password,
        iterations=args.iterations,
    )
    print("Put the following line in your .env file:")
    print(f"ENV_RUNTIME_PASSWORD_HASH={verifier}")
    print(
        "Only the hash goes into .env. Keep the plaintext password outside the repo and "
        "unlock it from /settings when using server .env mode."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
