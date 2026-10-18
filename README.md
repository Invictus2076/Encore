# Encore
High School computer science project for concert management through MySQL.

## Running it

Requires Python 3.8+, a running MySQL server, and the MySQL connector:

```bash
pip install mysql-connector-python
```

Encore reads the database password from an environment variable, so it is never stored in the code:

```bash
# macOS / Linux
export ENCORE_DB_PASSWORD='your-mysql-password'
# Windows (PowerShell)
$env:ENCORE_DB_PASSWORD = 'your-mysql-password'

python "encore 11.0 Release .py"
```

Optional: `ENCORE_DB_HOST` (default `localhost`), `ENCORE_DB_USER` (default `root`) and `ENCORE_DB_NAME` (default `concertmanagement`). The database must exist; Encore creates its tables on first run.

Passwords are stored as salted PBKDF2 hashes. Accounts created by older versions (plain-text passwords) still log in, and are upgraded to a hash on their next successful login.
