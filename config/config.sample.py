# config/config.py — copy this file to config.py and fill in your real values.
#
# Two identities:
#   gordon  — fake/test identity (default). Safe to use for dry-runs and dev.
#   sam     — real identity. Used when scripts are called with --real.

# ── Gordon (fake/test identity) ───────────────────────────────────────────────

gordon = {
    "first_name": "Gordon",
    "last_name": "Macdonald",
    "email": "gordonmaccas@proton.me",
    "mobile": "0417826417",
    # Campsite-specific
    "password": "testpassword",
    "phone": "0400000000",
    "address": "1 Test St",
    "city": "Sydney",
    "state": "NSW",
    "postcode": "2000",
    "vehicle_rego": "TEST00",
    "vehicle_state": "NSW",
    # Test card (Visa test number — never charges)
    "card_number": "4111111111111111",
    "card_expiry_month": "01",
    "card_expiry_year": "2029",
    "card_cvv": "123",
    "card_name": "Gordon Macdonald",
}

# ── Sam (real identity) ───────────────────────────────────────────────────────

sam = {
    "first_name": "",
    "last_name": "",
    "email": "",
    "mobile": "",
    # Campsite-specific
    "password": "",
    "phone": "",
    "address": "",
    "city": "",
    "state": "NSW",
    "postcode": "",
    "vehicle_rego": "",
    "vehicle_state": "NSW",
    # Real card details
    "card_number": "",
    "card_expiry_month": "",
    "card_expiry_year": "",
    "card_cvv": "",
    "card_name": "",
}
