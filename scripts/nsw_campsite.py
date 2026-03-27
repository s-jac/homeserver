#!/usr/bin/env python3
"""
NSW National Parks Campsite Booking Script

Two-step workflow:

  1. Check availability — see what's free, find adjacent groups:
       python nsw_campsite.py check --campground frazer --checkin 2026-09-04 --nights 2
       python nsw_campsite.py check --campground frazer --checkin 2026-09-04 --nights 2 --count 3
       python nsw_campsite.py check --campground frazer --checkin 2026-09-04 --nights 2 --sites 2,3,4

  2. Book specific sites:
       python nsw_campsite.py book  --campground frazer --checkin 2026-09-04 --nights 2 --sites 2,3,4 --adults 1

API architecture:
  - Discovery:  GET  nationalparks.nsw.gov.au/npws/ReservationApi/...
  - Booking:    POST nsw.rezexpert.com/api-nsw-console/request  (XML-over-POST)
  - Payment:    POST pay.service.nsw.gov.au/api/card/payment
                     via Westpac Quickstream card tokenisation
"""

import argparse
import base64
import re
import sys
import time
from itertools import combinations
from urllib.parse import quote, urlparse, parse_qs
import xml.etree.ElementTree as ET

import json
from pathlib import Path

import requests

BASE_DIR    = Path(__file__).parent.parent
CONFIG_FILE = BASE_DIR / "config" / "config.json"


def _load_campsite_cfg(real: bool) -> dict:
    """Load campsite section from config.json. real=True uses 'campsite', else 'campsite_fake'."""
    with open(CONFIG_FILE) as f:
        data = json.load(f)
    key = "campsite" if real else "campsite_fake"
    if key not in data:
        sys.exit(f"'{key}' section not found in config.json")
    return data[key]

# ── Campground registry ────────────────────────────────────────────────────────
#
# business_code : RezExpert property ID
# unit_type_id  : camping-type ID for this campground (tent, caravan, etc.)
# sites         : ordered list of {"label": "1", "unit_id": "2542"} dicts.
#                 Order matters — adjacency is inferred from position in this list.
#                 For "camp anywhere" campgrounds with no specific sites,
#                 leave sites as [] and unit_id "0" will be used.
#
# To discover unit_ids for a new campground, run:
#   python book.py check --campground <key> --checkin <date> --nights 1 --dump-ids

CAMPGROUNDS = {
    "frazer": {
        "name": "Frazer Campground (tent only) — Munmorah SCA",
        "business_code": "500288",
        "unit_type_id": "660",
        # Sites are physically ordered 1→6 along the beach walk.
        "sites": [
            {"label": "1", "unit_id": "2542"},
            {"label": "2", "unit_id": "2543"},
            {"label": "3", "unit_id": "2544"},
            {"label": "4", "unit_id": "2545"},
            {"label": "5", "unit_id": "2546"},
            {"label": "6", "unit_id": "2547"},
        ],
    },
    "policemans-point": {
        "name": "Policemans Point Campground — Capertee NP",
        "business_code": "500480",
        "unit_type_id": "5469",
        "sites": [],  # Camp-anywhere — no specific site selection
    },
    # Add more campgrounds here. Run `check --dump-ids` to discover unit_ids.
}

REZEXPERT_API  = "https://nsw.rezexpert.com/api-nsw-console/request"
REZEXPERT_BASE = "https://nsw.rezexpert.com"

NP_BASE            = "https://www.nationalparks.nsw.gov.au"
NP_CAMPGROUND_BASE = f"{NP_BASE}/camping-and-accommodation/campgrounds"

QUICKSTREAM_API        = "https://api.quickstream.westpac.com.au/rest/v1"
QUICKSTREAM_PUBLIC_KEY = "SERVICENSW_PUB_rqczvau95ziei28ysby2kvmkpeurb6rmxxcfatxswwgg767z3xbseetq7u"

PAYMENT_PORTAL = "https://pay.service.nsw.gov.au"


# ── RezExpert XML API client ───────────────────────────────────────────────────

class RezExpertClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
            ),
            "Accept": "application/xml, text/xml, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": REZEXPERT_BASE,
        })
        self.jwt: str | None = None
        # Populated by login():
        self.business_id: str | None = None
        self.business_group_id: str | None = None
        self.client_id: str | None = None

    def _post_xml(self, xml: str) -> ET.Element:
        body = f"format=xml&xmldata={quote(xml)}"
        headers = {"Authorization": f"Bearer {self.jwt}"} if self.jwt else {}
        resp = self.session.post(REZEXPERT_API, data=body, headers=headers)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        if root.get("t"):
            self.jwt = root.get("t")
        return root

    @staticmethod
    def _ok(root: ET.Element, method: str) -> ET.Element:
        el = root.find(method)
        if el is None:
            raise RuntimeError(f"Response missing <{method}>")
        fc = el.get("faultcode", "")
        if fc != "success":
            raise RuntimeError(f"{method} failed: {ET.tostring(el).decode()}")
        return el

    def set_cookies(self, jsessionid: str, phpsessid: str) -> None:
        """Inject browser session cookies (workaround until login is automated)."""
        self.session.cookies.set("JSESSIONID", jsessionid, domain="nsw.rezexpert.com")
        self.session.cookies.set("PHPSESSID",  phpsessid,  domain="nsw.rezexpert.com")

    # ── Session ────────────────────────────────────────────────────────────────

    def login(self, business_code: str, email: str | None = None, password: str | None = None) -> dict:
        """
        Authenticate and obtain a JWT.

        If email + password are supplied, calls s2rxc-session-login directly
        (no browser cookies required).  2FA accounts will raise a RuntimeError
        with a message indicating which auth mode is needed.

        If email/password are omitted, falls back to s2rxc-session-get which
        requires JSESSIONID + PHPSESSID to already be set via set_cookies().
        """
        if email and password:
            return self._login_with_credentials(business_code, email, password)
        return self._login_with_cookies(business_code)

    def _login_with_credentials(self, business_code: str, email: str, password: str) -> dict:
        # s2rxc-rez-set MUST be included in the same batch as session-login —
        # without it the Java session is not initialised and rez-create will
        # silently return faultcode="fail" faultmessage="".
        xml = f"""<request format="xml">
  <method name="s2rxc-session-login" user_name="{email}" password="{password}"
      login_type="1" two_factor_mode="0" two_factor_auth_code=""
      business_code="{business_code}"/>
  <method name="s2rxc-business-get" business_code="{business_code}" search_mode="2" xmlmode="3"/>
  <method name="s3rxc-resortweb-get" business_code="{business_code}"/>
  <method name="s3rxc-resortwebavail-get" business_code="{business_code}" mode="3"/>
  <method name="s2rxc-rez-set" business_code="{business_code}" shopping_cart_id="-1"/>
</request>"""
        root = self._post_xml(xml)
        login_el = self._ok(root, "s2rxc-session-login")
        tfm = int(login_el.get("tfm", 0) or 0)
        if tfm != 0:
            raise RuntimeError(
                f"Two-factor authentication required (mode={tfm}).\n"
                "Workaround: log in via browser, copy JSESSIONID + PHPSESSID from "
                "DevTools → Application → Cookies, and pass them with "
                "--jsessionid / --phpsessid."
            )
        sess = login_el.find("session")
        if sess is None:
            msg = login_el.get("faultmessage", "")
            raise RuntimeError(f"Login failed: {msg or 'invalid credentials'}")
        return self._capture_session(root, sess, business_code)

    def _login_with_cookies(self, business_code: str) -> dict:
        """Initialise session from pre-set browser cookies (legacy workaround)."""
        xml = f"""<request format="xml">
  <method name="s2rxc-session-get" login_type="1" xmlmode="2" business_code="{business_code}"/>
  <method name="s2rxc-business-get" business_code="{business_code}" search_mode="2" xmlmode="3"/>
  <method name="s3rxc-resortweb-get" business_code="{business_code}"/>
  <method name="s3rxc-resortwebavail-get" business_code="{business_code}" mode="3"/>
  <method name="s2rxc-rez-set" business_code="{business_code}" shopping_cart_id="-1"/>
</request>"""
        root = self._post_xml(xml)
        sess = self._ok(root, "s2rxc-session-get").find("session")
        if sess is None:
            raise RuntimeError("Login failed — check your session cookies")
        return self._capture_session(root, sess, business_code)

    def _init_booking(self, business_code: str) -> None:
        """
        Replicate the browser's post-login initialisation:
          1. s3rxc-shoppingcartgroup-add  — registers this business in the cart group
          2. s2rxc-sessionvariable-set    — marks T&Cs accepted
        """
        xml = f"""<request format="xml">
  <method name="s3rxc-shoppingcartgroup-add" business_code="{business_code}" mode="3"/>
</request>"""
        root = self._post_xml(xml)
        self._ok(root, "s3rxc-shoppingcartgroup-add")

        xml = f"""<request format="xml">
  <method name="s2rxc-sessionvariable-set" business_code="{business_code}"
      variable_name="terms_conditions_accepted" value="0_1"/>
</request>"""
        root = self._post_xml(xml)
        self._ok(root, "s2rxc-sessionvariable-set")

    def _capture_session(self, root: ET.Element, sess: ET.Element, business_code: str) -> dict:
        biz = self._ok(root, "s2rxc-business-get").find("business")
        if biz is not None:
            self.business_id       = biz.get("business_id")
            self.business_group_id = biz.get("business_group_id")
        self.client_id = sess.get("employee_client_id")
        # Mirror the Referer header the browser sends — required by the server
        # for mutation calls like s2rxc-rez-create.
        self.session.headers["Referer"] = (
            f"{REZEXPERT_BASE}/book?business_code={business_code}"
        )
        info = {k: sess.get(k) for k in ("first_name", "last_name", "email", "member_code")}
        # Register this business in the shopping cart group — required before
        # s2rxc-rez-create will succeed (mirrors what the browser does post-init).
        self._init_booking(business_code)
        return info

    def get_client_info(self, business_code: str) -> dict:
        """
        Fetch the logged-in client's profile, including saved vehicle instances.
        Returns dict with 'person' attrs and list of 'vehicles' [{roiid, rego, state}].
        """
        xml = f"""<request format="xml">
  <method name="s2rxc-onlinebookingclient-get" business_code="{business_code}" session_client="1"/>
</request>"""
        root = self._post_xml(xml)
        client_el = self._ok(root, "s2rxc-onlinebookingclient-get").find("client")
        person = client_el.find("person")
        vehicles = []
        for roi in client_el.findall("rezobjectinstances/rezobjectinstance"):
            attrs = {a.get("roaid"): a.get("v") for a in roi.findall("rezobjectattributes/rezobjectattr")}
            vehicles.append({
                "roiid": roi.get("roiid"),
                "rego":  attrs.get("9", ""),
                "state": attrs.get("8", ""),
            })
        return {
            "mobile": person.get("mobile_number") if person is not None else "",
            "vehicles": vehicles,
        }

    # ── Availability ───────────────────────────────────────────────────────────

    def get_availability(
        self,
        business_code: str,
        check_in: str,
        check_out: str,
        adults: int = 1,
        children: int = 0,
        infants: int = 0,
    ) -> dict[str, bool]:
        """Return {unit_id: available} for the requested dates."""
        xml = f"""<request format="xml">
  <method name="s2rxc-availability-get" business_code="{business_code}" mode="3"
      check_in_date="{check_in}T00:00:00" check_out_date="{check_out}T00:00:00"
      rr="pl_check_avail - 4">
    <rezgroup><rezs><rez><occupants>
      <occupant occupant_id="1" amount="{adults}"/>
      <occupant occupant_id="2" amount="{children}"/>
      <occupant occupant_id="3" amount="{infants}"/>
    </occupants></rez></rezs></rezgroup>
  </method>
  <method name="s2rxc-availableunit-get" business_code="{business_code}" search_mode="3"
      check_in_date="{check_in}T00:00:00" check_out_date="{check_out}T00:00:00"
      length="0" width="0" height="0" depth="0"/>
</request>"""
        root = self._post_xml(xml)
        self._ok(root, "s2rxc-availability-get")
        self._ok(root, "s2rxc-availableunit-get")
        return {
            el.get("unit_id"): el.get("available") == "1"
            for el in root.findall("s2rxc-availableunit-get/unit")
        }

    # ── Reservation creation ───────────────────────────────────────────────────

    def create_reservation(
        self,
        business_code: str,
        unit_type_id: str,
        unit_id: str,
        check_in: str,
        check_out: str,
        adults: int,
        children: int = 0,
        infants: int = 0,
    ) -> tuple[str, str]:
        """
        Create a pending reservation.

        unit_id: specific site unit_id, or "0" for camp-anywhere campgrounds.

        Returns (temp_rez_id, confirmation_number).
        The temp_rez_id is needed for all subsequent calls on this reservation.
        """
        # Only include occupants with a non-zero count — the server rejects zero counts.
        occ_pairs = [(i, n) for i, n in [(1, adults), (2, children), (3, infants)] if n > 0]
        occupant_ids    = ",".join(str(i) for i, _ in occ_pairs)
        occupant_counts = ",".join(str(n) for _, n in occ_pairs)
        xml = (
            f'<request format="xml">'
            f'<method name="s2rxc-rez-create" business_code="{business_code}">'
            f'<rez type_id="{unit_type_id}" unit_id="{unit_id}"'
            f' check_in_date="{check_in}T00:00:00" check_out_date="{check_out}T00:00:00"'
            f' occupant_ids="{occupant_ids}" occupant_counts="{occupant_counts}"'
            f' unit_length="0" unit_width="0" unit_height="0" unit_depth="0"/>'
            f'</method>'
            f'</request>'
        )
        root = self._post_xml(xml)
        rg = self._ok(root, "s2rxc-rez-create").find("rezgroup")
        if rg is None:
            raise RuntimeError("s2rxc-rez-create: missing rezgroup in response")
        rez = rg.find("rez")
        if rez is None:
            raise RuntimeError("s2rxc-rez-create: missing rez in response")
        deposit = float(rg.get("deposit_owed", 0) or 0)
        if rg.get("include_security_deposit_online") == "1":
            deposit += float(rg.get("security_deposit_owed", 0) or 0)
        return rez.get("temp_rez_id"), rg.get("confirmation_number"), f"{deposit:.2f}"

    def set_vehicle(
        self,
        business_code: str,
        temp_rez_id: str,
        mobile: str,
        rez_object_instance_id: str,
        rego: str,
        state: str,
        fallback_deposit: str = "0.00",
    ) -> tuple[str, str]:
        """
        Attach vehicle details to the reservation and confirm client details.
        Returns (temp_rez_id, deposit_owed) — deposit_owed is the amount due for payment.

        rez_object_instance_id: the roiid of an existing saved vehicle,
                                 or "0" to register a new one.
        Set rego="No vehicle" to explicitly select no vehicle (self-closing <rez>, no child).
        When no-vehicle, rez-set returns an empty success (no rezgroup); fallback_deposit
        (from rez-create's rezgroup) is used as the deposit in that case.
        """
        no_vehicle = rego.strip().lower() == "no vehicle"
        if no_vehicle:
            # Browser sends a bare self-closing <rez> — no <rezobjectinstance> child.
            # The server returns faultcode="success" but no rezgroup; deposit comes from
            # rez-create's response (passed in as fallback_deposit).
            rez_xml = (
                f'<rez rez_id="-1" temp_rez_id="{temp_rez_id}" refresh="1"'
                f' clear_rez_object_instances="1" marketing_type_id="1"/>'
            )
        else:
            rez_xml = (
                f'<rez rez_id="-1" temp_rez_id="{temp_rez_id}" refresh="1"'
                f' clear_rez_object_instances="1" marketing_type_id="1">'
                f'<rezobjectinstance rez_object_instance_id="{rez_object_instance_id}"'
                f' rez_object_type_id="1" rez_object_id="1"'
                f' rez_object_name="Vehicle" active="1" temp_index="2">'
                f'<rezobjectattr rez_object_attr_id="9" value="{rego}"/>'
                f'<rezobjectattr rez_object_attr_id="8" value="{state}"/>'
                f'</rezobjectinstance>'
                f'</rez>'
            )
        xml = (
            f'<request format="xml">'
            f'<method name="s2rxc-onlinebookingclient-set" business_code="{business_code}"'
            f' logged_in="1" mobile_phone="{mobile}" marketing_opt_in="0"/>'
            f'<method name="s2rxc-rez-set" business_code="{business_code}" shopping_cart_id="-1">'
            f'{rez_xml}'
            f'</method>'
            f'</request>'
        )
        root = self._post_xml(xml)
        self._ok(root, "s2rxc-onlinebookingclient-set")
        rez_set_el = self._ok(root, "s2rxc-rez-set")
        rezgroup = rez_set_el.find("rezgroup")
        rez = rezgroup.find("rez") if rezgroup is not None else None
        new_temp = rez.get("temp_rez_id") if rez is not None else temp_rez_id
        if rezgroup is not None:
            deposit = float(rezgroup.get("deposit_owed", 0) or 0)
            if rezgroup.get("include_security_deposit_online") == "1":
                deposit += float(rezgroup.get("security_deposit_owed", 0) or 0)
            return new_temp, f"{deposit:.2f}"
        # No rezgroup — expected for no-vehicle; use deposit from rez-create.
        return new_temp, fallback_deposit

    def _get_page_session_id(self, business_code: str) -> str:
        """
        GET the rezexpert booking page HTML and extract the rxc_api_session_id
        injected by the PHP server into the <body> tag.  Required for the
        paymentgatewayactivate.php redirect URL.
        """
        resp = self.session.get(
            f"{REZEXPERT_BASE}/book",
            params={"business_code": business_code},
            headers={"Accept": "text/html,application/xhtml+xml,*/*"},
        )
        m = re.search(r'rxc_api_session_id="([^"]+)"', resp.text)
        if not m:
            raise RuntimeError("Could not find rxc_api_session_id in booking page — are your session cookies valid?")
        return m.group(1)

    def initiate_payment(self, business_code: str, payment_amount: str) -> str:
        """
        Call s2rxc-rez-confirm then follow the server redirect through
        paymentgatewayactivate.php to obtain the NSW Government Payment Portal
        GPP-DIG-... reference string.

        Flow (confirmed from JS source):
          1. POST s2rxc-rez-confirm  → payment_gateway_trans_id (pgtid) + client_id
          2. GET  paymentgatewayactivate.php?bid=...&bgid=...&pgtid=...&ucid=...&rxcapisid=...
             → server-side redirects to pay.service.nsw.gov.au/?paymentReference=GPP-DIG-...
          3. Extract paymentReference query param and return it.
        """
        if not self.business_id or not self.business_group_id:
            raise RuntimeError("initiate_payment: call login() first")

        # Step 1 — confirm the reservation and trigger payment gateway transaction
        xml = f"""<request format="xml">
  <method name="s2rxc-rez-confirm" business_code="{business_code}" xmlmode="0"
      referral_code="" settlement_type_id="25" payment_account_id="0"
      payment_amount="{payment_amount}" points_amount="0"
      payment_gateway_trans_mode_id="1"
      rez_change_text="Submitted For Payment."/>
</request>"""
        root = self._post_xml(xml)
        confirm_el = self._ok(root, "s2rxc-rez-confirm")
        pgtid = confirm_el.get("payment_gateway_trans_id")
        if not pgtid:
            raise RuntimeError("s2rxc-rez-confirm: missing payment_gateway_trans_id in response")
        rezgroup = confirm_el.find("rezgroup")
        ucid = (
            rezgroup.find("client").get("client_id")
            if rezgroup is not None and rezgroup.find("client") is not None
            else self.client_id
        )

        # Step 2 — get the PHP page session ID and follow the payment redirect
        rxcapisid = self._get_page_session_id(business_code)
        activate_url = (
            f"{REZEXPERT_BASE}/s3files/server/paymentgateway/paymentgatewayactivate.php"
            f"?bid={self.business_id}&bgid={self.business_group_id}"
            f"&pgtid={pgtid}&ucid={ucid}&rxcapisid={rxcapisid}"
        )
        resp = self.session.get(
            activate_url,
            headers={"Accept": "text/html,application/xhtml+xml,*/*"},
            allow_redirects=True,
        )
        # Step 3 — extract GPP-DIG-... reference from final URL
        final_url = resp.url
        ref = parse_qs(urlparse(final_url).query).get("paymentReference", [None])[0]
        if not ref:
            raise RuntimeError(
                f"Could not extract paymentReference from redirect URL: {final_url!r}\n"
                f"HTTP status: {resp.status_code}"
            )
        return ref


# ── Payment client ─────────────────────────────────────────────────────────────

class PaymentClient:
    """
    Handles the NSW Government Payment Portal + Westpac Quickstream flow.

    Flow:
      1. GET pay.service.nsw.gov.au/?paymentReference={ref}
         → establishes XSRF-TOKEN cookie, fetches payment details
      2. POST api.quickstream.westpac.com.au/rest/v1/single-use-tokens
         → tokenises card, returns singleUseTokenId
      3. POST pay.service.nsw.gov.au/api/card/payment
         → submits token + reference, returns 202
      4. Poll pay.service.nsw.gov.au/api/payments/{ref}
         → wait for status to move from REQUESTED → COMPLETED
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
            ),
        })
        self._qs_auth = base64.b64encode(
            f"{QUICKSTREAM_PUBLIC_KEY}:".encode()
        ).decode()

    def _load_payment_page(self, payment_reference: str) -> dict:
        """
        Visit the payment portal page to establish the XSRF-TOKEN cookie,
        then fetch payment details JSON.
        Returns the payment details dict.
        """
        # Establish CSRF cookie
        self.session.get(
            f"{PAYMENT_PORTAL}/",
            params={"paymentReference": payment_reference},
        )
        # Fetch payment details
        resp = self.session.get(
            f"{PAYMENT_PORTAL}/api/payments/{payment_reference}",
            params={"_t": int(time.time() * 1000)},
            headers={"x-csrf-token": self.session.cookies.get("XSRF-TOKEN", "")},
        )
        resp.raise_for_status()
        return resp.json()

    def _tokenise_card(
        self,
        supplier_business_code: str,
        card_number: str,
        expiry_month: str,
        expiry_year: str,
        cvn: str,
        cardholder_name: str = "",
    ) -> str:
        """
        POST card details to Westpac Quickstream to get a single-use token.
        Returns the singleUseTokenId.
        Card details go directly to Quickstream — never touch our servers.
        """
        resp = self.session.post(
            f"{QUICKSTREAM_API}/single-use-tokens",
            json={
                "supplierBusinessCode": supplier_business_code,
                "accountType": "CREDIT_CARD",
                "cardholderName": cardholder_name,
                "cardNumber": card_number,
                "expiryDateMonth": expiry_month,
                "expiryDateYear": expiry_year,
                "cvn": cvn,
            },
            headers={
                "Authorization": f"Basic {self._qs_auth}",
                "Accept": "application/json",
                "Origin": QUICKSTREAM_API,
                "Referer": f"{QUICKSTREAM_API}/TrustedFrameCreditCardView",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("singleUseTokenId") or (
            data.get("data", [{}])[0].get("singleUseTokenId") if data.get("data") else None
        )
        if not token:
            raise RuntimeError(f"Quickstream tokenisation failed: {data}")
        return token

    def _submit_payment(
        self,
        payment_reference: str,
        single_use_token_id: str,
        payment_type: str = "MASTERCARD",
    ) -> None:
        """POST the card token to the NSW payment portal."""
        xsrf = self.session.cookies.get("XSRF-TOKEN", "")
        resp = self.session.post(
            f"{PAYMENT_PORTAL}/api/card/payment",
            json={
                "singleUseTokenId": single_use_token_id,
                "paymentReference": payment_reference,
                "paymentType": payment_type,
                "setRecurringPayment": False,
            },
            headers={
                "x-csrf-token": xsrf,
                "Accept": "application/json, text/plain, */*",
                "Referer": f"{PAYMENT_PORTAL}/?paymentReference={payment_reference}",
            },
        )
        if resp.status_code not in (200, 202):
            raise RuntimeError(f"Payment submission failed {resp.status_code}: {resp.text}")

    def _poll_status(self, payment_reference: str, timeout: int = 60) -> str:
        """Poll the payment portal until status moves past REQUESTED. Returns final status."""
        deadline = time.time() + timeout
        xsrf = self.session.cookies.get("XSRF-TOKEN", "")
        while time.time() < deadline:
            resp = self.session.get(
                f"{PAYMENT_PORTAL}/api/payments/{payment_reference}",
                params={"_t": int(time.time() * 1000)},
                headers={"x-csrf-token": xsrf},
            )
            resp.raise_for_status()
            status = resp.json().get("status", "")
            if status != "REQUESTED":
                return status
            time.sleep(2)
        raise RuntimeError("Timed out waiting for payment confirmation")

    def pay(
        self,
        payment_reference: str,
        card_number: str,
        expiry_month: str,
        expiry_year: str,
        cvn: str,
        cardholder_name: str = "",
    ) -> str:
        """
        Execute the full payment flow for a given payment reference.
        Returns the final payment status (e.g. "COMPLETED").
        """
        print("  Loading payment portal…")
        details = self._load_payment_page(payment_reference)
        supplier_code = details.get("agencyGatewayMappings", {}).get("CARD", "SNSW9399")

        print("  Tokenising card…")
        token = self._tokenise_card(
            supplier_business_code=supplier_code,
            card_number=card_number,
            expiry_month=expiry_month,
            expiry_year=expiry_year,
            cvn=cvn,
            cardholder_name=cardholder_name,
        )

        print("  Submitting payment…")
        self._submit_payment(payment_reference, token)

        print("  Waiting for confirmation…")
        return self._poll_status(payment_reference)


# ── Adjacency helpers ──────────────────────────────────────────────────────────

def adjacent_groups(sites: list[dict], avail: dict[str, bool], count: int) -> list[list[dict]]:
    """Return all runs of `count` consecutively adjacent available sites."""
    available_indices = [i for i, s in enumerate(sites) if avail.get(s["unit_id"])]
    groups = []
    for i in range(len(available_indices) - count + 1):
        window = available_indices[i:i + count]
        if window == list(range(window[0], window[0] + count)):
            groups.append([sites[j] for j in window])
    return groups


def non_adjacent_combos(sites: list[dict], avail: dict[str, bool], count: int) -> list[list[dict]]:
    available = [s for s in sites if avail.get(s["unit_id"])]
    return [list(c) for c in combinations(available, count)]


# ── Display ────────────────────────────────────────────────────────────────────

def show_availability(cg: dict, avail: dict[str, bool], count: int | None, specific: list[str] | None):
    sites = cg["sites"]

    if not sites:
        print("  (Camp-anywhere campground — no individual site selection)")
        total_avail = sum(1 for v in avail.values() if v)
        print(f"  {total_avail} spot(s) available.")
        return

    print(f"\n{'Site':<8} {'Status':<12} Unit ID")
    print("─" * 32)
    for s in sites:
        status = "✓ available" if avail.get(s["unit_id"]) else "✗ taken"
        marker = " ◀" if specific and s["label"] in specific else ""
        print(f"  {s['label']:<6} {status:<12} {s['unit_id']}{marker}")
    print()

    available_count = sum(1 for s in sites if avail.get(s["unit_id"]))
    print(f"{available_count} of {len(sites)} site(s) available.")

    if specific:
        print()
        requested = [s for s in sites if s["label"] in specific]
        taken = [s["label"] for s in requested if not avail.get(s["unit_id"])]
        if not taken:
            print(f"✓ Requested sites [{', '.join(specific)}] are all available.")
        else:
            print(f"✗ Site(s) {', '.join(taken)} from your request are not available.")

    if count:
        print(f"\nGroups of {count} adjacent available sites:")
        groups = adjacent_groups(sites, avail, count)
        if groups:
            for g in groups:
                labels = ", ".join(s["label"] for s in g)
                ids    = ", ".join(s["unit_id"] for s in g)
                print(f"  Sites [{labels}]  (unit_ids: {ids})")
        else:
            print("  None found.")
            avail_count = sum(1 for s in sites if avail.get(s["unit_id"]))
            if avail_count >= count:
                print(f"\nNon-adjacent combinations of {count} available sites:")
                for combo in non_adjacent_combos(sites, avail, count):
                    labels = ", ".join(s["label"] for s in combo)
                    ids    = ", ".join(s["unit_id"] for s in combo)
                    print(f"  Sites [{labels}]  (unit_ids: {ids})")
    print()


# ── Commands ───────────────────────────────────────────────────────────────────

def _login(client: RezExpertClient, business_code: str, args, cfg=None) -> dict:
    """
    Authenticate using config credentials when available, otherwise fall back
    to the manual-cookie workaround (--jsessionid / --phpsessid).
    cfg: dict from _load_campsite_cfg.
    """
    email    = cfg.get("email")    if cfg else None
    password = cfg.get("password") if cfg else None

    if email and password and not args.jsessionid:
        return client.login(business_code, email=email, password=password)
    return client.login(business_code)


def cmd_check(args):
    cg = resolve_campground(args.campground)
    check_out = add_nights(args.checkin, args.nights)

    print(f"Campground : {cg['name']}")
    print(f"Dates      : {args.checkin} → {check_out} ({args.nights} night(s))")
    print(f"Occupants  : {args.adults} adult(s)")

    client = RezExpertClient()
    if args.jsessionid:
        client.set_cookies(args.jsessionid, args.phpsessid)
    cfg = _load_campsite_cfg(args.real)
    _login(client, cg["business_code"], args, cfg=cfg)

    avail = client.get_availability(
        business_code=cg["business_code"],
        check_in=args.checkin,
        check_out=check_out,
        adults=args.adults,
    )

    if args.dump_ids:
        print("\nRaw unit_id dump:")
        for s in cg["sites"]:
            print(f"  label={s['label']}  unit_id={s['unit_id']}  available={avail.get(s['unit_id'])}")
        return

    specific = [s.strip() for s in args.sites.split(",")] if args.sites else None
    show_availability(cg, avail, args.count, specific)


def cmd_book(args):
    cfg = _load_campsite_cfg(args.real)

    cg = resolve_campground(args.campground)
    check_out = add_nights(args.checkin, args.nights)

    # Resolve which sites to book
    if cg["sites"]:
        if not args.sites:
            print("--sites is required for campgrounds with individual sites. Run `check` first.")
            sys.exit(1)
        requested_labels = [s.strip() for s in args.sites.split(",")]
        sites_to_book = [s for s in cg["sites"] if s["label"] in requested_labels]
        missing = set(requested_labels) - {s["label"] for s in sites_to_book}
        if missing:
            print(f"Unknown site label(s): {', '.join(missing)}")
            sys.exit(1)
    else:
        # Camp-anywhere: one reservation with unit_id "0"
        sites_to_book = [{"label": "any", "unit_id": "0"}]

    print(f"Campground : {cg['name']}")
    print(f"Dates      : {args.checkin} → {check_out} ({args.nights} night(s))")
    print(f"Adults     : {args.adults}")
    print(f"Sites      : {', '.join(s['label'] for s in sites_to_book)}")
    print()

    rez = RezExpertClient()
    if args.jsessionid:
        rez.set_cookies(args.jsessionid, args.phpsessid)

    info = _login(rez, cg["business_code"], args, cfg=cfg)
    print(f"Logged in as {info['first_name']} {info['last_name']} ({info['email']})\n")

    # Verify availability
    avail = rez.get_availability(
        business_code=cg["business_code"],
        check_in=args.checkin,
        check_out=check_out,
        adults=args.adults,
    )
    if cg["sites"]:
        taken = [s["label"] for s in sites_to_book if not avail.get(s["unit_id"])]
        if taken:
            print(f"✗ Site(s) {', '.join(taken)} are no longer available. Aborting.")
            sys.exit(1)
        print(f"✓ All requested sites are available.\n")

    # Get client info (vehicle instance ID)
    client_info = rez.get_client_info(cg["business_code"])
    vehicle_roiid = client_info["vehicles"][0]["roiid"] if client_info["vehicles"] else "0"
    mobile = client_info["mobile"] or cfg["phone"]

    pay = PaymentClient()

    for site in sites_to_book:
        label = site["label"]
        print(f"── Site {label} ──────────────────────────────")

        print("  Creating reservation…")
        temp_rez_id, confirmation, create_deposit = rez.create_reservation(
            business_code=cg["business_code"],
            unit_type_id=cg["unit_type_id"],
            unit_id=site["unit_id"],
            check_in=args.checkin,
            check_out=check_out,
            adults=args.adults,
        )
        print(f"  Reservation pending: {confirmation} (temp_id={temp_rez_id})")

        print("  Setting vehicle details…")
        temp_rez_id, deposit_owed = rez.set_vehicle(
            business_code=cg["business_code"],
            temp_rez_id=temp_rez_id,
            mobile=mobile,
            rez_object_instance_id=vehicle_roiid,
            rego=cfg["vehicle_rego"],
            state=cfg["vehicle_state"],
            fallback_deposit=create_deposit,
        )
        print(f"  Amount due: ${deposit_owed}")

        print("  Initiating payment…")
        payment_reference = rez.initiate_payment(cg["business_code"], deposit_owed)
        print(f"  Payment reference: {payment_reference}")

        if args.dry_run:
            print(f"  [DRY RUN] Would charge ${deposit_owed} to card ending "
                  f"{cfg['card_number'][-4:]} — stopping here.")
            print(f"  [DRY RUN] Booking reference (unpaid): {confirmation}\n")
            continue

        status = pay.pay(
            payment_reference=payment_reference,
            card_number=cfg["card_number"],
            expiry_month=cfg["card_expiry_month"],
            expiry_year=cfg["card_expiry_year"],
            cvn=cfg["card_cvv"],
            cardholder_name=cfg["card_name"],
        )

        if status == "COMPLETED":
            print(f"  ✓ Confirmed! Booking reference: {confirmation}\n")
        else:
            print(f"  ✗ Payment status: {status} — check rezexpert for details.\n")


# ── Utilities ──────────────────────────────────────────────────────────────────

def discover_campground(slug: str) -> dict:
    """
    Discover campground config by fetching the nationalparks.nsw.gov.au campground
    page and calling the DetailedAvailability API.

    slug: URL slug, e.g. "spring-gully-campground" or "frazer-campground"

    Returns a campground dict compatible with the CAMPGROUNDS registry.
    sites is always [] for dynamically discovered campgrounds — use --dump-ids
    to find unit IDs for site-specific campgrounds and add them to CAMPGROUNDS.
    """
    from datetime import date, timedelta

    url = f"{NP_CAMPGROUND_BASE}/{slug}"
    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    )

    resp = session.get(url)
    if resp.status_code == 404:
        sys.exit(
            f"Campground page not found: {url}\n"
            "Check the slug. Example: 'spring-gully-campground'"
        )
    resp.raise_for_status()

    m = re.search(r'data-context-item-id="(\{[^"]+\})"', resp.text)
    if not m:
        sys.exit(f"No availability widget found at {url} — is this a campground page?")
    context_id = m.group(1)

    m2 = (
        re.search(r'<input[^>]+name="__RequestVerificationToken"[^>]+value="([^"]+)"', resp.text)
        or re.search(r'<input[^>]+value="([^"]+)"[^>]+name="__RequestVerificationToken"', resp.text)
    )
    form_token = m2.group(1) if m2 else ""

    today    = date.today()
    tomorrow = today + timedelta(days=1)
    api_url  = (
        f"{NP_BASE}/npws/ReservationApi/DetailedAvailability"
        f"?contextItemId={quote(context_id)}"
        f"&dateFrom={quote(today.strftime('%d %b %Y'))}"
        f"&dateTo={quote(tomorrow.strftime('%d %b %Y'))}"
        f"&adults=1&children=0&infants=0"
        f"&formToken={quote(form_token)}"
    )
    api_resp = session.get(api_url)
    api_resp.raise_for_status()
    data = api_resp.json()

    if data.get("Error"):
        sys.exit(f"NP API error for '{slug}': {data['Error']}")

    locations = data.get("LocationTypes", [])
    if not locations:
        sys.exit(
            f"No booking config found for '{slug}' — campground may not support online booking.\n"
            f"Status: {data.get('Status', '?')}"
        )

    loc = locations[0]
    return {
        "name":          loc.get("UnitTypeName") or data.get("Name") or slug,
        "business_code": loc["BusinessCode"],
        "unit_type_id":  loc["UnitTypeId"],
        "sites":         [],
    }


def resolve_campground(key: str) -> dict:
    """
    Resolve a campground by short key (from the CAMPGROUNDS registry) or by
    nationalparks.nsw.gov.au URL slug (e.g. 'spring-gully-campground').
    """
    cg = CAMPGROUNDS.get(key)
    if cg is not None:
        return cg
    # Fall back to dynamic discovery via the NP website
    return discover_campground(key)


def add_nights(checkin: str, nights: int) -> str:
    from datetime import date, timedelta
    return str(date.fromisoformat(checkin) + timedelta(days=nights))


# ── Argument parsing ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="NSW National Parks campsite availability checker and booking tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # See what's free at Frazer for 2 nights from Sep 4:
  python book.py check --campground frazer --checkin 2026-09-04 --nights 2

  # Find 3 adjacent available sites at Frazer:
  python book.py check --campground frazer --checkin 2026-09-04 --nights 2 --count 3

  # Check if specific sites are available:
  python book.py check --campground frazer --checkin 2026-09-04 --nights 2 --sites 2,3,4

  # Check any campground by URL slug — no prior setup needed:
  python book.py check --campground spring-gully-campground --checkin 2026-10-10 --nights 3

  # Book 3 sites at Frazer, 2 adults each, for a long weekend:
  python book.py book  --campground frazer --checkin 2026-09-04 --nights 3 --sites 2,3,4 --adults 2

  # Book a camp-anywhere site (no --sites needed):
  python book.py book  --campground policemans-point --checkin 2026-10-01 --nights 1 --adults 1

  # Dry run — full flow up to payment initiation, no card charged:
  python book.py book  --campground frazer --checkin 2026-09-04 --nights 2 --sites 2,3,4 --adults 1 --dry-run

  # Dry run with fake config (default) — safe to run any time:
  python book.py book  --campground spring-gully-campground --checkin 2026-10-10 --nights 2 --adults 2 --dry-run

  # Use real config (--real flag) — actually charges the card:
  python book.py book  --campground frazer --checkin 2026-09-04 --nights 2 --sites 2,3,4 --adults 1 --real
""",
    )
    sub = p.add_subparsers(dest="command", required=True)

    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--campground", required=True,
                        help=f"Campground key. Known: {', '.join(CAMPGROUNDS)}")
    shared.add_argument("--checkin",  required=True, metavar="YYYY-MM-DD")
    shared.add_argument("--nights",   type=int, required=True)
    shared.add_argument("--adults",   type=int, default=1, help="Adults per site (default: 1)")
    shared.add_argument("--jsessionid", default=None,
                        help="JSESSIONID cookie from browser (until login is automated)")
    shared.add_argument("--phpsessid",  default=None,
                        help="PHPSESSID cookie from browser (until login is automated)")

    chk = sub.add_parser("check", parents=[shared], help="Show campsite availability")
    chk.add_argument("--count",    type=int, default=None,
                     help="Find groups of N adjacent available sites")
    chk.add_argument("--sites",    default=None,
                     help="Comma-separated site numbers to check, e.g. 2,3,4")
    chk.add_argument("--dump-ids", action="store_true",
                     help="Print raw unit_ids (useful for adding new campgrounds)")

    bk = sub.add_parser("book", parents=[shared], help="Book specific sites")
    bk.add_argument("--sites", default=None,
                    help="Comma-separated site numbers to book, e.g. 2,3,4 "
                         "(omit for camp-anywhere campgrounds)")
    bk.add_argument("--dry-run", action="store_true",
                    help="Run the full booking flow up to payment initiation, "
                         "then stop without charging the card")
    bk.add_argument("--real", action="store_true",
                    help="Use 'campsite' section in config.json (real card details); default is 'campsite_fake'")

    return p


def main():
    args = build_parser().parse_args()
    {"check": cmd_check, "book": cmd_book}[args.command](args)


if __name__ == "__main__":
    main()
