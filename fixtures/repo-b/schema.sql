CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL,
    total REAL NOT NULL,
    age_days INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE refunds (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(id)
);
