#!/usr/bin/env python3
"""
Load the Synthea hospital extract into a SQLite database so the analysis can be
done in SQL rather than only in pandas.

Produces: data/hospital.db  (six tables, one per source CSV, plus indexes)

Usage:
    python scripts/build_db.py

Source data
-----------
data/raw/*.csv is public Synthea synthetic-patient output (Massachusetts General
Hospital sample). Re-download from https://github.com/synthetichealth/synthea if
it is missing; row counts will differ between generator releases, which is why the
committed database is the reference point the analysis was verified against.

Loading note
------------
`CODE` (SNOMED-CT) is loaded exactly as the CSV types it, so SQLite stores it
as INTEGER rather than text. Harmless for this extract: no code in it carries
a leading zero.
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
DB = ROOT / "data" / "hospital.db"

# Table name = CSV stem. `data_dictionary` is metadata, the other five are data.
TABLES = [
    "encounters",
    "procedures",
    "patients",
    "payers",
    "organizations",
    "data_dictionary",
]

# Indexes on the columns the analysis actually joins and filters on. SQLite would
# otherwise full-scan `procedures` (47,701 rows) for every encounter lookup.
INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_encounters_patient  ON encounters(PATIENT)",
    "CREATE INDEX IF NOT EXISTS ix_encounters_payer    ON encounters(PAYER)",
    "CREATE INDEX IF NOT EXISTS ix_encounters_class    ON encounters(ENCOUNTERCLASS)",
    "CREATE INDEX IF NOT EXISTS ix_encounters_start    ON encounters(START)",
    "CREATE INDEX IF NOT EXISTS ix_procedures_encounter ON procedures(ENCOUNTER)",
    "CREATE INDEX IF NOT EXISTS ix_procedures_patient   ON procedures(PATIENT)",
]

# The scale the analysis was written against. Asserted rather than assumed: a
# regenerated Synthea run produces different counts, and every headline figure
# would quietly move with it.
EXPECTED_ROWS = {
    "encounters": 27_891,
    "procedures": 47_701,
    "patients": 974,
    "payers": 10,
    "organizations": 1,
}

# Foreign keys that must resolve. A non-zero orphan count means every JOIN in the
# notebook is silently lossy.
FK_CHECKS = [
    ("encounters.PATIENT -> patients.Id",
     "SELECT COUNT(*) FROM encounters e LEFT JOIN patients p ON e.PATIENT = p.Id WHERE p.Id IS NULL"),
    ("encounters.PAYER -> payers.Id",
     "SELECT COUNT(*) FROM encounters e LEFT JOIN payers p ON e.PAYER = p.Id WHERE p.Id IS NULL"),
    ("encounters.ORGANIZATION -> organizations.Id",
     "SELECT COUNT(*) FROM encounters e LEFT JOIN organizations o ON e.ORGANIZATION = o.Id WHERE o.Id IS NULL"),
    ("procedures.ENCOUNTER -> encounters.Id",
     "SELECT COUNT(*) FROM procedures pr LEFT JOIN encounters e ON pr.ENCOUNTER = e.Id WHERE e.Id IS NULL"),
    ("procedures.PATIENT -> patients.Id",
     "SELECT COUNT(*) FROM procedures pr LEFT JOIN patients p ON pr.PATIENT = p.Id WHERE p.Id IS NULL"),
]


def main() -> int:
    missing = [t for t in TABLES if not (RAW / f"{t}.csv").exists()]
    if missing:
        print(f"ERROR: missing source CSV(s) in {RAW}:", file=sys.stderr)
        for t in missing:
            print(f"       {t}.csv", file=sys.stderr)
        print("       See the module docstring for where to get them.", file=sys.stderr)
        return 1

    DB.parent.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        DB.unlink()  # build fresh: to_sql(if_exists="replace") would leave stale tables behind

    db = sqlite3.connect(DB)
    for table in TABLES:
        df = pd.read_csv(RAW / f"{table}.csv")
        df.to_sql(table, db, if_exists="replace", index=False)
        print(f"[ok  ] {table:18s} {len(df):7,d} rows  {df.shape[1]:2d} cols")

    for stmt in INDEXES:
        db.execute(stmt)
    db.commit()
    print(f"[ok  ] {len(INDEXES)} indexes created")

    # --- assert the scale the analysis assumes -------------------------------
    for table, expected in EXPECTED_ROWS.items():
        actual = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert actual == expected, f"{table}: expected {expected:,} rows, built {actual:,}. "\
                                  f"Regenerate the notebook's figures only if you intend this."
    print(f"[ok  ] row counts match the analysis baseline "
          f"({sum(EXPECTED_ROWS.values()):,} rows across {len(EXPECTED_ROWS)} data tables)")

    # --- assert referential integrity ---------------------------------------
    for label, sql in FK_CHECKS:
        orphans = db.execute(sql).fetchone()[0]
        assert orphans == 0, f"referential integrity broken: {label} has {orphans} orphans"
    print(f"[ok  ] referential integrity: 0 orphans across {len(FK_CHECKS)} foreign keys")

    n_tables = db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    print(f"[ok  ] {DB.relative_to(ROOT)}: {n_tables} tables, {DB.stat().st_size / 1e6:.1f} MB")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
