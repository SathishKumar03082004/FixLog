from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), nullable=False, unique=True, index=True)
    email = Column(String(120), nullable=False, unique=True, index=True)
    full_name = Column(String(100), nullable=False, default="")
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="user", index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_login_at = Column(DateTime, nullable=True)

    issues = relationship("Issue", back_populates="reporter")

    @property
    def display_name(self):
        return self.full_name or self.username


class Issue(Base):
    __tablename__ = "issues"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(160), nullable=False, index=True)
    description = Column(Text, nullable=False)
    error_message = Column(Text, default="")
    category = Column(String(80), nullable=False, default="Other")
    priority = Column(String(20), nullable=False, default="Medium")
    status = Column(String(30), nullable=False, default="Open")
    root_cause = Column(Text, default="")
    solution = Column(Text, default="")
    reported_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    reporter = relationship("User", back_populates="issues")
