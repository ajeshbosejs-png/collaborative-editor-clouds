import sqlite3

conn = sqlite3.connect("database.db")
c = conn.cursor()

try:
    rows = c.execute("SELECT * FROM restore_requests").fetchall()

    print("\n--- RESTORE REQUESTS TABLE DATA ---")
    if rows:
        for r in rows:
            print(r)
    else:
        print("\nTABLE IS EMPTY ❌  (No restore requests in DB)")

except Exception as e:
    print("ERROR:", e)

conn.close()
