import os
from datetime import datetime, timedelta
from typing import Optional, Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import func, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import Base, engine, get_db
import models

load_dotenv()
Base.metadata.create_all(bind=engine)

def migrate_additive_columns():
    """Add new optional/defaulted columns to existing databases without deleting data."""
    additions = {
        "inventory": {
            "has_accessories": "BOOLEAN NOT NULL DEFAULT FALSE",
            "customer_name": "VARCHAR(150) NOT NULL DEFAULT ''",
        },
        "sales": {
            "payment_method": "VARCHAR(20) NOT NULL DEFAULT 'Cash'",
            "bank_name": "VARCHAR(100) NOT NULL DEFAULT ''",
        },
    }
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table, columns in additions.items():
            if table not in inspector.get_table_names():
                continue
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))

migrate_additive_columns()

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SECRET_KEY = os.getenv("SECRET_KEY", "CHANGE_THIS_TO_A_LONG_RANDOM_SECRET_BEFORE_DEPLOYMENT")
ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "480"))
_default_origins = [
    "http://127.0.0.1:5500",
    "http://localhost:5500",
    "http://127.0.0.1:5501",
    "http://localhost:5501",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
]
_env_origins = [x.strip() for x in os.getenv("ALLOWED_ORIGINS", "").split(",") if x.strip()]
ALLOWED_ORIGINS = list(dict.fromkeys(_default_origins + _env_origins))
ROLES = {"admin", "stock_manager", "salesperson", "management"}

app = FastAPI(title="Hallel Inventory API", version="2.0.0")

# Allow missing Authorization headers so endpoints can return our own 401 message.
bearer = HTTPBearer(auto_error=False)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


# Password hashing uses PBKDF2 from the Python standard library.
import hashlib, hmac, secrets

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 310_000).hex()
    return f"pbkdf2_sha256$310000${salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def create_token(user: models.User) -> str:
    now = datetime.utcnow()
    payload = {"sub": user.username, "role": user.role, "iat": now, "exp": now + timedelta(minutes=ACCESS_TOKEN_MINUTES)}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer), db: Session = Depends(get_db)) -> models.User:
    if not credentials:
        raise HTTPException(status_code=401, detail="Please log in to continue.", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise JWTError("Missing subject")
    except JWTError:
        raise HTTPException(status_code=401, detail="Session expired or invalid. Please log in again.", headers={"WWW-Authenticate": "Bearer"})
    user = db.query(models.User).filter(models.User.username == username).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User is inactive or no longer exists.")
    return user


def require_roles(*roles):
    def checker(user: models.User = Depends(get_current_user)):
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="You do not have permission to perform this action.")
        return user
    return checker


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)

class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=10, max_length=200)
    role: Literal["admin", "stock_manager", "salesperson", "management"] = "salesperson"

class VehicleCreate(BaseModel):
    chassis_number: str = Field(min_length=3, max_length=80)
    brand: str = Field(default="", max_length=80)
    model: str = Field(min_length=1, max_length=120)
    year: int = Field(ge=1980, le=2100)
    vehicle_type: Literal["Fuel", "Hybrid", "Electric"] = "Electric"
    color: str = Field(default="", max_length=60)
    body: str = Field(default="", max_length=60)
    store: str = Field(min_length=1, max_length=100)
    has_charger: bool = False
    has_accessories: bool = False
    has_key: bool = False
    has_card: bool = False
    received_date: Optional[datetime] = None
    received_by: str = Field(default="", max_length=80)
    customer_name: str = Field(default="", max_length=150)
    notes: str = Field(default="", max_length=2000)

class SaleCreate(BaseModel):
    chassis_number: str = Field(min_length=3, max_length=80)
    customer_name: str = Field(min_length=1, max_length=150)
    contact_info: str = Field(default="", max_length=80)
    sale_price: float = Field(gt=0)
    payment_method: Literal["Cash", "Bank"] = "Cash"
    bank_name: str = Field(default="", max_length=100)
    sold_by: str = Field(min_length=1, max_length=80)
    notes: str = Field(default="", max_length=2000)


class BookingCreate(BaseModel):
    chassis_number: str = Field(min_length=3, max_length=80)
    customer_name: str = Field(min_length=1, max_length=150)
    customer_phone: str = Field(default="", max_length=80)
    booked_by: str = Field(min_length=1, max_length=80)
    booking_amount: float = Field(default=0, ge=0)
    notes: str = Field(default="", max_length=2000)


class TransferCreate(BaseModel):
    chassis_number: str = Field(min_length=3, max_length=80)
    from_store: str = Field(min_length=1, max_length=100)
    to_store: str = Field(min_length=1, max_length=100)
    transferred_by: str = Field(min_length=1, max_length=80)
    notes: str = Field(default="", max_length=2000)

class VehicleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brand: Optional[str] = Field(default=None, max_length=80)
    model: Optional[str] = Field(default=None, max_length=120)
    year: Optional[int] = Field(default=None, ge=1980, le=2100)
    vehicle_type: Optional[Literal["Fuel", "Hybrid", "Electric"]] = None
    color: Optional[str] = Field(default=None, max_length=60)
    body: Optional[str] = Field(default=None, max_length=60)
    store: Optional[str] = Field(default=None, max_length=100)
    has_charger: Optional[bool] = None
    has_accessories: Optional[bool] = None
    has_key: Optional[bool] = None
    has_card: Optional[bool] = None
    received_by: Optional[str] = Field(default=None, max_length=80)
    notes: Optional[str] = Field(default=None, max_length=2000)


def vehicle_dict(v: models.Vehicle):
    return {"id": v.id, "chassis_number": v.chassis_number, "vin": v.chassis_number, "brand": v.brand, "model": v.model, "year": v.year, "vehicle_type": v.vehicle_type, "type": v.vehicle_type, "color": v.color, "body": v.body, "store": v.store, "has_charger": v.has_charger, "has_accessories": v.has_accessories, "customer_name": v.customer_name, "has_key": v.has_key, "has_card": v.has_card, "received_date": v.received_date.isoformat() if v.received_date else None, "received_by": v.received_by, "status": v.status, "notes": v.notes, "created_at": v.created_at.isoformat() if v.created_at else None}


@app.on_event("startup")
def create_initial_admin():
    """Create the first admin only when explicit environment credentials are configured."""
    username, password = os.getenv("ADMIN_USERNAME"), os.getenv("ADMIN_PASSWORD")
    if not username or not password:
        return
    db = next(get_db())
    try:
        if not db.query(models.User).filter(models.User.username == username).first():
            db.add(models.User(username=username, password_hash=hash_password(password), role="admin"))
            db.commit()
    finally:
        db.close()


@app.get("/health")
def health():
    return {"status": "ok", "service": "Hallel Inventory API", "version": "2.0.0"}

@app.post("/auth/login")
def login(data: LoginInput, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == data.username).first()
    if not user or not user.is_active or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect username or password.")
    return {"access_token": create_token(user), "token_type": "bearer", "username": user.username, "role": user.role, "expires_in_minutes": ACCESS_TOKEN_MINUTES}

@app.get("/auth/me")
def who_am_i(user: models.User = Depends(get_current_user)):
    return {"username": user.username, "role": user.role, "is_active": user.is_active}

@app.post("/auth/users", status_code=201)
def create_user(data: UserCreate, db: Session = Depends(get_db), user: models.User = Depends(require_roles("admin"))):
    if db.query(models.User).filter(models.User.username == data.username).first():
        raise HTTPException(status_code=409, detail="That username already exists.")
    record = models.User(username=data.username, password_hash=hash_password(data.password), role=data.role)
    db.add(record)
    db.commit()
    return {"id": record.id, "username": record.username, "role": record.role, "is_active": record.is_active}

@app.get("/auth/users")
def list_users(db: Session = Depends(get_db), user: models.User = Depends(require_roles("admin", "management"))):
    return [{"id": u.id, "username": u.username, "role": u.role, "is_active": u.is_active, "created_at": u.created_at.isoformat()} for u in db.query(models.User).order_by(models.User.username).all()]

@app.patch("/auth/users/{user_id}/active")
def set_user_active(user_id: int, active: bool, db: Session = Depends(get_db), user: models.User = Depends(require_roles("admin"))):
    target = db.query(models.User).filter(models.User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    if target.id == user.id and not active:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account.")
    target.is_active = active
    db.commit()
    return {"id": target.id, "username": target.username, "is_active": target.is_active}

@app.get("/inventory")
def get_inventory(status_filter: Optional[str] = None, search: Optional[str] = None, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    query = db.query(models.Vehicle)
    if status_filter and status_filter.lower() != "all":
        query = query.filter(func.lower(models.Vehicle.status) == status_filter.lower())
    if search:
        pattern = f"%{search.strip()}%"
        query = query.filter((models.Vehicle.chassis_number.ilike(pattern)) | (models.Vehicle.model.ilike(pattern)) | (models.Vehicle.brand.ilike(pattern)))
    return [vehicle_dict(v) for v in query.order_by(models.Vehicle.created_at.desc()).all()]

@app.get("/inventory/{chassis_number}")
def get_vehicle(chassis_number: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    v = db.query(models.Vehicle).filter(models.Vehicle.chassis_number == chassis_number).first()
    if not v:
        raise HTTPException(status_code=404, detail="Vehicle not found.")
    return vehicle_dict(v)

@app.post("/inventory", status_code=201)
def add_inventory(data: VehicleCreate, db: Session = Depends(get_db), user: models.User = Depends(require_roles("admin", "stock_manager"))):
    vin = data.chassis_number.strip().upper()
    if db.query(models.Vehicle).filter(models.Vehicle.chassis_number == vin).first():
        raise HTTPException(status_code=409, detail="A vehicle with this VIN/chassis number already exists.")
    payload = data.model_dump()
    payload["chassis_number"] = vin
    payload["received_date"] = data.received_date or datetime.utcnow()
    v = models.Vehicle(**payload, status="In Stock")
    db.add(v)
    try:
        db.commit()
        db.refresh(v)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A vehicle with this VIN/chassis number already exists.")
    return vehicle_dict(v)

@app.patch("/inventory/{chassis_number}")
def update_inventory(chassis_number: str, data: VehicleUpdate, db: Session = Depends(get_db), user: models.User = Depends(require_roles("admin", "stock_manager"))):
    v = db.query(models.Vehicle).filter(models.Vehicle.chassis_number == chassis_number).first()
    if not v:
        raise HTTPException(status_code=404, detail="Vehicle not found.")
    changes = data.model_dump(exclude_unset=True)
    for key, value in changes.items():
        if value is not None:
            setattr(v, key, value)
    db.commit()
    db.refresh(v)
    return vehicle_dict(v)

@app.post("/sales", status_code=201)
def create_sale(data: SaleCreate, db: Session = Depends(get_db), user: models.User = Depends(require_roles("admin", "salesperson"))):
    v = db.query(models.Vehicle).filter(models.Vehicle.chassis_number == data.chassis_number.strip().upper()).with_for_update().first()
    if not v:
        raise HTTPException(status_code=404, detail="Vehicle not found. Check the VIN/chassis number.")
    if v.status != "In Stock":
        raise HTTPException(status_code=409, detail=f"This vehicle cannot be sold because its status is '{v.status}'.")
    if data.payment_method == "Bank" and not data.bank_name.strip():
        raise HTTPException(status_code=422, detail="Bank name is required when payment method is Bank.")
    sale = models.Sale(chassis_number=v.chassis_number, customer_name=data.customer_name.strip(), contact_info=data.contact_info.strip(), sale_price=data.sale_price, sold_by=data.sold_by.strip() or user.username, payment_method=data.payment_method, bank_name=data.bank_name.strip() if data.payment_method == "Bank" else "", notes=data.notes)
    v.status = "Sold"
    db.add(sale)
    db.commit()
    db.refresh(sale)
    return {"message": "Sale recorded successfully.", "sale_id": sale.id, "chassis_number": sale.chassis_number, "sold_at": sale.sold_at.isoformat()}

@app.get("/sales")
def list_sales(limit: int = 200, db: Session = Depends(get_db), user: models.User = Depends(require_roles("admin", "management", "stock_manager", "salesperson"))):
    limit = max(1, min(limit, 1000))
    query = db.query(models.Sale)
    if user.role == "salesperson":
        query = query.filter(models.Sale.sold_by == user.username)
    rows = query.order_by(models.Sale.sold_at.desc()).limit(limit).all()
    return [{"id": s.id, "chassis_number": s.chassis_number, "customer_name": s.customer_name, "contact_info": s.contact_info, "sale_price": s.sale_price, "sold_by": s.sold_by, "payment_method": s.payment_method, "bank_name": s.bank_name, "sold_at": s.sold_at.isoformat(), "notes": s.notes} for s in rows]

@app.post("/transfers", status_code=201)
def create_transfer(data: TransferCreate, db: Session = Depends(get_db), user: models.User = Depends(require_roles("admin", "stock_manager"))):
    if data.from_store.strip().casefold() == data.to_store.strip().casefold():
        raise HTTPException(status_code=400, detail="The source and destination branches must be different.")
    v = db.query(models.Vehicle).filter(models.Vehicle.chassis_number == data.chassis_number.strip().upper()).with_for_update().first()
    if not v:
        raise HTTPException(status_code=404, detail="Vehicle not found.")
    if v.status != "In Stock":
        raise HTTPException(status_code=409, detail="Only vehicles currently in stock can be transferred.")
    if v.store.casefold() != data.from_store.strip().casefold():
        raise HTTPException(status_code=409, detail=f"This vehicle is currently listed at '{v.store}', not '{data.from_store}'.")
    transfer = models.Transfer(chassis_number=v.chassis_number, from_store=v.store, to_store=data.to_store.strip(), transferred_by=data.transferred_by.strip() or user.username, notes=data.notes)
    v.store = data.to_store.strip()
    db.add(transfer)
    db.commit()
    db.refresh(transfer)
    return {"message": "Branch transfer recorded successfully.", "transfer_id": transfer.id, "chassis_number": transfer.chassis_number, "current_store": v.store, "transferred_at": transfer.transferred_at.isoformat()}

@app.get("/transfers")
def list_transfers(limit: int = 200, db: Session = Depends(get_db), user: models.User = Depends(require_roles("admin", "management", "stock_manager"))):
    limit = max(1, min(limit, 1000))
    rows = db.query(models.Transfer).order_by(models.Transfer.transferred_at.desc()).limit(limit).all()
    return [{"id": t.id, "chassis_number": t.chassis_number, "from_store": t.from_store, "to_store": t.to_store, "transferred_by": t.transferred_by, "transferred_at": t.transferred_at.isoformat(), "notes": t.notes} for t in rows]

@app.get("/reports/summary")
def reports_summary(db: Session = Depends(get_db), user: models.User = Depends(require_roles("admin", "management"))):
    total = db.query(models.Vehicle).count()
    in_stock = db.query(models.Vehicle).filter(models.Vehicle.status == "In Stock").count()
    sold = db.query(models.Vehicle).filter(models.Vehicle.status == "Sold").count()
    by_seller = db.query(models.Sale.sold_by, func.count(models.Sale.id).label("units_sold"), func.coalesce(func.sum(models.Sale.sale_price), 0).label("revenue")).group_by(models.Sale.sold_by).order_by(func.count(models.Sale.id).desc()).all()
    by_store = db.query(models.Vehicle.store, func.count(models.Vehicle.id).label("vehicles")).filter(models.Vehicle.status == "In Stock").group_by(models.Vehicle.store).all()
    recent_sales = db.query(models.Sale).order_by(models.Sale.sold_at.desc()).limit(10).all()
    return {"total_inventory": total, "in_stock": in_stock, "sold": sold, "by_seller": [{"sold_by": row[0], "units_sold": row[1], "revenue": float(row[2] or 0)} for row in by_seller], "by_store": [{"store": row[0], "vehicles": row[1]} for row in by_store], "recent_sales": [{"chassis_number": s.chassis_number, "customer_name": s.customer_name, "sale_price": s.sale_price, "sold_by": s.sold_by, "payment_method": s.payment_method, "bank_name": s.bank_name, "sold_at": s.sold_at.isoformat()} for s in recent_sales]}

@app.get("/reports/stock")
def stock_report(db: Session = Depends(get_db), user: models.User = Depends(require_roles("admin", "management", "stock_manager"))):
    rows = db.query(models.Vehicle.store, models.Vehicle.status, func.count(models.Vehicle.id)).group_by(models.Vehicle.store, models.Vehicle.status).all()
    return [{"store": row[0], "status": row[1], "count": row[2]} for row in rows]

@app.get("/")
def home():
    path = os.path.join(APP_DIR, "index.html")
    if os.path.exists(path):
        return FileResponse(path)
    return {"message": "Hallel Inventory API is running. Open /docs for API documentation."}
