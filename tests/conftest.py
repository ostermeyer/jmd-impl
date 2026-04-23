# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures and helpers for the JMD test suite."""



# ---------------------------------------------------------------------------
# Sample documents
# ---------------------------------------------------------------------------

ORDER_JMD = """\
# Order
id: 42
status: pending
paid: false
total: 99.95

## customer
name: Anna Müller
email: anna@example.com

## address
street: Hauptstraße 1
city: Berlin
zip: "10115"

## items[]
- product: Laptop Stand
  qty: 1
  price: 49.90
- product: USB Hub
  qty: 2
  price: 19.95
"""

ORDER_DICT = {
    "id": 42,
    "status": "pending",
    "paid": False,
    "total": 99.95,
    "customer": {"name": "Anna Müller", "email": "anna@example.com"},
    "address": {"street": "Hauptstraße 1", "city": "Berlin", "zip": "10115"},
    "items": [
        {"product": "Laptop Stand", "qty": 1, "price": 49.90},
        {"product": "USB Hub", "qty": 2, "price": 19.95},
    ],
}
