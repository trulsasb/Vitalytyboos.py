import sqlite3
import os
from flask import Flask, request, session, redirect, send_from_directory, jsonify
from flask import make_response

from datetime import datetime
import json

try:
    import stripe
except ImportError:
    stripe = None  # håndteres dynamisk

app = Flask(__name__)
app.secret_key = "vitalityboost_admin_secret"

DB_PATH = "database.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    # Produkter
    c.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            price INTEGER,
            active INTEGER,
            stock INTEGER,
            category_id INTEGER,
            image_url TEXT,
            short_description TEXT
        )
    """)

    # Kategorier
    c.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            sort_order INTEGER,
            active INTEGER
        )
    """)

    # Ordrer
    c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            email TEXT,
            amount INTEGER,
            method TEXT,
            status TEXT,
            created DATETIME
        )
    """)

    # Admin-brukere
    c.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT
        )
    """)

    # Innstillinger
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # Seed admin
    c.execute("SELECT COUNT(*) AS count FROM admins")
    if c.fetchone()["count"] == 0:
        c.execute("INSERT INTO admins (username, password) VALUES (?, ?)",
                  ("admin", "admin123"))

    # Seed settings
    c.execute("SELECT COUNT(*) AS count FROM settings")
    if c.fetchone()["count"] == 0:
        defaults = {
            "store_name": "Vitality Boost",
            "hero_title": "Premium kosttilskudd – uten abonnement og med gratis frakt",
            "hero_subtitle": "Velg produktene, legg inn e-post og betal trygt med kort eller Vipps.",
            "primary_color": "#1b7f5f",
            "accent_color": "#ffb347",
            "image_height": "220",
            "stripe_enabled": "0",
            "vipps_enabled": "0",
            "stripe_public_key": "",
            "stripe_secret_key": "",
            "vipps_merchant_key": "",
        }
        for k, v in defaults.items():
            c.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (k, v))

    # Seed produkter/kategorier
    c.execute("SELECT COUNT(*) AS count FROM products")
    if c.fetchone()["count"] == 0:
        c.execute("INSERT INTO categories (name, sort_order, active) VALUES (?,?,?)",
                  ("Bestselgere", 1, 1))
        c.execute("SELECT id FROM categories WHERE name=?", ("Bestselgere",))
        cat_id = c.fetchone()["id"]
        products = [
            ("Omega Vital+", 399, 1, 20, cat_id,
             "https://via.placeholder.com/300x200?text=Omega+Vital%2B",
             "Essensielle fettsyrer for hjerte og ledd."),
            ("Collagen Boost 50+", 449, 1, 15, cat_id,
             "https://via.placeholder.com/300x200?text=Collagen+Boost+50%2B",
             "Hud, ledd og bindevev – spesielt for 50+."),
            ("MindSharp Focus", 349, 1, 25, cat_id,
             "https://via.placeholder.com/300x200?text=MindSharp+Focus",
             "For fokus og mental klarhet i hverdagen."),
        ]
        c.executemany("""
            INSERT INTO products
            (title, price, active, stock, category_id, image_url, short_description)
            VALUES (?,?,?,?,?,?,?)
        """, products)

    conn.commit()
    conn.close()


def get_settings(keys):
    if isinstance(keys, str):
        keys = [keys]
    conn = get_db()
    c = conn.cursor()
    placeholders = ",".join(["?"] * len(keys))
    c.execute(f"SELECT key, value FROM settings WHERE key IN ({placeholders})", keys)
    rows = c.fetchall()
    conn.close()
    out = {}
    for r in rows:
        out[r["key"]] = r["value"]
    return out


def set_setting(key, value):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def require_admin():
    if "admin" not in session:
        return False
    return True


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "GET":
        html = """
<!DOCTYPE html>
<html lang="no">
<head>
<meta charset="UTF-8" />
<title>Admin login</title>
<style>
body{font-family:system-ui;background:#f4f4f4;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
.box{background:#fff;padding:1.5rem;border-radius:0.5rem;box-shadow:0 2px 8px rgba(0,0,0,0.1);width:300px;}
h2{margin-top:0;margin-bottom:1rem;}
input{width:100%;margin-bottom:0.5rem;padding:0.4rem;border-radius:0.3rem;border:1px solid #ccc;}
button{width:100%;padding:0.5rem;border:none;border-radius:999px;background:#1b7f5f;color:#fff;font-weight:600;cursor:pointer;}
</style>
</head>
<body>
  <div class="box">
    <h2>Admin login</h2>
    <form method="POST">
      <input type="text" name="username" placeholder="Brukernavn" required/>
      <input type="password" name="password" placeholder="Passord" required/>
      <button>Logg inn</button>
    </form>
  </div>
</body>
</html>
        """
        return html

    username = request.form.get("username", "")
    password = request.form.get("password", "")
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM admins WHERE username=? AND password=?",
              (username, password))
    row = c.fetchone()
    conn.close()
    if row:
        session["admin"] = {"id": row["id"], "username": row["username"]}
        return redirect("/admin")
    return "Feil brukernavn eller passord. Gå tilbake og prøv igjen."


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin/login")


@app.route("/admin")
def admin_dashboard():
    if not require_admin():
        return redirect("/admin/login")

    conn = get_db()
    c = conn.cursor()

    c.execute("SELECT * FROM orders ORDER BY created DESC LIMIT 50")
    orders = c.fetchall()

    c.execute("""
        SELECT p.*, c.name as category_name
        FROM products p
        LEFT JOIN categories c ON p.category_id=c.id
        ORDER BY p.id
    """)
    products = c.fetchall()

    c.execute("SELECT * FROM categories ORDER BY sort_order ASC, id ASC")
    categories = c.fetchall()

    settings = get_settings([
        "store_name",
        "hero_title",
        "hero_subtitle",
        "primary_color",
        "accent_color",
        "image_height",
        "stripe_enabled",
        "vipps_enabled",
        "stripe_public_key",
        "stripe_secret_key",
        "vipps_merchant_key",
    ])

    total = sum([o["amount"] or 0 for o in orders])

    conn.close()

    def esc(s):
        if s is None:
            return ""
        return str(s).replace('"', "&quot;")

    html = f"""
<!DOCTYPE html>
<html lang="no">
<head>
<meta charset="UTF-8" />
<title>Admin – {esc(settings.get('store_name', 'Vitality Boost'))}</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<style>
body{{font-family:system-ui;margin:0;background:#f5f5f7;color:#222;}}
header{{background:#fff;border-bottom:1px solid #ddd;padding:0.5rem 1rem;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:10;}}
header h1{{font-size:1.1rem;margin:0;}}
nav button{{margin-right:0.3rem;padding:0.3rem 0.7rem;border-radius:999px;border:1px solid #ccc;background:#fff;cursor:pointer;font-size:0.85rem;}}
nav button.active{{background:#222;color:#fff;border-color:#222;}}
main{{padding:1rem;max-width:1100px;margin:0 auto;}}
section{{display:none;}}
section.active{{display:block;}}
table{{width:100%;border-collapse:collapse;margin-top:0.5rem;font-size:0.85rem;}}
th,td{{border:1px solid #ddd;padding:0.35rem;text-align:left;}}
input[type="text"],input[type="number"],input[type="password"],input[type="email"]{{width:100%;box-sizing:border-box;padding:0.25rem;border-radius:0.25rem;border:1px solid #ccc;font-size:0.85rem;}}
textarea{{width:100%;box-sizing:border-box;padding:0.25rem;border-radius:0.25rem;border:1px solid #ccc;font-size:0.85rem;}}
button.primary{{background:#1b7f5f;color:#fff;border:none;border-radius:999px;padding:0.35rem 0.8rem;cursor:pointer;font-size:0.85rem;}}
.btn-sm{{font-size:0.75rem;padding:0.2rem 0.5rem;}}
.badge{{display:inline-block;padding:0.1rem 0.35rem;border-radius:999px;font-size:0.7rem;background:#eee;}}
.badge.ok{{background:#e6f5f0;color:#1b7f5f;}}
.flex{{display:flex;gap:0.5rem;flex-wrap:wrap;}}
.card{{background:#fff;border-radius:0.5rem;padding:0.75rem;box-shadow:0 1px 3px rgba(0,0,0,0.05);margin-top:0.5rem;}}
</style>
</head>
<body>

<header>
  <h1>Admin – {esc(settings.get('store_name', 'Vitality Boost'))}</h1>
  <div>
    <span class="badge ok">Ordrer: {len(orders)}</span>
    <span class="badge ok">Omsetning: {total} kr</span>
    <a href="/admin/logout" style="margin-left:0.7rem;font-size:0.8rem;">Logg ut</a>
  </div>
</header>

<nav style="padding:0.3rem 1rem;">
  <button data-tab="products" class="active">Produkter</button>
  <button data-tab="categories">Kategorier</button>
  <button data-tab="design">Design & tekster</button>
  <button data-tab="payments">Betaling</button>
  <button data-tab="orders">Ordrer</button>
</nav>

<main>

<section id="tab-products" class="active">
  <h2>Produkter</h2>
  <form method="POST" action="/admin/product/new" class="card">
    <h3>Nytt produkt</h3>
    <div class="flex">
      <div style="flex:2;">
        <label>Tittel</label>
        <input name="title" required />
      </div>
      <div style="flex:1;">
        <label>Pris (kr)</label>
        <input type="number" name="price" required />
      </div>
      <div style="flex:1;">
        <label>Lager</label>
        <input type="number" name="stock" value="10" required />
      </div>
    </div>
    <div class="flex">
      <div style="flex:1;">
        <label>Kategori</label>
        <select name="category_id">
          <option value="">Ingen</option>
          {''.join(f'<option value="{{c["id"]}}">{{c["name"]}}</option>'.replace('{{','{').replace('}}','}') for c in categories)}
        </select>
      </div>
      <div style="flex:2;">
        <label>Bilde-URL</label>
        <input name="image_url" placeholder="https://..." />
      </div>
    </div>
    <div>
      <label>Kort beskrivelse</label>
      <textarea name="short_description" rows="2"></textarea>
    </div>
    <div style="margin-top:0.5rem;">
      <label>Aktiv: <input type="checkbox" name="active" checked /></label>
    </div>
    <button class="primary" style="margin-top:0.5rem;">Lag produkt</button>
  </form>

  <div class="card">
    <h3>Eksisterende produkter</h3>
    <table>
      <tr>
        <th>ID</th><th>Tittel</th><th>Pris</th><th>Lager</th><th>Aktiv</th>
        <th>Kategori</th><th>Bilde</th><th>Tekst</th><th></th>
      </tr>
"""
    for p in products:
        html += f"""
      <tr>
        <form method="POST" action="/admin/product/{p['id']}">
          <td>{p['id']}</td>
          <td><input name="title" value="{esc(p['title'])}" /></td>
          <td><input type="number" name="price" value="{p['price']}" /></td>
          <td><input type="number" name="stock" value="{p['stock']}" /></td>
          <td>
            <select name="active">
              <option value="1" {"selected" if p["active"] else ""}>Ja</option>
              <option value="0" {"selected" if not p["active"] else ""}>Nei</option>
            </select>
          </td>
          <td>
            <select name="category_id">
              <option value="">Ingen</option>
        """
        for c in categories:
            sel = "selected" if p["category_id"] == c["id"] else ""
            html += f'<option value="{c["id"]}" {sel}>{esc(c["name"])}</option>'
        html += f"""
            </select>
          </td>
          <td><input name="image_url" value="{esc(p['image_url'])}" /></td>
          <td><input name="short_description" value="{esc(p['short_description'])}" /></td>
          <td><button class="btn-sm primary">Lagre</button></td>
        </form>
      </tr>
"""
    html += """
    </table>
  </div>
</section>

<section id="tab-categories">
  <h2>Kategorier</h2>
  <form method="POST" action="/admin/category/new" class="card">
    <h3>Ny kategori</h3>
    <div class="flex">
      <div style="flex:2;">
        <label>Navn</label>
        <input name="name" required />
      </div>
      <div style="flex:1;">
        <label>Sortering</label>
        <input type="number" name="sort_order" value="1" />
      </div>
    </div>
    <div style="margin-top:0.5rem;">
      <label>Aktiv: <input type="checkbox" name="active" checked /></label>
    </div>
    <button class="primary" style="margin-top:0.5rem;">Lag kategori</button>
  </form>

  <div class="card">
    <h3>Eksisterende kategorier</h3>
    <table>
      <tr><th>ID</th><th>Navn</th><th>Sortering</th><th>Aktiv</th><th></th></tr>
"""
    for c in categories:
        html += f"""
      <tr>
        <form method="POST" action="/admin/category/{c['id']}">
          <td>{c['id']}</td>
          <td><input name="name" value="{esc(c['name'])}" /></td>
          <td><input type="number" name="sort_order" value="{c['sort_order']}" /></td>
          <td>
            <select name="active">
              <option value="1" {"selected" if c["active"] else ""}>Ja</option>
              <option value="0" {"selected" if not c["active"] else ""}>Nei</option>
            </select>
          </td>
          <td><button class="btn-sm primary">Lagre</button></td>
        </form>
      </tr>
"""
    html += f"""
    </table>
  </div>
</section>

<section id="tab-design">
  <h2>Design & tekster</h2>
  <form method="POST" action="/admin/settings" class="card">
    <h3>Butikknavn og topptekst</h3>
    <label>Butikknavn</label>
    <input name="store_name" value="{esc(settings.get('store_name',''))}" />
    <label>Hovedoverskrift</label>
    <input name="hero_title" value="{esc(settings.get('hero_title',''))}" />
    <label>Undertekst</label>
    <textarea name="hero_subtitle" rows="2">{esc(settings.get('hero_subtitle',''))}</textarea>

    <h3>Farger og bilder</h3>
    <div class="flex">
      <div style="flex:1;">
        <label>Primærfarge (hex)</label>
        <input name="primary_color" value="{esc(settings.get('primary_color','#1b7f5f'))}" />
      </div>
      <div style="flex:1;">
        <label>Accent-farge (hex)</label>
        <input name="accent_color" value="{esc(settings.get('accent_color','#ffb347'))}" />
      </div>
    </div>
    <div style="margin-top:0.5rem;">
      <label>Bildehøyde (px)</label>
      <input type="range" min="120" max="320" name="image_height_slider"
             value="{esc(settings.get('image_height','220'))}"
             oninput="document.getElementById('imgHeightVal').innerText=this.value;
                      document.getElementById('image_height_hidden').value=this.value;">
      <div>Valgt: <span id="imgHeightVal">{esc(settings.get('image_height','220'))}</span> px</div>
      <input type="hidden" id="image_height_hidden" name="image_height"
             value="{esc(settings.get('image_height','220'))}">
    </div>
    <button class="primary" style="margin-top:0.75rem;">Lagre design & tekster</button>
  </form>
</section>

<section id="tab-payments">
  <h2>Betaling (Stripe/Vipps)</h2>
  <form method="POST" action="/admin/payments" class="card">
    <h3>Stripe</h3>
    <label><input type="checkbox" name="stripe_enabled" value="1" {"checked" if settings.get("stripe_enabled")=="1" else ""}/> Stripe aktivert</label>
    <label>Stripe public key</label>
    <input name="stripe_public_key" value="{esc(settings.get('stripe_public_key',''))}" />
    <label>Stripe secret key</label>
    <input name="stripe_secret_key" value="{esc(settings.get('stripe_secret_key',''))}" />

    <h3 style="margin-top:1rem;">Vipps</h3>
    <label><input type="checkbox" name="vipps_enabled" value="1" {"checked" if settings.get("vipps_enabled")=="1" else ""}/> Vipps aktivert</label>
    <label>Vipps merchant key (plassholder)</label>
    <input name="vipps_merchant_key" value="{esc(settings.get('vipps_merchant_key',''))}" />
    <p style="font-size:0.8rem;color:#666;">
      Vipps-integrasjonen i denne varianten registrerer ordren som Vipps-ordre. Videre integrasjon kan bygges på toppen.
    </p>

    <button class="primary" style="margin-top:0.75rem;">Lagre betalingsinnstillinger</button>
  </form>
</section>

<section id="tab-orders">
  <h2>Siste ordrer</h2>
  <div class="card">
    <table>
      <tr><th>ID</th><th>E-post</th><th>Beløp</th><th>Metode</th><th>Status</th><th>Tid</th></tr>
"""
    for o in orders:
        html += f"""
      <tr>
        <td>{o['id']}</td>
        <td>{esc(o['email'])}</td>
        <td>{o['amount']}</td>
        <td>{esc(o['method'])}</td>
        <td>{esc(o['status'])}</td>
        <td>{esc(o['created'])}</td>
      </tr>
"""
    html += """
    </table>
  </div>
</section>

</main>

<script>
const buttons = document.querySelectorAll('nav button');
const sections = {
  products: document.getElementById('tab-products'),
  categories: document.getElementById('tab-categories'),
  design: document.getElementById('tab-design'),
  payments: document.getElementById('tab-payments'),
  orders: document.getElementById('tab-orders')
};
buttons.forEach(btn=>{
  btn.addEventListener('click',()=>{
    buttons.forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    Object.values(sections).forEach(s=>s.classList.remove('active'));
    sections[btn.dataset.tab].classList.add('active');
  });
});
</script>

</body>
</html>
"""
    return html


@app.route("/admin/product/new", methods=["POST"])
def admin_product_new():
    if not require_admin():
        return redirect("/admin/login")
    f = request.form
    title = f.get("title", "")
    price = int(f.get("price", "0") or 0)
    stock = int(f.get("stock", "0") or 0)
    category_id = f.get("category_id") or None
    image_url = f.get("image_url", "")
    short_description = f.get("short_description", "")
    active = 1 if f.get("active") else 0

    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO products (title, price, active, stock, category_id, image_url, short_description)
        VALUES (?,?,?,?,?,?,?)
    """, (title, price, active, stock, category_id, image_url, short_description))
    conn.commit()
    conn.close()
    return redirect("/admin")


@app.route("/admin/product/<int:pid>", methods=["POST"])
def admin_product_update(pid):
    if not require_admin():
        return redirect("/admin/login")
    f = request.form
    title = f.get("title", "")
    price = int(f.get("price", "0") or 0)
    stock = int(f.get("stock", "0") or 0)
    category_id = f.get("category_id") or None
    image_url = f.get("image_url", "")
    short_description = f.get("short_description", "")
    active = 1 if f.get("active") == "1" else 0

    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE products
        SET title=?, price=?, stock=?, category_id=?, image_url=?, short_description=?, active=?
        WHERE id=?
    """, (title, price, stock, category_id, image_url, short_description, active, pid))
    conn.commit()
    conn.close()
    return redirect("/admin")


@app.route("/admin/category/new", methods=["POST"])
def admin_category_new():
    if not require_admin():
        return redirect("/admin/login")
    f = request.form
    name = f.get("name", "")
    sort_order = int(f.get("sort_order", "1") or 1)
    active = 1 if f.get("active") else 0

    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO categories (name, sort_order, active) VALUES (?,?,?)",
              (name, sort_order, active))
    conn.commit()
    conn.close()
    return redirect("/admin")


@app.route("/admin/category/<int:cid>", methods=["POST"])
def admin_category_update(cid):
    if not require_admin():
        return redirect("/admin/login")
    f = request.form
    name = f.get("name", "")
    sort_order = int(f.get("sort_order", "1") or 1)
    active = 1 if f.get("active") == "1" else 0

    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE categories
        SET name=?, sort_order=?, active=?
        WHERE id=?
    """, (name, sort_order, active, cid))
    conn.commit()
    conn.close()
    return redirect("/admin")


@app.route("/admin/settings", methods=["POST"])
def admin_settings_update():
    if not require_admin():
        return redirect("/admin/login")
    f = request.form
    keys = ["store_name", "hero_title", "hero_subtitle",
            "primary_color", "accent_color", "image_height"]
    for k in keys:
        set_setting(k, f.get(k, ""))
    return redirect("/admin")


@app.route("/admin/payments", methods=["POST"])
def admin_payments_update():
    if not require_admin():
        return redirect("/admin/login")
    f = request.form
    stripe_enabled = "1" if f.get("stripe_enabled") else "0"
    vipps_enabled = "1" if f.get("vipps_enabled") else "0"
    set_setting("stripe_enabled", stripe_enabled)
    set_setting("vipps_enabled", vipps_enabled)
    set_setting("stripe_public_key", f.get("stripe_public_key", ""))
    set_setting("stripe_secret_key", f.get("stripe_secret_key", ""))
    set_setting("vipps_merchant_key", f.get("vipps_merchant_key", ""))
    return redirect("/admin")


@app.route("/api/config")
def api_config():
    settings = get_settings([
        "store_name",
        "hero_title",
        "hero_subtitle",
        "primary_color",
        "accent_color",
        "image_height",
        "stripe_enabled",
        "vipps_enabled",
        "stripe_public_key",
    ])
    return jsonify(settings)


@app.route("/api/categories")
def api_categories():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM categories WHERE active=1 ORDER BY sort_order ASC, id ASC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify(rows)


@app.route("/api/products")
def api_products():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM products WHERE active=1 ORDER BY id ASC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify(rows)


@app.route("/api/order/init", methods=["POST"])
def api_order_init():
    data = request.get_json() or {}
    email = data.get("email")
    amount = data.get("amount")
    method = data.get("method", "INIT")
    if not email or not amount or amount <= 0:
        return jsonify({"error": "Ugyldige ordredata"}), 400
    order_id = "ORD-" + datetime.now().strftime("%Y%m%d%H%M%S%f")
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO orders (id, email, amount, method, status, created)
        VALUES (?,?,?,?,?,?)
    """, (order_id, email, amount, method, "PENDING", datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "orderId": order_id})


@app.route("/api/pay/vipps-init", methods=["POST"])
def api_pay_vipps_init():
    data = request.get_json() or {}
    cart = data.get("cart", [])
    email = data.get("email")
    if not cart or not email:
        return jsonify({"error": "Ugyldige data"}), 400

    settings = get_settings(["vipps_enabled"])
    if settings.get("vipps_enabled") != "1":
        return jsonify({"error": "Vipps ikke aktivert"}), 400

    amount = sum(item["price"] * item["qty"] for item in cart)
    order_id = "ORD-" + datetime.now().strftime("%Y%m%d%H%M%S%f")
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO orders (id, email, amount, method, status, created)
        VALUES (?,?,?,?,?,?)
    """, (order_id, email, amount, "Vipps", "PENDING", datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()
    # Her kan du senere koble til ekte Vipps-API
    return jsonify({"ok": True, "orderId": order_id})


@app.route("/api/pay/stripe-session", methods=["POST"])
def api_pay_stripe_session():
    data = request.get_json() or {}
    cart = data.get("cart", [])
    email = data.get("email")
    if not cart or not email:
        return jsonify({"error": "Ugyldige data"}), 400

    settings = get_settings(["stripe_enabled", "stripe_secret_key", "stripe_public_key"])
    if settings.get("stripe_enabled") != "1" or not settings.get("stripe_secret_key"):
        return jsonify({"error": "Stripe ikke aktivert"}), 400

    if stripe is None:
        return jsonify({"error": "Stripe-biblioteket er ikke installert"}), 500

    stripe.api_key = settings["stripe_secret_key"]
    amount = sum(item["price"] * item["qty"] for item in cart)
    order_id = "ORD-" + datetime.now().strftime("%Y%m%d%H%M%S%f")

    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO orders (id, email, amount, method, status, created)
        VALUES (?,?,?,?,?,?)
    """, (order_id, email, amount, "Stripe", "PENDING", datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()

    try:
        session_obj = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="payment",
            customer_email=email,
            line_items=[
                {
                    "price_data": {
                        "currency": "nok",
                        "unit_amount": item["price"] * 100,
                        "product_data": {"name": item["title"]},
                    },
                    "quantity": item["qty"],
                }
                for item in cart
            ],
            metadata={"orderId": order_id},
            success_url=request.url_root.rstrip("/") + "/?success=1&orderId=" + order_id,
            cancel_url=request.url_root.rstrip("/") + "/?canceled=1&orderId=" + order_id,
        )
        return jsonify({
            "ok": True,
            "sessionId": session_obj.id,
            "publicKey": settings.get("stripe_public_key")
        })
    except Exception as e:
        print("Stripe-feil:", e)
        return jsonify({"error": "Stripe-feil"}), 500


@app.route("/<path:filename>")
def static_files(filename):
    # serverer index.html og ev. andre filer hvis du legger dem i samme mappe
    return send_from_directory(".", filename)


if __name__ == "__main__":
    init_db()
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
