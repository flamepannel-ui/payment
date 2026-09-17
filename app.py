"""UPI Key Store — fixed panel session + auto poll + unlimited panel."""
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
_TEMPLATES=json.loads("{\"admin.html\":\"{% extends \\\"base.html\\\" %}\\n{% block title %}Admin Panel{% endblock %}\\n{% block content %}\\n<div class=\\\"min-h-screen px-4 py-8 max-w-3xl mx-auto\\\">\\n    <div class=\\\"flex justify-between items-center mb-6\\\">\\n        <div>\\n            <h1 class=\\\"text-xl font-bold\\\">Admin \\u00b7 Control</h1>\\n            <p class=\\\"text-gray-500 text-sm\\\">UPI: <span class=\\\"text-gray-300\\\">{{ upi_id or 'Not set' }}</span></p>\\n        </div>\\n        <a href=\\\"/\\\" class=\\\"text-sm text-gray-400 hover:text-white\\\">\\u2190 Store</a>\\n    </div>\\n\\n    <div id=\\\"flash\\\" class=\\\"hidden mb-4 rounded-xl px-4 py-3 text-sm font-mono break-all\\\"></div>\\n\\n    <!-- ADD PRODUCT -->\\n    <div class=\\\"card p-5 mb-6\\\">\\n        <h2 class=\\\"font-semibold mb-4 text-sm uppercase tracking-wider text-gray-400\\\">1. Add Product</h2>\\n        <form action=\\\"/adm-k9x2m7/product/add\\\" method=\\\"post\\\" class=\\\"space-y-3\\\" id=\\\"add-product-form\\\">\\n            <input type=\\\"text\\\" name=\\\"name\\\" required placeholder=\\\"Product name (e.g. JALBA BULLET TRACK)\\\" class=\\\"input-dark w-full px-4 py-3 text-sm\\\">\\n            \\n            <div class=\\\"flex gap-3\\\">\\n                <label class=\\\"flex-1 input-dark px-4 py-3 text-sm flex items-center gap-2 cursor-pointer\\\">\\n                    <input type=\\\"radio\\\" name=\\\"ptype\\\" value=\\\"bulk\\\" checked onchange=\\\"togglePanelFields()\\\">\\n                    <span>Bulk Keys</span>\\n                </label>\\n                <label class=\\\"flex-1 input-dark px-4 py-3 text-sm flex items-center gap-2 cursor-pointer\\\">\\n                    <input type=\\\"radio\\\" name=\\\"ptype\\\" value=\\\"panel\\\" onchange=\\\"togglePanelFields()\\\">\\n                    <span>Panel Method</span>\\n                </label>\\n            </div>\\n\\n            <div id=\\\"panel-fields\\\" class=\\\"space-y-3 hidden\\\">\\n                <input type=\\\"text\\\" name=\\\"panel_url\\\" placeholder=\\\"Panel URL (https://...)\\\" class=\\\"input-dark w-full px-4 py-3 text-sm\\\">\\n                <div class=\\\"grid grid-cols-2 gap-3\\\">\\n                    <input type=\\\"text\\\" name=\\\"panel_user\\\" placeholder=\\\"Login Username\\\" class=\\\"input-dark w-full px-4 py-3 text-sm\\\">\\n                    <input type=\\\"text\\\" name=\\\"panel_pass\\\" placeholder=\\\"Login Password\\\" class=\\\"input-dark w-full px-4 py-3 text-sm\\\">\\n                </div>\\n                <p class=\\\"text-xs text-yellow-400\\\">On add: logs into panel, generates a REAL 1h test key, and auto-creates default plans (5h\\u201330d). Stock is unlimited. After payment, generates the duration the buyer selected.</p>\\n            </div>\\n\\n            <textarea name=\\\"description\\\" rows=\\\"2\\\" placeholder=\\\"Description (optional)\\\" class=\\\"input-dark w-full px-4 py-3 text-sm\\\"></textarea>\\n            <button type=\\\"submit\\\" class=\\\"btn-primary px-6 py-2.5 text-white text-sm font-medium\\\">Add Product</button>\\n        </form>\\n    </div>\\n\\n    <!-- PRODUCTS LIST -->\\n    {% for p in products %}\\n    <div class=\\\"card p-5 mb-5\\\">\\n        <div class=\\\"flex justify-between items-start mb-4\\\">\\n            <div>\\n                <h3 class=\\\"font-semibold text-lg\\\">{{ p.name }}</h3>\\n                <p class=\\\"text-xs text-gray-500 mt-0.5\\\">\\n                    <span class=\\\"uppercase {% if p.type=='panel' %}text-purple-400{% else %}text-blue-400{% endif %}\\\">{{ p.type }}</span>\\n                    \\u00b7 {% if p.type == 'panel' %}<span class=\\\"text-green-400\\\">Unlimited</span>{% else %}Stock: {{ p.total_stock }}{% endif %}\\n                    {% if not p.is_active %}<span class=\\\"text-red-400\\\">\\u00b7 DISABLED</span>{% endif %}\\n                </p>\\n                {% if p.type == 'panel' %}\\n                <p class=\\\"text-xs text-gray-500 mt-1\\\">URL: {{ p.panel_url or '\\u2014' }} \\u00b7 User: {{ p.panel_user or '\\u2014' }}</p>\\n                {% endif %}\\n            </div>\\n            <form action=\\\"/adm-k9x2m7/product/delete/{{ p.id }}\\\" method=\\\"post\\\" onsubmit=\\\"return confirm('Disable this product?')\\\">\\n                <button class=\\\"text-red-400 text-sm\\\"><i class=\\\"fas fa-trash\\\"></i></button>\\n            </form>\\n        </div>\\n\\n        <!-- Variants -->\\n        <div class=\\\"mb-4\\\">\\n            <p class=\\\"text-xs text-gray-400 uppercase tracking-wider mb-2\\\">Duration Slots</p>\\n            {% if p.variants %}\\n            <div class=\\\"space-y-2 mb-3\\\">\\n                {% for v in p.variants %}\\n                <div class=\\\"flex justify-between items-center bg-[#1a1a24] rounded-xl px-4 py-2.5 text-sm\\\">\\n                    <span>{{ v.label }} \\u2014 \\u20b9{{ v.price }} <span class=\\\"text-gray-500\\\">({{ v.duration_hours }}h)</span></span>\\n                    <span class=\\\"flex items-center gap-3\\\">\\n                        <span class=\\\"{% if p.type == 'panel' %}text-green-400{% elif v.stock > 0 %}text-green-400{% else %}text-red-400{% endif %} text-xs\\\">{% if p.type == 'panel' %}Unlimited{% else %}{{ v.stock }} stock{% endif %}</span>\\n                        <form action=\\\"/adm-k9x2m7/variant/delete/{{ v.id }}\\\" method=\\\"post\\\" class=\\\"inline\\\">\\n                            <input type=\\\"hidden\\\" name=\\\"product_id\\\" value=\\\"{{ p.id }}\\\">\\n                            <button class=\\\"text-red-400 text-xs\\\"><i class=\\\"fas fa-times\\\"></i></button>\\n                        </form>\\n                    </span>\\n                </div>\\n                {% endfor %}\\n            </div>\\n            {% else %}\\n            <p class=\\\"text-gray-500 text-xs mb-3\\\">No duration slots yet \\u2014 add one below</p>\\n            {% endif %}\\n\\n            <!-- Add variant -->\\n            <form action=\\\"/adm-k9x2m7/variant/add\\\" method=\\\"post\\\" class=\\\"grid grid-cols-3 gap-2\\\">\\n                <input type=\\\"hidden\\\" name=\\\"product_id\\\" value=\\\"{{ p.id }}\\\">\\n                <input type=\\\"text\\\" name=\\\"label\\\" required placeholder=\\\"5 Hours\\\" class=\\\"input-dark px-3 py-2 text-sm\\\">\\n                <input type=\\\"number\\\" name=\\\"duration_hours\\\" required step=\\\"0.5\\\" placeholder=\\\"Hours (5)\\\" class=\\\"input-dark px-3 py-2 text-sm\\\">\\n                <input type=\\\"number\\\" name=\\\"price\\\" required step=\\\"0.01\\\" placeholder=\\\"Price \\u20b9\\\" class=\\\"input-dark px-3 py-2 text-sm\\\">\\n                <button type=\\\"submit\\\" class=\\\"col-span-3 btn-primary py-2 text-white text-sm\\\">+ Add Duration Slot</button>\\n            </form>\\n        </div>\\n\\n        <!-- Bulk keys upload (only for bulk type) -->\\n        {% if p.type == 'bulk' and p.variants %}\\n        <div class=\\\"border-t border-[#252532] pt-4 mt-2\\\">\\n            <p class=\\\"text-xs text-gray-400 uppercase tracking-wider mb-2\\\">Add Bulk Keys</p>\\n            <form action=\\\"/adm-k9x2m7/keys/add\\\" method=\\\"post\\\" class=\\\"space-y-2\\\">\\n                <input type=\\\"hidden\\\" name=\\\"product_id\\\" value=\\\"{{ p.id }}\\\">\\n                <select name=\\\"variant_id\\\" required class=\\\"input-dark w-full px-3 py-2 text-sm\\\">\\n                    <option value=\\\"\\\">Select duration slot\\u2026</option>\\n                    {% for v in p.variants %}\\n                    <option value=\\\"{{ v.id }}\\\">{{ v.label }} (\\u20b9{{ v.price }}) \\u2014 {{ v.stock }} in stock</option>\\n                    {% endfor %}\\n                </select>\\n                <textarea name=\\\"keys_text\\\" required rows=\\\"4\\\" placeholder=\\\"Paste keys here, one per line&#10;KEY-AAAA-BBBB&#10;KEY-CCCC-DDDD&#10;KEY-EEEE-FFFF\\\" class=\\\"input-dark w-full px-3 py-2 text-sm font-mono\\\"></textarea>\\n                <button type=\\\"submit\\\" class=\\\"btn-primary px-5 py-2 text-white text-sm\\\">Add Keys to Stock</button>\\n            </form>\\n        </div>\\n        {% endif %}\\n\\n        <!-- Test key + view keys -->\\n        <div class=\\\"border-t border-[#252532] pt-4 mt-4 flex flex-wrap gap-2\\\">\\n            <form action=\\\"/adm-k9x2m7/keys/test\\\" method=\\\"post\\\" class=\\\"inline\\\">\\n                <input type=\\\"hidden\\\" name=\\\"product_id\\\" value=\\\"{{ p.id }}\\\">\\n                <input type=\\\"hidden\\\" name=\\\"hours\\\" value=\\\"1\\\">\\n                <button type=\\\"submit\\\" class=\\\"input-dark px-4 py-2 text-sm text-yellow-400 hover:text-yellow-300\\\">\\n                    <i class=\\\"fas fa-flask\\\"></i> Generate 1h Test Key\\n                </button>\\n            </form>\\n            <a href=\\\"/adm-k9x2m7/keys/{{ p.id }}\\\" class=\\\"input-dark px-4 py-2 text-sm text-gray-300 hover:text-white inline-block\\\">\\n                <i class=\\\"fas fa-list\\\"></i> View All Keys\\n            </a>\\n        </div>\\n    </div>\\n    {% else %}\\n    <div class=\\\"card p-8 text-center text-gray-500 mb-6\\\">\\n        <p>No products yet. Add one above.</p>\\n    </div>\\n    {% endfor %}\\n\\n    <!-- Orders -->\\n    <div class=\\\"card p-5\\\">\\n        <h2 class=\\\"font-semibold mb-4 text-sm uppercase tracking-wider text-gray-400\\\">Recent Orders</h2>\\n        {% if orders %}\\n        <div class=\\\"space-y-2 text-sm\\\">\\n            {% for o in orders %}\\n            <div class=\\\"flex justify-between items-center bg-[#1a1a24] rounded-lg px-3 py-2.5\\\">\\n                <div>\\n                    <p class=\\\"font-medium\\\">{{ o.product_name or '\\u2014' }} \\u00b7 \\u20b9{{ o.amount }}</p>\\n                    <p class=\\\"text-xs text-gray-500\\\">{{ o.order_id[-12:] }}{% if o.delivered_key %} \\u00b7 {{ o.delivered_key[:20] }}\\u2026{% endif %}</p>\\n                </div>\\n                <span class=\\\"text-xs {% if o.status=='paid' %}text-green-400{% else %}text-yellow-400{% endif %}\\\">{{ o.status }}</span>\\n            </div>\\n            {% endfor %}\\n        </div>\\n        {% else %}\\n        <p class=\\\"text-gray-500 text-sm\\\">No orders yet</p>\\n        {% endif %}\\n    </div>\\n</div>\\n{% endblock %}\\n\\n{% block scripts %}\\n<script>\\nfunction togglePanelFields() {\\n    const isPanel = document.querySelector('input[name=\\\"ptype\\\"]:checked').value === 'panel';\\n    document.getElementById('panel-fields').classList.toggle('hidden', !isPanel);\\n}\\nconst params = new URLSearchParams(location.search);\\nconst flash = document.getElementById('flash');\\nfunction showFlash(msg, ok) {\\n    flash.textContent = msg;\\n    flash.className = 'mb-4 rounded-xl px-4 py-3 text-sm font-mono break-all ' +\\n        (ok ? 'bg-green-900/40 text-green-300 border border-green-700/50' : 'bg-red-900/40 text-red-300 border border-red-700/50');\\n    flash.classList.remove('hidden');\\n}\\nif (params.get('error')) showFlash('Error: ' + params.get('error'), false);\\nif (params.get('testkey')) showFlash('REAL key from your panel: ' + params.get('testkey'), true);\\nif (params.get('added')) showFlash(params.get('added') + ' keys added to stock', true);\\n</script>\\n{% endblock %}\\n\",\"admin_keys.html\":\"{% extends \\\"base.html\\\" %}\\n{% block title %}Keys - {{ product.name }}{% endblock %}\\n{% block content %}\\n<div class=\\\"min-h-screen px-4 py-8 max-w-3xl mx-auto\\\">\\n    <div class=\\\"flex justify-between items-center mb-6\\\">\\n        <div>\\n            <h1 class=\\\"text-xl font-bold\\\">{{ product.name }}</h1>\\n            <p class=\\\"text-gray-500 text-sm\\\">All keys</p>\\n        </div>\\n        <a href=\\\"/adm-k9x2m7\\\" class=\\\"text-sm text-gray-400 hover:text-white\\\">\\u2190 Admin</a>\\n    </div>\\n\\n    <div class=\\\"card p-5\\\">\\n        {% if keys %}\\n        <div class=\\\"space-y-2 text-sm\\\">\\n            {% for k in keys %}\\n            <div class=\\\"flex justify-between items-center bg-[#1a1a24] rounded-lg px-3 py-2.5\\\">\\n                <div class=\\\"font-mono text-xs break-all mr-3\\\">{{ k.key_value }}</div>\\n                <div class=\\\"text-right shrink-0\\\">\\n                    <span class=\\\"text-xs {% if k.status=='available' %}text-green-400{% elif k.status=='used' %}text-gray-500{% else %}text-yellow-400{% endif %}\\\">\\n                        {{ k.status }}\\n                    </span>\\n                    {% if k.used_at %}<p class=\\\"text-xs text-gray-600\\\">{{ k.used_at[:16] }}</p>{% endif %}\\n                    {% if k.expires_at %}<p class=\\\"text-xs text-gray-600\\\">exp {{ k.expires_at[:16] }}</p>{% endif %}\\n                </div>\\n            </div>\\n            {% endfor %}\\n        </div>\\n        {% else %}\\n        <p class=\\\"text-gray-500 text-sm text-center py-6\\\">No keys yet</p>\\n        {% endif %}\\n    </div>\\n</div>\\n{% endblock %}\\n\",\"base.html\":\"<!DOCTYPE html>\\n<html lang=\\\"en\\\">\\n<head>\\n    <meta charset=\\\"UTF-8\\\">\\n    <meta name=\\\"viewport\\\" content=\\\"width=device-width, initial-scale=1.0, maximum-scale=1.0\\\">\\n    <title>{% block title %}Key Store{% endblock %}</title>\\n    <script src=\\\"https://cdn.tailwindcss.com\\\"></script>\\n    <link rel=\\\"stylesheet\\\" href=\\\"https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css\\\">\\n    <style>\\n        body {\\n            background: #0a0a0f;\\n            min-height: 100vh;\\n            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;\\n        }\\n        .card {\\n            background: #14141c;\\n            border: 1px solid #252532;\\n            border-radius: 20px;\\n        }\\n        .input-dark {\\n            background: #1a1a24;\\n            border: 1px solid #2e2e3a;\\n            border-radius: 14px;\\n            color: #fff;\\n        }\\n        .input-dark:focus {\\n            outline: none;\\n            border-color: #7c5cfc;\\n            box-shadow: 0 0 0 2px rgba(124, 92, 252, 0.25);\\n        }\\n        .btn-primary {\\n            background: linear-gradient(135deg, #7c5cfc, #5b3fd6);\\n            border-radius: 14px;\\n            transition: all 0.2s;\\n        }\\n        .btn-primary:hover { filter: brightness(1.1); transform: translateY(-1px); }\\n        .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }\\n        .option-item { transition: background 0.15s; }\\n        .option-item:hover, .option-item.selected { background: #1f1f2c; }\\n        .stock-badge { color: #22c55e; font-size: 11px; font-weight: 600; }\\n        .dropdown-menu {\\n            background: #1a1a24;\\n            border: 1px solid #2e2e3a;\\n            border-radius: 14px;\\n            max-height: 280px;\\n            overflow-y: auto;\\n        }\\n        ::-webkit-scrollbar { width: 6px; }\\n        ::-webkit-scrollbar-track { background: transparent; }\\n        ::-webkit-scrollbar-thumb { background: #333; border-radius: 3px; }\\n    </style>\\n    {% block head %}{% endblock %}\\n</head>\\n<body class=\\\"text-white\\\">\\n    {% block content %}{% endblock %}\\n    {% block scripts %}{% endblock %}\\n</body>\\n</html>\\n\",\"index.html\":\"{% extends \\\"base.html\\\" %}\\n{% block title %}Select Access - Key Store{% endblock %}\\n{% block content %}\\n<div class=\\\"min-h-screen flex flex-col items-center justify-center px-4 py-10\\\">\\n    <div class=\\\"text-center mb-8\\\">\\n        <h1 class=\\\"text-xl font-bold tracking-widest text-white/80 uppercase\\\">Key Store</h1>\\n    </div>\\n\\n    <div class=\\\"card w-full max-w-md p-6 shadow-2xl\\\">\\n        <div class=\\\"text-center mb-6\\\">\\n            <h2 class=\\\"text-2xl font-bold text-white\\\">Select Access</h2>\\n            <p class=\\\"text-gray-400 text-sm mt-1\\\">Premium keys with instant delivery</p>\\n        </div>\\n\\n        {% if not products %}\\n        <div class=\\\"text-center py-10 text-gray-500\\\">\\n            <i class=\\\"fas fa-box-open text-4xl mb-3\\\"></i>\\n            <p>No products available right now</p>\\n        </div>\\n        {% else %}\\n        <form method=\\\"post\\\" action=\\\"/buy\\\" id=\\\"buy-form\\\">\\n            <!-- Product -->\\n            <div class=\\\"mb-5\\\">\\n                <label class=\\\"block text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2\\\">Product</label>\\n                <div class=\\\"relative\\\">\\n                    <button type=\\\"button\\\" id=\\\"product-btn\\\" onclick=\\\"toggleDD('product')\\\"\\n                        class=\\\"input-dark w-full px-4 py-3.5 text-left flex justify-between items-center\\\">\\n                        <span id=\\\"product-label\\\" class=\\\"text-gray-400\\\">Choose your product</span>\\n                        <i class=\\\"fas fa-chevron-down text-gray-500 text-sm\\\"></i>\\n                    </button>\\n                    <div id=\\\"product-menu\\\" class=\\\"dropdown-menu absolute z-20 w-full mt-2 hidden\\\">\\n                        {% for p in products %}\\n                        <div class=\\\"option-item px-4 py-3.5 cursor-pointer\\\"\\n                             onclick=\\\"selectProduct({{ p.id }})\\\">\\n                            {{ p.name }}\\n                            <span class=\\\"text-xs text-gray-500 ml-2\\\">{{ p.type }}</span>\\n                        </div>\\n                        {% endfor %}\\n                    </div>\\n                </div>\\n                <input type=\\\"hidden\\\" name=\\\"product_id\\\" id=\\\"product_id\\\" required>\\n            </div>\\n\\n            <!-- Duration -->\\n            <div class=\\\"mb-5\\\" id=\\\"duration-section\\\" style=\\\"display:none\\\">\\n                <label class=\\\"block text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2\\\">Duration</label>\\n                <div class=\\\"relative\\\">\\n                    <button type=\\\"button\\\" id=\\\"duration-btn\\\" onclick=\\\"toggleDD('duration')\\\"\\n                        class=\\\"input-dark w-full px-4 py-3.5 text-left flex justify-between items-center\\\">\\n                        <span id=\\\"duration-label\\\" class=\\\"text-gray-400\\\">Choose your duration</span>\\n                        <i class=\\\"fas fa-chevron-down text-gray-500 text-sm\\\"></i>\\n                    </button>\\n                    <div id=\\\"duration-menu\\\" class=\\\"dropdown-menu absolute z-20 w-full mt-2 hidden\\\"></div>\\n                </div>\\n                <input type=\\\"hidden\\\" name=\\\"variant_id\\\" id=\\\"variant_id\\\" required>\\n            </div>\\n\\n            <div class=\\\"mb-6\\\">\\n                <button type=\\\"button\\\" onclick=\\\"document.getElementById('promo-box').classList.toggle('hidden')\\\"\\n                    class=\\\"w-full input-dark px-4 py-3 flex items-center gap-2 text-gray-400 text-sm\\\">\\n                    <i class=\\\"fas fa-tag\\\"></i><span>HAVE A PROMO CODE?</span>\\n                    <i class=\\\"fas fa-chevron-down ml-auto text-xs\\\"></i>\\n                </button>\\n                <div id=\\\"promo-box\\\" class=\\\"hidden mt-2\\\">\\n                    <input type=\\\"text\\\" name=\\\"promo\\\" placeholder=\\\"Enter promo code\\\" class=\\\"input-dark w-full px-4 py-3 text-sm\\\">\\n                </div>\\n            </div>\\n\\n            <button type=\\\"submit\\\" id=\\\"buy-btn\\\" disabled\\n                class=\\\"btn-primary w-full py-4 text-white font-semibold flex items-center justify-center gap-2\\\">\\n                Purchase Access <i class=\\\"fas fa-arrow-right text-sm\\\"></i>\\n            </button>\\n        </form>\\n        {% endif %}\\n\\n        <div class=\\\"mt-6 flex items-center justify-center gap-2 text-xs text-gray-500\\\">\\n            <i class=\\\"fas fa-shield-alt text-green-500\\\"></i>\\n            <span>Safe and secure instant key delivery</span>\\n        </div>\\n    </div>\\n</div>\\n{% endblock %}\\n\\n{% block scripts %}\\n\\n<script id=\\\"products-json\\\" type=\\\"application/json\\\">\\n[\\n{% for p in products %}\\n  {\\\"id\\\": {{ p.id }}, \\\"name\\\": {{ p.name|tojson }}, \\\"type\\\": {{ p.type|tojson }}, \\\"variants\\\": [\\n    {% for v in p.variants %}\\n    {\\\"id\\\": {{ v.id }}, \\\"label\\\": {{ v.label|tojson }}, \\\"price\\\": {{ v.price }}, \\\"stock\\\": {{ v.stock }}, \\\"duration_hours\\\": {{ v.duration_hours }}}{% if not loop.last %},{% endif %}\\n    {% endfor %}\\n  ]}{% if not loop.last %},{% endif %}\\n{% endfor %}\\n]\\n</script>\\n\\n<script>\\nconst productsData = JSON.parse(document.getElementById('products-json').textContent);\\n\\nfunction toggleDD(t) {\\n    const m = document.getElementById(t+'-menu');\\n    const o = t==='product'?'duration':'product';\\n    document.getElementById(o+'-menu')?.classList.add('hidden');\\n    m.classList.toggle('hidden');\\n}\\n\\nfunction selectProduct(id) {\\n    const p = productsData.find(x => x.id === id);\\n    if (!p) return;\\n    document.getElementById('product_id').value = id;\\n    document.getElementById('product-label').textContent = p.name;\\n    document.getElementById('product-label').className = 'text-white';\\n    document.getElementById('product-menu').classList.add('hidden');\\n    document.getElementById('duration-section').style.display = 'block';\\n    document.getElementById('variant_id').value = '';\\n    document.getElementById('duration-label').textContent = 'Choose your duration';\\n    document.getElementById('duration-label').className = 'text-gray-400';\\n    document.getElementById('buy-btn').disabled = true;\\n\\n    const menu = document.getElementById('duration-menu');\\n    menu.innerHTML = '';\\n    const variants = p.variants || [];\\n    if (!variants.length) {\\n        menu.innerHTML = '<div class=\\\"px-4 py-3 text-gray-500 text-sm\\\">No durations added yet</div>';\\n        return;\\n    }\\n    variants.forEach(v => {\\n        const stockTxt = p.type === 'bulk'\\n            ? (v.stock > 0 ? `<span class=\\\"stock-badge\\\">${v.stock} IN STOCK</span>` : '<span class=\\\"text-red-400 text-xs\\\">OUT OF STOCK</span>')\\n            : '<span class=\\\"stock-badge\\\">AVAILABLE</span>';\\n        const disabled = p.type === 'bulk' && v.stock <= 0;\\n        const div = document.createElement('div');\\n        div.className = 'option-item px-4 py-3.5 cursor-pointer flex justify-between items-center' + (disabled ? ' opacity-40' : '');\\n        div.innerHTML = `<span>${v.label} \\u2014 \\u20b9${v.price % 1 === 0 ? v.price : v.price.toFixed(2)}</span>${stockTxt}`;\\n        if (!disabled) {\\n            div.onclick = () => selectDuration(v.id, v.label, v.price);\\n        }\\n        menu.appendChild(div);\\n    });\\n}\\n\\nfunction selectDuration(vid, label, price) {\\n    document.getElementById('variant_id').value = vid;\\n    const pt = price % 1 === 0 ? price : price.toFixed(2);\\n    document.getElementById('duration-label').textContent = `${label} \\u2014 \\u20b9${pt}`;\\n    document.getElementById('duration-label').className = 'text-white';\\n    document.getElementById('duration-menu').classList.add('hidden');\\n    document.getElementById('buy-btn').disabled = false;\\n}\\n\\ndocument.addEventListener('click', e => {\\n    if (!e.target.closest('#product-btn') && !e.target.closest('#product-menu'))\\n        document.getElementById('product-menu')?.classList.add('hidden');\\n    if (!e.target.closest('#duration-btn') && !e.target.closest('#duration-menu'))\\n        document.getElementById('duration-menu')?.classList.add('hidden');\\n});\\n</script>\\n{% endblock %}\\n\",\"payment.html\":\"{% extends \\\"base.html\\\" %}\\n{% block title %}Complete Payment{% endblock %}\\n{% block content %}\\n<div class=\\\"min-h-screen flex flex-col items-center justify-center px-4 py-10\\\">\\n    <div class=\\\"card w-full max-w-md p-6 shadow-2xl\\\">\\n        <div class=\\\"text-center mb-6\\\">\\n            <h2 class=\\\"text-xl font-bold text-white\\\">Complete Payment</h2>\\n            <p class=\\\"text-gray-400 text-sm mt-1\\\">{{ product.name }}{% if variant %} \\u00b7 {{ variant.label }}{% endif %}</p>\\n        </div>\\n        <div class=\\\"text-center mb-6\\\">\\n            <p class=\\\"text-gray-500 text-sm\\\">Amount</p>\\n            <p class=\\\"text-4xl font-bold text-white mt-1\\\">\\u20b9{{ \\\"%.0f\\\"|format(order.amount) if order.amount == order.amount|int else \\\"%.2f\\\"|format(order.amount) }}</p>\\n        </div>\\n        <div class=\\\"flex justify-center mb-5\\\">\\n            <div class=\\\"bg-white p-3 rounded-2xl\\\">\\n                <img src=\\\"{{ order.qr_url }}\\\" alt=\\\"UPI QR\\\" class=\\\"w-52 h-52 object-contain\\\">\\n            </div>\\n        </div>\\n        <p class=\\\"text-center text-sm text-gray-400 mb-5\\\">Scan with GPay / PhonePe / Paytm / FamApp</p>\\n        <div id=\\\"status-box\\\" class=\\\"bg-[#1a1a24] border border-[#2e2e3a] rounded-xl p-4 text-center mb-4\\\">\\n            <div class=\\\"flex items-center justify-center gap-2 text-blue-400\\\">\\n                <i class=\\\"fas fa-spinner fa-spin\\\"></i>\\n                <span id=\\\"status-text\\\">Waiting for payment\\u2026</span>\\n            </div>\\n        </div>\\n        <div class=\\\"text-center text-sm text-gray-500 mb-4\\\">\\n            <i class=\\\"fas fa-clock\\\"></i> Expires in <span id=\\\"timer\\\" class=\\\"font-mono font-bold text-orange-400\\\">{{ expiry_minutes }}:00</span>\\n        </div>\\n        <div class=\\\"text-center text-xs text-gray-600 break-all\\\">Order: {{ order.order_id }}</div>\\n    </div>\\n    <a href=\\\"/\\\" class=\\\"mt-6 text-sm text-gray-500 hover:text-gray-300\\\"><i class=\\\"fas fa-arrow-left\\\"></i> Back</a>\\n</div>\\n{% endblock %}\\n{% block scripts %}\\n<script>\\nconst orderId = \\\"{{ order.order_id }}\\\";\\nconst pollInterval = {{ poll_interval }} * 1000;\\nlet timeLeft = {{ expiry_minutes }} * 60;\\nlet pollTimer, countdownTimer;\\nfunction updateTimer() {\\n    const m = Math.floor(timeLeft/60), s = timeLeft%60;\\n    document.getElementById('timer').textContent = m+':'+s.toString().padStart(2,'0');\\n    if (timeLeft <= 0) {\\n        clearInterval(countdownTimer); clearInterval(pollTimer);\\n        document.getElementById('status-box').innerHTML = '<span class=\\\"text-red-400\\\"><i class=\\\"fas fa-times-circle\\\"></i> QR Expired</span>';\\n        return;\\n    }\\n    timeLeft--;\\n}\\nasync function checkPayment() {\\n    try {\\n        const res = await fetch('/api/check/'+orderId);\\n        const data = await res.json();\\n        if (data.status === 'paid') {\\n            clearInterval(pollTimer); clearInterval(countdownTimer);\\n            document.getElementById('status-box').innerHTML = '<span class=\\\"text-green-400\\\"><i class=\\\"fas fa-check-circle\\\"></i> Payment received! Redirecting\\u2026</span>';\\n            setTimeout(() => location.href = '/success/'+orderId, 1000);\\n        }\\n    } catch(e) {}\\n}\\ncountdownTimer = setInterval(updateTimer, 1000);\\npollTimer = setInterval(checkPayment, pollInterval);\\ncheckPayment();\\n</script>\\n{% endblock %}\\n\",\"success.html\":\"{% extends \\\"base.html\\\" %}\\n{% block title %}Access Granted{% endblock %}\\n{% block content %}\\n<div class=\\\"min-h-screen flex flex-col items-center justify-center px-4 py-10\\\">\\n    <div class=\\\"card w-full max-w-md p-6 shadow-2xl\\\">\\n        <div class=\\\"text-center mb-6\\\">\\n            <div class=\\\"w-16 h-16 bg-green-500/20 rounded-full flex items-center justify-center mx-auto mb-4\\\">\\n                <i class=\\\"fas fa-check text-3xl text-green-400\\\"></i>\\n            </div>\\n            <h2 class=\\\"text-2xl font-bold text-white\\\">Access Granted</h2>\\n            <p class=\\\"text-gray-400 text-sm mt-1\\\">{{ product.name }}{% if variant %} \\u00b7 {{ variant.label }}{% endif %}</p>\\n        </div>\\n\\n        <div class=\\\"bg-[#1a1a24] border border-[#2e2e3a] rounded-xl p-5 mb-5\\\">\\n            <p class=\\\"text-xs text-gray-400 uppercase tracking-wider mb-3\\\">\\n                {% if order.delivered_type == 'panel' %}Panel Credentials{% else %}Your Key{% endif %}\\n            </p>\\n            <div class=\\\"text-white font-mono text-sm break-all whitespace-pre-wrap leading-relaxed select-all\\\">{{ order.delivered_key or 'Contact admin' }}</div>\\n        </div>\\n\\n        {% if order.utr %}\\n        <div class=\\\"text-xs text-gray-500 space-y-1 mb-4\\\">\\n            {% if order.sender_name %}<p>Paid by: {{ order.sender_name }}</p>{% endif %}\\n            <p>UTR: {{ order.utr }}</p>\\n        </div>\\n        {% endif %}\\n\\n        <a href=\\\"/\\\" class=\\\"btn-primary w-full py-3.5 text-white font-medium flex items-center justify-center gap-2\\\">\\n            <i class=\\\"fas fa-home\\\"></i> Back to Store\\n        </a>\\n    </div>\\n</div>\\n{% endblock %}\\n\"}")
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


DEFAULT_PANEL_PLANS = [
    ("5 Hours", 5, 39),
    ("12 Hours", 12, 79),
    ("1 Day", 24, 100),
    ("3 Days", 72, 250),
    ("7 Days", 168, 499),
    ("15 Days", 360, 699),
    ("30 Days", 720, 899),
]

async def seed_default_panel_variants(product_id: int):
    """Create standard duration slots for a new panel product."""
    for label, hours, price in DEFAULT_PANEL_PLANS:
        await add_variant(product_id, label, hours, price)


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
        for v in p["variants"]:
            v["stock"] = -1 if p.get("type")=="panel" else await get_stock(p["id"],v["id"])
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
        for v in p["variants"]:
            v["stock"] = -1 if p.get("type")=="panel" else await get_stock(p["id"],v["id"])
        p["total_stock"] = -1 if p.get("type")=="panel" else await get_stock(p["id"])
    return templates.TemplateResponse(request,"admin.html",{"products":products,"orders":await get_orders(40),"upi_id":UPI_ID})
@app.post("/adm-k9x2m7/product/add")
async def admin_add_product(name:str=Form(...),ptype:str=Form(...),description:str=Form(""),panel_url:str=Form(""),panel_user:str=Form(""),panel_pass:str=Form(""),user:str=Depends(verify_admin)):
    if ptype not in ("panel","bulk"):raise HTTPException(400,"bad type")
    testkey=None
    if ptype=="panel":
        try:testkey=await test_connection(panel_url,panel_user,panel_pass)
        except PanelError as e:return _redir_admin(error=str(e))
    pid=await add_product(name,ptype,description,panel_url,panel_user,panel_pass)
    if ptype=="panel":
        await seed_default_panel_variants(pid)
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
