import os
import sqlite3
import time

from flask import Flask, g, jsonify, redirect, render_template, request, url_for

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "scores.db")
DEFAULT_DURATION = 120

app = Flask(__name__)


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS match_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            match_number INTEGER NOT NULL DEFAULT 1,
            red_team TEXT NOT NULL DEFAULT '---',
            blue_team TEXT NOT NULL DEFAULT '---',
            red_score INTEGER NOT NULL DEFAULT 0,
            blue_score INTEGER NOT NULL DEFAULT 0,
            red_penalties INTEGER NOT NULL DEFAULT 0,
            blue_penalties INTEGER NOT NULL DEFAULT 0,
            timer_duration INTEGER NOT NULL DEFAULT 120,
            timer_remaining REAL NOT NULL DEFAULT 120,
            timer_end REAL,
            timer_running INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    db.execute(
        "INSERT OR IGNORE INTO match_state (id, timer_duration, timer_remaining)"
        " VALUES (1, ?, ?)",
        (DEFAULT_DURATION, DEFAULT_DURATION),
    )
    db.commit()
    db.close()


def read_state():
    """Return (row, remaining, running), stopping the timer in the DB once it hits zero."""
    db = get_db()
    row = db.execute("SELECT * FROM match_state WHERE id = 1").fetchone()
    if row["timer_running"]:
        remaining = max(0.0, row["timer_end"] - time.time())
    else:
        remaining = row["timer_remaining"]
    running = bool(row["timer_running"]) and remaining > 0
    if row["timer_running"] and not running:
        db.execute(
            "UPDATE match_state SET timer_running = 0, timer_remaining = 0 WHERE id = 1"
        )
        db.commit()
    return row, remaining, running


def state_json():
    row, remaining, running = read_state()
    return jsonify(
        {
            "match_number": row["match_number"],
            "red_team": row["red_team"],
            "blue_team": row["blue_team"],
            "red_score": row["red_score"],
            "blue_score": row["blue_score"],
            "red_penalties": row["red_penalties"],
            "blue_penalties": row["blue_penalties"],
            "timer_duration": row["timer_duration"],
            "timer_remaining": remaining,
            "timer_running": running,
        }
    )


@app.route("/")
def referee():
    return render_template("referee.html")


@app.route("/panel/<team>")
def panel(team):
    if team not in ("red", "blue"):
        return redirect(url_for("referee"))
    return render_template("panel.html", team=team)


@app.route("/display")
def display():
    return render_template("display.html")


@app.route("/admin")
def admin():
    return render_template("admin.html")


@app.route("/api/state")
def api_state():
    return state_json()


@app.route("/api/adjust", methods=["POST"])
def api_adjust():
    data = request.get_json(silent=True) or {}
    team = data.get("team")
    if team not in ("red", "blue"):
        return jsonify({"error": "bad request"}), 400
    col = team + "_score"
    db = get_db()
    if data.get("clear"):
        db.execute("UPDATE match_state SET {0} = 0 WHERE id = 1".format(col))
    else:
        delta = data.get("delta")
        if delta not in (-1, 1, 2):
            return jsonify({"error": "bad request"}), 400
        db.execute(
            "UPDATE match_state SET {0} = MAX(0, {0} + ?) WHERE id = 1".format(col),
            (delta,),
        )
    db.commit()
    return state_json()


@app.route("/api/timer", methods=["POST"])
def api_timer():
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    if action not in ("start", "pause", "reset"):
        return jsonify({"error": "bad request"}), 400
    db = get_db()
    row, remaining, running = read_state()
    if action == "start" and not running:
        if remaining <= 0:
            remaining = row["timer_duration"]
        db.execute(
            "UPDATE match_state SET timer_running = 1, timer_end = ? WHERE id = 1",
            (time.time() + remaining,),
        )
    elif action == "pause" and running:
        db.execute(
            "UPDATE match_state SET timer_running = 0, timer_remaining = ? WHERE id = 1",
            (remaining,),
        )
    elif action == "reset":
        db.execute(
            "UPDATE match_state SET timer_running = 0,"
            " timer_remaining = timer_duration WHERE id = 1"
        )
    db.commit()
    return state_json()


@app.route("/api/setup", methods=["POST"])
def api_setup():
    data = request.get_json(silent=True) or {}
    db = get_db()
    row = db.execute("SELECT * FROM match_state WHERE id = 1").fetchone()

    def clamped_int(value, lo, hi):
        return min(max(int(value), lo), hi)

    try:
        match_number = clamped_int(data.get("match_number", row["match_number"]), 1, 9999)
        duration = clamped_int(data.get("timer_duration", row["timer_duration"]), 5, 3600)
    except (TypeError, ValueError):
        return jsonify({"error": "match number and match length must be whole numbers"}), 400

    red_team = (str(data.get("red_team", row["red_team"])).strip() or "---")[:20]
    blue_team = (str(data.get("blue_team", row["blue_team"])).strip() or "---")[:20]

    db.execute(
        "UPDATE match_state SET match_number = ?, red_team = ?, blue_team = ?,"
        " timer_duration = ? WHERE id = 1",
        (match_number, red_team, blue_team, duration),
    )
    if duration != row["timer_duration"]:
        db.execute(
            "UPDATE match_state SET timer_running = 0, timer_remaining = ? WHERE id = 1",
            (duration,),
        )
    if data.get("reset_scores"):
        db.execute(
            "UPDATE match_state SET red_score = 0, blue_score = 0,"
            " red_penalties = 0, blue_penalties = 0 WHERE id = 1"
        )
    db.commit()
    return state_json()


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
