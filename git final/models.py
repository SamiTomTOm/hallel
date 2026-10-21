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
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class Sale(Base):
    __tablename__ = "sales"
    id = Column(Integer, primary_key=True, index=True)
    chassis_number = Column(String(80), ForeignKey("inventory.chassis_number"), nullable=False, index=True)
    customer_name = Column(String(150), nullable=False)
    contact_info = Column(String(80), nullable=False, default="")
    sale_price = Column(Float, nullable=False)
    payment_method = Column(String(20), nullable=False, default="Cash")
    bank_name = Column(String(100), nullable=False, default="")
    sold_by = Column(String(80), nullable=False)
    sold_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    notes = Column(Text, nullable=False, default="")
    vehicle = relationship("Vehicle")


class Transfer(Base):
    __tablename__ = "transfers"
    id = Column(Integer, primary_key=True, index=True)
    chassis_number = Column(String(80), ForeignKey("inventory.chassis_number"), nullable=False, index=True)
    from_store = Column(String(100), nullable=False)
    to_store = Column(String(100), nullable=False)
    transferred_by = Column(String(80), nullable=False)
    transferred_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    notes = Column(Text, nullable=False, default="")
    class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, index=True)

    chassis_number = Column(
        String(80),
        ForeignKey("inventory.chassis_number"),
        nullable=False,
        index=True
    )

    customer_name = Column(String(150), nullable=False)
    customer_phone = Column(String(80), nullable=False, default="")
    booked_by = Column(String(80), nullable=False)
    booking_amount = Column(Float, nullable=False, default=0)
    booking_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    notes = Column(Text, nullable=False, default="")

    status = Column(
        String(30),
        nullable=False,
        default="Booked",
        index=True
    )

    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow
    )

    vehicle = relationship("Vehicle")

    customer_name = Column(String(150), nullable=False)
    customer_phone = Column(String(80), nullable=False, default="")
    booked_by = Column(String(80), nullable=False)
    booking_amount = Column(Float, nullable=False, default=0)
    booking_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    notes = Column(Text, nullable=False, default="")

    status = Column(
        String(30),
        nullable=False,
        default="Booked",
        index=True
    )

    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow
    )

    vehicle = relationship("Vehicle")
