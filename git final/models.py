from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(80), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(30), nullable=False, default="salesperson")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class Vehicle(Base):
    __tablename__ = "inventory"

    id = Column(Integer, primary_key=True, index=True)
    chassis_number = Column(String(80), unique=True, nullable=False, index=True)
    brand = Column(String(80), nullable=False, default="")
    model = Column(String(120), nullable=False)
    year = Column(Integer, nullable=False)
    vehicle_type = Column(String(30), nullable=False, default="Electric")
    color = Column(String(60), nullable=False, default="")
    body = Column(String(60), nullable=False, default="")
    store = Column(String(100), nullable=False, index=True)
    has_charger = Column(Boolean, nullable=False, default=False)
    has_accessories = Column(Boolean, nullable=False, default=False)
    has_key = Column(Boolean, nullable=False, default=False)
    has_card = Column(Boolean, nullable=False, default=False)
    received_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    received_by = Column(String(80), nullable=False, default="")
    customer_name = Column(String(150), nullable=False, default="")
    status = Column(String(20), nullable=False, default="In Stock", index=True)
    notes = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class Sale(Base):
    __tablename__ = "sales"

    id = Column(Integer, primary_key=True, index=True)
    chassis_number = Column(
        String(80),
        ForeignKey("inventory.chassis_number"),
        nullable=False,
        index=True,
    )
    customer_name = Column(String(150), nullable=False)
    contact_info = Column(String(80), nullable=False, default="")
    sale_price = Column(Float, nullable=False)
    payment_method = Column(String(20), nullable=False, default="Cash")
    bank_name = Column(String(100), nullable=False, default="")
    sold_by = Column(String(80), nullable=False)
    sold_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        index=True,
    )
    notes = Column(Text, nullable=False, default="")
    commission = Column(String(20), nullable=False, default="No commission")
    status = Column(String(20), nullable=False, default="Active", index=True)
    voided_at = Column(DateTime, nullable=True)
    voided_by = Column(String(80), nullable=False, default="")
    void_reason = Column(Text, nullable=False, default="")

    vehicle = relationship("Vehicle")


class Transfer(Base):
    __tablename__ = "transfers"

    id = Column(Integer, primary_key=True, index=True)
    chassis_number = Column(
        String(80),
        ForeignKey("inventory.chassis_number"),
        nullable=False,
        index=True,
    )
    from_store = Column(String(100), nullable=False)
    to_store = Column(String(100), nullable=False)
    transferred_by = Column(String(80), nullable=False)
    transferred_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        index=True,
    )
    notes = Column(Text, nullable=False, default="")


class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, index=True)
    chassis_number = Column(
        String(80),
        ForeignKey("inventory.chassis_number"),
        nullable=False,
        index=True,
    )
    customer_name = Column(String(150), nullable=False)
    customer_phone = Column(String(80), nullable=False, default="")
    booked_by = Column(String(80), nullable=False)
    booking_amount = Column(Float, nullable=False, default=0)
    booking_date = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )
    notes = Column(Text, nullable=False, default="")
    status = Column(String(30), nullable=False, default="Booked", index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    vehicle = relationship("Vehicle")


class WaitlistRequest(Base):
    """A customer who is waiting for a vehicle that is not in stock yet."""

    __tablename__ = "waitlist"

    id = Column(Integer, primary_key=True, index=True)
    customer_name = Column(String(150), nullable=False)
    customer_phone = Column(String(80), nullable=False)
    brand = Column(String(80), nullable=False, default="")
    model = Column(String(120), nullable=False, default="")
    year_from = Column(Integer, nullable=True)
    year_to = Column(Integer, nullable=True)
    vehicle_type = Column(String(30), nullable=False, default="")
    color = Column(String(60), nullable=False, default="")
    notes = Column(Text, nullable=False, default="")
    status = Column(String(20), nullable=False, default="Waiting", index=True)  # Waiting / Fulfilled / Cancelled
    created_by = Column(String(80), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_notified_at = Column(DateTime, nullable=True)
    notify_count = Column(Integer, nullable=False, default=0)


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id = Column(Integer, primary_key=True, index=True)
    actor_username = Column(String(80), nullable=False, index=True)
    actor_role = Column(String(30), nullable=False, default="")
    action = Column(String(60), nullable=False, index=True)
    entity_type = Column(String(40), nullable=False, default="")
    entity_id = Column(String(80), nullable=False, default="")
    chassis_number = Column(String(80), nullable=False, default="", index=True)
    description = Column(Text, nullable=False, default="")
    old_values = Column(Text, nullable=False, default="{}")
    new_values = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class TelegramGroup(Base):
    """A Telegram group the bot listens to (added from the Staff accounts page or with /connect)."""

    __tablename__ = "telegram_groups"

    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(String(40), unique=True, nullable=False, index=True)
    title = Column(String(150), nullable=False, default="")
    added_by = Column(String(80), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class TelegramLink(Base):
    """Links one Telegram account to one system user."""

    __tablename__ = "telegram_links"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String(40), unique=True, nullable=False, index=True)
    username = Column(String(80), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class TelegramLinkCode(Base):
    """A short-lived code an admin generates; the staff member sends /link CODE to the bot."""

    __tablename__ = "telegram_link_codes"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(12), unique=True, nullable=False, index=True)
    username = Column(String(80), nullable=False)
    expires_at = Column(DateTime, nullable=False)
