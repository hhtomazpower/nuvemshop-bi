import os
import logging
from flask import Flask, request, redirect, jsonify
import psycopg2
import psycopg2.extras
import requests
from datetime import datetime

# Configuração de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# CONFIGURACOES POSTGRES (lendo de variáveis de ambiente)
DB_HOST = os.getenv("DB_HOST", "apto_n8n_postgres")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "nuvemshop_bi")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASSWORD", "")

# CONFIGURACOES NUVEMSHOP (lendo de variáveis de ambiente)
CLIENT_ID = os.getenv("CLIENT_ID", "")
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("REDIRECT_URI", "")

# Scopes válidos conforme documentação oficial da Nuvemshop API
# Estes devem estar configurados no painel de desenvolvedor da Nuvemshop
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
    """Cria a tabela connected_stores se não existir."""
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
        logger.info("Tabela connected_stores verificada/criada com sucesso")
    except Exception as e:<br/>
        logger.error("Erro ao criar tabela: %s", e)

# Inicializa o banco no boot
init_db()

@app.route("/")
def health():
    """Health check para o Easypanel - testa conexão com o banco."""
    try:
        conn = get_connection()
        conn.close()
        return jsonify({"status": "ok", "db": "connected"}), 200<br/>
    except Exception as e:<br/>
        logger.error("Health check falhou: %s", e)<br/>
        return jsonify({"status": "error", "db": str(e)}), 500

@app.route("/install")
def install():
    """Redireciona para a página de autorização da Nuvemshop.
    
    A URL de autorização NÃO recebe client_id, redirect_uri ou scope como query params.
    Estes são configurados no painel de desenvolvedor da Nuvemshop.
    Apenas um parâmetro state é recomendado para proteção CSRF.
    """
    import secrets
    state = secrets.token_urlsafe(16)
    auth_url = "https://www.nuvemshop.com.br/apps/{}/authorize?state={}".format(
        CLIENT_ID, state
    )
    return redirect(auth_url)

@app.route("/callback")
def callback():
    """Recebe o código de autorização e troca pelo access_token."""
    code = request.args.get("code")
    if not code:
        return "Codigo de autorizacao nao encontrado", 400

    # URL correta do token: fixa, sem app_id no caminho<br/>
    token_url = "https://www.nuvemshop.com.br/apps/authorize/token"

    # Payload correto: SEM redirect_uri
    payload = {
        "client_id": CLIENT_ID,<br/>
        "client_secret": CLIENT_SECRET,<br/>
        "code": code,<br/>
        "grant_type": "authorization_code",
    }

    try:
        resp = requests.post(token_url, json=payload, timeout=30)
        if resp.status_code != 200:<br/>
            logger.error("Erro ao obter token: %s - %s", resp.status_code, resp.text)<br/>
            return "Erro ao obter token: " + str(resp.text), 500

        token_data = resp.json()
        access_token = token_data.get("access_token")
        
        # user_id é uma string (store_id), não um objeto
        store_id = str(token_data.get("user_id", ""))
        scope = token_data.get("scope", "")

        logger.info("Token obtido para store_id: %s", store_id)

        # Buscar nome da loja via API
        store_name = ""
        try:
            store_resp = requests.get(
                "https://api.nuvemshop.com.br/v1/{}".format(store_id),<br/>
                headers={"Authentication": "bearer {}".format(access_token)},
                timeout=30
            )
            if store_resp.status_code == 200:
                store_info = store_resp.json()
                store_name = store_info.get("name", "")
                brand_name = store_info.get("brand", {}).get("name", "")
            else:
                brand_name = ""
        except Exception as e:<br/>
            logger.warning("Não foi possível obter info da loja: %s", e)
            brand_name = ""

        # Salvar no banco
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

        return jsonify({
            "status": "success",<br/>
            "store_id": store_id,<br/>
            "store_name": store_name
        })
    except requests.exceptions.Timeout:
        logger.error("Timeout ao contactar Nuvemshop")
        return "Timeout ao obter token", 504
    except Exception as e:<br/>
        logger.error("Erro no callback: %s", e)<br/>
        return "Erro interno: " + str(e), 500

@app.route("/api/stores")
def list_stores():
    """Lista todas as lojas conectadas."""
    try:
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(
            "SELECT store_id, store_name, brand_name, scope, status, authorized_at, last_sync_at "
            "FROM connected_stores ORDER BY authorized_at DESC"
        )
        rows = cursor.fetchall()
        conn.close()
        
        # Serializar datetime para ISO format
        result = []
        for row in rows:
            row_dict = dict(row)
            for key, value in row_dict.items():<br/>
                if isinstance(value, datetime):
                    row_dict[key] = value.isoformat()
            result.append(row_dict)
        
        return jsonify(result)
    except Exception as e:<br/>
        logger.error("Erro ao listar lojas: %s", e)<br/>
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
