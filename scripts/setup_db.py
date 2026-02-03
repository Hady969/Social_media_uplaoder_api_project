#!/usr/bin/env python3
"""
Database setup script for X Analytics Dashboard.

This script helps you set up PostgreSQL for the application.
Run this script to create the database and run migrations.

Usage:
    python scripts/setup_db.py

Prerequisites:
    1. Install PostgreSQL
    2. Create a database user (or use default 'postgres')
    3. Update .env with your database credentials
"""

import subprocess
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def check_postgres_connection():
    """Check if PostgreSQL is accessible."""
    try:
        from app.config import get_settings
        from sqlalchemy import create_engine, text

        settings = get_settings()
        engine = create_engine(settings.database_url_sync)

        with engine.connect() as conn:
            result = conn.execute(text("SELECT version()"))
            version = result.scalar()
            print(f"Connected to PostgreSQL!")
            print(f"Version: {version}")
            return True
    except Exception as e:
        print(f"Failed to connect to PostgreSQL: {e}")
        return False


def create_database():
    """Create the database if it doesn't exist."""
    try:
        from app.config import get_settings
        from sqlalchemy import create_engine, text
        from sqlalchemy.exc import ProgrammingError

        settings = get_settings()

        # Connect to default 'postgres' database to create our database
        # Parse the database URL to get the database name
        db_name = settings.database_url_sync.split("/")[-1]
        base_url = settings.database_url_sync.rsplit("/", 1)[0] + "/postgres"

        engine = create_engine(base_url, isolation_level="AUTOCOMMIT")

        with engine.connect() as conn:
            # Check if database exists
            result = conn.execute(
                text(f"SELECT 1 FROM pg_database WHERE datname = '{db_name}'")
            )
            exists = result.scalar()

            if not exists:
                conn.execute(text(f'CREATE DATABASE "{db_name}"'))
                print(f"Database '{db_name}' created successfully!")
            else:
                print(f"Database '{db_name}' already exists.")

        return True
    except Exception as e:
        print(f"Failed to create database: {e}")
        return False


def run_migrations():
    """Run Alembic migrations."""
    try:
        print("\nRunning database migrations...")
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=Path(__file__).parent.parent,
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            print("Migrations completed successfully!")
            print(result.stdout)
            return True
        else:
            print(f"Migration failed: {result.stderr}")
            return False
    except FileNotFoundError:
        print("Alembic not found. Please install it: pip install alembic")
        return False
    except Exception as e:
        print(f"Failed to run migrations: {e}")
        return False


def main():
    print("=" * 50)
    print("X Analytics Dashboard - Database Setup")
    print("=" * 50)
    print()

    # Step 1: Check PostgreSQL connection
    print("Step 1: Checking PostgreSQL connection...")
    if not check_postgres_connection():
        print("\nPlease ensure PostgreSQL is installed and running.")
        print("Update .env with your database credentials:")
        print("  DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/dbname")
        print("  DATABASE_URL_SYNC=postgresql://user:pass@localhost:5432/dbname")
        sys.exit(1)

    print()

    # Step 2: Create database if needed
    print("Step 2: Creating database...")
    if not create_database():
        print("\nFailed to create database. Please create it manually:")
        print("  createdb xanalytics")
        print("or")
        print("  CREATE DATABASE xanalytics;")
        sys.exit(1)

    print()

    # Step 3: Run migrations
    print("Step 3: Running migrations...")
    if not run_migrations():
        print("\nFailed to run migrations. Try running manually:")
        print("  cd backend")
        print("  alembic upgrade head")
        sys.exit(1)

    print()
    print("=" * 50)
    print("Database setup completed successfully!")
    print("=" * 50)
    print()
    print("Next steps:")
    print("1. Start the backend: uvicorn app.main:app --reload")
    print("2. Start the frontend: npm run dev")


if __name__ == "__main__":
    main()
