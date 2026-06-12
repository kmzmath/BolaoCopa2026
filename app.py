import base64
import csv
import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import sys
import traceback
import urllib.parse
from datetime import datetime, timedelta
from http import cookies
from pathlib import Path
from wsgiref.simple_server import make_server
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
STATIC_DIR = ROOT / "static"
CSV_PATH = DATA_DIR / "copa_do_mundo_2026_jogos_horario_brasilia.csv"
BR_TZ = ZoneInfo("America/Sao_Paulo")
SESSION_COOKIE = "bolao_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")


def now_brasilia():
    return datetime.now(BR_TZ)


def iso_now():
    return now_brasilia().isoformat(timespec="seconds")


def parse_dt(value):
    return datetime.fromisoformat(value)


def b64url_encode(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64url_decode(value):
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def normalize_username(username):
    return re.sub(r"\s+", " ", username.strip()).casefold()


def boolish(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    return str(value).lower() in {"1", "true", "t", "yes"}


class Database:
    def __init__(self):
        self.url = os.getenv("DATABASE_URL", "").strip()
        if os.getenv("RENDER") == "true" and not self.url:
            raise RuntimeError(
                "DATABASE_URL precisa estar configurado no Render. "
                "O filesystem do plano free é efêmero, então SQLite não deve ser usado em produção."
            )
        self.is_postgres = self.url.startswith(("postgres://", "postgresql://"))
        self.sqlite_path = Path(os.getenv("SQLITE_PATH", DATA_DIR / "bolao.sqlite3"))

    def connect(self):
        if self.is_postgres:
            import psycopg
            from psycopg.rows import dict_row

            return psycopg.connect(self.url, row_factory=dict_row)

        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def q(self, sql):
        if self.is_postgres:
            return sql.replace("?", "%s")
        return sql

    def execute(self, conn, sql, params=()):
        return conn.execute(self.q(sql), params)

    @staticmethod
    def rows(cursor):
        return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def one(cursor):
        row = cursor.fetchone()
        return dict(row) if row else None


DB = Database()


def init_db():
    with DB.connect() as conn:
        if DB.is_postgres:
            DB.execute(
                conn,
                """
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    username_norm TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    avatar_mime TEXT,
                    avatar_data TEXT,
                    avatar_updated_at TEXT,
                    created_at TEXT NOT NULL
                )
                """,
            )
            DB.execute(
                conn,
                """
                CREATE TABLE IF NOT EXISTS matches (
                    id SERIAL PRIMARY KEY,
                    fifa_number INTEGER NOT NULL UNIQUE,
                    phase TEXT NOT NULL,
                    phase_slug TEXT NOT NULL,
                    group_code TEXT,
                    round_label TEXT,
                    team_a TEXT NOT NULL,
                    team_b TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'agendado',
                    result_home INTEGER,
                    result_away INTEGER,
                    penalty_winner TEXT,
                    closed_at TEXT,
                    updated_at TEXT NOT NULL
                )
                """,
            )
            DB.execute(
                conn,
                """
                CREATE TABLE IF NOT EXISTS predictions (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
                    home_score INTEGER NOT NULL,
                    away_score INTEGER NOT NULL,
                    advances TEXT,
                    points INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, match_id)
                )
                """,
            )
        else:
            DB.execute(
                conn,
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    username_norm TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    avatar_mime TEXT,
                    avatar_data TEXT,
                    avatar_updated_at TEXT,
                    created_at TEXT NOT NULL
                )
                """,
            )
            DB.execute(
                conn,
                """
                CREATE TABLE IF NOT EXISTS matches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fifa_number INTEGER NOT NULL UNIQUE,
                    phase TEXT NOT NULL,
                    phase_slug TEXT NOT NULL,
                    group_code TEXT,
                    round_label TEXT,
                    team_a TEXT NOT NULL,
                    team_b TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'agendado',
                    result_home INTEGER,
                    result_away INTEGER,
                    penalty_winner TEXT,
                    closed_at TEXT,
                    updated_at TEXT NOT NULL
                )
                """,
            )
            DB.execute(
                conn,
                """
                CREATE TABLE IF NOT EXISTS predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
                    home_score INTEGER NOT NULL,
                    away_score INTEGER NOT NULL,
                    advances TEXT,
                    points INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, match_id)
                )
                """,
            )

        DB.execute(conn, "CREATE INDEX IF NOT EXISTS idx_matches_start ON matches(start_at)")
        DB.execute(conn, "CREATE INDEX IF NOT EXISTS idx_predictions_user ON predictions(user_id)")
        DB.execute(conn, "CREATE INDEX IF NOT EXISTS idx_predictions_match ON predictions(match_id)")
        ensure_user_columns(conn)
        ensure_result_audit_table(conn)
        ensure_early_final_table(conn)
        seed_matches_if_needed(conn)
        ensure_admin_user(conn)
        recalc_all_points(conn)
        recalc_all_early_final_points(conn)
        conn.commit()


def ensure_user_columns(conn):
    if DB.is_postgres:
        DB.execute(conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE")
        DB.execute(conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_mime TEXT")
        DB.execute(conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_data TEXT")
        DB.execute(conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_updated_at TEXT")
        return
    columns = {row["name"] for row in DB.rows(DB.execute(conn, "PRAGMA table_info(users)"))}
    if "active" not in columns:
        DB.execute(conn, "ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
    if "avatar_mime" not in columns:
        DB.execute(conn, "ALTER TABLE users ADD COLUMN avatar_mime TEXT")
    if "avatar_data" not in columns:
        DB.execute(conn, "ALTER TABLE users ADD COLUMN avatar_data TEXT")
    if "avatar_updated_at" not in columns:
        DB.execute(conn, "ALTER TABLE users ADD COLUMN avatar_updated_at TEXT")


def ensure_result_audit_table(conn):
    if DB.is_postgres:
        DB.execute(
            conn,
            """
            CREATE TABLE IF NOT EXISTS result_audits (
                id SERIAL PRIMARY KEY,
                match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
                admin_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                admin_username TEXT NOT NULL,
                action TEXT NOT NULL,
                old_result_home INTEGER,
                old_result_away INTEGER,
                old_penalty_winner TEXT,
                old_status TEXT,
                old_closed_at TEXT,
                new_result_home INTEGER,
                new_result_away INTEGER,
                new_penalty_winner TEXT,
                new_status TEXT,
                new_closed_at TEXT,
                changed_at TEXT NOT NULL
            )
            """,
        )
    else:
        DB.execute(
            conn,
            """
            CREATE TABLE IF NOT EXISTS result_audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
                admin_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                admin_username TEXT NOT NULL,
                action TEXT NOT NULL,
                old_result_home INTEGER,
                old_result_away INTEGER,
                old_penalty_winner TEXT,
                old_status TEXT,
                old_closed_at TEXT,
                new_result_home INTEGER,
                new_result_away INTEGER,
                new_penalty_winner TEXT,
                new_status TEXT,
                new_closed_at TEXT,
                changed_at TEXT NOT NULL
            )
            """,
        )
    DB.execute(conn, "CREATE INDEX IF NOT EXISTS idx_result_audits_match ON result_audits(match_id)")
    DB.execute(conn, "CREATE INDEX IF NOT EXISTS idx_result_audits_changed ON result_audits(changed_at)")


def ensure_early_final_table(conn):
    if DB.is_postgres:
        DB.execute(
            conn,
            """
            CREATE TABLE IF NOT EXISTS early_final_predictions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                champion TEXT NOT NULL,
                runner_up TEXT NOT NULL,
                finalist_a TEXT,
                finalist_b TEXT,
                points INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        )
    else:
        DB.execute(
            conn,
            """
            CREATE TABLE IF NOT EXISTS early_final_predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                champion TEXT NOT NULL,
                runner_up TEXT NOT NULL,
                finalist_a TEXT NOT NULL,
                finalist_b TEXT NOT NULL,
                points INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        )
    ensure_early_final_columns(conn)
    DB.execute(conn, "CREATE INDEX IF NOT EXISTS idx_early_final_user ON early_final_predictions(user_id)")


def table_columns(conn, table):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
        raise ValueError("Nome de tabela inválido.")
    if DB.is_postgres:
        rows = DB.rows(
            DB.execute(
                conn,
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = ?
                """,
                (table,),
            )
        )
        return {row["column_name"] for row in rows}
    return {row["name"] for row in DB.rows(DB.execute(conn, f"PRAGMA table_info({table})"))}


def ensure_early_final_columns(conn):
    columns = table_columns(conn, "early_final_predictions")
    for column in ("champion", "runner_up", "finalist_a", "finalist_b"):
        if column in columns:
            continue
        if DB.is_postgres:
            DB.execute(conn, f"ALTER TABLE early_final_predictions ADD COLUMN IF NOT EXISTS {column} TEXT")
        else:
            DB.execute(conn, f"ALTER TABLE early_final_predictions ADD COLUMN {column} TEXT")
        columns.add(column)

    rows = DB.rows(
        DB.execute(
            conn,
            """
            SELECT id, champion, runner_up, finalist_a, finalist_b
            FROM early_final_predictions
            WHERE champion IS NULL OR champion = ''
               OR runner_up IS NULL OR runner_up = ''
               OR finalist_a IS NULL OR finalist_a = ''
               OR finalist_b IS NULL OR finalist_b = ''
            """,
        )
    )
    for row in rows:
        champion = row.get("champion") or row.get("finalist_a") or row.get("finalist_b") or row.get("runner_up")
        runner_up = row.get("runner_up")
        if not runner_up:
            runner_up = row.get("finalist_a") if row.get("finalist_a") != champion else row.get("finalist_b")
        if not runner_up:
            runner_up = row.get("finalist_b") or row.get("finalist_a") or champion
        finalist_a = row.get("finalist_a") or champion
        finalist_b = row.get("finalist_b") or runner_up
        DB.execute(
            conn,
            """
            UPDATE early_final_predictions
            SET champion = ?, runner_up = ?, finalist_a = ?, finalist_b = ?
            WHERE id = ?
            """,
            (champion, runner_up, finalist_a, finalist_b, row["id"]),
        )


def normalize_phase(raw_phase):
    phase = raw_phase.strip()
    lookup = {
        "Fase de grupos": "grupos",
        "Segunda fase": "segunda_fase",
        "Oitavas de final": "oitavas",
        "Quartas de final": "quartas",
        "Semifinais": "semifinal",
        "Disputa do 3º lugar": "terceiro_lugar",
        "Final": "final",
    }
    return lookup.get(phase, re.sub(r"\W+", "_", phase.lower()).strip("_"))


def seed_matches_if_needed(conn):
    existing = DB.one(DB.execute(conn, "SELECT COUNT(*) AS total FROM matches"))["total"]
    if existing:
        return
    if not CSV_PATH.exists():
        raise RuntimeError(f"CSV de jogos não encontrado em {CSV_PATH}")

    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            start = datetime.strptime(
                f"{row['data_brasilia']} {row['horario_brasilia']}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=BR_TZ)
            phase = row["fase"].strip()
            DB.execute(
                conn,
                """
                INSERT INTO matches (
                    fifa_number, phase, phase_slug, group_code, round_label,
                    team_a, team_b, start_at, status, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'agendado', ?)
                """,
                (
                    int(row["numero_partida_fifa"]),
                    phase,
                    normalize_phase(phase),
                    row["grupo"].strip() or None,
                    row["partida"].strip() or None,
                    row["equipe_ou_referencia_1"].strip(),
                    row["equipe_ou_referencia_2"].strip(),
                    start.isoformat(timespec="minutes"),
                    iso_now(),
                ),
            )


def ensure_admin_user(conn):
    admin_username = os.getenv("ADMIN_USERNAME", "Math").strip() or "Math"
    admin_password = os.getenv("ADMIN_PASSWORD", "admin123")
    norm = normalize_username(admin_username)
    existing = DB.one(DB.execute(conn, "SELECT id, is_admin, active FROM users WHERE username_norm = ?", (norm,)))
    if existing:
        if not boolish(existing["is_admin"]) or not boolish(existing.get("active", True)):
            DB.execute(conn, "UPDATE users SET is_admin = ?, active = ? WHERE id = ?", (True, True, existing["id"]))
        return
    legacy_norm = normalize_username("admin")
    if norm != legacy_norm:
        legacy_admin = DB.one(
            DB.execute(conn, "SELECT id, is_admin, active FROM users WHERE username_norm = ?", (legacy_norm,))
        )
        if legacy_admin and boolish(legacy_admin["is_admin"]):
            DB.execute(
                conn,
                "UPDATE users SET username = ?, username_norm = ?, is_admin = ?, active = ? WHERE id = ?",
                (admin_username, norm, True, True, legacy_admin["id"]),
            )
            return
    DB.execute(
        conn,
        """
        INSERT INTO users (username, username_norm, password_hash, is_admin, active, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (admin_username, norm, hash_password(admin_password), True, True, iso_now()),
    )


def hash_password(password):
    salt = secrets.token_bytes(16)
    iterations = 260_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${b64url_encode(salt)}${b64url_encode(digest)}"


def verify_password(password, stored):
    try:
        scheme, iterations, salt, digest = stored.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), b64url_decode(salt), int(iterations)
        )
        return hmac.compare_digest(b64url_encode(candidate), digest)
    except Exception:
        return False


def make_session(user_id):
    payload = json.dumps(
        {"uid": int(user_id), "exp": int(datetime.now().timestamp()) + SESSION_MAX_AGE},
        separators=(",", ":"),
    ).encode("utf-8")
    payload_b64 = b64url_encode(payload)
    signature = hmac.new(SECRET_KEY.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256)
    return f"{payload_b64}.{b64url_encode(signature.digest())}"


def verify_session(value):
    if not value or "." not in value:
        return None
    payload_b64, signature = value.split(".", 1)
    expected = hmac.new(SECRET_KEY.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256)
    if not hmac.compare_digest(b64url_encode(expected.digest()), signature):
        return None
    try:
        payload = json.loads(b64url_decode(payload_b64).decode("utf-8"))
    except Exception:
        return None
    if payload.get("exp", 0) < int(datetime.now().timestamp()):
        return None
    return payload.get("uid")


class Request:
    def __init__(self, environ):
        self.environ = environ
        self.method = environ.get("REQUEST_METHOD", "GET").upper()
        self.path = urllib.parse.unquote(environ.get("PATH_INFO", "/"))
        self.query = urllib.parse.parse_qs(environ.get("QUERY_STRING", ""))
        self._json = None
        jar = cookies.SimpleCookie(environ.get("HTTP_COOKIE", ""))
        self.cookies = {key: morsel.value for key, morsel in jar.items()}

    def json(self):
        if self._json is not None:
            return self._json
        length = int(self.environ.get("CONTENT_LENGTH") or 0)
        if length <= 0:
            self._json = {}
            return self._json
        raw = self.environ["wsgi.input"].read(length).decode("utf-8")
        self._json = json.loads(raw or "{}")
        return self._json


def status_line(status):
    phrases = {
        200: "OK",
        201: "Created",
        204: "No Content",
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        405: "Method Not Allowed",
        409: "Conflict",
        500: "Internal Server Error",
    }
    return f"{status} {phrases.get(status, 'OK')}"


def send(start_response, body, status=200, content_type="application/json; charset=utf-8", headers=None):
    headers = list(headers or [])
    if isinstance(body, str):
        raw = body.encode("utf-8")
    else:
        raw = body
    header_names = {name.lower() for name, _ in headers}
    if "content-type" not in header_names:
        headers.append(("Content-Type", content_type))
    if "content-length" not in header_names:
        headers.append(("Content-Length", str(len(raw))))
    if "cache-control" not in header_names:
        private_download = any(name.lower() == "content-disposition" for name, _ in headers)
        no_store = content_type.startswith("application/json") or private_download
        headers.append(("Cache-Control", "no-store" if no_store else "public, max-age=3600"))
    start_response(status_line(status), headers)
    return [raw]


def json_response(start_response, data, status=200, headers=None):
    return send(start_response, json.dumps(data, ensure_ascii=False), status=status, headers=headers)


def json_error(start_response, message, status=400, code="erro"):
    return json_response(start_response, {"ok": False, "error": message, "code": code}, status=status)


def set_session_cookie(token):
    return (
        "Set-Cookie",
        f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_MAX_AGE}",
    )


def clear_session_cookie():
    return ("Set-Cookie", f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")


def current_user(req):
    user_id = verify_session(req.cookies.get(SESSION_COOKIE))
    if not user_id:
        return None
    with DB.connect() as conn:
        user = DB.one(
            DB.execute(
                conn,
                "SELECT id, username, username_norm, is_admin, active, avatar_mime, avatar_updated_at, created_at FROM users WHERE id = ?",
                (user_id,),
            )
        )
    if user:
        user["is_admin"] = boolish(user["is_admin"])
        user["active"] = boolish(user.get("active", True))
        if not user["active"]:
            return None
    return user


def require_user(req, start_response):
    user = current_user(req)
    if not user:
        return None, json_error(start_response, "Faça login para continuar.", 401, "nao_autenticado")
    return user, None


def require_admin(req, start_response):
    user, error = require_user(req, start_response)
    if error:
        return None, error
    if not user["is_admin"]:
        return None, json_error(start_response, "Acesso restrito ao administrador.", 403, "sem_permissao")
    return user, None


def public_user(user):
    if not user:
        return None
    return {
        "id": user["id"],
        "username": user["username"],
        "is_admin": boolish(user["is_admin"]),
        "active": boolish(user.get("active", True)),
        "avatar_url": avatar_url(user),
    }


def avatar_url(user):
    mime = user.get("avatar_mime") if user else None
    user_id = user.get("id") if user else None
    if not mime or not user_id:
        return None
    version = user.get("avatar_updated_at") or user.get("created_at") or "1"
    return f"/api/users/{user_id}/avatar?v={urllib.parse.quote(str(version))}"


def parse_avatar_data_url(value):
    if not value:
        raise ValueError("Selecione uma imagem para enviar.")
    match = re.fullmatch(r"data:(image/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=]+)", value)
    if not match:
        raise ValueError("Use uma imagem JPG, PNG ou WebP.")
    mime, encoded = match.groups()
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("A imagem enviada está inválida.") from exc
    if len(raw) > 1_000_000:
        raise ValueError("A foto deve ter no máximo 1 MB.")
    if len(raw) < 32:
        raise ValueError("A imagem enviada está vazia ou inválida.")
    return mime, encoded


def prediction_outcome(home, away):
    if home > away:
        return "A"
    if away > home:
        return "B"
    return "D"


def is_knockout(match):
    return match["phase_slug"] != "grupos"


SCORE_RULES = [
    ("exact", "Placar exato", 6),
    ("result_goal_difference", "Resultado e diferença de gols", 4),
    ("result_team_goals", "Resultado e total de gols de uma equipe", 3),
    ("result", "Resultado (vencedor ou empate)", 2),
    ("inverted", "Placar invertido", -2),
    ("zero", "Placar errado", 0),
]


def score_prediction_detail(prediction, match):
    if prediction is None:
        return {"points": 0, "rule_key": "zero", "rule_label": "0 pontos"}
    if match.get("result_home") is None or match.get("result_away") is None:
        return {"points": 0, "rule_key": "zero", "rule_label": "0 pontos"}

    ph = int(prediction["home_score"])
    pa = int(prediction["away_score"])
    rh = int(match["result_home"])
    ra = int(match["result_away"])
    pred_outcome = prediction_outcome(ph, pa)
    real_outcome = prediction_outcome(rh, ra)

    if ph == rh and pa == ra:
        return {"points": 6, "rule_key": "exact", "rule_label": "Placar exato"}
    if pred_outcome == real_outcome:
        if ph - pa == rh - ra:
            return {"points": 4, "rule_key": "result_goal_difference", "rule_label": "Resultado e diferença de gols"}
        if ph == rh or pa == ra:
            return {"points": 3, "rule_key": "result_team_goals", "rule_label": "Resultado e total de gols de uma equipe"}
        return {"points": 2, "rule_key": "result", "rule_label": "Resultado (vencedor ou empate)"}
    if ph == ra and pa == rh and pred_outcome != "D" and real_outcome != "D":
        return {"points": -2, "rule_key": "inverted", "rule_label": "Placar invertido"}
    return {"points": 0, "rule_key": "zero", "rule_label": "Placar errado"}


def score_prediction(prediction, match):
    return score_prediction_detail(prediction, match)["points"]


def empty_breakdown():
    return {
        "settled_predictions": 0,
        "rules": [
            {"key": key, "label": label, "points": points, "count": 0}
            for key, label, points in SCORE_RULES
        ],
    }


def build_ranking_breakdown(rows):
    stats = {}
    for row in rows:
        user_id = row["user_id"]
        stats.setdefault(user_id, empty_breakdown())
        detail = score_prediction_detail(row, row)
        stats[user_id]["settled_predictions"] += 1
        for rule in stats[user_id]["rules"]:
            if rule["key"] == detail["rule_key"]:
                rule["count"] += 1
                break
    return stats


def actual_winner_side(match):
    if match.get("result_home") is None or match.get("result_away") is None:
        return None
    home = int(match["result_home"])
    away = int(match["result_away"])
    if home > away:
        return "A"
    if away > home:
        return "B"
    return None


def match_closed(match):
    return match.get("status") == "encerrado" and match.get("result_home") is not None and match.get("result_away") is not None


def compute_group_tables(matches):
    groups = {}
    for match in matches:
        if match["phase_slug"] != "grupos" or not match.get("group_code"):
            continue
        group = match["group_code"]
        groups.setdefault(group, {"matches": [], "teams": {}})
        groups[group]["matches"].append(match)
        for team in (match["team_a"], match["team_b"]):
            groups[group]["teams"].setdefault(
                team,
                {"team": team, "played": 0, "wins": 0, "draws": 0, "losses": 0, "gf": 0, "ga": 0, "gd": 0, "points": 0},
            )

    for group_data in groups.values():
        for match in group_data["matches"]:
            if not match_closed(match):
                continue
            a = group_data["teams"][match["team_a"]]
            b = group_data["teams"][match["team_b"]]
            ah = int(match["result_home"])
            ba = int(match["result_away"])
            a["played"] += 1
            b["played"] += 1
            a["gf"] += ah
            a["ga"] += ba
            b["gf"] += ba
            b["ga"] += ah
            if ah > ba:
                a["wins"] += 1
                b["losses"] += 1
                a["points"] += 3
            elif ba > ah:
                b["wins"] += 1
                a["losses"] += 1
                b["points"] += 3
            else:
                a["draws"] += 1
                b["draws"] += 1
                a["points"] += 1
                b["points"] += 1

        for team in group_data["teams"].values():
            team["gd"] = team["gf"] - team["ga"]
        group_data["complete"] = all(match_closed(match) for match in group_data["matches"])
        group_data["standings"] = sorted(
            group_data["teams"].values(),
            key=lambda t: (-t["points"], -t["gd"], -t["gf"], -t["wins"], t["team"]),
        )
    return groups


class Resolver:
    def __init__(self, matches):
        self.matches = matches
        self.by_fifa = {int(match["fifa_number"]): match for match in matches}
        self.groups = compute_group_tables(matches)

    def resolve_match_team(self, match, side, seen=None):
        seen = seen or set()
        key = (int(match["fifa_number"]), side)
        if key in seen:
            return None
        seen.add(key)
        label = match["team_a"] if side == "A" else match["team_b"]
        return self.resolve_label(label, seen)

    def resolve_label(self, label, seen=None):
        seen = seen or set()
        winner = re.fullmatch(r"Vencedor partida (\d+)", label)
        loser = re.fullmatch(r"Perdedor partida (\d+)", label)
        if winner or loser:
            source = self.by_fifa.get(int((winner or loser).group(1)))
            if not source:
                return None
            win_side = actual_winner_side(source)
            if not win_side:
                return None
            side = win_side
            if loser:
                side = "B" if win_side == "A" else "A"
            return self.resolve_match_team(source, side, seen)

        placed = re.fullmatch(r"([12])º colocado Grupo ([A-L])", label)
        if placed:
            position = int(placed.group(1)) - 1
            group = self.groups.get(placed.group(2))
            if group and group["complete"] and len(group["standings"]) > position:
                return group["standings"][position]["team"]
            return None

        third = re.fullmatch(r"3º colocado dos Grupos ([A-L/]+)", label)
        if third:
            group_codes = third.group(1).split("/")
            candidates = []
            for group_code in group_codes:
                group = self.groups.get(group_code)
                if not group or not group["complete"] or len(group["standings"]) < 3:
                    return None
                candidates.append(group["standings"][2])
            best = sorted(candidates, key=lambda t: (-t["points"], -t["gd"], -t["gf"], -t["wins"], t["team"]))[0]
            return best["team"]
        return label

    def team_payload(self, match, side):
        original = match["team_a"] if side == "A" else match["team_b"]
        resolved = self.resolve_label(original)
        return {
            "name": resolved or original,
            "reference": original if resolved and resolved != original else None,
            "resolved": bool(resolved and resolved != original),
        }


def early_final_lock_at(conn):
    row = DB.one(DB.execute(conn, "SELECT start_at FROM matches ORDER BY start_at ASC, fifa_number ASC LIMIT 1"))
    return row["start_at"] if row else None


def early_final_team_options(conn):
    rows = DB.rows(
        DB.execute(
            conn,
            """
            SELECT team_a, team_b
            FROM matches
            WHERE phase_slug = 'grupos'
            """,
        )
    )
    teams = set()
    for row in rows:
        teams.add(row["team_a"])
        teams.add(row["team_b"])
    return sorted(teams, key=normalize_username)


def is_reference_team(name):
    label = (name or "").casefold()
    return "partida" in label or "colocado" in label


def early_final_outcome(conn):
    matches = fetch_all_matches(conn)
    final_match = next((match for match in matches if match["phase_slug"] == "final"), None)
    if not final_match:
        return {"finalists": [], "champion": None, "runner_up": None, "final_closed": False}

    resolver = Resolver(matches)
    finalists = []
    for side in ("A", "B"):
        payload = resolver.team_payload(final_match, side)
        if not is_reference_team(payload["name"]):
            finalists.append(payload["name"])

    champion = None
    runner_up = None
    if match_closed(final_match) and len(finalists) == 2:
        home = int(final_match["result_home"])
        away = int(final_match["result_away"])
        if home > away:
            champion = finalists[0]
            runner_up = finalists[1]
        elif away > home:
            champion = finalists[1]
            runner_up = finalists[0]
        elif final_match.get("penalty_winner") == "A":
            champion = finalists[0]
            runner_up = finalists[1]
        elif final_match.get("penalty_winner") == "B":
            champion = finalists[1]
            runner_up = finalists[0]

    return {"finalists": finalists, "champion": champion, "runner_up": runner_up, "final_closed": match_closed(final_match)}


def score_early_final_prediction(prediction, outcome):
    if not prediction:
        return {"points": 0, "champion_hit": False, "runner_up_hit": False}

    champion_hit = bool(outcome.get("champion") and prediction["champion"] == outcome["champion"])
    runner_up_hit = bool(outcome.get("runner_up") and prediction["runner_up"] == outcome["runner_up"])
    return {
        "points": (10 if champion_hit else 0) + (5 if runner_up_hit else 0),
        "champion_hit": champion_hit,
        "runner_up_hit": runner_up_hit,
    }


def serialize_early_final_prediction(prediction, outcome=None):
    if not prediction:
        return None
    detail = score_early_final_prediction(prediction, outcome or {"finalists": [], "champion": None, "runner_up": None})
    return {
        "champion": prediction["champion"],
        "runner_up": prediction["runner_up"],
        "points": int(prediction.get("points") or detail["points"]),
        "champion_hit": detail["champion_hit"],
        "runner_up_hit": detail["runner_up_hit"],
        "created_at": prediction.get("created_at"),
        "updated_at": prediction.get("updated_at"),
    }


def fetch_early_final_prediction(conn, user_id):
    if not user_id:
        return None
    return DB.one(DB.execute(conn, "SELECT * FROM early_final_predictions WHERE user_id = ?", (user_id,)))


def build_early_final_payload(conn, user):
    lock_at = early_final_lock_at(conn)
    now = now_brasilia()
    outcome = early_final_outcome(conn)
    prediction = fetch_early_final_prediction(conn, user["id"])
    return {
        "teams": early_final_team_options(conn),
        "lock_at": lock_at,
        "locked": bool(lock_at and now >= parse_dt(lock_at)),
        "server_now": now.isoformat(timespec="seconds"),
        "prediction": serialize_early_final_prediction(prediction, outcome),
        "outcome": outcome,
    }


def effective_status(match, now=None):
    now = now or now_brasilia()
    if match["status"] == "encerrado":
        return "encerrado"
    if now >= parse_dt(match["start_at"]):
        return "em andamento"
    return "agendado"


def serialize_prediction(prediction):
    if not prediction:
        return None
    return {
        "home_score": prediction["home_score"],
        "away_score": prediction["away_score"],
        "points": prediction.get("points", 0),
        "updated_at": prediction.get("updated_at"),
    }


def serialize_match(match, resolver, prediction=None, user=None, now=None):
    now = now or now_brasilia()
    start = parse_dt(match["start_at"])
    reveal_at = start + timedelta(minutes=5)
    team_a = resolver.team_payload(match, "A")
    team_b = resolver.team_payload(match, "B")
    return {
        "id": match["id"],
        "fifa_number": match["fifa_number"],
        "phase": match["phase"],
        "phase_slug": match["phase_slug"],
        "group_code": match.get("group_code"),
        "round_label": match.get("round_label"),
        "team_a": team_a,
        "team_b": team_b,
        "start_at": match["start_at"],
        "status": effective_status(match, now),
        "stored_status": match["status"],
        "locked": now >= start,
        "reveal_at": reveal_at.isoformat(timespec="minutes"),
        "public_predictions_visible": now >= reveal_at or bool(user and user.get("is_admin")),
        "is_knockout": is_knockout(match),
        "result_home": match.get("result_home"),
        "result_away": match.get("result_away"),
        "closed_at": match.get("closed_at"),
        "my_prediction": serialize_prediction(prediction),
    }


def fetch_all_matches(conn):
    return DB.rows(DB.execute(conn, "SELECT * FROM matches ORDER BY start_at ASC, fifa_number ASC"))


def fetch_user_predictions(conn, user_id):
    if not user_id:
        return {}
    rows = DB.rows(DB.execute(conn, "SELECT * FROM predictions WHERE user_id = ?", (user_id,)))
    return {row["match_id"]: row for row in rows}


def recalc_match_points(conn, match_id):
    match = DB.one(DB.execute(conn, "SELECT * FROM matches WHERE id = ?", (match_id,)))
    predictions = DB.rows(DB.execute(conn, "SELECT * FROM predictions WHERE match_id = ?", (match_id,)))
    total_points = 0
    for prediction in predictions:
        points = score_prediction(prediction, match)
        total_points += points
        DB.execute(conn, "UPDATE predictions SET points = ? WHERE id = ?", (points, prediction["id"]))
    return {"predictions": len(predictions), "points": total_points}


def recalc_all_points(conn):
    rows = DB.rows(
        DB.execute(
            conn,
            """
            SELECT
                p.id,
                p.points,
                p.home_score,
                p.away_score,
                m.phase_slug,
                m.result_home,
                m.result_away
            FROM predictions p
            JOIN matches m ON m.id = p.match_id
            WHERE m.result_home IS NOT NULL AND m.result_away IS NOT NULL
            """,
        )
    )
    changed = 0
    for row in rows:
        points = score_prediction(row, row)
        if int(row.get("points") or 0) == points:
            continue
        DB.execute(conn, "UPDATE predictions SET points = ? WHERE id = ?", (points, row["id"]))
        changed += 1
    return changed


def recalc_all_early_final_points(conn):
    outcome = early_final_outcome(conn)
    predictions = DB.rows(DB.execute(conn, "SELECT * FROM early_final_predictions"))
    changed_at = iso_now()
    changed = 0
    for prediction in predictions:
        points = score_early_final_prediction(prediction, outcome)["points"]
        if int(prediction.get("points") or 0) == points:
            continue
        DB.execute(conn, "UPDATE early_final_predictions SET points = ?, updated_at = ? WHERE id = ?", (points, changed_at, prediction["id"]))
        changed += 1
    return changed


def parse_score(value, field_name):
    if value is None or value == "":
        raise ValueError(f"Informe o placar de {field_name}.")
    try:
        score = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"O placar de {field_name} precisa ser um número inteiro.")
    if score < 0 or score > 99:
        raise ValueError(f"O placar de {field_name} deve ficar entre 0 e 99.")
    return score


def validate_username(username):
    username = re.sub(r"\s+", " ", (username or "").strip())
    if len(username) < 3 or len(username) > 40:
        raise ValueError("O nome de usuÃ¡rio deve ter entre 3 e 40 caracteres.")
    if not re.fullmatch(r"[\w\u00C0-\u00FF .'-]+", username):
        raise ValueError("Use apenas letras, nÃºmeros, espaÃ§os, ponto, hÃ­fen ou apÃ³strofo no nome.")
    return username


def validate_password(password):
    password = password or ""
    if len(password) < 6:
        raise ValueError("A senha deve ter pelo menos 6 caracteres.")
    return password


def validate_early_final_payload(body, teams):
    valid_teams = set(teams)
    champion = (body.get("champion") or "").strip()
    runner_up = (body.get("runner_up") or "").strip()

    if not champion or not runner_up:
        raise ValueError("Escolha o campeão e o vice-campeão.")
    if champion not in valid_teams or runner_up not in valid_teams:
        raise ValueError("Escolha seleções válidas da lista.")
    if champion == runner_up:
        raise ValueError("Campeão e vice-campeão precisam ser seleções diferentes.")
    return champion, runner_up


def create_user(username, password, avatar):
    username = validate_username(username)
    password = validate_password(password)
    avatar_mime, avatar_data = parse_avatar_data_url(avatar)
    now = iso_now()
    if len(username) < 3 or len(username) > 40:
        raise ValueError("O nome de usuário deve ter entre 3 e 40 caracteres.")
    if not re.fullmatch(r"[\w\u00C0-\u00FF .'-]+", username):
        raise ValueError("Use apenas letras, números, espaços, ponto, hífen ou apóstrofo no nome.")
    if len(password) < 6:
        raise ValueError("A senha deve ter pelo menos 6 caracteres.")

    with DB.connect() as conn:
        try:
            DB.execute(
                conn,
                """
                INSERT INTO users (
                    username, username_norm, password_hash, is_admin, active,
                    avatar_mime, avatar_data, avatar_updated_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    username,
                    normalize_username(username),
                    hash_password(password),
                    False,
                    True,
                    avatar_mime,
                    avatar_data,
                    now,
                    now,
                ),
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                raise ValueError("Esse nome de usuário já existe.") from exc
            raise
        return DB.one(
            DB.execute(
                conn,
                "SELECT id, username, username_norm, is_admin, active, avatar_mime, avatar_updated_at, created_at FROM users WHERE username_norm = ?",
                (normalize_username(username),),
            )
        )


def handle_auth(req, start_response):
    if req.path == "/api/auth/register" and req.method == "POST":
        try:
            body = req.json()
            user = create_user(body.get("username"), body.get("password"), body.get("avatar"))
        except ValueError as exc:
            return json_error(start_response, str(exc), 400, "cadastro_invalido")
        token = make_session(user["id"])
        user["is_admin"] = boolish(user["is_admin"])
        user["active"] = boolish(user.get("active", True))
        return json_response(
            start_response,
            {"ok": True, "user": public_user(user), "message": "Conta criada."},
            status=201,
            headers=[set_session_cookie(token)],
        )

    if req.path == "/api/auth/login" and req.method == "POST":
        body = req.json()
        username = normalize_username(body.get("username", ""))
        password = body.get("password", "")
        with DB.connect() as conn:
            user = DB.one(
                DB.execute(
                    conn,
                    """
                    SELECT
                        id, username, username_norm, password_hash, is_admin, active,
                        avatar_mime, avatar_updated_at, created_at
                    FROM users
                    WHERE username_norm = ?
                    """,
                    (username,),
                )
            )
        if not user or not verify_password(password, user["password_hash"]):
            return json_error(start_response, "Usuário ou senha inválidos.", 401, "login_invalido")
        user["is_admin"] = boolish(user["is_admin"])
        user["active"] = boolish(user.get("active", True))
        if not user["active"]:
            return json_error(start_response, "Esta conta foi desativada pelo administrador.", 403, "conta_desativada")
        return json_response(
            start_response,
            {"ok": True, "user": public_user(user), "message": "Login realizado."},
            headers=[set_session_cookie(make_session(user["id"]))],
        )

    if req.path == "/api/auth/logout" and req.method == "POST":
        return json_response(start_response, {"ok": True}, headers=[clear_session_cookie()])

    return None


def build_ranking(conn):
    rows = DB.rows(
        DB.execute(
            conn,
            """
            SELECT
                u.id,
                u.username,
                u.active,
                u.avatar_mime,
                u.avatar_updated_at,
                COUNT(p.id) AS prediction_count,
                COALESCE(SUM(p.points), 0) AS match_points,
                COALESCE(MAX(efp.points), 0) AS early_final_points,
                COALESCE(SUM(p.points), 0) + COALESCE(MAX(efp.points), 0) AS points
            FROM users u
            LEFT JOIN predictions p ON p.user_id = u.id
            LEFT JOIN early_final_predictions efp ON efp.user_id = u.id
            WHERE u.active = ?
            GROUP BY u.id, u.username, u.active, u.avatar_mime, u.avatar_updated_at
            ORDER BY points DESC, prediction_count DESC, u.username ASC
            """,
            (True,),
        )
    )
    scoring_rows = DB.rows(
        DB.execute(
            conn,
            """
            SELECT
                p.user_id,
                p.home_score,
                p.away_score,
                m.phase_slug,
                m.result_home,
                m.result_away
            FROM predictions p
            JOIN matches m ON m.id = p.match_id
            JOIN users u ON u.id = p.user_id
            WHERE m.result_home IS NOT NULL AND m.result_away IS NOT NULL
              AND u.active = ?
            """,
            (True,),
        )
    )
    stats_by_user = build_ranking_breakdown(scoring_rows)
    ranking = []
    for index, row in enumerate(rows, start=1):
        breakdown = stats_by_user.get(row["id"], empty_breakdown())
        breakdown["specials"] = [
            {
                "key": "early_final",
                "label": "Final adiantada",
                "points": int(row["early_final_points"] or 0),
            }
        ]
        ranking.append(
            {
                "position": index,
                "id": row["id"],
                "username": row["username"],
                "active": boolish(row.get("active", True)),
                "avatar_url": avatar_url(row),
                "prediction_count": int(row["prediction_count"] or 0),
                "match_points": int(row["match_points"] or 0),
                "early_final_points": int(row["early_final_points"] or 0),
                "points": int(row["points"] or 0),
                "breakdown": breakdown,
            }
        )
    return ranking


def build_admin_users(conn):
    rows = DB.rows(
        DB.execute(
            conn,
            """
            SELECT
                u.id,
                u.username,
                u.is_admin,
                u.active,
                u.avatar_mime,
                u.avatar_updated_at,
                u.created_at,
                COUNT(p.id) AS prediction_count,
                COALESCE(SUM(p.points), 0) + COALESCE(MAX(efp.points), 0) AS points,
                MAX(efp.champion) AS early_final_champion,
                MAX(efp.runner_up) AS early_final_runner_up,
                COALESCE(MAX(efp.points), 0) AS early_final_points,
                MAX(efp.updated_at) AS early_final_updated_at
            FROM users u
            LEFT JOIN predictions p ON p.user_id = u.id
            LEFT JOIN early_final_predictions efp ON efp.user_id = u.id
            GROUP BY u.id, u.username, u.is_admin, u.active, u.avatar_mime, u.avatar_updated_at, u.created_at
            ORDER BY u.username ASC
            """,
        )
    )
    return [
        {
            "id": row["id"],
            "username": row["username"],
            "is_admin": boolish(row["is_admin"]),
            "active": boolish(row.get("active", True)),
            "avatar_url": avatar_url(row),
            "created_at": row["created_at"],
            "prediction_count": int(row["prediction_count"] or 0),
            "points": int(row["points"] or 0),
            "early_final": {
                "submitted": bool(row.get("early_final_champion") and row.get("early_final_runner_up")),
                "champion": row.get("early_final_champion"),
                "runner_up": row.get("early_final_runner_up"),
                "points": int(row["early_final_points"] or 0),
                "updated_at": row.get("early_final_updated_at"),
            },
        }
        for row in rows
    ]


def record_result_audit(conn, old_match, new_values, admin_user, action):
    DB.execute(
        conn,
        """
        INSERT INTO result_audits (
            match_id, admin_user_id, admin_username, action,
            old_result_home, old_result_away, old_penalty_winner, old_status, old_closed_at,
            new_result_home, new_result_away, new_penalty_winner, new_status, new_closed_at,
            changed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            old_match["id"],
            admin_user["id"],
            admin_user["username"],
            action,
            old_match.get("result_home"),
            old_match.get("result_away"),
            old_match.get("penalty_winner"),
            old_match.get("status"),
            old_match.get("closed_at"),
            new_values.get("result_home"),
            new_values.get("result_away"),
            new_values.get("penalty_winner"),
            new_values.get("status"),
            new_values.get("closed_at"),
            iso_now(),
        ),
    )


def fetch_result_audits(conn, limit=100):
    rows = DB.rows(
        DB.execute(
            conn,
            """
            SELECT
                a.*,
                m.fifa_number,
                m.phase,
                m.group_code,
                m.round_label,
                m.team_a,
                m.team_b,
                m.start_at
            FROM result_audits a
            JOIN matches m ON m.id = a.match_id
            ORDER BY a.changed_at DESC, a.id DESC
            LIMIT ?
            """,
            (int(limit),),
        )
    )
    return [
        {
            "id": row["id"],
            "match_id": row["match_id"],
            "fifa_number": row["fifa_number"],
            "phase": row["phase"],
            "group_code": row.get("group_code"),
            "round_label": row.get("round_label"),
            "team_a": row["team_a"],
            "team_b": row["team_b"],
            "start_at": row["start_at"],
            "admin_user_id": row.get("admin_user_id"),
            "admin_username": row["admin_username"],
            "action": row["action"],
            "old_result_home": row.get("old_result_home"),
            "old_result_away": row.get("old_result_away"),
            "old_penalty_winner": row.get("old_penalty_winner"),
            "old_status": row.get("old_status"),
            "old_closed_at": row.get("old_closed_at"),
            "new_result_home": row.get("new_result_home"),
            "new_result_away": row.get("new_result_away"),
            "new_penalty_winner": row.get("new_penalty_winner"),
            "new_status": row.get("new_status"),
            "new_closed_at": row.get("new_closed_at"),
            "changed_at": row["changed_at"],
        }
        for row in rows
    ]


def build_export_payload(conn):
    users = build_admin_users(conn)
    ranking = build_ranking(conn)
    matches = DB.rows(DB.execute(conn, "SELECT * FROM matches ORDER BY start_at ASC, fifa_number ASC"))
    for match in matches:
        match.pop("penalty_winner", None)
    predictions = DB.rows(
        DB.execute(
            conn,
            """
            SELECT
                p.id,
                p.user_id,
                u.username,
                p.match_id,
                m.fifa_number,
                m.phase,
                m.group_code,
                m.round_label,
                m.team_a,
                m.team_b,
                m.start_at,
                p.home_score,
                p.away_score,
                p.points,
                p.updated_at
            FROM predictions p
            JOIN users u ON u.id = p.user_id
            JOIN matches m ON m.id = p.match_id
            ORDER BY u.username ASC, m.start_at ASC
            """,
        )
    )
    early_final_predictions = DB.rows(
        DB.execute(
            conn,
            """
            SELECT
                e.id,
                e.user_id,
                u.username,
                e.champion,
                e.runner_up,
                e.points,
                e.created_at,
                e.updated_at
            FROM early_final_predictions e
            JOIN users u ON u.id = e.user_id
            ORDER BY u.username ASC
            """,
        )
    )
    audits = fetch_result_audits(conn, limit=10000)
    return {
        "generated_at": iso_now(),
        "users": users,
        "matches": matches,
        "predictions": predictions,
        "early_final_predictions": early_final_predictions,
        "ranking": ranking,
        "result_audits": audits,
    }


def format_export_result(home, away):
    if home is None or away is None:
        return "sem resultado"
    return f"{home}x{away}"


def export_payload_as_csv(payload):
    output = io.StringIO()
    fieldnames = [
        "section",
        "user_id",
        "username",
        "is_admin",
        "active",
        "created_at",
        "ranking_position",
        "prediction_count",
        "points",
        "match_id",
        "fifa_number",
        "phase",
        "group_code",
        "round_label",
        "team_a",
        "team_b",
        "start_at",
        "status",
        "result_home",
        "result_away",
        "prediction_home",
        "prediction_away",
        "prediction_points",
        "prediction_updated_at",
        "early_champion",
        "early_runner_up",
        "early_points",
        "early_created_at",
        "early_updated_at",
        "audit_action",
        "audit_admin",
        "audit_changed_at",
        "audit_old_result",
        "audit_new_result",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    ranking_by_user = {row["id"]: row for row in payload["ranking"]}
    for user in payload["users"]:
        rank = ranking_by_user.get(user["id"], {})
        writer.writerow(
            {
                "section": "users",
                "user_id": user["id"],
                "username": user["username"],
                "is_admin": user["is_admin"],
                "active": user["active"],
                "created_at": user["created_at"],
                "ranking_position": rank.get("position"),
                "prediction_count": user["prediction_count"],
                "points": user["points"],
            }
        )
    for rank in payload["ranking"]:
        writer.writerow(
            {
                "section": "ranking",
                "user_id": rank["id"],
                "username": rank["username"],
                "active": rank["active"],
                "ranking_position": rank["position"],
                "prediction_count": rank["prediction_count"],
                "points": rank["points"],
            }
        )
    for match in payload["matches"]:
        writer.writerow(
            {
                "section": "matches",
                "match_id": match["id"],
                "fifa_number": match["fifa_number"],
                "phase": match["phase"],
                "group_code": match.get("group_code"),
                "round_label": match.get("round_label"),
                "team_a": match["team_a"],
                "team_b": match["team_b"],
                "start_at": match["start_at"],
                "status": match["status"],
                "result_home": match.get("result_home"),
                "result_away": match.get("result_away"),
            }
        )
    for prediction in payload["predictions"]:
        writer.writerow(
            {
                "section": "predictions",
                "user_id": prediction["user_id"],
                "username": prediction["username"],
                "match_id": prediction["match_id"],
                "fifa_number": prediction["fifa_number"],
                "phase": prediction["phase"],
                "group_code": prediction.get("group_code"),
                "round_label": prediction.get("round_label"),
                "team_a": prediction["team_a"],
                "team_b": prediction["team_b"],
                "start_at": prediction["start_at"],
                "prediction_home": prediction["home_score"],
                "prediction_away": prediction["away_score"],
                "prediction_points": prediction["points"],
                "prediction_updated_at": prediction["updated_at"],
            }
        )
    for prediction in payload["early_final_predictions"]:
        writer.writerow(
            {
                "section": "early_final_predictions",
                "user_id": prediction["user_id"],
                "username": prediction["username"],
                "early_champion": prediction["champion"],
                "early_runner_up": prediction["runner_up"],
                "early_points": prediction["points"],
                "early_created_at": prediction["created_at"],
                "early_updated_at": prediction["updated_at"],
            }
        )
    for audit in payload["result_audits"]:
        writer.writerow(
            {
                "section": "result_audits",
                "match_id": audit["match_id"],
                "fifa_number": audit["fifa_number"],
                "phase": audit["phase"],
                "group_code": audit.get("group_code"),
                "round_label": audit.get("round_label"),
                "team_a": audit["team_a"],
                "team_b": audit["team_b"],
                "start_at": audit["start_at"],
                "audit_action": audit["action"],
                "audit_admin": audit["admin_username"],
                "audit_changed_at": audit["changed_at"],
                "audit_old_result": format_export_result(audit.get("old_result_home"), audit.get("old_result_away")),
                "audit_new_result": format_export_result(audit.get("new_result_home"), audit.get("new_result_away")),
            }
        )
    return output.getvalue()


def handle_api(req, start_response):
    auth_response = handle_auth(req, start_response)
    if auth_response is not None:
        return auth_response

    if req.path == "/api/me" and req.method == "GET":
        return json_response(start_response, {"ok": True, "user": public_user(current_user(req))})

    if req.path == "/api/me/avatar" and req.method == "POST":
        user, error = require_user(req, start_response)
        if error:
            return error
        body = req.json()
        with DB.connect() as conn:
            changed_at = iso_now()
            if body.get("clear"):
                DB.execute(
                    conn,
                    "UPDATE users SET avatar_mime = NULL, avatar_data = NULL, avatar_updated_at = ? WHERE id = ?",
                    (changed_at, user["id"]),
                )
            else:
                try:
                    mime, encoded = parse_avatar_data_url(body.get("avatar"))
                except ValueError as exc:
                    return json_error(start_response, str(exc), 400, "avatar_invalido")
                DB.execute(
                    conn,
                    "UPDATE users SET avatar_mime = ?, avatar_data = ?, avatar_updated_at = ? WHERE id = ?",
                    (mime, encoded, changed_at, user["id"]),
                )
            conn.commit()
            updated = DB.one(
                DB.execute(
                    conn,
                    "SELECT id, username, username_norm, is_admin, active, avatar_mime, avatar_updated_at, created_at FROM users WHERE id = ?",
                    (user["id"],),
                )
            )
        updated["is_admin"] = boolish(updated["is_admin"])
        updated["active"] = boolish(updated.get("active", True))
        return json_response(start_response, {"ok": True, "user": public_user(updated), "message": "Perfil atualizado."})

    avatar_match = re.fullmatch(r"/api/users/(\d+)/avatar", req.path)
    if avatar_match and req.method == "GET":
        _, error = require_user(req, start_response)
        if error:
            return error
        user_id = int(avatar_match.group(1))
        with DB.connect() as conn:
            row = DB.one(
                DB.execute(
                    conn,
                    "SELECT avatar_mime, avatar_data FROM users WHERE id = ?",
                    (user_id,),
                )
            )
        if not row or not row.get("avatar_mime") or not row.get("avatar_data"):
            return json_error(start_response, "Foto não encontrada.", 404, "foto_nao_encontrada")
        try:
            raw = base64.b64decode(row["avatar_data"], validate=True)
        except Exception:
            return json_error(start_response, "Foto inválida.", 500, "foto_invalida")
        return send(
            start_response,
            raw,
            content_type=row["avatar_mime"],
            headers=[("Cache-Control", "private, max-age=604800, immutable")],
        )

    if req.path == "/api/matches" and req.method == "GET":
        user = current_user(req)
        with DB.connect() as conn:
            matches = fetch_all_matches(conn)
            predictions = fetch_user_predictions(conn, user["id"] if user else None)
        resolver = Resolver(matches)
        now = now_brasilia()
        return json_response(
            start_response,
            {
                "ok": True,
                "server_now": now.isoformat(timespec="seconds"),
                "matches": [
                    serialize_match(match, resolver, predictions.get(match["id"]), user=user, now=now)
                    for match in matches
                ],
            },
        )

    if req.path == "/api/ranking" and req.method == "GET":
        with DB.connect() as conn:
            ranking = build_ranking(conn)
        return json_response(start_response, {"ok": True, "ranking": ranking})

    if req.path == "/api/early-final" and req.method == "GET":
        user, error = require_user(req, start_response)
        if error:
            return error
        with DB.connect() as conn:
            payload = build_early_final_payload(conn, user)
        return json_response(start_response, {"ok": True, **payload})

    if req.path == "/api/early-final" and req.method == "POST":
        user, error = require_user(req, start_response)
        if error:
            return error
        body = req.json()
        with DB.connect() as conn:
            lock_at = early_final_lock_at(conn)
            if lock_at and now_brasilia() >= parse_dt(lock_at):
                return json_error(
                    start_response,
                    f"Final adiantada fechada desde {parse_dt(lock_at).strftime('%d/%m/%Y às %H:%M')}.",
                    409,
                    "final_adiantada_fechada",
                )
            teams = early_final_team_options(conn)
            try:
                champion, runner_up = validate_early_final_payload(body, teams)
            except ValueError as exc:
                return json_error(start_response, str(exc), 400, "final_adiantada_invalida")
            now = iso_now()
            DB.execute(
                conn,
                """
                INSERT INTO early_final_predictions (user_id, champion, runner_up, finalist_a, finalist_b, points, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(user_id)
                DO UPDATE SET
                    champion = excluded.champion,
                    runner_up = excluded.runner_up,
                    finalist_a = excluded.finalist_a,
                    finalist_b = excluded.finalist_b,
                    updated_at = excluded.updated_at
                """,
                (user["id"], champion, runner_up, champion, runner_up, now, now),
            )
            recalc_all_early_final_points(conn)
            conn.commit()
            payload = build_early_final_payload(conn, user)
        return json_response(start_response, {"ok": True, "message": "Final adiantada salva.", **payload})

    if req.path == "/api/admin/users" and req.method == "GET":
        _, error = require_admin(req, start_response)
        if error:
            return error
        with DB.connect() as conn:
            users = build_admin_users(conn)
        return json_response(start_response, {"ok": True, "users": users})

    rename_user = re.fullmatch(r"/api/admin/users/(\d+)/rename", req.path)
    if rename_user and req.method == "POST":
        _, error = require_admin(req, start_response)
        if error:
            return error
        user_id = int(rename_user.group(1))
        body = req.json()
        try:
            username = validate_username(body.get("username"))
        except ValueError as exc:
            return json_error(start_response, str(exc), 400, "usuario_invalido")
        with DB.connect() as conn:
            target = DB.one(DB.execute(conn, "SELECT id FROM users WHERE id = ?", (user_id,)))
            if not target:
                return json_error(start_response, "UsuÃ¡rio nÃ£o encontrado.", 404, "usuario_nao_encontrado")
            try:
                DB.execute(
                    conn,
                    "UPDATE users SET username = ?, username_norm = ? WHERE id = ?",
                    (username, normalize_username(username), user_id),
                )
                conn.commit()
            except Exception as exc:
                conn.rollback()
                if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                    return json_error(start_response, "Esse nome de usuÃ¡rio jÃ¡ existe.", 409, "usuario_duplicado")
                raise
            users = build_admin_users(conn)
        return json_response(start_response, {"ok": True, "users": users, "message": "UsuÃ¡rio renomeado."})

    reset_user = re.fullmatch(r"/api/admin/users/(\d+)/reset-password", req.path)
    if reset_user and req.method == "POST":
        _, error = require_admin(req, start_response)
        if error:
            return error
        user_id = int(reset_user.group(1))
        body = req.json()
        try:
            password = validate_password(body.get("password"))
        except ValueError as exc:
            return json_error(start_response, str(exc), 400, "senha_invalida")
        with DB.connect() as conn:
            target = DB.one(DB.execute(conn, "SELECT id FROM users WHERE id = ?", (user_id,)))
            if not target:
                return json_error(start_response, "UsuÃ¡rio nÃ£o encontrado.", 404, "usuario_nao_encontrado")
            DB.execute(conn, "UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))
            conn.commit()
        return json_response(start_response, {"ok": True, "message": "Senha redefinida."})

    active_user = re.fullmatch(r"/api/admin/users/(\d+)/active", req.path)
    if active_user and req.method == "POST":
        admin, error = require_admin(req, start_response)
        if error:
            return error
        user_id = int(active_user.group(1))
        body = req.json()
        active = boolish(body.get("active"))
        if user_id == admin["id"] and not active:
            return json_error(start_response, "VocÃª nÃ£o pode desativar sua prÃ³pria conta.", 400, "auto_desativar")
        with DB.connect() as conn:
            target = DB.one(DB.execute(conn, "SELECT id FROM users WHERE id = ?", (user_id,)))
            if not target:
                return json_error(start_response, "UsuÃ¡rio nÃ£o encontrado.", 404, "usuario_nao_encontrado")
            DB.execute(conn, "UPDATE users SET active = ? WHERE id = ?", (active, user_id))
            conn.commit()
            users = build_admin_users(conn)
        return json_response(
            start_response,
            {"ok": True, "users": users, "message": "Conta ativada." if active else "Conta desativada."},
        )

    if req.path == "/api/admin/result-audits" and req.method == "GET":
        _, error = require_admin(req, start_response)
        if error:
            return error
        with DB.connect() as conn:
            audits = fetch_result_audits(conn)
        return json_response(start_response, {"ok": True, "audits": audits})

    if req.path == "/api/admin/export/json" and req.method == "GET":
        _, error = require_admin(req, start_response)
        if error:
            return error
        with DB.connect() as conn:
            payload = build_export_payload(conn)
        return send(
            start_response,
            json.dumps(payload, ensure_ascii=False, indent=2),
            content_type="application/json; charset=utf-8",
            headers=[("Content-Disposition", 'attachment; filename="bolao-copa-2026-export.json"')],
        )

    if req.path == "/api/admin/export/csv" and req.method == "GET":
        _, error = require_admin(req, start_response)
        if error:
            return error
        with DB.connect() as conn:
            payload = build_export_payload(conn)
        return send(
            start_response,
            export_payload_as_csv(payload),
            content_type="text/csv; charset=utf-8",
            headers=[("Content-Disposition", 'attachment; filename="bolao-copa-2026-export.csv"')],
        )

    public_match = re.fullmatch(r"/api/matches/(\d+)/predictions", req.path)
    if public_match and req.method == "GET":
        user, error = require_user(req, start_response)
        if error:
            return error
        match_id = int(public_match.group(1))
        with DB.connect() as conn:
            match = DB.one(DB.execute(conn, "SELECT * FROM matches WHERE id = ?", (match_id,)))
            if not match:
                return json_error(start_response, "Partida não encontrada.", 404, "partida_nao_encontrada")
            if now_brasilia() < parse_dt(match["start_at"]) + timedelta(minutes=5) and not user["is_admin"]:
                return json_error(
                    start_response,
                    "Os palpites desta partida ficam ocultos até 5 minutos após o início.",
                    403,
                    "palpites_ocultos",
                )
            rows = DB.rows(
                DB.execute(
                    conn,
                    """
                    SELECT
                        u.id,
                        u.username,
                        u.avatar_mime,
                        u.avatar_updated_at,
                        p.id AS prediction_id,
                        p.home_score,
                        p.away_score,
                        p.points,
                        p.updated_at
                    FROM users u
                    LEFT JOIN predictions p ON p.user_id = u.id AND p.match_id = ?
                    WHERE u.active = ?
                    ORDER BY CASE WHEN p.id IS NULL THEN 1 ELSE 0 END, u.username ASC
                    """,
                    (match_id, True),
                )
            )
        for row in rows:
            row["has_prediction"] = row.get("prediction_id") is not None
            row["avatar_url"] = avatar_url(row)
            row.pop("prediction_id", None)
            row.pop("avatar_mime", None)
            row.pop("avatar_updated_at", None)
        return json_response(start_response, {"ok": True, "predictions": rows})

    save_prediction = re.fullmatch(r"/api/predictions/(\d+)", req.path)
    if save_prediction and req.method == "POST":
        user, error = require_user(req, start_response)
        if error:
            return error
        match_id = int(save_prediction.group(1))
        body = req.json()
        with DB.connect() as conn:
            match = DB.one(DB.execute(conn, "SELECT * FROM matches WHERE id = ?", (match_id,)))
            if not match:
                return json_error(start_response, "Partida não encontrada.", 404, "partida_nao_encontrada")
            if match["status"] == "encerrado":
                return json_error(
                    start_response,
                    "Esta partida já foi encerrada pelo administrador.",
                    409,
                    "partida_encerrada",
                )
            start = parse_dt(match["start_at"])
            if now_brasilia() >= start:
                return json_error(
                    start_response,
                    f"Palpites encerrados para esta partida desde {start.strftime('%d/%m/%Y às %H:%M')}.",
                    409,
                    "palpite_fechado",
                )
            if body.get("clear"):
                DB.execute(conn, "DELETE FROM predictions WHERE user_id = ? AND match_id = ?", (user["id"], match_id))
                conn.commit()
                return json_response(start_response, {"ok": True, "message": "Palpite removido.", "prediction": None})
            try:
                home_score = parse_score(body.get("home_score"), "time A")
                away_score = parse_score(body.get("away_score"), "time B")
                advances = None
            except ValueError as exc:
                return json_error(start_response, str(exc), 400, "palpite_invalido")
            DB.execute(
                conn,
                """
                INSERT INTO predictions (user_id, match_id, home_score, away_score, advances, points, updated_at)
                VALUES (?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(user_id, match_id)
                DO UPDATE SET
                    home_score = excluded.home_score,
                    away_score = excluded.away_score,
                    advances = excluded.advances,
                    updated_at = excluded.updated_at
                """,
                (user["id"], match_id, home_score, away_score, advances, iso_now()),
            )
            conn.commit()
            prediction = DB.one(
                DB.execute(conn, "SELECT * FROM predictions WHERE user_id = ? AND match_id = ?", (user["id"], match_id))
            )
        return json_response(
            start_response,
            {"ok": True, "message": "Palpite aceito.", "prediction": serialize_prediction(prediction)},
        )

    result_match = re.fullmatch(r"/api/admin/matches/(\d+)/result", req.path)
    if result_match and req.method == "POST":
        user, error = require_admin(req, start_response)
        if error:
            return error
        match_id = int(result_match.group(1))
        body = req.json()
        with DB.connect() as conn:
            match = DB.one(DB.execute(conn, "SELECT * FROM matches WHERE id = ?", (match_id,)))
            if not match:
                return json_error(start_response, "Partida não encontrada.", 404, "partida_nao_encontrada")
            try:
                home = parse_score(body.get("result_home"), "time A")
                away = parse_score(body.get("result_away"), "time B")
                penalty_winner = None
            except ValueError as exc:
                return json_error(start_response, str(exc), 400, "resultado_invalido")

            closed_at = iso_now()
            record_result_audit(
                conn,
                match,
                {
                    "result_home": home,
                    "result_away": away,
                    "penalty_winner": penalty_winner,
                    "status": "encerrado",
                    "closed_at": closed_at,
                },
                user,
                "save_result",
            )
            DB.execute(
                conn,
                """
                UPDATE matches
                SET result_home = ?, result_away = ?, penalty_winner = ?,
                    status = 'encerrado', closed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (home, away, penalty_winner, closed_at, closed_at, match_id),
            )
            summary = recalc_match_points(conn, match_id)
            early_final_changed = recalc_all_early_final_points(conn)
            conn.commit()
        return json_response(
            start_response,
            {
                "ok": True,
                "message": "Resultado salvo. Ranking recalculado.",
                "recalculated": summary,
                "early_final_recalculated": early_final_changed,
                "admin": public_user(user),
            },
        )

    clear_match = re.fullmatch(r"/api/admin/matches/(\d+)/clear-result", req.path)
    if clear_match and req.method == "POST":
        user, error = require_admin(req, start_response)
        if error:
            return error
        match_id = int(clear_match.group(1))
        with DB.connect() as conn:
            match = DB.one(DB.execute(conn, "SELECT * FROM matches WHERE id = ?", (match_id,)))
            changed_at = iso_now()
            if match:
                record_result_audit(
                    conn,
                    match,
                    {
                        "result_home": None,
                        "result_away": None,
                        "penalty_winner": None,
                        "status": "agendado",
                        "closed_at": None,
                    },
                    user,
                    "clear_result",
                )
            if not match:
                return json_error(start_response, "Partida não encontrada.", 404, "partida_nao_encontrada")
            DB.execute(
                conn,
                """
                UPDATE matches
                SET result_home = NULL, result_away = NULL, penalty_winner = NULL,
                    status = 'agendado', closed_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (changed_at, match_id),
            )
            DB.execute(conn, "UPDATE predictions SET points = 0 WHERE match_id = ?", (match_id,))
            early_final_changed = recalc_all_early_final_points(conn)
            conn.commit()
        return json_response(
            start_response,
            {
                "ok": True,
                "message": "Resultado reaberto. Pontos removidos desta partida.",
                "early_final_recalculated": early_final_changed,
            },
        )

    return json_error(start_response, "Rota não encontrada.", 404, "rota_nao_encontrada")


def serve_static(req, start_response):
    rel = req.path.removeprefix("/static/")
    requested = (STATIC_DIR / rel).resolve()
    if STATIC_DIR not in requested.parents and requested != STATIC_DIR:
        return json_error(start_response, "Arquivo inválido.", 403, "arquivo_invalido")
    if not requested.exists() or not requested.is_file():
        return json_error(start_response, "Arquivo não encontrado.", 404, "arquivo_nao_encontrado")
    content_type = mimetypes.guess_type(str(requested))[0] or "application/octet-stream"
    if requested.name.endswith(".webmanifest"):
        content_type = "application/manifest+json"
    return send(start_response, requested.read_bytes(), content_type=content_type)


def serve_service_worker(start_response):
    worker = STATIC_DIR / "sw.js"
    if not worker.exists():
        return json_error(start_response, "Arquivo nÃ£o encontrado.", 404, "arquivo_nao_encontrado")
    return send(
        start_response,
        worker.read_bytes(),
        content_type="application/javascript; charset=utf-8",
        headers=[("Cache-Control", "no-cache")],
    )


def serve_index(start_response):
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    return send(start_response, html, content_type="text/html; charset=utf-8")


def application(environ, start_response):
    req = Request(environ)
    try:
        if req.path.startswith("/api/"):
            return handle_api(req, start_response)
        if req.path == "/sw.js":
            return serve_service_worker(start_response)
        if req.path.startswith("/static/"):
            return serve_static(req, start_response)
        if req.path in {"/", "/index.html"}:
            return serve_index(start_response)
        return serve_index(start_response)
    except json.JSONDecodeError:
        return json_error(start_response, "JSON inválido.", 400, "json_invalido")
    except Exception:
        traceback.print_exc(file=sys.stderr)
        if req.path.startswith("/api/"):
            return json_error(start_response, "Erro interno do servidor.", 500, "erro_interno")
        return send(start_response, "<h1>Erro interno</h1>", status=500, content_type="text/html; charset=utf-8")


if os.getenv("BOLAO_SKIP_INIT") != "1":
    init_db()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    try:
        print(f"Bolão rodando em http://127.0.0.1:{port}", flush=True)
    except OSError:
        pass
    with make_server("", port, application) as httpd:
        httpd.serve_forever()
