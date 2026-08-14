from flask import Flask, request, redirect, jsonify
import psycopg2
import psycopg2.extras
import requests
from datetime import datetime

app = Flask(__name__)

# CONFIGURACOES POSTGRES
DB_HOST = "apto_n8n_postgres"
DB_PORT = "5432"
DB_NAME = "nuvemshop_bi"
DB_USER = "postgres"
DB_PASS = "ecbf7b24620d811af77b"

# CONFIGURACOES NUVEM SHOP
CLIENT_ID = "37481"
CLIENT_SECRET = "bc48a48983cf73fa139c1039699cd09c2176e64a14f55e3d"
REDIRECT_URI = "https://187-77-57-53.sslip.io/callback"
SCOPES = "read_content,read_products,read_coupons,read_customers,read_orders,read_shipping,read_discounts,read_draft_orders,read_locations,read_fulfillment_orders,read_logistic,read_orders_risk,read_storecredit,read_giftcard"

def get_connection():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS
    )
@app.route("/")
def health():
    try:
        conn = get_connection()
        conn.close()
        return jsonify({"status": "ok", "db": "connected"}), 200
    except Exception as e:
        return jsonify({"status": "error", "db": str(e)}), 500
@app.route("/install")
def install():
    auth_url = (
        "https://www.nuvemshop.com.br/apps/{}/authorize"
        "?client_id={}&redirect_uri={}&scope={}"
    ).format(CLIENT_ID, CLIENT_ID, REDIRECT_URI, SCOPES)
    return redirect(auth_url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "Codigo de autorizacao nao encontrado", 400

    token_url = "https://www.nuvemshop.com.br/apps/{}/access_token".format(CLIENT_ID)
    payload = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    }

    resp = requests.post(token_url, json=payload, timeout=30)
    if resp.status_code != 200:
        return "Erro ao obter token: " + str(resp.text), 500

    token_data = resp.json()
    access_token = token_data.get("access_token")
    user_info = token_data.get("user_id", {})
    store_id = str(user_info.get("id", ""))
    store_name = user_info.get("name", "")
    scope = token_data.get("scope", "")

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO connected_stores (store_id, access_token, scope, store_name, authorized_at, status) "
        "VALUES (%s, %s, %s, %s, %s, 'active') "
        "ON CONFLICT (store_id) DO UPDATE SET "
        "access_token = EXCLUDED.access_token, scope = EXCLUDED.scope, "
        "store_name = EXCLUDED.store_name, authorized_at = EXCLUDED.authorized_at, status = 'active'",
        (store_id, access_token, scope, store_name, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

    return jsonify({"status": "success", "store_id": store_id, "store_name": store_name})

@app.route("/api/stores")
def list_stores():
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute(
        "SELECT store_id, store_name, brand_name, scope, status, authorized_at, last_sync_at "
        "FROM connected_stores ORDER BY authorized_at DESC"
    )
    rows = cursor.fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
