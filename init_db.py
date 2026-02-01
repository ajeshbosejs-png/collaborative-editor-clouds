import sqlite3

conn = sqlite3.connect('database.db')
c = conn.cursor()

# Users table
c.execute('''
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL
)
''')

# Restore requests table
c.execute('''
CREATE TABLE IF NOT EXISTS restore_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user TEXT,
    doc_id TEXT,
    version_file TEXT,
    status TEXT DEFAULT 'PENDING'
)
''')

conn.commit()
conn.close()

print("Database initialized successfully")
print("restore_requests table created successfully")
