from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Integer, Numeric, Text, TIMESTAMP, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class FoodItem(Base):
    __tablename__ = "food_items"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    image_sha1: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    guess_label: Mapped[str] = mapped_column(Text, nullable=False)
    class_id: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class FreshCheck(Base):
    __tablename__ = "fresh_checks"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    food_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage: Mapped[str] = mapped_column(Text, nullable=False)
    ripeness: Mapped[str] = mapped_column(Text, nullable=False)
    hours_left: Mapped[int] = mapped_column(Integer, nullable=False)
    deadline: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

