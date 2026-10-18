# Encore: a console concert manager on MySQL.
#
# Database credentials are read from environment variables, never stored here:
#   ENCORE_DB_PASSWORD  (required)
#   ENCORE_DB_HOST      (default: localhost)
#   ENCORE_DB_USER      (default: root)
#   ENCORE_DB_NAME      (default: concertmanagement)

import hashlib
import hmac
import os
import secrets
import sys
from datetime import datetime

import mysql.connector


# ---------- Connection ----------

def connect():
    password = os.environ.get("ENCORE_DB_PASSWORD")
    if not password:
        sys.exit("Set the ENCORE_DB_PASSWORD environment variable to your MySQL password, then run Encore again.")
    return mysql.connector.connect(
        host=os.environ.get("ENCORE_DB_HOST", "localhost"),
        user=os.environ.get("ENCORE_DB_USER", "root"),
        password=password,
        database=os.environ.get("ENCORE_DB_NAME", "concertmanagement"),
    )


# ---------- Session state ----------

Adminauth = 0
logged_in = 0
username = None


# ---------- Input helpers (a typo re-asks instead of crashing the app) ----------

def read_int(prompt, minimum=None, maximum=None):
    while True:
        raw = input(prompt).strip()
        try:
            value = int(raw)
        except ValueError:
            print("Please enter a whole number.")
            continue
        if minimum is not None and value < minimum:
            print(f"Please enter a number of at least {minimum}.")
            continue
        if maximum is not None and value > maximum:
            print(f"Please enter a number no greater than {maximum}.")
            continue
        return value


def read_price(prompt):
    while True:
        raw = input(prompt).strip()
        try:
            value = float(raw)
        except ValueError:
            print("Please enter a price, for example 1499.00")
            continue
        if value < 0:
            print("The price can't be negative.")
            continue
        return round(value, 2)


def read_text(prompt):
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("This can't be empty.")


def read_date(prompt):
    while True:
        raw = input(prompt).strip()
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            print("Please use the format YYYY-MM-DD, for example 2026-12-31.")


def read_time(prompt):
    while True:
        raw = input(prompt).strip()
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                return datetime.strptime(raw, fmt).time()
            except ValueError:
                pass
        print("Please use the format HH:MM or HH:MM:SS, for example 19:30.")


def read_yes_no(prompt):
    while True:
        answer = input(prompt).strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("Please answer y or n.")


# ---------- Passwords (salted PBKDF2 hashes, never plain text) ----------

PBKDF2_ITERATIONS = 200_000


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS).hex()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password, stored):
    """Check a password against a stored hash. Returns (matches, needs_upgrade).

    Accounts created before hashing still hold plain text; those are compared directly
    and flagged so the caller can replace them with a hash on a successful login."""
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


# ---------- Schema ----------

def create_table(cursor):
    cursor.execute("""
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
    """)
    cursor.execute("CREATE TABLE IF NOT EXISTS user (username VARCHAR(255) PRIMARY KEY, password VARCHAR(255))")
    cursor.execute("CREATE TABLE IF NOT EXISTS admin (adminid INT AUTO_INCREMENT PRIMARY KEY, password VARCHAR(255))")
    cursor.execute("CREATE TABLE IF NOT EXISTS favourites (username VARCHAR(255), artist_name VARCHAR(255), PRIMARY KEY (username, artist_name))")
    cursor.execute("CREATE TABLE IF NOT EXISTS artists (artist_name VARCHAR(255) PRIMARY KEY, artistid INT, Concerts_held INT)")
    # Booking previously wrote to a table that was never created.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            booking_id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(255) NOT NULL,
            concert_id INT NOT NULL,
            no_of_tickets INT NOT NULL,
            booked_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Concerts added before this fix never had their available tickets set.
    cursor.execute("UPDATE Concerts SET available_tickets = total_tickets WHERE available_tickets IS NULL")


# ---------- Accounts ----------

def login(db, cursor):
    global logged_in, username
    name = read_text("Enter your username: ")
    pw = input("Enter your password: ")
    cursor.execute("SELECT password FROM user WHERE username = %s", (name,))
    row = cursor.fetchone()
    ok, upgrade = verify_password(pw, row[0] if row else None)
    if not ok:
        print("Error: Invalid username or password.")
        return
    if upgrade:
        cursor.execute("UPDATE user SET password = %s WHERE username = %s", (hash_password(pw), name))
        db.commit()
    username = name
    logged_in = 1
    print("Login successful. Welcome,", username)


def registration(db, cursor):
    print("Welcome to Encore registration!")
    name = read_text("Enter username: ")
    cursor.execute("SELECT 1 FROM user WHERE username = %s", (name,))
    if cursor.fetchone():
        print("Error: username taken.")
        return
    pw = read_text("Enter password: ")
    cursor.execute("INSERT INTO user VALUES (%s, %s)", (name, hash_password(pw)))
    db.commit()
    print("Signup successful. Please log in to continue.")
    login(db, cursor)


def admin_login(db, cursor):
    global Adminauth
    adminid = read_int("Enter Admin ID: ", minimum=1)
    pw = input("Enter password: ")
    cursor.execute("SELECT password FROM admin WHERE adminid = %s", (adminid,))
    row = cursor.fetchone()
    ok, upgrade = verify_password(pw, row[0] if row else None)
    if not ok:
        print("Error: Invalid Admin ID or password")
        return
    if upgrade:
        cursor.execute("UPDATE admin SET password = %s WHERE adminid = %s", (hash_password(pw), adminid))
        db.commit()
    Adminauth = 1
    print("Login successful. Welcome, Admin.")


# ---------- Menus ----------

def user_menu(db, cursor):
    print("Press 1 to search for a concert")
    print("Press 2 to display all concerts")
    print("Press 3 to view favourites")
    print("Press 4 to book tickets for a concert")
    choice = read_int("Enter your choice (1-4): ", 1, 4)
    if choice == 1:
        search_concert(cursor)
    elif choice == 2:
        display_concerts(cursor)
    elif choice == 3:
        Favourites(db, cursor)
    elif choice == 4:
        booking(db, cursor)


def Admin_menu(db, cursor):
    print("1. Add Concert")
    print("2. Update Concert")
    print("3. Display All Concerts")
    print("4. Delete Concert")
    print("5. Exit")
    choice = read_int("Enter your choice (1-5): ", 1, 5)
    if choice == 1:
        add_concert(db, cursor)
    elif choice == 2:
        update_concert(db, cursor)
    elif choice == 3:
        display_concerts(cursor)
    elif choice == 4:
        delete_concert(db, cursor)
    else:
        print("Logging out...")
        return False
    return True


# ---------- Concerts ----------

def print_concert(concert):
    print("Concert ID:", concert[0])
    print("Artist/Band Name:", concert[1])
    print("Venue:", concert[2])
    print("Date:", concert[3])
    print("Time:", concert[4])
    print("Ticket Price:", concert[5])
    print("Available Tickets:", concert[6])
    print("Total Tickets:", concert[7])
    print("Organizer:", concert[8])
    print("Artist ID is: ", concert[9])
    print()


def display_concerts(cursor):
    cursor.execute("SELECT * FROM Concerts ORDER BY date, time")
    concerts = cursor.fetchall()
    if not concerts:
        print("No concerts found!")
        return
    print("Concerts List:")
    for concert in concerts:
        print_concert(concert)


def search_concert(cursor):
    artist_name = read_text("Enter artist/band name to search: ")
    # A parameter, not text pasted into the SQL, so a search can't change the query.
    cursor.execute("SELECT * FROM Concerts WHERE artist_name LIKE %s ORDER BY date, time", (f"%{artist_name}%",))
    concerts = cursor.fetchall()
    if not concerts:
        print("No concerts found!")
        return
    print("\nSearch results:")
    for concert in concerts:
        print_concert(concert)


def booking(db, cursor):
    if not logged_in:
        print("Please log in to book tickets.")
        return
    concid = read_int("Enter id of concert for which you would like to book: ", minimum=1)
    no = read_int("Enter number of tickets you would like to book: ", minimum=1)
    try:
        # Lock the concert's row so two bookings can't sell the same seats.
        db.start_transaction()
        cursor.execute("SELECT available_tickets FROM Concerts WHERE concert_id = %s FOR UPDATE", (concid,))
        row = cursor.fetchone()
        if row is None:
            db.rollback()
            print("Concert not found!")
            return
        available = row[0] or 0
        if no > available:
            db.rollback()
            print(f"Sorry, only {available} ticket(s) left for this concert.")
            return
        cursor.execute("INSERT INTO bookings (username, concert_id, no_of_tickets) VALUES (%s, %s, %s)", (username, concid, no))
        booking_id = cursor.lastrowid
        cursor.execute("UPDATE Concerts SET available_tickets = available_tickets - %s WHERE concert_id = %s", (no, concid))
        db.commit()
    except mysql.connector.Error as err:
        db.rollback()
        print("Booking failed:", err.msg)
        return
    print(f"Booking successful! Your booking ID is {booking_id}.")


def Favourites(db, cursor):
    if not logged_in:
        print("Please log in to see your favourites.")
        return
    cursor.execute("SELECT artist_name FROM favourites WHERE username = %s ORDER BY artist_name", (username,))
    favourites = [row[0] for row in cursor.fetchall()]
    if favourites:
        print("Your favourite artists:")
        for name in favourites:
            print(" -", name)
    else:
        print("You haven't added any favourite artists yet.")
    if not read_yes_no("Favourite new artist? (y/n) "):
        return
    cursor.execute("SELECT DISTINCT artist_name FROM Concerts ORDER BY artist_name")
    artists = [row[0] for row in cursor.fetchall()]
    if not artists:
        print("There are no artists with concerts yet.")
        return
    print("Artists with concerts:", ", ".join(artists))
    add = read_text("Enter artist name: ")
    match = next((a for a in artists if a.lower() == add.lower()), None)
    if match is None:
        print("That artist doesn't have any concerts listed.")
        return
    if match in favourites:
        print(f"{match} is already in your favourites.")
        return
    cursor.execute("INSERT INTO favourites VALUES (%s, %s)", (username, match))
    db.commit()
    print(f"Added {match} to your favourites.")


def add_concert(db, cursor):
    artist_name = read_text("Enter artist/band name: ")
    venue = read_text("Enter venue name: ")
    date = read_date("Enter date (YYYY-MM-DD): ")
    time = read_time("Enter start time (HH:MM:SS): ")
    ticket_price = read_price("Enter ticket price: ")
    total_tickets = read_int("Enter total number of tickets: ", minimum=1)
    organizer = read_text("Enter concert organizer/promoter name: ")
    artistid = read_int("Enter artist ID: ", minimum=1)
    # A new concert starts with every ticket available (this used to be left empty).
    cursor.execute(
        "INSERT INTO Concerts (artist_name, venue, date, time, ticket_price, available_tickets, total_tickets, organizer, artistid)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (artist_name, venue, date, time, ticket_price, total_tickets, total_tickets, organizer, artistid),
    )
    db.commit()
    print("Concert added successfully!")


def update_concert(db, cursor):
    concert_id = read_int("Enter concert ID to update: ", minimum=1)
    cursor.execute("SELECT available_tickets, total_tickets FROM Concerts WHERE concert_id = %s", (concert_id,))
    concert = cursor.fetchone()
    if concert is None:
        print("Concert not found!")
        return
    available, old_total = (concert[0] or 0), (concert[1] or 0)
    sold = max(0, old_total - available)

    artist_name = read_text("Enter updated artist/band name: ")
    venue = read_text("Enter updated venue name: ")
    date = read_date("Enter updated date (YYYY-MM-DD): ")
    time = read_time("Enter updated start time (HH:MM:SS): ")
    ticket_price = read_price("Enter updated ticket price: ")
    total_tickets = read_int(f"Enter updated total number of tickets (at least {max(sold, 1)} already sold or reserved): ", minimum=max(sold, 1))
    organizer = read_text("Enter updated concert organizer/promoter name: ")
    artistid = read_int("Enter updated artist ID: ", minimum=1)

    # Values in the same order as the columns (concert_id and artistid used to be swapped),
    # and available tickets follow the new total so sold seats stay sold.
    cursor.execute(
        "UPDATE Concerts SET artist_name = %s, venue = %s, date = %s, time = %s, ticket_price = %s,"
        " total_tickets = %s, available_tickets = %s, organizer = %s, artistid = %s WHERE concert_id = %s",
        (artist_name, venue, date, time, ticket_price, total_tickets, total_tickets - sold, organizer, artistid, concert_id),
    )
    db.commit()
    print("Concert updated successfully!")


def delete_concert(db, cursor):
    concert_id = read_int("Enter concert ID to delete: ", minimum=1)
    cursor.execute("SELECT artist_name, venue, date FROM Concerts WHERE concert_id = %s", (concert_id,))
    concert = cursor.fetchone()
    if concert is None:
        print("Concert not found!")
        return
    if not read_yes_no(f"Delete {concert[0]} at {concert[1]} on {concert[2]}? (y/n) "):
        print("Nothing deleted.")
        return
    cursor.execute("DELETE FROM Concerts WHERE concert_id = %s", (concert_id,))
    db.commit()
    print("Concert deleted successfully")


# ---------- Main ----------

def mdp(db, cursor):
    print("---------------------------------- Encore. Concert planning made easy. ---------------------------------")
    ch = input("Would you like to login, register, or login as an admin?  ").strip().lower()
    if ch == "login":
        login(db, cursor)
    elif ch == "register":
        registration(db, cursor)
    elif ch == "admin":
        admin_login(db, cursor)
    else:
        print("Invalid choice!")


def mdp_contd(db, cursor):
    while read_yes_no("Continue? Y/N "):
        if Adminauth == 1:
            if not Admin_menu(db, cursor):
                break
        else:
            user_menu(db, cursor)


def main():
    db = connect()
    cursor = db.cursor()
    try:
        create_table(cursor)
        db.commit()
        mdp(db, cursor)
        # Only a successful user or admin login opens the menus.
        if logged_in == 1 or Adminauth == 1:
            mdp_contd(db, cursor)
    except (KeyboardInterrupt, EOFError):
        print("\nGoodbye.")
    finally:
        cursor.close()
        db.close()


if __name__ == "__main__":
    main()
