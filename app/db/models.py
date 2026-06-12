"""SQLAlchemy ORM models for CalAI."""

import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str] = mapped_column(String(255), default="")
    timezone: Mapped[str] = mapped_column(String(50), default="UTC")

    # Profile
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(10), nullable=True)  # male/female

    # Context
    last_meal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # for "и ещё" chaining

    # Goals
    goal: Mapped[str | None] = mapped_column(String(20), nullable=True)  # cut/maintain/bulk
    target_kcal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_protein_g: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_fat_g: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_carbs_g: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    meals: Mapped[list["Meal"]] = relationship(back_populates="user", lazy="selectin")
    raw_messages: Mapped[list["RawMessage"]] = relationship(back_populates="user", lazy="selectin")


class Meal(Base):
    __tablename__ = "meals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    meal_type: Mapped[str] = mapped_column(String(50), default="unknown")
    source_type: Mapped[str] = mapped_column(String(50), default="text")
    original_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    calories_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    calories_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    protein_min_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    protein_max_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fat_min_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fat_max_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbs_min_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbs_max_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[str] = mapped_column(String(20), default="medium")
    status: Mapped[str] = mapped_column(String(20), default="confirmed")
    eaten_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="meals")
    items: Mapped[list["MealItem"]] = relationship(back_populates="meal", lazy="selectin", cascade="all, delete-orphan")


class MealItem(Base):
    __tablename__ = "meal_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meal_id: Mapped[int] = mapped_column(Integer, ForeignKey("meals.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    portion_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    calories_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    calories_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    protein_min_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    protein_max_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fat_min_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fat_max_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbs_min_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbs_max_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[str] = mapped_column(String(20), default="medium")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    meal: Mapped["Meal"] = relationship(back_populates="items")


class RawMessage(Base):
    __tablename__ = "raw_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    telegram_message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    message_type: Mapped[str] = mapped_column(String(50), default="text")
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="raw_messages")
