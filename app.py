"""UPI Key Store — MongoDB persistent (crash-safe)."""
import os, secrets, json, string
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import quote
from typing import Optional, Tuple, List, Any

from fastapi import FastAPI, Request, Form, HTTPException, Depends, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from mangum import Mangum
import httpx

# Lazy / safe pymongo import
try:
    from pymongo import MongoClient, ASCENDING, DESCENDING, ReturnDocument
    from bson import ObjectId
    HAS_MONGO = True
except ImportError as _e:
    HAS_MONGO = False
    MongoClient = None
    ObjectId = None
    ASCENDING = DESCENDING = ReturnDocument = None
    _MONGO_IMPORT_ERR = str(_e)

UPI_ID = os.getenv("UPI_ID", "")
API_KEY = os.getenv("API_KEY", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
QR_API = "https://fampay.anujbots.xyz/qr.php"
VERIFY_API = "https://fampay.anujbots.xyz/verify.php"
QR_EXPIRY_MINUTES = 5
POLL_INTERVAL_SECONDS = 4

_tpl_dir = Path(__file__).parent / "templates"


# ===== PANEL =====


class PanelError(Exception):
    pass


def _norm_base(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        raise PanelError("Panel URL is required")
    if not url.startswith("http"):
        url = "https://" + url
    return url


def _extract_key(obj) -> Optional[str]:
    if obj is None:
        return None
    if isinstance(obj, str):
        s = obj.strip()
        return s if s and "html" not in s[:40].lower() else None
    if isinstance(obj, dict):
        for k in ("key", "license", "license_key", "generated_key", "code", "token", "value"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        data = obj.get("data")
        if data is not None and data is not obj:
            got = _extract_key(data)
            if got:
                return got
        for k in ("keys", "items", "results"):
            v = obj.get(k)
            if isinstance(v, list) and v:
                got = _extract_key(v[-1]) or _extract_key(v[0])
                if got:
                    return got
    if isinstance(obj, list) and obj:
        return _extract_key(obj[-1]) or _extract_key(obj[0])
    return None


def _duration_parts(hours: float) -> Tuple[int, str]:
    """Map store hours to panel duration_value + duration_unit."""
    h = float(hours or 1)
    # Prefer whole days when exact multiples of 24
    if h >= 24 and abs(h % 24) < 1e-9:
        return int(h // 24), "days"
    if h < 1:
        mins = max(1, int(round(h * 60)))
        return mins, "minutes"
    return max(1, int(round(h))), "hours"


async def login(base: str, username: str, password: str) -> httpx.AsyncClient:
    base = _norm_base(base)
    username = (username or "").strip()
    password = password or ""
    if not username or not password:
        raise PanelError("Panel username and password are required")

    client = httpx.AsyncClient(follow_redirects=True, timeout=20.0, headers={
        "User-Agent": "Mozilla/5.0 KeyStore/1.0",
        "Accept": "text/html,application/json",
    })
    try:
        r = await client.post(
            f"{base}/login",
            data={"username": username, "password": password},
        )
        text = r.text or ""
        if r.status_code >= 400:
            await client.aclose()
            raise PanelError(f"Panel login HTTP {r.status_code}")
        if "invalid credentials" in text.lower() or "attempt(s) left" in text.lower():
            await client.aclose()
            raise PanelError("Invalid panel username or password")
        if str(r.url).rstrip("/").endswith("/login") and 'name="password"' in text:
            await client.aclose()
            raise PanelError("Panel login failed — still on login page")
        return client
    except PanelError:
        raise
    except Exception as e:
        await client.aclose()
        raise PanelError(f"Could not reach panel: {e}")


async def _try_generate(
    client: httpx.AsyncClient,
    base: str,
    hours: float,
    name: str = "STORE",
) -> Tuple[Optional[str], str]:
    value, unit = _duration_parts(hours)
    body = {
        "name": name or "STORE",
        "duration_value": str(value),
        "duration_unit": unit,
        "device_limit": "1",
    }
    try:
        r = await client.post(f"{base}/api/generate", json=body)
    except Exception as e:
        return None, str(e)

    last = f"HTTP {r.status_code} {r.text[:400]}"
    if r.status_code in (401, 302, 303):
        raise PanelError("Panel session expired / not logged in")

    data = None
    text = r.text or ""
    ct = r.headers.get("content-type", "")
    if "json" in ct or text.lstrip().startswith("{") or text.lstrip().startswith("["):
        try:
            data = r.json()
        except Exception:
            data = None

    if isinstance(data, dict) and data.get("error"):
        return None, str(data.get("error"))

    key = _extract_key(data) or _extract_key(text)
    if key and "login" not in key.lower() and "<html" not in key.lower():
        return key, json.dumps(body)
    return None, last


async def generate_key(
    panel_url: str,
    username: str,
    password: str,
    hours: float = 1.0,
    name: str = "STORE",
) -> str:
    """Login to Overload panel and mint a real key for the given duration."""
    base = _norm_base(panel_url)
    client = await login(base, username, password)
    try:
        key, used = await _try_generate(client, base, hours, name=name)
        if not key:
            raise PanelError(
                f"Logged in, but key generate failed for {hours}h. Panel reply: {used[:400]}"
            )
        return key
    finally:
        await client.aclose()


async def test_connection(panel_url: str, username: str, password: str) -> str:
    """Verify credentials by minting a real 1-hour key."""
    return await generate_key(panel_url, username, password, hours=1, name="STORE-TEST")

# ===== MONGO DB =====
_client = None
_db = None

DEFAULT_PANEL_PLANS = [
    ("5 Hours", 5, 39),
    ("12 Hours", 12, 79),
    ("1 Day", 24, 100),
    ("3 Days", 72, 250),
    ("7 Days", 168, 499),
    ("15 Days", 360, 699),
    ("30 Days", 720, 899),
]

def _get_db():
    global _client, _db
    if _db is not None:
        return _db
    if not HAS_MONGO:
        raise RuntimeError("pymongo not installed: " + globals().get("_MONGO_IMPORT_ERR", ""))
    if not MONGODB_URI:
        raise RuntimeError("MONGODB_URI env var is not set on Vercel")
    _client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=10000)
    # Always use fixed db name (URI often has no db path)
    _db = _client["keystore"]
    return _db

def _oid(v):
    if v is None or ObjectId is None:
        return None
    if isinstance(v, ObjectId):
        return v
    try:
        return ObjectId(str(v))
    except Exception:
        return None

def _doc_id(doc):
    if not doc:
        return doc
    d = dict(doc)
    if "_id" in d:
        d["id"] = str(d["_id"])
        del d["_id"]
    return d

async def init_db():
    db = _get_db()
    db.products.create_index([("is_active", ASCENDING)])
    db.variants.create_index([("product_id", ASCENDING)])
    db.keys.create_index([("product_id", ASCENDING), ("status", ASCENDING)])
    db.orders.create_index([("order_id", ASCENDING)], unique=True)

async def add_product(name, ptype, description="", panel_url="", panel_user="", panel_pass="", apk_url=""):
    db = _get_db()
    res = db.products.insert_one({
        "name": name, "type": ptype, "description": description or "",
        "panel_url": panel_url or "", "panel_user": panel_user or "", "panel_pass": panel_pass or "",
        "apk_url": apk_url or "",
        "is_active": 1, "created_at": datetime.utcnow().isoformat(),
    })
    return str(res.inserted_id)

async def get_products(active_only=True):
    db = _get_db()
    q = {"is_active": 1} if active_only else {}
    return [_doc_id(d) for d in db.products.find(q).sort("_id", DESCENDING)]

async def get_product(pid):
    oid = _oid(pid)
    if not oid:
        return None
    doc = _get_db().products.find_one({"_id": oid})
    return _doc_id(doc) if doc else None

async def delete_product(pid):
    oid = _oid(pid)
    if oid:
        _get_db().products.update_one({"_id": oid}, {"$set": {"is_active": 0}})

async def add_variant(product_id, label, duration_hours, price):
    res = _get_db().variants.insert_one({
        "product_id": str(product_id), "label": label,
        "duration_hours": float(duration_hours), "price": float(price), "is_active": 1,
    })
    return str(res.inserted_id)

async def seed_default_panel_variants(product_id):
    for label, hours, price in DEFAULT_PANEL_PLANS:
        await add_variant(product_id, label, hours, price)

async def get_variants(product_id):
    cur = _get_db().variants.find({"product_id": str(product_id), "is_active": 1}).sort("_id", ASCENDING)
    return [_doc_id(d) for d in cur]

async def get_variant(vid):
    oid = _oid(vid)
    if not oid:
        return None
    doc = _get_db().variants.find_one({"_id": oid})
    return _doc_id(doc) if doc else None

async def delete_variant(vid):
    oid = _oid(vid)
    if oid:
        _get_db().variants.update_one({"_id": oid}, {"$set": {"is_active": 0}})

async def add_keys_bulk(product_id, variant_id, keys_text):
    if isinstance(keys_text, str):
        lines = [ln.strip() for ln in keys_text.replace(",", "\n").splitlines() if ln.strip()]
    else:
        lines = [str(keys_text).strip()] if keys_text else []
    if not lines:
        return 0
    docs = [{
        "product_id": str(product_id),
        "variant_id": str(variant_id) if variant_id else None,
        "key_value": k, "status": "available",
        "used_at": None, "used_order_id": None, "expires_at": None,
        "created_at": datetime.utcnow().isoformat(),
    } for k in lines]
    _get_db().keys.insert_many(docs)
    return len(docs)

async def get_stock(product_id, variant_id=None):
    q = {"product_id": str(product_id), "status": "available"}
    if variant_id is not None:
        q["variant_id"] = str(variant_id)
    return _get_db().keys.count_documents(q)

async def get_keys(product_id, limit=100):
    cur = _get_db().keys.find({"product_id": str(product_id)}).sort("_id", DESCENDING).limit(limit)
    return [_doc_id(d) for d in cur]

async def claim_next_key(product_id, variant_id, order_id):
    q = {
        "product_id": str(product_id),
        "variant_id": str(variant_id) if variant_id else None,
        "status": "available",
    }
    doc = _get_db().keys.find_one_and_update(
        q,
        {"$set": {"status": "used", "used_at": datetime.utcnow().isoformat(), "used_order_id": str(order_id)}},
        sort=[("_id", ASCENDING)],
        return_document=ReturnDocument.AFTER,
    )
    return doc["key_value"] if doc else None

async def create_order(order_id, product_id, variant_id, amount, qr_url):
    _get_db().orders.insert_one({
        "order_id": order_id, "product_id": str(product_id),
        "variant_id": str(variant_id) if variant_id else None,
        "amount": float(amount), "status": "pending", "qr_url": qr_url or "",
        "delivered_key": None, "delivered_type": None,
        "transaction_id": None, "utr": None, "sender_name": None, "payment_time": None,
        "created_at": datetime.utcnow().isoformat(), "paid_at": None,
    })

async def get_order_by_api_id(order_id):
    doc = _get_db().orders.find_one({"order_id": order_id})
    return _doc_id(doc) if doc else None

async def mark_order_paid(order_id, transaction_id="", utr="", sender_name="", payment_time="",
                          delivered_key="", delivered_type="key"):
    _get_db().orders.update_one({"order_id": order_id}, {"$set": {
        "status": "paid", "transaction_id": transaction_id or "", "utr": utr or "",
        "sender_name": sender_name or "", "payment_time": payment_time or "",
        "delivered_key": delivered_key or "", "delivered_type": delivered_type or "key",
        "paid_at": datetime.utcnow().isoformat(),
    }})

async def get_orders(limit=40):
    out = []
    for d in _get_db().orders.find().sort("_id", DESCENDING).limit(limit):
        row = _doc_id(d)
        p = await get_product(row["product_id"]) if row.get("product_id") else None
        row["product_name"] = p["name"] if p else None
        out.append(row)
    return out

# ===== APP =====
app = FastAPI(title="Key Store")
templates = Jinja2Templates(directory=str(_tpl_dir))
security = HTTPBasic()
handler = Mangum(app)

def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    if not (credentials.username == "admin" and secrets.compare_digest(credentials.password, ADMIN_PASSWORD)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect",
                            headers={"WWW-Authenticate": "Basic"})
    return credentials.username

@app.middleware("http")
async def ensure_db(request: Request, call_next):
    request.state.db_error = None
    try:
        if MONGODB_URI and HAS_MONGO:
            await init_db()
    except Exception as e:
        request.state.db_error = str(e)
    return await call_next(request)

def _redir_admin(pid=None, **qs):
    q = []
    if pid:
        q.append(f"pid={pid}")
    for k, v in qs.items():
        if v is not None:
            q.append(f"{k}={quote(str(v), safe='')}")
    return RedirectResponse("/adm-k9x2m7" + (("?" + "&".join(q)) if q else ""), status_code=303)

def _err_page(msg: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!DOCTYPE html><html><body style="background:#0a0a0f;color:#eee;font-family:system-ui;padding:2rem">
        <h1>Setup needed</h1><p style="color:#f87171">{msg}</p>
        <p>Set <code>MONGODB_URI</code> on Vercel → Settings → Environment Variables, then Redeploy.</p>
        <p><a href="/health" style="color:#818cf8">/health</a></p></body></html>""",
        status_code=503,
    )

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if not MONGODB_URI:
        return _err_page("MONGODB_URI is not set")
    if not HAS_MONGO:
        return _err_page("pymongo failed to import — check requirements.txt deploy")
    try:
        products = await get_products(active_only=True)
    except Exception as e:
        return _err_page(f"MongoDB error: {e}")
    for p in products:
        p["variants"] = await get_variants(p["id"])
        for v in p["variants"]:
            v["stock"] = -1 if p.get("type") == "panel" else await get_stock(p["id"], v["id"])
    return templates.TemplateResponse(request, "index.html", {"products": products})

@app.post("/buy")
async def buy(request: Request, product_id: str = Form(...), variant_id: str = Form(...)):
    product = await get_product(product_id)
    variant = await get_variant(variant_id)
    if not product or not product.get("is_active") or not variant:
        raise HTTPException(404, "Not found")
    if product["type"] == "bulk" and (await get_stock(product_id, variant_id)) <= 0:
        raise HTTPException(400, "Out of stock")
    if not UPI_ID or not API_KEY:
        raise HTTPException(400, "Payment not configured")
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            data = (await client.get(QR_API, params={"upi": UPI_ID, "amount": variant["price"]})).json()
        except Exception as e:
            raise HTTPException(502, f"QR:{e}")
    if data.get("status") != "success":
        raise HTTPException(502, str(data.get("message", "err")))
    qr = data["data"]
    await create_order(qr["order_id"], product_id, variant_id, float(qr.get("amount", variant["price"])), qr["qr_url"])
    return RedirectResponse(f"/pay/{qr['order_id']}", status_code=303)

@app.get("/pay/{order_id}", response_class=HTMLResponse)
async def pay_page(request: Request, order_id: str):
    order = await get_order_by_api_id(order_id)
    if not order:
        raise HTTPException(404, "Not found")
    product = await get_product(order["product_id"])
    variant = await get_variant(order["variant_id"]) if order.get("variant_id") else None
    if order["status"] == "paid":
        return templates.TemplateResponse(request, "success.html", {"order": order, "product": product, "variant": variant})
    return templates.TemplateResponse(request, "payment.html", {
        "order": order, "product": product, "variant": variant,
        "expiry_minutes": QR_EXPIRY_MINUTES, "poll_interval": POLL_INTERVAL_SECONDS,
    })

async def _deliver_for_paid_order(order, product, variant):
    if product["type"] == "bulk":
        key = await claim_next_key(order["product_id"], order["variant_id"], order["order_id"])
        return (key or "NO KEY – contact admin"), "key"
    hours = float(variant["duration_hours"]) if variant and variant.get("duration_hours") else 1.0
    return (await generate_key(
        product.get("panel_url") or "", product.get("panel_user") or "", product.get("panel_pass") or "",
        hours=hours,
    )), "key"

@app.get("/api/check/{order_id}")
async def check_payment(order_id: str):
    order = await get_order_by_api_id(order_id)
    if not order:
        return JSONResponse({"status": "error", "message": "not found"})
    if order["status"] == "paid":
        return {"status": "paid", "delivered_key": order.get("delivered_key"), "delivered_type": order.get("delivered_type")}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            data = (await client.get(VERIFY_API, params={"order_id": order_id, "api_key": API_KEY})).json()
        except Exception as e:
            return {"status": "pending", "message": str(e)}
    if data.get("status") != "success":
        return {"status": "pending", "message": data.get("message", "wait")}
    d = data["data"]
    product = await get_product(order["product_id"])
    variant = await get_variant(order["variant_id"]) if order.get("variant_id") else None
    try:
        delivered_key, delivered_type = await _deliver_for_paid_order(order, product, variant)
    except PanelError as e:
        delivered_key, delivered_type = f"PAID but key failed: {e}", "error"
    await mark_order_paid(
        order_id=order_id, transaction_id=d.get("transaction_id", ""), utr=d.get("utr", ""),
        sender_name=d.get("sender_name", ""), payment_time=d.get("payment_time_ist", ""),
        delivered_key=delivered_key, delivered_type=delivered_type,
    )
    return {"status": "paid", "delivered_key": delivered_key, "delivered_type": delivered_type}

@app.get("/success/{order_id}", response_class=HTMLResponse)
async def success(request: Request, order_id: str):
    order = await get_order_by_api_id(order_id)
    if not order or order["status"] != "paid":
        raise HTTPException(404, "Not found")
    product = await get_product(order["product_id"])
    variant = await get_variant(order["variant_id"]) if order.get("variant_id") else None
    return templates.TemplateResponse(request, "success.html", {"order": order, "product": product, "variant": variant})

@app.get("/adm-k9x2m7", response_class=HTMLResponse)
async def admin_home(request: Request, user: str = Depends(verify_admin)):
    if not MONGODB_URI:
        return _err_page("MONGODB_URI is not set")
    try:
        products = await get_products(active_only=False)
    except Exception as e:
        return _err_page(f"MongoDB error: {e}")
    for p in products:
        p["variants"] = await get_variants(p["id"])
        for v in p["variants"]:
            v["stock"] = -1 if p.get("type") == "panel" else await get_stock(p["id"], v["id"])
        p["total_stock"] = -1 if p.get("type") == "panel" else await get_stock(p["id"])
    return templates.TemplateResponse(request, "admin.html", {
        "products": products, "orders": await get_orders(40), "upi_id": UPI_ID,
    })

@app.post("/adm-k9x2m7/product/add")
async def admin_add_product(
    name: str = Form(...), ptype: str = Form(...), description: str = Form(""),
    panel_url: str = Form(""), panel_user: str = Form(""), panel_pass: str = Form(""),
    apk_url: str = Form(""),
    user: str = Depends(verify_admin),
):
    if ptype not in ("panel", "bulk"):
        raise HTTPException(400, "bad type")
    testkey = None
    if ptype == "panel":
        try:
            testkey = await test_connection(panel_url, panel_user, panel_pass)
        except PanelError as e:
            return _redir_admin(error=str(e))
    pid = await add_product(name, ptype, description, panel_url, panel_user, panel_pass, apk_url)
    if ptype == "panel":
        await seed_default_panel_variants(pid)
    if testkey:
        await add_keys_bulk(pid, 0, testkey)
        return _redir_admin(pid, testkey=testkey)
    return _redir_admin(pid)

@app.post("/adm-k9x2m7/variant/add")
async def admin_add_variant(
    product_id: str = Form(...), label: str = Form(...),
    duration_hours: float = Form(...), price: float = Form(...),
    user: str = Depends(verify_admin),
):
    await add_variant(product_id, label, duration_hours, price)
    return _redir_admin(product_id)

@app.post("/adm-k9x2m7/keys/add")
async def admin_add_keys(
    product_id: str = Form(...), variant_id: str = Form(...), keys_text: str = Form(...),
    user: str = Depends(verify_admin),
):
    return _redir_admin(product_id, added=await add_keys_bulk(product_id, variant_id, keys_text))

@app.post("/adm-k9x2m7/keys/test")
async def admin_gen_test(product_id: str = Form(...), hours: float = Form(1.0), user: str = Depends(verify_admin)):
    product = await get_product(product_id)
    if not product:
        return _redir_admin(error="not found")
    if product.get("type") != "panel":
        return _redir_admin(product_id, error="panel only")
    try:
        key = await generate_key(
            product.get("panel_url") or "", product.get("panel_user") or "",
            product.get("panel_pass") or "", hours=hours,
        )
    except PanelError as e:
        return _redir_admin(product_id, error=str(e))
    await add_keys_bulk(product_id, 0, key)
    return _redir_admin(product_id, testkey=key)

@app.post("/adm-k9x2m7/product/delete/{pid}")
async def admin_del_product(pid: str, user: str = Depends(verify_admin)):
    await delete_product(pid)
    return _redir_admin()

@app.post("/adm-k9x2m7/variant/delete/{vid}")
async def admin_del_variant(vid: str, product_id: str = Form(...), user: str = Depends(verify_admin)):
    await delete_variant(vid)
    return _redir_admin(product_id)

@app.get("/adm-k9x2m7/keys/{pid}", response_class=HTMLResponse)
async def admin_view_keys(request: Request, pid: str, user: str = Depends(verify_admin)):
    return templates.TemplateResponse(request, "admin_keys.html", {
        "product": await get_product(pid), "keys": await get_keys(pid), "variants": await get_variants(pid),
    })

@app.get("/health")
async def health():
    info = {
        "status": "ok", "has_mongo_lib": HAS_MONGO, "mongo_uri_set": bool(MONGODB_URI),
        "upi_set": bool(UPI_ID), "api_key_set": bool(API_KEY),
        "time": datetime.utcnow().isoformat(),
    }
    if MONGODB_URI and HAS_MONGO:
        try:
            _get_db().command("ping")
            info["mongo"] = True
        except Exception as e:
            info["mongo"] = False
            info["mongo_error"] = str(e)
            info["status"] = "db_error"
    else:
        info["mongo"] = False
        info["status"] = "config_error"
    return info
