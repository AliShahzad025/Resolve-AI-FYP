"""Small server-side Shopify Admin GraphQL/OAuth client."""
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Customer, Order, ShopifyCredential, ShopifyOAuthState, SourceEvent, Store

logger = logging.getLogger(__name__)

SHOP_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$")
SCOPES = "read_orders,read_customers"
ORDER_QUERY = """query Orders($first:Int!, $after:String) {
  orders(first:$first, after:$after, sortKey:CREATED_AT, reverse:true) {
    edges { cursor node {
      id legacyResourceId name createdAt updatedAt displayFinancialStatus fulfillmentStatus: displayFulfillmentStatus currencyCode
      totalPriceSet { shopMoney { amount currencyCode } }
      customer { legacyResourceId displayName email phone }
      shippingAddress { city provinceCode countryCodeV2 }
      paymentGatewayNames
      lineItems(first:50) { nodes { title quantity sku originalUnitPriceSet { shopMoney { amount currencyCode } } } }
    } }
    pageInfo { hasNextPage endCursor }
  }
}"""
WEBHOOK_LIST_QUERY = """query ExistingWebhooks { webhookSubscriptions(first:100) { nodes { topic uri } } }"""
WEBHOOK_CREATE_MUTATION = """mutation AddWebhook($topic:WebhookSubscriptionTopic!, $webhookSubscription:WebhookSubscriptionInput!) {
  webhookSubscriptionCreate(topic:$topic, webhookSubscription:$webhookSubscription) {
    webhookSubscription { id topic uri } userErrors { field message }
  }
}"""


def validate_shop_domain(domain: str) -> str:
    domain = domain.strip().lower().rstrip("/")
    if not SHOP_DOMAIN_RE.fullmatch(domain):
        raise HTTPException(status_code=422, detail="Enter a valid shop-name.myshopify.com domain")
    return domain


def _fernet() -> Fernet:
    if not settings.shopify_encryption_key:
        raise HTTPException(status_code=503, detail="Shopify token encryption is not configured on the server")
    try:
        return Fernet(settings.shopify_encryption_key.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        raise HTTPException(status_code=503, detail="Shopify token encryption key is invalid")


def ensure_encryption_ready() -> None:
    """Fail before redirecting to OAuth if credentials could not be stored safely."""
    _fernet()


def encrypt_bundle(bundle: dict) -> str:
    return _fernet().encrypt(json.dumps(bundle, separators=(",", ":")).encode()).decode("ascii")


def decrypt_bundle(value: str) -> dict:
    try:
        return json.loads(_fernet().decrypt(value.encode("ascii")))
    except (InvalidToken, ValueError, UnicodeEncodeError, json.JSONDecodeError):
        raise HTTPException(status_code=503, detail="Shopify credentials cannot be decrypted; reconnect the store")


def _request(url: str, *, method: str = "GET", headers: dict | None = None, data: bytes | None = None) -> dict:
    request = Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        # Never surface provider response bodies, which can contain credentials or customer data.
        raise HTTPException(status_code=502, detail=f"Shopify returned HTTP {exc.code}")
    except (URLError, TimeoutError, json.JSONDecodeError):
        raise HTTPException(status_code=502, detail="Could not complete the Shopify request")


def exchange_code(shop: str, code: str) -> dict:
    if not settings.shopify_api_key or not settings.shopify_api_secret:
        raise HTTPException(status_code=503, detail="Shopify app credentials are not configured on the server")
    data = urlencode({"client_id": settings.shopify_api_key, "client_secret": settings.shopify_api_secret, "code": code, "expiring": "1"}).encode()
    token = _request(f"https://{shop}/admin/oauth/access_token", method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"}, data=data)
    if not token.get("access_token"):
        raise HTTPException(status_code=502, detail="Shopify did not return an access token")
    return token


def verify_oauth_hmac(params: list[tuple[str, str]]) -> bool:
    received = next((value for key, value in params if key == "hmac"), None)
    if not received or not settings.shopify_api_secret:
        return False
    message = "&".join(f"{key}={value}" for key, value in sorted(params) if key not in {"hmac", "signature"})
    digest = hmac.new(settings.shopify_api_secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, received)


def verify_webhook_hmac(raw_body: bytes, received: str | None) -> bool:
    if not received or not settings.shopify_api_secret:
        return False
    digest = base64.b64encode(hmac.new(settings.shopify_api_secret.encode(), raw_body, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(digest, received)


def _refresh_if_needed(db: Session, credential: ShopifyCredential) -> dict:
    bundle = decrypt_bundle(credential.encrypted_token_bundle)
    if not bundle.get("refresh_token") or int(bundle.get("expires_at", 0)) > int(time.time()) + 300:
        return bundle
    if not settings.shopify_api_key or not settings.shopify_api_secret:
        raise HTTPException(status_code=503, detail="Shopify app credentials are not configured on the server")
    form = urlencode({"client_id": settings.shopify_api_key, "client_secret": settings.shopify_api_secret, "grant_type": "refresh_token", "refresh_token": bundle["refresh_token"]}).encode()
    refreshed = _request(f"https://{credential.store.shopify_domain}/admin/oauth/access_token", method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"}, data=form)
    now = int(time.time())
    bundle = {**refreshed, "expires_at": now + int(refreshed.get("expires_in", 3600)), "refresh_token_expires_at": now + int(refreshed.get("refresh_token_expires_in", 7776000))}
    credential.encrypted_token_bundle = encrypt_bundle(bundle)
    db.commit()
    return bundle


def graphql(db: Session, store: Store, query: str, variables: dict | None = None) -> dict:
    credential = db.scalar(select(ShopifyCredential).where(ShopifyCredential.store_id == store.id).with_for_update())
    if not credential:
        raise HTTPException(status_code=409, detail="Shopify is not connected for this store")
    bundle = _refresh_if_needed(db, credential)
    api_url = f"https://{store.shopify_domain}/admin/api/{settings.shopify_api_version}/graphql.json"
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    result = _request(api_url, method="POST", headers={"Content-Type": "application/json", "X-Shopify-Access-Token": bundle["access_token"]}, data=body)
    if result.get("errors"):
        # Record only machine-readable identifiers; provider messages and response data
        # can contain customer information or other sensitive values.
        errors = result["errors"]
        codes = sorted({
            str(error.get("extensions", {}).get("code", "UNKNOWN"))
            for error in errors if isinstance(error, dict)
        })
        paths = sorted({
            ".".join(str(part) for part in error.get("path", []))
            for error in errors if isinstance(error, dict) and isinstance(error.get("path"), list)
        })
        logger.error("Shopify GraphQL request rejected: codes=%s paths=%s", codes, paths)
        raise HTTPException(status_code=502, detail="Shopify rejected the Admin API request; check app scopes and protected customer data access")
    return result.get("data", {})


def register_webhooks(db: Session, store: Store, credential: ShopifyCredential) -> tuple[bool, str | None]:
    base = settings.shopify_webhook_base_url.strip().rstrip("/")
    if not base.startswith("https://"):
        return False, "Set SHOPIFY_WEBHOOK_BASE_URL to the public HTTPS backend URL"
    try:
        existing = graphql(db, store, WEBHOOK_LIST_QUERY).get("webhookSubscriptions", {}).get("nodes", [])
        topics = ["ORDERS_CREATE", "ORDERS_UPDATED", "ORDERS_CANCELLED", "ORDERS_FULFILLED", "ORDERS_DELETE", "REFUNDS_CREATE", "APP_UNINSTALLED"]
        for topic in topics:
            if any(row.get("topic") == topic and row.get("uri") == f"{base}/api/webhooks/shopify" for row in existing):
                continue
            data = graphql(db, store, WEBHOOK_CREATE_MUTATION, {"topic": topic, "webhookSubscription": {"uri": f"{base}/api/webhooks/shopify"}})
            errors = data.get("webhookSubscriptionCreate", {}).get("userErrors", [])
            if errors:
                return False, f"Could not register {topic} webhook; check the Shopify app scopes and webhook configuration"
        return True, None
    except HTTPException as exc:
        return False, str(exc.detail)


def _shopify_id(value: object) -> str | None:
    if value is None:
        return None
    text_value = str(value)
    return text_value.rsplit("/", 1)[-1]


def _money(order: dict) -> tuple[Decimal, str]:
    money = (order.get("totalPriceSet") or {}).get("shopMoney") or {}
    raw_amount = money.get("amount", order.get("total_price", "0"))
    raw_currency = money.get("currencyCode", order.get("currency", "PKR"))
    try:
        return Decimal(str(raw_amount)).quantize(Decimal("0.01")), str(raw_currency or "PKR")[:3].upper()
    except (InvalidOperation, ValueError):
        return Decimal("0.00"), str(raw_currency or "PKR")[:3].upper()


def _outcome(order: dict) -> str:
    financial = str(order.get("displayFinancialStatus", order.get("financial_status", ""))).upper()
    fulfillment = str(order.get("fulfillmentStatus", order.get("fulfillment_status", ""))).upper()
    if order.get("cancelled_at") or financial == "VOIDED":
        return "cancelled"
    if financial in {"REFUNDED", "PARTIALLY_REFUNDED"}:
        return "refunded" if financial == "REFUNDED" else "partially_refunded"
    if fulfillment == "FULFILLED":
        return "completed"
    return "processing"


def normalize_order(db: Session, store: Store, order_data: dict) -> Order | None:
    remote_id = _shopify_id(order_data.get("legacyResourceId") or order_data.get("id"))
    if not remote_id:
        return None
    customer_data = order_data.get("customer") or {}
    customer_remote_id = _shopify_id(customer_data.get("legacyResourceId") or customer_data.get("id"))
    customer = None
    if customer_remote_id:
        customer = db.scalar(select(Customer).where(Customer.store_id == store.id, Customer.shopify_customer_id == customer_remote_id))
        if not customer:
            customer = Customer(store_id=store.id, shopify_customer_id=customer_remote_id)
            db.add(customer)
        customer.name = customer_data.get("displayName") or " ".join(filter(None, [customer_data.get("firstName") or customer_data.get("first_name"), customer_data.get("lastName") or customer_data.get("last_name")])) or None
        customer.email = customer_data.get("email")
        customer.phone = customer_data.get("phone")
        db.flush()
    amount, currency = _money(order_data)
    line_items = order_data.get("lineItems") or {}
    nodes = line_items.get("nodes") or order_data.get("line_items") or []
    items = []
    for item in nodes:
        unit = ((item.get("originalUnitPriceSet") or {}).get("shopMoney") or {}).get("amount", item.get("price"))
        items.append({"title": item.get("title") or "Item", "quantity": int(item.get("quantity") or 1), "sku": item.get("sku"), "unit_price": str(unit) if unit is not None else None})
    shipping = order_data.get("shippingAddress") or order_data.get("shipping_address") or {}
    location_values = {
        "city": shipping.get("city"),
        "region": shipping.get("provinceCode") or shipping.get("province_code") or shipping.get("province"),
        "country": shipping.get("countryCodeV2") or shipping.get("country_code"),
    }
    location = {key: value for key, value in location_values.items() if value} or None
    created = order_data.get("createdAt") or order_data.get("created_at")
    try:
        ordered_at = datetime.fromisoformat(str(created).replace("Z", "+00:00")) if created else datetime.now(timezone.utc)
    except ValueError:
        ordered_at = datetime.now(timezone.utc)
    order = db.scalar(select(Order).where(Order.store_id == store.id, Order.shopify_order_id == remote_id))
    if not order:
        order = Order(store_id=store.id, shopify_order_id=remote_id, ordered_at=ordered_at, total_amount=amount, currency=currency, outcome_status="processing", items=[], customer_id=None)
        db.add(order)
    order.customer_id = customer.id if customer else None
    order.ordered_at = ordered_at
    order.total_amount = amount
    order.currency = currency
    gateways = order_data.get("paymentGatewayNames") or order_data.get("payment_gateway_names") or []
    order.payment_method = ", ".join(str(x) for x in gateways)[:50] or None
    order.items = items
    order.location = location
    order.outcome_status = _outcome(order_data)
    order.delivery_metadata = {"fulfillment_status": order_data.get("fulfillmentStatus", order_data.get("fulfillment_status"))} if order_data.get("fulfillmentStatus", order_data.get("fulfillment_status")) else None
    db.flush()
    return order


def sanitized_event(topic: str, payload: dict) -> dict:
    allowed = {"id", "order_id", "created_at", "updated_at", "cancelled_at", "processed_at", "financial_status", "fulfillment_status", "currency", "total_price", "name", "payment_gateway_names", "customer", "shipping_address", "line_items"}
    cleaned = {key: payload[key] for key in allowed if key in payload}
    if "customer" in cleaned and isinstance(cleaned["customer"], dict):
        cleaned["customer"] = {key: cleaned["customer"].get(key) for key in ("id", "first_name", "last_name", "email", "phone") if cleaned["customer"].get(key) is not None}
    if "shipping_address" in cleaned and isinstance(cleaned["shipping_address"], dict):
        cleaned["shipping_address"] = {key: cleaned["shipping_address"].get(key) for key in ("city", "province_code", "country_code") if cleaned["shipping_address"].get(key)}
    if "line_items" in cleaned and isinstance(cleaned["line_items"], list):
        cleaned["line_items"] = [{key: item.get(key) for key in ("title", "quantity", "sku", "price") if item.get(key) is not None} for item in cleaned["line_items"] if isinstance(item, dict)]
    return {"topic": topic, "data": cleaned}


def process_webhook(db: Session, store: Store, topic: str, webhook_id: str, payload: dict) -> bool:
    occurred_value = payload.get("updated_at") or payload.get("created_at") or payload.get("processed_at")
    try:
        occurred = datetime.fromisoformat(str(occurred_value).replace("Z", "+00:00")) if occurred_value else datetime.now(timezone.utc)
    except ValueError:
        occurred = datetime.now(timezone.utc)
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=timezone.utc)
    event_insert = insert(SourceEvent).values(store_id=store.id, provider_event_id=webhook_id[:200], source="shopify", event_type=topic[:100], payload=sanitized_event(topic, payload), occurred_at=occurred, processing_status="pending", attempts=0).on_conflict_do_nothing(constraint="source_events_store_id_source_provider_event_id_key").returning(SourceEvent.id)
    event_id = db.scalar(event_insert)
    if event_id is None:
        return False
    remote_order_id = payload.get("id") if topic != "refunds/create" else payload.get("order_id")
    if topic == "orders/delete":
        existing = db.scalar(select(Order).where(Order.store_id == store.id, Order.shopify_order_id == str(remote_order_id))) if remote_order_id else None
        if existing:
            existing.outcome_status = "deleted"
    elif topic == "refunds/create":
        existing = db.scalar(select(Order).where(Order.store_id == store.id, Order.shopify_order_id == str(remote_order_id))) if remote_order_id else None
        if existing and existing.outcome_status not in {"refunded", "partially_refunded"}:
            existing.outcome_status = "refund_activity"
    elif topic in {"orders/create", "orders/updated", "orders/cancelled", "orders/fulfilled"}:
        normalize_order(db, store, payload)
    event = db.get(SourceEvent, event_id)
    event.processing_status = "processed"
    event.attempts = 1
    store.last_synced_at = datetime.now(timezone.utc)
    db.commit()
    return True


def fetch_and_store_orders(db: Session, store: Store, limit: int, after: str | None = None) -> dict:
    synced = 0
    has_more = False
    next_cursor = None
    while synced < limit:
        batch_size = min(100, limit - synced)
        data = graphql(db, store, ORDER_QUERY, {"first": batch_size, "after": after}).get("orders") or {}
        edges = data.get("edges") or []
        for edge in edges:
            node = edge.get("node") or {}
            order = normalize_order(db, store, node)
            if order:
                remote_event = f"sync:{order.shopify_order_id}:{node.get('updatedAt') or node.get('createdAt') or ''}"[:200]
                occurred = datetime.fromisoformat(str(node.get("updatedAt") or node.get("createdAt")).replace("Z", "+00:00")) if node.get("updatedAt") or node.get("createdAt") else datetime.now(timezone.utc)
                db.execute(insert(SourceEvent).values(store_id=store.id, order_id=order.id, provider_event_id=remote_event, source="shopify", event_type="orders/sync", payload={"shopify_order_id": order.shopify_order_id, "outcome_status": order.outcome_status}, occurred_at=occurred, processing_status="processed", attempts=1).on_conflict_do_nothing(constraint="source_events_store_id_source_provider_event_id_key"))
                synced += 1
        db.commit()
        page = data.get("pageInfo") or {}
        has_more = bool(page.get("hasNextPage"))
        next_cursor = page.get("endCursor")
        if not edges or not has_more or not next_cursor:
            has_more = False
            break
        after = next_cursor
    store.last_synced_at = datetime.now(timezone.utc)
    db.commit()
    return {"synced": synced, "has_more": has_more, "next_cursor": next_cursor if has_more else None}


def order_output(order: Order, customer: Customer | None) -> dict:
    return {
        "id": order.shopify_order_id,
        "customer": customer.name if customer else "Customer unavailable",
        "email": customer.email if customer else "",
        "date": order.ordered_at.isoformat(),
        "amount": float(order.total_amount),
        "currency": order.currency,
        "payment": order.payment_method or "Unavailable",
        "outcome": order.outcome_status.replace("_", " ").title(),
        "city": (order.location or {}).get("city", "Location unavailable"),
        "region": (order.location or {}).get("region", ""),
        "items": order.items or [],
        "fulfillment": (order.delivery_metadata or {}).get("fulfillment_status"),
    }
