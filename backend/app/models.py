import uuid
from datetime import datetime

from sqlalchemy import Boolean, CHAR, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    memberships: Mapped[list["Membership"]] = relationship(back_populates="user")


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    shopify_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    shopify_token_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    whatsapp_number_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    whatsapp_token_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    connection_status: Mapped[str] = mapped_column(PGEnum("disconnected", "active", "error", name="connection_status_t", create_type=False), nullable=False, server_default="disconnected")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    memberships: Mapped[list["Membership"]] = relationship(back_populates="store")


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        Index("ux_membership_active", "store_id", "user_id", unique=True, postgresql_where=text("active")),
        Index("ix_membership_user", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    role: Mapped[str] = mapped_column(PGEnum("owner", "manager", "agent", name="member_role_t", create_type=False), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    store: Mapped[Store] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    jti: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ShopifyOAuthState(Base):
    __tablename__ = "shopify_oauth_states"

    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    shop_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ShopifyCredential(Base):
    __tablename__ = "shopify_credentials"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, unique=True)
    encrypted_token_bundle: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[str] = mapped_column(Text, nullable=False)
    webhooks_registered: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    webhook_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("id", "store_id", name="customers_id_store_id_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False)
    shopify_customer_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    messaging_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("store_id", "shopify_order_id", name="orders_store_id_shopify_order_id_key"),
        UniqueConstraint("id", "store_id", name="orders_id_store_id_key"),
        ForeignKeyConstraint(["customer_id", "store_id"], ["customers.id", "customers.store_id"], ondelete="RESTRICT"),
        Index("ix_orders_store_date", "store_id", "ordered_at"),
        Index("ix_orders_store_outcome", "store_id", "outcome_status"),
        Index("ix_orders_customer", "customer_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    shopify_order_id: Mapped[str] = mapped_column(String(100), nullable=False)
    ordered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_amount: Mapped[object] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, server_default="PKR")
    payment_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    items: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    location: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    outcome_status: Mapped[str] = mapped_column(String(50), nullable=False)
    delivery_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class SourceEvent(Base):
    __tablename__ = "source_events"
    __table_args__ = (
        UniqueConstraint("store_id", "source", "provider_event_id", name="source_events_store_id_source_provider_event_id_key"),
        ForeignKeyConstraint(["order_id", "store_id"], ["orders.id", "orders.store_id"], ondelete="RESTRICT"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False)
    order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    provider_event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(PGEnum("shopify", "whatsapp", name="event_source_t", create_type=False), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processing_status: Mapped[str] = mapped_column(PGEnum("pending", "processed", "failed", name="processing_status_t", create_type=False), nullable=False, server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
