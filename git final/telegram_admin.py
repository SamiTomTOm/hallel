"""Staff-accounts page support for the Telegram bot: connected groups and staff links.
Kept in its own file so main.py stays small. main.py calls register(app, ...) once."""
import os
import secrets
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import models
import telegram_bot as tgbot


class TelegramLinkCodeIn(BaseModel):
    username: str = Field(min_length=1, max_length=80)


def register(app, get_db, require_roles, log_activity):
    admin_only = require_roles("admin")

    @app.get("/telegram/overview")
    def telegram_overview(db: Session = Depends(get_db), user: models.User = Depends(admin_only)):
        bot = ""
        try:
            bot = tgbot._call("getMe", {}).get("result", {}).get("username", "")
        except Exception:
            pass
        return {
            "bot": bot,
            "fixed_groups": sorted(tgbot._groups_fallback()),
            "groups": [{"id": g.id, "chat_id": g.chat_id, "title": g.title, "added_by": g.added_by} for g in db.query(models.TelegramGroup).order_by(models.TelegramGroup.id).all()],
            "links": [{"id": x.id, "telegram_id": x.telegram_id, "username": x.username} for x in db.query(models.TelegramLink).order_by(models.TelegramLink.id).all()],
        }

    @app.post("/telegram/link-code")
    def telegram_link_code(data: TelegramLinkCodeIn, db: Session = Depends(get_db), user: models.User = Depends(admin_only)):
        target = db.query(models.User).filter(models.User.username == data.username.strip()).first()
        if not target:
            raise HTTPException(status_code=404, detail="User not found.")
        db.query(models.TelegramLinkCode).filter(models.TelegramLinkCode.username == target.username).delete()
        code = "".join(secrets.choice("0123456789") for _ in range(6))
        db.add(models.TelegramLinkCode(code=code, username=target.username, expires_at=datetime.utcnow() + timedelta(minutes=15)))
        log_activity(db, user, "TELEGRAM_LINK_CODE", "User", target.id, "", f"Created a Telegram link code for {target.username}")
        db.commit()
        return {"code": code, "username": target.username, "expires_in_minutes": 15}

    @app.delete("/telegram/links/{link_id}")
    def telegram_unlink(link_id: int, db: Session = Depends(get_db), user: models.User = Depends(admin_only)):
        row = db.query(models.TelegramLink).filter(models.TelegramLink.id == link_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Not found.")
        log_activity(db, user, "TELEGRAM_UNLINK", "User", row.id, "", f"Removed Telegram link of {row.username}")
        db.delete(row)
        db.commit()
        return {"message": "Telegram unlinked."}

    @app.delete("/telegram/groups/{group_id}")
    def telegram_group_remove(group_id: int, db: Session = Depends(get_db), user: models.User = Depends(admin_only)):
        row = db.query(models.TelegramGroup).filter(models.TelegramGroup.id == group_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Not found.")
        log_activity(db, user, "TELEGRAM_GROUP_REMOVED", "Telegram", row.id, "", f"Disconnected group {row.title or row.chat_id}")
        db.delete(row)
        db.commit()
        return {"message": "Group disconnected."}

    @app.get("/telegram/daily-summary")
    def telegram_daily_summary(request: Request, key: str = "", db: Session = Depends(get_db)):
        """Called every evening by the Vercel cron (see vercel.json). Posts today's figures to the connected groups."""
        cron = os.getenv("CRON_SECRET", "")
        if key != tgbot.cron_key() and not (cron and request.headers.get("authorization") == f"Bearer {cron}"):
            raise HTTPException(status_code=403, detail="Forbidden")
        sent = tgbot.broadcast(tgbot.summary_text(db))
        return {"sent_to_groups": sent}
