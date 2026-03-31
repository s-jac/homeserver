# config/config.py — copy this file to config.py and fill in your real values.
#
# Two identities:
#   gordon  — fake/test identity (default). Safe to use for dry-runs and dev.
#   sam     — real identity. Used when scripts are called with --real.
#
# App settings (auth, email) are also here. Edit this file directly to change them —
# they are not editable via the web UI.

# ── App ──────────────────────────────────────────────────────────────────────

auth = {
    "password": "changeme",
    # Generate with: python3 -c "import secrets; print(secrets.token_hex(32))"
    "jwt_secret": "replace-with-64-char-hex-string",
    "token_expiry_hours": 168,
}

email = {
    "enabled": False,
    "from_address": "you@gmail.com",
    "to_address": "you@gmail.com",
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "username": "you@gmail.com",
    "app_password": "",  # Gmail app password (Settings > Security > App passwords)
}


# ── News Digest ──────────────────────────────────────────────────────────────

gemini_api_keys = [""]  # Add keys from multiple projects for quota fallback

github_token = ""  # GitHub PAT with contents:write on s-jac/s-jac.github.io
                   # Create at: github.com/settings/tokens → Classic → repo scope

news_recipients = ["you@example.com", "partner@example.com"]


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
