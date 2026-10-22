from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, String, Text, false
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

    # Two foreign keys now point at users (reporter + assignee), so be explicit.
    issues = relationship("Issue", foreign_keys="Issue.reported_by_id", back_populates="reporter")

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
    # Kanban board fields
    assignee_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    is_blocked = Column(Boolean, nullable=False, default=False, server_default=false())
    blocked_reason = Column(String(255), nullable=False, default="", server_default="")
    # Smart bug report fields (all optional at the database level so old rows stay valid)
    steps_to_reproduce = Column(Text, default="")
    expected_result = Column(Text, default="")
    actual_result = Column(Text, default="")
    browser = Column(String(80), nullable=False, default="", server_default="")
    os_name = Column(String(80), nullable=False, default="", server_default="")
    app_version = Column(String(40), nullable=False, default="", server_default="")
    environment = Column(String(30), nullable=False, default="", server_default="")
    severity = Column(String(20), nullable=False, default="Medium", server_default="Medium")
    affected_users = Column(String(30), nullable=False, default="", server_default="")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    reporter = relationship("User", foreign_keys=[reported_by_id], back_populates="issues")
    assignee = relationship("User", foreign_keys=[assignee_id])
    attachments = relationship(
        "IssueAttachment", back_populates="issue", cascade="all, delete-orphan",
        order_by="IssueAttachment.id",
    )

    @property
    def bug_id(self):
        """Automatic, human-friendly bug ID derived from the primary key (BUG-00042)."""
        return f"BUG-{self.id:05d}" if self.id else "BUG-NEW"

    @property
    def reported_at(self):
        """Report timestamp: set automatically (UTC) when the row is first created."""
        return self.created_at


class IssueAttachment(Base):
    """Screenshot, video or log file attached to a bug report. The bytes live on disk."""
    __tablename__ = "issue_attachments"

    id = Column(Integer, primary_key=True, index=True)
    issue_id = Column(Integer, ForeignKey("issues.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String(20), nullable=False)  # screenshot | video | log
    original_name = Column(String(255), nullable=False)
    stored_name = Column(String(80), nullable=False, unique=True)
    size_bytes = Column(BigInteger, nullable=False, default=0)
    uploaded_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    issue = relationship("Issue", back_populates="attachments")
    uploaded_by = relationship("User", foreign_keys=[uploaded_by_id])
