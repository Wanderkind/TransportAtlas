"""Portable SQL subset so SQLite can run offline tests; DuckDB is default in production."""
from __future__ import annotations
import os
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS feed_sources (
 source_id VARCHAR PRIMARY KEY, publisher VARCHAR NOT NULL,
 landing_url VARCHAR, download_url VARCHAR, license_name VARCHAR
);
CREATE TABLE IF NOT EXISTS feed_snapshots (
 snapshot_id VARCHAR PRIMARY KEY, source_id VARCHAR NOT NULL,
 content_sha256 VARCHAR NOT NULL, retrieved_at VARCHAR NOT NULL,
 raw_zip_path VARCHAR NOT NULL, import_status VARCHAR NOT NULL,
 timezone VARCHAR, feed_start VARCHAR, feed_end VARCHAR
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_feed_snapshot_content ON feed_snapshots(source_id,content_sha256);
CREATE TABLE IF NOT EXISTS selection_members (
 source_id VARCHAR PRIMARY KEY, snapshot_id VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS agencies (
 snapshot_id VARCHAR NOT NULL, agency_id VARCHAR NOT NULL, name VARCHAR NOT NULL,
 url VARCHAR, timezone VARCHAR NOT NULL,
 PRIMARY KEY(snapshot_id,agency_id)
);
CREATE TABLE IF NOT EXISTS routes (
 snapshot_id VARCHAR NOT NULL, route_id VARCHAR NOT NULL, agency_id VARCHAR,
 short_name VARCHAR, long_name VARCHAR, route_type INTEGER NOT NULL,
 route_color VARCHAR,
 PRIMARY KEY(snapshot_id,route_id)
);
CREATE TABLE IF NOT EXISTS stops (
 snapshot_id VARCHAR NOT NULL, stop_id VARCHAR NOT NULL, name VARCHAR,
 lat DOUBLE, lon DOUBLE, location_type INTEGER, parent_station VARCHAR, timezone VARCHAR,
 PRIMARY KEY(snapshot_id,stop_id)
);
CREATE TABLE IF NOT EXISTS trips (
 snapshot_id VARCHAR NOT NULL, trip_id VARCHAR NOT NULL, route_id VARCHAR NOT NULL,
 service_id VARCHAR NOT NULL, shape_id VARCHAR, direction_id INTEGER,
 headsign VARCHAR, block_id VARCHAR,
 PRIMARY KEY(snapshot_id,trip_id)
);
CREATE TABLE IF NOT EXISTS stop_times (
 snapshot_id VARCHAR NOT NULL, trip_id VARCHAR NOT NULL, stop_sequence INTEGER NOT NULL,
 stop_id VARCHAR NOT NULL, arrival_sec INTEGER, departure_sec INTEGER,
 pickup_type INTEGER, drop_off_type INTEGER, timepoint INTEGER,
 PRIMARY KEY(snapshot_id,trip_id,stop_sequence)
);
CREATE INDEX IF NOT EXISTS idx_stop_times_stop ON stop_times(snapshot_id,stop_id);
CREATE TABLE IF NOT EXISTS calendars (
 snapshot_id VARCHAR NOT NULL, service_id VARCHAR NOT NULL,
 monday INTEGER,tuesday INTEGER,wednesday INTEGER,thursday INTEGER,friday INTEGER,
 saturday INTEGER,sunday INTEGER,start_date VARCHAR,end_date VARCHAR,
 PRIMARY KEY(snapshot_id,service_id)
);
CREATE TABLE IF NOT EXISTS calendar_exceptions (
 snapshot_id VARCHAR NOT NULL, service_id VARCHAR NOT NULL,
 service_date VARCHAR NOT NULL, exception_type INTEGER NOT NULL,
 PRIMARY KEY(snapshot_id,service_id,service_date)
);
CREATE TABLE IF NOT EXISTS shapes (
 snapshot_id VARCHAR NOT NULL, shape_id VARCHAR NOT NULL,
 point_sequence INTEGER NOT NULL, lat DOUBLE NOT NULL, lon DOUBLE NOT NULL,
 PRIMARY KEY(snapshot_id,shape_id,point_sequence)
);
CREATE TABLE IF NOT EXISTS frequencies (
 snapshot_id VARCHAR NOT NULL, trip_id VARCHAR NOT NULL,
 start_sec INTEGER NOT NULL, end_sec INTEGER NOT NULL, headway_secs INTEGER NOT NULL,
 exact_times INTEGER NOT NULL,
 PRIMARY KEY(snapshot_id,trip_id,start_sec)
);
CREATE TABLE IF NOT EXISTS qa_issues (
 snapshot_id VARCHAR NOT NULL, issue_code VARCHAR NOT NULL,
 severity VARCHAR NOT NULL, entity_id VARCHAR, message VARCHAR
);
"""

def connect(db_path: str | Path, backend: str | None = None):
    backend = backend or os.environ.get("TRANSPORT_ATLAS_DB_BACKEND", "duckdb")
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if backend == "duckdb":
        try:
            import duckdb
        except ImportError as exc:
            raise RuntimeError("DuckDB not installed. Install project dependencies, or set TRANSPORT_ATLAS_DB_BACKEND=sqlite for offline tests.") from exc
        con = duckdb.connect(str(path))
    elif backend == "sqlite":
        con = sqlite3.connect(str(path), check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
    else:
        raise ValueError(f"Unsupported database backend: {backend}")
    # DDL is deliberately portable between DuckDB and SQLite.
    for stmt in SCHEMA.split(";"):
        if stmt.strip():
            con.execute(stmt)
    con.commit()
    return con

def rows(con, sql: str, params: tuple = ()) -> list[dict]:
    # A separate cursor prevents concurrent read operations from sharing result state.
    cur = con.cursor()
    try:
        cur.execute(sql, params)
        names = [item[0] for item in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]
    finally:
        cur.close()

def one(con, sql: str, params: tuple = ()) -> dict | None:
    return next(iter(rows(con, sql, params)), None)
