"""Telegram group bot: staff post "sold ..." / "booked ..." in a group and the
system records it after a one-tap Confirm.

Standard library only (urllib + threading). Runs as a background thread inside
the FastAPI process, using Telegram long polling, so no public URL is needed.

Environment variables
  TELEGRAM_BOT_TOKEN   bot token from @BotFather            (required)
  TELEGRAM_GROUP_ID    the group's chat id, e.g. -1001234567 (required)
  TELEGRAM_STAFF       "telegram_id:system_username,..."     (required)
                       e.g. 111222333:abel,444555666:sara
"""
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request

PENDING_TTL = 600  # seconds a confirmation stays valid

SOLD_WORDS = {"sold", "sale", "sell", "sale:", "sold:"}
BOOK_WORDS = {"book", "booked", "booking", "reserve", "reserved", "book:", "booked:"}
FILLER = {"to", "for", "by", "at", "price", "birr", "etb", "br", "paid", "deposit", "customer", "phone", "tel", "with", "via", "-", ":"}

PHONE_RE = re.compile(r"(?<![A-Za-z0-9])(?:\+?251|0)[\s-]?[79](?:[\s-]?\d){8}(?![A-Za-z0-9])")
AMOUNT_RE = re.compile(r"^(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?([kKmM])?$")

_pending = {}
_lock = threading.Lock()
_state = {"running": False, "last_error": "", "handled": 0}


# ------------------------------------------------------------------ config
def _token(): return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
def _group(): return os.getenv("TELEGRAM_GROUP_ID", "").strip()
def _api_base(): return os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")


def staff_map() -> dict:
    out = {}
    for part in os.getenv("TELEGRAM_STAFF", "").split(","):
        if ":" in part:
            tid, name = part.split(":", 1)
            if tid.strip() and name.strip():
                out[tid.strip()] = name.strip()
    return out


def configured() -> bool:
    return bool(_token() and _group() and staff_map())


def status() -> dict:
    return {"group_commands_configured": configured(), "group_commands_running": _state["running"],
            "group_commands_handled": _state["handled"], "group_commands_error": _state["last_error"]}


# ------------------------------------------------------------------ telegram api
def _call(method: str, payload: dict, timeout: int = 15):
    req = urllib.request.Request(f"{_api_base()}/bot{_token()}/{method}", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def _say(chat_id, text, reply_to=None, buttons=None):
    p = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if reply_to: p["reply_to_message_id"] = reply_to; p["allow_sending_without_reply"] = True
    if buttons: p["reply_markup"] = {"inline_keyboard": [buttons]}
    try: return _call("sendMessage", p)
    except Exception as e: _state["last_error"] = f"send failed: {e}"


# ------------------------------------------------------------------ parser
def _num(tok: str):
    m = AMOUNT_RE.match(tok)
    if not m: return None
    v = float(tok[:len(tok) - (1 if m.group(2) else 0)].replace(",", ""))
    if m.group(2): v *= 1000 if m.group(2).lower() == "k" else 1_000_000
    return v


BANKS = ["awash", "cbe", "dashen", "abyssinia", "telebirr", "zemen", "nib", "hibret", "oromia", "wegagen", "enat", "bunna", "berhan", "lion", "amhara", "siinqee"]
SALE_RE = re.compile(r"\b(sold|sale|sell|prosses|proccess|process|processed|processing)\b", re.I)
BOOK_RE = re.compile(r"\b(book|booked|booking|reserve|reserved|deposit)\b", re.I)
NOISE = {"bank", "cash", "chassis", "chasis", "vin", "price", "with", "no", "without", "commission", "comission", "and", "the", "a", "birr", "etb",
         "prosses", "proccess", "process", "processed", "processing", "sold", "sale", "sell", "book", "booked", "booking", "reserve", "reserved", "deposit", "to", "for"}


def _norm(x): return re.sub(r"[^a-z0-9]", "", (x or "").lower())


def parse(text: str, find_vehicles) -> dict:
    """Understand the group's own style, e.g.
        Bank prosses bz3x / Chassis 5722 / Price 7,800,000 / With commission / Awash
    and the explicit style  'sold <VIN> <customer> <phone> <price> cash'.
    find_vehicles(token) -> list of vehicle dicts whose chassis equals or ends with token."""
    text = (text or "").strip()
    if not text: return {}
    body = re.sub(r"^/(\w+)(@\w+)?", lambda m: m.group(1), text)  # '/sold@bot x' -> 'sold x'
    if BOOK_RE.search(body): kind = "booking"
    elif SALE_RE.search(body): kind = "sale"
    else: return {}  # ordinary chat: stay silent

    work = body
    phone = ""
    m = PHONE_RE.search(work)
    if m:
        phone = re.sub(r"[\s-]", "", m.group(0)); work = work[:m.start()] + " " + work[m.end():]

    # chassis: "chassis 5722" / "vin 5722" or a full VIN-looking token
    chassis = ""
    cm = re.search(r"\b(?:chassis|chasis|chasi|vin|ch)\b\s*(?:no\.?|number|#|:|-)?\s*([A-Za-z0-9]{3,20})", work, re.I)
    if cm:
        chassis = cm.group(1); work = work[:cm.start()] + " " + work[cm.end():]
    else:
        for tok in work.split():
            c = tok.strip(".,;:()[]")
            if 8 <= len(c) <= 20 and re.search(r"\d", c) and re.search(r"[A-Za-z]", c) and find_vehicles(c.upper()):
                chassis = c; work = work.replace(tok, " ", 1); break
    if not chassis:
        return {"error": "I could not see the chassis. Write e.g. 'Chassis 5722' (last 4 digits of the VIN)."}

    # price
    amount = None
    pm = re.search(r"\b(?:price|amount|deposit|birr|etb)\b\s*[:\-]?\s*([\d][\d,\.]*\s*[kKmM]?)", work, re.I)
    if pm:
        amount = _num(pm.group(1).replace(" ", "").rstrip(".")); work = work[:pm.start()] + " " + work[pm.end():]
    else:
        for tok in work.split():
            n = _num(tok.strip(".,;:()[]"))
            if n and (amount is None or n > amount): amount = n

    # commission
    commission = ""
    if re.search(r"\b(no|without)\s+commi?ss?ion\b", work, re.I): commission = "No commission"
    elif re.search(r"\bcommi?ss?ion\b", work, re.I): commission = "With commission"

    # payment + bank name (typos like 'Awach' are matched to the closest bank)
    import difflib
    words = [w.strip(".,;:()[]").lower() for w in work.split()]
    bank = ""
    for w in words:
        hit = difflib.get_close_matches(w, BANKS, n=1, cutoff=0.8) if len(w) >= 3 else []
        if hit: bank = hit[0].upper() if len(hit[0]) <= 4 else hit[0].title(); break
    payment = "Bank" if (re.search(r"\bbank\b", work, re.I) or bank) else ("Cash" if re.search(r"\bcash\b", work, re.I) else "")
    if not payment and kind == "sale":
        return {"error": "Please say how it was paid: 'cash' or 'bank' (with the bank name)."}
    if payment == "Bank" and not bank: bank = "Bank"

    # optional customer name ("customer Abebe"), plus a car hint (bz3x, wuling...) from the leftover words
    customer = ""
    nm = re.search(r"\b(?:customer|client|name)\b\s*[:\-]?\s*([A-Za-z][A-Za-z ]{1,60})", work, re.I)
    if nm: customer = nm.group(1).strip()
    hint_words = [w for w in words if w and w not in NOISE and not _num(w) and w not in BANKS
                  and not difflib.get_close_matches(w, BANKS, n=1, cutoff=0.8) and not (customer and w in customer.lower().split())
                  and not _norm(w) == _norm(chassis)]

    found = find_vehicles(chassis.upper()) or []
    if kind == "sale":
        live = [v for v in found if v.get("status") in ("In Stock", "Booked")]
        found = live or found
    if len(found) > 1 and hint_words:
        h = [_norm(w) for w in hint_words]
        narrowed = [v for v in found if any(x and (x in _norm(f"{v.get('brand')}{v.get('model')}") or _norm(v.get("model")) in x) for x in h)]
        found = narrowed or found
    if not found:
        return {"error": f"No vehicle found with chassis ending/equal '{chassis}'."}
    if len(found) > 1:
        lst = "\n".join(f"- {v['chassis_number']} {v.get('brand','')} {v.get('model','')} ({v.get('status','')})" for v in found[:6])
        return {"error": f"More than one car matches '{chassis}'. Add more VIN digits or the model:\n{lst}"}
    v = found[0]
    if kind == "sale" and not amount:
        return {"error": "Please include the price, e.g. 'Price 7,800,000'."}
    notes = "Recorded from Telegram group" + (f"; {commission}" if commission else "") + (f"; hint: {' '.join(hint_words)}" if hint_words else "")
    return {"kind": kind, "vin": v["chassis_number"], "vehicle": v, "customer": customer or "Not given (Telegram)", "phone": phone,
            "amount": amount or 0, "payment": payment or "Cash", "bank": bank, "commission": commission, "notes": notes}


def _describe(p: dict) -> str:
    v = p["vehicle"]
    car = " ".join(str(x) for x in (v.get("brand"), v.get("model"), v.get("year"), v.get("color")) if x)
    lines = ["SALE" if p["kind"] == "sale" else "BOOKING", f"Car: {car}", f"VIN: {p['vin']}", f"Status now: {v.get('status', '')}",
             f"Customer: {p['customer']}"]
    if p["phone"]: lines.append(f"Phone: {p['phone']}")
    if p.get("commission"): lines.append(f"Commission: {p['commission']}")
    if p["amount"]: lines.append(("Price: " if p["kind"] == "sale" else "Deposit: ") + f"{p['amount']:,.0f} ETB")
    if p["kind"] == "sale": lines.append("Payment: " + (f"Bank ({p['bank']})" if p["payment"] == "Bank" else "Cash"))
    return "\n".join(lines)


HELP = ("Hallel Inventory bot\n\nPost the receipt photo with this caption (as you already do):\n"
        "Bank prosses bz3x\nChassis 5722\nPrice 7,800,000\nWith commission\nAwash\n\n"
        "Cash: 'Cash prosses wuling / Chassis 5490 / Price 6,400,000'\n"
        "Booking: 'Booked bz3x / Chassis 5722 / Deposit 100,000'\n\n"
        "I show a summary; tap Confirm to save. /myid shows your Telegram id.")


# ------------------------------------------------------------------ update handling
def _handle_message(msg: dict, find_vehicle, execute):
    chat = msg.get("chat", {})
    chat_id = str(chat.get("id", ""))
    sender = msg.get("from", {}) or {}
    sid = str(sender.get("id", ""))
    text = (msg.get("text") or msg.get("caption") or "").strip()
    if not text: return
    cmd = text.split()[0].lower().split("@")[0]

    if cmd == "/myid":
        _say(chat_id, f"Your Telegram id: {sid}\nThis chat id: {chat_id}", msg.get("message_id")); return
    if chat_id != _group(): return  # ignore every other chat
    if cmd in ("/help", "/start"):
        _say(chat_id, HELP, msg.get("message_id")); return

    user = staff_map().get(sid)
    parsed = parse(text, find_vehicle)
    if not parsed: return
    if not user:
        _say(chat_id, f"Your Telegram id ({sid}) is not linked to a system user. Ask the admin to add it to TELEGRAM_STAFF.", msg.get("message_id")); return
    if "error" in parsed:
        _say(chat_id, parsed["error"] + "\n\nSend /help for the format.", msg.get("message_id")); return

    token = secrets.token_hex(4)
    with _lock:
        now = time.time()
        for k in [k for k, v in _pending.items() if v["exp"] < now]: _pending.pop(k, None)
        _pending[token] = {"p": parsed, "user": user, "sid": sid, "exp": now + PENDING_TTL}
    _say(chat_id, _describe(parsed) + f"\n\nRecorded by: {user}",
         msg.get("message_id"),
         [{"text": "Confirm", "callback_data": f"y:{token}"}, {"text": "Cancel", "callback_data": f"n:{token}"}])


def _handle_callback(cb: dict, execute):
    data = cb.get("data", "")
    msg = cb.get("message", {}) or {}
    chat_id, mid = msg.get("chat", {}).get("id"), msg.get("message_id")
    sid = str(cb.get("from", {}).get("id", ""))
    act, _, token = data.partition(":")

    def answer(t):
        try: _call("answerCallbackQuery", {"callback_query_id": cb.get("id"), "text": t})
        except Exception: pass

    def edit(t):
        try: _call("editMessageText", {"chat_id": chat_id, "message_id": mid, "text": t})
        except Exception as e: _state["last_error"] = f"edit failed: {e}"

    with _lock:
        item = _pending.get(token)
        if not item or item["exp"] < time.time():
            _pending.pop(token, None); answer("This request expired."); edit("Expired. Please send the message again."); return
        if sid != item["sid"] and sid not in staff_map():
            answer("Only staff can do this."); return
        if sid != item["sid"] and act == "y":
            answer("Only the person who sent it can confirm."); return
        _pending.pop(token, None)

    if act != "y":
        answer("Cancelled"); edit("Cancelled. Nothing was saved."); return
    ok, message = execute(item["p"]["kind"], item["user"], item["p"])
    _state["handled"] += 1 if ok else 0
    answer("Saved" if ok else "Not saved")
    head = _describe(item["p"]).split("\n")[0]
    edit((f"SAVED - {head.title()} recorded\n" if ok else "NOT SAVED\n") + _describe(item["p"]) + f"\nBy: {item['user']}\n\n{message}")


# ------------------------------------------------------------------ polling loop
def _loop(find_vehicle, execute):
    offset = None
    try: _call("deleteWebhook", {"drop_pending_updates": False})
    except Exception as e: _state["last_error"] = f"deleteWebhook: {e}"
    # skip anything sent while the server was down so old commands never replay
    try:
        r = _call("getUpdates", {"timeout": 0, "offset": -1})
        if r.get("result"): offset = r["result"][-1]["update_id"] + 1
    except Exception: pass
    while _state["running"]:
        try:
            r = _call("getUpdates", {"timeout": 25, "offset": offset, "allowed_updates": ["message", "callback_query"]}, timeout=40)
            _state["last_error"] = ""
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                try:
                    if "callback_query" in u: _handle_callback(u["callback_query"], execute)
                    elif "message" in u: _handle_message(u["message"], find_vehicle, execute)
                except Exception as e:
                    _state["last_error"] = f"handler: {e}"
        except urllib.error.HTTPError as e:
            _state["last_error"] = f"Telegram HTTP {e.code}" + (" (another copy of the server is also polling this bot)" if e.code == 409 else "")
            time.sleep(10)
        except Exception as e:
            _state["last_error"] = f"{type(e).__name__}: {e}"
            time.sleep(5)


def start(find_vehicle, execute) -> bool:
    """find_vehicle(VIN)->dict|None ; execute(kind, username, parsed)->(ok, message)."""
    if not configured() or _state["running"]: return False
    _state["running"] = True
    threading.Thread(target=_loop, args=(find_vehicle, execute), daemon=True, name="telegram-group-bot").start()
    return True


def stop():
    _state["running"] = False
