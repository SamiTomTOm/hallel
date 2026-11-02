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
# Fallback values, used only when the host has no environment variable set.
# ONLY fill these in if your GitHub repository is PRIVATE; otherwise anyone can take over the bot.
FALLBACK_TOKEN = "9221246170:AAEhnRl9vxtYbWfe4oJ9Ev3mrx9ddNzyQuw"   # e.g. "123456:ABC..."
FALLBACK_GROUP_ID = "-1003650826154"  # e.g. "-1001234567890"
FALLBACK_STAFF = "327504061:admin"   # e.g. "111222333:abel,444555666:sara"


def _token(): return os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or FALLBACK_TOKEN.strip()
def _group(): return os.getenv("TELEGRAM_GROUP_ID", "").strip() or FALLBACK_GROUP_ID.strip()
def _groups_fallback() -> set:
    """One or many group ids, separated by commas: "-100111,-100222"."""
    return {g.strip() for g in _group().split(",") if g.strip()}
def _groups() -> set: return groups_all()
def _api_base(): return os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")


def _db():
    from database import SessionLocal
    return SessionLocal()


def staff_map() -> dict:
    """telegram id -> system username. Links made on the Staff accounts page win over the fallback list."""
    out = {}
    for part in (os.getenv("TELEGRAM_STAFF", "") or FALLBACK_STAFF).split(","):
        if ":" in part:
            tid, name = part.split(":", 1)
            if tid.strip() and name.strip():
                out[tid.strip()] = name.strip()
    try:
        import models
        db = _db()
        try:
            for r in db.query(models.TelegramLink).all(): out[r.telegram_id] = r.username
        finally: db.close()
    except Exception as e:
        print(f"[telegram-bot] could not read links: {e}", flush=True)
    return out


def groups_all() -> set:
    out = set(_groups_fallback())
    try:
        import models
        db = _db()
        try:
            for r in db.query(models.TelegramGroup).all(): out.add(r.chat_id)
        finally: db.close()
    except Exception as e:
        print(f"[telegram-bot] could not read groups: {e}", flush=True)
    return out


def _role_of(username: str) -> str:
    try:
        import models
        db = _db()
        try:
            u = db.query(models.User).filter(models.User.username == username, models.User.is_active == True).first()
            return u.role if u else ""
        finally: db.close()
    except Exception:
        return ""


def configured() -> bool:
    return bool(_token() and groups_all() and staff_map())


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
        "I show a summary; tap Confirm to save.\n\n"
        "Other commands:\n"
        "stock bz3x  - what is in stock (model, color or branch; or just 'stock')\n"
        "summary  - today's sales and bookings\n"
        "/myid shows your Telegram id.")


# ------------------------------------------------------------------ stock lookup and daily summary
def stock_text(vehicles: list, query: str = "") -> str:
    """vehicles: dicts with brand, model, year, color, store, chassis_number (all 'In Stock')."""
    words = [w for w in re.split(r"\s+", (query or "").lower()) if w]
    pick = [v for v in vehicles if all(w in f"{v.get('brand','')} {v.get('model','')} {v.get('year','')} {v.get('color','')} {v.get('store','')}".lower() for w in words)]
    if not pick:
        return f"No '{query}' in stock right now." if words else "Nothing in stock."
    groups = {}
    for v in pick:
        key = " ".join(str(x) for x in (v.get("brand"), v.get("model")) if x) or "Unknown"
        groups.setdefault(key, []).append(v)
    lines = [f"In stock{(' - ' + query) if words else ''}: {len(pick)} car(s)"]
    shown = 0
    for name, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"\n{name} ({len(items)})")
        for v in items:
            if shown >= 25:
                break
            lines.append(f"- {v.get('year') or ''} {v.get('color') or ''} | {v.get('store') or ''} | ...{str(v.get('chassis_number',''))[-4:]}".replace("  ", " "))
            shown += 1
    if shown < len(pick):
        lines.append(f"\n...and {len(pick) - shown} more. Add a model or color to narrow it down.")
    return "\n".join(lines)


def summary_lines(day_label: str, sales: list, bookings: list, in_stock: int, booked: int) -> str:
    """sales: dicts sold_by, sale_price, commission ; bookings: dicts booking_amount."""
    total = sum(float(s.get("sale_price") or 0) for s in sales)
    lines = [f"Hallel daily summary - {day_label}", "", f"Sales today: {len(sales)}" + (f" | total {total:,.0f}" if sales else "")]
    by = {}
    for s in sales:
        r = by.setdefault(s.get("sold_by") or "?", [0, 0.0]); r[0] += 1; r[1] += float(s.get("sale_price") or 0)
    for name, (n, amt) in sorted(by.items(), key=lambda kv: -kv[1][0]):
        lines.append(f"  - {name}: {n} ({amt:,.0f})")
    withc = sum(1 for s in sales if "commission" in (s.get("commission") or "").lower() and not (s.get("commission") or "").lower().startswith("no"))
    nocom = sum(1 for s in sales if (s.get("commission") or "").lower().startswith("no"))
    if withc or nocom:
        lines.append(f"  Commission: {withc} with, {nocom} without")
    dep = sum(float(b.get("booking_amount") or 0) for b in bookings)
    lines.append(f"Bookings today: {len(bookings)}" + (f" | deposits {dep:,.0f}" if bookings else ""))
    lines.append(f"In stock: {in_stock} | Booked: {booked}")
    return "\n".join(lines)


def _vehicle_rows(db) -> list:
    import models
    rows = db.query(models.Vehicle).filter(models.Vehicle.status == "In Stock").all()
    return [{"brand": v.brand, "model": v.model, "year": v.year, "color": v.color, "store": v.store, "chassis_number": v.chassis_number} for v in rows]


def summary_text(db) -> str:
    """Today's figures (Nairobi time, UTC+3) as one message."""
    import models
    from datetime import datetime, timedelta
    off = timedelta(hours=3)
    local = datetime.utcnow() + off
    start = local.replace(hour=0, minute=0, second=0, microsecond=0) - off
    end = start + timedelta(days=1)
    sales = db.query(models.Sale).filter(models.Sale.status != "Voided", models.Sale.sold_at >= start, models.Sale.sold_at < end).all()
    books = db.query(models.Booking).filter(models.Booking.booking_date >= start, models.Booking.booking_date < end).all()
    in_stock = db.query(models.Vehicle).filter(models.Vehicle.status == "In Stock").count()
    booked = db.query(models.Vehicle).filter(models.Vehicle.status == "Booked").count()
    return summary_lines(local.strftime("%d %b %Y"),
                         [{"sold_by": s.sold_by, "sale_price": s.sale_price, "commission": s.commission} for s in sales],
                         [{"booking_amount": b.booking_amount} for b in books], in_stock, booked)


def cron_key() -> str:
    import hashlib
    return hashlib.sha256(("hallel-cron:" + _token()).encode()).hexdigest()[:24]


def broadcast(text: str) -> int:
    n = 0
    for g in sorted(groups_all()):
        if _say(g, text): n += 1
    return n


# ------------------------------------------------------------------ update handling
def _vanish(chat_id, ids, delay=None):
    time.sleep(float(os.getenv("MYID_SECONDS", "8")) if delay is None else delay)
    for mid in ids:
        if mid:
            try: _call("deleteMessage", {"chat_id": chat_id, "message_id": mid})
            except Exception: pass


def _cmd_link(msg, chat_id, sid, text):
    parts = text.split()
    code = parts[1].strip() if len(parts) > 1 else ""
    r = None
    try:
        import models
        from datetime import datetime
        db = _db()
        try:
            row = db.query(models.TelegramLinkCode).filter(models.TelegramLinkCode.code == code).first() if code else None
            if not row or row.expires_at < datetime.utcnow():
                r = _say(chat_id, "That code is wrong or expired. Ask the admin for a new one (Staff accounts page).", msg.get("message_id"))
            else:
                link = db.query(models.TelegramLink).filter(models.TelegramLink.telegram_id == sid).first()
                if link: link.username = row.username
                else: db.add(models.TelegramLink(telegram_id=sid, username=row.username))
                name = row.username
                db.delete(row); db.commit()
                r = _say(chat_id, f"Linked. Your Telegram is now connected to the system user '{name}'.", msg.get("message_id"))
        finally: db.close()
    except Exception as e:
        r = _say(chat_id, f"Could not link: {e}", msg.get("message_id"))
    _vanish(chat_id, [((r or {}).get("result") or {}).get("message_id"), msg.get("message_id")])


def _cmd_connect(msg, chat_id, sid):
    r = None
    if not chat_id.startswith("-"):
        r = _say(chat_id, "Send /connect inside the group you want to connect.", msg.get("message_id"))
    else:
        user = staff_map().get(sid)
        if not user or _role_of(user) != "admin":
            r = _say(chat_id, "Only a linked administrator can connect a group.", msg.get("message_id"))
        else:
            try:
                import models
                db = _db()
                try:
                    if not db.query(models.TelegramGroup).filter(models.TelegramGroup.chat_id == chat_id).first():
                        db.add(models.TelegramGroup(chat_id=chat_id, title=(msg.get("chat", {}).get("title") or "")[:150], added_by=user))
                        db.commit()
                finally: db.close()
                r = _say(chat_id, "This group is now connected. Staff can post sales and bookings here.", msg.get("message_id"))
            except Exception as e:
                r = _say(chat_id, f"Could not connect this group: {e}", msg.get("message_id"))
    _vanish(chat_id, [((r or {}).get("result") or {}).get("message_id"), msg.get("message_id")])


def _handle_message(msg: dict, find_vehicle, execute):
    chat = msg.get("chat", {})
    chat_id = str(chat.get("id", ""))
    sender = msg.get("from", {}) or {}
    sid = str(sender.get("id", ""))
    text = (msg.get("text") or msg.get("caption") or "").strip()
    if not text: return
    cmd = text.split()[0].lower().split("@")[0]

    if cmd == "/myid":
        r = _say(chat_id, f"Your Telegram id: {sid}\nThis chat id: {chat_id}\n(this message deletes itself in a few seconds)", msg.get("message_id"))
        time.sleep(float(os.getenv("MYID_SECONDS", "8")))
        for mid in (((r or {}).get("result") or {}).get("message_id"), msg.get("message_id")):
            if mid:
                try: _call("deleteMessage", {"chat_id": chat_id, "message_id": mid})
                except Exception: pass
        return
    if cmd == "/link":
        _cmd_link(msg, chat_id, sid, text); return
    if cmd == "/connect":
        _cmd_connect(msg, chat_id, sid); return
    if chat_id not in _groups():
        print(f"[telegram-bot] ignored a message from chat {chat_id} (TELEGRAM_GROUP_ID is {_group()!r})", flush=True)
        return  # ignore every other chat
    if cmd in ("/help", "/start"):
        _say(chat_id, HELP, msg.get("message_id")); return

    user = staff_map().get(sid)
    if cmd in ("stock", "/stock", "summary", "/summary"):
        if not user:
            return  # only linked staff get answers
        try:
            db = _db()
            try:
                if cmd.endswith("summary"):
                    _say(chat_id, summary_text(db), msg.get("message_id"))
                else:
                    query = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ""
                    _say(chat_id, stock_text(_vehicle_rows(db), query), msg.get("message_id"))
            finally: db.close()
        except Exception as e:
            _say(chat_id, f"Could not read the system: {e}", msg.get("message_id"))
        return
    parsed = parse(text, find_vehicle)
    if not parsed: return
    if not user:
        _say(chat_id, f"Your Telegram id ({sid}) is not linked to a system user. Ask the admin to add it to TELEGRAM_STAFF.", msg.get("message_id")); return
    if "error" in parsed:
        _say(chat_id, parsed["error"] + "\n\nSend /help for the format.", msg.get("message_id")); return

    _say(chat_id, _describe(parsed) + f"\n\nRecorded by: {user}",
         msg.get("message_id"),
         [{"text": "Confirm", "callback_data": f"y:{sid}"}, {"text": "Cancel", "callback_data": f"n:{sid}"}])


def _handle_callback(cb: dict, find_vehicle, execute):
    """Stateless: the message the Confirm button sits on is a reply to the original post, and Telegram sends that
    original (with its caption) back to us. We simply read it again, so this works on serverless hosts like Vercel."""
    data = cb.get("data", "")
    msg = cb.get("message", {}) or {}
    chat_id, mid = msg.get("chat", {}).get("id"), msg.get("message_id")
    sid = str(cb.get("from", {}).get("id", ""))
    act, _, owner = data.partition(":")

    def answer(t):
        try: _call("answerCallbackQuery", {"callback_query_id": cb.get("id"), "text": t})
        except Exception: pass

    def edit(t):
        try: _call("editMessageText", {"chat_id": chat_id, "message_id": mid, "text": t})
        except Exception as e: _state["last_error"] = f"edit failed: {e}"

    def remove():
        try: _call("deleteMessage", {"chat_id": chat_id, "message_id": mid})
        except Exception: edit("Done.")  # no permission to delete: at least clear the buttons

    if str(chat_id) not in _groups(): return
    if sid != owner:
        answer("Only the person who sent it can do this."); return
    user = staff_map().get(sid)
    orig = msg.get("reply_to_message") or {}
    text = (orig.get("text") or orig.get("caption") or "").strip()
    if not user or not text:
        answer("Could not read the original message."); edit("Could not read the original message. Please send it again."); return
    if time.time() - int(msg.get("date", 0) or 0) > PENDING_TTL * 6:
        answer("Expired"); edit("Expired. Please send the message again."); return
    if act != "y":
        answer("Cancelled"); remove(); return
    p = parse(text, find_vehicle)
    if not p or "error" in p:
        answer("Not saved"); edit("NOT SAVED\n" + (p or {}).get("error", "Could not read the message.")); return
    ok, message = execute(p["kind"], user, p)
    _state["handled"] += 1 if ok else 0
    answer(message if ok else "Not saved")
    if ok:
        remove(); return  # success: the summary disappears; failures stay visible with the reason
    head = _describe(p).split("\n")[0]
    edit((f"SAVED - {head.title()} recorded\n" if ok else "NOT SAVED\n") + _describe(p) + f"\nBy: {user}\n\n{message}")


def handle_update(u: dict, find_vehicle, execute):
    """Process one Telegram update (used by the webhook and by the polling loop)."""
    try:
        if "callback_query" in u: _handle_callback(u["callback_query"], find_vehicle, execute)
        elif "message" in u: _handle_message(u["message"], find_vehicle, execute)
    except Exception as e:
        _state["last_error"] = f"handler: {e}"
        print(f"[telegram-bot] handler error: {e}", flush=True)


def webhook_secret() -> str:
    import hashlib
    return hashlib.sha256(("hallel-webhook:" + _token()).encode()).hexdigest()[:32]


def set_webhook(url: str) -> dict:
    return _call("setWebhook", {"url": url, "secret_token": webhook_secret(),
                                "allowed_updates": ["message", "callback_query"], "drop_pending_updates": True})


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
                handle_update(u, find_vehicle, execute)
        except urllib.error.HTTPError as e:
            print(f"[telegram-bot] Telegram HTTP {e.code}", flush=True)
            _state["last_error"] = f"Telegram HTTP {e.code}" + (" (another copy of the server is also polling this bot)" if e.code == 409 else "")
            time.sleep(10)
        except Exception as e:
            if _state["last_error"] != f"{type(e).__name__}: {e}": print(f"[telegram-bot] {type(e).__name__}: {e}", flush=True)
            _state["last_error"] = f"{type(e).__name__}: {e}"
            time.sleep(5)


def start(find_vehicle, execute) -> bool:
    """find_vehicle(VIN)->dict|None ; execute(kind, username, parsed)->(ok, message)."""
    if not _token():
        print("[telegram-bot] NOT started. TELEGRAM_BOT_TOKEN is missing in .env", flush=True)
        return False
    miss = []
    if miss:
        print("[telegram-bot] running in setup mode (only /myid works). Still missing in .env: " + ", ".join(miss), flush=True)
    if os.getenv("VERCEL"):
        print("[telegram-bot] Vercel detected: using webhook mode (open /telegram/setup once).", flush=True)
        return False
    if _state["running"]: return False
    print("[telegram-bot] started, listening to group " + _group(), flush=True)
    _state["running"] = True
    threading.Thread(target=_loop, args=(find_vehicle, execute), daemon=True, name="telegram-group-bot").start()
    return True


def stop():
    _state["running"] = False
