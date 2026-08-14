import os
import logging
from flask import Flask, request, redirect, jsonify
import psycopg2
import psycopg2.extras
import requests
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

DB_HOST = os.getenv("DB_HOST", "apto_n8n_postgres")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "nuvemshop_bi")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASSWORD", "")

CLIENT_ID = os.getenv("CLIENT_ID", "")
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("REDIRECT_URI", "")

SCOPES = "read_content,read_products,read_orders,read_customers,read_coupons"

def get_connection():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS
    )

def init_db():
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS connected_stores (
                id SERIAL PRIMARY KEY,
                store_id VARCHAR(50) UNIQUE NOT NULL,
                access_token TEXT NOT NULL,
                scope TEXT,
                store_name VARCHAR(255),
                brand_name VARCHAR(255),
                status VARCHAR(20) DEFAULT 'active',
                authorized_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_sync_at TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()
        logger.info("Tabela connected_stores verificada/criada")
    except Exception as e:
        logger.error("Erro ao criar tabela: %s", e)

init_db()

@app.route("/")
def health():
    try:
        conn = get_connection()
        conn.close()
        return jsonify({"status": "ok", "db": "connected"}), 200
    except Exception as e:
        logger.error("Health check falhou: %s", e)
        return jsonify({"status": "error", "db": str(e)}), 500

@app.route("/install")
def install():
    import secrets
    state = secrets.token_urlsafe(16)
    auth_url = "https://www.nuvemshop.com.br/apps/{}/authorize?state={}".format(CLIENT_ID, state)
    return redirect(auth_url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "Codigo de autorizacao nao encontrado", 400

    token_url = "https://www.nuvemshop.com.br/apps/authorize/token"
    payload = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
    }

    try:
        resp = requests.post(token_url, json=payload, timeout=30)
        if resp.status_code != 200:
            logger.error("Erro ao obter token: %s - %s", resp.status_code, resp.text)
            return "Erro ao obter token: " + str(resp.text), 500

        token_data = resp.json()
        access_token = token_data.get("access_token")
        store_id = str(token_data.get("user_id", ""))
        scope = token_data.get("scope", "")

        logger.info("Token obtido para store_id: %s", store_id)

        store_name = ""
        brand_name = ""
        try:
            store_resp = requests.get(
                "https://api.nuvemshop.com.br/v1/{}".format(store_id),
                headers={"Authentication": "bearer {}".format(access_token)},
                timeout=30
            )
            if store_resp.status_code == 200:
                store_info = store_resp.json()
                store_name = store_info.get("name", "")
                brand_name = store_info.get("brand", {}).get("name", "")
        except Exception as e:
            logger.warning("Nao foi possivel obter info da loja: %s", e)

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO connected_stores (store_id, access_token, scope, store_name, brand_name, authorized_at, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'active') "
            "ON CONFLICT (store_id) DO UPDATE SET "
            "access_token = EXCLUDED.access_token, scope = EXCLUDED.scope, "
            "store_name = EXCLUDED.store_name, brand_name = EXCLUDED.brand_name, "
            "authorized_at = EXCLUDED.authorized_at, status = 'active'",
            (store_id, access_token, scope, store_name, brand_name, datetime.now())
        )
        conn.commit()
        conn.close()

        return jsonify({"status": "success", "store_id": store_id, "store_name": store_name})
    except requests.exceptions.Timeout:
        logger.error("Timeout ao contactar Nuvemshop")
        return "Timeout ao obter token", 504
    except Exception as e:
        logger.error("Erro no callback: %s", e)
        return "Erro interno: " + str(e), 500

@app.route("/api/stores")
def list_stores():
    try:
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(
            "SELECT store_id, store_name, brand_name, scope, status, authorized_at, last_sync_at "
            "FROM connected_stores ORDER BY authorized_at DESC"
        )
        rows = cursor.fetchall()
        conn.close()
        result = []
        for row in rows:
            row_dict = dict(row)
            for key, value in row_dict.items():
                if isinstance(value, datetime):
                    row_dict[key] = value.isoformat()
            result.append(row_dict)
        return jsonify(result)
    except Exception as e:
        logger.error("Erro ao listar lojas: %s", e)
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
