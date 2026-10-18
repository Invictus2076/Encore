"""End-to-end tests for the Encore website, run against an in-memory SQLite stand-in for MySQL.

    python -m unittest discover -s tests -v
"""
import datetime as dt
import os
import re
import sys
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "fakes"))           # the fake mysql.connector
sys.path.insert(0, os.path.dirname(HERE))                 # the project root
os.environ.setdefault("ENCORE_DB_PASSWORD", "test")
os.environ.setdefault("ENCORE_SECRET_KEY", "test-secret")

from mysql import connector as fake  # noqa: E402
import app as encore  # noqa: E402
import encore_core as core  # noqa: E402

FUTURE = (dt.date.today() + dt.timedelta(days=30)).isoformat()
PAST = (dt.date.today() - dt.timedelta(days=30)).isoformat()


class EncoreTests(unittest.TestCase):
    def setUp(self):
        fake.reset()
        encore._schema_ready = False
        encore.app.config["TESTING"] = True
        self.client = encore.app.test_client()
        conn = fake.connect()
        core.ensure_schema(conn)
        cur = conn.cursor()
        cur.execute("INSERT INTO Concerts (artist_name, venue, date, time, ticket_price, available_tickets, total_tickets, organizer, artistid)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", ("The Velvet Static", "Echo Hall", FUTURE, "19:30:00", 1500, 5, 5, "Lowlight Live", 1))
        cur.execute("INSERT INTO Concerts (artist_name, venue, date, time, ticket_price, available_tickets, total_tickets, organizer, artistid)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", ("Old Show", "Echo Hall", PAST, "20:00:00", 900, 10, 10, "Lowlight Live", 2))
        cur.execute("INSERT INTO admin (password) VALUES (%s)", ("legacy-admin-pw",))  # plain text from the console era
        conn.commit()

    # helpers
    def csrf(self, path="/"):
        html = self.client.get(path).get_data(as_text=True)
        return re.search(r'name="csrf" value="([^"]+)"', html).group(1)

    def post(self, path, data, form_page="/login"):
        data = dict(data, csrf=self.csrf(form_page))
        return self.client.post(path, data=data)

    def register(self, name="alice", pw="correct-horse"):
        return self.post("/register", {"username": name, "password": pw}, "/register")

    def rows(self, sql, params=()):
        cur = fake.connect().cursor()
        cur.execute(sql, params)
        return cur.fetchall()

    # tests
    def test_home_lists_only_upcoming_concerts(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("The Velvet Static", html)
        self.assertNotIn("Old Show", html)

    def test_search_is_parameterised(self):
        html = self.client.get("/?q=' OR '1'='1").get_data(as_text=True)
        self.assertIn("No concerts match", html)
        self.assertIn("Velvet", self.client.get("/?q=velvet").get_data(as_text=True))

    def test_unknown_sort_falls_back_safely(self):
        self.assertEqual(self.client.get("/?sort=artist_name;DROP TABLE user").status_code, 200)

    def test_post_without_csrf_is_rejected(self):
        self.assertEqual(self.client.post("/register", data={"username": "x", "password": "y" * 8}).status_code, 400)

    def test_register_hashes_password_and_logs_in(self):
        r = self.register()
        self.assertEqual(r.status_code, 302)
        stored = self.rows("SELECT password FROM user WHERE username = 'alice'")[0][0]
        self.assertTrue(stored.startswith("pbkdf2_sha256$"))
        self.assertIn("My tickets", self.client.get("/").get_data(as_text=True))

    def test_register_rejects_taken_name_and_short_password(self):
        self.register()
        self.client.post("/logout", data={"csrf": self.csrf()})
        self.assertEqual(self.register().status_code, 400)
        self.assertEqual(self.register("bob", "short").status_code, 400)

    def test_wrong_password_fails(self):
        self.register()
        self.client.post("/logout", data={"csrf": self.csrf()})
        self.assertEqual(self.post("/login", {"username": "alice", "password": "nope"}).status_code, 401)

    def test_booking_takes_seats_and_refuses_to_oversell(self):
        self.register()
        r = self.post("/concert/1/book", {"tickets": "3"}, "/concert/1")
        self.assertEqual(r.headers["Location"], "/bookings")
        self.assertEqual(self.rows("SELECT available_tickets FROM Concerts WHERE concert_id = 1")[0][0], 2)
        self.post("/concert/1/book", {"tickets": "3"}, "/concert/1")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM bookings")[0][0], 1)
        self.assertEqual(self.rows("SELECT available_tickets FROM Concerts WHERE concert_id = 1")[0][0], 2)

    def test_booking_rejects_bad_counts_and_past_concerts(self):
        self.register()
        for n in ("0", "-2", "11", "abc"):
            self.post("/concert/1/book", {"tickets": n}, "/concert/1")
        self.post("/concert/2/book", {"tickets": "1"}, "/concert/1")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM bookings")[0][0], 0)

    def test_booking_requires_login(self):
        r = self.post("/concert/1/book", {"tickets": "1"}, "/login")
        self.assertIn("/login", r.headers["Location"])

    def test_cancel_returns_seats_and_only_for_owner(self):
        self.register()
        self.post("/concert/1/book", {"tickets": "2"}, "/concert/1")
        self.client.post("/logout", data={"csrf": self.csrf()})
        self.register("mallory", "another-password")
        self.assertEqual(self.post("/bookings/1/cancel", {}, "/bookings").status_code, 404)
        self.client.post("/logout", data={"csrf": self.csrf()})
        self.post("/login", {"username": "alice", "password": "correct-horse"})
        self.post("/bookings/1/cancel", {}, "/bookings")
        self.assertEqual(self.rows("SELECT available_tickets FROM Concerts WHERE concert_id = 1")[0][0], 5)

    def test_favourites_toggle(self):
        self.register()
        self.post("/favourite", {"artist": "The Velvet Static", "next": "/favourites"}, "/concert/1")
        self.assertIn("Velvet", self.client.get("/favourites").get_data(as_text=True))
        self.post("/favourite", {"artist": "The Velvet Static"}, "/concert/1")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM favourites")[0][0], 0)
        self.assertEqual(self.post("/favourite", {"artist": "Nobody"}, "/concert/1").status_code, 400)

    def test_open_redirect_is_blocked(self):
        self.register()
        r = self.post("/favourite", {"artist": "The Velvet Static", "next": "//evil.example"}, "/concert/1")
        self.assertEqual(r.headers["Location"], "/")

    def test_admin_pages_require_admin(self):
        self.assertIn("/admin/login", self.client.get("/admin").headers["Location"])
        self.register()
        self.assertIn("/admin/login", self.client.get("/admin/concert/new").headers["Location"])

    def test_admin_legacy_login_upgrades_and_manages_concerts(self):
        r = self.post("/admin/login", {"adminid": "1", "password": "legacy-admin-pw"}, "/admin/login")
        self.assertEqual(r.headers["Location"], "/admin")
        self.assertTrue(self.rows("SELECT password FROM admin")[0][0].startswith("pbkdf2_sha256$"))
        form = {"artist_name": "Neon Tide", "venue": "Harbour Arena", "date": FUTURE, "time": "18:00",
                "ticket_price": "999", "total_tickets": "100", "organizer": "Tidewater", "artistid": ""}
        self.post("/admin/concert/new", form, "/admin")
        self.assertEqual(self.rows("SELECT available_tickets FROM Concerts WHERE artist_name = 'Neon Tide'")[0][0], 100)
        self.assertEqual(self.post("/admin/concert/new", dict(form, date="", ticket_price="-1"), "/admin").status_code, 400)

    def test_admin_edit_keeps_sold_seats_and_delete_is_guarded(self):
        self.register()
        self.post("/concert/1/book", {"tickets": "2"}, "/concert/1")
        self.client.post("/logout", data={"csrf": self.csrf()})
        self.post("/admin/login", {"adminid": "1", "password": "legacy-admin-pw"}, "/admin/login")
        form = {"artist_name": "The Velvet Static", "venue": "Echo Hall", "date": FUTURE, "time": "19:30",
                "ticket_price": "1500", "total_tickets": "10", "organizer": "Lowlight Live", "artistid": "1"}
        self.post("/admin/concert/1/edit", form, "/admin")
        self.assertEqual(self.rows("SELECT available_tickets, total_tickets FROM Concerts WHERE concert_id = 1")[0], (8, 10))
        self.assertEqual(self.post("/admin/concert/1/edit", dict(form, total_tickets="1"), "/admin").status_code, 400)
        self.post("/admin/concert/1/delete", {}, "/admin")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM Concerts WHERE concert_id = 1")[0][0], 1)
        self.post("/admin/concert/2/delete", {}, "/admin")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM Concerts WHERE concert_id = 2")[0][0], 0)

    def test_dashboard_reports_sales(self):
        self.register()
        self.post("/concert/1/book", {"tickets": "2"}, "/concert/1")
        self.client.post("/logout", data={"csrf": self.csrf()})
        self.post("/admin/login", {"adminid": "1", "password": "legacy-admin-pw"}, "/admin/login")
        html = self.client.get("/admin").get_data(as_text=True)
        self.assertIn("₹3,000", html)

    def test_unknown_concert_is_404(self):
        self.assertEqual(self.client.get("/concert/999").status_code, 404)


if __name__ == "__main__":
    unittest.main()
