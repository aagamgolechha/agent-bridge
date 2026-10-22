"""
Mock Payment Gateway API - A realistic mock payment backend for Apache JMeter performance testing.

FastAPI + SQLite + SQLAlchemy single-file application.
"""

import os
import random
import uuid
import time
import asyncio
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, List

from dotenv import load_dotenv

load_dotenv()  # Local .env support; production platform env vars take precedence.

from fastapi import FastAPI, HTTPException, Depends, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import create_engine, Column, String, Float, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError

from loki_logging import configure_logging, is_loki_enabled, get_loki_url

logger = configure_logging()

BASE_DIR = Path(__file__).resolve().parent

# ──────────────────────────────────────────────
# Configuration via Environment Variables
# ──────────────────────────────────────────────
# Local development: copy .env.example to .env and adjust.
# Production: set these as platform env vars (they take precedence over .env).

MOCK_DATA_DIR = os.getenv("MOCK_DATA_DIR", str(BASE_DIR))
MIN_LATENCY_MS = max(0, int(os.getenv("MOCK_MIN_LATENCY_MS", "50")))
MAX_LATENCY_MS = max(0, int(os.getenv("MOCK_MAX_LATENCY_MS", "300")))
if MAX_LATENCY_MS < MIN_LATENCY_MS:
    MAX_LATENCY_MS = MIN_LATENCY_MS
SYSTEM_ERROR_RATE = min(100, max(0, int(os.getenv("MOCK_SYSTEM_ERROR_RATE", "0"))))
THROTTLE_ENABLED = os.getenv("MOCK_THROTTLE_ENABLED", "false").lower() == "true"
THROTTLE_RPM = int(os.getenv("MOCK_THROTTLE_REQUESTS_PER_MINUTE", "60"))
SUCCESS_RATE = int(os.getenv("MOCK_PAYMENT_SUCCESS_RATE", "90"))
DECLINE_RATE = int(os.getenv("MOCK_PAYMENT_DECLINE_RATE", "8"))
PENDING_RATE = int(os.getenv("MOCK_PAYMENT_PENDING_RATE", "2"))
# --- Fault injection (explicit, opt-in; all default to "no fault") ---
MOCK_SEED = os.getenv("MOCK_SEED", "").strip()  # e.g. "42" for deterministic runs
MOCK_FORCE_OUTCOME = os.getenv("MOCK_FORCE_OUTCOME", "").strip().upper()  # AUTHORIZED|DECLINED|PENDING|FAILED|""
MOCK_FORCE_SYSTEM_ERROR = os.getenv("MOCK_FORCE_SYSTEM_ERROR", "false").lower() == "true"
MOCK_DB_ERROR_RATE = min(100, max(0, int(os.getenv("MOCK_DB_ERROR_RATE", "0"))))
MOCK_FORCE_DB_ERROR = os.getenv("MOCK_FORCE_DB_ERROR", "false").lower() == "true"
DATABASE_URL = os.getenv("DATABASE_URL", "")


def _resolve_database_url() -> str:
    """Resolve the SQLAlchemy URL, defaulting to a file next to this app.

    Honors DATABASE_URL when set (e.g. ``postgresql://...`` from a host,
    or ``sqlite:////data/mock_payments.db`` on a persistent disk).
    Otherwise stores SQLite at ``MOCK_DATA_DIR/mock_payments.db`` so the
    path does not depend on the process working directory.
    """
    if DATABASE_URL:
        return DATABASE_URL
    Path(MOCK_DATA_DIR).mkdir(parents=True, exist_ok=True)
    db_path = Path(MOCK_DATA_DIR) / "mock_payments.db"
    return f"sqlite:///{db_path}"


RESOLVED_DATABASE_URL = _resolve_database_url()

if MOCK_SEED:
    try:
        random.seed(int(MOCK_SEED))
        logger.info("Deterministic mock mode enabled seed=%s", MOCK_SEED)
    except ValueError:
        logger.warning("Ignoring invalid MOCK_SEED=%r (must be an integer)", MOCK_SEED)
        MOCK_SEED = ""

# ──────────────────────────────────────────────
# SQLAlchemy Setup
# ──────────────────────────────────────────────

_sqlite_kwargs = {"check_same_thread": False} if RESOLVED_DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(RESOLVED_DATABASE_URL, connect_args=_sqlite_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ──────────────────────────────────────────────
# ORM Models
# ──────────────────────────────────────────────

class CustomerModel(Base):
    __tablename__ = "customers"
    customer_id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False, unique=True, index=True)
    created_at = Column(DateTime, nullable=False)


class PaymentModel(Base):
    __tablename__ = "payments"
    payment_id = Column(String, primary_key=True, index=True)
    customer_id = Column(String, nullable=False, index=True)
    amount = Column(Float, nullable=False)
    currency = Column(String, nullable=False, default="USD")
    card_last4 = Column(String, nullable=False)
    card_brand = Column(String, nullable=False)
    status = Column(String, nullable=False, default="PENDING", index=True)
    authorization_code = Column(String, nullable=True)
    transaction_reference = Column(String, nullable=True)
    failure_reason = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


class SubscriptionModel(Base):
    __tablename__ = "subscriptions"
    subscription_id = Column(String, primary_key=True, index=True)
    customer_id = Column(String, nullable=False, index=True)
    plan_id = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String, nullable=False, default="USD")
    billing_cycle = Column(String, nullable=False, default="monthly")
    status = Column(String, nullable=False, default="ACTIVE", index=True)
    start_date = Column(DateTime, nullable=False)
    next_billing_date = Column(DateTime, nullable=False)
    cancelled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


# ──────────────────────────────────────────────
# Pydantic Schemas
# ──────────────────────────────────────────────

class CardInfo(BaseModel):
    card_number: str = Field(..., min_length=13, max_length=19, examples=["4111111111111111"])
    expiry: str = Field(..., examples=["12/28"])
    cvv: str = Field(..., min_length=3, max_length=4, examples=["123"])

    @field_validator("card_number")
    @classmethod
    def clean_card_number(cls, v):
        cleaned = v.replace(" ", "").replace("-", "")
        if not cleaned.isdigit():
            raise ValueError("Card number must contain only digits")
        if len(cleaned) < 13 or len(cleaned) > 19:
            raise ValueError("Card number must be between 13 and 19 digits")
        return cleaned


class CustomerInfo(BaseModel):
    name: str = Field(..., min_length=1, examples=["John Doe"])
    email: str = Field(..., examples=["john@example.com"])


class PaymentAuthorizeRequest(BaseModel):
    customer: CustomerInfo
    card: CardInfo
    amount: float = Field(..., gt=0, le=999999.99, examples=[99.99])
    currency: str = Field(default="USD", examples=["USD"])


class PaymentCaptureRequest(BaseModel):
    payment_id: str = Field(..., examples=["pay_abc123"])


class PaymentRefundRequest(BaseModel):
    payment_id: str = Field(..., examples=["pay_abc123"])


class PaymentResponse(BaseModel):
    payment_id: str
    customer_id: str
    amount: float
    currency: str
    card_last4: str
    card_brand: str
    status: str
    authorization_code: Optional[str] = None
    transaction_reference: Optional[str] = None
    failure_reason: Optional[str] = None
    created_at: str
    updated_at: str


class SubscriptionCreateRequest(BaseModel):
    customer: CustomerInfo
    plan_id: str = Field(..., min_length=1, examples=["plan_basic"])
    amount: float = Field(..., gt=0, le=999999.99, examples=[29.99])
    currency: str = Field(default="USD", examples=["USD"])
    billing_cycle: str = Field(default="monthly", examples=["monthly", "yearly"])


class SubscriptionResponse(BaseModel):
    subscription_id: str
    customer_id: str
    plan_id: str
    amount: float
    currency: str
    billing_cycle: str
    status: str
    start_date: str
    next_billing_date: str
    cancelled_at: Optional[str] = None
    created_at: str
    updated_at: str


class ErrorResponse(BaseModel):
    detail: str
    error_code: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    timestamp: str
    database: str
    mock_config: dict


# ──────────────────────────────────────────────
# Mock Decision Engine
# ──────────────────────────────────────────────

DECLINE_REASONS = [
    "INSUFFICIENT_FUNDS",
    "CARD_DECLINED",
    "EXPIRED_CARD",
    "LIMIT_EXCEEDED",
    "INVALID_CARD",
]

CARD_BRANDS = {"4": "Visa", "5": "Mastercard", "3": "Amex", "6": "Discover"}


def detect_card_brand(card_number: str) -> str:
    prefix = card_number.replace(" ", "").replace("-", "")[0]
    return CARD_BRANDS.get(prefix, "Unknown")


def determine_payment_outcome():
    # Deterministic override for tests/demos (seeded randomness otherwise).
    if MOCK_FORCE_OUTCOME in ("AUTHORIZED", "DECLINED", "PENDING", "FAILED"):
        reason = None
        if MOCK_FORCE_OUTCOME == "DECLINED":
            reason = "CARD_DECLINED"
        elif MOCK_FORCE_OUTCOME == "FAILED":
            reason = "SYSTEM_ERROR"
        logger.warning("Forced payment outcome applied outcome=%s", MOCK_FORCE_OUTCOME)
        return MOCK_FORCE_OUTCOME, reason
    rand = random.randint(1, 100)
    if rand <= SUCCESS_RATE:
        return "AUTHORIZED", None
    cumulative = SUCCESS_RATE + DECLINE_RATE
    if rand <= cumulative:
        return "DECLINED", random.choice(DECLINE_REASONS)
    cumulative += PENDING_RATE
    if rand <= cumulative:
        return "PENDING", None
    return "FAILED", "SYSTEM_ERROR"


def maybe_raise_simulated_db_error(operation: str) -> None:
    """Raise a simulated DB error when fault injection is armed.

    Logs context (operation only — never payment secrets) and raises
    SQLAlchemyError so existing handlers return a structured 500/DB_ERROR.
    """
    if MOCK_FORCE_DB_ERROR or (
        MOCK_DB_ERROR_RATE > 0 and random.randint(1, 100) <= MOCK_DB_ERROR_RATE
    ):
        logger.error(
            "Simulated database error injected operation=%s force_db_error=%s db_error_rate=%s",
            operation,
            MOCK_FORCE_DB_ERROR,
            MOCK_DB_ERROR_RATE,
        )
        raise SQLAlchemyError(f"Simulated database error during {operation}")


# ──────────────────────────────────────────────
# Database Helpers
# ──────────────────────────────────────────────

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def db_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def generate_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def generate_authorization_code() -> str:
    return f"AUTH_{uuid.uuid4().hex[:8].upper()}"


def generate_transaction_reference() -> str:
    return f"TXN_{uuid.uuid4().hex[:10].upper()}"


def get_or_create_customer(db: Session, name: str, email: str) -> str:
    existing = db.query(CustomerModel).filter(CustomerModel.email == email).first()
    if existing:
        return existing.customer_id
    now = db_now()
    customer = CustomerModel(
        customer_id=generate_id("cus"), name=name, email=email, created_at=now
    )
    db.add(customer)
    db.flush()
    return customer.customer_id


def payment_to_response(p: PaymentModel) -> dict:
    return {
        "payment_id": p.payment_id,
        "customer_id": p.customer_id,
        "amount": p.amount,
        "currency": p.currency,
        "card_last4": p.card_last4,
        "card_brand": p.card_brand,
        "status": p.status,
        "authorization_code": p.authorization_code,
        "transaction_reference": p.transaction_reference,
        "failure_reason": p.failure_reason,
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
    }


def subscription_to_response(s: SubscriptionModel) -> dict:
    return {
        "subscription_id": s.subscription_id,
        "customer_id": s.customer_id,
        "plan_id": s.plan_id,
        "amount": s.amount,
        "currency": s.currency,
        "billing_cycle": s.billing_cycle,
        "status": s.status,
        "start_date": s.start_date.isoformat(),
        "next_billing_date": s.next_billing_date.isoformat(),
        "cancelled_at": s.cancelled_at.isoformat() if s.cancelled_at else None,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }


# ──────────────────────────────────────────────
# Throttle State
# ──────────────────────────────────────────────

class ThrottleState:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.request_times: List[float] = []

    async def check(self) -> bool:
        async with self.lock:
            now = time.time()
            cutoff = now - 60.0  # Sliding 1-minute window
            self.request_times = [t for t in self.request_times if t > cutoff]
            if len(self.request_times) >= THROTTLE_RPM:
                return False
            self.request_times.append(now)
            return True


throttle_state = ThrottleState()


# ──────────────────────────────────────────────
# Application Lifespan
# ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Mock Payment Gateway starting up...")
    Base.metadata.create_all(bind=engine)
    logger.info("Database initialized successfully")
    yield
    logger.info("Mock Payment Gateway shutting down...")


# ──────────────────────────────────────────────
# FastAPI Application
# ──────────────────────────────────────────────

app = FastAPI(
    title="Mock Payment Gateway API",
    description="A realistic mock payment backend designed for Apache JMeter performance testing.\n\n"
    "Supports configurable latency, throttling, error injection, and payment outcome simulation.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

# ──────────────────────────────────────────────
# CORS Middleware
# ──────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──────────────────────────────────────────────
# Request Logging Middleware
# ──────────────────────────────────────────────

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()

    logger.info(
        "HTTP request started method=%s path=%s",
        request.method,
        request.url.path,
    )

    try:
        response = await call_next(request)

        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "HTTP request completed method=%s path=%s status=%s duration_ms=%.2f",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response

    except Exception:
        logger.exception(
            "HTTP request failed method=%s path=%s",
            request.method,
            request.url.path,
        )
        raise

# ──────────────────────────────────────────────
# Middleware: Latency, Error Injection, Throttling
# ──────────────────────────────────────────────

@app.middleware("http")
async def mock_simulation_middleware(request: Request, call_next):
    path = request.url.path
    # Static frontend + docs/health bypass fault injection.
    if path in ("/", "/app.js", "/styles.css", "/health", "/docs", "/openapi.json", "/favicon.ico") or path.startswith(
        ("/docs/", "/openapi/", "/static/")
    ):
        return await call_next(request)

    if MOCK_FORCE_SYSTEM_ERROR or (
        SYSTEM_ERROR_RATE > 0 and random.randint(1, 100) <= SYSTEM_ERROR_RATE
    ):
        logger.error(
            "System error simulation triggered method=%s path=%s forced=%s rate=%s",
            request.method,
            path,
            MOCK_FORCE_SYSTEM_ERROR,
            SYSTEM_ERROR_RATE,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Simulated system error", "error_code": "SYSTEM_ERROR"},
        )

    if THROTTLE_ENABLED:
        allowed = await throttle_state.check()
        if not allowed:
            logger.warning("Rate limit exceeded for method=%s path=%s", request.method, path)
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too Many Requests - rate limit exceeded",
                    "error_code": "RATE_LIMITED",
                },
            )

    if MOCK_SEED:
        # Deterministic latency when a seed is set (midpoint of the range).
        delay_ms = (MIN_LATENCY_MS + MAX_LATENCY_MS) / 2.0
    else:
        delay_ms = random.uniform(MIN_LATENCY_MS, MAX_LATENCY_MS)
    await asyncio.sleep(delay_ms / 1000.0)

    return await call_next(request)


# ──────────────────────────────────────────────
# Exception Handlers
# ──────────────────────────────────────────────

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    logger.warning("HTTPException on %s %s: status_code=%s detail=%s", request.method, request.url.path, exc.status_code, exc.detail)
    content = exc.detail
    if isinstance(content, str):
        content = {"detail": content, "error_code": "HTTP_ERROR"}
    elif "error_code" not in content:
        content["error_code"] = "HTTP_ERROR"
    return JSONResponse(status_code=exc.status_code, content=content)


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error_code": "INTERNAL_ERROR"},
    )


# ──────────────────────────────────────────────
# Frontend (single-server static hosting)
# ──────────────────────────────────────────────
# index.html / app.js / styles.css live next to this file and are
# served by the same FastAPI process so the UI works same-origin
# (no hardcoded localhost API base URL needed).

if (BASE_DIR / "assets").is_dir():
    app.mount("/static", StaticFiles(directory=BASE_DIR / "assets"), name="static")


@app.get("/", include_in_schema=False)
async def serve_frontend():
    index_path = BASE_DIR / "index.html"
    if not index_path.is_file():
        return JSONResponse(
            status_code=404,
            content={"detail": "Frontend not found", "error_code": "NOT_FOUND"},
        )
    return FileResponse(index_path, media_type="text/html")


@app.get("/app.js", include_in_schema=False)
async def serve_app_js():
    return FileResponse(BASE_DIR / "app.js", media_type="application/javascript")


@app.get("/styles.css", include_in_schema=False)
async def serve_styles_css():
    return FileResponse(BASE_DIR / "styles.css", media_type="text/css")


@app.get("/favicon.ico", include_in_schema=False)
async def serve_favicon():
    favicon = BASE_DIR / "favicon.ico"
    if not favicon.is_file():
        return JSONResponse(status_code=204, content=None)
    return FileResponse(favicon, media_type="image/x-icon")


# ──────────────────────────────────────────────
# Health Endpoint
# ──────────────────────────────────────────────

@app.get(
    "/health",
    tags=["Health"],
    summary="Health check",
    description="Returns service status, current timestamp, database connectivity, and active mock configuration values.",
    response_model=HealthResponse,
)
async def health(db: Session = Depends(get_db)):
    db_ok = False
    try:
        from sqlalchemy import text as sa_text
        db.execute(sa_text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    return HealthResponse(
        status="healthy" if db_ok else "degraded",
        timestamp=db_now().isoformat(),
        database="connected" if db_ok else "disconnected",
        mock_config={
            "min_latency_ms": MIN_LATENCY_MS,
            "max_latency_ms": MAX_LATENCY_MS,
            "system_error_rate_pct": SYSTEM_ERROR_RATE,
            "throttle_enabled": THROTTLE_ENABLED,
            "throttle_requests_per_minute": THROTTLE_RPM,
            "payment_success_rate_pct": SUCCESS_RATE,
            "payment_decline_rate_pct": DECLINE_RATE,
            "payment_pending_rate_pct": PENDING_RATE,
            "seed": MOCK_SEED or None,
            "force_outcome": MOCK_FORCE_OUTCOME or None,
            "force_system_error": MOCK_FORCE_SYSTEM_ERROR,
            "db_error_rate_pct": MOCK_DB_ERROR_RATE,
            "force_db_error": MOCK_FORCE_DB_ERROR,
            "loki_enabled": is_loki_enabled(),
            "loki_url_configured": bool(get_loki_url()),
        },
    )


# ──────────────────────────────────────────────
# Payment Endpoints
# ──────────────────────────────────────────────

@app.post(
    "/payments/authorize",
    tags=["Payments"],
    summary="Authorize a payment",
    description="Simulates card authorization. Outcome is determined by configured success/decline/pending/error rates.",
    response_model=PaymentResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request"},
        500: {"model": ErrorResponse, "description": "Simulated system error"},
    },
)
async def authorize_payment(req: PaymentAuthorizeRequest, db: Session = Depends(get_db)):
    card_last4 = req.card.card_number[-4:]
    logger.info(
        "Payment authorization request received customer=%s amount=%.2f currency=%s card_last4=%s",
        req.customer.email,
        req.amount,
        req.currency,
        card_last4,
    )
    try:
        maybe_raise_simulated_db_error("payments.authorize")
        now = db_now()
        customer_id = get_or_create_customer(db, req.customer.name, req.customer.email)
        card_brand = detect_card_brand(req.card.card_number)
        status, failure_reason = determine_payment_outcome()

        payment = PaymentModel(
            payment_id=generate_id("pay"),
            customer_id=customer_id,
            amount=req.amount,
            currency=req.currency,
            card_last4=card_last4,
            card_brand=card_brand,
            status=status,
            authorization_code=generate_authorization_code() if status == "AUTHORIZED" else None,
            transaction_reference=generate_transaction_reference() if status != "FAILED" else None,
            failure_reason=failure_reason,
            created_at=now,
            updated_at=now,
        )
        db.add(payment)
        db.commit()
        db.refresh(payment)

        if status == "AUTHORIZED":
            logger.info(
                "Payment authorized successfully payment_id=%s auth_code=%s txn_ref=%s",
                payment.payment_id,
                payment.authorization_code,
                payment.transaction_reference,
            )
        elif status == "DECLINED":
            logger.warning(
                "Payment declined payment_id=%s reason=%s customer=%s",
                payment.payment_id,
                failure_reason,
                req.customer.email,
            )
        elif status == "PENDING":
            logger.info(
                "Payment pending payment_id=%s customer=%s",
                payment.payment_id,
                req.customer.email,
            )
        else:
            logger.error(
                "Payment failed payment_id=%s reason=%s customer=%s",
                payment.payment_id,
                failure_reason,
                req.customer.email,
            )

        return payment_to_response(payment)
    except SQLAlchemyError as exc:
        logger.exception("Database error occurred during payment authorization: %s", exc)
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail={"detail": "Database error occurred", "error_code": "DB_ERROR"},
        )


@app.post(
    "/payments/capture",
    tags=["Payments"],
    summary="Capture an authorized payment",
    description="Moves an AUTHORIZED payment to CAPTURED status.",
    response_model=PaymentResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Payment not in AUTHORIZED status"},
        404: {"model": ErrorResponse, "description": "Payment not found"},
    },
)
async def capture_payment(req: PaymentCaptureRequest, db: Session = Depends(get_db)):
    logger.info("Payment capture requested for payment_id=%s", req.payment_id)
    try:
        maybe_raise_simulated_db_error("payments.capture")
        payment = db.query(PaymentModel).filter(PaymentModel.payment_id == req.payment_id).first()
        if not payment:
            logger.warning("Payment capture failed: payment_id=%s not found", req.payment_id)
            raise HTTPException(
                status_code=404,
                detail={"detail": "Payment not found", "error_code": "NOT_FOUND"},
            )
        if payment.status != "AUTHORIZED":
            logger.warning(
                "Payment capture rejected: payment_id=%s status '%s' is not AUTHORIZED",
                req.payment_id,
                payment.status,
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "detail": f"Cannot capture payment in '{payment.status}' status. Must be 'AUTHORIZED'.",
                    "error_code": "INVALID_STATUS",
                },
            )
        payment.status = "CAPTURED"
        payment.updated_at = db_now()
        db.commit()
        db.refresh(payment)
        logger.info("Payment captured successfully payment_id=%s amount=%.2f", payment.payment_id, payment.amount)
        return payment_to_response(payment)
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError as exc:
        logger.exception("Database error during payment capture: %s", exc)
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail={"detail": "Database error occurred", "error_code": "DB_ERROR"},
        )


@app.post(
    "/payments/refund",
    tags=["Payments"],
    summary="Refund a captured payment",
    description="Moves a CAPTURED payment to REFUNDED status.",
    response_model=PaymentResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Payment not in CAPTURED status"},
        404: {"model": ErrorResponse, "description": "Payment not found"},
    },
)
async def refund_payment(req: PaymentRefundRequest, db: Session = Depends(get_db)):
    logger.info("Payment refund requested for payment_id=%s", req.payment_id)
    try:
        maybe_raise_simulated_db_error("payments.refund")
        payment = db.query(PaymentModel).filter(PaymentModel.payment_id == req.payment_id).first()
        if not payment:
            logger.warning("Payment refund failed: payment_id=%s not found", req.payment_id)
            raise HTTPException(
                status_code=404,
                detail={"detail": "Payment not found", "error_code": "NOT_FOUND"},
            )
        if payment.status != "CAPTURED":
            logger.warning(
                "Payment refund rejected: payment_id=%s status '%s' is not CAPTURED",
                req.payment_id,
                payment.status,
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "detail": f"Cannot refund payment in '{payment.status}' status. Must be 'CAPTURED'.",
                    "error_code": "INVALID_STATUS",
                },
            )
        payment.status = "REFUNDED"
        payment.updated_at = db_now()
        db.commit()
        db.refresh(payment)
        logger.info("Payment refunded successfully payment_id=%s amount=%.2f", payment.payment_id, payment.amount)
        return payment_to_response(payment)
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError as exc:
        logger.exception("Database error during payment refund: %s", exc)
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail={"detail": "Database error occurred", "error_code": "DB_ERROR"},
        )


@app.post(
    "/payment",
    tags=["Payments"],
    summary="Simple payment processing endpoint",
    description="Processes a payment and emits real-time logs to Loki.",
)
def process_payment():
    logger.info("Payment request received")
    try:
        status, failure_reason = determine_payment_outcome()
        if status == "AUTHORIZED":
            logger.info("Payment processed successfully")
            return {"status": "success", "result": "AUTHORIZED"}
        elif status == "DECLINED":
            logger.warning("Payment rejected by provider: reason=%s", failure_reason)
            return {"status": "declined", "reason": failure_reason}
        elif status == "PENDING":
            logger.warning("Payment provider responded slowly: status=PENDING")
            return {"status": "pending"}
        else:
            logger.error("Payment failed with system error: reason=%s", failure_reason)
            raise HTTPException(
                status_code=500,
                detail={"detail": "Payment failed", "error_code": failure_reason},
            )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Unexpected exception in payment processing")
        raise



@app.get(
    "/payments/{payment_id}",
    tags=["Payments"],
    summary="Get payment by ID",
    description="Retrieves a single payment record by its payment_id.",
    response_model=PaymentResponse,
    responses={404: {"model": ErrorResponse, "description": "Payment not found"}},
)
async def get_payment(payment_id: str, db: Session = Depends(get_db)):
    payment = db.query(PaymentModel).filter(PaymentModel.payment_id == payment_id).first()
    if not payment:
        raise HTTPException(
            status_code=404,
            detail={"detail": "Payment not found", "error_code": "NOT_FOUND"},
        )
    return payment_to_response(payment)


@app.get(
    "/payments",
    tags=["Payments"],
    summary="List payments",
    description="Lists payments with optional pagination and status filtering.",
    response_model=List[PaymentResponse],
)
async def list_payments(
    skip: int = Query(default=0, ge=0, description="Number of records to skip"),
    limit: int = Query(default=10, ge=1, le=100, description="Max records to return"),
    status: Optional[str] = Query(default=None, description="Filter by payment status"),
    db: Session = Depends(get_db),
):
    query = db.query(PaymentModel).order_by(PaymentModel.created_at.desc())
    if status:
        query = query.filter(PaymentModel.status == status.upper())
    payments = query.offset(skip).limit(limit).all()
    return [payment_to_response(p) for p in payments]


# ──────────────────────────────────────────────
# Subscription Endpoints
# ──────────────────────────────────────────────

@app.post(
    "/subscriptions",
    tags=["Subscriptions"],
    summary="Create a subscription",
    description="Creates a new subscription with ACTIVE status and sets the next billing date based on the billing cycle.",
    response_model=SubscriptionResponse,
)
async def create_subscription(req: SubscriptionCreateRequest, db: Session = Depends(get_db)):
    logger.info(
        "Subscription creation requested customer=%s plan_id=%s cycle=%s amount=%.2f",
        req.customer.email,
        req.plan_id,
        req.billing_cycle,
        req.amount,
    )
    try:
        maybe_raise_simulated_db_error("subscriptions.create")
        now = db_now()
        customer_id = get_or_create_customer(db, req.customer.name, req.customer.email)

        next_billing = now
        if req.billing_cycle == "monthly":
            month = now.month + 1
            year = now.year
            if month > 12:
                month = 1
                year += 1
            next_billing = now.replace(year=year, month=month)
        elif req.billing_cycle == "yearly":
            next_billing = now.replace(year=now.year + 1)
        elif req.billing_cycle == "weekly":
            next_billing = now  # simplified

        subscription = SubscriptionModel(
            subscription_id=generate_id("sub"),
            customer_id=customer_id,
            plan_id=req.plan_id,
            amount=req.amount,
            currency=req.currency,
            billing_cycle=req.billing_cycle,
            status="ACTIVE",
            start_date=now,
            next_billing_date=next_billing,
            cancelled_at=None,
            created_at=now,
            updated_at=now,
        )
        db.add(subscription)
        db.commit()
        db.refresh(subscription)
        logger.info(
            "Subscription created successfully subscription_id=%s customer_id=%s",
            subscription.subscription_id,
            customer_id,
        )
        return subscription_to_response(subscription)
    except SQLAlchemyError as exc:
        logger.exception("Database error during subscription creation: %s", exc)
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail={"detail": "Database error occurred", "error_code": "DB_ERROR"},
        )


@app.get(
    "/subscriptions/{subscription_id}",
    tags=["Subscriptions"],
    summary="Get subscription by ID",
    description="Retrieves a single subscription record by its subscription_id.",
    response_model=SubscriptionResponse,
    responses={404: {"model": ErrorResponse, "description": "Subscription not found"}},
)
async def get_subscription(subscription_id: str, db: Session = Depends(get_db)):
    sub = (
        db.query(SubscriptionModel)
        .filter(SubscriptionModel.subscription_id == subscription_id)
        .first()
    )
    if not sub:
        raise HTTPException(
            status_code=404,
            detail={"detail": "Subscription not found", "error_code": "NOT_FOUND"},
        )
    return subscription_to_response(sub)


@app.post(
    "/subscriptions/{subscription_id}/cancel",
    tags=["Subscriptions"],
    summary="Cancel a subscription",
    description="Sets an ACTIVE subscription to CANCELLED status and records the cancellation timestamp.",
    response_model=SubscriptionResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Subscription not in ACTIVE status"},
        404: {"model": ErrorResponse, "description": "Subscription not found"},
    },
)
async def cancel_subscription(subscription_id: str, db: Session = Depends(get_db)):
    logger.info("Subscription cancellation requested for subscription_id=%s", subscription_id)
    try:
        maybe_raise_simulated_db_error("subscriptions.cancel")
        sub = (
            db.query(SubscriptionModel)
            .filter(SubscriptionModel.subscription_id == subscription_id)
            .first()
        )
        if not sub:
            logger.warning("Subscription cancellation failed: subscription_id=%s not found", subscription_id)
            raise HTTPException(
                status_code=404,
                detail={"detail": "Subscription not found", "error_code": "NOT_FOUND"},
            )
        if sub.status != "ACTIVE":
            logger.warning(
                "Subscription cancellation rejected: subscription_id=%s status '%s' is not ACTIVE",
                subscription_id,
                sub.status,
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "detail": f"Cannot cancel subscription in '{sub.status}' status. Must be 'ACTIVE'.",
                    "error_code": "INVALID_STATUS",
                },
            )
        now = db_now()
        sub.status = "CANCELLED"
        sub.cancelled_at = now
        sub.updated_at = now
        db.commit()
        db.refresh(sub)
        logger.info("Subscription cancelled successfully subscription_id=%s", subscription_id)
        return subscription_to_response(sub)
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError as exc:
        logger.exception("Database error during subscription cancellation: %s", exc)
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail={"detail": "Database error occurred", "error_code": "DB_ERROR"},
        )



@app.get(
    "/subscriptions",
    tags=["Subscriptions"],
    summary="List subscriptions",
    description="Lists subscriptions with optional pagination and status filtering.",
    response_model=List[SubscriptionResponse],
)
async def list_subscriptions(
    skip: int = Query(default=0, ge=0, description="Number of records to skip"),
    limit: int = Query(default=10, ge=1, le=100, description="Max records to return"),
    status: Optional[str] = Query(default=None, description="Filter by subscription status"),
    db: Session = Depends(get_db),
):
    query = db.query(SubscriptionModel).order_by(SubscriptionModel.created_at.desc())
    if status:
        query = query.filter(SubscriptionModel.status == status.upper())
    subs = query.offset(skip).limit(limit).all()
    return [subscription_to_response(s) for s in subs]


# ──────────────────────────────────────────────
# Deployment Instructions
# ──────────────────────────────────────────────

# ── Install Dependencies ──────────────────────
#   pip install fastapi uvicorn sqlalchemy pydantic
#
# ── Run (default config) ──────────────────────
#   uvicorn app:app --host 0.0.0.0 --port 8000
#
# ── Run (custom config, PowerShell) ───────────
#   $env:MOCK_MIN_LATENCY_MS=100
#   $env:MOCK_MAX_LATENCY_MS=500
#   $env:MOCK_SYSTEM_ERROR_RATE=2
#   $env:MOCK_PAYMENT_SUCCESS_RATE=90
#   $env:MOCK_PAYMENT_DECLINE_RATE=8
#   $env:MOCK_THROTTLE_ENABLED=true
#   $env:MOCK_THROTTLE_REQUESTS_PER_MINUTE=60
#   uvicorn app:app --host 0.0.0.0 --port 8000
#
# ── Swagger UI ────────────────────────────────
#   http://localhost:8000/docs
#
# ── Example Curl Commands ─────────────────────
#
# Health check:
#   curl -s http://localhost:8000/health
#
# Authorize payment:
#   curl -s -X POST http://localhost:8000/payments/authorize ^
#     -H "Content-Type: application/json" ^
#     -d "{\"customer\":{\"name\":\"John Doe\",\"email\":\"john@example.com\"},\"card\":{\"card_number\":\"4111111111111111\",\"expiry\":\"12/28\",\"cvv\":\"123\"},\"amount\":99.99,\"currency\":\"USD\"}"
#
# Capture payment (replace PAYMENT_ID):
#   curl -s -X POST http://localhost:8000/payments/capture ^
#     -H "Content-Type: application/json" ^
#     -d "{\"payment_id\":\"PAYMENT_ID\"}"
#
# Refund payment (replace PAYMENT_ID):
#   curl -s -X POST http://localhost:8000/payments/refund ^
#     -H "Content-Type: application/json" ^
#     -d "{\"payment_id\":\"PAYMENT_ID\"}"
#
# Get payment:
#   curl -s http://localhost:8000/payments/PAYMENT_ID
#
# List payments:
#   curl -s "http://localhost:8000/payments?skip=0&limit=10"
#
# Create subscription:
#   curl -s -X POST http://localhost:8000/subscriptions ^
#     -H "Content-Type: application/json" ^
#     -d "{\"customer\":{\"name\":\"Jane Doe\",\"email\":\"jane@example.com\"},\"plan_id\":\"plan_basic\",\"amount\":29.99,\"currency\":\"USD\",\"billing_cycle\":\"monthly\"}"
#
# Get subscription:
#   curl -s http://localhost:8000/subscriptions/SUBSCRIPTION_ID
#
# Cancel subscription:
#   curl -s -X POST http://localhost:8000/subscriptions/SUBSCRIPTION_ID/cancel
#
# List subscriptions:
#   curl -s "http://localhost:8000/subscriptions?skip=0&limit=10"


# ──────────────────────────────────────────────
# Main Entry Point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    # Host-provided PORT wins (Render/Railway/Heroku-style); default 8001
    # preserves the existing local mock_payment port.
    port = int(os.getenv("PORT", "8001"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
