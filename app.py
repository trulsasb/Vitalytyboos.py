# app.py
import os
import sqlite3
from datetime import datetime
from flask import (
    Flask, request, session, redirect, url_for, render_template_string,
    send_file, jsonify, flash
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# Stripe
import stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "vitalityboost_admin_secret")

DB_PATH = os.getenv("DB_PATH", "database.db")
UPLOAD_FOLDER = "static/images"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ---------- DATABASE SETUP ----------
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                price INTEGER,
                stock INTEGER,
                short_desc TEXT,
                long_desc TEXT,
                image TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS theme (
                id INTEGER PRIMARY KEY,
                header_bg TEXT,
                header_text TEXT,
                card_bg TEXT,
                card_text TEXT,
                accent TEXT,
                background TEXT,
                font TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE,
                password TEXT,
                created_at TIMESTAMP
            )
        """)
        conn.execute("""
            INSERT OR IGNORE INTO users (username, password, created_at)
            VALUES (?, ?, ?)
        """, ("admin", generate_password_hash("admin"), datetime.utcnow()))
        conn.commit()
# ---------- HANDLEKURV OG KJØP ----------
@app.route("/")
def index():
    with sqlite3.connect(DB_PATH) as conn:
        products = conn.execute("SELECT * FROM products").fetchall()
        theme = conn.execute("SELECT * FROM theme WHERE id = 1").fetchone()
    return render_template_string("""
        <html>
        <head>
            <title>Vitalityboost</title>
            <style>
                body {
                    background: {{ t[6] }};
                    font-family: {{ t[7] }};
                }
                header {
                    background: {{ t[1] }};
                    color: {{ t[2] }};
                    padding: 1em;
                }
                .product {
                    background: {{ t[3] }};
                    color: {{ t[4] }};
                    border: 1px solid #ccc;
                    padding: 1em;
                    margin: 1em;
                }
                .accent {
                    background: {{ t[5] }};
                    color: white;
                    padding: 0.5em;
                    border: none;
                }
            </style>
        </head>
        <body>
            <header>
                <h1>Vitalityboost</h1>
                <a href="/cart">Handlekurv</a> |
                <a href="/admin">Admin</a>
            </header>
            <main>
                {% for p in products %}
                    <div class="product">
                        <h2>{{ p[1] }}</h2>
                        <img src="/static/images/{{ p[6] }}" width="200">
                        <p>{{ p[4] }}</p>
                        <p><strong>{{ p[2] }} kr</strong></p>
                        <form method="post" action="/add-to-cart">
                            <input type="hidden" name="product_id" value="{{ p[0] }}">
                            <button class="accent" type="submit">Legg i handlekurv</button>
                        </form>
                    </div>
                {% endfor %}
            </main>
            <footer>
                <p>© Vitalityboost</p>
            </footer>
        </body>
        </html>
    """, products=products, t=theme or [""]*8)

@app.route("/add-to-cart", methods=["POST"])
def add_to_cart():
    pid = str(request.form["product_id"])
    cart = session.get("cart", {})
    cart[pid] = cart.get(pid, 0) + 1
    session["cart"] = cart
    return redirect(url_for("index"))

@app.route("/cart")
def cart():
    cart = session.get("cart", {})
    items = []
    total = 0
    with sqlite3.connect(DB_PATH) as conn:
        for pid, qty in cart.items():
            product = conn.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
            if product:
                subtotal = product[2] * qty
                total += subtotal
                items.append((product, qty, subtotal))
    return render_template_string("""
        <h2>Handlekurv</h2>
        <ul>
        {% for p, qty, subtotal in items %}
            <li>{{ p[1] }} x {{ qty }} = {{ subtotal }} kr</li>
        {% endfor %}
        </ul>
        <p><strong>Total: {{ total }} kr</strong></p>
        <form action="/checkout" method="post">
            <button type="submit">Gå til betaling</button>
        </form>
    """, items=items, total=total)

@app.route("/checkout", methods=["POST"])
def checkout():
    cart = session.get("cart", {})
    line_items = []
    with sqlite3.connect(DB_PATH) as conn:
        for pid, qty in cart.items():
            product = conn.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
            if product:
                line_items.append({
                    "price_data": {
                        "currency": "nok",
                        "product_data": {"name": product[1]},
                        "unit_amount": product[2] * 100
                    },
                    "quantity": qty
                })
    if not line_items:
        return "Ingen varer i handlekurven", 400
    session["cart"] = {}
    checkout_session = stripe.checkout.Session.create(
        payment_method_types=["card", "vipps"],
        line_items=line_items,
        mode="payment",
        success_url=url_for("success", _external=True),
        cancel_url=url_for("cart", _external=True)
    )
    return redirect(checkout_session.url, code=303)

init_db()
@app.route("/success")
def success():
    return "<h2>Takk for kjøpet! Du vil motta en bekreftelse på e-post.</h2>"

@app.route("/webhook", methods=["POST"])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get("stripe-signature")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ValueError:
        return "Invalid payload", 400
    except stripe.error.SignatureVerificationError:
        return "Invalid signature", 400

    if event["type"] == "checkout.session.completed":
        session_data = event["data"]["object"]
        print("✅ Betaling fullført:", session_data["id"])
        # Her kan du oppdatere ordrestatus, sende e-post, osv.

    return "", 200

# ---------- ADMIN OG TEMA ----------
@app.route("/admin/upload", methods=["POST"])
def upload_image():
    if not session.get("admin"):
        return redirect(url_for("login"))
    file = request.files["image"]
    if file:
        filename = secure_filename(file.filename)
        file.save(os.path.join(UPLOAD_FOLDER, filename))
    return redirect(url_for("admin"))

@app.route("/admin", methods=["GET"])
def admin():
    if not session.get("admin"):
        return redirect(url_for("login"))
    with sqlite3.connect(DB_PATH) as conn:
        products = conn.execute("SELECT * FROM products").fetchall()
        theme = conn.execute("SELECT * FROM theme WHERE id = 1").fetchone()
        images = os.listdir(UPLOAD_FOLDER)
    return render_template_string("""
        <h1>Adminpanel</h1>
        <a href="{{ url_for('logout') }}">Logg ut</a>
        <h2>Produkter</h2>
        <a href="{{ url_for('new_product') }}">Nytt produkt</a>
        <ul>
        {% for p in products %}
            <li>{{ p[1] }} – {{ p[2] }} kr – <a href="{{ url_for('edit_product', product_id=p[0]) }}">Rediger</a></li>
        {% endfor %}
        </ul>
        <h2>Bilder</h2>
        <form method="post" action="{{ url_for('upload_image') }}" enctype="multipart/form-data">
            <input type="file" name="image">
            <button type="submit">Last opp bilde</button>
        </form>
        <ul>
        {% for img in images %}
            <li><img src="{{ url_for('static', filename='images/' + img) }}" width="100"> {{ img }}</li>
        {% endfor %}
        </ul>
        <h2>Tema og fonter</h2>
        <form method="post" action="{{ url_for('save_theme') }}">
            {% set t = theme or ['','','','','','','',''] %}
            <input name="header_bg" value="{{ t[1] }}" placeholder="Header bakgrunn">
            <input name="header_text" value="{{ t[2] }}" placeholder="Header tekst">
            <input name="card_bg" value="{{ t[3] }}" placeholder="Produktkort bakgrunn">
            <input name="card_text" value="{{ t[4] }}" placeholder="Produkt tekst">
            <input name="accent" value="{{ t[5] }}" placeholder="Accent farge">
            <input name="background" value="{{ t[6] }}" placeholder="Side bakgrunn">
            <input name="font" value="{{ t[7] }}" placeholder="Font family">
            <button type="submit">Lagre tema</button>
        </form>
    """, products=products, theme=theme, images=images)

@app.route("/admin/product/new", methods=["GET", "POST"])
def new_product():
    if not session.get("admin"):
        return redirect(url_for("login"))
    if request.method == "POST":
        data = (
            request.form["title"],
            int(request.form["price"]),
            int(request.form["stock"]),
            request.form["short_desc"],
            request.form["long_desc"],
            request.form["image"]
        )
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT INTO products (title, price, stock, short_desc, long_desc, image) VALUES (?, ?, ?, ?, ?, ?)", data)
            conn.commit()
        return redirect(url_for("admin"))
    images = os.listdir(UPLOAD_FOLDER)
    return render_template_string("""
        <h2>Nytt produkt</h2>
        <form method="post">
            <input name="title" placeholder="Tittel">
            <input name="price" placeholder="Pris">
            <input name="stock" placeholder="Lager">
            <input name="short_desc" placeholder="Kort beskrivelse">
            <textarea name="long_desc" placeholder="Lang beskrivelse"></textarea>
            <select name="image">
                {% for img in images %}
                    <option value="{{ img }}">{{ img }}</option>
                {% endfor %}
            </select>
            <button type="submit">Lagre</button>
        </form>
    """, images=images)

@app.route("/admin/product/<int:product_id>/edit", methods=["GET", "POST"])
def edit_product(product_id):
    if not session.get("admin"):
        return redirect(url_for("login"))
    with sqlite3.connect(DB_PATH) as conn:
        product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if request.method == "POST":
        data = (
            request.form["title"],
            int(request.form["price"]),
            int(request.form["stock"]),
            request.form["short_desc"],
            request.form["long_desc"],
            request.form["image"],
            product_id
        )
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("UPDATE products SET title=?, price=?, stock=?, short_desc=?, long_desc=?, image=? WHERE id=?", data)
            conn.commit()
        return redirect(url_for("admin"))
    images = os.listdir(UPLOAD_FOLDER)
    return render_template_string("""
        <h2>Rediger produkt</h2>
        <form method="post">
            <input name="title" value="{{ p[1] }}">
            <input name="price" value="{{ p[2] }}">
            <input name="stock" value="{{ p[3] }}">
            <input name="short_desc" value="{{ p[4] }}">
            <textarea name="long_desc">{{ p[5] }}</textarea>
            <select name="image">
                {% for img in images %}
                    <option value="{{ img }}" {% if img == p[6] %}selected{% endif %}>{{ img }}</option>
                {% endfor %}
            </select>
            <button type="submit">Oppdater</button>
        </form>
    """, p=product, images=images)

@app.route("/admin/upload", methods=["POST"])
def upload_image():
    if not session.get("admin"):
        return redirect(url_for("login"))
    file = request.files["image"]
    if file:
        filename = secure_filename(file.filename)
        file.save(os.path.join(UPLOAD_FOLDER, filename))
    return redirect(url_for("admin"))

@app.route("/admin/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        with sqlite3.connect(DB_PATH) as conn:
            user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if user and check_password_hash(user[2], password):
                session["admin"] = True
                return redirect(url_for("admin"))
        flash("Feil brukernavn eller passord")
    return render_template_string("""
        <h2>Admin login</h2>
        <form method="post">
            <input name="username" placeholder="Brukernavn">
            <input name="password" type="password" placeholder="Passord">
            <button type="submit">Logg inn</button>
        </form>
    """)

@app.route("/admin/logout")
def logout():
    session.pop("admin", None)
    return redirect(url_for("login"))
