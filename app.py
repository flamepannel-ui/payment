"""UPI Key Store."""
import os,secrets,json,string
from pathlib import Path
from datetime import datetime,timedelta
from urllib.parse import quote
from typing import Optional,Tuple,List
from fastapi import FastAPI,Request,Form,HTTPException,Depends,status
from fastapi.responses import HTMLResponse,RedirectResponse,JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic,HTTPBasicCredentials
from mangum import Mangum
import httpx,aiosqlite
UPI_ID=os.getenv("UPI_ID","")
API_KEY=os.getenv("API_KEY","")
ADMIN_PASSWORD=os.getenv("ADMIN_PASSWORD","admin123")
QR_API="https://fampay.anujbots.xyz/qr.php"
VERIFY_API="https://fampay.anujbots.xyz/verify.php"
QR_EXPIRY_MINUTES=5
POLL_INTERVAL_SECONDS=4
DB_PATH=os.environ.get("DB_PATH","/tmp/store.db")
_TEMPLATES=json.loads("{\"base.html\":\"<!DOCTYPE html>\\n<html lang=\\\"en\\\"><head><meta charset=\\\"utf-8\\\"><meta name=\\\"viewport\\\" content=\\\"width=device-width,initial-scale=1\\\">\\n<title>{% block title %}Store{% endblock %}</title>\\n<script src=\\\"https://cdn.tailwindcss.com\\\"></script>\\n<link rel=\\\"stylesheet\\\" href=\\\"https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css\\\">\\n<style>\\nbody{background:#0a0a0f;color:#e8e8ed;font-family:system-ui,sans-serif}\\n.card{background:#12121a;border:1px solid #252532;border-radius:16px}\\n.input-dark{background:#1a1a24;border:1px solid #2a2a38;border-radius:12px;color:#fff}\\n.btn-primary{background:linear-gradient(135deg,#6366f1,#8b5cf6);border-radius:12px}\\n</style></head>\\n<body class=\\\"min-h-screen\\\">{% block content %}{% endblock %}{% block scripts %}{% endblock %}</body></html>\",\"index.html\":\"{% extends \\\"base.html\\\" %}{% block title %}Select Access{% endblock %}{% block content %}\\n<div class=\\\"min-h-screen px-4 py-10 max-w-md mx-auto\\\">\\n<div class=\\\"text-center mb-8\\\"><h1 class=\\\"text-2xl font-bold tracking-wide\\\">Select Access</h1>\\n<p class=\\\"text-gray-500 text-sm mt-1\\\">Choose product & duration</p></div>\\n{% if products %}\\n{% for p in products %}\\n<div class=\\\"card p-5 mb-4\\\">\\n<p class=\\\"text-xs text-purple-400 uppercase tracking-wider mb-1\\\">{{ p.name }}</p>\\n{% if p.variants %}\\n<form action=\\\"/buy\\\" method=\\\"post\\\" class=\\\"space-y-2\\\">\\n<input type=\\\"hidden\\\" name=\\\"product_id\\\" value=\\\"{{ p.id }}\\\">\\n{% for v in p.variants %}\\n<label class=\\\"flex items-center justify-between input-dark px-4 py-3 cursor-pointer\\\">\\n<span class=\\\"flex items-center gap-3\\\">\\n<input type=\\\"radio\\\" name=\\\"variant_id\\\" value=\\\"{{ v.id }}\\\" required class=\\\"accent-indigo-500\\\">\\n<span>{{ v.label }}</span></span>\\n<span class=\\\"font-semibold\\\">\\u20b9{{ v.price }}</span></label>\\n{% endfor %}\\n<button type=\\\"submit\\\" class=\\\"btn-primary w-full py-3 text-white font-medium mt-3\\\">Purchase Access</button>\\n</form>\\n{% else %}<p class=\\\"text-gray-500 text-sm\\\">No durations yet</p>{% endif %}\\n</div>\\n{% endfor %}\\n{% else %}\\n<div class=\\\"card p-8 text-center text-gray-500\\\"><p>No products available \\u2014 add in admin</p></div>\\n{% endif %}\\n</div>{% endblock %}\",\"payment.html\":\"{% extends \\\"base.html\\\" %}{% block title %}Payment{% endblock %}{% block content %}\\n<div class=\\\"min-h-screen px-4 py-10 max-w-md mx-auto text-center\\\">\\n<h1 class=\\\"text-xl font-bold mb-2\\\">Complete Payment</h1>\\n<p class=\\\"text-gray-400 text-sm mb-6\\\">{{ product.name if product else '' }} \\u00b7 \\u20b9{{ order.amount }}</p>\\n{% if order.qr_url %}\\n<img src=\\\"{{ order.qr_url }}\\\" alt=\\\"UPI QR\\\" class=\\\"mx-auto rounded-2xl w-64 h-64 bg-white p-2 mb-4\\\">\\n{% endif %}\\n<p class=\\\"text-sm text-gray-500 mb-2\\\">Scan with any UPI app</p>\\n<p class=\\\"text-xs text-yellow-400\\\" id=\\\"status\\\">Waiting for payment\\u2026</p>\\n<p class=\\\"text-xs text-gray-600 mt-4\\\">Expires in {{ expiry_minutes }} min</p>\\n</div>\\n<script>\\nconst oid=\\\"{{ order.order_id }}\\\";\\nconst iv={{ poll_interval }}*1000;\\nasync function poll(){\\n  try{\\n    const r=await fetch(\\\"/api/check/\\\"+oid);\\n    const d=await r.json();\\n    if(d.status===\\\"paid\\\"){ location.href=\\\"/success/\\\"+oid; return; }\\n    document.getElementById(\\\"status\\\").textContent=d.message||\\\"Waiting\\u2026\\\";\\n  }catch(e){}\\n  setTimeout(poll,iv);\\n}\\nsetTimeout(poll,iv);\\n</script>{% endblock %}\",\"success.html\":\"{% extends \\\"base.html\\\" %}{% block title %}Success{% endblock %}{% block content %}\\n<div class=\\\"min-h-screen px-4 py-10 max-w-md mx-auto text-center\\\">\\n<div class=\\\"text-green-400 text-4xl mb-4\\\"><i class=\\\"fas fa-check-circle\\\"></i></div>\\n<h1 class=\\\"text-xl font-bold mb-2\\\">Payment Successful</h1>\\n<p class=\\\"text-gray-400 text-sm mb-6\\\">{{ product.name if product else '' }}</p>\\n<div class=\\\"card p-5 text-left\\\">\\n<p class=\\\"text-xs text-gray-500 uppercase mb-2\\\">Your Key</p>\\n<p class=\\\"font-mono text-sm break-all select-all\\\" id=\\\"key\\\">{{ order.delivered_key }}</p>\\n<button onclick=\\\"navigator.clipboard.writeText(document.getElementById('key').textContent)\\\" class=\\\"btn-primary mt-4 w-full py-2.5 text-white text-sm\\\">Copy Key</button>\\n</div>\\n<a href=\\\"/\\\" class=\\\"inline-block mt-6 text-sm text-gray-400\\\">\\u2190 Back to store</a>\\n</div>{% endblock %}\",\"admin.html\":\"{% extends \\\"base.html\\\" %}{% block title %}Admin{% endblock %}{% block content %}\\n<div class=\\\"min-h-screen px-4 py-8 max-w-3xl mx-auto\\\">\\n<div class=\\\"flex justify-between items-center mb-6\\\">\\n<div><h1 class=\\\"text-xl font-bold\\\">Admin Panel</h1>\\n<p class=\\\"text-gray-500 text-sm\\\">UPI: {{ upi_id or 'Not set' }} \\u00b7 /adm-k9x2m7</p></div>\\n<a href=\\\"/\\\" class=\\\"text-sm text-gray-400\\\">\\u2190 Store</a></div>\\n<div id=\\\"flash\\\" class=\\\"hidden mb-4 p-3 rounded-xl text-sm\\\"></div>\\n<div class=\\\"card p-5 mb-6\\\">\\n<h2 class=\\\"font-semibold mb-4 text-sm uppercase text-gray-400\\\">1. Add Product</h2>\\n<form action=\\\"/adm-k9x2m7/product/add\\\" method=\\\"post\\\" class=\\\"space-y-3\\\">\\n<input type=\\\"text\\\" name=\\\"name\\\" required placeholder=\\\"Product name\\\" class=\\\"input-dark w-full px-4 py-3 text-sm\\\">\\n<div class=\\\"flex gap-3\\\">\\n<label class=\\\"flex-1 input-dark px-4 py-3 text-sm flex items-center gap-2 cursor-pointer\\\">\\n<input type=\\\"radio\\\" name=\\\"ptype\\\" value=\\\"bulk\\\" checked onchange=\\\"togglePanelFields()\\\"><span>Bulk Keys</span></label>\\n<label class=\\\"flex-1 input-dark px-4 py-3 text-sm flex items-center gap-2 cursor-pointer\\\">\\n<input type=\\\"radio\\\" name=\\\"ptype\\\" value=\\\"panel\\\" onchange=\\\"togglePanelFields()\\\"><span>Panel Method</span></label>\\n</div>\\n<div id=\\\"panel-fields\\\" class=\\\"space-y-3 hidden\\\">\\n<input type=\\\"text\\\" name=\\\"panel_url\\\" placeholder=\\\"Panel URL (https://over-load-ochre.vercel.app)\\\" class=\\\"input-dark w-full px-4 py-3 text-sm\\\">\\n<div class=\\\"grid grid-cols-2 gap-3\\\">\\n<input type=\\\"text\\\" name=\\\"panel_user\\\" placeholder=\\\"Username\\\" class=\\\"input-dark w-full px-4 py-3 text-sm\\\">\\n<input type=\\\"text\\\" name=\\\"panel_pass\\\" placeholder=\\\"Password\\\" class=\\\"input-dark w-full px-4 py-3 text-sm\\\">\\n</div>\\n<p class=\\\"text-xs text-yellow-400\\\">Generates REAL 1h key on add from your Overload panel.</p>\\n</div>\\n<textarea name=\\\"description\\\" rows=\\\"2\\\" placeholder=\\\"Description\\\" class=\\\"input-dark w-full px-4 py-3 text-sm\\\"></textarea>\\n<button type=\\\"submit\\\" class=\\\"btn-primary px-6 py-2.5 text-white text-sm\\\">Add Product</button>\\n</form></div>\\n{% for p in products %}\\n<div class=\\\"card p-5 mb-5\\\">\\n<div class=\\\"flex justify-between items-start mb-4\\\">\\n<div><h3 class=\\\"font-semibold text-lg\\\">{{ p.name }}</h3>\\n<p class=\\\"text-xs text-gray-500\\\"><span class=\\\"{% if p.type=='panel' %}text-purple-400{% else %}text-blue-400{% endif %} uppercase\\\">{{ p.type }}</span> \\u00b7 Stock: {{ p.total_stock }}</p>\\n{% if p.type=='panel' %}<p class=\\\"text-xs text-gray-500 mt-1\\\">{{ p.panel_url or '\\u2014' }} \\u00b7 {{ p.panel_user or '\\u2014' }}</p>{% endif %}\\n</div>\\n<form action=\\\"/adm-k9x2m7/product/delete/{{ p.id }}\\\" method=\\\"post\\\" onsubmit=\\\"return confirm('Disable?')\\\">\\n<button class=\\\"text-red-400 text-sm\\\"><i class=\\\"fas fa-trash\\\"></i></button></form>\\n</div>\\n<p class=\\\"text-xs text-gray-400 uppercase mb-2\\\">Duration Slots</p>\\n{% for v in p.variants %}\\n<div class=\\\"flex justify-between items-center bg-[#1a1a24] rounded-xl px-4 py-2.5 text-sm mb-2\\\">\\n<span>{{ v.label }} \\u2014 \\u20b9{{ v.price }} <span class=\\\"text-gray-500\\\">({{ v.duration_hours }}h)</span></span>\\n<span class=\\\"flex items-center gap-3\\\">\\n<span class=\\\"{% if v.stock>0 %}text-green-400{% else %}text-red-400{% endif %} text-xs\\\">{{ v.stock }} stock</span>\\n<form action=\\\"/adm-k9x2m7/variant/delete/{{ v.id }}\\\" method=\\\"post\\\" class=\\\"inline\\\">\\n<input type=\\\"hidden\\\" name=\\\"product_id\\\" value=\\\"{{ p.id }}\\\">\\n<button class=\\\"text-red-400 text-xs\\\"><i class=\\\"fas fa-times\\\"></i></button></form>\\n</span></div>\\n{% else %}<p class=\\\"text-gray-500 text-xs mb-3\\\">No slots yet</p>{% endfor %}\\n<form action=\\\"/adm-k9x2m7/variant/add\\\" method=\\\"post\\\" class=\\\"grid grid-cols-3 gap-2 mt-2\\\">\\n<input type=\\\"hidden\\\" name=\\\"product_id\\\" value=\\\"{{ p.id }}\\\">\\n<input type=\\\"text\\\" name=\\\"label\\\" required placeholder=\\\"5 Hours\\\" class=\\\"input-dark px-3 py-2 text-sm\\\">\\n<input type=\\\"number\\\" name=\\\"duration_hours\\\" required step=\\\"0.5\\\" placeholder=\\\"Hours\\\" class=\\\"input-dark px-3 py-2 text-sm\\\">\\n<input type=\\\"number\\\" name=\\\"price\\\" required step=\\\"0.01\\\" placeholder=\\\"\\u20b9\\\" class=\\\"input-dark px-3 py-2 text-sm\\\">\\n<button type=\\\"submit\\\" class=\\\"col-span-3 btn-primary py-2 text-white text-sm\\\">+ Add Duration</button>\\n</form>\\n{% if p.type=='bulk' and p.variants %}\\n<div class=\\\"border-t border-[#252532] pt-4 mt-4\\\">\\n<p class=\\\"text-xs text-gray-400 uppercase mb-2\\\">Add Bulk Keys</p>\\n<form action=\\\"/adm-k9x2m7/keys/add\\\" method=\\\"post\\\" class=\\\"space-y-2\\\">\\n<input type=\\\"hidden\\\" name=\\\"product_id\\\" value=\\\"{{ p.id }}\\\">\\n<select name=\\\"variant_id\\\" required class=\\\"input-dark w-full px-3 py-2 text-sm\\\">\\n<option value=\\\"\\\">Select slot\\u2026</option>\\n{% for v in p.variants %}<option value=\\\"{{ v.id }}\\\">{{ v.label }} (\\u20b9{{ v.price }})</option>{% endfor %}\\n</select>\\n<textarea name=\\\"keys_text\\\" required rows=\\\"3\\\" placeholder=\\\"One key per line\\\" class=\\\"input-dark w-full px-3 py-2 text-sm font-mono\\\"></textarea>\\n<button type=\\\"submit\\\" class=\\\"btn-primary px-5 py-2 text-white text-sm\\\">Add Keys</button>\\n</form></div>\\n{% endif %}\\n<div class=\\\"border-t border-[#252532] pt-4 mt-4 flex flex-wrap gap-2\\\">\\n<form action=\\\"/adm-k9x2m7/keys/test\\\" method=\\\"post\\\" class=\\\"inline\\\">\\n<input type=\\\"hidden\\\" name=\\\"product_id\\\" value=\\\"{{ p.id }}\\\"><input type=\\\"hidden\\\" name=\\\"hours\\\" value=\\\"1\\\">\\n<button type=\\\"submit\\\" class=\\\"input-dark px-4 py-2 text-sm text-yellow-400\\\">Generate 1h Test Key</button>\\n</form>\\n<a href=\\\"/adm-k9x2m7/keys/{{ p.id }}\\\" class=\\\"input-dark px-4 py-2 text-sm text-gray-300\\\">View Keys</a>\\n</div></div>\\n{% else %}\\n<div class=\\\"card p-8 text-center text-gray-500 mb-6\\\">No products yet</div>\\n{% endfor %}\\n<div class=\\\"card p-5\\\">\\n<h2 class=\\\"font-semibold mb-4 text-sm uppercase text-gray-400\\\">Recent Orders</h2>\\n{% for o in orders %}\\n<div class=\\\"flex justify-between items-center bg-[#1a1a24] rounded-lg px-3 py-2.5 text-sm mb-2\\\">\\n<div><p class=\\\"font-medium\\\">{{ o.product_name or '\\u2014' }} \\u00b7 \\u20b9{{ o.amount }}</p>\\n<p class=\\\"text-xs text-gray-500\\\">{{ o.order_id[-12:] }}{% if o.delivered_key %} \\u00b7 {{ o.delivered_key[:24] }}\\u2026{% endif %}</p></div>\\n<span class=\\\"text-xs {% if o.status=='paid' %}text-green-400{% else %}text-yellow-400{% endif %}\\\">{{ o.status }}</span>\\n</div>\\n{% else %}<p class=\\\"text-gray-500 text-sm\\\">No orders yet</p>{% endfor %}\\n</div></div>\\n<script>\\nfunction togglePanelFields(){\\n  const isPanel=document.querySelector('input[name=\\\"ptype\\\"]:checked').value==='panel';\\n  document.getElementById('panel-fields').classList.toggle('hidden',!isPanel);\\n}\\nconst p=new URLSearchParams(location.search);\\nconst f=document.getElementById('flash');\\nif(p.get('error')){f.className='mb-4 p-3 rounded-xl text-sm bg-red-900/40 text-red-300 border border-red-800';f.textContent=p.get('error');f.classList.remove('hidden');}\\nif(p.get('testkey')){f.className='mb-4 p-3 rounded-xl text-sm bg-green-900/40 text-green-300 border border-green-800';f.innerHTML='<b>REAL key:</b> <code class=\\\"break-all\\\">'+p.get('testkey')+'</code>';f.classList.remove('hidden');}\\nif(p.get('added')){f.className='mb-4 p-3 rounded-xl text-sm bg-blue-900/40 text-blue-300';f.textContent=p.get('added')+' keys added';f.classList.remove('hidden');}\\n</script>{% endblock %}\",\"admin_keys.html\":\"{% extends \\\"base.html\\\" %}{% block title %}Keys{% endblock %}{% block content %}\\n<div class=\\\"min-h-screen px-4 py-8 max-w-3xl mx-auto\\\">\\n<div class=\\\"flex justify-between mb-6\\\"><h1 class=\\\"text-xl font-bold\\\">Keys \\u2014 {{ product.name if product else '' }}</h1>\\n<a href=\\\"/adm-k9x2m7\\\" class=\\\"text-sm text-gray-400\\\">\\u2190 Admin</a></div>\\n{% for k in keys %}\\n<div class=\\\"card px-4 py-3 mb-2 flex justify-between text-sm\\\">\\n<code class=\\\"font-mono break-all\\\">{{ k.key_value }}</code>\\n<span class=\\\"text-xs {% if k.status=='used' %}text-red-400{% else %}text-green-400{% endif %}\\\">{% if k.status=='used' %}USED{% else %}{{ k.status|upper }}{% endif %}</span>\\n</div>\\n{% else %}<p class=\\\"text-gray-500\\\">No keys</p>{% endfor %}\\n</div>{% endblock %}\"}")
_tpl_dir=Path("/tmp/ks_templates");_tpl_dir.mkdir(parents=True,exist_ok=True)
for _n,_c in _TEMPLATES.items():(_tpl_dir/_n).write_text(_c,encoding="utf-8")


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


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'bulk',  -- 'panel' or 'bulk'
                description TEXT,
                panel_url TEXT,
                panel_user TEXT,
                panel_pass TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS variants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                label TEXT NOT NULL,           -- e.g. "5 Hours", "1 Day", "7 Days"
                duration_hours REAL NOT NULL, -- 5, 24, 168, etc.
                price REAL NOT NULL,
                is_active INTEGER DEFAULT 1,
                FOREIGN KEY (product_id) REFERENCES products(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                variant_id INTEGER,
                key_value TEXT NOT NULL,
                status TEXT DEFAULT 'available',  -- available | used | test
                used_at TEXT,
                used_order_id TEXT,
                expires_at TEXT,                  -- for test keys
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (product_id) REFERENCES products(id),
                FOREIGN KEY (variant_id) REFERENCES variants(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT UNIQUE NOT NULL,
                product_id INTEGER NOT NULL,
                variant_id INTEGER,
                amount REAL NOT NULL,
                status TEXT DEFAULT 'pending',
                qr_url TEXT,
                delivered_key TEXT,
                delivered_type TEXT,              -- 'key' or 'panel'
                transaction_id TEXT,
                utr TEXT,
                sender_name TEXT,
                payment_time TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                paid_at TEXT,
                FOREIGN KEY (product_id) REFERENCES products(id)
            )
        """)
        await db.commit()


def _gen_key(prefix="TEST"):
    chars = string.ascii_uppercase + string.digits
    return f"{prefix}-{''.join(secrets.choice(chars) for _ in range(4))}-{''.join(secrets.choice(chars) for _ in range(4))}-{''.join(secrets.choice(chars) for _ in range(4))}"


async def add_product(name: str, ptype: str, description: str = "",
                      panel_url: str = "", panel_user: str = "", panel_pass: str = "") -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO products (name, type, description, panel_url, panel_user, panel_pass) VALUES (?,?,?,?,?,?)",
            (name, ptype, description, panel_url, panel_user, panel_pass)
        )
        await db.commit()
        return cur.lastrowid

async def get_products(active_only=True) -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        q = "SELECT * FROM products WHERE is_active=1 ORDER BY id DESC" if active_only else "SELECT * FROM products ORDER BY id DESC"
        cur = await db.execute(q)
        return [dict(r) for r in await cur.fetchall()]

async def get_product(pid: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM products WHERE id=?", (pid,))
        row = await cur.fetchone()
        return dict(row) if row else None

async def delete_product(pid: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE products SET is_active=0 WHERE id=?", (pid,))
        await db.commit()


async def add_variant(product_id: int, label: str, duration_hours: float, price: float) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO variants (product_id, label, duration_hours, price) VALUES (?,?,?,?)",
            (product_id, label, duration_hours, price)
        )
        await db.commit()
        return cur.lastrowid

async def get_variants(product_id: int) -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM variants WHERE product_id=? AND is_active=1 ORDER BY duration_hours",
            (product_id,)
        )
        return [dict(r) for r in await cur.fetchall()]

async def get_variant(vid: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM variants WHERE id=?", (vid,))
        row = await cur.fetchone()
        return dict(row) if row else None

async def delete_variant(vid: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE variants SET is_active=0 WHERE id=?", (vid,))
        await db.commit()


async def add_keys_bulk(product_id: int, variant_id: int, keys_text: str) -> int:
    """Add multiple keys (one per line). Returns count added."""
    lines = [k.strip() for k in keys_text.strip().splitlines() if k.strip()]
    async with aiosqlite.connect(DB_PATH) as db:
        for k in lines:
            await db.execute(
                "INSERT INTO keys (product_id, variant_id, key_value, status) VALUES (?,?,?,'available')",
                (product_id, variant_id, k)
            )
        await db.commit()
    return len(lines)

async def generate_test_key(product_id: int, hours: float = 1.0) -> str:
    key = _gen_key("TEST")
    expires = (datetime.now() + timedelta(hours=hours)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO keys (product_id, key_value, status, expires_at) VALUES (?,?,?,?)",
            (product_id, key, "test", expires)
        )
        await db.commit()
    return key

async def get_stock(product_id: int, variant_id: int = None) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        if variant_id:
            cur = await db.execute(
                "SELECT COUNT(*) FROM keys WHERE product_id=? AND variant_id=? AND status='available'",
                (product_id, variant_id)
            )
        else:
            cur = await db.execute(
                "SELECT COUNT(*) FROM keys WHERE product_id=? AND status='available'",
                (product_id,)
            )
        row = await cur.fetchone()
        return row[0] if row else 0

async def get_keys(product_id: int, status: str = None) -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if status:
            cur = await db.execute(
                "SELECT * FROM keys WHERE product_id=? AND status=? ORDER BY id DESC LIMIT 100",
                (product_id, status)
            )
        else:
            cur = await db.execute(
                "SELECT * FROM keys WHERE product_id=? ORDER BY id DESC LIMIT 100",
                (product_id,)
            )
        return [dict(r) for r in await cur.fetchall()]

async def claim_next_key(product_id: int, variant_id: int, order_id: str) -> Optional[str]:
    """Atomically claim the next available key for this variant."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, key_value FROM keys WHERE product_id=? AND variant_id=? AND status='available' ORDER BY id LIMIT 1",
            (product_id, variant_id)
        )
        row = await cur.fetchone()
        if not row:
            return None
        await db.execute(
            "UPDATE keys SET status='used', used_at=?, used_order_id=? WHERE id=?",
            (datetime.now().isoformat(), order_id, row["id"])
        )
        await db.commit()
        return row["key_value"]


async def create_order(order_id: str, product_id: int, variant_id: int, amount: float, qr_url: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO orders (order_id, product_id, variant_id, amount, qr_url, status) VALUES (?,?,?,?,?,'pending')",
            (order_id, product_id, variant_id, amount, qr_url)
        )
        await db.commit()
        return cur.lastrowid

async def get_order_by_api_id(order_id: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM orders WHERE order_id=?", (order_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

async def mark_order_paid(order_id: str, transaction_id: str, utr: str, sender_name: str,
                          payment_time: str, delivered_key: str = "", delivered_type: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE orders SET status='paid', transaction_id=?, utr=?, sender_name=?,
               payment_time=?, paid_at=?, delivered_key=?, delivered_type=? WHERE order_id=?""",
            (transaction_id, utr, sender_name, payment_time,
             datetime.now().isoformat(), delivered_key, delivered_type, order_id)
        )
        await db.commit()

async def get_orders(limit: int = 50) -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT o.*, p.name as product_name
            FROM orders o LEFT JOIN products p ON o.product_id = p.id
            ORDER BY o.id DESC LIMIT ?
        """, (limit,))
        return [dict(r) for r in await cur.fetchall()]
app=FastAPI(title="Key Store")
templates=Jinja2Templates(directory=str(_tpl_dir))
security=HTTPBasic()
handler=Mangum(app)
def verify_admin(credentials:HTTPBasicCredentials=Depends(security)):
    if not(credentials.username=="admin" and secrets.compare_digest(credentials.password,ADMIN_PASSWORD)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="Incorrect",headers={"WWW-Authenticate":"Basic"})
    return credentials.username
@app.middleware("http")
async def ensure_db(request:Request,call_next):
    await init_db();return await call_next(request)
def _redir_admin(pid=None,**qs):
    q=[]
    if pid:q.append(f"pid={pid}")
    for k,v in qs.items():
        if v is not None:q.append(f"{k}={quote(str(v),safe='')}")
    return RedirectResponse("/adm-k9x2m7"+(("?"+"&".join(q)) if q else ""),status_code=303)
@app.get("/",response_class=HTMLResponse)
async def home(request:Request):
    products=await get_products(active_only=True)
    for p in products:
        p["variants"]=await get_variants(p["id"])
        for v in p["variants"]:v["stock"]=await get_stock(p["id"],v["id"])
    return templates.TemplateResponse(request,"index.html",{"products":products})
@app.post("/buy")
async def buy(request:Request,product_id:int=Form(...),variant_id:int=Form(...)):
    product=await get_product(product_id);variant=await get_variant(variant_id)
    if not product or not product["is_active"] or not variant:raise HTTPException(404,"Not found")
    if product["type"]=="bulk" and (await get_stock(product_id,variant_id))<=0:raise HTTPException(400,"Out of stock")
    if not UPI_ID or not API_KEY:raise HTTPException(400,"Payment not configured")
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:data=(await client.get(QR_API,params={"upi":UPI_ID,"amount":variant["price"]})).json()
        except Exception as e:raise HTTPException(502,f"QR:{e}")
    if data.get("status")!="success":raise HTTPException(502,str(data.get("message","err")))
    qr=data["data"]
    await create_order(qr["order_id"],product_id,variant_id,float(qr.get("amount",variant["price"])),qr["qr_url"])
    return RedirectResponse(f"/pay/{qr['order_id']}",status_code=303)
@app.get("/pay/{order_id}",response_class=HTMLResponse)
async def pay_page(request:Request,order_id:str):
    order=await get_order_by_api_id(order_id)
    if not order:raise HTTPException(404,"Not found")
    product=await get_product(order["product_id"])
    variant=await get_variant(order["variant_id"]) if order.get("variant_id") else None
    if order["status"]=="paid":return templates.TemplateResponse(request,"success.html",{"order":order,"product":product,"variant":variant})
    return templates.TemplateResponse(request,"payment.html",{"order":order,"product":product,"variant":variant,"expiry_minutes":QR_EXPIRY_MINUTES,"poll_interval":POLL_INTERVAL_SECONDS})
async def _deliver_for_paid_order(order,product,variant):
    if product["type"]=="bulk":
        key=await claim_next_key(order["product_id"],order["variant_id"],order["order_id"])
        return (key or "NO KEY – contact admin"),"key"
    hours=float(variant["duration_hours"]) if variant and variant.get("duration_hours") else 1.0
    return (await generate_key(product.get("panel_url") or "",product.get("panel_user") or "",product.get("panel_pass") or "",hours=hours)),"key"
@app.get("/api/check/{order_id}")
async def check_payment(order_id:str):
    order=await get_order_by_api_id(order_id)
    if not order:return JSONResponse({"status":"error","message":"not found"})
    if order["status"]=="paid":return {"status":"paid","delivered_key":order.get("delivered_key"),"delivered_type":order.get("delivered_type")}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:data=(await client.get(VERIFY_API,params={"order_id":order_id,"api_key":API_KEY})).json()
        except Exception as e:return {"status":"pending","message":str(e)}
    if data.get("status")!="success":return {"status":"pending","message":data.get("message","wait")}
    d=data["data"];product=await get_product(order["product_id"])
    variant=await get_variant(order["variant_id"]) if order.get("variant_id") else None
    try:delivered_key,delivered_type=await _deliver_for_paid_order(order,product,variant)
    except PanelError as e:delivered_key,delivered_type=f"PAID but key failed: {e}","error"
    await mark_order_paid(order_id=order_id,transaction_id=d.get("transaction_id",""),utr=d.get("utr",""),sender_name=d.get("sender_name",""),payment_time=d.get("payment_time_ist",""),delivered_key=delivered_key,delivered_type=delivered_type)
    return {"status":"paid","delivered_key":delivered_key,"delivered_type":delivered_type}
@app.get("/success/{order_id}",response_class=HTMLResponse)
async def success(request:Request,order_id:str):
    order=await get_order_by_api_id(order_id)
    if not order or order["status"]!="paid":raise HTTPException(404,"Not found")
    product=await get_product(order["product_id"])
    variant=await get_variant(order["variant_id"]) if order.get("variant_id") else None
    return templates.TemplateResponse(request,"success.html",{"order":order,"product":product,"variant":variant})
@app.get("/adm-k9x2m7",response_class=HTMLResponse)
async def admin_home(request:Request,user:str=Depends(verify_admin)):
    products=await get_products(active_only=False)
    for p in products:
        p["variants"]=await get_variants(p["id"])
        for v in p["variants"]:v["stock"]=await get_stock(p["id"],v["id"])
        p["total_stock"]=await get_stock(p["id"])
    return templates.TemplateResponse(request,"admin.html",{"products":products,"orders":await get_orders(40),"upi_id":UPI_ID})
@app.post("/adm-k9x2m7/product/add")
async def admin_add_product(name:str=Form(...),ptype:str=Form(...),description:str=Form(""),panel_url:str=Form(""),panel_user:str=Form(""),panel_pass:str=Form(""),user:str=Depends(verify_admin)):
    if ptype not in ("panel","bulk"):raise HTTPException(400,"bad type")
    testkey=None
    if ptype=="panel":
        try:testkey=await test_connection(panel_url,panel_user,panel_pass)
        except PanelError as e:return _redir_admin(error=str(e))
    pid=await add_product(name,ptype,description,panel_url,panel_user,panel_pass)
    if testkey:
        await add_keys_bulk(pid,0,testkey);return _redir_admin(pid,testkey=testkey)
    return _redir_admin(pid)
@app.post("/adm-k9x2m7/variant/add")
async def admin_add_variant(product_id:int=Form(...),label:str=Form(...),duration_hours:float=Form(...),price:float=Form(...),user:str=Depends(verify_admin)):
    await add_variant(product_id,label,duration_hours,price);return _redir_admin(product_id)
@app.post("/adm-k9x2m7/keys/add")
async def admin_add_keys(product_id:int=Form(...),variant_id:int=Form(...),keys_text:str=Form(...),user:str=Depends(verify_admin)):
    return _redir_admin(product_id,added=await add_keys_bulk(product_id,variant_id,keys_text))
@app.post("/adm-k9x2m7/keys/test")
async def admin_gen_test(product_id:int=Form(...),hours:float=Form(1.0),user:str=Depends(verify_admin)):
    product=await get_product(product_id)
    if not product:return _redir_admin(error="not found")
    if product.get("type")!="panel":return _redir_admin(product_id,error="panel only")
    try:key=await generate_key(product.get("panel_url") or "",product.get("panel_user") or "",product.get("panel_pass") or "",hours=hours)
    except PanelError as e:return _redir_admin(product_id,error=str(e))
    await add_keys_bulk(product_id,0,key);return _redir_admin(product_id,testkey=key)
@app.post("/adm-k9x2m7/product/delete/{pid}")
async def admin_del_product(pid:int,user:str=Depends(verify_admin)):
    await delete_product(pid);return _redir_admin()
@app.post("/adm-k9x2m7/variant/delete/{vid}")
async def admin_del_variant(vid:int,product_id:int=Form(...),user:str=Depends(verify_admin)):
    await delete_variant(vid);return _redir_admin(product_id)
@app.get("/adm-k9x2m7/keys/{pid}",response_class=HTMLResponse)
async def admin_view_keys(request:Request,pid:int,user:str=Depends(verify_admin)):
    return templates.TemplateResponse(request,"admin_keys.html",{"product":await get_product(pid),"keys":await get_keys(pid),"variants":await get_variants(pid)})
@app.get("/health")
async def health():return {"status":"ok","time":datetime.now().isoformat()}
