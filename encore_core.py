"""Shared pieces of Encore: the database connection, the schema and password hashing.

Used by the website (app.py). The console app keeps its own copy of the same logic, and
both store passwords in the same format, so one account works in both.
"""

import hashlib
import hmac
import os
import secrets

import mysql.connector

PBKDF2_ITERATIONS = 200_000

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS Concerts (
        concert_id INT AUTO_INCREMENT PRIMARY KEY,
        artist_name VARCHAR(255),
        venue VARCHAR(255),
        date DATE,
        time TIME,
        ticket_price DECIMAL(10,2),
        available_tickets INT,
        total_tickets INT,
        organizer VARCHAR(255),
        artistid INT
    )
    """,
    "CREATE TABLE IF NOT EXISTS user (username VARCHAR(255) PRIMARY KEY, password VARCHAR(255))",
    "CREATE TABLE IF NOT EXISTS admin (adminid INT AUTO_INCREMENT PRIMARY KEY, password VARCHAR(255))",
    "CREATE TABLE IF NOT EXISTS favourites (username VARCHAR(255), artist_name VARCHAR(255), PRIMARY KEY (username, artist_name))",
    "CREATE TABLE IF NOT EXISTS artists (artist_name VARCHAR(255) PRIMARY KEY, artistid INT, Concerts_held INT)",
    """
    CREATE TABLE IF NOT EXISTS bookings (
        booking_id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(255) NOT NULL,
        concert_id INT NOT NULL,
        no_of_tickets INT NOT NULL,
        booked_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # Concerts added by old versions never had their available tickets set.
    "UPDATE Concerts SET available_tickets = total_tickets WHERE available_tickets IS NULL",
]


def connect():
    """Open a MySQL connection using the ENCORE_DB_* environment variables."""
    password = os.environ.get("ENCORE_DB_PASSWORD")
    if not password:
        raise RuntimeError("Set the ENCORE_DB_PASSWORD environment variable to your MySQL password.")
    return mysql.connector.connect(
        host=os.environ.get("ENCORE_DB_HOST", "localhost"),
        user=os.environ.get("ENCORE_DB_USER", "root"),
        password=password,
        database=os.environ.get("ENCORE_DB_NAME", "concertmanagement"),
    )


def ensure_schema(conn):
    cursor = conn.cursor()
    try:
        for statement in SCHEMA:
            cursor.execute(statement)
        conn.commit()
    finally:
        cursor.close()


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS).hex()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password, stored):
    """Return (matches, needs_upgrade). Plain-text passwords from old versions still match
    and are flagged so the caller can replace them with a hash."""
    if stored is None:
        return False, False
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, iterations, salt, digest = stored.split("$")
            check = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations)).hex()
        except ValueError:
            return False, False
        return hmac.compare_digest(check, digest), False
    return hmac.compare_digest(password, stored), True
