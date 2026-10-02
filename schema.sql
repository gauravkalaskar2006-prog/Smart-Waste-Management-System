-- STRICT SCHEMA (do not modify)

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  mobile_number TEXT UNIQUE NOT NULL,
  password TEXT NOT NULL,
  role TEXT CHECK(role IN ('citizen','worker','admin')) NOT NULL,
  zone INTEGER
);

CREATE TABLE IF NOT EXISTS bins (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bin_id TEXT UNIQUE NOT NULL,
  location TEXT,
  latitude REAL,
  longitude REAL,
  fill_level REAL DEFAULT 0,
  status TEXT CHECK(status IN ('Empty','Half','Full')) DEFAULT 'Empty',
  last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS complaints (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bin_id TEXT NOT NULL,
  citizen_id INTEGER,
  complaint_text TEXT NOT NULL,
  image TEXT,
  status TEXT CHECK(status IN ('Pending','In Progress','Resolved')) DEFAULT 'Pending',
  assigned_worker_id INTEGER,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);




