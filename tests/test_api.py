import base64
import io
import json
import os
import tempfile
import unittest
import urllib.parse
from datetime import timedelta
from wsgiref.util import setup_testing_defaults

os.environ["BOLAO_SKIP_INIT"] = "1"

import app as bolao


TEST_AVATAR = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode("ascii")


class ApiClient:
    def __init__(self):
        self.cookie = None

    def request(self, method, path, body=None):
        parsed = urllib.parse.urlsplit(path)
        raw_body = b""
        if body is not None:
            raw_body = json.dumps(body).encode("utf-8")
        environ = {}
        setup_testing_defaults(environ)
        environ.update(
            {
                "REQUEST_METHOD": method,
                "PATH_INFO": parsed.path,
                "QUERY_STRING": parsed.query,
                "CONTENT_LENGTH": str(len(raw_body)),
                "CONTENT_TYPE": "application/json",
                "wsgi.input": io.BytesIO(raw_body),
            }
        )
        if self.cookie:
            environ["HTTP_COOKIE"] = self.cookie

        captured = {}

        def start_response(status, headers, exc_info=None):
            captured["status"] = int(status.split()[0])
            captured["headers"] = headers
            for name, value in headers:
                if name.lower() == "set-cookie":
                    self.cookie = value.split(";", 1)[0]

        response_body = b"".join(bolao.application(environ, start_response))
        content_type = next(
            (value for name, value in captured["headers"] if name.lower() == "content-type"),
            "",
        )
        if "json" in content_type:
            data = json.loads(response_body.decode("utf-8"))
        else:
            try:
                data = response_body.decode("utf-8")
            except UnicodeDecodeError:
                data = response_body
        return captured["status"], data, dict(captured["headers"])

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, body=None):
        return self.request("POST", path, body or {})


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("RENDER", None)
        os.environ["SQLITE_PATH"] = os.path.join(self.tmp.name, "test.sqlite3")
        os.environ["ADMIN_USERNAME"] = "Math"
        os.environ["ADMIN_PASSWORD"] = "secret123"
        os.environ["SECRET_KEY"] = "test-secret"
        bolao.SECRET_KEY = "test-secret"
        bolao.DB = bolao.Database()
        bolao.init_db()
        self.admin = ApiClient()
        status, data, _ = self.admin.post(
            "/api/auth/login",
            {"username": "Math", "password": "secret123"},
        )
        self.assertEqual(status, 200, data)

    def tearDown(self):
        self.tmp.cleanup()

    def first_match_id(self):
        with bolao.DB.connect() as conn:
            return bolao.DB.one(bolao.DB.execute(conn, "SELECT id FROM matches ORDER BY start_at LIMIT 1"))["id"]

    def first_knockout_match_id(self):
        with bolao.DB.connect() as conn:
            return bolao.DB.one(
                bolao.DB.execute(conn, "SELECT id FROM matches WHERE phase_slug != 'grupos' ORDER BY start_at LIMIT 1")
            )["id"]

    def final_match_id(self):
        with bolao.DB.connect() as conn:
            return bolao.DB.one(bolao.DB.execute(conn, "SELECT id FROM matches WHERE phase_slug = 'final' LIMIT 1"))["id"]

    def set_match_start(self, match_id, when):
        with bolao.DB.connect() as conn:
            bolao.DB.execute(
                conn,
                "UPDATE matches SET start_at = ?, status = 'agendado', result_home = NULL, result_away = NULL, penalty_winner = NULL WHERE id = ?",
                (when.isoformat(timespec="minutes"), match_id),
            )
            conn.commit()

    def set_all_match_starts(self, when):
        with bolao.DB.connect() as conn:
            bolao.DB.execute(
                conn,
                "UPDATE matches SET start_at = ?, status = 'agendado', result_home = NULL, result_away = NULL, penalty_winner = NULL",
                (when.isoformat(timespec="minutes"),),
            )
            conn.commit()

    def register_player(self, username="Jogador Teste", password="senha123"):
        client = ApiClient()
        status, data, _ = client.post(
            "/api/auth/register",
            {"username": username, "password": password, "avatar": TEST_AVATAR},
        )
        self.assertEqual(status, 201, data)
        return client, data["user"]

    def test_register_requires_avatar(self):
        client = ApiClient()
        status, data, _ = client.post(
            "/api/auth/register",
            {"username": "Sem Foto", "password": "senha123"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["code"], "cadastro_invalido")

    def test_avatar_is_served_by_cacheable_endpoint_not_embedded_in_json(self):
        player, user = self.register_player()

        self.assertTrue(user["avatar_url"].startswith(f"/api/users/{user['id']}/avatar?v="))
        self.assertNotIn("data:image", json.dumps(user))

        status, data, headers = player.get(user["avatar_url"])
        self.assertEqual(status, 200)
        self.assertIsInstance(data, bytes)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertIn("private", headers["Cache-Control"])
        self.assertIn("max-age=604800", headers["Cache-Control"])

        status, data, _ = self.admin.get("/api/ranking")
        self.assertEqual(status, 200, data)
        self.assertNotIn("data:image", json.dumps(data))

    def test_prediction_locks_at_start_and_reveals_after_five_minutes(self):
        player, _ = self.register_player()
        self.register_player("Aaa Sem Palpite", "senha123")
        match_id = self.first_match_id()
        self.set_match_start(match_id, bolao.now_brasilia() + timedelta(hours=2))

        status, data, _ = player.post(f"/api/predictions/{match_id}", {"home_score": 2, "away_score": 1})
        self.assertEqual(status, 200, data)

        self.set_match_start(match_id, bolao.now_brasilia() - timedelta(minutes=1))
        status, data, _ = player.post(f"/api/predictions/{match_id}", {"home_score": 1, "away_score": 1})
        self.assertEqual(status, 409)
        self.assertEqual(data["code"], "palpite_fechado")

        status, data, _ = player.get(f"/api/matches/{match_id}/predictions")
        self.assertEqual(status, 403)
        self.assertEqual(data["code"], "palpites_ocultos")

        self.set_match_start(match_id, bolao.now_brasilia() - timedelta(minutes=6))
        status, data, _ = player.get(f"/api/matches/{match_id}/predictions")
        self.assertEqual(status, 200, data)
        self.assertEqual(sum(1 for row in data["predictions"] if row["has_prediction"]), 1)
        first_missing = next(index for index, row in enumerate(data["predictions"]) if not row["has_prediction"])
        self.assertTrue(all(not row["has_prediction"] for row in data["predictions"][first_missing:]))

    def test_result_edit_recalculates_ranking_and_records_audit(self):
        player, user = self.register_player()
        match_id = self.first_match_id()
        self.set_match_start(match_id, bolao.now_brasilia() + timedelta(hours=2))
        player.post(f"/api/predictions/{match_id}", {"home_score": 2, "away_score": 1})
        prediction_updated_at = "2026-06-01T10:11:12-03:00"
        with bolao.DB.connect() as conn:
            bolao.DB.execute(
                conn,
                "UPDATE predictions SET updated_at = ? WHERE user_id = ? AND match_id = ?",
                (prediction_updated_at, user["id"], match_id),
            )
            conn.commit()

        status, data, _ = self.admin.post(
            f"/api/admin/matches/{match_id}/result",
            {"result_home": 2, "result_away": 1},
        )
        self.assertEqual(status, 200, data)
        status, data, _ = self.admin.get(f"/api/matches/{match_id}/predictions")
        self.assertEqual(status, 200, data)
        self.assertEqual(data["predictions"][0]["updated_at"], prediction_updated_at)

        status, data, _ = self.admin.get("/api/ranking")
        player_rank = next(row for row in data["ranking"] if row["id"] == user["id"])
        self.assertEqual(player_rank["points"], 6)

        status, data, _ = self.admin.post(
            f"/api/admin/matches/{match_id}/result",
            {"result_home": 3, "result_away": 2},
        )
        self.assertEqual(status, 200, data)
        status, data, _ = self.admin.get("/api/ranking")
        player_rank = next(row for row in data["ranking"] if row["id"] == user["id"])
        self.assertEqual(player_rank["points"], 4)

        status, data, _ = self.admin.get("/api/admin/result-audits")
        self.assertEqual(status, 200, data)
        self.assertGreaterEqual(len(data["audits"]), 2)
        self.assertEqual(data["audits"][0]["action"], "save_result")

    def test_early_final_locks_at_first_match_and_scores_ranking(self):
        player, user = self.register_player()
        first_match = self.first_match_id()
        final_match = self.final_match_id()
        self.set_all_match_starts(bolao.now_brasilia() + timedelta(hours=2))

        status, data, _ = player.get("/api/early-final")
        self.assertEqual(status, 200, data)
        self.assertFalse(data["locked"])
        self.assertIn("Brasil", data["teams"])

        status, data, _ = player.post(
            "/api/early-final",
            {"champion": "Brasil", "runner_up": "Argentina"},
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["prediction"]["champion"], "Brasil")
        self.assertEqual(data["prediction"]["runner_up"], "Argentina")

        with bolao.DB.connect() as conn:
            bolao.DB.execute(
                conn,
                "UPDATE matches SET team_a = 'Brasil', team_b = 'Argentina', result_home = NULL, result_away = NULL, status = 'agendado' WHERE id = ?",
                (final_match,),
            )
            conn.commit()

        status, data, _ = self.admin.post(
            f"/api/admin/matches/{final_match}/result",
            {"result_home": 2, "result_away": 1},
        )
        self.assertEqual(status, 200, data)

        status, data, _ = self.admin.get("/api/ranking")
        player_rank = next(row for row in data["ranking"] if row["id"] == user["id"])
        self.assertEqual(player_rank["early_final_points"], 15)
        self.assertEqual(player_rank["points"], 15)

        self.set_match_start(first_match, bolao.now_brasilia() - timedelta(minutes=1))
        status, data, _ = player.post(
            "/api/early-final",
            {"champion": "Argentina", "runner_up": "Brasil"},
        )
        self.assertEqual(status, 409)
        self.assertEqual(data["code"], "final_adiantada_fechada")

    def test_admin_users_show_early_final_submitted_and_missing(self):
        player_with_final, user_with_final = self.register_player("Com Final", "senha123")
        _, user_missing_final = self.register_player("Sem Final", "senha123")
        self.set_all_match_starts(bolao.now_brasilia() + timedelta(hours=2))

        status, data, _ = player_with_final.post(
            "/api/early-final",
            {"champion": "Brasil", "runner_up": "Argentina"},
        )
        self.assertEqual(status, 200, data)

        status, data, _ = self.admin.get("/api/admin/users")
        self.assertEqual(status, 200, data)
        users = {row["id"]: row for row in data["users"]}

        self.assertTrue(users[user_with_final["id"]]["early_final"]["submitted"])
        self.assertEqual(users[user_with_final["id"]]["early_final"]["champion"], "Brasil")
        self.assertEqual(users[user_with_final["id"]]["early_final"]["runner_up"], "Argentina")
        self.assertFalse(users[user_missing_final["id"]]["early_final"]["submitted"])

    def test_migrates_early_final_table_with_champion_and_runner_up_only(self):
        _, user = self.register_player("Legacy Runner", "senha123")
        now = bolao.iso_now()
        with bolao.DB.connect() as conn:
            bolao.DB.execute(conn, "DROP TABLE early_final_predictions")
            bolao.DB.execute(
                conn,
                """
                CREATE TABLE early_final_predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                    champion TEXT NOT NULL,
                    runner_up TEXT NOT NULL,
                    points INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """,
            )
            bolao.DB.execute(
                conn,
                """
                INSERT INTO early_final_predictions (user_id, champion, runner_up, points, created_at, updated_at)
                VALUES (?, ?, ?, 0, ?, ?)
                """,
                (user["id"], "Brasil", "Argentina", now, now),
            )
            conn.commit()

        bolao.init_db()

        with bolao.DB.connect() as conn:
            columns = bolao.table_columns(conn, "early_final_predictions")
            row = bolao.DB.one(bolao.DB.execute(conn, "SELECT * FROM early_final_predictions WHERE user_id = ?", (user["id"],)))
        self.assertIn("finalist_a", columns)
        self.assertIn("finalist_b", columns)
        self.assertEqual(row["champion"], "Brasil")
        self.assertEqual(row["runner_up"], "Argentina")
        self.assertEqual(row["finalist_a"], "Brasil")
        self.assertEqual(row["finalist_b"], "Argentina")

        status, data, _ = self.admin.post("/api/auth/login", {"username": "Math", "password": "secret123"})
        self.assertEqual(status, 200, data)

    def test_migrates_early_final_table_with_legacy_finalists_only(self):
        _, user = self.register_player("Legacy Finalists", "senha123")
        now = bolao.iso_now()
        with bolao.DB.connect() as conn:
            bolao.DB.execute(conn, "DROP TABLE early_final_predictions")
            bolao.DB.execute(
                conn,
                """
                CREATE TABLE early_final_predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                    champion TEXT NOT NULL,
                    finalist_a TEXT NOT NULL,
                    finalist_b TEXT NOT NULL,
                    points INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """,
            )
            bolao.DB.execute(
                conn,
                """
                INSERT INTO early_final_predictions (user_id, champion, finalist_a, finalist_b, points, created_at, updated_at)
                VALUES (?, ?, ?, ?, 0, ?, ?)
                """,
                (user["id"], "Brasil", "Brasil", "Argentina", now, now),
            )
            conn.commit()

        bolao.init_db()

        with bolao.DB.connect() as conn:
            row = bolao.DB.one(bolao.DB.execute(conn, "SELECT * FROM early_final_predictions WHERE user_id = ?", (user["id"],)))
        self.assertEqual(row["champion"], "Brasil")
        self.assertEqual(row["runner_up"], "Argentina")
        self.assertEqual(row["finalist_a"], "Brasil")
        self.assertEqual(row["finalist_b"], "Argentina")

    def test_knockout_draw_does_not_require_advancing_team(self):
        player, user = self.register_player()
        match_id = self.first_knockout_match_id()
        self.set_match_start(match_id, bolao.now_brasilia() + timedelta(hours=2))

        status, data, _ = player.post(f"/api/predictions/{match_id}", {"home_score": 1, "away_score": 1})
        self.assertEqual(status, 200, data)
        self.assertNotIn("advances", data["prediction"])

        status, data, _ = self.admin.post(
            f"/api/admin/matches/{match_id}/result",
            {"result_home": 1, "result_away": 1},
        )
        self.assertEqual(status, 200, data)

        status, data, _ = self.admin.get("/api/ranking")
        player_rank = next(row for row in data["ranking"] if row["id"] == user["id"])
        self.assertEqual(player_rank["points"], 6)

    def test_admin_can_rename_reset_password_and_disable_user(self):
        _, user = self.register_player("Carlos", "senha123")

        status, data, _ = self.admin.post(f"/api/admin/users/{user['id']}/rename", {"username": "Carlos Novo"})
        self.assertEqual(status, 200, data)
        self.assertTrue(any(row["username"] == "Carlos Novo" for row in data["users"]))

        status, data, _ = self.admin.post(
            f"/api/admin/users/{user['id']}/reset-password",
            {"password": "nova123"},
        )
        self.assertEqual(status, 200, data)

        status, data, _ = self.admin.post(f"/api/admin/users/{user['id']}/active", {"active": False})
        self.assertEqual(status, 200, data)

        status, ranking_data, _ = self.admin.get("/api/ranking")
        self.assertEqual(status, 200, ranking_data)
        self.assertFalse(any(row["id"] == user["id"] for row in ranking_data["ranking"]))

        inactive_login = ApiClient()
        status, data, _ = inactive_login.post(
            "/api/auth/login",
            {"username": "Carlos Novo", "password": "nova123"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(data["code"], "conta_desativada")

        self.admin.post(f"/api/admin/users/{user['id']}/active", {"active": True})
        active_login = ApiClient()
        status, data, _ = active_login.post(
            "/api/auth/login",
            {"username": "Carlos Novo", "password": "nova123"},
        )
        self.assertEqual(status, 200, data)

        status, ranking_data, _ = self.admin.get("/api/ranking")
        self.assertEqual(status, 200, ranking_data)
        self.assertTrue(any(row["id"] == user["id"] for row in ranking_data["ranking"]))

    def test_admin_exports_json_and_csv(self):
        self.register_player("Exportador", "senha123")
        status, data, _ = self.admin.get("/api/admin/export/json")
        self.assertEqual(status, 200, data)
        self.assertIn("users", data)
        self.assertIn("matches", data)
        self.assertIn("predictions", data)
        self.assertIn("early_final_predictions", data)
        self.assertIn("ranking", data)

        status, data, headers = self.admin.get("/api/admin/export/csv")
        self.assertEqual(status, 200, data)
        self.assertIn("text/csv", headers["Content-Type"])
        self.assertIn("section", data)
        self.assertIn("ranking", data)


if __name__ == "__main__":
    unittest.main()
