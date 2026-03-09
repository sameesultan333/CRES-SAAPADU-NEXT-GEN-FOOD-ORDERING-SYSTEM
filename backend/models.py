from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey, DateTime, Enum
from database import Base
from datetime import datetime
import enum


class PrepType(str, enum.Enum):
    RA = "RA"
    COOK = "COOK"


class Canteen(Base):
    __tablename__ = "canteens"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    location = Column(String)
    is_active = Column(Boolean, default=True)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    phone = Column(String, nullable=False)
    department = Column(String, nullable=False)
    year = Column(String, nullable=False)

    password = Column(String, nullable=False)
    wallet_balance = Column(Float, default=0.0)
    role = Column(String, default="student")


class MenuItem(Base):
    __tablename__ = "menu_items"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    price = Column(Float)
    stock = Column(Integer, default=0)
    is_veg = Column(Boolean, default=True)
    canteen_id = Column(Integer, ForeignKey("canteens.id"))

    # 🔥 NEW FIELDS
    prep_type = Column(Enum(PrepType), default=PrepType.RA)
    prep_time_seconds = Column(Integer, default=60)


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    canteen_id = Column(Integer, ForeignKey("canteens.id"))

    status = Column(String, default="PLACED")
    payment_mode = Column(String)
    estimated_wait_time = Column(Integer)
    estimated_ready_at = Column(Integer)
    order_type = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    menu_item_id = Column(Integer, ForeignKey("menu_items.id"), nullable=False)
    quantity = Column(Integer, nullable=False)