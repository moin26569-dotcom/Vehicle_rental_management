"""Vehicle Rental Management System
Flow: Streamlit UI -> Python logic -> SQL -> PostgreSQL -> Python -> Streamlit UI
"""
import os
import datetime as dt
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras
import streamlit as st

st.set_page_config(page_title="Vehicle Rental Management", page_icon="🚗", layout="wide")

VEHICLE_TYPES = ["Car", "SUV", "Bike", "Scooter", "Van", "Truck"]
STATUSES = ["Available", "Rented", "Maintenance"]
METHODS = ["Cash", "UPI", "Card", "Bank Transfer"]


# ----------------------------------------------------------------------------
# Database layer (Python -> SQL -> PostgreSQL)
# ----------------------------------------------------------------------------
def db_url():
    try:
        return st.secrets["DATABASE_URL"]
    except Exception:
        return os.environ.get("DATABASE_URL")


def connect():
    return psycopg2.connect(db_url())


def query(sql, params=None):
    """Run a SELECT and return a pandas DataFrame."""
    conn = connect()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return pd.DataFrame(rows)
    finally:
        conn.close()


def execute(sql, params=None):
    """Run INSERT / UPDATE / DELETE inside a transaction."""
    conn = connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
    finally:
        conn.close()


@st.cache_resource
def init_db():
    execute((Path(__file__).parent / "schema.sql").read_text())
    return True


# ----------------------------------------------------------------------------
# UI helpers
# ----------------------------------------------------------------------------
def flash(msg, kind="success"):
    st.session_state["flash"] = (kind, msg)


def show_flash():
    f = st.session_state.pop("flash", None)
    if f:
        getattr(st, f[0])(f[1])


def act(fn, *args, ok="Done"):
    """Run a DB action, show friendly errors, refresh the page on success."""
    try:
        fn(*args)
    except ValueError as e:
        st.error(str(e))
        return
    except psycopg2.errors.UniqueViolation:
        st.error("Duplicate value: vehicle number, email or license number already exists.")
        return
    except psycopg2.errors.ForeignKeyViolation:
        st.error("Cannot delete: this record is linked to rentals (foreign key constraint).")
        return
    except Exception as e:
        st.error(f"Database error: {e}")
        return
    flash(ok)
    st.rerun()


def pick(label, df, id_col, text_fn, key):
    if df.empty:
        st.info("No records available.")
        return None
    opts = {text_fn(r): int(r[id_col]) for _, r in df.iterrows()}
    return opts[st.selectbox(label, list(opts), key=key)]


def customers_df():
    return query("SELECT customer_id, name, phone, email, license_no, address FROM customers ORDER BY name")


def vehicles_df(where="", params=None):
    return query(f"SELECT * FROM vehicles {where} ORDER BY vehicle_number", params)


def cust_label(r):
    return f"{r['name']} ({r['license_no']})"


def veh_label(r):
    return f"{r['vehicle_number']} - {r['brand']} {r['model']} [{r['status']}]"


# ----------------------------------------------------------------------------
# Business logic
# ----------------------------------------------------------------------------
def rent_vehicle(cid, vid, start, end, advance, method):
    if end < start:
        raise ValueError("Expected return date cannot be before the rent date.")
    conn = connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status, daily_rate FROM vehicles WHERE vehicle_id=%s FOR UPDATE", (vid,))
                row = cur.fetchone()
                if not row:
                    raise ValueError("Vehicle not found.")
                if row[0] != "Available":
                    raise ValueError(f"Vehicle is not available (current status: {row[0]}).")
                amount = max(1, (end - start).days) * row[1]
                cur.execute(
                    """INSERT INTO rentals(customer_id, vehicle_id, rent_date, expected_return_date,
                                           expected_amount, status)
                       VALUES (%s,%s,%s,%s,%s,'Active') RETURNING rental_id""",
                    (cid, vid, start, end, amount),
                )
                rid = cur.fetchone()[0]
                cur.execute("UPDATE vehicles SET status='Rented' WHERE vehicle_id=%s", (vid,))
                if advance and advance > 0:
                    cur.execute(
                        "INSERT INTO payments(rental_id, amount, payment_date, method) VALUES (%s,%s,%s,%s)",
                        (rid, advance, start, method),
                    )
    finally:
        conn.close()


def return_vehicle(rid, actual, pay, method):
    conn = connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT vehicle_id, rent_date, status FROM rentals WHERE rental_id=%s FOR UPDATE", (rid,)
                )
                row = cur.fetchone()
                if not row or row[2] != "Active":
                    raise ValueError("This rental is not active.")
                vid, start, _ = row
                if actual < start:
                    raise ValueError("Return date cannot be before the rent date.")
                cur.execute("SELECT daily_rate FROM vehicles WHERE vehicle_id=%s", (vid,))
                final = max(1, (actual - start).days) * cur.fetchone()[0]
                cur.execute(
                    """UPDATE rentals SET actual_return_date=%s, final_amount=%s, status='Completed'
                       WHERE rental_id=%s""",
                    (actual, final, rid),
                )
                cur.execute("UPDATE vehicles SET status='Available' WHERE vehicle_id=%s", (vid,))
                if pay and pay > 0:
                    cur.execute(
                        "INSERT INTO payments(rental_id, amount, payment_date, method) VALUES (%s,%s,%s,%s)",
                        (rid, pay, actual, method),
                    )
    finally:
        conn.close()


def add_payment(rid, amount, method, balance):
    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")
    if amount > balance:
        raise ValueError(f"Amount exceeds the pending balance (Rs. {balance:,.2f}).")
    execute(
        "INSERT INTO payments(rental_id, amount, payment_date, method) VALUES (%s,%s,CURRENT_DATE,%s)",
        (rid, amount, method),
    )


def add_maintenance(vid, desc, cost, mdate):
    conn = connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM vehicles WHERE vehicle_id=%s FOR UPDATE", (vid,))
                if cur.fetchone()[0] == "Rented":
                    raise ValueError("Vehicle is currently rented. Return it before sending to maintenance.")
                cur.execute(
                    "INSERT INTO maintenance(vehicle_id, description, cost, maintenance_date, status) "
                    "VALUES (%s,%s,%s,%s,'Ongoing')",
                    (vid, desc, cost, mdate),
                )
                cur.execute("UPDATE vehicles SET status='Maintenance' WHERE vehicle_id=%s", (vid,))
    finally:
        conn.close()


def complete_maintenance(mid):
    conn = connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE maintenance SET status='Completed' WHERE maintenance_id=%s RETURNING vehicle_id", (mid,)
                )
                vid = cur.fetchone()[0]
                cur.execute(
                    """UPDATE vehicles SET status='Available'
                       WHERE vehicle_id=%s AND NOT EXISTS
                         (SELECT 1 FROM maintenance WHERE vehicle_id=%s AND status='Ongoing')""",
                    (vid, vid),
                )
    finally:
        conn.close()


def load_sample_data():
    today = dt.date.today()
    customers = [
        ("Rahul Sharma", "9876543210", "rahul@example.com", "MH1220190001", "Kothrud, Pune"),
        ("Priya Patil", "9823012345", "priya@example.com", "MH1420200002", "Baner, Pune"),
        ("Amit Verma", "9765432109", "amit@example.com", "DL0820180003", "Dwarka, Delhi"),
        ("Sneha Joshi", "9890011223", "sneha@example.com", "MH1220210004", "Aundh, Pune"),
        ("Karan Mehta", "9988776655", "karan@example.com", "GJ0120170005", "Satellite, Ahmedabad"),
    ]
    vehicles = [
        ("MH12AB1001", "Maruti", "Swift", "Car", 1500),
        ("MH12AB1002", "Hyundai", "Creta", "SUV", 2500),
        ("MH12AB1003", "Toyota", "Innova", "SUV", 3000),
        ("MH12AB1004", "Honda", "City", "Car", 1800),
        ("MH12AB1005", "Royal Enfield", "Classic 350", "Bike", 800),
        ("MH12AB1006", "Honda", "Activa", "Scooter", 400),
        ("MH12AB1007", "Tata", "Nexon", "SUV", 2200),
        ("MH12AB1008", "Mahindra", "Thar", "SUV", 2800),
        ("MH12AB1009", "Tata", "Ace", "Truck", 2000),
        ("MH12AB1010", "Maruti", "Baleno", "Car", 1400),
    ]
    # (license, vehicle, days_ago_started, planned_days, actual_days or None if still active)
    rentals = [
        ("MH1220190001", "MH12AB1001", 70, 3, 3), ("MH1420200002", "MH12AB1002", 62, 5, 6),
        ("DL0820180003", "MH12AB1003", 55, 2, 2), ("MH1220190001", "MH12AB1004", 40, 4, 4),
        ("MH1220210004", "MH12AB1005", 33, 3, 2), ("GJ0120170005", "MH12AB1001", 25, 2, 2),
        ("MH1420200002", "MH12AB1007", 18, 6, 7), ("DL0820180003", "MH12AB1002", 12, 3, 3),
        ("MH1220190001", "MH12AB1008", 3, 5, None), ("MH1220210004", "MH12AB1003", 9, 4, None),
    ]
    conn = connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM vehicles")
                if cur.fetchone()[0] > 0:
                    raise ValueError("Sample data can only be loaded into an empty database.")
                cur.executemany(
                    "INSERT INTO customers(name, phone, email, license_no, address) VALUES (%s,%s,%s,%s,%s)",
                    customers,
                )
                cur.executemany(
                    "INSERT INTO vehicles(vehicle_number, brand, model, vehicle_type, daily_rate) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    vehicles,
                )
                for lic, vn, ago, plan, act_days in rentals:
                    cur.execute("SELECT customer_id FROM customers WHERE license_no=%s", (lic,))
                    cid = cur.fetchone()[0]
                    cur.execute("SELECT vehicle_id, daily_rate FROM vehicles WHERE vehicle_number=%s", (vn,))
                    vid, rate = cur.fetchone()
                    start = today - dt.timedelta(days=ago)
                    end = start + dt.timedelta(days=plan)
                    expected = max(1, plan) * rate
                    if act_days is not None:
                        actual, final, status = start + dt.timedelta(days=act_days), max(1, act_days) * rate, "Completed"
                    else:
                        actual, final, status = None, None, "Active"
                    cur.execute(
                        """INSERT INTO rentals(customer_id, vehicle_id, rent_date, expected_return_date,
                               actual_return_date, expected_amount, final_amount, status)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING rental_id""",
                        (cid, vid, start, end, actual, expected, final, status),
                    )
                    rid = cur.fetchone()[0]
                    if status == "Completed":
                        cur.execute(
                            "INSERT INTO payments(rental_id, amount, payment_date, method) VALUES (%s,%s,%s,%s)",
                            (rid, final, actual, "UPI"),
                        )
                    else:
                        cur.execute("UPDATE vehicles SET status='Rented' WHERE vehicle_id=%s", (vid,))
                        cur.execute(
                            "INSERT INTO payments(rental_id, amount, payment_date, method) VALUES (%s,%s,%s,%s)",
                            (rid, rate, start, "Cash"),
                        )
                cur.execute("SELECT vehicle_id FROM vehicles WHERE vehicle_number='MH12AB1010'")
                vid = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO maintenance(vehicle_id, description, cost, maintenance_date, status) VALUES "
                    "(%s,'Engine service and brake pad change',6500,%s,'Ongoing')",
                    (vid, today - dt.timedelta(days=2)),
                )
                cur.execute("UPDATE vehicles SET status='Maintenance' WHERE vehicle_id=%s", (vid,))
                cur.execute("SELECT vehicle_id FROM vehicles WHERE vehicle_number='MH12AB1004'")
                cur.execute(
                    "INSERT INTO maintenance(vehicle_id, description, cost, maintenance_date, status) VALUES "
                    "(%s,'Tyre replacement',8000,%s,'Completed')",
                    (cur.fetchone()[0], today - dt.timedelta(days=30)),
                )
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# Page 1: Dashboard
# ----------------------------------------------------------------------------
def page_dashboard():
    st.header("📊 Dashboard")
    s = query(
        """SELECT
             (SELECT COUNT(*) FROM vehicles)                              AS total_vehicles,
             (SELECT COUNT(*) FROM vehicles WHERE status='Available')     AS available,
             (SELECT COUNT(*) FROM vehicles WHERE status='Rented')        AS rented,
             (SELECT COUNT(*) FROM vehicles WHERE status='Maintenance')   AS maintenance,
             (SELECT COUNT(*) FROM customers)                             AS customers,
             (SELECT COUNT(*) FROM rentals WHERE status='Active')         AS active_rentals,
             (SELECT COALESCE(SUM(amount),0) FROM payments)::float        AS revenue"""
    ).iloc[0]
    c = st.columns(4)
    c[0].metric("Total Vehicles", int(s.total_vehicles))
    c[1].metric("Available", int(s.available))
    c[2].metric("Rented", int(s.rented))
    c[3].metric("Under Maintenance", int(s.maintenance))
    c = st.columns(3)
    c[0].metric("Total Customers", int(s.customers))
    c[1].metric("Active Rentals", int(s.active_rentals))
    c[2].metric("Total Revenue", f"Rs. {s.revenue:,.0f}")

    st.subheader("Recent Rentals")
    recent = query(
        """SELECT r.rental_id AS id, c.name AS customer, v.vehicle_number AS vehicle,
                  v.brand || ' ' || v.model AS model, r.rent_date, r.expected_return_date,
                  r.actual_return_date, COALESCE(r.final_amount, r.expected_amount)::float AS amount, r.status
           FROM rentals r JOIN customers c ON c.customer_id = r.customer_id
                          JOIN vehicles  v ON v.vehicle_id  = r.vehicle_id
           ORDER BY r.rental_id DESC LIMIT 8"""
    )
    st.dataframe(recent, use_container_width=True, hide_index=True)

    left, right = st.columns(2)
    with left:
        st.subheader("Vehicle Availability")
        d = query("SELECT status, COUNT(*)::int AS vehicles FROM vehicles GROUP BY status ORDER BY status")
        if not d.empty:
            st.bar_chart(d.set_index("status"))
    with right:
        st.subheader("Revenue by Vehicle Type")
        d = query(
            """SELECT v.vehicle_type, SUM(p.amount)::float AS revenue
               FROM payments p JOIN rentals r ON r.rental_id=p.rental_id
                               JOIN vehicles v ON v.vehicle_id=r.vehicle_id
               GROUP BY v.vehicle_type ORDER BY revenue DESC"""
        )
        if not d.empty:
            st.bar_chart(d.set_index("vehicle_type"))


# ----------------------------------------------------------------------------
# Page 2: Customers
# ----------------------------------------------------------------------------
def customer_form(prefix, row=None):
    r = row if row is not None else {}
    name = st.text_input("Name", r.get("name", ""), key=f"{prefix}_name")
    phone = st.text_input("Phone", r.get("phone", ""), key=f"{prefix}_phone")
    email = st.text_input("Email", r.get("email", ""), key=f"{prefix}_email")
    lic = st.text_input("Driving License Number", r.get("license_no", ""), key=f"{prefix}_lic")
    addr = st.text_area("Address", r.get("address", ""), key=f"{prefix}_addr")
    return name.strip(), phone.strip(), email.strip(), lic.strip(), addr.strip()


def validate_customer(vals):
    if not all(vals):
        raise ValueError("All customer fields are required.")
    if "@" not in vals[2]:
        raise ValueError("Enter a valid email address.")


def insert_customer(vals):
    validate_customer(vals)
    execute("INSERT INTO customers(name, phone, email, license_no, address) VALUES (%s,%s,%s,%s,%s)", vals)


def update_customer(cid, vals):
    validate_customer(vals)
    execute(
        "UPDATE customers SET name=%s, phone=%s, email=%s, license_no=%s, address=%s WHERE customer_id=%s",
        (*vals, cid),
    )


def page_customers():
    st.header("👥 Customers")
    t = st.tabs(["View / Search", "Add", "Update", "Delete", "Rental History"])
    with t[0]:
        q = st.text_input("Search by name, phone, email or license number")
        df = query(
            """SELECT customer_id AS id, name, phone, email, license_no, address FROM customers
               WHERE name ILIKE %(q)s OR phone ILIKE %(q)s OR email ILIKE %(q)s OR license_no ILIKE %(q)s
               ORDER BY customer_id""",
            {"q": f"%{q}%"},
        )
        st.caption(f"{len(df)} customer(s)")
        st.dataframe(df, use_container_width=True, hide_index=True)
    with t[1]:
        vals = customer_form("add_c")
        if st.button("Add Customer", type="primary"):
            act(insert_customer, vals, ok="Customer added.")
    with t[2]:
        cid = pick("Select customer", customers_df(), "customer_id", cust_label, "upd_c_sel")
        if cid:
            row = query("SELECT * FROM customers WHERE customer_id=%s", (cid,)).iloc[0].to_dict()
            vals = customer_form(f"upd_c_{cid}", row)
            if st.button("Update Customer", type="primary"):
                act(update_customer, cid, vals, ok="Customer updated.")
    with t[3]:
        cid = pick("Select customer to delete", customers_df(), "customer_id", cust_label, "del_c_sel")
        if cid:
            st.warning("Customers who already have rentals cannot be deleted (foreign key RESTRICT).")
            if st.button("Delete Customer"):
                act(execute, "DELETE FROM customers WHERE customer_id=%s", (cid,), ok="Customer deleted.")
    with t[4]:
        cid = pick("Select customer", customers_df(), "customer_id", cust_label, "hist_c_sel")
        if cid:
            h = query(
                """SELECT r.rental_id AS id, v.vehicle_number, v.brand, v.model, r.rent_date,
                          r.expected_return_date, r.actual_return_date,
                          COALESCE(r.final_amount, r.expected_amount)::float AS amount, r.status
                   FROM rentals r JOIN vehicles v ON v.vehicle_id=r.vehicle_id
                   WHERE r.customer_id=%s ORDER BY r.rent_date DESC""",
                (cid,),
            )
            st.dataframe(h, use_container_width=True, hide_index=True)
            if not h.empty:
                st.metric("Total billed", f"Rs. {h['amount'].sum():,.0f}")


# ----------------------------------------------------------------------------
# Page 3: Vehicles
# ----------------------------------------------------------------------------
def vehicle_form(prefix, row=None):
    r = row if row is not None else {}
    num = st.text_input("Vehicle Number", r.get("vehicle_number", ""), key=f"{prefix}_num")
    brand = st.text_input("Brand", r.get("brand", ""), key=f"{prefix}_brand")
    model = st.text_input("Model", r.get("model", ""), key=f"{prefix}_model")
    types = VEHICLE_TYPES if r.get("vehicle_type", "Car") in VEHICLE_TYPES else VEHICLE_TYPES + [r["vehicle_type"]]
    vtype = st.selectbox("Vehicle Type", types, index=types.index(r.get("vehicle_type", "Car")), key=f"{prefix}_type")
    rate = st.number_input("Daily Rate (Rs.)", min_value=1.0, value=float(r.get("daily_rate", 1000.0)),
                           step=100.0, key=f"{prefix}_rate")
    status = st.selectbox("Status", STATUSES, index=STATUSES.index(r.get("status", "Available")), key=f"{prefix}_status")
    return num.strip().upper(), brand.strip(), model.strip(), vtype, rate, status


def insert_vehicle(v):
    if not (v[0] and v[1] and v[2]):
        raise ValueError("Vehicle number, brand and model are required.")
    execute(
        "INSERT INTO vehicles(vehicle_number, brand, model, vehicle_type, daily_rate, status) "
        "VALUES (%s,%s,%s,%s,%s,%s)", v,
    )


def update_vehicle(vid, v):
    if not (v[0] and v[1] and v[2]):
        raise ValueError("Vehicle number, brand and model are required.")
    execute(
        "UPDATE vehicles SET vehicle_number=%s, brand=%s, model=%s, vehicle_type=%s, daily_rate=%s, "
        "status=%s WHERE vehicle_id=%s", (*v, vid),
    )


def page_vehicles():
    st.header("🚗 Vehicles")
    t = st.tabs(["View / Search", "Add", "Update", "Delete", "Maintenance"])
    with t[0]:
        c1, c2 = st.columns([2, 1])
        q = c1.text_input("Search by number, brand, model or type")
        stf = c2.selectbox("Status", ["All"] + STATUSES)
        df = query(
            """SELECT vehicle_id AS id, vehicle_number, brand, model, vehicle_type, daily_rate::float AS daily_rate, status
               FROM vehicles
               WHERE (vehicle_number ILIKE %(q)s OR brand ILIKE %(q)s OR model ILIKE %(q)s OR vehicle_type ILIKE %(q)s)
                 AND (%(s)s = 'All' OR status = %(s)s)
               ORDER BY vehicle_id""",
            {"q": f"%{q}%", "s": stf},
        )
        st.caption(f"{len(df)} vehicle(s)")
        st.dataframe(df, use_container_width=True, hide_index=True)
    with t[1]:
        v = vehicle_form("add_v")
        if st.button("Add Vehicle", type="primary"):
            act(insert_vehicle, v, ok="Vehicle added.")
    with t[2]:
        vid = pick("Select vehicle", vehicles_df(), "vehicle_id", veh_label, "upd_v_sel")
        if vid:
            row = query("SELECT * FROM vehicles WHERE vehicle_id=%s", (vid,)).iloc[0].to_dict()
            v = vehicle_form(f"upd_v_{vid}", row)
            if st.button("Update Vehicle", type="primary"):
                act(update_vehicle, vid, v, ok="Vehicle updated.")
    with t[3]:
        vid = pick("Select vehicle to delete", vehicles_df(), "vehicle_id", veh_label, "del_v_sel")
        if vid:
            st.warning("Vehicles with rental history cannot be deleted. Maintenance records are deleted with the vehicle.")
            if st.button("Delete Vehicle"):
                act(execute, "DELETE FROM vehicles WHERE vehicle_id=%s", (vid,), ok="Vehicle deleted.")
    with t[4]:
        m = query(
            """SELECT m.maintenance_id AS id, v.vehicle_number, v.brand, v.model, m.description,
                      m.cost::float AS cost, m.maintenance_date, m.status
               FROM maintenance m JOIN vehicles v ON v.vehicle_id=m.vehicle_id
               ORDER BY m.maintenance_date DESC, m.maintenance_id DESC"""
        )
        st.subheader("Maintenance records")
        st.dataframe(m, use_container_width=True, hide_index=True)
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Send vehicle to maintenance")
            vid = pick("Vehicle", vehicles_df("WHERE status <> 'Rented'"), "vehicle_id", veh_label, "mnt_v")
            desc = st.text_input("Description", key="mnt_desc")
            cost = st.number_input("Cost (Rs.)", min_value=0.0, step=500.0, key="mnt_cost")
            mdate = st.date_input("Date", dt.date.today(), key="mnt_date")
            if vid and st.button("Add Maintenance Record", type="primary"):
                if not desc.strip():
                    st.error("Description is required.")
                else:
                    act(add_maintenance, vid, desc.strip(), cost, mdate, ok="Maintenance record added, vehicle marked Maintenance.")
        with c2:
            st.subheader("Complete maintenance")
            ongoing = m[m["status"] == "Ongoing"] if not m.empty else m
            mid = pick("Ongoing record", ongoing, "id", lambda r: f"#{r['id']} {r['vehicle_number']} - {r['description']}", "mnt_done")
            if mid and st.button("Mark Completed"):
                act(complete_maintenance, mid, ok="Maintenance completed, vehicle is Available again.")


# ----------------------------------------------------------------------------
# Page 4: Rentals
# ----------------------------------------------------------------------------
RENTAL_SQL = """
SELECT r.rental_id AS id, c.name AS customer, v.vehicle_number AS vehicle, v.brand || ' ' || v.model AS model,
       r.rent_date, r.expected_return_date, r.actual_return_date,
       r.expected_amount::float AS expected_amount, r.final_amount::float AS final_amount,
       COALESCE(p.paid, 0)::float AS paid, r.status
FROM rentals r
JOIN customers c ON c.customer_id = r.customer_id
JOIN vehicles  v ON v.vehicle_id  = r.vehicle_id
LEFT JOIN (SELECT rental_id, SUM(amount) AS paid FROM payments GROUP BY rental_id) p ON p.rental_id = r.rental_id
"""


def page_rentals():
    st.header("🔑 Rentals")
    t = st.tabs(["Rent a Vehicle", "Return a Vehicle", "Active", "Completed", "History", "Payments"])
    today = dt.date.today()

    with t[0]:
        cid = pick("Customer", customers_df(), "customer_id", cust_label, "rent_c")
        avail = vehicles_df("WHERE status='Available'")
        vid = pick("Available vehicle", avail, "vehicle_id", lambda r: f"{r['vehicle_number']} - {r['brand']} {r['model']} (Rs. {r['daily_rate']}/day)", "rent_v")
        c1, c2 = st.columns(2)
        start = c1.date_input("Rent date", today, key="rent_start")
        end = c2.date_input("Expected return date", today + dt.timedelta(days=3), key="rent_end")
        if vid:
            rate = float(avail[avail["vehicle_id"] == vid].iloc[0]["daily_rate"])
            days = max(1, (end - start).days)
            expected = days * rate
            st.info(f"Duration: {days} day(s) x Rs. {rate:,.0f} = **Expected amount Rs. {expected:,.0f}**")
        adv = st.number_input("Advance payment (optional, Rs.)", min_value=0.0, step=100.0, key="rent_adv")
        method = st.selectbox("Payment method", METHODS, key="rent_method")
        if cid and vid and st.button("Rent Vehicle", type="primary"):
            if vid and adv > expected:
                st.error("Advance cannot exceed the expected amount.")
            else:
                act(rent_vehicle, cid, vid, start, end, adv, method, ok="Vehicle rented successfully. Status changed to Rented.")

    with t[1]:
        active = query(RENTAL_SQL + " WHERE r.status='Active' ORDER BY r.rental_id")
        rid = pick("Active rental", active, "id", lambda r: f"#{r['id']} {r['customer']} - {r['vehicle']}", "ret_sel")
        if rid:
            row = active[active["id"] == rid].iloc[0]
            actual = st.date_input("Actual return date", today, key=f"ret_date_{rid}")
            rate = float(query("SELECT v.daily_rate FROM rentals r JOIN vehicles v ON v.vehicle_id=r.vehicle_id WHERE r.rental_id=%s", (rid,)).iloc[0, 0])
            days = max(1, (actual - row["rent_date"]).days)
            final = days * rate
            balance = max(0.0, final - float(row["paid"]))
            st.info(f"Rented for {days} day(s). **Final amount Rs. {final:,.0f}** | Already paid Rs. {row['paid']:,.0f} | Balance Rs. {balance:,.0f}")
            pay = st.number_input("Payment received now (Rs.)", min_value=0.0, value=float(balance), step=100.0, key=f"ret_pay_{rid}")
            method = st.selectbox("Payment method", METHODS, key=f"ret_method_{rid}")
            if st.button("Return Vehicle", type="primary"):
                if pay > balance:
                    st.error("Payment exceeds the balance.")
                else:
                    act(return_vehicle, rid, actual, pay, method, ok="Vehicle returned. Rental completed and vehicle is Available.")

    with t[2]:
        st.dataframe(query(RENTAL_SQL + " WHERE r.status='Active' ORDER BY r.rental_id DESC"), use_container_width=True, hide_index=True)
    with t[3]:
        st.dataframe(query(RENTAL_SQL + " WHERE r.status='Completed' ORDER BY r.rental_id DESC"), use_container_width=True, hide_index=True)
    with t[4]:
        st.dataframe(query(RENTAL_SQL + " ORDER BY r.rental_id DESC"), use_container_width=True, hide_index=True)

    with t[5]:
        st.subheader("Record a payment")
        due = query(RENTAL_SQL + " ORDER BY r.rental_id DESC")
        if not due.empty:
            due["total"] = due["final_amount"].fillna(due["expected_amount"])
            due["balance"] = (due["total"] - due["paid"]).round(2)
            due = due[due["balance"] > 0]
        rid = pick("Rental with pending balance", due, "id", lambda r: f"#{r['id']} {r['customer']} - {r['vehicle']} (balance Rs. {r['balance']:,.0f})", "pay_sel")
        if rid:
            bal = float(due[due["id"] == rid].iloc[0]["balance"])
            amt = st.number_input("Amount (Rs.)", min_value=0.0, max_value=bal, value=bal, step=100.0, key=f"pay_amt_{rid}")
            method = st.selectbox("Method", METHODS, key=f"pay_method_{rid}")
            if st.button("Save Payment", type="primary"):
                act(add_payment, rid, amt, method, bal, ok="Payment recorded.")
        st.subheader("All payments")
        st.dataframe(
            query(
                """SELECT p.payment_id AS id, p.rental_id, c.name AS customer, v.vehicle_number AS vehicle,
                          p.amount::float AS amount, p.payment_date, p.method
                   FROM payments p JOIN rentals r ON r.rental_id=p.rental_id
                                   JOIN customers c ON c.customer_id=r.customer_id
                                   JOIN vehicles v ON v.vehicle_id=r.vehicle_id
                   ORDER BY p.payment_id DESC"""
            ),
            use_container_width=True, hide_index=True,
        )


# ----------------------------------------------------------------------------
# Page 5: Reports (each report shows the SQL concept it demonstrates)
# ----------------------------------------------------------------------------
REPORTS = {
    "Monthly revenue": (
        "GROUP BY, aggregate functions, window function (running total)",
        """SELECT TO_CHAR(DATE_TRUNC('month', payment_date), 'YYYY-MM') AS month,
       COUNT(*)                                   AS payments,
       SUM(amount)::float                         AS revenue,
       SUM(SUM(amount)) OVER (ORDER BY DATE_TRUNC('month', payment_date))::float AS running_total
FROM payments
GROUP BY DATE_TRUNC('month', payment_date)
ORDER BY 1""",
        ("month", "revenue"),
    ),
    "Most rented vehicles": (
        "LEFT JOIN, GROUP BY, COUNT, window function RANK()",
        """SELECT RANK() OVER (ORDER BY COUNT(r.rental_id) DESC) AS rank,
       v.vehicle_number, v.brand, v.model, COUNT(r.rental_id) AS times_rented
FROM vehicles v
LEFT JOIN rentals r ON r.vehicle_id = v.vehicle_id
GROUP BY v.vehicle_id, v.vehicle_number, v.brand, v.model
ORDER BY times_rented DESC, v.vehicle_number
LIMIT 10""",
        ("vehicle_number", "times_rented"),
    ),
    "Top customers": (
        "CTE, JOIN, GROUP BY + HAVING, window function DENSE_RANK()",
        """WITH customer_totals AS (
    SELECT c.customer_id, c.name, COUNT(r.rental_id) AS rentals,
           SUM(COALESCE(r.final_amount, r.expected_amount))::float AS total_billed
    FROM customers c
    JOIN rentals r ON r.customer_id = c.customer_id
    GROUP BY c.customer_id, c.name
    HAVING COUNT(r.rental_id) >= 1
)
SELECT DENSE_RANK() OVER (ORDER BY total_billed DESC) AS rank, name, rentals, total_billed
FROM customer_totals
ORDER BY rank, name""",
        ("name", "total_billed"),
    ),
    "Vehicle utilization (last 30 days)": (
        "CTE, LEFT JOIN, GREATEST/LEAST, aggregate SUM",
        """WITH usage AS (
    SELECT v.vehicle_id, v.vehicle_number, v.brand, v.model, v.status,
           COALESCE(SUM(GREATEST(0,
               LEAST(COALESCE(r.actual_return_date, CURRENT_DATE), CURRENT_DATE)
               - GREATEST(r.rent_date, CURRENT_DATE - 30))), 0) AS days_rented
    FROM vehicles v
    LEFT JOIN rentals r ON r.vehicle_id = v.vehicle_id
    GROUP BY v.vehicle_id, v.vehicle_number, v.brand, v.model, v.status
)
SELECT vehicle_number, brand, model, status, days_rented,
       ROUND(days_rented * 100.0 / 30, 1)::float AS utilization_pct
FROM usage
ORDER BY utilization_pct DESC, vehicle_number""",
        ("vehicle_number", "utilization_pct"),
    ),
    "Active / overdue rentals": (
        "JOIN, CASE expression, date arithmetic",
        """SELECT r.rental_id AS id, c.name AS customer, c.phone, v.vehicle_number,
       r.rent_date, r.expected_return_date,
       GREATEST(0, CURRENT_DATE - r.expected_return_date) AS days_overdue,
       CASE WHEN r.expected_return_date < CURRENT_DATE THEN 'OVERDUE' ELSE 'On time' END AS state
FROM rentals r
JOIN customers c ON c.customer_id = r.customer_id
JOIN vehicles  v ON v.vehicle_id  = r.vehicle_id
WHERE r.status = 'Active'
ORDER BY days_overdue DESC""",
        None,
    ),
    "Revenue by vehicle type": (
        "Multi-table JOIN, GROUP BY, HAVING, SUM",
        """SELECT v.vehicle_type, COUNT(DISTINCT r.rental_id) AS rentals, SUM(p.amount)::float AS revenue
FROM payments p
JOIN rentals  r ON r.rental_id  = p.rental_id
JOIN vehicles v ON v.vehicle_id = r.vehicle_id
GROUP BY v.vehicle_type
HAVING SUM(p.amount) > 0
ORDER BY revenue DESC""",
        ("vehicle_type", "revenue"),
    ),
    "Rental statistics": (
        "Aggregate functions: COUNT, AVG, MAX, MIN, FILTER",
        """SELECT COUNT(*)                                   AS total_rentals,
       COUNT(*) FILTER (WHERE status = 'Active')    AS active,
       COUNT(*) FILTER (WHERE status = 'Completed') AS completed,
       ROUND(AVG(final_amount), 2)::float           AS avg_bill,
       MAX(final_amount)::float                     AS max_bill,
       MIN(final_amount)::float                     AS min_bill,
       ROUND(AVG(actual_return_date - rent_date), 1)::float AS avg_rental_days
FROM rentals""",
        None,
    ),
    "Customers spending above average": (
        "Subquery in HAVING",
        """SELECT c.name, SUM(COALESCE(r.final_amount, r.expected_amount))::float AS total_billed
FROM customers c
JOIN rentals r ON r.customer_id = c.customer_id
GROUP BY c.customer_id, c.name
HAVING SUM(COALESCE(r.final_amount, r.expected_amount)) >
       (SELECT AVG(COALESCE(final_amount, expected_amount)) FROM rentals)
ORDER BY total_billed DESC""",
        ("name", "total_billed"),
    ),
    "Vehicles never rented": (
        "Correlated subquery with NOT EXISTS",
        """SELECT v.vehicle_number, v.brand, v.model, v.vehicle_type, v.daily_rate::float AS daily_rate
FROM vehicles v
WHERE NOT EXISTS (SELECT 1 FROM rentals r WHERE r.vehicle_id = v.vehicle_id)
ORDER BY v.vehicle_number""",
        None,
    ),
}


def page_reports():
    st.header("📈 Reports")
    name = st.selectbox("Choose a report", list(REPORTS))
    concept, sql, chart = REPORTS[name]
    st.caption(f"SQL concepts: {concept}")
    df = query(sql)
    if df.empty:
        st.info("No data for this report yet.")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)
        if chart:
            st.bar_chart(df.set_index(chart[0])[chart[1]])
    with st.expander("Show SQL query"):
        st.code(sql, language="sql")


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    st.sidebar.title("🚗 Vehicle Rental")
    st.sidebar.caption("DBMS Project | Streamlit + Python + PostgreSQL")
    if not db_url():
        st.error("DATABASE_URL is not configured.")
        st.markdown(
            "Add your PostgreSQL connection string in Streamlit **Settings -> Secrets**:\n\n"
            "```toml\nDATABASE_URL = \"postgresql://user:password@host/dbname?sslmode=require\"\n```"
        )
        st.stop()
    try:
        init_db()
    except Exception as e:
        st.error(f"Cannot connect to PostgreSQL: {e}")
        st.stop()

    page = st.sidebar.radio("Navigation", ["Dashboard", "Customers", "Vehicles", "Rentals", "Reports"])
    st.sidebar.divider()
    if st.sidebar.button("Load sample data"):
        act(load_sample_data, ok="Sample data loaded into PostgreSQL.")
    st.sidebar.caption("Sample data works only on an empty database.")

    show_flash()
    {"Dashboard": page_dashboard, "Customers": page_customers, "Vehicles": page_vehicles,
     "Rentals": page_rentals, "Reports": page_reports}[page]()


main()
