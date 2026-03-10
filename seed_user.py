#!/usr/bin/env python3
"""CLI tool to add users to the database.

Usage:
    python seed_user.py <username> <password>
"""
import asyncio
import sys
import os

import asyncpg
import bcrypt
from dotenv import load_dotenv

load_dotenv()
DB_URL = os.getenv("DB_URL", "postgresql://admin:admin@localhost:5432/aichat")


async def add_user(username: str, password: str):
    pool = await asyncpg.create_pool(DB_URL)
    try:
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        await pool.execute(
            "INSERT INTO users (username, password_hash) VALUES ($1, $2) "
            "ON CONFLICT (username) DO UPDATE SET password_hash = EXCLUDED.password_hash",
            username, pw_hash
        )
        print(f"User '{username}' created/updated.")
    finally:
        await pool.close()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python seed_user.py <username> <password>")
        sys.exit(1)
    asyncio.run(add_user(sys.argv[1], sys.argv[2]))
