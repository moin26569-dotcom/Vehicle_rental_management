-- Vehicle Rental Management System : PostgreSQL schema (3NF)

CREATE TABLE IF NOT EXISTS customers (
    customer_id  SERIAL PRIMARY KEY,
    name         VARCHAR(100) NOT NULL,
    phone        VARCHAR(20)  NOT NULL,
    email        VARCHAR(120) NOT NULL UNIQUE,
    license_no   VARCHAR(30)  NOT NULL UNIQUE,
    address      TEXT         NOT NULL
);

CREATE TABLE IF NOT EXISTS vehicles (
    vehicle_id      SERIAL PRIMARY KEY,
    vehicle_number  VARCHAR(20)   NOT NULL UNIQUE,
    brand           VARCHAR(50)   NOT NULL,
    model           VARCHAR(50)   NOT NULL,
    vehicle_type    VARCHAR(30)   NOT NULL,
    daily_rate      NUMERIC(10,2) NOT NULL CHECK (daily_rate > 0),
    status          VARCHAR(20)   NOT NULL DEFAULT 'Available'
                    CHECK (status IN ('Available', 'Rented', 'Maintenance'))
);

CREATE TABLE IF NOT EXISTS rentals (
    rental_id             SERIAL PRIMARY KEY,
    customer_id           INT NOT NULL REFERENCES customers(customer_id) ON DELETE RESTRICT,
    vehicle_id            INT NOT NULL REFERENCES vehicles(vehicle_id)  ON DELETE RESTRICT,
    rent_date             DATE NOT NULL,
    expected_return_date  DATE NOT NULL,
    actual_return_date    DATE,
    expected_amount       NUMERIC(12,2) NOT NULL,
    final_amount          NUMERIC(12,2),
    status                VARCHAR(20) NOT NULL DEFAULT 'Active'
                          CHECK (status IN ('Active', 'Completed')),
    CHECK (expected_return_date >= rent_date)
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id    SERIAL PRIMARY KEY,
    rental_id     INT NOT NULL REFERENCES rentals(rental_id) ON DELETE CASCADE,
    amount        NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    payment_date  DATE NOT NULL DEFAULT CURRENT_DATE,
    method        VARCHAR(20) NOT NULL CHECK (method IN ('Cash', 'UPI', 'Card', 'Bank Transfer'))
);

CREATE TABLE IF NOT EXISTS maintenance (
    maintenance_id    SERIAL PRIMARY KEY,
    vehicle_id        INT NOT NULL REFERENCES vehicles(vehicle_id) ON DELETE CASCADE,
    description       TEXT NOT NULL,
    cost              NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (cost >= 0),
    maintenance_date  DATE NOT NULL DEFAULT CURRENT_DATE,
    status            VARCHAR(20) NOT NULL DEFAULT 'Ongoing'
                      CHECK (status IN ('Ongoing', 'Completed'))
);

CREATE INDEX IF NOT EXISTS idx_rentals_customer ON rentals(customer_id);
CREATE INDEX IF NOT EXISTS idx_rentals_vehicle  ON rentals(vehicle_id);
CREATE INDEX IF NOT EXISTS idx_payments_rental  ON payments(rental_id);
