from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import requests
from flask import Flask, abort, g, jsonify, redirect, render_template, request, session, url_for
from flask_cors import CORS  # Added for cross-origin access
from werkzeug.security import check_password_hash, generate_password_hash

from db import close_db, get_db, init_db

# Set template folder to your specific name 'templeate'
app = Flask(__name__, template_folder="templeate")
app.secret_key = "silentstorm_secret"
# database.db in project root
app.config["DATABASE"] = str(Path(__file__).resolve().parent / "database.db")
CORS(app)  # This allows your HTML file to fetch data without errors

# ESP32 API address (Update this if your ESP32 IP changes)
ESP32_URL = "http://10.197.28.80/data"

# Bin Configuration
BIN_HEIGHT = 30  # cm

@app.before_request
def _ensure_db():
    # Initialize DB once per process (safe to call repeatedly)
    if not app.config.get("_DB_INIT_DONE"):
        try:
            init_db()
            db = get_db()
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS worker_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    mobile_number TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    zone INTEGER NOT NULL,
                    status TEXT CHECK(status IN ('Pending','Approved','Rejected')) NOT NULL DEFAULT 'Pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reviewed_at TIMESTAMP,
                    reviewed_by INTEGER
                )
                """
            )
            # Create a default admin if none exists (helps first-run usability)
            admin_exists = db.execute(
                "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"
            ).fetchone()
            if not admin_exists:
                db.execute(
                    "INSERT INTO users (name, mobile_number, password, role, zone) VALUES (?, ?, ?, 'admin', NULL)",
                    ("Admin", "9999999999", generate_password_hash("admin123"),),
                )
            # Seed a few bins if table is empty (keeps UI dynamic on first run)
            bins_exist = db.execute("SELECT 1 FROM bins LIMIT 1").fetchone()
            if not bins_exist:
                seed_bins = [
                    ("BIN-001", "Rajapeth Market", 20.9374, 77.7796),
                    ("BIN-002", "Gandhi Square", 20.9320, 77.7820),
                    ("BIN-003", "Main Market Zone 1", 20.9350, 77.7750),
                ]
                db.executemany(
                    "INSERT OR IGNORE INTO bins (bin_id, location, latitude, longitude) VALUES (?, ?, ?, ?)",
                    seed_bins,
                )
            db.commit()
        finally:
            app.config["_DB_INIT_DONE"] = True


@app.teardown_appcontext
def _teardown_db(exc):
    close_db(exc)


def _get_current_user():
    """Load current user row as dict (id, role, zone, mobile_number, name)."""
    uid = session.get("user_id")
    if not uid:
        return None
    db = get_db()
    row = db.execute(
        "SELECT id, name, mobile_number, role, zone FROM users WHERE id = ?",
        (uid,),
    ).fetchone()
    return dict(row) if row else None


def _login_required():
    if not session.get("user_id"):
        return redirect(url_for("index"))
    return None


def _role_required(*roles: str):
    user = _get_current_user()
    if not user or user["role"] not in roles:
        return redirect(url_for("index"))
    g.current_user = user
    return None


def _map_complaint_status_for_ui(db_status: str) -> str:
    # Existing frontend uses "Open" for active complaints.
    if db_status in ("Pending", "In Progress"):
        return "Open"
    return "Resolved"


def _bin_status_from_fill(fill_level: float) -> str:
    if fill_level >= 90:
        return "Full"
    if fill_level >= 50:
        return "Half"
    return "Empty"


def _parse_zone(value: str | None) -> int | None:
    try:
        zone_int = int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    if zone_int is None:
        return None
    if 1 <= zone_int <= 6:
        return zone_int
    return None


def _wants_json_response() -> bool:
    if (request.args.get("format") or "").strip().lower() == "json":
        return True
    best = request.accept_mimetypes.best
    return best == "application/json" and (
        request.accept_mimetypes["application/json"] >= request.accept_mimetypes["text/html"]
    )


def _get_latest_worker_request(db, mobile: str):
    return db.execute(
        """
        SELECT id, name, mobile_number, password, zone, status
          FROM worker_requests
         WHERE mobile_number = ?
         ORDER BY id DESC
         LIMIT 1
        """,
        (mobile,),
    ).fetchone()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/login", methods=["POST"])
def login():
    login_type = (request.form.get("login_type") or "").strip().lower()  # citizen | official
    mobile = (request.form.get("mobile") or "").strip()
    password = request.form.get("password") or ""

    if not mobile or not password:
        return render_template("index.html", error="Mobile number and password are required.")

    db = get_db()
    if login_type == "citizen":
        user = db.execute(
            "SELECT id, password, role, zone FROM users WHERE mobile_number = ? AND role = 'citizen'",
            (mobile,),
        ).fetchone()
        if not user or not check_password_hash(user["password"], password):
            return render_template("index.html", error="Invalid credentials.")

        session.clear()
        session["user_id"] = user["id"]
        return redirect(url_for("citizendashboard"))

    if login_type == "official":
        role_raw = (request.form.get("role") or "").strip().lower()
        if role_raw not in ("worker", "admin"):
            return render_template("index.html", error="Please select a valid official role.")

        if role_raw == "worker":
            request_row = _get_latest_worker_request(db, mobile)
            if request_row and request_row["status"] == "Rejected":
                if check_password_hash(request_row["password"], password):
                    return render_template("index.html", error="Your worker request was rejected by admin. Login is not allowed.")
            if request_row and request_row["status"] == "Pending":
                if check_password_hash(request_row["password"], password):
                    return render_template("index.html", error="Your worker request is pending admin approval.")

        user = db.execute(
            "SELECT id, password, role, zone FROM users WHERE mobile_number = ? AND role = ?",
            (mobile, role_raw),
        ).fetchone()
        if not user or not check_password_hash(user["password"], password):
            return render_template("index.html", error="Invalid credentials.")

        if role_raw == "worker":
            request_row = _get_latest_worker_request(db, mobile)
            if request_row and request_row["status"] in ("Pending", "Rejected"):
                return render_template("index.html", error="Worker login is not allowed until admin approves your request.")
            zone_int = _parse_zone(request.form.get("zone"))
            if zone_int is None:
                return render_template("index.html", error="Zone is required for worker login.")
            if user["zone"] != zone_int:
                return render_template("index.html", error="Invalid credentials.")

        session.clear()
        session["user_id"] = user["id"]
        if role_raw == "worker":
            return redirect(url_for("worker"))
        return redirect(url_for("admindashboard"))

    # Fallback for older forms that do not send login_type.
    user = db.execute(
        "SELECT id, password, role, zone FROM users WHERE mobile_number = ?",
        (mobile,),
    ).fetchone()
    if not user or not check_password_hash(user["password"], password):
        # Keep backward compatibility but still enforce worker request status on old login forms.
        request_row = _get_latest_worker_request(db, mobile)
        if request_row and check_password_hash(request_row["password"], password):
            if request_row["status"] == "Pending":
                return render_template("index.html", error="Your worker request is pending admin approval.")
            if request_row["status"] == "Rejected":
                return render_template("index.html", error="Your worker request was rejected by admin. Login is not allowed.")
        return render_template("index.html", error="Invalid credentials.")

    if user["role"] == "worker":
        request_row = _get_latest_worker_request(db, mobile)
        if request_row and request_row["status"] in ("Pending", "Rejected"):
            return render_template("index.html", error="Worker login is not allowed until admin approves your request.")
        zone_int = _parse_zone(request.form.get("zone"))
        if zone_int is None or user["zone"] != zone_int:
            return render_template("index.html", error="Invalid credentials.")

    session.clear()
    session["user_id"] = user["id"]

    if user["role"] == "citizen":
        return redirect(url_for("citizendashboard"))
    if user["role"] == "worker":
        return redirect(url_for("worker"))
    if user["role"] == "admin":
        return redirect(url_for("admindashboard"))

    return render_template("index.html", error="Invalid role.")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/register/citizen", methods=["GET", "POST"])
def register_citizen():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        mobile = (request.form.get("mobile") or "").strip()
        password = request.form.get("password") or ""
        zone_raw = request.form.get("zone")
        zone_int = _parse_zone(zone_raw) if zone_raw else None

        if not name or not mobile or not password:
            return render_template("citizen_register.html", error="Name, mobile number, and password are required.")
        if zone_raw and zone_int is None:
            return render_template("citizen_register.html", error="Please select a valid zone.")

        db = get_db()
        try:
            db.execute(
                "INSERT INTO users (name, mobile_number, password, role, zone) VALUES (?, ?, ?, 'citizen', ?)",
                (name, mobile, generate_password_hash(password), zone_int),
            )
            db.commit()
        except sqlite3.IntegrityError:
            return render_template("citizen_register.html", error="This mobile number is already registered.")
        except Exception:
            return render_template("citizen_register.html", error="Unable to register citizen right now.")

        return render_template("index.html", success="Citizen registered. You can now sign in.")
    return render_template("citizen_register.html")

@app.route("/register/worker", methods=["GET", "POST"])
def register_worker():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        mobile = (request.form.get("mobile") or "").strip()
        password = request.form.get("password") or ""
        zone_int = _parse_zone(request.form.get("zone"))

        if not name or not mobile or not password or zone_int is None:
            return render_template("worker_register.html", error="Name, mobile number, password, and valid zone are required.")

        db = get_db()
        try:
            existing_user = db.execute(
                "SELECT role FROM users WHERE mobile_number = ?",
                (mobile,),
            ).fetchone()
            if existing_user:
                return render_template("worker_register.html", error="This mobile number is already registered.")

            existing_request = _get_latest_worker_request(db, mobile)
            if existing_request:
                if existing_request["status"] == "Pending":
                    return render_template("worker_register.html", error="Your request is already pending admin approval.")
                if existing_request["status"] == "Rejected":
                    return render_template("worker_register.html", error="Your previous request was rejected by admin.")
                return render_template("worker_register.html", error="This mobile number is already approved for worker login.")

            db.execute(
                """
                INSERT INTO worker_requests (name, mobile_number, password, zone, status)
                VALUES (?, ?, ?, ?, 'Pending')
                """,
                (name, mobile, generate_password_hash(password), zone_int),
            )
            db.commit()
        except sqlite3.IntegrityError:
            return render_template("worker_register.html", error="This mobile number is already registered.")
        except Exception:
            return render_template("worker_register.html", error="Unable to register worker right now.")

        return render_template("index.html", success="Worker request submitted. Wait for admin approval before sign in.")
    return render_template("worker_register.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    """Registration page used by `templeate/register.html` (role-based)."""
    if request.method == "GET":
        return render_template("register.html")

    name = (request.form.get("name") or "").strip()
    mobile = (request.form.get("mobile") or "").strip()
    role = (request.form.get("role") or "").strip().lower()
    password = request.form.get("password") or ""

    if role not in ("admin", "worker", "citizen"):
        return render_template("register.html")
    if not name or not mobile or not password:
        return render_template("register.html")
    if role == "worker":
        return redirect(url_for("register_worker"))

    zone = 1 if role == "worker" else None

    db = get_db()
    try:
        db.execute(
            "INSERT INTO users (name, mobile_number, password, role, zone) VALUES (?, ?, ?, ?, ?)",
            (name, mobile, generate_password_hash(password), role, zone),
        )
        db.commit()
    except Exception:
        # likely duplicate mobile_number
        return render_template("register.html")

    return redirect(url_for("index"))

@app.route("/citizendashboard")
def citizendashboard():
    guard = _role_required("citizen")
    if guard:
        return guard
    # Keep template compatibility (expects user.mobile and user.zone)
    u = g.current_user
    return render_template(
        "citizendashboard.html",
        user={"mobile": u["mobile_number"], "zone": u["zone"] or 1, "name": u["name"], "id": u["id"]},
    )

@app.route("/admindashboard")
def admindashboard():
    guard = _role_required("admin")
    if guard:
        return guard
    u = g.current_user
    return render_template(
        "admindashboard.html",
        user={"mobile": u["mobile_number"], "zone": u["zone"], "name": u["name"], "id": u["id"]},
    )

@app.route("/worker")
def worker():
    guard = _role_required("worker")
    if guard:
        return guard
    u = g.current_user
    return render_template(
        "workerdashboard.html",
        user={"mobile": u["mobile_number"], "zone": u["zone"] or 1, "id": u["id"], "name": u["name"]},
    )

@app.route("/bins")
def bins():
    # Admin + Worker can view; citizen can too if logged in, but keep it open.
    user = _get_current_user()
    tmpl_user = None
    if user:
        tmpl_user = {
            "id": user["id"],
            "mobile": user["mobile_number"],
            "role": "Worker" if user["role"] == "worker" else ("Admin" if user["role"] == "admin" else "Citizen"),
            "zone": user["zone"],
        }
    return render_template("bins.html", user=tmpl_user)

@app.route("/addbins")
def addbins():
    return render_template("addbins.html")

@app.route("/removebins")
def removebins():
    return render_template("removebins.html")

@app.route("/alerts")
def alerts():
    return render_template("alerts.html")

@app.route("/analysis")
def analysis():
    return render_template("analysis.html")

@app.route("/settings", methods=["GET", "POST"])
def settings():
    guard = _login_required()
    if guard:
        return guard

    user = _get_current_user()
    if not user:
        return redirect(url_for("index"))

    # Citizen settings with DB-backed profile + password updates.
    if user["role"] == "citizen":
        success = None
        error = None
        if request.method == "POST":
            action = (request.form.get("action") or "").strip()
            db = get_db()

            if action == "change_password":
                old_password = request.form.get("old_password") or ""
                new_password = request.form.get("new_password") or ""

                if not old_password or not new_password:
                    error = "Old password and new password are required."
                elif len(new_password) < 6:
                    error = "New password must be at least 6 characters long."
                else:
                    row = db.execute("SELECT password FROM users WHERE id = ?", (user["id"],)).fetchone()
                    if not row or not check_password_hash(row["password"], old_password):
                        error = "Old password is incorrect."
                    else:
                        db.execute(
                            "UPDATE users SET password = ? WHERE id = ?",
                            (generate_password_hash(new_password), user["id"]),
                        )
                        db.commit()
                        success = "Password changed successfully."
            elif action == "update_profile":
                new_name = (request.form.get("name") or "").strip() or user["name"]
                zone_raw = (request.form.get("zone") or "").strip()
                if zone_raw:
                    zone_int = _parse_zone(zone_raw)
                    if zone_int is None:
                        error = "Please select a valid zone."
                    else:
                        db.execute(
                            "UPDATE users SET name = ?, zone = ? WHERE id = ?",
                            (new_name, zone_int, user["id"]),
                        )
                        db.commit()
                        success = "Profile updated successfully."
                else:
                    db.execute(
                        "UPDATE users SET name = ? WHERE id = ?",
                        (new_name, user["id"]),
                    )
                    db.commit()
                    success = "Profile updated successfully."
            else:
                error = "Invalid settings request."

            user = _get_current_user() or user

        return render_template("citizen_settings.html", user=user, success=success, error=error)

    return render_template("settings.html", user=user)

@app.route("/workers")
def workers():
    guard = _role_required("admin")
    if guard:
        return guard
    return render_template("workers.html")

@app.route("/api/admin/worker_requests")
def api_worker_requests():
    guard = _role_required("admin")
    if guard:
        return jsonify([]), 401

    db = get_db()
    rows = db.execute(
        """
        SELECT id, name, mobile_number, zone, status, created_at, reviewed_at
          FROM worker_requests
         ORDER BY id DESC
        """
    ).fetchall()
    return jsonify(
        [
            {
                "id": r["id"],
                "name": r["name"],
                "mobile": r["mobile_number"],
                "zone": r["zone"] or 1,
                "status": r["status"],
                "created_at": r["created_at"],
                "reviewed_at": r["reviewed_at"],
            }
            for r in rows
        ]
    )

@app.route("/api/admin/approve_worker/<mobile>", methods=["POST"])
def api_approve_worker(mobile):
    guard = _role_required("admin")
    if guard:
        return jsonify({"success": False}), 401
    db = get_db()
    req = _get_latest_worker_request(db, mobile)
    if not req:
        return jsonify({"success": False, "error": "Worker request not found."}), 404

    existing_user = db.execute(
        "SELECT id, role FROM users WHERE mobile_number = ?",
        (mobile,),
    ).fetchone()
    if existing_user and existing_user["role"] != "worker":
        return jsonify({"success": False, "error": "This mobile number belongs to a non-worker account."}), 409

    try:
        if not existing_user:
            db.execute(
                """
                INSERT INTO users (name, mobile_number, password, role, zone)
                VALUES (?, ?, ?, 'worker', ?)
                """,
                (req["name"], req["mobile_number"], req["password"], req["zone"]),
            )

        db.execute(
            """
            UPDATE worker_requests
               SET status = 'Approved',
                   reviewed_at = CURRENT_TIMESTAMP,
                   reviewed_by = ?
             WHERE id = ?
            """,
            (g.current_user["id"], req["id"]),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"success": False, "error": "Unable to approve worker request."}), 409
    except Exception:
        return jsonify({"success": False, "error": "Unable to approve worker request right now."}), 500

    return jsonify({"success": True})

@app.route("/api/admin/reject_worker/<mobile>", methods=["POST"])
def api_reject_worker(mobile):
    guard = _role_required("admin")
    if guard:
        return jsonify({"success": False}), 401
    db = get_db()
    req = _get_latest_worker_request(db, mobile)

    if not req:
        existing_worker = db.execute(
            "SELECT name, mobile_number, password, zone FROM users WHERE role = 'worker' AND mobile_number = ?",
            (mobile,),
        ).fetchone()
        if existing_worker:
            try:
                db.execute(
                    """
                    INSERT INTO worker_requests (name, mobile_number, password, zone, status, reviewed_at, reviewed_by)
                    VALUES (?, ?, ?, ?, 'Rejected', CURRENT_TIMESTAMP, ?)
                    """,
                    (
                        existing_worker["name"],
                        existing_worker["mobile_number"],
                        existing_worker["password"],
                        existing_worker["zone"] or 1,
                        g.current_user["id"],
                    ),
                )
            except sqlite3.IntegrityError:
                db.execute(
                    """
                    UPDATE worker_requests
                       SET status = 'Rejected',
                           reviewed_at = CURRENT_TIMESTAMP,
                           reviewed_by = ?
                     WHERE mobile_number = ?
                    """,
                    (g.current_user["id"], mobile),
                )
    else:
        db.execute(
            """
            UPDATE worker_requests
               SET status = 'Rejected',
                   reviewed_at = CURRENT_TIMESTAMP,
                   reviewed_by = ?
             WHERE id = ?
            """,
            (g.current_user["id"], req["id"]),
        )

    db.execute("DELETE FROM users WHERE role = 'worker' AND mobile_number = ?", (mobile,))
    db.commit()
    return jsonify({"success": True})

# --------- CITIZEN API FOR QR SCAN ---------
@app.route("/api/citizen/scan_qr", methods=["POST"])
def api_scan_qr():
    # Citizen creates a complaint against a bin_id (QR decoded text)
    guard = _role_required("citizen")
    if guard:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    bin_id = (data.get("bin_id") or "").strip() or "UNKNOWN"
    complaint_text = (data.get("complaint_text") or "QR complaint").strip()

    db = get_db()
    # Ensure bin exists in bins table (create stub if missing)
    db.execute(
        "INSERT OR IGNORE INTO bins (bin_id, location) VALUES (?, ?)",
        (bin_id, None),
    )
    cur = db.execute(
        "INSERT INTO complaints (bin_id, citizen_id, complaint_text, image, status, assigned_worker_id) VALUES (?, ?, ?, ?, 'Pending', NULL)",
        (bin_id, g.current_user["id"], complaint_text, data.get("image")),
    )
    db.commit()

    complaint_row = db.execute(
        "SELECT id, bin_id, status, created_at FROM complaints WHERE id = ?",
        (cur.lastrowid,),
    ).fetchone()

    return jsonify(
        {
            "success": True,
            "complaint": {
                "id": f"COMP-{complaint_row['id']}",
                "bin_id": complaint_row["bin_id"],
                "bin_zone": str(g.current_user["zone"] or 1),
                "status": _map_complaint_status_for_ui(complaint_row["status"]),
                "timestamp": complaint_row["created_at"],
            },
        }
    )

@app.route("/api/complaints")
def api_get_complaints():
    user = _get_current_user()
    db = get_db()

    # For citizen/worker dashboards, they currently filter client-side by bin_zone.
    # We provide bin_zone derived from bins table when possible.
    rows = db.execute(
        """
        SELECT c.id, c.bin_id, c.status, c.created_at,
               b.location, b.latitude, b.longitude
        FROM complaints c
        LEFT JOIN bins b ON b.bin_id = c.bin_id
        ORDER BY c.id DESC
        """
    ).fetchall()

    # Derive zone: if the current user has a zone, echo it (keeps existing UI working)
    # Otherwise default to "1".
    zone_str = str(user["zone"] if user and user.get("zone") else 1)

    return jsonify(
        [
            {
                "id": f"COMP-{r['id']}",
                "bin_id": r["bin_id"],
                "bin_zone": zone_str,
                "status": _map_complaint_status_for_ui(r["status"]),
                "timestamp": r["created_at"],
                "location": r["location"],
                "lat": r["latitude"],
                "lng": r["longitude"],
            }
            for r in rows
        ]
    )


@app.route("/api/complaints/my")
def api_get_my_complaints():
    """Citizen: view their complaints (filter by citizen_id)."""
    guard = _role_required("citizen")
    if guard:
        return jsonify({"error": "Unauthorized"}), 401

    db = get_db()
    rows = db.execute(
        """
        SELECT c.id, c.bin_id, c.complaint_text, c.status, c.created_at, c.updated_at,
               c.assigned_worker_id, b.location
          FROM complaints c
          LEFT JOIN bins b ON b.bin_id = c.bin_id
         WHERE c.citizen_id = ?
         ORDER BY c.id DESC
        """,
        (g.current_user["id"],),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/worker/complaints")
def api_worker_complaints():
    """Worker: fetch complaints assigned to them."""
    guard = _role_required("worker")
    if guard:
        return jsonify({"error": "Unauthorized"}), 401

    db = get_db()
    rows = db.execute(
        """
        SELECT c.id, c.bin_id, c.complaint_text, c.status, c.created_at, c.updated_at,
               c.citizen_id, b.location, b.latitude, b.longitude
          FROM complaints c
          LEFT JOIN bins b ON b.bin_id = c.bin_id
         WHERE c.assigned_worker_id = ?
         ORDER BY c.id DESC
        """,
        (g.current_user["id"],),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/admin/bins")
def api_admin_bins():
    """Admin: view all bins."""
    guard = _role_required("admin")
    if guard:
        return jsonify({"error": "Unauthorized"}), 401
    db = get_db()
    rows = db.execute("SELECT * FROM bins ORDER BY id DESC").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/admin/complaints")
def api_admin_complaints():
    """Admin: view all complaints."""
    guard = _role_required("admin")
    if guard:
        return jsonify({"error": "Unauthorized"}), 401
    db = get_db()
    rows = db.execute(
        """
        SELECT c.*, u.mobile_number AS citizen_mobile, w.mobile_number AS worker_mobile
          FROM complaints c
          LEFT JOIN users u ON u.id = c.citizen_id
          LEFT JOIN users w ON w.id = c.assigned_worker_id
         ORDER BY c.id DESC
        """
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/complaints/<int:complaint_id>/status", methods=["POST"])
def api_update_complaint_status(complaint_id: int):
    guard = _role_required("worker", "admin")
    if guard:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    new_status = data.get("status")
    if new_status not in ("Pending", "In Progress", "Resolved"):
        return jsonify({"success": False, "error": "Invalid status"}), 400

    db = get_db()
    # workers can only update complaints assigned to them (if assigned)
    if g.current_user["role"] == "worker":
        db.execute(
            """
            UPDATE complaints
               SET status = ?, updated_at = CURRENT_TIMESTAMP
             WHERE id = ? AND (assigned_worker_id IS NULL OR assigned_worker_id = ?)
            """,
            (new_status, complaint_id, g.current_user["id"]),
        )
    else:
        db.execute(
            "UPDATE complaints SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_status, complaint_id),
        )
    db.commit()
    return jsonify({"success": True})


@app.route("/api/admin/assign_worker", methods=["POST"])
def api_admin_assign_worker():
    guard = _role_required("admin")
    if guard:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    complaint_id = data.get("complaint_id")
    worker_id = data.get("worker_id")
    if not complaint_id or not worker_id:
        return jsonify({"success": False, "error": "Missing complaint_id/worker_id"}), 400

    db = get_db()
    # Validate worker exists
    w = db.execute("SELECT id FROM users WHERE id = ? AND role = 'worker'", (worker_id,)).fetchone()
    if not w:
        return jsonify({"success": False, "error": "Worker not found"}), 404

    db.execute(
        "UPDATE complaints SET assigned_worker_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (worker_id, complaint_id),
    )
    db.commit()
    return jsonify({"success": True})


@app.route("/complaint/<bin_id>", methods=["GET", "POST"])
def complaint(bin_id: str):
    db = get_db()
    row = db.execute("SELECT * FROM bins WHERE bin_id = ?", (bin_id,)).fetchone()
    wants_json = request.is_json or _wants_json_response()

    if not row:
        if wants_json:
            return jsonify({"success": False, "error": "Bin not found", "bin_id": bin_id}), 404
        return render_template("complaint.html", bin=None, error=f"Bin {bin_id} was not found.", success=None), 404

    fill_value = float(row["fill_level"] or 0)
    status_value = row["status"] or _bin_status_from_fill(fill_value)
    bin_details = {
        "bin_id": row["bin_id"],
        "location": row["location"] or "Not available",
        "fill_level": int(round(fill_value)),
        "status": status_value,
        "last_updated": row["last_updated"] or "N/A",
    }

    if request.method == "GET":
        if wants_json:
            return jsonify(bin_details)
        return render_template(
            "complaint.html",
            bin=bin_details,
            error=None,
            success=None,
            user=_get_current_user(),
        )

    data = request.get_json(silent=True) or {}
    complaint_text = (request.form.get("complaint_text") or data.get("complaint_text") or "").strip()
    if not complaint_text:
        if wants_json:
            return jsonify({"success": False, "error": "Complaint text is required."}), 400
        return render_template(
            "complaint.html",
            bin=bin_details,
            error="Complaint text is required.",
            success=None,
            user=_get_current_user(),
        ), 400

    current_user = _get_current_user()
    citizen_id = current_user["id"] if current_user and current_user.get("role") == "citizen" else None

    db.execute(
        "INSERT INTO complaints (bin_id, citizen_id, complaint_text) VALUES (?, ?, ?)",
        (bin_id, citizen_id, complaint_text),
    )
    db.commit()

    if wants_json:
        return jsonify({"success": True, "message": "Complaint submitted successfully.", "bin_id": bin_id})
    return render_template(
        "complaint.html",
        bin=bin_details,
        error=None,
        success="Complaint submitted successfully.",
        user=_get_current_user(),
    )

# ... (Keep all your other routes like get_single_bin_data, api_bins, etc. exactly as they are) ...

# NEW ENDPOINT: Specifically for the Modal in bins.html
@app.route("/data")
def get_single_bin_data():
    try:
        response = requests.get(ESP32_URL, timeout=2)
        sensor = response.json()
        distance = sensor.get("distance", 0)
        
        # Calculate fill percentage
        fill_level = max(0, min(100, int((BIN_HEIGHT - distance) / BIN_HEIGHT * 100)))

        return jsonify({
            "distance": distance,
            "fill": fill_level
        })
    except Exception as e:
        # Return zeros if ESP32 is disconnected
        return jsonify({
            "distance": "--",
            "fill": 0,
            "error": "Device Offline"
        })

# -------- UPDATED ESP32 LIVE DATA API FOR TABLE --------
@app.route("/api/bins")
def api_bins():
    # Return bins from DB (dynamic). Optionally merge one live ESP32 distance reading as a "global" sensor.
    db = get_db()
    rows = db.execute(
        "SELECT bin_id, location, fill_level, status, last_updated FROM bins ORDER BY id ASC"
    ).fetchall()

    distance = None
    try:
        response = requests.get(ESP32_URL, timeout=2)
        sensor = response.json()
        distance = sensor.get("distance", None)
    except Exception:
        distance = None

    bins_out = []
    for r in rows:
        fill_level = float(r["fill_level"] or 0)
        bins_out.append(
            {
                "id": r["bin_id"],
                "location": r["location"] or "",
                "distance": distance if distance is not None else 0,
                "fillLevel": int(fill_level),
            }
        )

    return jsonify({"bins": bins_out})


@app.route("/api/bin/update", methods=["POST"])
def api_bin_update():
    """ESP32 integration: update fill_level/status/last_updated for a bin."""
    data = request.get_json(silent=True) or {}
    bin_id = (data.get("bin_id") or "").strip()
    if not bin_id:
        return jsonify({"success": False, "error": "bin_id required"}), 400

    try:
        fill_level = float(data.get("fill_level", 0))
    except (TypeError, ValueError):
        fill_level = 0.0
    fill_level = max(0.0, min(100.0, fill_level))
    status = data.get("status")
    if status not in ("Empty", "Half", "Full"):
        status = _bin_status_from_fill(fill_level)

    db = get_db()
    # Insert stub if missing, then update fields.
    db.execute("INSERT OR IGNORE INTO bins (bin_id) VALUES (?)", (bin_id,))
    db.execute(
        """
        UPDATE bins
           SET location = COALESCE(?, location),
               latitude = COALESCE(?, latitude),
               longitude = COALESCE(?, longitude),
               fill_level = ?,
               status = ?,
               last_updated = CURRENT_TIMESTAMP
         WHERE bin_id = ?
        """,
        (
            data.get("location"),
            data.get("latitude"),
            data.get("longitude"),
            fill_level,
            status,
            bin_id,
        ),
    )
    db.commit()
    return jsonify({"success": True})

@app.route("/chatbot", methods=["POST"])
def chatbot_api():
    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({"reply": "I didn't catch that. Type 'help' for options.", "suggestions": ["Help"]})
         
    msg = data["message"].lower()
    
    # Define primary buttons
    primary_buttons = ["🗑️ Check Bin Status", "🚛 Pickup Schedule", "⚠️ Report Complaint", "📍 Nearby Bins"]
    yes_no_buttons = ["Yes", "No"]
    
    # Greeting / Help
    if any(word in msg for word in ["hi", "hello", "hey", "namaste", "नमस्कार", "help"]):
        return jsonify({
            "reply": "Hi! How can I help you today? (नमस्ते! मैं आपकी कैसे मदद कर सकता हूँ?)",
            "suggestions": primary_buttons
        })
        
    # Complaint Flow - Yes/No
    if msg in ["yes"]:
        return jsonify({
            "reply": "Please describe the exact location or upload a photo of the bin so we can register the complaint.",
            "suggestions": ["Cancel"]
        })
    elif msg in ["no", "cancel"]:
        return jsonify({
            "reply": "Okay, let me know if you need anything else.",
            "suggestions": primary_buttons
        })
    
    # 1. Bin Status
    elif "status" in msg or "fill" in msg or "level" in msg or "garbage" in msg:
        if "full" in msg and ("house" in msg or "my area" in msg):
            return jsonify({
                "reply": "Thanks for reporting. Do you want to register a complaint?",
                "suggestions": yes_no_buttons
            })
            
        try:
            # fetch live data
            response = requests.get(ESP32_URL, timeout=2)
            sensor = response.json()
            distance = sensor.get("distance", 0)
            fill_level = max(0, min(100, int((BIN_HEIGHT - distance) / BIN_HEIGHT * 100)))
            return jsonify({
                "reply": f"The nearest waste bin is currently {fill_level}% full.",
                "suggestions": ["📍 Nearby Bins", "⚠️ Report Complaint"]
            })
        except:
             return jsonify({
                 "reply": "Please enter your area or allow location access to check the exact bin status. (Simulated: The nearest waste bin is currently 75% full.)",
                 "suggestions": ["📍 Nearby Bins", "⚠️ Report Complaint"]
             })
             
    # 2. Nearby Bins / Location
    elif "nearby" in msg or "near me" in msg or "closest" in msg or "where" in msg:
        return jsonify({
            "reply": "There are 3 smart waste bins within 500 meters of your location. The closest one is at Rajapeth Square.",
            "suggestions": ["🗑️ Check Bin Status", "🚛 Pickup Schedule"]
        })
        
    # 3. Garbage Pickup
    elif "pickup" in msg or "collection" in msg or "schedule" in msg or "delayed" in msg:
        return jsonify({
            "reply": "Garbage collection in your area is scheduled for tomorrow morning between 7:00 AM and 9:00 AM.",
            "suggestions": ["⚠️ Report Complaint", "🗑️ Check Bin Status"]
        })
        
    # 4. Report Complaint / Issues
    elif "overflow" in msg or "missed" in msg or "complaint" in msg or "report" in msg or "damaged" in msg or "not collected" in msg:
        return jsonify({
            "reply": "Your complaint flow has been initiated. Are you reporting an overflowing bin or a missed pickup?",
            "suggestions": ["Overflowing Bin", "Missed Pickup", "Damaged Bin", "Cancel"]
        })
        
    # 5. Complaint Status
    elif "status" in msg and "complaint" in msg or "resolved" in msg or "track" in msg:
        return jsonify({
            "reply": "Please provide your Complaint ID to track its status.",
            "suggestions": ["Cancel"]
        })
        
    # Flow catch answers
    elif msg in ["overflowing bin", "missed pickup", "damaged bin"]:
        return jsonify({
            "reply": f"Thank you for reporting '{msg}'. Your complaint has been registered. Our municipal worker will address it soon.",
            "suggestions": primary_buttons
        })

    else:
        return jsonify({
            "reply": f"Did you mean to check Bin Status or Report a Complaint? I'm sorry, I didn't fully understand '{data['message']}'.",
            "suggestions": primary_buttons
        })

if __name__ == "__main__":
    # host="0.0.0.0" allows other devices in Pune/Amravati network to see the site
    app.run(host="0.0.0.0", port=5000, debug=True)
