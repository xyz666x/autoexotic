import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json
import time
import hashlib
import re

# ---------- CONFIG & SESSION STATE -----------
IST = ZoneInfo("Asia/Kolkata")
st.set_page_config(page_title="ExoticBill", page_icon="🧾")
for key, default in [
    ("logged_in", False),
    ("role", None),
    ("username", ""),
    ("bill_saved", False),
    ("bill_total", 0.0),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ---------- PRICING & DISCOUNTS -----------
ITEM_PRICES = {
    "Repair Kit": 400,
    "Car Wax": 2000,
    "NOS": 1500,
    "Adv Lockpick": 400,
    "Lockpick": 250,
    "Wash Kit": 300,
    "Harness": 12000,
}
PART_COST = 125
LABOR = 450
MEMBERSHIP_DISCOUNTS = {
    "Tier1": {"REPAIR": 0.20, "CUSTOMIZATION": 0.10},
    "Tier2": {"REPAIR": 0.33, "CUSTOMIZATION": 0.20},
    "Tier3": {"REPAIR": 0.50, "CUSTOMIZATION": 0.30},
    "Racer": {"REPAIR": 0.00, "CUSTOMIZATION": 0.00},
}
MEMBERSHIP_PRICES = {"Tier1": 2000, "Tier2": 4000, "Tier3": 6000}
COMMISSION_RATES = {
    "Trainee": 0.10,
    "Mechanic": 0.15,
    "Senior Mechanic": 0.18,
    "Lead Upgrade Specialist": 0.20,
    "Stock Manager": 0.15,
    "Manager": 0.25,
    "CEO": 0.69,
}
TAX_RATE = 0.05
LOYALTY_EARN_PER_RS = 100

# ---------- DATABASE INIT & MIGRATION -----------
def init_db():
    conn = sqlite3.connect("auto_exotic_billing.db")
    c = conn.cursor()

    def has_column(table, col):
        info = c.execute(f"PRAGMA table_info({table})").fetchall()
        return any(row[1] == col for row in info)

    c.execute("""
      CREATE TABLE IF NOT EXISTS bills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_cid TEXT,
        customer_cid TEXT,
        billing_type TEXT,
        details TEXT,
        total_amount REAL,
        timestamp TEXT
      )
    """)
    if not has_column("bills", "commission"):
        c.execute("ALTER TABLE bills ADD COLUMN commission REAL DEFAULT 0")
    if not has_column("bills", "tax"):
        c.execute("ALTER TABLE bills ADD COLUMN tax REAL DEFAULT 0")

    c.execute("""
      CREATE TABLE IF NOT EXISTS employees (
        cid TEXT PRIMARY KEY,
        name TEXT,
        rank TEXT,
        hood TEXT DEFAULT 'No Hood',
        username TEXT UNIQUE,
        password_hash TEXT
      )
    """)
    if not has_column("employees", "rank"):
        c.execute("ALTER TABLE employees ADD COLUMN rank TEXT DEFAULT 'Trainee'")
    if not has_column("employees", "hood"):
        c.execute("ALTER TABLE employees ADD COLUMN hood TEXT DEFAULT 'No Hood'")
    if not has_column("employees", "username"):
        c.execute("ALTER TABLE employees ADD COLUMN username TEXT UNIQUE")
    if not has_column("employees", "password_hash"):
        c.execute("ALTER TABLE employees ADD COLUMN password_hash TEXT")

    c.execute("""
      CREATE TABLE IF NOT EXISTS memberships (
        customer_cid TEXT PRIMARY KEY,
        tier TEXT,
        dop TEXT
      )
    """)
    c.execute("""
      CREATE TABLE IF NOT EXISTS membership_history (
        customer_cid TEXT,
        tier TEXT,
        dop TEXT,
        expired_at TEXT
      )
    """)
    c.execute("""
      CREATE TABLE IF NOT EXISTS hoods (
        name TEXT PRIMARY KEY,
        location TEXT
      )
    """)
    c.execute("""
      CREATE TABLE IF NOT EXISTS bills_deleted (
        id INTEGER,
        employee_cid TEXT,
        customer_cid TEXT,
        billing_type TEXT,
        details TEXT,
        total_amount REAL,
        timestamp TEXT,
        commission REAL,
        tax REAL,
        deleted_by TEXT,
        deleted_at TEXT
      )
    """)
    c.execute("""
      CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT,
        table_name TEXT,
        row_id TEXT,
        actor TEXT,
        ts TEXT,
        old_values TEXT,
        new_values TEXT
      )
    """)
    c.execute("""
      CREATE TABLE IF NOT EXISTS shifts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_cid TEXT,
        start_ts TEXT,
        end_ts TEXT,
        duration_minutes INTEGER,
        bills_count INTEGER,
        revenue REAL
      )
    """)
    c.execute("""
      CREATE TABLE IF NOT EXISTS loyalty (
        customer_cid TEXT PRIMARY KEY,
        points INTEGER DEFAULT 0
      )
    """)
    for stmt in [
        "CREATE INDEX idx_bills_ts ON bills(timestamp)",
        "CREATE INDEX idx_bills_emp_ts ON bills(employee_cid, timestamp)",
        "CREATE INDEX idx_bills_cust_ts ON bills(customer_cid, timestamp)",
        "CREATE INDEX idx_memberships_dop ON memberships(dop)",
        "CREATE INDEX idx_membership_hist_exp ON membership_history(expired_at)",
        "CREATE INDEX idx_employees_hood ON employees(hood)",
        "CREATE INDEX idx_shifts_emp_active ON shifts(employee_cid, end_ts)",
        "CREATE INDEX idx_loyalty_points ON loyalty(points)",
    ]:
        try:
            c.execute(stmt)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()

init_db()

# ---------- SHIFTS SCHEMA ENSURER -----------
def _ensure_shifts_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_cid TEXT,
            start_ts TEXT,
            end_ts TEXT,
            duration_minutes INTEGER,
            bills_count INTEGER,
            revenue REAL
        );
    """)
    try:
        conn.execute("CREATE INDEX idx_shifts_emp_active ON shifts(employee_cid, end_ts)")
    except sqlite3.OperationalError:
        pass

with sqlite3.connect("auto_exotic_billing.db") as _boot_conn:
    _ensure_shifts_schema(_boot_conn)

# ---------- EXPIRE MEMBERSHIPS -----------
def purge_expired_memberships():
    conn = sqlite3.connect("auto_exotic_billing.db")
    c = conn.cursor()
    cutoff_dt = datetime.now(IST) - timedelta(days=7)
    cutoff_str = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")
    expired = c.execute(
        "SELECT customer_cid, tier, dop FROM memberships WHERE dop <= ?",
        (cutoff_str,)
    ).fetchall()
    for cid, tier, dop_str in expired:
        try:
            dop = datetime.strptime(dop_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        except (ValueError, TypeError):
            dop = cutoff_dt
        expired_at = (dop + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        c.execute(
            "INSERT INTO membership_history (customer_cid, tier, dop, expired_at) VALUES (?,?,?,?)",
            (cid, tier, dop_str, expired_at)
        )
    c.execute("DELETE FROM memberships WHERE dop <= ?", (cutoff_str,))
    conn.commit()
    conn.close()

purge_expired_memberships()

# ---------- HELPERS -----------
def get_employee_rank(cid):
    conn = sqlite3.connect("auto_exotic_billing.db")
    row = conn.execute("SELECT rank FROM employees WHERE cid = ?", (cid,)).fetchone()
    conn.close()
    return row[0] if row else "Trainee"

def audit(action, table_name, row_id, actor, old_values=None, new_values=None):
    conn = sqlite3.connect("auto_exotic_billing.db")
    conn.execute("""
      INSERT INTO audit_log (action, table_name, row_id, actor, ts, old_values, new_values)
      VALUES (?,?,?,?,?,?,?)
    """, (
        action, table_name, str(row_id), actor,
        datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
        json.dumps(old_values) if old_values is not None else None,
        json.dumps(new_values) if new_values is not None else None
    ))
    conn.commit()
    conn.close()

def add_loyalty_points(customer_cid, points):
    if points <= 0:
        return
    conn = sqlite3.connect("auto_exotic_billing.db")
    cur = conn.cursor()
    row = cur.execute("SELECT points FROM loyalty WHERE customer_cid = ?", (customer_cid,)).fetchone()
    if row:
        cur.execute("UPDATE loyalty SET points = points + ? WHERE customer_cid = ?", (points, customer_cid))
    else:
        cur.execute("INSERT INTO loyalty (customer_cid, points) VALUES (?, ?)", (customer_cid, points))
    conn.commit()
    conn.close()

def save_bill(emp, cust, btype, det, amt):
    now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    no_commission = False
    if btype in ["UPGRADES", "MEMBERSHIP"]:
        no_commission = True
    elif btype == "ITEMS":
        no_commission_items = {"Harness", "NOS"}
        item_names = []
        if det:
            try:
                item_names = [i.strip().split("×")[0] for i in det.split(",") if i.strip()]
            except Exception:
                item_names = []
        if item_names and all(name in no_commission_items for name in item_names):
            no_commission = True
    if no_commission:
        commission = 0.0
        tax = 0.0
    else:
        comm_rate = COMMISSION_RATES.get(get_employee_rank(emp), 0)
        commission = amt * comm_rate
        tax = commission * TAX_RATE
    conn = sqlite3.connect("auto_exotic_billing.db")
    conn.execute("""
        INSERT INTO bills
          (employee_cid, customer_cid, billing_type, details, total_amount, timestamp, commission, tax)
        VALUES (?,?,?,?,?,?,?,?)
    """, (emp, cust, btype, det, amt, now_ist, commission, tax))
    conn.commit()
    conn.close()
    if btype != "MEMBERSHIP" and cust:
        points = int(amt // LOYALTY_EARN_PER_RS)
        add_loyalty_points(cust, points)

def add_employee(cid, name, rank="Trainee"):
    # Validate inputs
    if not cid or len(cid) < 4:
        st.warning("CID must be at least 4 characters.")
        return None, None
    if not name or len(name) < 4:
        st.warning("Name must be at least 4 characters.")
        return None, None
    if not re.match(r'^[a-zA-Z\s]+$', name):
        st.warning("Name must contain only letters and spaces.")
        return None, None

    conn = sqlite3.connect("auto_exotic_billing.db")
    try:
        conn.execute("INSERT INTO employees (cid, name, rank) VALUES (?,?,?)", (cid, name, rank))
        conn.commit()
    except sqlite3.IntegrityError:
        st.warning("Employee CID already exists.")
        conn.close()
        return None, None

    # Generate username and password
    first_name = name.split()[0] if ' ' in name else name
    username = first_name.lower()
    plain_pass = first_name[:4].lower() + cid[-4:]
    password_hash = hashlib.sha256(plain_pass.encode()).hexdigest()

    try:
        conn.execute("UPDATE employees SET username = ?, password_hash = ? WHERE cid = ?",
                     (username, password_hash, cid))
        conn.commit()
    except sqlite3.IntegrityError:
        st.warning("Generated username already exists. Please choose a different name.")
        conn.execute("DELETE FROM employees WHERE cid = ?", (cid,))
        conn.commit()
        conn.close()
        return None, None

    conn.close()
    return username, plain_pass

def delete_employee(cid):
    conn = sqlite3.connect("auto_exotic_billing.db")
    conn.execute("DELETE FROM employees WHERE cid = ?", (cid,))
    conn.commit()
    conn.close()

def update_employee(cid, name=None, rank=None, hood=None):
    before = get_employee_details(cid)
    conn = sqlite3.connect("auto_exotic_billing.db")
    if name is not None:
        if len(name) < 4:
            st.warning("Name must be at least 4 characters.")
            conn.close()
            return
        if not re.match(r'^[a-zA-Z\s]+$', name):
            st.warning("Name must contain only letters and spaces.")
            conn.close()
            return
        # Update username if name changes
        first_name = name.split()[0] if ' ' in name else name
        new_username = first_name.lower()
        try:
            conn.execute("UPDATE employees SET name = ?, username = ? WHERE cid = ?",
                         (name, new_username, cid))
        except sqlite3.IntegrityError:
            st.warning("New username conflicts with an existing one. Choose a different name.")
            conn.close()
            return
    if rank is not None:
        conn.execute("UPDATE employees SET rank = ? WHERE cid = ?", (rank, cid))
    if hood is not None:
        conn.execute("UPDATE employees SET hood = ? WHERE cid = ?", (hood, cid))
    conn.commit()
    conn.close()
    after = get_employee_details(cid)
    audit("UPDATE_EMP", "employees", cid, st.session_state.get("username", "?"), before, after)

def get_employee_details(cid):
    conn = sqlite3.connect("auto_exotic_billing.db")
    row = conn.execute("SELECT name, rank, hood, username FROM employees WHERE cid = ?", (cid,)).fetchone()
    conn.close()
    if row:
        return {"name": row[0], "rank": row[1], "hood": row[2], "username": row[3]}
    return None

def get_all_employee_cids():
    conn = sqlite3.connect("auto_exotic_billing.db")
    rows = conn.execute("SELECT cid, name FROM employees").fetchall()
    conn.close()
    return rows

def add_membership(cust, tier):
    dop_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect("auto_exotic_billing.db")
    conn.execute(
        "INSERT OR REPLACE INTO memberships (customer_cid, tier, dop) VALUES (?,?,?)",
        (cust, tier, dop_ist)
    )
    conn.commit()
    conn.close()

def get_membership(cust):
    conn = sqlite3.connect("auto_exotic_billing.db")
    row = conn.execute(
        "SELECT tier, dop FROM memberships WHERE customer_cid = ?", (cust,)
    ).fetchone()
    conn.close()
    return {"tier": row[0], "dop": row[1]} if row else None

def get_all_memberships():
    conn = sqlite3.connect("auto_exotic_billing.db")
    rows = conn.execute("SELECT customer_cid, tier, dop FROM memberships").fetchall()
    conn.close()
    return rows

def get_past_memberships():
    conn = sqlite3.connect("auto_exotic_billing.db")
    rows = conn.execute("""
        SELECT customer_cid, tier, dop, expired_at
        FROM membership_history
        ORDER BY expired_at DESC
    """).fetchall()
    conn.close()
    return rows

def get_billing_summary_by_cid(cid):
    conn = sqlite3.connect("auto_exotic_billing.db")
    summary = {}
    for bt in ["ITEMS", "UPGRADES", "REPAIR", "CUSTOMIZATION", "MEMBERSHIP"]:
        amt = conn.execute(
            "SELECT SUM(total_amount) FROM bills WHERE employee_cid=? AND billing_type=?",
            (cid, bt)
        ).fetchone()[0] or 0.0
        summary[bt] = amt
    total = conn.execute("SELECT SUM(total_amount) FROM bills WHERE employee_cid=?", (cid,)).fetchone()[0] or 0.0
    conn.close()
    return summary, total

def get_employee_bills(cid):
    conn = sqlite3.connect("auto_exotic_billing.db")
    rows = conn.execute("""
        SELECT id, customer_cid, billing_type, details,
               total_amount, timestamp, commission, tax
        FROM bills WHERE employee_cid=?
        ORDER BY timestamp DESC
    """, (cid,)).fetchall()
    conn.close()
    return rows

def get_bill_by_id(bill_id):
    conn = sqlite3.connect("auto_exotic_billing.db")
    row = conn.execute("""
        SELECT id, employee_cid, customer_cid, billing_type, details,
               total_amount, timestamp, commission, tax
        FROM bills WHERE id=?
    """, (bill_id,)).fetchone()
    conn.close()
    return row

def soft_delete_bill(bill_id, actor):
    row = get_bill_by_id(bill_id)
    if not row:
        return False
    (bid, emp, cust, btype, details, amt, ts, comm, tax) = row
    conn = sqlite3.connect("auto_exotic_billing.db")
    cur = conn.cursor()
    cur.execute("""
      INSERT INTO bills_deleted
      (id, employee_cid, customer_cid, billing_type, details, total_amount, timestamp, commission, tax, deleted_by, deleted_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        bid, emp, cust, btype, details, amt, ts, comm, tax,
        actor, datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    ))
    cur.execute("DELETE FROM bills WHERE id=?", (bill_id,))
    conn.commit()
    conn.close()
    audit("DELETE_BILL", "bills", bill_id, actor, old_values={
        "id": bid, "employee_cid": emp, "customer_cid": cust, "billing_type": btype,
        "details": details, "total_amount": amt, "timestamp": ts,
        "commission": comm, "tax": tax
    }, new_values=None)
    return True

def get_all_customers():
    conn = sqlite3.connect("auto_exotic_billing.db")
    rows = conn.execute("SELECT DISTINCT customer_cid FROM bills").fetchall()
    conn.close()
    return [r[0] for r in rows]

def get_customer_bills(cid):
    conn = sqlite3.connect("auto_exotic_billing.db")
    try:
        rows = conn.execute("""
            SELECT employee_cid, billing_type, details,
                   total_amount, timestamp, commission, tax
            FROM bills
            WHERE customer_cid = ?
            ORDER BY timestamp DESC
        """, (cid,)).fetchall()
        return rows
    finally:
        conn.close()

def get_total_billing():
    conn = sqlite3.connect("auto_exotic_billing.db")
    total = conn.execute("SELECT SUM(total_amount) FROM bills").fetchone()[0] or 0.0
    conn.close()
    return total

def get_bill_count():
    conn = sqlite3.connect("auto_exotic_billing.db")
    cnt = conn.execute("SELECT COUNT(*) FROM bills").fetchone()[0] or 0
    conn.close()
    return cnt

def get_total_commission_and_tax():
    conn = sqlite3.connect("auto_exotic_billing.db")
    row = conn.execute("SELECT SUM(commission), SUM(tax) FROM bills").fetchone()
    conn.close()
    return (row[0] or 0.0, row[1] or 0.0)

def add_hood(name, location):
    conn = sqlite3.connect("auto_exotic_billing.db")
    try:
        conn.execute("INSERT INTO hoods (name, location) VALUES (?,?)", (name, location))
        conn.commit()
    except sqlite3.IntegrityError:
        st.warning("That hood already exists.")
    conn.close()

def update_hood(old_name, new_name, new_location):
    conn = sqlite3.connect("auto_exotic_billing.db")
    c = conn.cursor()
    c.execute("UPDATE hoods SET name=?, location=? WHERE name=?", (new_name, new_location, old_name))
    c.execute("UPDATE employees SET hood=? WHERE hood=?", (new_name, old_name))
    conn.commit()
    conn.close()

def delete_hood(name):
    conn = sqlite3.connect("auto_exotic_billing.db")
    c = conn.cursor()
    c.execute("DELETE FROM hoods WHERE name=?", (name,))
    c.execute("UPDATE employees SET hood='No Hood' WHERE hood=?", (name,))
    conn.commit()
    conn.close()

def get_all_hoods():
    conn = sqlite3.connect("auto_exotic_billing.db")
    rows = conn.execute("SELECT name, location FROM hoods").fetchall()
    conn.close()
    return rows

def assign_employees_to_hood(hood, cids):
    conn = sqlite3.connect("auto_exotic_billing.db")
    for cid in cids:
        conn.execute("UPDATE employees SET hood=? WHERE cid=?", (hood, cid))
    conn.commit()
    conn.close()

def get_employees_by_hood(hood):
    conn = sqlite3.connect("auto_exotic_billing.db")
    rows = conn.execute("SELECT cid, name FROM employees WHERE hood=?", (hood,)).fetchall()
    conn.close()
    return rows

def get_bill_logs(start_str=None, end_str=None):
    conn = sqlite3.connect("auto_exotic_billing.db")
    c = conn.cursor()
    base_sql = """
        SELECT
            b.id, b.timestamp,
            COALESCE(e.name, 'Unknown') AS emp_name,
            b.employee_cid,
            COALESCE(e.hood, 'No Hood') AS hood,
            b.customer_cid, b.billing_type, b.details,
            b.total_amount, b.commission, b.tax
        FROM bills b
        LEFT JOIN employees e ON e.cid = b.employee_cid
    """
    params = ()
    if start_str and end_str:
        base_sql += " WHERE b.timestamp >= ? AND b.timestamp <= ?"
        params = (start_str, end_str)
    base_sql += " ORDER BY b.timestamp DESC"
    rows = c.execute(base_sql, params).fetchall()
    conn.close()
    return rows

def start_shift(employee_cid):
    if not (employee_cid and str(employee_cid).strip()):
        return False, "Please enter your CID first."
    conn = sqlite3.connect("auto_exotic_billing.db")
    try:
        _ensure_shifts_schema(conn)
        active = conn.execute(
            "SELECT id FROM shifts WHERE employee_cid=? AND end_ts IS NULL",
            (employee_cid,)
        ).fetchone()
        if active:
            return False, "Shift already active."
        conn.execute(
            "INSERT INTO shifts (employee_cid, start_ts) VALUES (?,?)",
            (employee_cid, datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"))
        )
        conn.commit()
    finally:
        conn.close()
    audit("SHIFT_START", "shifts", "-", st.session_state.get("username", "?"),
          new_values={"employee_cid": employee_cid})
    return True, "Shift started."

def end_shift(employee_cid):
    if not (employee_cid and str(employee_cid).strip()):
        return False, "Please enter your CID first."
    conn = sqlite3.connect("auto_exotic_billing.db")
    try:
        _ensure_shifts_schema(conn)
        row = conn.execute(
            "SELECT id, start_ts FROM shifts WHERE employee_cid=? AND end_ts IS NULL",
            (employee_cid,)
        ).fetchone()
        if not row:
            return False, "No active shift."
        sid, start_ts = row
        now = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        bills = conn.execute("""
            SELECT COUNT(*), COALESCE(SUM(total_amount),0)
            FROM bills WHERE employee_cid=? AND timestamp>=? AND timestamp<=?
        """, (employee_cid, start_ts, now)).fetchone()
        bcount, revenue = (bills[0] or 0, bills[1] or 0.0)
        dt_start = datetime.strptime(start_ts, "%Y-%m-%d %H:%M:%S")
        dt_end = datetime.strptime(now, "%Y-%m-%d %H:%M:%S")
        duration = int((dt_end - dt_start).total_seconds() // 60)
        conn.execute("""
            UPDATE shifts SET end_ts=?, duration_minutes=?, bills_count=?, revenue=?
            WHERE id=?
        """, (now, duration, bcount, revenue, sid))
        conn.commit()
    finally:
        conn.close()
    audit("SHIFT_END", "shifts", sid, st.session_state.get("username", "?"),
          old_values={"start_ts": start_ts},
          new_values={"end_ts": now, "bills": bcount, "revenue": revenue})
    return True, "Shift ended."

def login(u, p):
    if u == "owner" and p == "owner666":
        st.session_state.logged_in, st.session_state.role, st.session_state.username = True, "admin", u
        return True
    else:
        conn = sqlite3.connect("auto_exotic_billing.db")
        row = conn.execute("SELECT password_hash FROM employees WHERE username = ?", (u.lower(),)).fetchone()
        conn.close()
        if row:
            expected_hash = row[0]
            input_hash = hashlib.sha256(p.encode()).hexdigest()
            if input_hash == expected_hash:
                st.session_state.logged_in, st.session_state.role, st.session_state.username = True, "user", u
                return True
    return False

if not st.session_state.logged_in:
    st.title("🧾 ExoticBill Login")
    with st.form("login_form"):
        uname = st.text_input("Username")
        pwd = st.text_input("Password", type="password")
        if st.form_submit_button("Login"):
            if login(uname, pwd):
                st.rerun()
            else:
                st.error("Invalid credentials")
    st.stop()

with st.sidebar:
    st.success(f"Logged in as: {st.session_state.username}")
    if st.button("Logout"):
        st.session_state.clear()
        st.rerun()

# ---------- USER PANEL -----------
if st.session_state.role == "user":
    st.title("🧾 ExoticBill - Add New Bill")
    if st.session_state.bill_saved:
        st.success(f"Bill saved! Total: ₹{st.session_state.bill_total:.2f}")
        st.session_state.bill_saved = False

    st.markdown("### ⏱️ Shift Controls")
    with st.container():
        cid_for_shift = st.text_input("Your CID for Shift", key="user_shift_cid")
        colA, colB = st.columns(2)
        with colA:
            if st.button("▶️ Start Shift"):
                if cid_for_shift:
                    ok, msg = start_shift(cid_for_shift)
                    (st.success if ok else st.warning)(msg)
                else:
                    st.warning("Enter your CID to start shift.")
        with colB:
            if st.button("⏹️ End Shift"):
                if cid_for_shift:
                    ok, msg = end_shift(cid_for_shift)
                    (st.success if ok else st.warning)(msg)
                else:
                    st.warning("Enter your CID to end shift.")

    st.markdown("---")
    btype = st.selectbox("Select Billing Type", ["ITEMS", "UPGRADES", "REPAIR", "CUSTOMIZATION"])
    rtype = st.radio("Repair Type", ["Normal Repair", "Advanced Repair"]) if btype == "REPAIR" else None

    with st.form("bill_form", clear_on_submit=True):
        emp_cid = st.text_input("Your CID (Employee)")
        cust_cid = st.text_input("Customer CID")
        total, det = 0.0, ""

        if btype == "ITEMS":
            sel = {}
            for item, price in ITEM_PRICES.items():
                q = st.number_input(f"{item} (₹{price}) – Qty", min_value=0, step=1, key=f"user_items_{item}")
                if q:
                    sel[item] = q
                    total += price * q
            det = ", ".join(f"{i}×{q}" for i, q in sel.items())
        elif btype == "UPGRADES":
            amt = st.number_input("Base upgrade amount (₹)", min_value=0.0, key="user_upg_amt")
            total = amt * 1.5
            det = f"Upgrade: ₹{amt}"
        elif btype == "REPAIR":
            if rtype == "Normal Repair":
                b = st.number_input("Base repair charge (₹)", min_value=0.0, key="user_rep_base")
                total = b + LABOR
                det = f"Normal Repair: ₹{b}+₹{LABOR}"
            else:
                p = st.number_input("Number of parts repaired", min_value=0, step=1, key="user_rep_parts")
                total = p * PART_COST
                det = f"Advanced Repair: {p}×₹{PART_COST}"
        else:
            c_amt = st.number_input("Base customization amount (₹)", min_value=0.0, key="user_cust_amt")
            total = c_amt * 2
            det = f"Customization: ₹{c_amt}×2"

        mem = get_membership(cust_cid)
        if mem:
            disc = MEMBERSHIP_DISCOUNTS.get(mem["tier"], {}).get(btype, 0)
            if disc > 0:
                total *= (1 - disc)
                det += f" | {mem['tier']} discount {int(disc * 100)}%"

        if st.form_submit_button("💾 Save Bill"):
            if not emp_cid or not cust_cid or total == 0:
                st.warning("Fill all fields.")
            else:
                save_bill(emp_cid, cust_cid, btype, det, total)
                st.session_state.bill_saved = True
                st.session_state.bill_total = total

    st.markdown("---")
    st.subheader("🎟️ Manage Membership")
    with st.form("mem_form_user", clear_on_submit=True):
        m_cust = st.text_input("Customer CID", key="mem_cust")
        m_tier = st.selectbox("Tier", ["Tier1", "Tier2", "Tier3", "Racer"], key="mem_tier")
        seller_cid = st.text_input("Your CID (Seller)", key="mem_seller")
        submitted = st.form_submit_button("Add/Update Membership")
        if submitted:
            if m_cust and seller_cid and m_tier:
                add_membership(m_cust, m_tier)
                if m_tier in MEMBERSHIP_PRICES:
                    sale_amt = MEMBERSHIP_PRICES[m_tier]
                    save_bill(seller_cid, m_cust, "MEMBERSHIP", f"{m_tier} Membership", sale_amt)
                    st.success(f"{m_tier} membership updated and billed (₹{sale_amt})")
                elif m_tier == "Racer":
                    st.success("Racer membership updated (no billing).")
                else:
                    st.warning("Unknown tier.")
            else:
                st.warning("Fill all fields correctly.")

    st.subheader("🔍 Check Membership")
    lookup = st.text_input("Customer CID to check", key="mem_lookup")
    if st.button("Check Membership"):
        mem = get_membership(lookup)
        if mem:
            dop = datetime.strptime(mem["dop"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
            expiry = dop + timedelta(days=7)
            rem = expiry - datetime.now(IST)
            st.info(f"{lookup}: {mem['tier']}, expires in {rem.days}d {rem.seconds // 3600}h on {expiry.strftime('%Y-%m-%d %H:%M:%S')} IST")
        else:
            st.info(f"No active membership for {lookup}")

# ---------- ADMIN PANEL -----------
elif st.session_state.role == "admin":
    st.title("👑 ExoticBill Admin")
    st.metric("💵 Total Revenue", f"₹{get_total_billing():,.2f}")
    st.markdown("---")
    st.subheader("🧹 Maintenance")
    confirm = st.checkbox("I understand this will erase all billing history")
    if confirm and st.button("⚠️ Reset All Billings"):
        conn = sqlite3.connect("auto_exotic_billing.db")
        conn.execute("DELETE FROM bills")
        conn.commit()
        conn.close()
        st.success("All billing records have been reset.")

    menu = st.sidebar.selectbox(
        "Main Menu",
        ["Sales", "Live Stats", "Manage Hoods", "Manage Staff", "Tracking", "Bill Logs", "Hood War", "Loyalty", "Shifts", "Audit"],
        index=0
    )

    if menu == "Sales":
        st.header("💹 Sales Overview")
        total_sales = get_total_billing()
        bill_count = get_bill_count()
        avg_sale = total_sales / bill_count if bill_count else 0.0
        sum_comm, sum_tax = get_total_commission_and_tax()
        profit = total_sales - (sum_comm + sum_tax)
        st.metric("Total Sales", f"₹{total_sales:,.2f}")
        st.metric("Average Sale", f"₹{avg_sale:,.2f}")
        st.metric("Total Commission Paid", f"₹{sum_comm:,.2f}")
        st.metric("Total Tax on Commission", f"₹{sum_tax:,.2f}")
        st.metric("Estimated Profit", f"₹{profit:,.2f}")

    elif menu == "Live Stats":
        st.header("📈 Live Stats")
        auto = st.toggle("Auto-refresh every 60s", value=False)
        now = datetime.now(IST)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        last_hour = now - timedelta(hours=1)
        conn = sqlite3.connect("auto_exotic_billing.db")
        cur = conn.cursor()
        today_count = cur.execute("SELECT COUNT(*) FROM bills WHERE timestamp>=?",
                                  (today_start.strftime("%Y-%m-%d %H:%M:%S"),)).fetchone()[0] or 0
        today_amount = cur.execute("SELECT COALESCE(SUM(total_amount),0) FROM bills WHERE timestamp>=?",
                                   (today_start.strftime("%Y-%m-%d %H:%M:%S"),)).fetchone()[0] or 0.0
        hr_count = cur.execute("SELECT COUNT(*) FROM bills WHERE timestamp>=?",
                               (last_hour.strftime("%Y-%m-%d %H:%M:%S"),)).fetchone()[0] or 0
        hr_amount = cur.execute("SELECT COALESCE(SUM(total_amount),0) FROM bills WHERE timestamp>=?",
                                (last_hour.strftime("%Y-%m-%d %H:%M:%S"),)).fetchone()[0] or 0.0
        top_types = cur.execute("""
            SELECT billing_type, COUNT(*), COALESCE(SUM(total_amount),0)
            FROM bills WHERE timestamp>=?
            GROUP BY billing_type ORDER BY 3 DESC
        """, (today_start.strftime("%Y-%m-%d %H:%M:%S"),)).fetchall()
        active_shifts = cur.execute("SELECT employee_cid, start_ts FROM shifts WHERE end_ts IS NULL").fetchall()
        conn.close()
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Bills Today", f"{today_count:,}")
        with col2:
            st.metric("Revenue Today", f"₹{today_amount:,.2f}")
        with col3:
            st.metric("Active Shifts", f"{len(active_shifts)}")
        col4, col5 = st.columns(2)
        with col4:
            st.metric("Bills (Last Hour)", f"{hr_count:,}")
        with col5:
            st.metric("Revenue (Last Hour)", f"₹{hr_amount:,.2f}")
        st.subheader("Top Billing Types Today")
        if top_types:
            df_top = pd.DataFrame(top_types, columns=["Type", "Count", "Amount"]).sort_values("Amount", ascending=False)
            st.table(df_top)
        else:
            st.info("No bills yet today.")
        if active_shifts:
            st.subheader("Active Shifts")
            st.table(pd.DataFrame(active_shifts, columns=["Employee CID", "Start Time"]))
        if auto:
            time.sleep(60)
            st.rerun()

    elif menu == "Manage Hoods":
        st.header("🏙️ Manage Hoods")
        tabs = st.tabs(["Add Hood", "Edit Hood", "Assign Staff", "View Hoods"])
        with tabs[0]:
            st.subheader("➕ Add New Hood")
            with st.form("add_hood", clear_on_submit=True):
                hname = st.text_input("Hood Name")
                hloc = st.text_input("Location")
                if st.form_submit_button("Add Hood") and hname and hloc:
                    add_hood(hname, hloc)
                    st.success(f"Added hood '{hname}'")
        with tabs[1]:
            st.subheader("✏️ Edit / Delete Hood")
            hds = get_all_hoods()
            if hds:
                names = [h[0] for h in hds]
                sel = st.selectbox("Select Hood", names, key="edit_hood_sel")
                old_loc = dict(hds)[sel]
                new_name = st.text_input("New Name", sel, key="edit_hood_name")
                new_loc = st.text_input("New Location", old_loc, key="edit_hood_loc")
                if st.button("Update Hood"):
                    update_hood(sel, new_name, new_loc)
                    st.success("Hood updated.")
                if st.button("Delete Hood"):
                    delete_hood(sel)
                    st.success("Hood deleted.")
            else:
                st.info("No hoods defined yet.")
        with tabs[2]:
            st.subheader("👷 Assign Employees to Hood")
            hds = get_all_hoods()
            if hds:
                hood_names = [h[0] for h in hds]
                sel_hood = st.selectbox("Select Hood", hood_names, key="assign_hood_sel")
                all_emp = get_all_employee_cids()
                choices = {f"{n} ({c})": c for c, n in all_emp}
                sel_list = st.multiselect("Select Employees to assign", list(choices.keys()), key="assign_emp_multi")
                if st.button("Assign"):
                    assign_employees_to_hood(sel_hood, [choices[k] for k in sel_list])
                    st.success("Employees reassigned.")
            else:
                st.info("Define some hoods first.")
        with tabs[3]:
            st.subheader("🔍 View Hoods & Members")
            hds = get_all_hoods()
            if hds:
                for name, loc in hds:
                    with st.expander(f"{name} — {loc}"):
                        emps = get_employees_by_hood(name)
                        if emps:
                            st.table(pd.DataFrame(emps, columns=["CID", "Name"]))
                        else:
                            st.write("No employees assigned.")
            else:
                st.info("No hoods to view.")

    elif menu == "Manage Staff":
        st.header("👷 Manage Staff")
        tabs = st.tabs(["➕ Add Employee", "🗑️ Remove Employee", "✏️ Edit Employee", "📋 View All Employees"])
        with tabs[0]:
            st.subheader("➕ Add New Employee")
            with st.form("add_emp", clear_on_submit=True):
                new_cid = st.text_input("Employee CID")
                new_name = st.text_input("Name")
                new_rank = st.selectbox("Rank", list(COMMISSION_RATES.keys()))
                hds = [h[0] for h in get_all_hoods()] or []
                new_hood = st.selectbox("Hood", ["No Hood"] + hds)
                if st.form_submit_button("Add Employee"):
                    if new_cid and new_name:
                        username, plain_pass = add_employee(new_cid, new_name, new_rank)
                        if username:
                            if new_hood != "No Hood":
                                update_employee(new_cid, hood=new_hood)
                            st.success(f"Added {new_name} ({new_cid})\nUsername: {username}\nPassword: {plain_pass}")
                        else:
                            st.warning("Failed to add employee.")
                    else:
                        st.warning("CID and Name required.")
        with tabs[1]:
            st.subheader("🗑️ Remove Employee")
            all_emp = get_all_employee_cids()
            if all_emp:
                opts = {f"{n} ({c})": c for c, n in all_emp}
                sel = st.selectbox("Select Employee to Remove", list(opts.keys()), key="rm_emp_sel")
                if st.button("Delete Employee"):
                    delete_employee(opts[sel])
                    st.success(f"Removed {sel}")
            else:
                st.info("No employees to remove.")
        with tabs[2]:
            st.subheader("✏️ Edit Employee")
            all_emp = get_all_employee_cids()
            if not all_emp:
                st.info("No employees to edit.")
            else:
                labels = [f"{name} ({cid})" for cid, name in all_emp]
                cid_map = {f"{name} ({cid})": cid for cid, name in all_emp}
                sel_emp = st.selectbox("Select Employee", labels, key="edit_emp_sel")
                if sel_emp:
                    emp_cid = cid_map[sel_emp]
                    details = get_employee_details(emp_cid)
                    if not details:
                        st.warning("Could not load details for this employee.")
                    else:
                        current_name = details.get("name") or ""
                        current_rank = details.get("rank") or "Trainee"
                        rank_options = list(COMMISSION_RATES.keys())
                        try:
                            rank_index = rank_options.index(current_rank)
                        except ValueError:
                            rank_index = 0
                        hds = [h[0] for h in get_all_hoods()] or []
                        hood_options = ["No Hood"] + hds
                        current_hood = details.get("hood") or "No Hood"
                        try:
                            hood_index = hood_options.index(current_hood)
                        except ValueError:
                            hood_options = [current_hood] + hood_options
                            hood_index = 0
                        with st.form("edit_emp", clear_on_submit=True):
                            name = st.text_input("Name", value=current_name)
                            rank = st.selectbox("Rank", rank_options, index=rank_index)
                            hood = st.selectbox("Hood", hood_options, index=hood_index)
                            submitted = st.form_submit_button("Update Employee")
                            if submitted:
                                update_employee(emp_cid, name=name, rank=rank, hood=hood)
                                st.success(f"Updated {sel_emp}")
                                st.rerun()
        with tabs[3]:
            st.subheader("📋 All Employees List")
            all_rows = []
            for cid, name in get_all_employee_cids():
                details = get_employee_details(cid)
                if details:
                    all_rows.append({
                        "CID": cid,
                        "Name": name,
                        "Rank": details["rank"],
                        "Hood": details["hood"],
                        "Username": details["username"]
                    })
            if all_rows:
                df = pd.DataFrame(all_rows)
                st.dataframe(df)
            else:
                st.info("No employees found.")

    elif menu == "Tracking":
        st.header("📊 Tracking")
        tabs = st.tabs(["Employee", "Customer", "Hood", "Membership", "Employee Rankings", "Custom Filter"])
        with tabs[0]:
            st.subheader("Employee Billing")
            ranks = ["All"] + list(COMMISSION_RATES.keys())
            sel_rank = st.selectbox("Filter by Rank", ranks)
            all_emps = get_all_employee_cids()
            if sel_rank != "All":
                all_emps = [(cid, name) for cid, name in all_emps if get_employee_rank(cid) == sel_rank]
            emp_keys = [f"{n} ({c})" for c, n in all_emps]
            if not emp_keys:
                st.info("No employees match that rank.")
            else:
                sel = st.selectbox("Select Employee", emp_keys)
                view = st.radio("View", ["Overall", "Detailed"], horizontal=True)
                cid = dict(zip(emp_keys, [c for c, _ in all_emps]))[sel]
                if view == "Overall":
                    summary, total = get_billing_summary_by_cid(cid)
                    for k, v in summary.items():
                        st.metric(k, f"₹{v:.2f}")
                    st.metric("Total", f"₹{total:.2f}")
                else:
                    bills = get_employee_bills(cid)
                    if bills:
                        st.subheader("📋 Bill Entries")
                        for bill in bills:
                            bill_id, cust, btype, details, amt, ts, comm, tax = bill
                            col1, col2 = st.columns([9, 1])
                            with col1:
                                st.markdown(
                                    f"**ID:** `{bill_id}` | **Customer:** `{cust}` | **Type:** `{btype}`  \n"
                                    f"**Details:** {details}  \n"
                                    f"**Amount:** ₹{amt:.2f} | **Commission:** ₹{comm:.2f} | **Tax:** ₹{tax:.2f}  \n"
                                    f"🕒 {ts}"
                                )
                            with col2:
                                if st.button("🗑️", key=f"del_{bill_id}"):
                                    ok = soft_delete_bill(bill_id, st.session_state.get("username", "?"))
                                    if ok:
                                        st.success(f"Deleted bill ID {bill_id} (soft delete)")
                                        st.rerun()
                                    else:
                                        st.warning("Bill not found.")
                    else:
                        st.info("No bills found for this employee.")
        with tabs[1]:
            st.subheader("Customer Billing History")
            customers = get_all_customers()
            if customers:
                cust = st.selectbox("Select Customer", customers)
                df = pd.DataFrame(get_customer_bills(cust),
                                  columns=["Employee", "Type", "Details", "Amount", "Time", "Commission", "Tax"])
                st.dataframe(df)
            else:
                st.info("No customer billing data yet.")
        with tabs[2]:
            st.subheader("Hood Summary")
            hood_names = [h[0] for h in get_all_hoods()]
            if hood_names:
                sel_hood = st.selectbox("Select Hood", hood_names)
                rows = []
                for cid, name in get_employees_by_hood(sel_hood):
                    _, tot = get_billing_summary_by_cid(cid)
                    rows.append({"CID": cid, "Name": name, "Total": tot})
                st.table(pd.DataFrame(rows))
            else:
                st.info("No hoods found.")
        with tabs[3]:
            st.subheader("📋 Memberships")
            view = st.radio("Show", ["Active", "Past"], horizontal=True)
            if view == "Active":
                rows = get_all_memberships()
                data = []
                for cid, tier, dop_str in rows:
                    dop = datetime.strptime(dop_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
                    expiry = dop + timedelta(days=7)
                    rem = expiry - datetime.now(IST)
                    data.append({
                        "Customer CID": cid,
                        "Tier": tier,
                        "Started On": dop.strftime("%Y-%m-%d %H:%M:%S"),
                        "Expires On": expiry.strftime("%Y-%m-%d %H:%M:%S"),
                        "Remaining": f"{rem.days}d {rem.seconds // 3600}h"
                    })
                st.table(pd.DataFrame(data))
                st.markdown("---")
                st.subheader("🗑️ Delete a Membership")
                mem_options = {f"{cid} ({tier})": cid for cid, tier, _ in rows}
                if mem_options:
                    sel_mem = st.selectbox("Select membership to delete", list(mem_options.keys()))
                    if st.button("Delete Selected Membership"):
                        cid_to_delete = mem_options[sel_mem]
                        conn = sqlite3.connect("auto_exotic_billing.db")
                        conn.execute("DELETE FROM memberships WHERE customer_cid = ?", (cid_to_delete,))
                        conn.commit()
                        conn.close()
                        st.success(f"Deleted membership for {cid_to_delete}.")
                        st.rerun()
                else:
                    st.info("No active memberships found.")
            else:
                rows = get_past_memberships()
                data = []
                for cid, tier, dop_str, expired_str in rows:
                    data.append({
                        "Customer CID": cid,
                        "Tier": tier,
                        "Started On": dop_str,
                        "Expired At": expired_str
                    })
                st.table(pd.DataFrame(data))
        with tabs[4]:
            st.subheader("🏆 Employee Rankings")
            metric = st.selectbox("Select ranking metric",
                                  ["Total Sales", "ITEMS", "UPGRADES", "REPAIR", "CUSTOMIZATION", "MEMBERSHIP"])
            ranking = []
            conn = sqlite3.connect("auto_exotic_billing.db")
            for cid, name in get_all_employee_cids():
                if metric == "Total Sales":
                    q = "SELECT SUM(total_amount) FROM bills WHERE employee_cid=?"
                    params = (cid,)
                else:
                    q = ("SELECT SUM(total_amount) FROM bills "
                         "WHERE employee_cid=? AND billing_type=?")
                    params = (cid, metric)
                val = conn.execute(q, params).fetchone()[0] or 0.0
                ranking.append({"Employee": f"{name} ({cid})", metric: val})
            conn.close()
            df_rank = pd.DataFrame(ranking).sort_values(by=metric, ascending=False)
            st.table(df_rank.head(100))
        with tabs[5]:
            st.subheader("🔍 Custom Sales Filter")
            days = st.number_input("Last X days", min_value=1, max_value=30, value=7)
            min_sales = st.number_input("Min sales amount (₹)", min_value=0.0, value=0.0)
            if st.button("Apply Filter"):
                cutoff = datetime.now(IST) - timedelta(days=days)
                results = []
                conn = sqlite3.connect("auto_exotic_billing.db")
                for cid, name in get_all_employee_cids():
                    q = ("SELECT SUM(total_amount) FROM bills "
                         "WHERE employee_cid=? AND timestamp>=?")
                    total = conn.execute(q, (cid, cutoff.strftime("%Y-%m-%d %H:%M:%S"))).fetchone()[0] or 0.0
                    if total >= min_sales:
                        results.append({"Employee": f"{name} ({cid})",
                                        f"Sales in last {days}d": total})
                conn.close()
                if results:
                    st.table(pd.DataFrame(results))
                else:
                    st.info("No employees match that filter.")

    elif menu == "Bill Logs":
        st.header("🧾 Bill Logs")
        now = datetime.now(IST)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = now.replace(hour=23, minute=59, second=59, microsecond=0)
        quick_range = st.selectbox(
            "Quick Date Range",
            ["Today", "Yesterday", "Last 2 days", "Last 7 days", "This Month", "Custom"]
        )
        start_dt, end_dt = None, None
        if quick_range == "Today":
            start_dt, end_dt = today_start, today_end
        elif quick_range == "Yesterday":
            y = today_start - timedelta(days=1)
            start_dt = y
            end_dt = y.replace(hour=23, minute=59, second=59)
        elif quick_range == "Last 2 days":
            start_dt, end_dt = (now - timedelta(days=2)), now
        elif quick_range == "Last 7 days":
            start_dt, end_dt = (now - timedelta(days=7)), now
        elif quick_range == "This Month":
            first_of_month = today_start.replace(day=1)
            start_dt, end_dt = first_of_month, today_end
        else:
            st.markdown("### Custom Range")
            colA, colB = st.columns(2)
            with colA:
                sd = st.date_input("Start date", value=today_start.date(), key="bill_logs_sd")
            with colB:
                ed = st.date_input("End date", value=today_end.date(), key="bill_logs_ed")
            colC, colD = st.columns(2)
            with colC:
                sh = st.number_input("Start hour", min_value=0, max_value=23, value=0, key="bill_logs_sh")
            with colD:
                eh = st.number_input("End hour", min_value=0, max_value=23, value=23, key="bill_logs_eh")
            start_dt = datetime(sd.year, sd.month, sd.day, sh, 0, 0, tzinfo=IST)
            end_dt = datetime(ed.year, ed.month, ed.day, eh, 59, 59, tzinfo=IST)
        start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
        end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")
        st.markdown("### Filters")
        col1, col2, col3 = st.columns([2, 2, 2])
        with col1:
            type_filter = st.multiselect(
                "Billing Type",
                ["ITEMS", "UPGRADES", "REPAIR", "CUSTOMIZATION", "MEMBERSHIP"],
                default=[],
                key="bill_logs_typefilter"
            )
        with col2:
            emp_query = st.text_input("Employee (name or CID) contains", key="bill_logs_empq")
        with col3:
            cust_query = st.text_input("Customer CID contains", key="bill_logs_custq")
        rows = get_bill_logs(start_str, end_str)
        df = pd.DataFrame(rows, columns=[
            "ID", "Time", "Employee Name", "Employee CID", "Hood",
            "Customer CID", "Type", "Details", "Amount", "Commission", "Tax"
        ])
        if type_filter:
            df = df[df["Type"].isin(type_filter)]
        if emp_query:
            emp_query_low = emp_query.lower()
            df = df[
                df["Employee Name"].str.lower().str.contains(emp_query_low, na=False) |
                df["Employee CID"].str.lower().str.contains(emp_query_low, na=False)
            ]
        if cust_query:
            df = df[df["Customer CID"].str.lower().str.contains(cust_query.lower(), na=False)]
        total_amt = df["Amount"].sum() if not df.empty else 0.0
        total_comm = df["Commission"].sum() if not df.empty else 0.0
        total_tax = df["Tax"].sum() if not df.empty else 0.0
        st.markdown(
            f"**Showing {len(df):,} bill(s)** from **{start_str}** to **{end_str}**  \n"
            f"**Total Amount:** ₹{total_amt:,.2f} | **Total Commission:** ₹{total_comm:,.2f} | **Total Tax:** ₹{total_tax:,.2f}"
        )
        st.dataframe(df, use_container_width=True)
        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Download CSV",
            data=csv,
            file_name=f"bill_logs_{start_str.replace(':','-')}_to_{end_str.replace(':','-')}.csv",
            mime="text/csv",
            key="bill_logs_dl"
        )

    elif menu == "Hood War":
        st.header("⚔️ Hood War — Revenue Leaderboard")
        now = datetime.now(IST)
        default_start = (now - timedelta(days=7)).date()
        default_end = now.date()
        colA, colB = st.columns(2)
        with colA:
            sd = st.date_input("Start date", value=default_start, key="war_sd")
        with colB:
            ed = st.date_input("End date", value=default_end, key="war_ed")
        start_str = datetime(sd.year, sd.month, sd.day, 0, 0, 0, tzinfo=IST).strftime("%Y-%m-%d %H:%M:%S")
        end_str = datetime(ed.year, ed.month, ed.day, 23, 59, 59, tzinfo=IST).strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect("auto_exotic_billing.db")
        rows = conn.execute("""
          SELECT e.hood, COALESCE(SUM(b.total_amount),0) AS revenue
          FROM employees e
          LEFT JOIN bills b ON b.employee_cid = e.cid
            AND b.timestamp >= ? AND b.timestamp <= ?
          GROUP BY e.hood
          ORDER BY revenue DESC
        """, (start_str, end_str)).fetchall()
        conn.close()
        df = pd.DataFrame(rows, columns=["Hood", "Revenue"]).sort_values("Revenue", ascending=False)
        st.table(df)

    elif menu == "Loyalty":
        st.header("🎯 Customer Loyalty")
        st.caption(f"Earning rate: 1 point per ₹{LOYALTY_EARN_PER_RS} on non-membership bills")
        conn = sqlite3.connect("auto_exotic_billing.db")
        top = conn.execute("SELECT customer_cid, points FROM loyalty ORDER BY points DESC LIMIT 100").fetchall()
        conn.close()
        if top:
            st.subheader("Top Customers")
            st.table(pd.DataFrame(top, columns=["Customer CID", "Points"]))
        else:
            st.info("No loyalty data yet.")
        st.markdown("---")
        st.subheader("Adjust Points (Admin)")
        with st.form("loyalty_adjust", clear_on_submit=True):
            cust = st.text_input("Customer CID")
            delta = st.number_input("Add/Subtract Points (e.g., 50 or -20)", value=0, step=1)
            submitted = st.form_submit_button("Apply")
            if submitted:
                if not cust or delta == 0:
                    st.warning("Enter CID and non-zero delta.")
                else:
                    add_loyalty_points(cust, int(delta))
                    st.success("Points updated.")
        st.markdown("---")
        st.subheader("Lookup Customer Points")
        lookup = st.text_input("Customer CID", key="loy_lookup")
        if st.button("Check Points"):
            conn = sqlite3.connect("auto_exotic_billing.db")
            row = conn.execute("SELECT points FROM loyalty WHERE customer_cid=?", (lookup,)).fetchone()
            conn.close()
            pts = row[0] if row else 0
            st.info(f"{lookup} has **{pts}** loyalty points.")

    elif menu == "Shifts":
        st.header("⏱️ Shifts")
        tab_emp, tab_live = st.tabs(["By Employee", "Live Shifts"])
        with tab_emp:
            all_emp = get_all_employee_cids()
            if not all_emp:
                st.info("No employees found.")
            else:
                labels = [f"{name} ({cid})" for cid, name in all_emp]
                cid_map = {f"{name} ({cid})": cid for cid, name in all_emp}
                sel_label = st.selectbox("Select Employee", labels, key="shift_emp_picker")
                sel_cid = cid_map[sel_label]
                now = datetime.now(IST)
                colA, colB = st.columns(2)
                with colA:
                    sd = st.date_input("From", value=(now - timedelta(days=7)).date(), key="shift_emp_sd")
                with colB:
                    ed = st.date_input("To", value=now.date(), key="shift_emp_ed")
                start_str = datetime(sd.year, sd.month, sd.day, 0, 0, 0, tzinfo=IST).strftime("%Y-%m-%d %H:%M:%S")
                end_str = datetime(ed.year, ed.month, ed.day, 23, 59, 59, tzinfo=IST).strftime("%Y-%m-%d %H:%M:%S")
                conn = sqlite3.connect("auto_exotic_billing.db")
                rows = conn.execute(
                    """
                    SELECT s.id,
                           s.employee_cid,
                           COALESCE(e.name, 'Unknown') AS employee_name,
                           s.start_ts, s.end_ts,
                           s.duration_minutes, s.bills_count, s.revenue
                    FROM shifts s
                    LEFT JOIN employees e ON e.cid = s.employee_cid
                    WHERE s.employee_cid = ?
                      AND s.start_ts >= ?
                      AND s.start_ts <= ?
                    ORDER BY COALESCE(s.end_ts, s.start_ts) DESC
                    """,
                    (sel_cid, start_str, end_str)
                ).fetchall()
                conn.close()
                df = pd.DataFrame(
                    rows,
                    columns=[
                        "ID", "Employee CID", "Employee Name", "Start", "End",
                        "Duration (min)", "Bills", "Revenue"
                    ]
                )
                if not df.empty:
                    total_shifts = len(df)
                    total_minutes = int(df["Duration (min)"].fillna(0).sum())
                    total_bills = int(df["Bills"].fillna(0).sum())
                    total_rev = float(df["Revenue"].fillna(0).sum())
                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("Shifts", f"{total_shifts}")
                    col2.metric("Minutes", f"{total_minutes:,}")
                    col3.metric("Bills", f"{total_bills:,}")
                    col4.metric("Revenue", f"₹{total_rev:,.2f}")
                st.dataframe(df, use_container_width=True)
        with tab_live:
            st.subheader("Active (Live) Shifts")
            auto = st.toggle("Auto-refresh every 60s", value=False, key="shifts_live_auto")
            conn = sqlite3.connect("auto_exotic_billing.db")
            live = conn.execute(
                """
                SELECT s.employee_cid, COALESCE(e.name, 'Unknown') AS employee_name, s.start_ts
                FROM shifts s
                LEFT JOIN employees e ON e.cid = s.employee_cid
                WHERE s.end_ts IS NULL
                ORDER BY s.start_ts ASC
                """
            ).fetchall()
            conn.close()
            if live:
                data = []
                now_ist = datetime.now(IST)
                for cid, name, start_ts in live:
                    try:
                        dt_start = datetime.strptime(start_ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
                    except Exception:
                        dt_start = now_ist
                    elapsed_min = int((now_ist - dt_start).total_seconds() // 60)
                    data.append({
                        "Employee Name": name,
                        "Employee CID": cid,
                        "Start Time": start_ts,
                        "Elapsed (min)": elapsed_min
                    })
                st.table(pd.DataFrame(data).sort_values("Elapsed (min)", ascending=False))
            else:
                st.info("No active shifts.")
            if auto:
                time.sleep(60)
                st.rerun()

    elif menu == "Audit":
        st.header("🛡️ Audit Log")
        conn = sqlite3.connect("auto_exotic_billing.db")
        rows = conn.execute("""
          SELECT action, table_name, row_id, actor, ts, old_values, new_values
          FROM audit_log ORDER BY ts DESC LIMIT 500
        """).fetchall()
        conn.close()
        if rows:
            df = pd.DataFrame(rows, columns=["Action", "Table", "Row ID", "Actor", "Time", "Old", "New"])
            st.dataframe(df, use_container_width=True)
        else:
            st.info("Audit log is empty.")
