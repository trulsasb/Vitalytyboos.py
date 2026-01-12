# app.py
import os
import sqlite3
from io import BytesIO
from datetime import datetime
from flask import (
    Flask, request, session, redirect, url_for, render_template_string,
    send_file, jsonify, flash
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# Optional stripe import handled gracefully
try:
    import stripe
except ImportError:
    stripe = None

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "vitalityboost_admin_secret")

DB_PATH = os.getenv("DB_PATH", "database.db")
ALLOWED_EXT = {"png", "jpg", "jpeg", "gif"}

# -------------------------
# Database helpers
# -------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            price INTEGER,
            active INTEGER DEFAULT 1,
            stock INTEGER DEFAULT 0,
            category_id INTEGER,
            image_id INTEGER,
            short_description TEXT,
            long_description TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            sort_order INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1
        )
    """)
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
    c.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password_hash TEXT,
            created DATETIME
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            content_type TEXT,
            data BLOB,
            created DATETIME
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()

    # Ensure default admin exists (username: admin, password: admin) — change after first login
    cur = conn.execute("SELECT * FROM admins WHERE username = ?", ("admin",))
    if not cur.fetchone():
        conn.execute(
            "INSERT INTO admins (username, password_hash, created) VALUES (?, ?, ?)",
            ("admin", generate_password_hash("admin"), datetime.utcnow())
        )
        conn.commit()

    # Default theme settings (expert chosen palette)
    defaults = {
        "header_bg": "#0B3D91",        # deep blue
        "header_fg": "#FFFFFF",        # white
        "product_bg": "#FFFFFF",       # white cards
        "product_fg": "#111827",       # near black text
        "accent": "#FF6B35",           # warm orange accent
        "page_bg": "#F7FAFC",          # light gray background
        "font_family": "Inter, system-ui, Arial, sans-serif"
    }
    for k, v in defaults.items():
        cur = conn.execute("SELECT value FROM settings WHERE key = ?", (k,))
        if not cur.fetchone():
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (k, v))
    conn.commit()
    conn.close()

# -------------------------
# Utility helpers
# -------------------------
def get_setting(key, default=None):
    conn = get_db()
    cur = conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    return row["value"] if row else default

def set_setting(key, value):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT

# -------------------------
# Image endpoints
# -------------------------
@app.route("/admin/upload_image", methods=["GET", "POST"])
def admin_upload_image():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    if request.method == "POST":
        f = request.files.get("image")
        if not f or f.filename == "":
            flash("Ingen fil valgt", "error")
            return redirect(request.url)
        if not allowed_file(f.filename):
            flash("Ugyldig filtype", "error")
            return redirect(request.url)
        data = f.read()
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO images (filename, content_type, data, created) VALUES (?, ?, ?, ?)",
            (secure_filename(f.filename), f.content_type, data, datetime.utcnow())
        )
        conn.commit()
        image_id = cur.lastrowid
        conn.close()
        flash("Bilde lastet opp", "success")
        return redirect(url_for("admin_images"))
    return render_template_string(ADMIN_UPLOAD_TEMPLATE, settings=get_all_settings())

@app.route("/image/<int:image_id>")
def serve_image(image_id):
    conn = get_db()
    cur = conn.execute("SELECT filename, content_type, data FROM images WHERE id = ?", (image_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return ("Not found", 404)
    return send_file(BytesIO(row["data"]), mimetype=row["content_type"], download_name=row["filename"])

# -------------------------
# Admin: login, logout, index
# -------------------------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        conn = get_db()
        cur = conn.execute("SELECT * FROM admins WHERE username = ?", (username,))
        row = cur.fetchone()
        conn.close()
        if row and check_password_hash(row["password_hash"], password):
            session["admin"] = username
            return redirect(url_for("admin_index"))
        flash("Ugyldig brukernavn eller passord", "error")
    return render_template_string(ADMIN_LOGIN_TEMPLATE, settings=get_all_settings())

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("admin_login"))

@app.route("/admin")
def admin_index():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    conn = get_db()
    products = conn.execute("SELECT * FROM products").fetchall()
    images = conn.execute("SELECT id, filename FROM images ORDER BY created DESC").fetchall()
    conn.close()
    return render_template_string(ADMIN_INDEX_TEMPLATE, products=products, images=images, settings=get_all_settings())

# -------------------------
# Admin: product CRUD and settings
# -------------------------
@app.route("/admin/product/new", methods=["GET", "POST"])
def admin_product_new():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    if request.method == "POST":
        title = request.form.get("title")
        price = int(request.form.get("price", "0"))
        stock = int(request.form.get("stock", "0"))
        short = request.form.get("short_description", "")
        long = request.form.get("long_description", "")
        image_id = request.form.get("image_id") or None
        conn = get_db()
        conn.execute(
            "INSERT INTO products (title, price, stock, short_description, long_description, image_id) VALUES (?, ?, ?, ?, ?, ?)",
            (title, price, stock, short, long, image_id)
        )
        conn.commit()
        conn.close()
        return redirect(url_for("admin_index"))
    conn = get_db()
    images = conn.execute("SELECT id, filename FROM images ORDER BY created DESC").fetchall()
    conn.close()
    return render_template_string(ADMIN_PRODUCT_FORM, product=None, images=images, settings=get_all_settings())

@app.route("/admin/product/<int:pid>/edit", methods=["GET", "POST"])
def admin_product_edit(pid):
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    conn = get_db()
    if request.method == "POST":
        title = request.form.get("title")
        price = int(request.form.get("price", "0"))
        stock = int(request.form.get("stock", "0"))
        short = request.form.get("short_description", "")
        long = request.form.get("long_description", "")
        image_id = request.form.get("image_id") or None
        conn.execute(
            "UPDATE products SET title=?, price=?, stock=?, short_description=?, long_description=?, image_id=? WHERE id=?",
            (title, price, stock, short, long, image_id, pid)
        )
        conn.commit()
        conn.close()
        return redirect(url_for("admin_index"))
    product = conn.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
    images = conn.execute("SELECT id, filename FROM images ORDER BY created DESC").fetchall()
    conn.close()
    return render_template_string(ADMIN_PRODUCT_FORM, product=product, images=images, settings=get_all_settings())

@app.route("/admin/product/<int:pid>/delete", methods=["POST"])
def admin_product_delete(pid):
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    conn = get_db()
    conn.execute("DELETE FROM products WHERE id = ?", (pid,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_index"))

@app.route("/admin/images")
def admin_images():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    conn = get_db()
    images = conn.execute("SELECT id, filename FROM images ORDER BY created DESC").fetchall()
    conn.close()
    return render_template_string(ADMIN_IMAGES_TEMPLATE, images=images, settings=get_all_settings())

@app.route("/admin/settings", methods=["GET", "POST"])
def admin_settings():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    if request.method == "POST":
        # Save theme settings
        keys = ["header_bg", "header_fg", "product_bg", "product_fg", "accent", "page_bg", "font_family"]
        for k in keys:
            v = request.form.get(k, "")
            set_setting(k, v)
        flash("Innstillinger lagret", "success")
        return redirect(url_for("admin_index"))
    return render_template_string(ADMIN_SETTINGS_TEMPLATE, settings=get_all_settings())

def get_all_settings():
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}

# -------------------------
# Frontend: shop, product, cart, checkout
# -------------------------
@app.route("/")
def index():
    conn = get_db()
    products = conn.execute("SELECT * FROM products WHERE active=1").fetchall()
    conn.close()
    settings = get_all_settings()
    return render_template_string(INDEX_TEMPLATE, products=products, settings=settings)

@app.route("/product/<int:pid>")
def product_detail(pid):
    conn = get_db()
    p = conn.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
    conn.close()
    if not p:
        return ("Not found", 404)
    settings = get_all_settings()
    return render_template_string(PRODUCT_TEMPLATE, product=p, settings=settings)

# Cart stored in session
@app.route("/cart")
def cart_view():
    cart = session.get("cart", {})
    conn = get_db()
    items = []
    total = 0
    for pid, qty in cart.items():
        p = conn.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
        if p:
            subtotal = p["price"] * qty
            items.append({"product": p, "qty": qty, "subtotal": subtotal})
            total += subtotal
    conn.close()
    settings = get_all_settings()
    return render_template_string(CART_TEMPLATE, items=items, total=total, settings=settings)

@app.route("/cart/add/<int:pid>", methods=["POST"])
def cart_add(pid):
    qty = int(request.form.get("qty", 1))
    cart = session.get("cart", {})
    cart[str(pid)] = cart.get(str(pid), 0) + qty
    session["cart"] = cart
    return redirect(url_for("cart_view"))

@app.route("/cart/remove/<int:pid>", methods=["POST"])
def cart_remove(pid):
    cart = session.get("cart", {})
    cart.pop(str(pid), None)
    session["cart"] = cart
    return redirect(url_for("cart_view"))

@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    if request.method == "POST":
        email = request.form.get("email")
        method = request.form.get("method", "simulated")
        cart = session.get("cart", {})
        if not cart:
            flash("Handlekurven er tom", "error")
            return redirect(url_for("cart_view"))
        conn = get_db()
        total = 0
        for pid, qty in cart.items():
            p = conn.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
            if p:
                total += p["price"] * qty
        order_id = f"ord-{int(datetime.utcnow().timestamp())}"
        conn.execute(
            "INSERT INTO orders (id, email, amount, method, status, created) VALUES (?, ?, ?, ?, ?, ?)",
            (order_id, email, total, method, "created", datetime.utcnow())
        )
        conn.commit()
        conn.close()
        session.pop("cart", None)
        flash(f"Bestilling mottatt: {order_id}", "success")
        return redirect(url_for("index"))
    settings = get_all_settings()
    return render_template_string(CHECKOUT_TEMPLATE, settings=settings)

# -------------------------
# Minimal templates (render_template_string)
# -------------------------
# For production you should move templates to files. These are inline for simplicity.
BASE_CSS = """
:root{
  --header-bg: {{ settings.get('header_bg','#0B3D91') }};
  --header-fg: {{ settings.get('header_fg','#FFFFFF') }};
  --product-bg: {{ settings.get('product_bg','#FFFFFF') }};
  --product-fg: {{ settings.get('product_fg','#111827') }};
  --accent: {{ settings.get('accent','#FF6B35') }};
  --page-bg: {{ settings.get('page_bg','#F7FAFC') }};
  --font-family: {{ settings.get('font_family','Inter, system-ui, Arial, sans-serif') }};
}
*{box-sizing:border-box}
body{font-family:var(--font-family);background:var(--page-bg);color:var(--product-fg);margin:0;padding:0}
.header{background:var(--header-bg);color:var(--header-fg);padding:20px}
.container{max-width:1100px;margin:20px auto;padding:0 16px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:16px}
.card{background:var(--product-bg);padding:12px;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,0.06)}
.btn{background:var(--accent);color:#fff;padding:8px 12px;border:none;border-radius:6px;cursor:pointer;text-decoration:none}
.small{font-size:0.9rem}
.form-row{margin-bottom:8px}
.input{padding:8px;border:1px solid #ddd;border-radius:6px;width:100%}
.notice{padding:8px;border-radius:6px;margin-bottom:12px}
.notice.success{background:#e6ffef;color:#064e3b}
.notice.error{background:#ffe6e6;color:#7f1d1d}
.footer{padding:20px;text-align:center;color:#6b7280}
"""

INDEX_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Vitalityboost - Butikk</title>
  <style>{{ base_css }}</style>
</head>
<body>
  <div class="header">
    <div class="container">
      <h1>Vitalityboost</h1>
      <div><a href="{{ url_for('cart_view') }}" class="btn small">Handlekurv</a> <a href="{{ url_for('admin_index') }}" class="btn small">Admin</a></div>
    </div>
  </div>
  <div class="container">
    <h2>Produkter</h2>
    <div class="grid">
      {% for p in products %}
      <div class="card">
        {% if p.image_id %}
          <img src="{{ url_for('serve_image', image_id=p.image_id) }}" alt="" style="width:100%;height:160px;object-fit:cover;border-radius:6px">
        {% endif %}
        <h3>{{ p.title }}</h3>
        <p class="small">{{ p.short_description }}</p>
        <p><strong>{{ p.price }} NOK</strong></p>
        <form action="{{ url_for('cart_add', pid=p.id) }}" method="post">
          <input type="number" name="qty" value="1" min="1" class="input" style="width:80px;display:inline-block">
          <button class="btn">Legg i handlekurv</button>
        </form>
        <a href="{{ url_for('product_detail', pid=p.id) }}" class="small">Detaljer</a>
      </div>
      {% endfor %}
    </div>
  </div>
  <div class="footer">© Vitalityboost</div>
</body>
</html>
"""

PRODUCT_TEMPLATE = """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>{{ product.title }}</title><style>{{ base_css }}</style></head>
<body>
  <div class="header"><div class="container"><h1>{{ product.title }}</h1><a href="{{ url_for('index') }}" class="btn small">Tilbake</a></div></div>
  <div class="container">
    <div style="display:flex;gap:16px;align-items:flex-start">
      <div style="flex:1">
        {% if product.image_id %}
          <img src="{{ url_for('serve_image', image_id=product.image_id) }}" alt="" style="width:100%;height:360px;object-fit:cover;border-radius:8px">
        {% endif %}
      </div>
      <div style="flex:1">
        <h2>{{ product.title }}</h2>
        <p>{{ product.long_description or product.short_description }}</p>
        <p><strong>{{ product.price }} NOK</strong></p>
        <form action="{{ url_for('cart_add', pid=product.id) }}" method="post">
          <input type="number" name="qty" value="1" min="1" class="input" style="width:80px;display:inline-block">
          <button class="btn">Legg i handlekurv</button>
        </form>
      </div>
    </div>
  </div>
  <div class="footer">© Vitalityboost</div>
</body>
</html>
"""

CART_TEMPLATE = """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Handlekurv</title><style>{{ base_css }}</style></head>
<body>
  <div class="header"><div class="container"><h1>Handlekurv</h1><a href="{{ url_for('index') }}" class="btn small">Fortsett å handle</a></div></div>
  <div class="container">
    {% if items %}
      <table style="width:100%;border-collapse:collapse">
        <thead><tr><th>Produkt</th><th>Antall</th><th>Pris</th><th>Subtotal</th><th></th></tr></thead>
        <tbody>
        {% for it in items %}
          <tr>
            <td>{{ it.product.title }}</td>
            <td>{{ it.qty }}</td>
            <td>{{ it.product.price }} NOK</td>
            <td>{{ it.subtotal }} NOK</td>
            <td>
              <form action="{{ url_for('cart_remove', pid=it.product.id) }}" method="post">
                <button class="btn small">Fjern</button>
              </form>
            </td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
      <h3>Total: {{ total }} NOK</h3>
      <a href="{{ url_for('checkout') }}" class="btn">Gå til betaling</a>
    {% else %}
      <p>Handlekurven er tom.</p>
    {% endif %}
  </div>
  <div class="footer">© Vitalityboost</div>
</body>
</html>
"""

CHECKOUT_TEMPLATE = """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Checkout</title><style>{{ base_css }}</style></head>
<body>
  <div class="header"><div class="container"><h1>Betaling</h1><a href="{{ url_for('cart_view') }}" class="btn small">Tilbake til handlekurv</a></div></div>
  <div class="container">
    <form method="post">
      <div class="form-row"><input class="input" name="email" placeholder="Epost" required></div>
      <div class="form-row">
        <label class="small">Betalingsmetode</label>
        <select name="method" class="input">
          <option value="simulated">Simulert betaling</option>
          {% if stripe %}<option value="stripe">Stripe</option>{% endif %}
        </select>
      </div>
      <button class="btn">Fullfør bestilling</button>
    </form>
  </div>
  <div class="footer">© Vitalityboost</div>
</body>
</html>
"""

# -------------------------
# Admin templates
# -------------------------
ADMIN_LOGIN_TEMPLATE = """
<!doctype html>
<html><head><meta charset="utf-8"><title>Admin login</title><style>{{ base_css }}</style></head>
<body>
  <div class="header"><div class="container"><h1>Admin</h1></div></div>
  <div class="container">
    <form method="post">
      <div class="form-row"><input class="input" name="username" placeholder="Brukernavn"></div>
      <div class="form-row"><input class="input" name="password" type="password" placeholder="Passord"></div>
      <button class="btn">Logg inn</button>
    </form>
  </div>
</body></html>
"""

ADMIN_INDEX_TEMPLATE = """
<!doctype html>
<html><head><meta charset="utf-8"><title>Admin</title><style>{{ base_css }}</style></head>
<body>
  <div class="header"><div class="container"><h1>Admin</h1><a href="{{ url_for('admin_logout') }}" class="btn small">Logg ut</a></div></div>
  <div class="container">
    <h2>Produkter</h2>
    <a href="{{ url_for('admin_product_new') }}" class="btn">Nytt produkt</a>
    <a href="{{ url_for('admin_images') }}" class="btn">Bilder</a>
    <a href="{{ url_for('admin_settings') }}" class="btn">Tema / fonter</a>
    <div style="margin-top:12px">
      {% for p in products %}
        <div class="card" style="margin-bottom:8px">
          <strong>{{ p.title }}</strong> — {{ p.price }} NOK
          <div style="margin-top:8px">
            <a href="{{ url_for('admin_product_edit', pid=p.id) }}" class="btn small">Rediger</a>
            <form action="{{ url_for('admin_product_delete', pid=p.id) }}" method="post" style="display:inline">
              <button class="btn small">Slett</button>
            </form>
          </div>
        </div>
      {% endfor %}
    </div>
  </div>
</body></html>
"""

ADMIN_PRODUCT_FORM = """
<!doctype html>
<html><head><meta charset="utf-8"><title>Produkt</title><style>{{ base_css }}</style></head>
<body>
  <div class="header"><div class="container"><h1>{% if product %}Rediger{% else %}Nytt{% endif %} produkt</h1></div></div>
  <div class="container">
    <form method="post">
      <div class="form-row"><input class="input" name="title" placeholder="Tittel" value="{{ product.title if product else '' }}"></div>
      <div class="form-row"><input class="input" name="price" placeholder="Pris (NOK)" value="{{ product.price if product else '' }}"></div>
      <div class="form-row"><input class="input" name="stock" placeholder="Lager" value="{{ product.stock if product else '' }}"></div>
      <div class="form-row"><textarea class="input" name="short_description" placeholder="Kort beskrivelse">{{ product.short_description if product else '' }}</textarea></div>
      <div class="form-row"><textarea class="input" name="long_description" placeholder="Lang beskrivelse">{{ product.long_description if product else '' }}</textarea></div>
      <div class="form-row">
        <label class="small">Velg bilde</label>
        <select name="image_id" class="input">
          <option value="">Ingen</option>
          {% for img in images %}
            <option value="{{ img.id }}" {% if product and product.image_id==img.id %}selected{% endif %}>{{ img.filename }}</option>
          {% endfor %}
        </select>
        <a href="{{ url_for('admin_upload_image') }}" class="btn small">Last opp nytt bilde</a>
      </div>
      <button class="btn">Lagre</button>
    </form>
  </div>
</body></html>
"""

ADMIN_UPLOAD_TEMPLATE = """
<!doctype html>
<html><head><meta charset="utf-8"><title>Last opp bilde</title><style>{{ base_css }}</style></head>
<body>
  <div class="header"><div class="container"><h1>Last opp bilde</h1></div></div>
  <div class="container">
    <form method="post" enctype="multipart/form-data">
      <div class="form-row"><input type="file" name="image" accept="image/*"></div>
      <button class="btn">Last opp</button>
    </form>
  </div>
</body></html>
"""

ADMIN_IMAGES_TEMPLATE = """
<!doctype html>
<html><head><meta charset="utf-8"><title>Bilder</title><style>{{ base_css }}</style></head>
<body>
  <div class="header"><div class="container"><h1>Bilder</h1></div></div>
  <div class="container">
    <a href="{{ url_for('admin_upload_image') }}" class="btn">Last opp nytt bilde</a>
    <div style="margin-top:12px">
      {% for img in images %}
        <div class="card" style="display:flex;align-items:center;gap:12px">
          <img src="{{ url_for('serve_image', image_id=img.id) }}" style="width:80px;height:80px;object-fit:cover;border-radius:6px">
          <div>{{ img.filename }}</div>
        </div>
      {% endfor %}
    </div>
  </div>
</body></html>
"""

ADMIN_SETTINGS_TEMPLATE = """
<!doctype html>
<html><head><meta charset="utf-8"><title>Tema</title><style>{{ base_css }}</style></head>
<body>
  <div class="header"><div class="container"><h1>Tema og fonter</h1></div></div>
  <div class="container">
    <form method="post">
      <div class="form-row"><label class="small">Header bakgrunn</label><input class="input" name="header_bg" value="{{ settings.get('header_bg') }}"></div>
      <div class="form-row"><label class="small">Header tekst</label><input class="input" name="header_fg" value="{{ settings.get('header_fg') }}"></div>
      <div class="form-row"><label class="small">Produktkort bakgrunn</label><input class="input" name="product_bg" value="{{ settings.get('product_bg') }}"></div>
      <div class="form-row"><label class="small">Produkt tekst</label><input class="input" name="product_fg" value="{{ settings.get('product_fg') }}"></div>
      <div class="form-row"><label class="small">Accent farge</label><input class="input" name="accent" value="{{ settings.get('accent') }}"></div>
      <div class="form-row"><label class="small">Side bakgrunn</label><input class="input" name="page_bg" value="{{ settings.get('page_bg') }}"></div>
      <div class="form-row"><label class="small">Font family</label><input class="input" name="font_family" value="{{ settings.get('font_family') }}"></div>
      <button class="btn">Lagre tema</button>
    </form>
  </div>
</body></html>
"""

# -------------------------
# Template context injection
# -------------------------
@app.context_processor
def inject_base():
    return dict(base_css=BASE_CSS, stripe=(stripe is not None))

# -------------------------
# App startup
# -------------------------
init_db()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
