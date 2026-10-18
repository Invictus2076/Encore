# Encore
High School computer science project for concert management through MySQL, now with a website.

- **Website** (`app.py`, Flask): browse and search upcoming concerts, book tickets, cancel bookings, follow artists, and an admin dashboard to add, edit and delete concerts with sales and revenue.
- **Console app** (`encore 11.0 Release .py`): the original menu-driven version. Both use the same MySQL database and the same accounts.

## Setup

Requires Python 3.9+ and a running MySQL server with an empty database (default name `concertmanagement`). Encore creates its tables on first run.

```bash
pip install -r requirements.txt
```

Settings come from environment variables, never from the code:

| Variable | Needed by | Meaning |
|---|---|---|
| `ENCORE_DB_PASSWORD` | both | MySQL password (required) |
| `ENCORE_DB_HOST` / `ENCORE_DB_USER` / `ENCORE_DB_NAME` | both | Optional; default `localhost` / `root` / `concertmanagement` |
| `ENCORE_SECRET_KEY` | website | Long random string that signs the login cookie (required) |
| `ENCORE_HTTPS` | website | Set to `1` when served over HTTPS, so cookies are marked secure |

Generate a secret key with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.

```bash
# macOS / Linux
export ENCORE_DB_PASSWORD='your-mysql-password'
export ENCORE_SECRET_KEY='paste-the-generated-key'
# Windows (PowerShell)
$env:ENCORE_DB_PASSWORD = 'your-mysql-password'
$env:ENCORE_SECRET_KEY = 'paste-the-generated-key'
```

## Running

```bash
flask --app app run                 # website on http://127.0.0.1:5000
python "encore 11.0 Release .py"    # console app
```

Admin accounts live in the `admin` table. To create one, insert a row in MySQL and log in with its `adminid`. A plain-text password is accepted once and replaced with a hash on first login:

```sql
INSERT INTO admin (password) VALUES ('choose-a-strong-password');
```

`flask run` is a development server. To put the site online, run it behind a production server such as gunicorn or waitress, over HTTPS, with `ENCORE_HTTPS=1`.

## Security

- Passwords are stored as salted PBKDF2 hashes. Accounts from older versions (plain text) still log in and are upgraded on their next login.
- Every query is parameterised, and sort options are an allowlist.
- Forms carry CSRF tokens; the session cookie is HttpOnly and SameSite=Lax.
- Bookings and cancellations run in transactions that lock the concert's row, so seats can't be oversold. Up to 10 tickets per booking.
- Concerts with bookings can't be deleted, and an edit can't cut the total below the tickets already sold.

## Tests

```bash
python -m unittest discover -s tests -v
```

The 18 tests drive the website end to end against an in-memory SQLite stand-in for MySQL (`tests/fakes/`), so they need no database server.
