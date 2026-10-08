# Vehicle Rental Management System (DBMS Project)

Flow: Streamlit UI -> Python logic -> SQL -> PostgreSQL -> Python -> Streamlit UI

## Deploy (free, ~10 min)
1. **Database:** create a free project at neon.tech -> copy the connection string (postgresql://...).
2. **GitHub:** create a repo, upload these files (app.py, schema.sql, requirements.txt).
3. **Streamlit:** share.streamlit.io -> New app -> pick repo -> main file `app.py`.
4. In app **Settings -> Secrets** paste: `DATABASE_URL = "your neon connection string"`
5. Open the app link, click **Load sample data** in the sidebar. Send the link to sir.

Tables are created automatically from schema.sql on first run.

## Run locally
    pip install -r requirements.txt
    export DATABASE_URL="postgresql://user:pass@localhost/rental"
    streamlit run app.py

## DBMS concepts and where to show them
| Concept | Where |
|---|---|
| CRUD | Customers and Vehicles pages |
| PK / FK / UNIQUE / NOT NULL / CHECK | schema.sql |
| Transactions (rent / return) | rent_vehicle(), return_vehicle() in app.py |
| JOIN, GROUP BY, HAVING, aggregates | Reports page (Show SQL query) |
| Subquery | Reports: above-average customers, never-rented vehicles |
| CTE | Reports: Top customers, Vehicle utilization |
| Window functions | Reports: Monthly revenue (running total), RANK, DENSE_RANK |
| Normalization (3NF) | customers, vehicles, rentals, payments, maintenance are separate tables; no repeated data |

## Demo order
Dashboard -> add customer -> add vehicle -> rent -> try renting same vehicle again (blocked) -> return -> Reports.
