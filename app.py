"""Encore on the web: browse concerts, book tickets, follow artists, and run the show as an admin.

Run with:  flask --app app run
Needs ENCORE_DB_PASSWORD (MySQL) and ENCORE_SECRET_KEY (signs the login cookie). See README.md.
"""

import datetime as dt
import os
import secrets
from decimal import Decimal, InvalidOperation
from functools import wraps

import click
import mysql.connector
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for

import encore_core as core

app = Flask(__name__)
_secret = os.environ.get("ENCORE_SECRET_KEY")
if not _secret:
    raise RuntimeError("Set ENCORE_SECRET_KEY to a long random string (see README.md).")
app.config.update(
    SECRET_KEY=_secret,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("ENCORE_HTTPS") == "1",
)

_schema_ready = False
MAX_TICKETS_PER_BOOKING = 10


# ---------- Database ----------

def db():
    global _schema_ready
    if "db" not in g:
        g.db = core.connect()
        if not _schema_ready:
            core.ensure_schema(g.db)
            _schema_ready = True
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def query(sql, params=(), one=False):
    """Run a SELECT and return rows as dicts (or one dict / None)."""
    cursor = db().cursor()
    try:
        cursor.execute(sql, params)
        names = [d[0] for d in cursor.description]
        rows = [dict(zip(names, row)) for row in cursor.fetchall()]
    finally:
        cursor.close()
    return (rows[0] if rows else None) if one else rows


def execute(sql, params=()):
    cursor = db().cursor()
    try:
        cursor.execute(sql, params)
        return cursor.lastrowid
    finally:
        cursor.close()


# ---------- Security: CSRF tokens and access control ----------

def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def check_csrf():
    if request.method == "POST":
        sent = request.form.get("csrf", "")
        if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
            abort(400, "Your form expired. Go back, refresh the page and try again.")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            flash("Log in to continue.", "info")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapped


def safe_next(target):
    """Only follow redirects back into this site."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("index")


# ---------- Formatting ----------

def as_date(value):
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def as_time(value):
    if isinstance(value, dt.timedelta):  # MySQL TIME arrives as a timedelta
        secs = int(value.total_seconds())
        return dt.time(secs // 3600 % 24, secs // 60 % 60)
    if isinstance(value, dt.time):
        return value
    parts = [int(p) for p in str(value).split(":")[:2]]
    return dt.time(*parts)


@app.template_filter("day")
def f_day(value):
    return as_date(value).strftime("%d")


@app.template_filter("month")
def f_month(value):
    return as_date(value).strftime("%b")


@app.template_filter("year")
def f_year(value):
    return as_date(value).strftime("%Y")


@app.template_filter("longdate")
def f_longdate(value):
    return as_date(value).strftime("%A, %d %B %Y")


@app.template_filter("clock")
def f_clock(value):
    return as_time(value).strftime("%I:%M %p").lstrip("0")


@app.template_filter("money")
def f_money(value):
    amount = Decimal(str(value or 0))
    text = f"{amount:,.2f}"
    return "₹" + (text[:-3] if text.endswith(".00") else text)


@app.template_filter("isodate")
def f_isodate(value):
    return as_date(value).isoformat()


@app.template_filter("hhmm")
def f_hhmm(value):
    return as_time(value).strftime("%H:%M")


@app.context_processor
def inject_globals():
    unseen = 0
    if session.get("user"):
        try:
            unseen = query("SELECT COUNT(*) AS n FROM notifications WHERE username = %s AND seen = 0", (session["user"],), one=True)["n"]
        except mysql.connector.Error:
            unseen = 0
    return {"today": dt.date.today(), "unseen": unseen}


# ---------- Waitlist ----------

def notify(username, message):
    execute("INSERT INTO notifications (username, message) VALUES (%s, %s)", (username, message))


def fill_from_waitlist(concert_id):
    """Give freed seats to the waitlist, oldest request first. A request bigger than the seats
    left is skipped (it stays in line) so smaller requests behind it aren't blocked.
    Must run inside the caller's transaction, after the seats were returned."""
    c = query("SELECT artist_name, available_tickets FROM Concerts WHERE concert_id = %s FOR UPDATE", (concert_id,), one=True)
    if c is None:
        return []
    left = c["available_tickets"] or 0
    granted = []
    for w in query("SELECT waitlist_id, username, tickets FROM waitlist WHERE concert_id = %s ORDER BY created_at, waitlist_id", (concert_id,)):
        if left <= 0:
            break
        if w["tickets"] > left:
            continue
        booking_id = execute("INSERT INTO bookings (username, concert_id, no_of_tickets, ticket_code) VALUES (%s, %s, %s, %s)",
                             (w["username"], concert_id, w["tickets"], core.new_ticket_code()))
        execute("UPDATE Concerts SET available_tickets = available_tickets - %s WHERE concert_id = %s", (w["tickets"], concert_id))
        execute("DELETE FROM waitlist WHERE waitlist_id = %s", (w["waitlist_id"],))
        notify(w["username"], f"Good news: seats opened up for {c['artist_name']}. You now have {w['tickets']} "
                              f"ticket{'s' if w['tickets'] != 1 else ''} (booking #{booking_id}).")
        left -= w["tickets"]
        granted.append(w["username"])
    return granted


# ---------- Public pages ----------

SORTS = {"date": "date, time", "price_low": "ticket_price, date", "price_high": "ticket_price DESC, date"}


def recommendations(user, limit=3):
    """Shows to suggest: artists followed or booked by fans who share your taste. With no
    signal yet (or logged out), the upcoming shows selling fastest."""
    today = dt.date.today().isoformat()
    upcoming = query("SELECT * FROM Concerts WHERE date >= %s AND available_tickets > 0 ORDER BY date, time", (today,))
    if user:
        taste = query("SELECT username, artist_name FROM favourites UNION "
                      "SELECT b.username, c.artist_name FROM bookings b JOIN Concerts c ON c.concert_id = b.concert_id")
        by_user = {}
        for r in taste:
            by_user.setdefault(r["username"], set()).add(r["artist_name"])
        mine = by_user.pop(user, set())
        scores, because = {}, {}
        for artists in by_user.values():
            shared = artists & mine
            if not shared:
                continue
            for a in artists - mine:
                scores[a] = scores.get(a, 0) + len(shared)
                because.setdefault(a, sorted(shared)[0])
        picks, seen = [], set()
        for a in sorted(scores, key=lambda a: (-scores[a], a)):
            for c in upcoming:
                if c["artist_name"] == a and a not in seen:
                    picks.append(dict(c, reason=f"Fans of {because[a]} love {a}"))
                    seen.add(a)
                    break
            if len(picks) == limit:
                return picks
        if picks:
            return picks
    hot = sorted(upcoming, key=lambda c: -((c["total_tickets"] or 0) - (c["available_tickets"] or 0)) / max(c["total_tickets"] or 1, 1))
    return [dict(c, reason="Selling fast") for c in hot[:limit] if (c["total_tickets"] or 0) > (c["available_tickets"] or 0)]


@app.get("/")
def index():
    q = request.args.get("q", "").strip()
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    max_price = request.args.get("max", "")
    sort = request.args.get("sort", "date")
    where, params = ["date >= %s"], [dt.date.today().isoformat()]
    if q:
        where.append("(artist_name LIKE %s OR venue LIKE %s)")
        params += [f"%{q}%", f"%{q}%"]
    try:
        if date_from:
            params.append(dt.date.fromisoformat(date_from).isoformat()); where.append("date >= %s")
        if date_to:
            params.append(dt.date.fromisoformat(date_to).isoformat()); where.append("date <= %s")
    except ValueError:
        flash("Dates should look like 2026-12-31.", "error")
    if max_price:
        try:
            params.append(str(Decimal(max_price))); where.append("ticket_price <= %s")
        except InvalidOperation:
            flash("Max price should be a number.", "error")
    order = SORTS.get(sort, SORTS["date"])  # allowlisted, never user text
    concerts = query(f"SELECT * FROM Concerts WHERE {' AND '.join(where)} ORDER BY {order}", params)
    favs = set()
    if session.get("user"):
        favs = {r["artist_name"] for r in query("SELECT artist_name FROM favourites WHERE username = %s", (session["user"],))}
    filtered = bool(q or date_from or date_to or max_price)
    recs = [] if filtered else recommendations(session.get("user"))
    return render_template("index.html", concerts=concerts, favs=favs, filtered=filtered, recs=recs,
                           args={"q": q, "from": date_from, "to": date_to, "max": max_price, "sort": sort})


@app.get("/concert/<int:concert_id>")
def concert(concert_id):
    c = query("SELECT * FROM Concerts WHERE concert_id = %s", (concert_id,), one=True)
    if c is None:
        abort(404)
    is_fav = False
    if session.get("user"):
        is_fav = query("SELECT 1 AS x FROM favourites WHERE username = %s AND artist_name = %s",
                       (session["user"], c["artist_name"]), one=True) is not None
    more = query("SELECT * FROM Concerts WHERE artist_name = %s AND concert_id <> %s AND date >= %s ORDER BY date LIMIT 3",
                 (c["artist_name"], concert_id, dt.date.today().isoformat()))
    past = as_date(c["date"]) < dt.date.today()
    reviews = query("SELECT username, rating, comment, created_at FROM reviews WHERE concert_id = %s ORDER BY created_at DESC",
                    (concert_id,)) if past else []
    artist_rating = query("SELECT AVG(r.rating) AS avg, COUNT(*) AS n FROM reviews r JOIN Concerts c ON c.concert_id = r.concert_id"
                          " WHERE c.artist_name = %s", (c["artist_name"],), one=True)
    wait = can_review = None
    if session.get("user"):
        wait = query("SELECT tickets, created_at, waitlist_id FROM waitlist WHERE username = %s AND concert_id = %s",
                     (session["user"], concert_id), one=True)
        if wait:
            wait["position"] = query("SELECT COUNT(*) AS n FROM waitlist WHERE concert_id = %s AND (created_at < %s OR"
                                     " (created_at = %s AND waitlist_id <= %s))",
                                     (concert_id, wait["created_at"], wait["created_at"], wait["waitlist_id"]), one=True)["n"]
        if past:
            booked = query("SELECT 1 AS x FROM bookings WHERE username = %s AND concert_id = %s LIMIT 1", (session["user"], concert_id), one=True)
            done = query("SELECT 1 AS x FROM reviews WHERE username = %s AND concert_id = %s", (session["user"], concert_id), one=True)
            can_review = bool(booked) and not done
    return render_template("concert.html", c=c, is_fav=is_fav, more=more, max_per=MAX_TICKETS_PER_BOOKING, past=past,
                           reviews=reviews, artist_rating=artist_rating, wait=wait, can_review=can_review)


@app.post("/concert/<int:concert_id>/waitlist")
@login_required
def join_waitlist(concert_id):
    try:
        n = int(request.form.get("tickets", ""))
    except ValueError:
        n = 0
    c = query("SELECT available_tickets, date FROM Concerts WHERE concert_id = %s", (concert_id,), one=True)
    if c is None:
        abort(404)
    if not 1 <= n <= MAX_TICKETS_PER_BOOKING or as_date(c["date"]) < dt.date.today():
        flash(f"Choose between 1 and {MAX_TICKETS_PER_BOOKING} tickets.", "error")
    elif n <= (c["available_tickets"] or 0):
        flash("Those seats are available right now, so just book them.", "info")
    else:
        if query("SELECT 1 AS x FROM waitlist WHERE username = %s AND concert_id = %s", (session["user"], concert_id), one=True):
            execute("UPDATE waitlist SET tickets = %s WHERE username = %s AND concert_id = %s", (n, session["user"], concert_id))
        else:
            execute("INSERT INTO waitlist (username, concert_id, tickets) VALUES (%s, %s, %s)", (session["user"], concert_id, n))
        db().commit()
        flash("You're on the waitlist. If seats free up, we'll book them for you automatically.", "success")
    return redirect(url_for("concert", concert_id=concert_id))


@app.post("/concert/<int:concert_id>/waitlist/leave")
@login_required
def leave_waitlist(concert_id):
    execute("DELETE FROM waitlist WHERE username = %s AND concert_id = %s", (session["user"], concert_id))
    db().commit()
    flash("You've left the waitlist.", "info")
    return redirect(safe_next(request.form.get("next")) if request.form.get("next") else url_for("concert", concert_id=concert_id))


@app.post("/concert/<int:concert_id>/review")
@login_required
def review(concert_id):
    c = query("SELECT date FROM Concerts WHERE concert_id = %s", (concert_id,), one=True)
    if c is None:
        abort(404)
    booked = query("SELECT 1 AS x FROM bookings WHERE username = %s AND concert_id = %s LIMIT 1", (session["user"], concert_id), one=True)
    if as_date(c["date"]) >= dt.date.today() or not booked:
        abort(400, "You can review a concert after you've been to it.")
    try:
        rating = int(request.form.get("rating", ""))
    except ValueError:
        rating = 0
    comment = request.form.get("comment", "").strip()[:500]
    if not 1 <= rating <= 5:
        flash("Pick a rating from 1 to 5 stars.", "error")
        return redirect(url_for("concert", concert_id=concert_id))
    if query("SELECT 1 AS x FROM reviews WHERE username = %s AND concert_id = %s", (session["user"], concert_id), one=True):
        flash("You've already reviewed this show.", "info")
    else:
        execute("INSERT INTO reviews (username, concert_id, rating, comment) VALUES (%s, %s, %s, %s)",
                (session["user"], concert_id, rating, comment or None))
        db().commit()
        flash("Thanks for the review!", "success")
    return redirect(url_for("concert", concert_id=concert_id))


@app.get("/bookings/<int:booking_id>/ticket")
@login_required
def ticket(booking_id):
    t = query("SELECT b.*, c.artist_name, c.venue, c.date, c.time, c.organizer FROM bookings b"
              " JOIN Concerts c ON c.concert_id = b.concert_id WHERE b.booking_id = %s AND b.username = %s",
              (booking_id, session["user"]), one=True)
    if t is None:
        abort(404)
    checkin_url = url_for("admin_checkin", code=t["ticket_code"], _external=True)
    return render_template("ticket.html", t=t, checkin_url=checkin_url)


@app.post("/concert/<int:concert_id>/book")
@login_required
def book(concert_id):
    try:
        n = int(request.form.get("tickets", ""))
    except ValueError:
        n = 0
    if not 1 <= n <= MAX_TICKETS_PER_BOOKING:
        flash(f"Choose between 1 and {MAX_TICKETS_PER_BOOKING} tickets.", "error")
        return redirect(url_for("concert", concert_id=concert_id))
    conn = db()
    try:
        conn.start_transaction()
        row = query("SELECT available_tickets, date FROM Concerts WHERE concert_id = %s FOR UPDATE", (concert_id,), one=True)
        if row is None:
            conn.rollback(); abort(404)
        if as_date(row["date"]) < dt.date.today():
            conn.rollback(); flash("This concert has already happened.", "error")
            return redirect(url_for("concert", concert_id=concert_id))
        left = row["available_tickets"] or 0
        if n > left:
            conn.rollback()
            flash(f"Only {left} ticket{'s' if left != 1 else ''} left." if left else "Sold out, sorry.", "error")
            return redirect(url_for("concert", concert_id=concert_id))
        booking_id = execute("INSERT INTO bookings (username, concert_id, no_of_tickets, ticket_code) VALUES (%s, %s, %s, %s)",
                             (session["user"], concert_id, n, core.new_ticket_code()))
        execute("DELETE FROM waitlist WHERE username = %s AND concert_id = %s", (session["user"], concert_id))
        execute("UPDATE Concerts SET available_tickets = available_tickets - %s WHERE concert_id = %s", (n, concert_id))
        conn.commit()
    except mysql.connector.Error:
        conn.rollback()
        flash("Booking failed. Nothing was charged; please try again.", "error")
        return redirect(url_for("concert", concert_id=concert_id))
    flash(f"You're going! Booking #{booking_id}, {n} ticket{'s' if n != 1 else ''}. Your e-ticket is ready.", "success")
    return redirect(url_for("ticket", booking_id=booking_id))


@app.post("/favourite")
@login_required
def toggle_favourite():
    artist = request.form.get("artist", "").strip()
    exists = query("SELECT 1 AS x FROM Concerts WHERE artist_name = %s LIMIT 1", (artist,), one=True)
    if not artist or exists is None:
        abort(400, "Unknown artist.")
    if query("SELECT 1 AS x FROM favourites WHERE username = %s AND artist_name = %s", (session["user"], artist), one=True):
        execute("DELETE FROM favourites WHERE username = %s AND artist_name = %s", (session["user"], artist))
        flash(f"Unfollowed {artist}.", "info")
    else:
        execute("INSERT INTO favourites VALUES (%s, %s)", (session["user"], artist))
        flash(f"Following {artist}. Their shows appear in Favourites.", "success")
    db().commit()
    return redirect(safe_next(request.form.get("next")))


# ---------- Accounts ----------

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("username", "").strip()
        pw = request.form.get("password", "")
        error = None
        if not 3 <= len(name) <= 40:
            error = "Usernames need 3 to 40 characters."
        elif len(pw) < 8:
            error = "Use a password of at least 8 characters."
        elif query("SELECT 1 AS x FROM user WHERE username = %s", (name,), one=True):
            error = "That username is taken."
        if error:
            flash(error, "error")
            return render_template("auth.html", mode="register", username=name), 400
        execute("INSERT INTO user VALUES (%s, %s)", (name, core.hash_password(pw)))
        db().commit()
        session.clear()
        session["user"] = name
        flash(f"Welcome to Encore, {name}.", "success")
        return redirect(safe_next(request.args.get("next")))
    return render_template("auth.html", mode="register", username="")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        name = request.form.get("username", "").strip()
        pw = request.form.get("password", "")
        row = query("SELECT password FROM user WHERE username = %s", (name,), one=True)
        ok, upgrade = core.verify_password(pw, row["password"] if row else None)
        if not ok:
            flash("Wrong username or password.", "error")
            return render_template("auth.html", mode="login", username=name), 401
        if upgrade:
            execute("UPDATE user SET password = %s WHERE username = %s", (core.hash_password(pw), name))
            db().commit()
        session.clear()
        session["user"] = name
        flash(f"Welcome back, {name}.", "success")
        return redirect(safe_next(request.args.get("next")))
    return render_template("auth.html", mode="login", username="")


@app.post("/logout")
def logout():
    session.clear()
    flash("You're logged out.", "info")
    return redirect(url_for("index"))


@app.get("/bookings")
@login_required
def bookings():
    rows = query(
        "SELECT b.booking_id, b.no_of_tickets, b.booked_at, b.ticket_code, b.checked_in_at, c.concert_id, c.artist_name, c.venue, c.date, c.time, c.ticket_price"
        " FROM bookings b JOIN Concerts c ON c.concert_id = b.concert_id WHERE b.username = %s ORDER BY c.date, c.time",
        (session["user"],))
    today = dt.date.today()
    upcoming = [r for r in rows if as_date(r["date"]) >= today]
    past = [r for r in rows if as_date(r["date"]) < today]
    reviewed = {r["concert_id"] for r in query("SELECT concert_id FROM reviews WHERE username = %s", (session["user"],))}
    waiting = query("SELECT w.tickets, c.concert_id, c.artist_name, c.venue, c.date, c.time FROM waitlist w"
                    " JOIN Concerts c ON c.concert_id = w.concert_id WHERE w.username = %s AND c.date >= %s ORDER BY c.date",
                    (session["user"], today.isoformat()))
    notes = query("SELECT message, created_at, seen FROM notifications WHERE username = %s ORDER BY notification_id DESC LIMIT 10",
                  (session["user"],))
    execute("UPDATE notifications SET seen = 1 WHERE username = %s AND seen = 0", (session["user"],))
    db().commit()
    return render_template("bookings.html", upcoming=upcoming, past=past, reviewed=reviewed, waiting=waiting, notes=notes)


@app.post("/bookings/<int:booking_id>/cancel")
@login_required
def cancel_booking(booking_id):
    conn = db()
    try:
        conn.start_transaction()
        b = query("SELECT b.concert_id, b.no_of_tickets, c.date FROM bookings b JOIN Concerts c ON c.concert_id = b.concert_id"
                  " WHERE b.booking_id = %s AND b.username = %s FOR UPDATE", (booking_id, session["user"]), one=True)
        if b is None:
            conn.rollback(); abort(404)
        if as_date(b["date"]) < dt.date.today():
            conn.rollback(); flash("Past bookings can't be cancelled.", "error")
            return redirect(url_for("bookings"))
        execute("DELETE FROM bookings WHERE booking_id = %s", (booking_id,))
        execute("UPDATE Concerts SET available_tickets = available_tickets + %s WHERE concert_id = %s",
                (b["no_of_tickets"], b["concert_id"]))
        fill_from_waitlist(b["concert_id"])
        conn.commit()
    except mysql.connector.Error:
        conn.rollback()
        flash("Couldn't cancel that booking. Please try again.", "error")
        return redirect(url_for("bookings"))
    flash(f"Booking #{booking_id} cancelled. The seats are back on sale.", "info")
    return redirect(url_for("bookings"))


@app.get("/favourites")
@login_required
def favourites():
    artists = [r["artist_name"] for r in query("SELECT artist_name FROM favourites WHERE username = %s ORDER BY artist_name",
                                               (session["user"],))]
    shows = []
    if artists:
        marks = ", ".join(["%s"] * len(artists))
        shows = query(f"SELECT * FROM Concerts WHERE artist_name IN ({marks}) AND date >= %s ORDER BY date, time",
                      (*artists, dt.date.today().isoformat()))
    return render_template("favourites.html", artists=artists, shows=shows)


# ---------- Admin ----------

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        try:
            admin_id = int(request.form.get("adminid", ""))
        except ValueError:
            admin_id = -1
        pw = request.form.get("password", "")
        row = query("SELECT password FROM admin WHERE adminid = %s", (admin_id,), one=True)
        ok, upgrade = core.verify_password(pw, row["password"] if row else None)
        if not ok:
            flash("Wrong admin ID or password.", "error")
            return render_template("admin_login.html"), 401
        if upgrade:
            execute("UPDATE admin SET password = %s WHERE adminid = %s", (core.hash_password(pw), admin_id))
            db().commit()
        session.clear()
        session["admin"] = admin_id
        return redirect(url_for("admin"))
    return render_template("admin_login.html")


@app.get("/admin")
@admin_required
def admin():
    concerts = query("SELECT c.*, COALESCE(c.total_tickets - c.available_tickets, 0) AS sold FROM Concerts c ORDER BY date, time")
    for c in concerts:
        c["revenue"] = Decimal(str(c["ticket_price"] or 0)) * int(c["sold"] or 0)
    today = dt.date.today()
    stats = {
        "upcoming": sum(1 for c in concerts if as_date(c["date"]) >= today),
        "sold": sum(int(c["sold"] or 0) for c in concerts),
        "revenue": sum((c["revenue"] for c in concerts), Decimal(0)),
        "users": query("SELECT COUNT(*) AS n FROM user", one=True)["n"],
    }
    fill = [{"label": c["artist_name"], "sub": f_day(c["date"]) + " " + f_month(c["date"]),
             "pct": round(100 * int(c["sold"] or 0) / c["total_tickets"]) if c["total_tickets"] else 0,
             "sold": int(c["sold"] or 0), "total": c["total_tickets"] or 0}
            for c in concerts if as_date(c["date"]) >= today][:8]
    start = today - dt.timedelta(days=29)
    per_day = {start + dt.timedelta(days=i): 0 for i in range(30)}
    for b in query("SELECT booked_at, no_of_tickets FROM bookings"):
        if b["booked_at"] is not None:
            day = as_date(b["booked_at"])
            if day in per_day:
                per_day[day] += b["no_of_tickets"]
    daily = [{"day": d, "n": n} for d, n in per_day.items()]
    stats["checked_in"] = query("SELECT COUNT(*) AS n FROM bookings WHERE checked_in_at IS NOT NULL", one=True)["n"]
    return render_template("admin.html", concerts=concerts, stats=stats, fill=fill, daily=daily,
                           daily_max=max([d["n"] for d in daily] + [1]))


@app.route("/admin/checkin", methods=["GET", "POST"])
@admin_required
def admin_checkin():
    code = (request.values.get("code") or "").strip().upper()
    t = None
    if code:
        t = query("SELECT b.*, c.artist_name, c.venue, c.date, c.time FROM bookings b JOIN Concerts c ON c.concert_id = b.concert_id"
                  " WHERE b.ticket_code = %s", (code,), one=True)
    if request.method == "POST" and t is not None:
        if t["checked_in_at"]:
            flash("This ticket was already used.", "error")
        else:
            execute("UPDATE bookings SET checked_in_at = %s WHERE booking_id = %s AND checked_in_at IS NULL",
                    (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), t["booking_id"]))
            db().commit()
            flash(f"Checked in: {t['no_of_tickets']} for {t['artist_name']}.", "success")
        return redirect(url_for("admin_checkin", code=code))
    return render_template("checkin.html", code=code, t=t)


@app.cli.command("create-admin")
def create_admin():
    """Create an admin account and print its ID."""
    pw = click.prompt("Admin password", hide_input=True, confirmation_prompt=True)
    if len(pw) < 8:
        raise click.ClickException("Use at least 8 characters.")
    with app.app_context():
        admin_id = execute("INSERT INTO admin (password) VALUES (%s)", (core.hash_password(pw),))
        db().commit()
    click.echo(f"Created admin {admin_id}. Log in at /admin/login with that ID.")


def read_concert_form(form, min_total=1):
    values = {k: form.get(k, "").strip() for k in ("artist_name", "venue", "date", "time", "ticket_price", "total_tickets", "organizer", "artistid")}
    errors = {}
    for k, label in (("artist_name", "Artist"), ("venue", "Venue"), ("organizer", "Organizer")):
        if not values[k]:
            errors[k] = f"{label} is required."
        elif len(values[k]) > 255:
            errors[k] = f"{label} is too long."
    try:
        values["date_v"] = dt.date.fromisoformat(values["date"])
    except ValueError:
        errors["date"] = "Pick a date."
    try:
        values["time_v"] = as_time(values["time"])
    except (ValueError, TypeError):
        errors["time"] = "Pick a start time."
    try:
        price = Decimal(values["ticket_price"])
        if price < 0:
            raise InvalidOperation
        values["price_v"] = price.quantize(Decimal("0.01"))
    except InvalidOperation:
        errors["ticket_price"] = "Enter a price of 0 or more."
    try:
        total = int(values["total_tickets"])
        if total < min_total:
            raise ValueError
        values["total_v"] = total
    except ValueError:
        errors["total_tickets"] = f"Enter at least {min_total}."
    try:
        values["artistid_v"] = int(values["artistid"]) if values["artistid"] else None
    except ValueError:
        errors["artistid"] = "Artist ID must be a number."
    return values, errors


@app.route("/admin/concert/new", methods=["GET", "POST"])
@admin_required
def admin_new():
    values, errors = {}, {}
    if request.method == "POST":
        values, errors = read_concert_form(request.form)
        if not errors:
            execute("INSERT INTO Concerts (artist_name, venue, date, time, ticket_price, available_tickets, total_tickets, organizer, artistid)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (values["artist_name"], values["venue"], values["date_v"].isoformat(), values["time_v"].strftime("%H:%M:%S"),
                     str(values["price_v"]), values["total_v"], values["total_v"], values["organizer"], values["artistid_v"]))
            db().commit()
            flash(f"Added {values['artist_name']} at {values['venue']}.", "success")
            return redirect(url_for("admin"))
    return render_template("admin_form.html", values=values, errors=errors, editing=None, sold=0), (400 if errors else 200)


@app.route("/admin/concert/<int:concert_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit(concert_id):
    c = query("SELECT * FROM Concerts WHERE concert_id = %s", (concert_id,), one=True)
    if c is None:
        abort(404)
    sold = max(0, (c["total_tickets"] or 0) - (c["available_tickets"] or 0))
    if request.method == "POST":
        values, errors = read_concert_form(request.form, min_total=max(sold, 1))
        if not errors:
            execute("UPDATE Concerts SET artist_name = %s, venue = %s, date = %s, time = %s, ticket_price = %s,"
                    " total_tickets = %s, available_tickets = %s, organizer = %s, artistid = %s WHERE concert_id = %s",
                    (values["artist_name"], values["venue"], values["date_v"].isoformat(), values["time_v"].strftime("%H:%M:%S"),
                     str(values["price_v"]), values["total_v"], values["total_v"] - sold, values["organizer"],
                     values["artistid_v"], concert_id))
            granted = fill_from_waitlist(concert_id)
            db().commit()
            flash("Concert updated." + (f" {len(granted)} waitlisted fan(s) got tickets." if granted else ""), "success")
            return redirect(url_for("admin"))
        return render_template("admin_form.html", values=values, errors=errors, editing=c, sold=sold), 400
    values = {"artist_name": c["artist_name"], "venue": c["venue"], "date": f_isodate(c["date"]), "time": f_hhmm(c["time"]),
              "ticket_price": c["ticket_price"], "total_tickets": c["total_tickets"], "organizer": c["organizer"],
              "artistid": c["artistid"] if c["artistid"] is not None else ""}
    return render_template("admin_form.html", values=values, errors={}, editing=c, sold=sold)


@app.post("/admin/concert/<int:concert_id>/delete")
@admin_required
def admin_delete(concert_id):
    c = query("SELECT artist_name FROM Concerts WHERE concert_id = %s", (concert_id,), one=True)
    if c is None:
        abort(404)
    booked = query("SELECT COUNT(*) AS n FROM bookings WHERE concert_id = %s", (concert_id,), one=True)["n"]
    if booked:
        flash(f"{c['artist_name']} has {booked} booking(s), so it can't be deleted. Edit it instead.", "error")
        return redirect(url_for("admin"))
    execute("DELETE FROM Concerts WHERE concert_id = %s", (concert_id,))
    db().commit()
    flash(f"Deleted {c['artist_name']}.", "info")
    return redirect(url_for("admin"))


@app.post("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("index"))


# ---------- Errors ----------

@app.errorhandler(400)
@app.errorhandler(404)
def http_error(err):
    return render_template("error.html", code=err.code, message=err.description if err.code == 400 else
                           "That page doesn't exist, or the concert was removed."), err.code
