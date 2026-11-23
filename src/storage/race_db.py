"""race_db.py -- SQLite storage for race/lap data."""

import sqlite3
from datetime import UTC, datetime
from statistics import median

from paths import PROJECT_DIR

DB_PATH = PROJECT_DIR / "race_data.db"

DEFAULT_VARIANT = "modern"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL,
    variant TEXT NOT NULL DEFAULT 'modern',
    UNIQUE(name, variant)
);

CREATE TABLE IF NOT EXISTS drivers (
    id                 INTEGER PRIMARY KEY,
    name               TEXT UNIQUE NOT NULL,
    bonus_description  TEXT
);

CREATE TABLE IF NOT EXISTS races (
    id                 INTEGER PRIMARY KEY,
    track_id           INTEGER NOT NULL REFERENCES tracks(id),
    driver_id          INTEGER REFERENCES drivers(id),
    era_decade         TEXT,
    era_stage          TEXT,
    starting_position  INTEGER,
    timestamp          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS laps (
    id                    INTEGER PRIMARY KEY,
    race_id               INTEGER NOT NULL REFERENCES races(id),
    lap_num               INTEGER,
    compound              TEXT,
    laptime_sec           REAL,
    lap_delta_sec         REAL,
    tyre_used             REAL,
    tyre_gained           REAL,
    fuel_used             REAL,
    fuel_gained           REAL,
    running_avg_laptime_sec REAL,
    weather               TEXT,
    position              INTEGER,
    excluded              INTEGER NOT NULL DEFAULT 0,
    is_pitstop_lap        INTEGER NOT NULL DEFAULT 0,
    is_pit_out_lap        INTEGER NOT NULL DEFAULT 0,
    is_double_stop        INTEGER NOT NULL DEFAULT 0,
    incomplete            INTEGER NOT NULL DEFAULT 0,
    pitstop_time_lost_sec REAL
);
"""

INDEX_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_laps_race ON laps(race_id);
CREATE INDEX IF NOT EXISTS idx_races_track ON races(track_id);
CREATE INDEX IF NOT EXISTS idx_races_driver ON races(driver_id);
"""


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


_MIGRATIONS = {
    "tracks": [("variant", "TEXT NOT NULL DEFAULT 'modern'")],
    "races": [
        ("driver_id", "INTEGER"),
        ("era_decade", "TEXT"),
        ("era_stage", "TEXT"),
        ("starting_position", "INTEGER"),
    ],
    "laps": [
        ("lap_delta_sec", "REAL"),
        ("tyre_gained", "REAL"),
        ("fuel_gained", "REAL"),
        ("running_avg_laptime_sec", "REAL"),
        ("weather", "TEXT"),
        ("position", "INTEGER"),
        ("is_double_stop", "INTEGER NOT NULL DEFAULT 0"),
        ("excluded", "INTEGER NOT NULL DEFAULT 0"),
    ],
}


def _apply_migrations(conn):
    for table, columns in _MIGRATIONS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, decl in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _fix_tracks_unique_constraint(conn):
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='tracks'").fetchone()
    if row is None or "UNIQUE(name, variant)" in (row["sql"] or ""):
        return

    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("PRAGMA legacy_alter_table = ON")
    try:
        conn.execute("ALTER TABLE tracks RENAME TO tracks_old")
        conn.execute(
            """CREATE TABLE tracks (
                id      INTEGER PRIMARY KEY,
                name    TEXT NOT NULL,
                variant TEXT NOT NULL DEFAULT 'modern',
                UNIQUE(name, variant)
            )"""
        )
        conn.execute("INSERT INTO tracks (id, name, variant) SELECT id, name, variant FROM tracks_old")
        conn.execute("DROP TABLE tracks_old")
    finally:
        conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.execute("PRAGMA foreign_keys = ON")


def init_db():
    with _connect() as conn:
        conn.executescript(SCHEMA)
        _apply_migrations(conn)
        _fix_tracks_unique_constraint(conn)
        conn.executescript(INDEX_SCHEMA)


def known_tracks():
    init_db()
    with _connect() as conn:
        rows = conn.execute("SELECT DISTINCT name FROM tracks ORDER BY name").fetchall()
    return [r["name"] for r in rows]


def _get_or_create_track(conn, track_name, variant):
    row = conn.execute("SELECT id FROM tracks WHERE name = ? AND variant = ?", (track_name, variant)).fetchone()
    if row:
        return row["id"]
    return conn.execute("INSERT INTO tracks(name, variant) VALUES (?, ?)", (track_name, variant)).lastrowid


def known_drivers():
    init_db()
    with _connect() as conn:
        rows = conn.execute("SELECT name, bonus_description FROM drivers ORDER BY name").fetchall()
    return [(r["name"], r["bonus_description"]) for r in rows]


def _get_or_create_driver(conn, name):
    if not name:
        return None
    row = conn.execute("SELECT id FROM drivers WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    return conn.execute("INSERT INTO drivers(name) VALUES (?)", (name,)).lastrowid


def add_race_data(
    track_name,
    lap_summaries,
    variant=DEFAULT_VARIANT,
    driver_name=None,
    era_decade=None,
    era_stage=None,
    starting_position=None,
    timestamp=None,
):
    init_db()
    timestamp = timestamp or datetime.now(UTC).isoformat()

    with _connect() as conn:
        track_id = _get_or_create_track(conn, track_name, variant)
        driver_id = _get_or_create_driver(conn, driver_name)

        race_id = conn.execute(
            """INSERT INTO races
               (track_id, driver_id, era_decade, era_stage, starting_position, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (track_id, driver_id, era_decade, era_stage, starting_position, timestamp),
        ).lastrowid

        rows = [
            (
                race_id,
                lap.get("lap"),
                lap.get("compound"),
                lap.get("laptime_sec"),
                lap.get("lap_delta_sec"),
                lap.get("tyre_used"),
                lap.get("tyre_gained"),
                lap.get("fuel_used"),
                lap.get("fuel_gained"),
                lap.get("avg_laptime_sec"),
                lap.get("condition"),
                lap.get("position"),
                int(bool(lap.get("excluded"))),
                int(bool(lap.get("is_pitstop_lap"))),
                int(bool(lap.get("is_pit_out_lap"))),
                int(bool(lap.get("is_double_stop"))),
                int(bool(lap.get("incomplete"))),
                lap.get("pitstop_time_lost_sec"),
            )
            for lap in lap_summaries
        ]
        conn.executemany(
            """INSERT INTO laps
               (race_id, lap_num, compound, laptime_sec, lap_delta_sec, tyre_used,
                tyre_gained, fuel_used, fuel_gained, running_avg_laptime_sec,
                weather, position, excluded, is_pitstop_lap, is_pit_out_lap, is_double_stop,
                incomplete, pitstop_time_lost_sec)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
    return race_id


def list_races():
    """Saved races, newest first: [{"id", "timestamp", "track", "variant", "driver", "lap_count"}]."""
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            """SELECT r.id, r.timestamp, t.name AS track, t.variant, d.name AS driver,
                      (SELECT COUNT(*) FROM laps l WHERE l.race_id = r.id) AS lap_count
               FROM races r JOIN tracks t ON r.track_id = t.id
               LEFT JOIN drivers d ON r.driver_id = d.id
               ORDER BY r.timestamp DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def race_laps(race_id):
    """One race's laps in lap order, keyed like lap_summary's summaries."""
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            """SELECT id AS db_id, lap_num AS lap, compound, laptime_sec, lap_delta_sec, tyre_used, tyre_gained,
                      fuel_used, fuel_gained, running_avg_laptime_sec AS avg_laptime_sec, position,
                      weather AS condition, excluded,
                      is_pitstop_lap, is_pit_out_lap, is_double_stop, incomplete, pitstop_time_lost_sec
               FROM laps WHERE race_id = ? ORDER BY lap_num, id""",
            (race_id,),
        ).fetchall()
    flags = ("is_pitstop_lap", "is_pit_out_lap", "is_double_stop", "incomplete", "excluded")
    return [{k: (bool(v) if k in flags else v) for k, v in dict(r).items()} for r in rows]


def review_laps(db_ids, condition=None, excluded=None):
    """Set condition and/or excluded on saved laps by db_id; None leaves that field alone, "" clears condition."""
    init_db()
    with _connect() as conn:
        if condition is not None:
            conn.executemany("UPDATE laps SET weather = ? WHERE id = ?", [(condition or None, i) for i in db_ids])
        if excluded is not None:
            conn.executemany("UPDATE laps SET excluded = ? WHERE id = ?", [(int(excluded), i) for i in db_ids])


def track_laps(track_name, variant=DEFAULT_VARIANT):
    """Every saved race at a track as [race_laps(race_id), ...], oldest race first."""
    init_db()
    with _connect() as conn:
        ids = [
            r["id"]
            for r in conn.execute(
                """SELECT r.id FROM races r JOIN tracks t ON r.track_id = t.id
               WHERE t.name = ? AND t.variant = ? ORDER BY r.timestamp""",
                (track_name, variant),
            ).fetchall()
        ]
    return [race_laps(race_id) for race_id in ids]


def median_pit_loss(track_name, variant=DEFAULT_VARIANT):
    """(median single-stop pit loss in sec, sample count) for a track; (None, 0) if no data."""
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            """SELECT l.pitstop_time_lost_sec FROM laps l
               JOIN races r ON l.race_id = r.id JOIN tracks t ON r.track_id = t.id
               WHERE t.name = ? AND t.variant = ? AND l.incomplete = 0 AND l.excluded = 0
                 AND l.is_double_stop = 0 AND l.pitstop_time_lost_sec IS NOT NULL""",
            (track_name, variant),
        ).fetchall()
    losses = [r["pitstop_time_lost_sec"] for r in rows]
    return (median(losses) if losses else None), len(losses)
