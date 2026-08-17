import os
import json
import requests
from datetime import datetime
from flask import Flask, jsonify, request
import psycopg2

app = Flask(__name__)

# 
# CONFIG
# 
DB_CONFIG = {
    'host': os.environ.get('DB_HOST'),
    'port': os.environ.get('DB_PORT', '5432'),
    'dbname': os.environ.get('DB_NAME', 'nuvemshop_bi'),
    'user': os.environ.get('DB_USER'),
    'password': os.environ.get('DB_PASSWORD'),
}

CLIENT_ID = os.environ.get('CLIENT_ID')
CLIENT_SECRET = os.environ.get('CLIENT_SECRET')
NUVEMSHOP_API_BASE = 'https://api.tiendanube.com/v1'

# 
# DB CONNECTION
# 
def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)

def get_active_store_token(store_id):
    """Busca o access_token da loja conectada."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT access_token FROM connected_stores WHERE store_id = %s AND is_active = TRUE",
        (str(store_id),)
    )
    result = cur.fetchone()
    cur.close()
    conn.close()
    if not result:
        raise ValueError(f"Loja {store_id} não encontrada ou inativa")
    return result[0]

def make_api_headers(access_token):
    """Monta headers com o token real da loja."""
    return {
        'Authentication': f'bearer {access_token}',
        'User-Agent': 'modapower2-bi (contato@ontrade.com.br)',
        'Content-Type': 'application/json'
    }

# 
# SYNC: CATEGORIES
# 
def sync_categories(store_id, access_token):
    headers = make_api_headers(access_token)
    url = f"{NUVEMSHOP_API_BASE}/{store_id}/categories"
    all_categories = []
    page = 1

    while True:
        params = {'page': page, 'per_page': 100}
        resp = requests.get(url, headers=headers, params=params)
        if resp.status_code != 200:
            app.logger.error(f"Erro ao buscar categorias (página {page}): {resp.status_code} - {resp.text}")
            break

        data = resp.json()
        if not data:
            break

        all_categories.extend(data)
        page += 1

    conn = get_db_connection()
    cur = conn.cursor()
    now = datetime.now()

    for cat in all_categories:
        name_data = cat.get('name', {})
        desc_data = cat.get('description', {})
        cur.execute("""
            INSERT INTO categories (
                store_id, nuvemshop_id, name_pt, name_es, name_en,
                parent_category_id, description, handle,
                is_visible, product_count, subcategories_count,
                created_at_api, updated_at_api, synced_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (store_id, nuvemshop_id) DO UPDATE SET
                name_pt = EXCLUDED.name_pt,
                name_es = EXCLUDED.name_es,
                name_en = EXCLUDED.name_en,
                parent_category_id = EXCLUDED.parent_category_id,
                description = EXCLUDED.description,
                is_visible = EXCLUDED.is_visible,
                product_count = EXCLUDED.product_count,
                updated_at_api = EXCLUDED.updated_at_api,
                synced_at = EXCLUDED.synced_at
        """, (
            str(store_id), cat.get('id'),
            name_data.get('pt') if isinstance(name_data, dict) else str(name_data),
            name_data.get('es') if isinstance(name_data, dict) else None,
            name_data.get('en') if isinstance(name_data, dict) else None,
            cat.get('parent_category_id'),
            desc_data.get('pt') if isinstance(desc_data, dict) else str(desc_data) if desc_data else None,
            cat.get('handle'),
            cat.get('visible', True),
            cat.get('product_count', 0),
            cat.get('subcategories_count', 0),
            cat.get('created_at'),
            cat.get('updated_at'),
            now
        ))

    cur.execute("UPDATE connected_stores SET last_synced_at = %s WHERE store_id = %s", (now, str(store_id)))
    conn.commit()
    cur.close()
    conn.close()
    return len(all_categories)

# 
# SYNC: PRODUCTS
# 
def sync_products(store_id, access_token):
    headers = make_api_headers(access_token)
    url = f"{NUVEMSHOP_API_BASE}/{store_id}/products"
    all_products = []
    page = 1

    while True:
        params = {'page': page, 'per_page': 100}
        resp = requests.get(url, headers=headers, params=params)
        if resp.status_code != 200:
            app.logger.error(f"Erro ao buscar produtos (página {page}): {resp.status_code} - {resp.text}")
            break

        data = resp.json()
        if not data:
            break

        all_products.extend(data)
        page += 1

    conn = get_db_connection()
    cur = conn.cursor()
    now = datetime.now()

    for prod in all_products:
        name_data = prod.get('name', {})
        desc_data = prod.get('description', {})
        price_data = prod.get('price', 0) or 0
        compare_price = prod.get('compare_at_price', 0) or 0
        cost_price = prod.get('cost', 0) or 0
        images = prod.get('images', [])
        skus = prod.get('skus', [])
        categories = prod.get('categories', [])

        cur.execute("""
            INSERT INTO products (
                store_id, nuvemshop_id, name_pt, name_es, name_en,
                slug, description, brand, variant_count,
                category_id, category_name,
                price, compare_at_price, cost_price,
                weight, weight_unit, width, height, depth,
                sku, stock, stock_management,
                is_published, requires_shipping,
                images_count, thumbnail_url,
                created_at_api, updated_at_api, synced_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (store_id, nuvemshop_id) DO UPDATE SET
                name_pt = EXCLUDED.name_pt,
                name_es = EXCLUDED.name_es,
                name_en = EXCLUDED.name_en,
                slug = EXCLUDED.slug,
                description = EXCLUDED.description,
                brand = EXCLUDED.brand,
                price = EXCLUDED.price,
                compare_at_price = EXCLUDED.compare_at_price,
                cost_price = EXCLUDED.cost_price,
                stock = EXCLUDED.stock,
                stock_management = EXCLUDED.stock_management,
                is_published = EXCLUDED.is_published,
                images_count = EXCLUDED.images_count,
                thumbnail_url = EXCLUDED.thumbnail_url,
                updated_at_api = EXCLUDED.updated_at_api,
                synced_at = EXCLUDED.synced_at
        """, (
            str(store_id), prod.get('id'),
            name_data.get('pt') if isinstance(name_data, dict) else str(name_data),
            name_data.get('es') if isinstance(name_data, dict) else None,
            name_data.get('en') if isinstance(name_data, dict) else None,
            prod.get('handle', {}).get('pt') if isinstance(prod.get('handle'), dict) else prod.get('handle'),
            desc_data.get('pt') if isinstance(desc_data, dict) else (str(desc_data) if desc_data else None),
            prod.get('brand'),
            len(prod.get('variants', [])),
            categories[0] if categories else None,
            None,
            float(price_data) / 100 if price_data else 0,
            float(compare_price) / 100 if compare_price else 0,
            float(cost_price) / 100 if cost_price else 0,
            prod.get('weight', 0),
            prod.get('weight_unit', 'g'),
            prod.get('width', 0),
            prod.get('height', 0),
            prod.get('depth', 0),
            skus[0] if skus else None,
            prod.get('stock', 0),
            prod.get('stock_management', False),
            prod.get('published', True),
            prod.get('requires_shipping', True),
            len(images),
            images[0].get('src') if images else None,
            prod.get('created_at'),
            prod.get('updated_at'),
            now
        ))

    conn.commit()
    cur.close()
    conn.close()
    return len(all_products)

# 
# SYNC: CUSTOMERS
# 
def sync_customers(store_id, access_token):
    headers = make_api_headers(access_token)
    url = f"{NUVEMSHOP_API_BASE}/{store_id}/customers"
    all_customers = []
    page = 1

    while True:
        params = {'page': page, 'per_page': 100}
        resp = requests.get(url, headers=headers, params=params)
        if resp.status_code != 200:
            app.logger.error(f"Erro ao buscar clientes (página {page}): {resp.status_code} - {resp.text}")
            break

        data = resp.json()
        if not data:
            break

        all_customers.extend(data)
        page += 1

    conn = get_db_connection()
    cur = conn.cursor()
    now = datetime.now()

    for cust in all_customers:
        ident = cust.get('identification', {})
        cur.execute("""
            INSERT INTO customers (
                store_id, nuvemshop_id, name, email, phone,
                document, identification_type, note,
                default_address, addresses,
                total_spent, total_orders,
                is_newsletter_subscribed, accepts_marketing,
                tags, created_at_api, updated_at_api, synced_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (store_id, nuvemshop_id) DO UPDATE SET
                name = EXCLUDED.name,
                email = EXCLUDED.email,
                phone = EXCLUDED.phone,
                document = EXCLUDED.document,
                total_spent = EXCLUDED.total_spent,
                total_orders = EXCLUDED.total_orders,
                updated_at_api = EXCLUDED.updated_at_api,
                synced_at = EXCLUDED.synced_at
        """, (
            str(store_id), cust.get('id'),
            f"{cust.get('first_name', '')} {cust.get('last_name', '')}".strip(),
            cust.get('email'),
            cust.get('phone'),
            ident.get('number') if isinstance(ident, dict) else None,
            ident.get('type') if isinstance(ident, dict) else None,
            cust.get('note'),
            json.dumps(cust.get('default_address')) if cust.get('default_address') else None,
            json.dumps(cust.get('addresses')) if cust.get('addresses') else None,
            float(cust.get('total_spent', 0)) / 100 if cust.get('total_spent') else 0,
            cust.get('total_orders', 0),
            cust.get('newsletter_subscription', False),
            cust.get('accepts_marketing', False),
            cust.get('tags', []),
            cust.get('created_at'),
            cust.get('updated_at'),
            now
        ))

    conn.commit()
    cur.close()
    conn.close()
    return len(all_customers)

# 
# SYNC: ORDERS + ORDER_ITEMS
# 
def sync_orders(store_id, access_token):
    headers = make_api_headers(access_token)
    url = f"{NUVEMSHOP_API_BASE}/{store_id}/orders"
    all_orders = []
    page = 1

    while True:
        params = {'page': page, 'per_page': 50}
        resp = requests.get(url, headers=headers, params=params)
        if resp.status_code != 200:
            app.logger.error(f"Erro ao buscar pedidos (página {page}): {resp.status_code} - {resp.text}")
            break

        data = resp.json()
        if not data:
            break

        all_orders.extend(data)
        page += 1

    conn = get_db_connection()
    cur = conn.cursor()
    now = datetime.now()

    for ord_data in all_orders:
        customer = ord_data.get('customer', {}) if isinstance(ord_data.get('customer'), dict) else {}
        payment = ord_data.get('payment_method', {})

        cur.execute("""
            INSERT INTO orders (
                store_id, nuvemshop_id, order_number, status,
                financial_status, fulfillment_status,
                customer_id, customer_name, customer_email,
                customer_phone, customer_document,
                subtotal, discount, shipping_cost, taxes, total,
                total_weight, currency, language,
                payment_method, payment_status_detail,
                shipping_method_name, shipping_address, billing_address,
                note, tags, cancelled_at, closed_at,
                created_at_api, updated_at_api, synced_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (store_id, nuvemshop_id) DO UPDATE SET
                status = EXCLUDED.status,
                financial_status = EXCLUDED.financial_status,
                fulfillment_status = EXCLUDED.fulfillment_status,
                customer_name = EXCLUDED.customer_name,
                subtotal = EXCLUDED.subtotal,
                discount = EXCLUDED.discount,
                shipping_cost = EXCLUDED.shipping_cost,
                total = EXCLUDED.total,
                payment_method = EXCLUDED.payment_method,
                updated_at_api = EXCLUDED.updated_at_api,
                synced_at = EXCLUDED.synced_at
            RETURNING id
        """, (
            str(store_id), ord_data.get('id'), ord_data.get('number'),
            ord_data.get('status'),
            ord_data.get('payment_status'),
            ord_data.get('shipping_status'),
            customer.get('id'),
            f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip(),
            customer.get('email'),
            customer.get('phone'),
            customer.get('identification', {}).get('number') if isinstance(customer.get('identification'), dict) else None,
            float(ord_data.get('subtotal', 0)) / 100 if ord_data.get('subtotal') else 0,
            float(ord_data.get('discount', 0)) / 100 if ord_data.get('discount') else 0,
            float(ord_data.get('shipping_cost', 0)) / 100 if ord_data.get('shipping_cost') else 0,
            float(ord_data.get('tax', 0)) / 100 if ord_data.get('tax') else 0,
            float(ord_data.get('total', 0)) / 100 if ord_data.get('total') else 0,
            ord_data.get('total_weight', 0),
            ord_data.get('currency', 'BRL'),
            ord_data.get('language', 'pt'),
            payment.get('name') if isinstance(payment, dict) else str(ord_data.get('payment_method', '')),
            None,
            ord_data.get('shipping_method_name'),
            json.dumps(ord_data.get('shipping_address')) if ord_data.get('shipping_address') else None,
            json.dumps(ord_data.get('billing_address')) if ord_data.get('billing_address') else None,
            ord_data.get('note'),
            ord_data.get('tags', []),
            ord_data.get('cancelled_at'),
            ord_data.get('closed_at'),
            ord_data.get('created_at'),
            ord_data.get('updated_at'),
            now
        ))

        order_db_id = cur.fetchone()[0]

        for item in ord_data.get('products', []):
            item_name = item.get('name', {})
            item_price = item.get('price', 0) or 0
            qty = int(item.get('quantity', 1))
            cur.execute("""
                INSERT INTO order_items (
                    store_id, order_id, nuvemshop_order_id,
                    product_id, product_name, variant_id,
                    variant_values, sku, quantity,
                    unit_price, total_price, weight, weight_unit, synced_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (nuvemshop_order_id, product_id, variant_id) DO UPDATE SET
                    quantity = EXCLUDED.quantity,
                    unit_price = EXCLUDED.unit_price,
                    total_price = EXCLUDED.total_price,
                    synced_at = EXCLUDED.synced_at
            """, (
                str(store_id), order_db_id, ord_data.get('id'),
                item.get('product_id'),
                item_name.get('pt') if isinstance(item_name, dict) else str(item_name),
                item.get('variant_id'),
                json.dumps(item.get('variant_values')) if item.get('variant_values') else None,
                item.get('sku'),
                qty,
                float(item_price) / 100 if item_price else 0,
                (float(item_price) / 100 * qty) if item_price else 0,
                item.get('weight', 0),
                item.get('weight_unit', 'g'),
                now
            ))

    conn.commit()
    cur.close()
    conn.close()
    return len(all_orders)

# 
# ENDPOINT: SYNC ALL
# 
@app.route('/api/sync/<store_id>', methods=['POST'])
def sync_all(store_id):
    """Sincroniza todos os dados da loja: categorias, produtos, clientes, pedidos."""
    try:
        access_token = get_active_store_token(store_id)
    except ValueError as e:
        return jsonify({'error': str(e)}), 404

    results = {}
    errors = []

    try:
        results['categories'] = sync_categories(store_id, access_token)
    except Exception as e:
        errors.append(f'categories: {str(e)}')
        results['categories'] = 0

    try:
        results['products'] = sync_products(store_id, access_token)
    except Exception as e:
        errors.append(f'products: {str(e)}')
        results['products'] = 0

    try:
        results['customers'] = sync_customers(store_id, access_token)
    except Exception as e:
        errors.append(f'customers: {str(e)}')
        results['customers'] = 0

    try:
        results['orders'] = sync_orders(store_id, access_token)
    except Exception as e:
        errors.append(f'orders: {str(e)}')
        results['orders'] = 0

    return jsonify({
        'store_id': store_id,
        'synced': results,
        'errors': errors,
        'timestamp': datetime.now().isoformat()
    })

# 
# ENDPOINT: SYNC por entidade
# 
@app.route('/api/sync/<store_id>/<entity>', methods=['POST'])
def sync_entity(store_id, entity):
    try:
        access_token = get_active_store_token(store_id)
    except ValueError as e:
        return jsonify({'error': str(e)}), 404

    sync_map = {
        'categories': sync_categories,
        'products': sync_products,
        'customers': sync_customers,
        'orders': sync_orders
    }

    if entity not in sync_map:
        return jsonify({'error': f'Entidade inválida. Use: {list(sync_map.keys())}'}), 400

    try:
        count = sync_map[entity](store_id, access_token)
        return jsonify({
            'store_id': store_id,
            'entity': entity,
            'synced_count': count,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# 
# HEALTH CHECK
# 
@app.route('/')
def health_check():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        return jsonify({'db': 'connected', 'status': 'ok'})
    except Exception as e:
        return jsonify({'db': 'error', 'status': 'fail', 'error': str(e)}), 500

# 
# DASHBOARD: Métricas
# 
@app.route('/api/dashboard/<store_id>', methods=['GET'])
def dashboard(store_id):
    conn = get_db_connection()
    cur = conn.cursor()
    metrics = {}

    cur.execute("SELECT COUNT(*) FROM products WHERE store_id = %s", (str(store_id),))
    metrics['total_products'] = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM orders WHERE store_id = %s", (str(store_id),))
    metrics['total_orders'] = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM customers WHERE store_id = %s", (str(store_id),))
    metrics['total_customers'] = cur.fetchone()[0]

    cur.execute("SELECT COALESCE(SUM(total), 0) FROM orders WHERE store_id = %s AND status != 'cancelled'", (str(store_id),))
    metrics['total_revenue'] = float(cur.fetchone()[0])

    cur.execute("SELECT COALESCE(AVG(total), 0) FROM orders WHERE store_id = %s AND status != 'cancelled'", (str(store_id),))
    metrics['avg_order_value'] = float(cur.fetchone()[0])

    cur.execute("SELECT COUNT(*) FROM order_items WHERE store_id = %s", (str(store_id),))
    metrics['total_items_sold'] = cur.fetchone()[0]

    cur.close()
    conn.close()
    return jsonify({'store_id': store_id, 'metrics': metrics})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
