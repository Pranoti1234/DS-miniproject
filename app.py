import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request
from flask_cors import CORS


BLOOD_GROUPS = ("A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-")
BANKS = (
    ("BB-101", "Apex Metro Blood Center (Simulated)", "Mumbai", "+91-22-5550-0101"),
    ("BB-102", "Harborview Blood Center (Simulated)", "Pune", "+91-20-5550-0102"),
    ("BB-103", "Sunrise Regional Blood Center (Simulated)", "Nagpur", "+91-712-5550-0103"),
    ("BB-104", "Evergreen Community Blood Center (Simulated)", "Nashik", "+91-253-5550-0104"),
    ("BB-105", "Crimson Health Depot (Simulated)", "Aurangabad", "+91-240-5550-0105"),
    ("BB-106", "Riverbend Blood Center (Simulated)", "Kolhapur", "+91-231-5550-0106"),
    ("BB-107", "Summit Regional Blood Center (Simulated)", "Solapur", "+91-217-5550-0107"),
    ("BB-108", "Lakeside Blood Center (Simulated)", "Thane", "+91-22-5550-0108"),
    ("BB-109", "Meadowlands Blood Center (Simulated)", "Satara", "+91-2162-5550-0109"),
    ("BB-110", "Central Valley Blood Center (Simulated)", "Navi Mumbai", "+91-22-5550-0110"),
)
SEED_STOCK = (
    (58, 18, 70, 15, 28, 8, 85, 20),
    (22, 7, 30, 6, 13, 4, 38, 9),
    (20, 6, 28, 6, 12, 4, 34, 8),
    (19, 6, 27, 6, 11, 3, 32, 8),
    (18, 6, 26, 5, 11, 3, 30, 8),
    (17, 5, 25, 5, 10, 3, 29, 7),
    (16, 5, 24, 5, 10, 3, 28, 7),
    (16, 5, 23, 5, 9, 3, 27, 7),
    (16, 5, 22, 5, 9, 3, 26, 7),
    (16, 5, 32, 6, 14, 3, 55, 12),
)


@contextmanager
def connect(path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 10000")
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize_database(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS banks (
                bank_id TEXT PRIMARY KEY, name TEXT NOT NULL,
                city TEXT NOT NULL, contact TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS inventory (
                bank_id TEXT NOT NULL REFERENCES banks(bank_id),
                blood_group TEXT NOT NULL,
                units INTEGER NOT NULL CHECK(units >= 0),
                PRIMARY KEY (bank_id, blood_group)
            );
            """
        )
        if db.execute("SELECT COUNT(*) FROM banks").fetchone()[0] == 0:
            for bank, stock in zip(BANKS, SEED_STOCK):
                db.execute("INSERT INTO banks VALUES (?, ?, ?, ?)", bank)
                db.executemany(
                    "INSERT INTO inventory VALUES (?, ?, ?)",
                    [(bank[0], group, units) for group, units in zip(BLOOD_GROUPS, stock)],
                )


def create_app(db_path=None):
    db_path = db_path or os.environ.get("INVENTORY_DB_PATH", "data/inventory.db")
    initialize_database(db_path)
    app = Flask(__name__)
    CORS(app)

    @app.get("/health")
    def health():
        with connect(db_path) as db:
            db.execute("SELECT 1")
        return jsonify(status="healthy", service="inventory-service", database="sqlite-connected",
                       timestamp=datetime.now(timezone.utc).isoformat())

    @app.get("/api/banks")
    def banks():
        with connect(db_path) as db:
            rows = db.execute(
                "SELECT b.*, COALESCE(SUM(i.units), 0) total_units FROM banks b "
                "LEFT JOIN inventory i USING(bank_id) GROUP BY b.bank_id ORDER BY b.bank_id"
            ).fetchall()
        return jsonify(status="success", count=len(rows), data=[dict(row) for row in rows])

    @app.get("/api/inventory")
    def inventory():
        group = request.args.get("blood_group")
        if group and group not in BLOOD_GROUPS:
            return jsonify(status="error", message="Invalid blood_group"), 400
        with connect(db_path) as db:
            if group:
                rows = db.execute(
                    "SELECT b.bank_id, b.name AS bank_name, i.blood_group, i.units "
                    "FROM inventory i JOIN banks b USING(bank_id) WHERE i.blood_group=? ORDER BY b.bank_id",
                    (group,),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT b.bank_id, b.name AS bank_name, i.blood_group, i.units "
                    "FROM inventory i JOIN banks b USING(bank_id) ORDER BY b.bank_id, i.blood_group"
                ).fetchall()
        return jsonify(status="success", count=len(rows), data=[dict(row) for row in rows])

    @app.get("/api/inventory/summary")
    def summary():
        with connect(db_path) as db:
            groups = {row["blood_group"]: row["units"] for row in db.execute(
                "SELECT blood_group, SUM(units) units FROM inventory GROUP BY blood_group"
            )}
            count = db.execute("SELECT COUNT(*) FROM banks").fetchone()[0]
        return jsonify(status="success", data={
            "groups": {group: groups.get(group, 0) for group in BLOOD_GROUPS},
            "grand_total": sum(groups.values()), "total_banks": count,
        })

    @app.post("/api/reserve")
    def reserve():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(status="error", message="A JSON object is required"), 400
        group, units = payload.get("blood_group"), payload.get("units")
        if group not in BLOOD_GROUPS or isinstance(units, bool) or not isinstance(units, int) or units <= 0:
            return jsonify(status="error", message="blood_group and positive integer units are required"), 400
        with connect(db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT i.bank_id, b.name, i.units FROM inventory i JOIN banks b USING(bank_id) "
                "WHERE i.blood_group=? AND i.units>=? ORDER BY i.bank_id", (group, units)
            ).fetchall()
            if rows:
                bank = rows[0]
                db.execute(
                    "UPDATE inventory SET units=units-? WHERE bank_id=? AND blood_group=? AND units>=?",
                    (units, bank["bank_id"], group, units),
                )
                reservation = {
                    "success": True, "bank_id": bank["bank_id"], "bank_name": bank["name"],
                    "blood_group": group, "units_allocated": units, "remaining_units": bank["units"] - units,
                }
                return jsonify(status="success", reservation=reservation)
        return jsonify(status="failed", reservation={
            "success": False, "message": f"No single blood bank has {units} units of {group} available.",
            "units_requested": units,
        }), 422

    @app.post("/api/reset")
    def reset():
        with connect(db_path) as db:
            for bank, stock in zip(BANKS, SEED_STOCK):
                db.executemany("UPDATE inventory SET units=? WHERE bank_id=? AND blood_group=?",
                               [(units, bank[0], group) for group, units in zip(BLOOD_GROUPS, stock)])
        return jsonify(status="success", message="Inventory reset to demo seed data")

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5001")), threaded=True)
