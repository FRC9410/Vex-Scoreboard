import os
import random
import sqlite3
import time

from flask import Flask, g, jsonify, redirect, render_template, request, url_for

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "scores.db")

# GEM QUEST match: 0:20 auto + 1:40 teleop + 0:30 endgame = 2:30
DEFAULT_AUTO = 20
DEFAULT_DRIVER = 100
DEFAULT_ENDGAME = 30

# Two head-to-head alliance slots. The internal keys stay "yellow"/"green"
# (they name every score column and API field); the DISPLAYED name and color
# of each slot are configurable in the admin Setup tab.
TEAMS = ("yellow", "green")

DEFAULT_CONFIG = {
    "ally_a_name": "YELLOW",
    "ally_a_color": "#e6a800",
    "ally_b_name": "GREEN",
    "ally_b_color": "#1fa14a",
    "elim_num_teams": 4,
    "elim_double": 1,
    "elim_gf_best_of": 1,
}

# Default roster, seeded into the editable `teams` table on first run.
ROSTER = ["Obsidian", "Ruby", "Amethyst", "Quartz", "Lapis", "Emerald", "Topaz"]

RESULT_FIELDS = (
    "yellow_score", "green_score",
    "yellow_fouls", "yellow_majors", "green_fouls", "green_majors",
)

FOUL_POINTS = {"foul": 1, "major": 5}

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


# ===================================================================
#  Schema + migrations
# ===================================================================

def init_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS match_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            match_number INTEGER NOT NULL DEFAULT 1,
            yellow_team TEXT NOT NULL DEFAULT '---',
            green_team TEXT NOT NULL DEFAULT '---',
            yellow_score INTEGER NOT NULL DEFAULT 0,
            green_score INTEGER NOT NULL DEFAULT 0,
            yellow_fouls INTEGER NOT NULL DEFAULT 0,
            yellow_majors INTEGER NOT NULL DEFAULT 0,
            green_fouls INTEGER NOT NULL DEFAULT 0,
            green_majors INTEGER NOT NULL DEFAULT 0,
            auto_duration INTEGER NOT NULL DEFAULT {auto},
            driver_duration INTEGER NOT NULL DEFAULT {driver},
            endgame_duration INTEGER NOT NULL DEFAULT {endgame},
            schedule_index INTEGER NOT NULL DEFAULT 0,
            phase TEXT NOT NULL DEFAULT 'quali',
            elim_index INTEGER NOT NULL DEFAULT 0,
            elim_game INTEGER NOT NULL DEFAULT 1,
            timer_duration INTEGER NOT NULL DEFAULT {total},
            timer_remaining REAL NOT NULL DEFAULT {total},
            timer_end REAL,
            timer_running INTEGER NOT NULL DEFAULT 0
        )
        """.format(
            auto=DEFAULT_AUTO,
            driver=DEFAULT_DRIVER,
            endgame=DEFAULT_ENDGAME,
            total=DEFAULT_AUTO + DEFAULT_DRIVER + DEFAULT_ENDGAME,
        )
    )
    db.execute("INSERT OR IGNORE INTO match_state (id) VALUES (1)")

    # Migrate databases from when the teams were red/blue.
    existing = {row[1] for row in db.execute("PRAGMA table_info(match_state)")}
    renames = {
        "red_team": "yellow_team", "blue_team": "green_team",
        "red_score": "yellow_score", "blue_score": "green_score",
        "red_fouls": "yellow_fouls", "red_majors": "yellow_majors",
        "blue_fouls": "green_fouls", "blue_majors": "green_majors",
    }
    for old, new in renames.items():
        if old in existing and new not in existing:
            db.execute(
                "ALTER TABLE match_state RENAME COLUMN {0} TO {1}".format(old, new)
            )
            existing.discard(old)
            existing.add(new)

    for col, decl in (
        ("yellow_fouls", "INTEGER NOT NULL DEFAULT 0"),
        ("yellow_majors", "INTEGER NOT NULL DEFAULT 0"),
        ("green_fouls", "INTEGER NOT NULL DEFAULT 0"),
        ("green_majors", "INTEGER NOT NULL DEFAULT 0"),
        ("auto_duration", "INTEGER NOT NULL DEFAULT {0}".format(DEFAULT_AUTO)),
        ("driver_duration", "INTEGER NOT NULL DEFAULT {0}".format(DEFAULT_DRIVER)),
        ("endgame_duration", "INTEGER NOT NULL DEFAULT {0}".format(DEFAULT_ENDGAME)),
        ("schedule_index", "INTEGER NOT NULL DEFAULT 0"),
        ("phase", "TEXT NOT NULL DEFAULT 'quali'"),
        ("elim_index", "INTEGER NOT NULL DEFAULT 0"),
        ("elim_game", "INTEGER NOT NULL DEFAULT 1"),
    ):
        name = col.split()[0]
        if name not in existing:
            db.execute("ALTER TABLE match_state ADD COLUMN {0} {1}".format(col, decl))
            existing.add(name)
            if name == "endgame_duration":
                total = DEFAULT_AUTO + DEFAULT_DRIVER + DEFAULT_ENDGAME
                db.execute(
                    "UPDATE match_state SET timer_duration = ?,"
                    " timer_remaining = ?, timer_running = 0 WHERE id = 1",
                    (total, total),
                )

    # Config (single row): alliance names/colors + elim format.
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            ally_a_name TEXT NOT NULL DEFAULT 'YELLOW',
            ally_a_color TEXT NOT NULL DEFAULT '#e6a800',
            ally_b_name TEXT NOT NULL DEFAULT 'GREEN',
            ally_b_color TEXT NOT NULL DEFAULT '#1fa14a',
            elim_num_teams INTEGER NOT NULL DEFAULT 4,
            elim_double INTEGER NOT NULL DEFAULT 1,
            elim_gf_best_of INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    db.execute("INSERT OR IGNORE INTO config (id) VALUES (1)")

    # Editable roster (replaces the hardcoded ROSTER constant).
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            position INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    if db.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 0:
        for i, name in enumerate(ROSTER):
            db.execute(
                "INSERT INTO teams (name, position) VALUES (?, ?)", (name, i)
            )

    # Editable quali pairings.
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS quali_matches (
            match_index INTEGER PRIMARY KEY,
            rotation INTEGER NOT NULL,
            yellow_team TEXT NOT NULL,
            green_team TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS results (
            match_index INTEGER PRIMARY KEY,
            yellow_score INTEGER NOT NULL,
            green_score INTEGER NOT NULL,
            yellow_fouls INTEGER NOT NULL DEFAULT 0,
            yellow_majors INTEGER NOT NULL DEFAULT 0,
            green_fouls INTEGER NOT NULL DEFAULT 0,
            green_majors INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    if db.execute("SELECT COUNT(*) FROM quali_matches").fetchone()[0] == 0:
        rebuild_quali(db)

    # Elim seeds. Older DBs constrained seed BETWEEN 1 AND 4; brackets can now
    # be up to 8 teams, so drop the old constrained table (seeds are transient).
    seeds_sql = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='elim_seeds'"
    ).fetchone()
    if seeds_sql and "BETWEEN 1 AND 4" in (seeds_sql[0] or ""):
        db.execute("DROP TABLE elim_seeds")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS elim_seeds (
            seed INTEGER PRIMARY KEY,
            team TEXT NOT NULL
        )
        """
    )

    # Per-game elim results: an elim match can be a series (Bo1/Bo3) or gain
    # extra rematch/tiebreaker games.
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS elim_games (
            match_index INTEGER NOT NULL,
            game_number INTEGER NOT NULL,
            yellow_score INTEGER NOT NULL DEFAULT 0,
            green_score INTEGER NOT NULL DEFAULT 0,
            yellow_fouls INTEGER NOT NULL DEFAULT 0,
            yellow_majors INTEGER NOT NULL DEFAULT 0,
            green_fouls INTEGER NOT NULL DEFAULT 0,
            green_majors INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (match_index, game_number)
        )
        """
    )
    # Migrate a legacy single-result-per-match elim table into game 1.
    legacy = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='elim_results'"
    ).fetchone()
    if legacy and db.execute("SELECT COUNT(*) FROM elim_games").fetchone()[0] == 0:
        for r in db.execute("SELECT * FROM elim_results"):
            db.execute(
                "INSERT OR IGNORE INTO elim_games (match_index, game_number, {0})"
                " VALUES (?, 1, ?, ?, ?, ?, ?, ?)".format(", ".join(RESULT_FIELDS)),
                (r["match_index"],) + tuple(r[f] for f in RESULT_FIELDS),
            )

    db.commit()
    db.close()


# ===================================================================
#  Config + teams
# ===================================================================

def get_config(db):
    row = db.execute("SELECT * FROM config WHERE id = 1").fetchone()
    if row is None:
        return dict(DEFAULT_CONFIG)
    return {k: row[k] for k in DEFAULT_CONFIG}


def get_teams(db):
    return [
        r["name"] for r in db.execute(
            "SELECT name FROM teams ORDER BY position, id"
        )
    ]


# ===================================================================
#  Qualification schedule (auto round-robin, circle method)
# ===================================================================

def generate_round_robin(team_list):
    """Full single round-robin: every team plays every other once. Returns a
    list of rounds; each round is a list of (yellow, green) pairings, with a
    bye for the odd team out."""
    teams = list(team_list)
    if len(teams) < 2:
        return []
    arr = teams[:]
    if len(arr) % 2:
        arr.append(None)  # phantom opponent → whoever draws it gets a bye
    m = len(arr)
    rounds = []
    for r in range(m - 1):
        pairs = []
        for i in range(m // 2):
            a, b = arr[i], arr[m - 1 - i]
            if a is None or b is None:
                continue
            # Alternate which side is yellow so home/away stays balanced.
            pairs.append((a, b) if (r + i) % 2 == 0 else (b, a))
        rounds.append(pairs)
        arr = [arr[0]] + [arr[-1]] + arr[1:-1]  # rotate, first fixed
    return rounds


def rebuild_quali(db):
    """Regenerate the whole quali schedule from the current roster and wipe any
    saved quali results (pairings no longer line up)."""
    db.execute("DELETE FROM quali_matches")
    db.execute("DELETE FROM results")
    i = 0
    for rot, pairs in enumerate(generate_round_robin(get_teams(db)), start=1):
        for yellow, green in pairs:
            db.execute(
                "INSERT INTO quali_matches (match_index, rotation, yellow_team,"
                " green_team) VALUES (?, ?, ?, ?)",
                (i, rot, yellow, green),
            )
            i += 1


def get_quali_matches(db):
    return [
        dict(r) for r in db.execute(
            "SELECT * FROM quali_matches ORDER BY match_index"
        )
    ]


def quali_count(db):
    return db.execute("SELECT COUNT(*) FROM quali_matches").fetchone()[0]


def compute_byes(db, quali):
    """The team(s) of the roster not playing in each rotation."""
    roster = set(get_teams(db))
    byes = {}
    for m in quali:
        byes.setdefault(m["rotation"], set(roster))
        byes[m["rotation"]] -= {m["yellow_team"], m["green_team"]}
    return {rot: " / ".join(sorted(t)) if t else "—" for rot, t in byes.items()}


def compute_rankings(db, quali):
    """Standings from completed quali matches: Win = 2 RP, Tie = 1. Ties broken
    by total match points, then fewest penalty points given away, then name."""
    stats = {
        name: {
            "team": name, "played": 0, "wins": 0, "losses": 0, "ties": 0,
            "rp": 0, "points": 0, "fouls": 0, "majors": 0, "penalty_points": 0,
        }
        for name in get_teams(db)
    }
    pairings = {m["match_index"]: m for m in quali}
    for r in db.execute("SELECT * FROM results"):
        m = pairings.get(r["match_index"])
        if m is None:
            continue
        for side, opp in (("yellow", "green"), ("green", "yellow")):
            name = m[side + "_team"]
            if name not in stats:
                continue
            s = stats[name]
            s["played"] += 1
            s["points"] += r[side + "_score"]
            s["fouls"] += r[side + "_fouls"]
            s["majors"] += r[side + "_majors"]
            if r[side + "_score"] > r[opp + "_score"]:
                s["wins"] += 1
            elif r[side + "_score"] < r[opp + "_score"]:
                s["losses"] += 1
            else:
                s["ties"] += 1
    for s in stats.values():
        s["rp"] = 2 * s["wins"] + s["ties"]
        s["penalty_points"] = (
            s["fouls"] * FOUL_POINTS["foul"] + s["majors"] * FOUL_POINTS["major"]
        )
    return sorted(
        stats.values(),
        key=lambda s: (-s["rp"], -s["points"], s["penalty_points"], s["team"]),
    )


# ===================================================================
#  Elimination bracket engine
# ===================================================================

def build_bracket_def(num_teams, double, gf_best_of):
    """Ordered list of bracket slots for the chosen format. Each slot names its
    two competitors by feeder: ('seed', k) / ('win', slot) / ('lose', slot).
    Slots are always listed in a valid play order (feeders come earlier)."""
    slots = []

    def add(label, a, b, best_of=1):
        slots.append({"label": label, "a": a, "b": b, "best_of": best_of})
        return len(slots) - 1

    n = num_teams
    if n == 2:
        add("GRAND FINAL", ("seed", 1), ("seed", 2), gf_best_of)
    elif not double and n == 4:
        s1 = add("SEMIFINAL 1", ("seed", 1), ("seed", 4))
        s2 = add("SEMIFINAL 2", ("seed", 2), ("seed", 3))
        add("GRAND FINAL", ("win", s1), ("win", s2), gf_best_of)
    elif not double and n == 8:
        q1 = add("QUARTERFINAL 1", ("seed", 1), ("seed", 8))
        q2 = add("QUARTERFINAL 2", ("seed", 4), ("seed", 5))
        q3 = add("QUARTERFINAL 3", ("seed", 2), ("seed", 7))
        q4 = add("QUARTERFINAL 4", ("seed", 3), ("seed", 6))
        sf1 = add("SEMIFINAL 1", ("win", q1), ("win", q2))
        sf2 = add("SEMIFINAL 2", ("win", q3), ("win", q4))
        add("GRAND FINAL", ("win", sf1), ("win", sf2), gf_best_of)
    elif double and n == 4:
        e1 = add("SEMIFINAL 1", ("seed", 1), ("seed", 4))
        e2 = add("SEMIFINAL 2", ("seed", 2), ("seed", 3))
        wf = add("WINNERS FINAL", ("win", e1), ("win", e2))
        em = add("ELIMINATION MATCH", ("lose", e1), ("lose", e2))
        lf = add("LOSERS FINAL", ("lose", wf), ("win", em))
        add("GRAND FINAL", ("win", wf), ("win", lf), gf_best_of)
    elif double and n == 8:
        w1 = add("WB QUARTER 1", ("seed", 1), ("seed", 8))
        w2 = add("WB QUARTER 2", ("seed", 4), ("seed", 5))
        w3 = add("WB QUARTER 3", ("seed", 2), ("seed", 7))
        w4 = add("WB QUARTER 4", ("seed", 3), ("seed", 6))
        l1 = add("LB ROUND 1-1", ("lose", w1), ("lose", w2))
        l2 = add("LB ROUND 1-2", ("lose", w3), ("lose", w4))
        w5 = add("WB SEMIFINAL 1", ("win", w1), ("win", w2))
        w6 = add("WB SEMIFINAL 2", ("win", w3), ("win", w4))
        l3 = add("LB ROUND 2-1", ("win", l1), ("lose", w6))
        l4 = add("LB ROUND 2-2", ("win", l2), ("lose", w5))
        l5 = add("LB SEMIFINAL", ("win", l3), ("win", l4))
        w7 = add("WINNERS FINAL", ("win", w5), ("win", w6))
        l6 = add("LOSERS FINAL", ("win", l5), ("lose", w7))
        add("GRAND FINAL", ("win", w7), ("win", l6), gf_best_of)
    else:
        # Fallback: single-game final between the top two seeds.
        add("GRAND FINAL", ("seed", 1), ("seed", 2), gf_best_of)
    return slots


def _feeder_placeholder(feeder, slots):
    kind, ref = feeder
    if kind == "seed":
        return "Seed {0}".format(ref)
    verb = "Winner" if kind == "win" else "Loser"
    return "{0} {1}".format(verb, slots[ref]["label"])


def elim_slot_count(db):
    cfg = get_config(db)
    return len(build_bracket_def(
        cfg["elim_num_teams"], cfg["elim_double"], cfg["elim_gf_best_of"]))


def resolve_bracket(db):
    """Resolve the whole bracket from seeds + per-game results. Returns None
    until every seed is filled. Each match reports its resolved competitors (or
    a placeholder), its games, series score, and winner."""
    cfg = get_config(db)
    n = cfg["elim_num_teams"]
    slots = build_bracket_def(n, cfg["elim_double"], cfg["elim_gf_best_of"])
    seeds = {r["seed"]: r["team"] for r in db.execute("SELECT * FROM elim_seeds")}
    if len([s for s in seeds if 1 <= s <= n]) < n:
        return None

    games = {}
    for r in db.execute("SELECT * FROM elim_games ORDER BY match_index, game_number"):
        games.setdefault(r["match_index"], []).append(dict(r))

    teams = [(None, None)] * len(slots)
    winner = [None] * len(slots)
    loser = [None] * len(slots)

    def feeder_name(f):
        kind, ref = f
        if kind == "seed":
            return seeds.get(ref)
        return winner[ref] if kind == "win" else loser[ref]

    out = []
    for i, sd in enumerate(slots):
        a, b = feeder_name(sd["a"]), feeder_name(sd["b"])
        teams[i] = (a, b)
        gl = games.get(i, [])
        wa = sum(1 for gm in gl if gm["yellow_score"] > gm["green_score"])
        wb = sum(1 for gm in gl if gm["green_score"] > gm["yellow_score"])
        need = sd["best_of"] // 2 + 1
        w = None
        if a is not None and b is not None:
            if wa >= need and wa > wb:
                w = a
            elif wb >= need and wb > wa:
                w = b
        winner[i] = w
        loser[i] = (b if w == a else a) if (w is not None) else None
        out.append({
            "n": i + 1,
            "label": sd["label"],
            "best_of": sd["best_of"],
            "yellow": a or _feeder_placeholder(sd["a"], slots),
            "green": b or _feeder_placeholder(sd["b"], slots),
            "decided": a is not None and b is not None,
            "wins": {"yellow": wa, "green": wb},
            "need": need,
            "winner": w,
            "games": [
                {"game_number": gm["game_number"],
                 **{f: gm[f] for f in RESULT_FIELDS}}
                for gm in gl
            ],
            "series_done": w is not None,
        })
    return {
        "seeds": seeds,
        "matches": out,
        "champion": winner[-1] if out else None,
        "num_teams": n,
        "double": cfg["elim_double"],
        "gf_best_of": cfg["elim_gf_best_of"],
    }


# ===================================================================
#  Live match state
# ===================================================================

def read_state():
    """Return (row, remaining, running), stopping the timer once it hits zero."""
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


def match_label(db, row):
    if row["phase"] == "elims":
        slots = build_bracket_def(
            *(get_config(db)[k] for k in
              ("elim_num_teams", "elim_double", "elim_gf_best_of")))
        idx = min(max(row["elim_index"], 0), len(slots) - 1)
        label = slots[idx]["label"] if slots else "ELIMINATION"
        if slots and slots[idx]["best_of"] > 1:
            label += " · GAME {0}".format(row["elim_game"])
        return label
    return "MATCH {0}".format(row["match_number"])


def state_json():
    row, remaining, running = read_state()
    db = get_db()
    cfg = get_config(db)
    return jsonify({
        "match_number": row["match_number"],
        "match_label": match_label(db, row),
        "phase": row["phase"],
        "yellow_team": row["yellow_team"],
        "green_team": row["green_team"],
        "yellow_score": row["yellow_score"],
        "green_score": row["green_score"],
        "yellow_fouls": row["yellow_fouls"],
        "yellow_majors": row["yellow_majors"],
        "green_fouls": row["green_fouls"],
        "green_majors": row["green_majors"],
        "auto_duration": row["auto_duration"],
        "driver_duration": row["driver_duration"],
        "endgame_duration": row["endgame_duration"],
        "timer_duration": row["timer_duration"],
        "timer_remaining": remaining,
        "timer_running": running,
        "ally_a_name": cfg["ally_a_name"],
        "ally_a_color": cfg["ally_a_color"],
        "ally_b_name": cfg["ally_b_name"],
        "ally_b_color": cfg["ally_b_color"],
    })


def load_match(db, phase, idx, teams, scores=None, game=1):
    """Point the live scoreboard at a match: set teams, scores (or zeros),
    reset the timer."""
    s = scores or {f: 0 for f in RESULT_FIELDS}
    col = "elim_index" if phase == "elims" else "schedule_index"
    db.execute(
        "UPDATE match_state SET phase = ?, {0} = ?, elim_game = ?, match_number = ?,"
        " yellow_team = ?, green_team = ?,"
        " yellow_score = ?, green_score = ?, yellow_fouls = ?, yellow_majors = ?,"
        " green_fouls = ?, green_majors = ?,"
        " timer_running = 0, timer_remaining = timer_duration WHERE id = 1".format(col),
        (
            phase, idx, game, idx + 1, teams[0], teams[1],
            s["yellow_score"], s["green_score"], s["yellow_fouls"],
            s["yellow_majors"], s["green_fouls"], s["green_majors"],
        ),
    )


# ===================================================================
#  Pages
# ===================================================================

@app.route("/")
def referee():
    return render_template("referee.html")


@app.route("/panel/<team>")
def panel(team):
    legacy = {"red": "yellow", "blue": "green"}
    if team in legacy:
        return redirect(url_for("panel", team=legacy[team]))
    if team not in TEAMS:
        return redirect(url_for("referee"))
    return render_template("panel.html", team=team)


@app.route("/display")
def display():
    return render_template("display.html")


@app.route("/schedule")
def schedule():
    return render_template("schedule.html")


@app.route("/admin")
def admin():
    return render_template("admin.html")


# ===================================================================
#  State / schedule APIs
# ===================================================================

@app.route("/api/state")
def api_state():
    return state_json()


@app.route("/api/schedule")
def api_schedule():
    db = get_db()
    row = db.execute(
        "SELECT schedule_index, phase, elim_index, elim_game FROM match_state WHERE id = 1"
    ).fetchone()
    cfg = get_config(db)
    quali = get_quali_matches(db)
    results = {r["match_index"]: dict(r) for r in db.execute("SELECT * FROM results")}
    matches = []
    for m in quali:
        r = results.get(m["match_index"])
        matches.append({
            "n": m["match_index"] + 1,
            "rotation": m["rotation"],
            "yellow": m["yellow_team"],
            "green": m["green_team"],
            "result": {"yellow": r["yellow_score"], "green": r["green_score"]} if r else None,
            "full_result": {f: r[f] for f in RESULT_FIELDS} if r else None,
        })
    bracket = resolve_bracket(db) if row["phase"] == "elims" else None
    return jsonify({
        "phase": row["phase"],
        "teams": get_teams(db),
        "current": row["schedule_index"],
        "matches": matches,
        "byes": compute_byes(db, quali),
        "rankings": compute_rankings(db, quali),
        "config": cfg,
        "elims": dict(bracket, current=row["elim_index"], current_game=row["elim_game"])
        if bracket else None,
    })


# ===================================================================
#  Match navigation (Prev / Next)
# ===================================================================

@app.route("/api/next_match", methods=["POST"])
def api_next_match():
    data = request.get_json(silent=True) or {}
    direction = data.get("dir")
    if direction not in (1, -1):
        return jsonify({"error": "bad request"}), 400
    db = get_db()
    row = db.execute("SELECT * FROM match_state WHERE id = 1").fetchone()

    if row["phase"] == "elims":
        return elim_step(db, row, direction)

    count = quali_count(db)
    if count == 0:
        return jsonify({"error": "no qualification matches scheduled"}), 400
    idx = min(max(row["schedule_index"], 0), count - 1)
    if direction == 1:
        db.execute(
            "INSERT OR REPLACE INTO results (match_index, {0})"
            " VALUES (?, ?, ?, ?, ?, ?, ?)".format(", ".join(RESULT_FIELDS)),
            (idx,) + tuple(row[f] for f in RESULT_FIELDS),
        )
        new_idx = min(idx + 1, count - 1)
    else:
        new_idx = max(idx - 1, 0)
    saved = db.execute(
        "SELECT * FROM results WHERE match_index = ?", (new_idx,)
    ).fetchone()
    scores = {f: saved[f] for f in RESULT_FIELDS} if (direction == -1 and saved) else None
    m = db.execute(
        "SELECT * FROM quali_matches WHERE match_index = ?", (new_idx,)
    ).fetchone()
    load_match(db, "quali", new_idx, (m["yellow_team"], m["green_team"]), scores)
    db.commit()
    return state_json()


def elim_step(db, row, direction):
    """Advance the bracket. Next saves the live scores as the current game of
    the current match; if that match's series is now decided we move to the
    next match, otherwise we set up the next game of the same match."""
    bracket = resolve_bracket(db)
    if bracket is None:
        return jsonify({"error": "set the elimination seeds first"}), 400
    slots = bracket["matches"]
    idx = min(max(row["elim_index"], 0), len(slots) - 1)
    game = row["elim_game"]

    if direction == 1:
        if row["yellow_score"] == row["green_score"]:
            return jsonify({"error": "elimination games can't end in a tie —"
                            " someone has to win the game before you advance"}), 400
        db.execute(
            "INSERT OR REPLACE INTO elim_games (match_index, game_number, {0})"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)".format(", ".join(RESULT_FIELDS)),
            (idx, game) + tuple(row[f] for f in RESULT_FIELDS),
        )
        bracket = resolve_bracket(db)
        cur = bracket["matches"][idx]
        if not cur["series_done"]:
            # Same match, next game.
            load_match(db, "elims", idx, (cur["yellow"], cur["green"]), None, game + 1)
            db.commit()
            return state_json()
        new_idx = min(idx + 1, len(slots) - 1)
        if new_idx == idx:
            # That was the grand final — leave it on the board.
            db.commit()
            return state_json()
        nxt = bracket["matches"][new_idx]
        if not nxt["decided"]:
            return jsonify({"error": "the next bracket match isn't decided yet"}), 400
        load_match(db, "elims", new_idx, (nxt["yellow"], nxt["green"]), None, 1)
        db.commit()
        return state_json()

    # direction == -1: step back to the previous match, last game played.
    new_idx = max(idx - 1, 0)
    prev = bracket["matches"][new_idx]
    if not prev["decided"]:
        return jsonify({"error": "that bracket match isn't decided yet"}), 400
    last_game = prev["games"][-1] if prev["games"] else None
    scores = {f: last_game[f] for f in RESULT_FIELDS} if last_game else None
    g = last_game["game_number"] if last_game else 1
    load_match(db, "elims", new_idx, (prev["yellow"], prev["green"]), scores, g)
    db.commit()
    return state_json()


# ===================================================================
#  Quali match editor
# ===================================================================

@app.route("/api/quali_match", methods=["POST"])
def api_quali_match():
    data = request.get_json(silent=True) or {}
    db = get_db()
    count = quali_count(db)
    try:
        idx = int(data.get("index"))
    except (TypeError, ValueError):
        return jsonify({"error": "bad match index"}), 400
    if not 0 <= idx < count:
        return jsonify({"error": "bad match index"}), 400
    roster = set(get_teams(db))
    row = db.execute("SELECT * FROM match_state WHERE id = 1").fetchone()

    if "yellow" in data or "green" in data:
        m = db.execute(
            "SELECT * FROM quali_matches WHERE match_index = ?", (idx,)
        ).fetchone()
        yellow = data.get("yellow", m["yellow_team"])
        green = data.get("green", m["green_team"])
        if yellow not in roster or green not in roster or yellow == green:
            return jsonify({"error": "pick two different teams"}), 400
        db.execute(
            "UPDATE quali_matches SET yellow_team = ?, green_team = ?"
            " WHERE match_index = ?",
            (yellow, green, idx),
        )
        if row["phase"] == "quali" and row["schedule_index"] == idx:
            db.execute(
                "UPDATE match_state SET yellow_team = ?, green_team = ? WHERE id = 1",
                (yellow, green),
            )

    if "result" in data:
        if data["result"] is None:
            db.execute("DELETE FROM results WHERE match_index = ?", (idx,))
        else:
            existing = db.execute(
                "SELECT * FROM results WHERE match_index = ?", (idx,)
            ).fetchone()
            vals = {}
            for f in RESULT_FIELDS:
                supplied = data["result"].get(f)
                if supplied is None:
                    vals[f] = existing[f] if existing else 0
                else:
                    try:
                        vals[f] = min(max(int(supplied), 0), 999)
                    except (TypeError, ValueError):
                        return jsonify({"error": "scores must be whole numbers"}), 400
            db.execute(
                "INSERT OR REPLACE INTO results (match_index, {0})"
                " VALUES (?, ?, ?, ?, ?, ?, ?)".format(", ".join(RESULT_FIELDS)),
                (idx,) + tuple(vals[f] for f in RESULT_FIELDS),
            )

    if data.get("make_current"):
        if row["phase"] != "quali":
            return jsonify({"error": "leave eliminations first"}), 400
        m = db.execute(
            "SELECT * FROM quali_matches WHERE match_index = ?", (idx,)
        ).fetchone()
        saved = db.execute(
            "SELECT * FROM results WHERE match_index = ?", (idx,)
        ).fetchone()
        scores = {f: saved[f] for f in RESULT_FIELDS} if saved else None
        load_match(db, "quali", idx, (m["yellow_team"], m["green_team"]), scores)

    db.commit()
    return state_json()


# ===================================================================
#  Elim editor
# ===================================================================

@app.route("/api/elim_match", methods=["POST"])
def api_elim_match():
    """Edit one elimination match: set/clear a game's score, add a rematch
    game, or put a specific game on the scoreboard."""
    data = request.get_json(silent=True) or {}
    db = get_db()
    row = db.execute("SELECT * FROM match_state WHERE id = 1").fetchone()
    if row["phase"] != "elims":
        return jsonify({"error": "start eliminations first"}), 400
    bracket = resolve_bracket(db)
    if bracket is None:
        return jsonify({"error": "set the elimination seeds first"}), 400
    slots = bracket["matches"]
    try:
        idx = int(data.get("index"))
    except (TypeError, ValueError):
        return jsonify({"error": "bad match index"}), 400
    if not 0 <= idx < len(slots):
        return jsonify({"error": "bad match index"}), 400

    if data.get("add_game"):
        games = db.execute(
            "SELECT COALESCE(MAX(game_number), 0) AS mx FROM elim_games"
            " WHERE match_index = ?", (idx,)
        ).fetchone()["mx"]
        db.execute(
            "INSERT INTO elim_games (match_index, game_number) VALUES (?, ?)",
            (idx, games + 1),
        )
        db.commit()
        return state_json()

    if "game" in data and "result" in data:
        try:
            game = int(data["game"])
        except (TypeError, ValueError):
            return jsonify({"error": "bad game number"}), 400
        if data["result"] is None:
            db.execute(
                "DELETE FROM elim_games WHERE match_index = ? AND game_number = ?",
                (idx, game),
            )
        else:
            existing = db.execute(
                "SELECT * FROM elim_games WHERE match_index = ? AND game_number = ?",
                (idx, game),
            ).fetchone()
            vals = {}
            for f in RESULT_FIELDS:
                supplied = data["result"].get(f)
                if supplied is None:
                    vals[f] = existing[f] if existing else 0
                else:
                    try:
                        vals[f] = min(max(int(supplied), 0), 999)
                    except (TypeError, ValueError):
                        return jsonify({"error": "scores must be whole numbers"}), 400
            db.execute(
                "INSERT OR REPLACE INTO elim_games (match_index, game_number, {0})"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)".format(", ".join(RESULT_FIELDS)),
                (idx, game) + tuple(vals[f] for f in RESULT_FIELDS),
            )

    if data.get("make_current"):
        cur = slots[idx]
        if not cur["decided"]:
            return jsonify({"error": "that bracket match isn't decided yet"}), 400
        game = int(data.get("game", 1) or 1)
        saved = db.execute(
            "SELECT * FROM elim_games WHERE match_index = ? AND game_number = ?",
            (idx, game),
        ).fetchone()
        scores = {f: saved[f] for f in RESULT_FIELDS} if saved else None
        load_match(db, "elims", idx, (cur["yellow"], cur["green"]), scores, game)

    db.commit()
    return state_json()


@app.route("/api/elims", methods=["POST"])
def api_elims():
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    db = get_db()

    if action == "start":
        cfg = get_config(db)
        n = cfg["elim_num_teams"]
        seeds = data.get("seeds")
        roster = set(get_teams(db))
        if (not isinstance(seeds, list) or len(seeds) != n
                or len(set(seeds)) != n or any(t not in roster for t in seeds)):
            return jsonify({"error": "pick {0} different teams".format(n)}), 400
        db.execute("DELETE FROM elim_seeds")
        for i, team in enumerate(seeds, start=1):
            db.execute("INSERT INTO elim_seeds (seed, team) VALUES (?, ?)", (i, team))
        db.execute("DELETE FROM elim_games")
        bracket = resolve_bracket(db)
        first = bracket["matches"][0]
        load_match(db, "elims", 0, (first["yellow"], first["green"]), None, 1)
        db.commit()
        return state_json()

    if action == "back_to_quali":
        row = db.execute("SELECT * FROM match_state WHERE id = 1").fetchone()
        count = quali_count(db)
        if count == 0:
            return jsonify({"error": "no qualification matches scheduled"}), 400
        idx = min(max(row["schedule_index"], 0), count - 1)
        m = db.execute(
            "SELECT * FROM quali_matches WHERE match_index = ?", (idx,)
        ).fetchone()
        saved = db.execute(
            "SELECT * FROM results WHERE match_index = ?", (idx,)
        ).fetchone()
        scores = {f: saved[f] for f in RESULT_FIELDS} if saved else None
        load_match(db, "quali", idx, (m["yellow_team"], m["green_team"]), scores)
        db.commit()
        return state_json()

    return jsonify({"error": "bad request"}), 400


# ===================================================================
#  Live scoring
# ===================================================================

@app.route("/api/adjust", methods=["POST"])
def api_adjust():
    data = request.get_json(silent=True) or {}
    team = data.get("team")
    if team not in TEAMS:
        return jsonify({"error": "bad request"}), 400
    col = team + "_score"
    db = get_db()
    if data.get("clear"):
        db.execute("UPDATE match_state SET {0} = 0 WHERE id = 1".format(col))
    else:
        delta = data.get("delta")
        if delta not in (-1, 1, 2, 3):
            return jsonify({"error": "bad request"}), 400
        db.execute(
            "UPDATE match_state SET {0} = MAX(0, {0} + ?) WHERE id = 1".format(col),
            (delta,),
        )
    db.commit()
    return state_json()


@app.route("/api/foul", methods=["POST"])
def api_foul():
    data = request.get_json(silent=True) or {}
    team = data.get("team")
    kind = data.get("kind")
    if team not in TEAMS or kind not in FOUL_POINTS:
        return jsonify({"error": "bad request"}), 400
    offender = "green" if team == "yellow" else "yellow"
    points = FOUL_POINTS[kind]
    count_col = offender + ("_fouls" if kind == "foul" else "_majors")
    score_col = team + "_score"
    db = get_db()
    if data.get("undo"):
        row = db.execute("SELECT * FROM match_state WHERE id = 1").fetchone()
        if row[count_col] > 0:
            db.execute(
                "UPDATE match_state SET {0} = {0} - 1, {1} = MAX(0, {1} - ?)"
                " WHERE id = 1".format(count_col, score_col),
                (points,),
            )
    else:
        db.execute(
            "UPDATE match_state SET {0} = {0} + 1, {1} = {1} + ? WHERE id = 1".format(
                count_col, score_col
            ),
            (points,),
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


# ===================================================================
#  Setup / configuration
# ===================================================================

@app.route("/api/setup", methods=["POST"])
def api_setup():
    data = request.get_json(silent=True) or {}
    db = get_db()
    row = db.execute("SELECT * FROM match_state WHERE id = 1").fetchone()

    def clamped_int(value, lo, hi):
        return min(max(int(value), lo), hi)

    try:
        match_number = clamped_int(data.get("match_number", row["match_number"]), 1, 9999)
        auto = clamped_int(data.get("auto_duration", row["auto_duration"]), 0, 600)
        driver = clamped_int(data.get("driver_duration", row["driver_duration"]), 5, 3600)
        endgame = clamped_int(data.get("endgame_duration", row["endgame_duration"]), 0, 600)
    except (TypeError, ValueError):
        return jsonify({"error": "match number and phase times must be whole numbers"}), 400

    yellow_team = (str(data.get("yellow_team", row["yellow_team"])).strip() or "---")[:20]
    green_team = (str(data.get("green_team", row["green_team"])).strip() or "---")[:20]
    duration = auto + driver + endgame

    db.execute(
        "UPDATE match_state SET match_number = ?, yellow_team = ?, green_team = ?,"
        " auto_duration = ?, driver_duration = ?, endgame_duration = ?,"
        " timer_duration = ? WHERE id = 1",
        (match_number, yellow_team, green_team, auto, driver, endgame, duration),
    )
    if duration != row["timer_duration"]:
        db.execute(
            "UPDATE match_state SET timer_running = 0, timer_remaining = ? WHERE id = 1",
            (duration,),
        )
    if data.get("reset_scores"):
        db.execute(
            "UPDATE match_state SET yellow_score = 0, green_score = 0,"
            " yellow_fouls = 0, yellow_majors = 0, green_fouls = 0, green_majors = 0"
            " WHERE id = 1"
        )
    if data.get("regenerate_quali"):
        rebuild_quali(db)
        _reload_first_quali(db)
    if data.get("reset_schedule"):
        db.execute("DELETE FROM results")
        db.execute("DELETE FROM elim_games")
        db.execute("DELETE FROM elim_seeds")
        _reload_first_quali(db)
    db.commit()
    return state_json()


@app.route("/api/quali_schedule", methods=["POST"])
def api_quali_schedule():
    """Add a blank qualification match at the end, or delete one (re-indexing
    the matches and their saved results so indices stay contiguous)."""
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    db = get_db()
    roster = get_teams(db)
    if action == "add":
        if len(roster) < 2:
            return jsonify({"error": "add at least two teams first"}), 400
        count = quali_count(db)
        last_rot = db.execute(
            "SELECT COALESCE(MAX(rotation), 0) AS r FROM quali_matches"
        ).fetchone()["r"]
        db.execute(
            "INSERT INTO quali_matches (match_index, rotation, yellow_team, green_team)"
            " VALUES (?, ?, ?, ?)",
            (count, last_rot + 1, roster[0], roster[1]),
        )
        db.commit()
        return jsonify({"ok": True})

    if action == "delete":
        try:
            idx = int(data.get("index"))
        except (TypeError, ValueError):
            return jsonify({"error": "bad match index"}), 400
        count = quali_count(db)
        if not 0 <= idx < count:
            return jsonify({"error": "bad match index"}), 400
        rows = get_quali_matches(db)
        results = {r["match_index"]: dict(r) for r in db.execute("SELECT * FROM results")}
        db.execute("DELETE FROM quali_matches")
        db.execute("DELETE FROM results")
        new_i = 0
        for m in rows:
            if m["match_index"] == idx:
                continue
            db.execute(
                "INSERT INTO quali_matches (match_index, rotation, yellow_team, green_team)"
                " VALUES (?, ?, ?, ?)",
                (new_i, m["rotation"], m["yellow_team"], m["green_team"]),
            )
            r = results.get(m["match_index"])
            if r:
                db.execute(
                    "INSERT INTO results (match_index, {0}) VALUES (?, ?, ?, ?, ?, ?, ?)".format(
                        ", ".join(RESULT_FIELDS)),
                    (new_i,) + tuple(r[f] for f in RESULT_FIELDS),
                )
            new_i += 1
        # Keep the live scoreboard pointing at a valid quali match.
        row = db.execute("SELECT phase, schedule_index FROM match_state WHERE id = 1").fetchone()
        if row["phase"] == "quali":
            si = min(row["schedule_index"], max(new_i - 1, 0))
            m = db.execute(
                "SELECT * FROM quali_matches WHERE match_index = ?", (si,)
            ).fetchone()
            if m:
                saved = db.execute(
                    "SELECT * FROM results WHERE match_index = ?", (si,)
                ).fetchone()
                scores = {f: saved[f] for f in RESULT_FIELDS} if saved else None
                load_match(db, "quali", si, (m["yellow_team"], m["green_team"]), scores)
        db.commit()
        return jsonify({"ok": True})

    return jsonify({"error": "bad request"}), 400


def _reload_first_quali(db):
    m = db.execute(
        "SELECT * FROM quali_matches ORDER BY match_index LIMIT 1"
    ).fetchone()
    if m:
        load_match(db, "quali", m["match_index"], (m["yellow_team"], m["green_team"]))
    else:
        db.execute("UPDATE match_state SET phase = 'quali' WHERE id = 1")


@app.route("/api/config", methods=["POST"])
def api_config():
    """Update alliance names/colors and/or the elimination format."""
    data = request.get_json(silent=True) or {}
    db = get_db()
    cfg = get_config(db)

    def clean_color(v, fallback):
        v = str(v).strip()
        if len(v) == 7 and v.startswith("#"):
            try:
                int(v[1:], 16)
                return v
            except ValueError:
                pass
        return fallback

    ally_a_name = (str(data.get("ally_a_name", cfg["ally_a_name"])).strip() or "A")[:14]
    ally_b_name = (str(data.get("ally_b_name", cfg["ally_b_name"])).strip() or "B")[:14]
    ally_a_color = clean_color(data.get("ally_a_color", cfg["ally_a_color"]), cfg["ally_a_color"])
    ally_b_color = clean_color(data.get("ally_b_color", cfg["ally_b_color"]), cfg["ally_b_color"])

    num_teams = data.get("elim_num_teams", cfg["elim_num_teams"])
    try:
        num_teams = int(num_teams)
    except (TypeError, ValueError):
        num_teams = cfg["elim_num_teams"]
    if num_teams not in (2, 4, 8):
        num_teams = cfg["elim_num_teams"]
    double = 1 if data.get("elim_double", cfg["elim_double"]) in (1, True, "1", "true") else 0
    if num_teams == 2:
        double = 0  # a 2-team bracket is just a final
    gf = data.get("elim_gf_best_of", cfg["elim_gf_best_of"])
    gf = 3 if str(gf) == "3" or gf == 3 else 1

    # Changing the elim format while a bracket is live would desync it.
    row = db.execute("SELECT phase FROM match_state WHERE id = 1").fetchone()
    format_changed = (num_teams != cfg["elim_num_teams"] or double != cfg["elim_double"]
                      or gf != cfg["elim_gf_best_of"])
    if format_changed and row["phase"] == "elims":
        return jsonify({"error": "leave eliminations before changing the elim format"}), 400

    db.execute(
        "UPDATE config SET ally_a_name = ?, ally_a_color = ?, ally_b_name = ?,"
        " ally_b_color = ?, elim_num_teams = ?, elim_double = ?, elim_gf_best_of = ?"
        " WHERE id = 1",
        (ally_a_name, ally_a_color, ally_b_name, ally_b_color, num_teams, double, gf),
    )
    db.commit()
    return state_json()


@app.route("/api/teams", methods=["POST"])
def api_teams():
    """Replace the whole roster with the supplied ordered list of names."""
    data = request.get_json(silent=True) or {}
    names = data.get("teams")
    if not isinstance(names, list):
        return jsonify({"error": "bad request"}), 400
    cleaned = []
    seen = set()
    for raw in names:
        name = str(raw).strip()[:20]
        if not name:
            continue
        key = name.lower()
        if key in seen:
            return jsonify({"error": "team names must be unique: " + name}), 400
        seen.add(key)
        cleaned.append(name)
    if len(cleaned) < 2:
        return jsonify({"error": "you need at least two teams"}), 400
    db = get_db()
    db.execute("DELETE FROM teams")
    for i, name in enumerate(cleaned):
        db.execute("INSERT INTO teams (name, position) VALUES (?, ?)", (name, i))
    rebuild_quali(db)
    db.execute("DELETE FROM elim_games")
    db.execute("DELETE FROM elim_seeds")
    _reload_first_quali(db)
    db.commit()
    return jsonify({"ok": True, "teams": get_teams(db)})


# ===================================================================
#  Test-data generator
# ===================================================================

@app.route("/api/test_data", methods=["POST"])
def api_test_data():
    """Fill in random results for testing. quali=True fills every quali match;
    elims=True seeds a bracket from the standings and plays it out randomly."""
    data = request.get_json(silent=True) or {}
    db = get_db()

    def rand_result():
        y = random.randint(0, 120)
        g = random.randint(0, 120)
        if y == g:
            y += random.choice((1, 2, 3))
        return {
            "yellow_score": y, "green_score": g,
            "yellow_fouls": random.randint(0, 3), "yellow_majors": random.randint(0, 1),
            "green_fouls": random.randint(0, 3), "green_majors": random.randint(0, 1),
        }

    if data.get("quali", True):
        db.execute("DELETE FROM results")
        for m in db.execute("SELECT match_index FROM quali_matches"):
            r = rand_result()
            db.execute(
                "INSERT OR REPLACE INTO results (match_index, {0})"
                " VALUES (?, ?, ?, ?, ?, ?, ?)".format(", ".join(RESULT_FIELDS)),
                (m["match_index"],) + tuple(r[f] for f in RESULT_FIELDS),
            )

    if data.get("elims"):
        quali = get_quali_matches(db)
        rankings = compute_rankings(db, quali)
        cfg = get_config(db)
        n = cfg["elim_num_teams"]
        if len(rankings) < n:
            return jsonify({"error": "not enough teams for a {0}-team bracket".format(n)}), 400
        seeds = [t["team"] for t in rankings[:n]]
        db.execute("DELETE FROM elim_seeds")
        for i, team in enumerate(seeds, start=1):
            db.execute("INSERT INTO elim_seeds (seed, team) VALUES (?, ?)", (i, team))
        db.execute("DELETE FROM elim_games")
        db.commit()
        # Play the bracket out match by match, respecting each series length.
        while True:
            bracket = resolve_bracket(db)
            progressed = False
            for slot in bracket["matches"]:
                if not slot["decided"] or slot["series_done"]:
                    continue
                idx = slot["n"] - 1
                game_no = len(slot["games"]) + 1
                r = rand_result()
                db.execute(
                    "INSERT OR REPLACE INTO elim_games (match_index, game_number, {0})"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)".format(", ".join(RESULT_FIELDS)),
                    (idx, game_no) + tuple(r[f] for f in RESULT_FIELDS),
                )
                progressed = True
                break
            if not progressed:
                break
            db.commit()
        # Put the (decided) grand final on the board.
        bracket = resolve_bracket(db)
        last = bracket["matches"][-1]
        db.execute("UPDATE match_state SET phase = 'elims' WHERE id = 1")
        g = last["games"][-1]["game_number"] if last["games"] else 1
        load_match(db, "elims", len(bracket["matches"]) - 1,
                   (last["yellow"], last["green"]), None, g)

    db.commit()
    return jsonify({"ok": True})


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
