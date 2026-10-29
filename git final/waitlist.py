"""Waiting list: match newly received vehicles to customers who asked for them,
and send ONE text (SMS) to the owner/staff listing who to call.

Configuration: environment variables, set where the backend is hosted, then restart it.

  SMS_ALERT_NUMBERS     numbers that receive the alert, e.g. 0911223344,0922334455
  SMS_PROVIDER          "android"  (recommended: sends from a phone with a SIM, via the free SMS Gateway app)
                        or "twilio"
  --- android (sms-gate.app cloud server) ---
  SMS_GATE_USER, SMS_GATE_PASSWORD   shown on the Home tab of the SMS Gateway app
  SMS_GATE_URL          optional, default https://api.sms-gate.app
  --- twilio ---
  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM  (an alphanumeric sender ID registered with Twilio for Ethiopia)
  --- optional extra channel ---
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS
  RESTOCK_BATCH_SECONDS optional, default 20. Vehicles received within this window are combined
                        into one message (so an Excel import sends one text, not fifty).
Uses only the Python standard library, so nothing new has to be installed.
"""
import json
import logging
import os
import re
import base64
import threading
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("hallel.waitlist")

TELEGRAM_LIMIT = 3800  # Telegram allows 4096 characters per message


# ---------------------------------------------------------------- matching
def _norm(value) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _contains(wanted: str, haystack: str, short: str = "") -> bool:
    """wanted appears in haystack ('seagull' in 'byd seagull'), or the vehicle's own short value
    appears in what the customer typed ('seagull' in 'seagull flying edition')."""
    if not wanted:
        return True
    if wanted in haystack:
        return True
    return len(short) >= 3 and short in wanted


def request_matches(req, veh) -> bool:
    """Does a newly received vehicle satisfy a waiting customer's request?

    A request must name at least a brand or a model, so it can never match everything.
    Year, type and colour only narrow the match when the customer gave them.
    """
    brand, model = _norm(req.brand), _norm(req.model)
    if not brand and not model:
        return False
    v_brand, v_model = _norm(veh.brand), _norm(veh.model)
    text = f"{v_brand} {v_model}".strip()  # people often type "BYD Seagull" in the brand box
    if not _contains(brand, text, v_brand) or not _contains(model, text, v_model):
        return False
    year = getattr(veh, "year", None)
    if getattr(req, "year_from", None) and (year is None or year < req.year_from):
        return False
    if getattr(req, "year_to", None) and (year is None or year > req.year_to):
        return False
    wanted_type = _norm(getattr(req, "vehicle_type", ""))
    if wanted_type and wanted_type != _norm(veh.vehicle_type):
        return False
    wanted_color = _norm(getattr(req, "color", ""))
    v_color = _norm(veh.color)
    if wanted_color and not (wanted_color in v_color or (len(v_color) >= 3 and v_color in wanted_color)):
        return False
    return True


# ---------------------------------------------------------------- message
def vehicle_info(v) -> dict:
    return {
        "brand": v.brand or "", "model": v.model or "", "year": v.year, "type": v.vehicle_type or "",
        "color": v.color or "", "vin": v.chassis_number, "store": v.store or "",
    }


def customer_info(w) -> dict:
    wants = " ".join(x for x in [w.brand, w.model] if x).strip()
    extras = []
    yf, yt = getattr(w, "year_from", None), getattr(w, "year_to", None)
    if yf and yt:
        extras.append(str(yf) if yf == yt else f"{yf}-{yt}")
    elif yf:
        extras.append(f"{yf} or newer")
    elif yt:
        extras.append(f"up to {yt}")
    if getattr(w, "color", ""):
        extras.append(w.color)
    return {"name": w.customer_name, "phone": w.customer_phone, "wants": wants + (f" ({', '.join(extras)})" if extras else ""), "notes": w.notes or ""}


def build_messages(items: list) -> list:
    """items: [{'vehicle': {...}, 'customers': [{...}]}]. Returns a list of message texts."""
    blocks = []
    for it in items:
        v, customers = it["vehicle"], it["customers"]
        title = " ".join(str(x) for x in [v["brand"], v["model"], v["year"] or ""] if x)
        details = ", ".join(x for x in [v["color"], v["type"]] if x)
        lines = [f"🚗 {title}" + (f" - {details}" if details else ""), f"VIN: {v['vin']}" + (f" | Branch: {v['store']}" if v["store"] else "")]
        lines.append(f"Customers waiting ({len(customers)}):")
        for c in customers:
            row = f"• {c['name']} - {c['phone']}"
            if c["wants"]:
                row += f" (wants {c['wants']})"
            if c["notes"]:
                row += f" - {c['notes']}"
            lines.append(row)
        blocks.append("\n".join(lines))
    head = "📣 Restock alert - Hallel Inventory\nA vehicle customers asked for has arrived. Please call them:\n"
    messages, current = [], head
    for b in blocks:
        if len(current) + len(b) + 2 > TELEGRAM_LIMIT and current != head:
            messages.append(current.rstrip())
            current = head
        current += "\n" + b + "\n"
    if current != head:
        messages.append(current.rstrip())
    return messages


# ---------------------------------------------------------------- telegram
def _chat_ids() -> list:
    return [x.strip() for x in os.getenv("TELEGRAM_CHAT_IDS", "").split(",") if x.strip()]


def telegram_configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip() and _chat_ids())


def send_telegram(text: str) -> dict:
    """Send one text to every configured chat. Returns {'sent': n, 'errors': [..]}; never raises."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    base = os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")
    result = {"sent": 0, "errors": []}
    if not token or not _chat_ids():
        result["errors"].append("Telegram is not set up (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_IDS).")
        return result
    for chat in _chat_ids():
        body = json.dumps({"chat_id": chat, "text": text, "disable_web_page_preview": True}).encode()
        req = urllib.request.Request(f"{base}/bot{token}/sendMessage", data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if 200 <= resp.status < 300:
                    result["sent"] += 1
                else:
                    result["errors"].append(f"chat {chat}: HTTP {resp.status}")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read().decode()).get("description", "")
            except Exception:
                pass
            result["errors"].append(f"chat {chat}: {detail or e.reason}")
        except Exception as e:  # network down, DNS, timeout...
            result["errors"].append(f"chat {chat}: {e}")
    if result["errors"]:
        log.warning("Telegram problems: %s", result["errors"])
    return result


# ---------------------------------------------------------------- sms
SMS_MAX = 450  # about three SMS segments per text; keeps cost and splitting predictable


def normalize_phone(raw: str) -> str:
    """Ethiopian numbers to international form: 0911223344 / 911223344 / 251911223344 -> +251911223344."""
    digits = re.sub(r"[^\d+]", "", str(raw or ""))
    if digits.startswith("+"):
        return digits
    digits = digits.lstrip("0") if digits.startswith("0") and not digits.startswith("00") else digits
    if digits.startswith("00"):
        return "+" + digits[2:]
    if digits.startswith("251"):
        return "+" + digits
    return "+251" + digits


def _sms_numbers() -> list:
    out = []
    for x in os.getenv("SMS_ALERT_NUMBERS", "").split(","):
        if x.strip():
            n = normalize_phone(x)
            if n not in out:
                out.append(n)
    return out


def sms_provider() -> str:
    return os.getenv("SMS_PROVIDER", "android").strip().lower()


def sms_configured() -> bool:
    if not _sms_numbers():
        return False
    if sms_provider() == "twilio":
        return all(os.getenv(k, "").strip() for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM"))
    return all(os.getenv(k, "").strip() for k in ("SMS_GATE_USER", "SMS_GATE_PASSWORD"))


def alerts_configured() -> bool:
    return sms_configured() or telegram_configured()


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def _http_error_text(e: urllib.error.HTTPError) -> str:
    try:
        raw = e.read().decode()
        data = json.loads(raw)
        return str(data.get("message") or data.get("description") or raw)[:200]
    except Exception:
        return str(e.reason)


def send_sms(text: str) -> dict:
    """Send one text to every number in SMS_ALERT_NUMBERS. Returns {'sent': n, 'errors': [..]}; never raises."""
    result = {"sent": 0, "errors": []}
    numbers = _sms_numbers()
    if not sms_configured():
        result["errors"].append("SMS is not set up (see SMS_PROVIDER and the settings for it).")
        return result
    try:
        if sms_provider() == "twilio":
            sid, tok, sender = os.getenv("TWILIO_ACCOUNT_SID").strip(), os.getenv("TWILIO_AUTH_TOKEN").strip(), os.getenv("TWILIO_FROM").strip()
            base = os.getenv("TWILIO_API_BASE", "https://api.twilio.com").rstrip("/")
            for to in numbers:  # Twilio sends one recipient per request
                body = urllib.parse.urlencode({"To": to, "From": sender, "Body": text}).encode()
                req = urllib.request.Request(f"{base}/2010-04-01/Accounts/{sid}/Messages.json", data=body, headers={"Authorization": _basic(sid, tok), "Content-Type": "application/x-www-form-urlencoded"})
                try:
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        if 200 <= resp.status < 300:
                            result["sent"] += 1
                        else:
                            result["errors"].append(f"{to}: HTTP {resp.status}")
                except urllib.error.HTTPError as e:
                    result["errors"].append(f"{to}: {_http_error_text(e)}")
        else:  # android: SMS Gateway for Android (sms-gate.app); the message leaves from your phone's SIM
            base = os.getenv("SMS_GATE_URL", "https://api.sms-gate.app").rstrip("/")
            user, pw = os.getenv("SMS_GATE_USER").strip(), os.getenv("SMS_GATE_PASSWORD").strip()
            body = json.dumps({"textMessage": {"text": text}, "phoneNumbers": numbers, "priority": 100}).encode()
            req = urllib.request.Request(f"{base}/3rdparty/v1/messages", data=body, headers={"Authorization": _basic(user, pw), "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    if 200 <= resp.status < 300:  # 202 = queued on the phone
                        result["sent"] += len(numbers)
                    else:
                        result["errors"].append(f"HTTP {resp.status}")
            except urllib.error.HTTPError as e:
                result["errors"].append(_http_error_text(e))
    except Exception as e:  # network down, DNS, timeout...
        result["errors"].append(str(e))
    if result["errors"]:
        log.warning("SMS problems: %s", result["errors"])
    return result


def build_sms(items: list) -> list:
    """Compact plain-text version (no emoji, so it stays cheap): list of texts of at most SMS_MAX characters."""
    lines = []
    for it in items:
        v = it["vehicle"]
        title = " ".join(str(x) for x in [v["brand"], v["model"], v["year"] or "", v["color"]] if x)
        people = "; ".join(f"{c['name']} {c['phone']}" for c in it["customers"])
        lines.append(f"{title} (VIN {v['vin']}): {people}")
    head = "Hallel restock - call: "
    messages, current = [], head
    for line in lines:
        piece = line + "\n"
        if len(current) + len(piece) > SMS_MAX and current != head:
            messages.append(current.rstrip())
            current = head
        while len(head) + len(piece) > SMS_MAX:  # one very long line: cut it into pieces
            cut = SMS_MAX - len(head)
            messages.append((current if current != head else head) + piece[:cut])
            piece, current = piece[cut:], head
        current += piece
    if current != head:
        messages.append(current.rstrip())
    return messages


# ---------------------------------------------------------------- batching
_lock = threading.Lock()
_pending: list = []
_timer = None


def _flush():
    global _timer
    with _lock:
        items, _pending[:] = list(_pending), []
        _timer = None
    if not items:
        return
    if sms_configured():
        for text in build_sms(items):
            send_sms(text)
    if telegram_configured():
        for text in build_messages(items):
            send_telegram(text)


def queue_restock(vehicle: dict, customers: list) -> None:
    """Called once per received vehicle that has waiting customers. Sends after a short quiet
    window so several vehicles (for example an Excel import) become one message."""
    global _timer
    delay = float(os.getenv("RESTOCK_BATCH_SECONDS", "20"))
    with _lock:
        _pending.append({"vehicle": vehicle, "customers": customers})
        if _timer is None:
            _timer = threading.Timer(delay, _flush)
            _timer.daemon = True
            _timer.start()
