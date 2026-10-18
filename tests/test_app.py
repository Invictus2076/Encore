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
        self.assertEqual(r.headers["Location"], "/bookings/1/ticket")
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

    # ----- e-tickets and check-in -----
    def test_eticket_is_owner_only_and_carries_a_code(self):
        self.register()
        self.post("/concert/1/book", {"tickets": "2"}, "/concert/1")
        code = self.rows("SELECT ticket_code FROM bookings WHERE booking_id = 1")[0][0]
        self.assertRegex(code, r"^[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$")
        html = self.client.get("/bookings/1/ticket").get_data(as_text=True)
        self.assertIn(code, html)
        self.assertIn("/admin/checkin?code=" + code, html)
        self.client.post("/logout", data={"csrf": self.csrf()})
        self.register("mallory", "another-password")
        self.assertEqual(self.client.get("/bookings/1/ticket").status_code, 404)

    def test_checkin_works_once(self):
        self.register()
        self.post("/concert/1/book", {"tickets": "2"}, "/concert/1")
        code = self.rows("SELECT ticket_code FROM bookings WHERE booking_id = 1")[0][0]
        self.client.post("/logout", data={"csrf": self.csrf()})
        self.post("/admin/login", {"adminid": "1", "password": "legacy-admin-pw"}, "/admin/login")
        self.assertIn("The Velvet Static", self.client.get("/admin/checkin?code=" + code.lower()).get_data(as_text=True))
        self.post("/admin/checkin", {"code": code}, "/admin")
        first = self.rows("SELECT checked_in_at FROM bookings WHERE booking_id = 1")[0][0]
        self.assertIsNotNone(first)
        self.post("/admin/checkin", {"code": code}, "/admin")
        self.assertEqual(self.rows("SELECT checked_in_at FROM bookings WHERE booking_id = 1")[0][0], first)
        self.assertIn("Already used", self.client.get("/admin/checkin?code=" + code).get_data(as_text=True))
        self.assertIn("No ticket", self.client.get("/admin/checkin?code=NOPE").get_data(as_text=True))

    def test_checkin_needs_admin(self):
        self.register()
        self.assertIn("/admin/login", self.client.get("/admin/checkin?code=X").headers["Location"])

    # ----- waitlist -----
    def sell_out_concert_1(self):
        self.register("alice")
        self.post("/concert/1/book", {"tickets": "5"}, "/concert/1")
        self.client.post("/logout", data={"csrf": self.csrf()})

    def test_waitlist_gets_seats_when_someone_cancels(self):
        self.sell_out_concert_1()
        self.register("bob", "bob-password")
        self.post("/concert/1/waitlist", {"tickets": "2"}, "/concert/1")
        self.assertIn("#1 on the waitlist", self.client.get("/concert/1").get_data(as_text=True))
        self.client.post("/logout", data={"csrf": self.csrf()})
        self.post("/login", {"username": "alice", "password": "correct-horse"})
        self.post("/bookings/1/cancel", {}, "/bookings")
        self.assertEqual(self.rows("SELECT username, no_of_tickets FROM bookings ORDER BY booking_id"), [("bob", 2)])
        self.assertEqual(self.rows("SELECT available_tickets FROM Concerts WHERE concert_id = 1")[0][0], 3)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM waitlist")[0][0], 0)
        self.client.post("/logout", data={"csrf": self.csrf()})
        self.post("/login", {"username": "bob", "password": "bob-password"})
        self.assertIn('class="badge"', self.client.get("/").get_data(as_text=True))
        self.assertIn("Good news", self.client.get("/bookings").get_data(as_text=True))
        self.assertNotIn('class="badge"', self.client.get("/").get_data(as_text=True))

    def test_waitlist_skips_requests_that_dont_fit(self):
        self.sell_out_concert_1()
        self.register("bob", "bob-password")
        self.post("/concert/1/waitlist", {"tickets": "4"}, "/concert/1")
        self.client.post("/logout", data={"csrf": self.csrf()})
        self.register("cara", "cara-password")
        self.post("/concert/1/waitlist", {"tickets": "1"}, "/concert/1")
        self.client.post("/logout", data={"csrf": self.csrf()})
        self.post("/admin/login", {"adminid": "1", "password": "legacy-admin-pw"}, "/admin/login")
        form = {"artist_name": "The Velvet Static", "venue": "Echo Hall", "date": FUTURE, "time": "19:30",
                "ticket_price": "1500", "total_tickets": "7", "organizer": "Lowlight Live", "artistid": "1"}
        self.post("/admin/concert/1/edit", form, "/admin")
        self.assertEqual(self.rows("SELECT username FROM bookings WHERE username <> 'alice'"), [("cara",)])
        self.assertEqual(self.rows("SELECT username FROM waitlist"), [("bob",)])

    def test_waitlist_only_when_seats_short(self):
        self.register()
        self.post("/concert/1/waitlist", {"tickets": "2"}, "/concert/1")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM waitlist")[0][0], 0)
        self.post("/concert/1/waitlist", {"tickets": "8"}, "/concert/1")
        self.assertEqual(self.rows("SELECT tickets FROM waitlist")[0][0], 8)
        self.post("/concert/1/waitlist/leave", {}, "/concert/1")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM waitlist")[0][0], 0)

    # ----- reviews -----
    def test_reviews_only_after_attending_and_once(self):
        self.register()
        conn = fake.connect(); cur = conn.cursor()
        cur.execute("INSERT INTO bookings (username, concert_id, no_of_tickets, ticket_code) VALUES ('alice', 2, 1, 'AAAA-BBBB-CCCC')")
        conn.commit()
        self.assertEqual(self.post("/concert/1/review", {"rating": "5"}, "/concert/1").status_code, 400)
        self.post("/concert/2/review", {"rating": "9"}, "/concert/2")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM reviews")[0][0], 0)
        self.post("/concert/2/review", {"rating": "4", "comment": "Loud and great <script>"}, "/concert/2")
        self.post("/concert/2/review", {"rating": "1"}, "/concert/2")
        self.assertEqual(self.rows("SELECT rating FROM reviews"), [(4,)])
        html = self.client.get("/concert/2").get_data(as_text=True)
        self.assertIn("Loud and great &lt;script&gt;", html)
        self.assertIn("4.0 from 1 fan review", html)  # the artist's rating line on their concert page

    def test_review_needs_a_booking(self):
        self.register()
        self.assertEqual(self.post("/concert/2/review", {"rating": "5"}, "/concert/2").status_code, 400)

    # ----- recommendations -----
    def test_recommends_what_similar_fans_like(self):
        conn = fake.connect(); cur = conn.cursor()
        cur.execute("INSERT INTO Concerts (artist_name, venue, date, time, ticket_price, available_tickets, total_tickets, organizer)"
                    " VALUES ('Neon Tide', 'Harbour', %s, '18:00:00', 999, 50, 50, 'Tidewater')", (FUTURE,))
        cur.execute("INSERT INTO favourites VALUES ('zed', 'The Velvet Static')")
        cur.execute("INSERT INTO favourites VALUES ('zed', 'Neon Tide')")
        conn.commit()
        self.register()
        self.post("/favourite", {"artist": "The Velvet Static"}, "/concert/1")
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("Recommended for you", html)
        self.assertIn("Fans of The Velvet Static love Neon Tide", html)

    def test_logged_out_sees_selling_fast(self):
        conn = fake.connect(); cur = conn.cursor()
        cur.execute("UPDATE Concerts SET available_tickets = 1 WHERE concert_id = 1")
        conn.commit()
        self.assertIn("Selling fast", self.client.get("/").get_data(as_text=True))

    # ----- dashboard, CLI, migration -----
    def test_dashboard_has_charts(self):
        self.post("/admin/login", {"adminid": "1", "password": "legacy-admin-pw"}, "/admin/login")
        html = self.client.get("/admin").get_data(as_text=True)
        self.assertIn("How full are upcoming shows", html)
        self.assertIn("Tickets booked per day", html)

    def test_create_admin_command(self):
        result = encore.app.test_cli_runner().invoke(args=["create-admin"], input="long-enough-pw\nlong-enough-pw\n")
        self.assertIn("Created admin 2", result.output)
        self.assertTrue(self.rows("SELECT password FROM admin WHERE adminid = 2")[0][0].startswith("pbkdf2_sha256$"))

    def test_schema_upgrade_adds_columns_to_old_bookings(self):
        fake.reset()
        conn = fake.connect(); cur = conn.cursor()
        cur.execute("CREATE TABLE bookings (booking_id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, concert_id INT, no_of_tickets INT, booked_at TEXT)")
        cur.execute("INSERT INTO bookings (username, concert_id, no_of_tickets) VALUES ('old', 1, 2)")
        conn.commit()
        core.ensure_schema(conn)
        code, checked = self.rows("SELECT ticket_code, checked_in_at FROM bookings")[0]
        self.assertRegex(code, r"^[0-9A-F]{4}-")
        self.assertIsNone(checked)

    def test_unknown_concert_is_404(self):
        self.assertEqual(self.client.get("/concert/999").status_code, 404)


if __name__ == "__main__":
    unittest.main()
