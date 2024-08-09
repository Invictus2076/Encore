#importing modules
import mysql.connector
import random

# Establishing the connection
db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="Siddharth2006",
    database="concertmanagement"
)
cursor = db.cursor()

# Global variables
Adminauth = 0
registered = 0
logged_in = 0

# Functionality

def create_table(cursor):


    # Creating various tables


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

def login(cursor):
    global logged_in
    global username

    username = input("Enter your username: ")
    pw = input("Enter your password: ")
    cursor.execute("SELECT * FROM user")
    data = cursor.fetchall()
    for i in data:
        if i[0] == username and i[1] == pw:
            print("Login successful. Welcome, ", username)
            logged_in = 1
            return
    print("Error: Invalid username or password.")

def registration(cursor):
    global registered
    print("Welcome to Concertio registration!")

    username = input("Enter username: ")
    pw = input("Enter password: ")
    cursor.execute("SELECT * FROM user")
    data = cursor.fetchall()
    for i in data:
        if i[0] == username:
            print("Error: username taken.")
            registered = 1
            return
    cursor.execute("INSERT INTO user VALUES (%s, %s)", (username, pw))
    db.commit()
    print("Signup successful. Please login to continue.")
    registered = 1

def user_menu(cursor):
    print("Press 1 to search for a concert")
    print("Press 2 to display all concerts")
    print("Press 3 to view favourites")
    print("Press 4 to book tickets for a concert")
    choice = int(input("Enter your choice (1-4): "))

    if choice == 1:
        search_concert(cursor)
    elif choice == 2:
        display_concerts(cursor)
    elif choice == 3:
        Favourites(cursor)
    elif choice == 4:
        booking(cursor)
        print("Booking Successful!")
    else:
        print("Invalid choice!")

def admin_login(cursor):


    # Admin login


    global Adminauth


    adminid = int(input("Enter Admin ID: "))


    pw = input("Enter password: ")


    creds = (adminid, pw)


    cursor.execute("SELECT * FROM admin")


    admindata = cursor.fetchall()


    for i in admindata:


        if i == creds:


            print("Login successful. Welcome, Admin.")


            Adminauth = 1


            return


    print("Error: Invalid Admin ID or password")

def Admin_menu(cursor):
    print("1. Add Concert")
    print("2. Update Concert")
    print("3. Display All Concerts")
    print("4. Delete Concert")
    print("5. Exit")
    choice = int(input("Enter your choice (1-5): "))

    if choice == 1:
        add_concert(cursor)
    elif choice == 2:
        update_concert(cursor)
    elif choice == 3:
        display_concerts(cursor)
    elif choice == 4:
        delete_concert(cursor)
    elif choice == 5:
        print("Logging out...")
    else:
        print("Invalid choice!")

def display_concerts(cursor):
    # Displaying all concerts in the database
    display_query = "SELECT * FROM Concerts"
    cursor.execute(display_query)
    concerts = cursor.fetchall()

    if len(concerts) == 0:
        print("No concerts found!")
        return
 
    print("Concerts List:")
    for concert in concerts:
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
        print("\n")

def booking(cursor):
     concid = int(input("Enter id of concert for which you would like to book: "))
     no = int(input("Enter number of tickets you would like to book: "))
     
     
     vals = (no,concid)
     entry = (username,concid,no)
     
     query = "insert into bookings(booking_id, username, concert_id, no_of_tickets) values(%s, %s, %s, %s)"
     query2 = "UPDATE CONCERTS SET AVAILABLE_TICKETS = AVAILABLE_TICKETS - %s WHERE CONCERT_ID = %s"

     cursor.execute(query,entry)
     cursor.execute(query2,vals)
     print("Booking successful!")

def Favourites(cursor):
    global logged_in
    if not logged_in:
        print("Please reload app and login first.")
        return
    cursor.execute("SELECT artist_name from favourites where username=%s", [username, ])
    data = cursor.fetchall()
    print(data)
    choice = input("Favourite new artist? (y/n)")
    if choice == 'y':
        cursor.execute("SELECT artist_name FROM concerts")
        dat = cursor.fetchall()
        add = input("Enter artist name")
        cursor.execute("INSERT INTO favourites VALUES (%s, %s)", (username, add))
        db.commit()

def add_concert(cursor):
    # Adding a new concert to the database
    artist_name = input("Enter artist/band name: ")
    venue = input("Enter venue name: ")
    date = input("Enter date (YYYY-MM-DD): ")
    time = input("Enter start time (HH:MM:SS): ")
    ticket_price = float(input("Enter ticket price: "))
    total_tickets = int(input("Enter total number of tickets: "))
    organizer = input("Enter concert organizer/promoter name: ")
    artistid = int(input("Enter artist ID: "))

    query = "INSERT INTO Concerts (artist_name, venue, date, time, ticket_price, total_tickets, organizer, artistid)  VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
    values = (artist_name, venue, date, time, ticket_price, total_tickets, organizer, artistid)
    cursor.execute(query, values)
    db.commit()
    print("Concert added successfully!")

def update_concert(cursor):
    # Updating an existing concert in the database
    concert_id = int(input("Enter concert ID to update: "))

    # Check if concert exists
    cursor.execute("SELECT * FROM Concerts WHERE concert_id = {}".format(concert_id))
    concert = cursor.fetchone()
    if concert is None:
        print("Concert not found!")
        return

    # Get the updated concert details
    artist_name = input("Enter updated artist/band name: ")
    venue = input("Enter updated venue name: ")
    date = input("Enter updated date (YYYY-MM-DD): ")
    time = input("Enter updated start time (HH:MM:SS): ")
    ticket_price = float(input("Enter updated ticket price: "))
    total_tickets = int(input("Enter updated total number of tickets: "))
    organizer = input("Enter updated concert organizer/promoter name: ")
    artistid = int(input("Enter updated artist ID"))

    update_query = """
        UPDATE Concerts SET artist_name = %s, venue = %s, date = %s, time = %s,
        ticket_price = %s, total_tickets = %s, organizer = %s, artistid = %s WHERE concert_id = %s
    """
    update_values = (artist_name, venue, date, time, ticket_price, total_tickets,
                     organizer, concert_id, artistid)
    cursor.execute(update_query, update_values)
    db.commit()
    print("Concert updated successfully!")

def search_concert():
    # Searching for a concert by artist name
    artist_name = input("Enter artist/band name to search: ")

    search_query = "SELECT * FROM Concerts WHERE artist_name LIKE '%{}%'".format(artist_name)
    cursor.execute(search_query)
    concerts = cursor.fetchall()

    if len(concerts) == 0:
        print("No concerts found!")
        return

    print("\nSearch results:")
    for concert in concerts:
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
        print("\n")


def delete_concert(cursor):
    # Deleting a concert from the database
    concert_id = int(input("Enter concert ID to delete: "))

    delete_query = "DELETE FROM Concerts WHERE concert_id = {}".format(concert_id)
    cursor.execute(delete_query)
    db.commit()
    print("Concert deleted successfully")

def mdp():

    print("---------------------------------- Encore. Concert planning made easy. ---------------------------------")
    ch = input("Would you like to login, register, or login as an admin?  ")
    if ch.lower() == 'login':
        login(cursor)
    elif ch.lower() == 'register':
        registration(cursor)
    elif ch.lower() == 'admin':
        admin_login(cursor)
    else:
        print("Invalid choice!")


def mdp_contd():
    while True:
        choice = input("Continue? Y/N")
        if choice.lower() == 'n':
            break
        if Adminauth == 1:
            Admin_menu(cursor)
        else:
            user_menu(cursor)
        
create_table(cursor)

mdp()

if logged_in == 1:
    mdp_contd()
elif registered == 1:
    mdp_contd()
elif Adminauth == 1:
    mdp_contd()

# Closing the database connection
db.commit()
db.close()

