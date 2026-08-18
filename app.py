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
# SYNC: CATEGORIES (CORRIGIDO)
# 
def sync_categories(store_id, access_token):
    headers = make_api_headers(access_token)
    url = f"{NUVEMSHOP_API_BASE}/{store_id}/categories"
    all_categories = []
    page = 1
    per_page = 100
    while True:
        params = {'page': page, 'per_page': per_page}
        resp = requests.get(url, headers=headers, params=params)
        # 404 = não há mais páginas — parar sem erro
        if resp.status_code == 404:
            break
        if resp.status_code != 200:
            app.logger.error(f"Erro ao buscar categorias (página {page}): {resp.status_code} - {resp.text}")
            break
        data = resp.json()
        if not data:
            break
        all_categories.extend(data)
        # Se veio menos que per_page, é a última página
        if len(data) < per_page:
            break
        page += 1
    conn = get_db_connection()
    cur = conn.cursor()
    now = datetime.now()
    for cat in all_categories:
        # Extrair campos multilíngues com segurança
        name_data = cat.get('name', {})
        name_pt = name_data.get('pt') if isinstance(name_data, dict) else str(name_data) if name_data else None
        name_es = name_data.get('es') if isinstance(name_data, dict) else None
        name_en = name_data.get('en') if isinstance(name_data, dict) else None
        desc_data = cat.get('description', {})
        if isinstance(desc_data, dict):
            description = desc_data.get('pt')
        elif desc_data:
            description = str(desc_data)
        else:
            description = None
        handle_data = cat.get('handle', {})
        if isinstance(handle_data, dict):
            handle = handle_data.get('pt')
        elif handle_data:
            handle = str(handle_data)
        else:
            handle = None
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
                handle = EXCLUDED.handle,
                is_visible = EXCLUDED.is_visible,
                product_count = EXCLUDED.product_count,
                updated_at_api = EXCLUDED.updated_at_api,
                synced_at = EXCLUDED.synced_at
        """, (
            str(store_id), cat.get('id'),
            name_pt, name_es, name_en,
            cat.get('parent_category_id'),
            description, handle,
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
# SYNC: PRODUCTS (CORRIGIDO v3 - CONTAGEM DE %s CORRIGIDA)
# 
def safe_str(val):
    if val is None:
        return None
    if isinstance(val, dict):
        return val.get('pt') or val.get('es') or val.get('en') or json.dumps(val)
    if isinstance(val, list):
        return json.dumps(val)
    return str(val)
def safe_float(val, divide_by=1):
    if val is None:
        return 0
    if isinstance(val, (dict, list)):
        return 0
    try:
        return float(val) / divide_by
    except (ValueError, TypeError):
        return 0
def safe_int(val):
    if val is None:
        return 0
    if isinstance(val, (dict, list)):
        return 0
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0
def safe_bool(val):
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    if isinstance(val, (dict, list)):
        return False
    if isinstance(val, str):
        return val.lower() in ('true', '1', 'yes')
    return bool(val)
def sync_products(store_id, access_token):
    headers = make_api_headers(access_token)
    url = f"{NUVEMSHOP_API_BASE}/{store_id}/products"
    all_products = []
    page = 1
    per_page = 100
    while True:
        params = {'page': page, 'per_page': per_page}
        resp = requests.get(url, headers=headers, params=params)
        # 404 = não há mais páginas — parar sem erro
        if resp.status_code == 404:
            break
        if resp.status_code != 200:
            app.logger.error(f"Erro ao buscar produtos (página {page}): {resp.status_code} - {resp.text}")
            break
        data = resp.json()
        if not data:
            break
        all_products.extend(data)
        # Se veio menos que per_page, é a última página
        if len(data) < per_page:
            break
        page += 1
    conn = get_db_connection()
    cur = conn.cursor()
    now = datetime.now()
    for prod in all_products:
        name_data = prod.get('name', {})
        name_pt = name_data.get('pt') if isinstance(name_data, dict) else safe_str(name_data)
        name_es = name_data.get('es') if isinstance(name_data, dict) else None
        name_en = name_data.get('en') if isinstance(name_data, dict) else None
        desc_data = prod.get('description', {})
        description = desc_data.get('pt') if isinstance(desc_data, dict) else safe_str(desc_data)
        handle_data = prod.get('handle', {})
        slug = handle_data.get('pt') if isinstance(handle_data, dict) else safe_str(handle_data)
        images = prod.get('images', [])
        if not isinstance(images, list):
            images = []
        skus = prod.get('skus', [])
        if not isinstance(skus, list):
            skus = []
        categories = prod.get('categories', [])
        if not isinstance(categories, list):
            categories = []
        variants = prod.get('variants', [])
        if not isinstance(variants, list):
            variants = []
        # IMPORTANTE: 29 colunas = 29 %s
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
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s,
                %s, %s, %s
            )
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
            # 29 parâmetros — alinhados 1:1 com os %s acima
            str(store_id),                          # 1  store_id
            safe_int(prod.get('id')),               # 2  nuvemshop_id
            name_pt,                                # 3  name_pt
            name_es,                                # 4  name_es
            name_en,                                # 5  name_en
            slug,                                   # 6  slug
            description,                            # 7  description
            safe_str(prod.get('brand')),            # 8  brand
            len(variants),                          # 9  variant_count
            categories[0] if categories and isinstance(categories[0], (int, float)) else None,  # 10 category_id
            None,                                   # 11 category_name
            safe_float(prod.get('price'), 100),     # 12 price
            safe_float(prod.get('compare_at_price'), 100),  # 13 compare_at_price
            safe_float(prod.get('cost'), 100),      # 14 cost_price
            safe_float(prod.get('weight')),          # 15 weight
            safe_str(prod.get('weight_unit', 'g')),  # 16 weight_unit
            safe_float(prod.get('width')),           # 17 width
            safe_float(prod.get('height')),          # 18 height
            safe_float(prod.get('depth')),           # 19 depth
            skus[0] if skus and isinstance(skus[0], str) else None,  # 20 sku
            safe_int(prod.get('stock')),             # 21 stock
            safe_bool(prod.get('stock_management')), # 22 stock_management
            safe_bool(prod.get('published')),        # 23 is_published
            safe_bool(prod.get('requires_shipping')),# 24 requires_shipping
            len(images),                             # 25 images_count
            images[0].get('src') if images and isinstance(images[0], dict) else (safe_str(images[0]) if images else None),  # 26 thumbnail_url
            prod.get('created_at'),                  # 27 created_at_api
            prod.get('updated_at'),                  # 28 updated_at_api
            now,                                     # 29 synced_at
        ))
    conn.commit()
    cur.close()
    conn.close()
    return len(all_products)
# 
# SYNC: PRODUCT VARIANTS
# 
def sync_product_variants(store_id, access_token):
    """Sincroniza variantes de todos os produtos já cadastrados."""
    headers = make_api_headers(access_token)
    url_base = f"{NUVEMSHOP_API_BASE}/{store_id}/products"
    conn = get_db_connection()
    cur = conn.cursor()
    now = datetime.now()
    # Buscar todos os nuvemshop_id de produtos já sincronizados
    cur.execute("SELECT nuvemshop_id FROM products WHERE store_id = %s", (str(store_id),))
    product_ids = [row[0] for row in cur.fetchall()]
    total_variants = 0
    for prod_id in product_ids:
        # Buscar o produto completo na API (com variants)
        url = f"{url_base}/{prod_id}"
        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            app.logger.error(f"Erro ao buscar produto {prod_id}: {resp.status_code}")
            continue
        prod_data = resp.json()
        variants = prod_data.get('variants', [])
        if not isinstance(variants, list):
            variants = []
        for variant in variants:
            variant_values = variant.get('values', [])
            if not isinstance(variant_values, list):
                variant_values = []
            # Extrair valores das variantes (tamanho, cor, etc)
            clean_values = []
            for val in variant_values:
                if isinstance(val, dict):
                    clean_values.append({
                        'pt': val.get('pt'),
                        'es': val.get('es'),
                        'en': val.get('en')
                    })
                else:
                    clean_values.append(str(val))
            price = variant.get('price', 0) or 0
            compare_price = variant.get('compare_at_price', 0) or 0
            cost = variant.get('cost', 0) or 0
            cur.execute("""
                INSERT INTO product_variants (
                    store_id, product_id, nuvemshop_variant_id,
                    variant_values, sku, barcode,
                    price, compare_at_price, cost_price,
                    weight, weight_unit, stock, stock_management,
                    position, is_default,
                    created_at_api, updated_at_api, synced_at
                ) VALUES (
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s
                )
                ON CONFLICT (store_id, nuvemshop_variant_id) DO UPDATE SET
                    variant_values = EXCLUDED.variant_values,
                    sku = EXCLUDED.sku,
                    price = EXCLUDED.price,
                    compare_at_price = EXCLUDED.compare_at_price,
                    cost_price = EXCLUDED.cost_price,
                    stock = EXCLUDED.stock,
                    stock_management = EXCLUDED.stock_management,
                    position = EXCLUDED.position,
                    updated_at_api = EXCLUDED.updated_at_api,
                    synced_at = EXCLUDED.synced_at
            """, (
                str(store_id),
                prod_id,
                safe_int(variant.get('id')),
                json.dumps(clean_values) if clean_values else None,
                safe_str(variant.get('sku')),
                safe_str(variant.get('barcode')),
                safe_float(price, 100),
                safe_float(compare_price, 100),
                safe_float(cost, 100),
                safe_float(variant.get('weight', 0)),
                safe_str(variant.get('weight_unit', 'g')),
                safe_int(variant.get('stock', 0)),
                safe_bool(variant.get('stock_management')),
                safe_int(variant.get('position', 0)),
                variant.get('default', False) if isinstance(variant.get('default'), bool) else False,
                variant.get('created_at'),
                variant.get('updated_at'),
                now
            ))
            total_variants += 1
        # Atualizar o produto pai com preço/estoque agregados da primeira variante
        if variants:
            first_variant = variants[0]
            v_price = safe_float(first_variant.get('price', 0), 100)
            v_stock = sum(safe_int(v.get('stock', 0)) for v in variants if isinstance(v, dict))
            v_sku = safe_str(first_variant.get('sku')) if first_variant.get('sku') else None
            cur.execute("""
                UPDATE products 
                SET price = %s, stock = %s, sku = %s
                WHERE store_id = %s AND nuvemshop_id = %s
            """, (v_price, v_stock, v_sku, str(store_id), prod_id))
    conn.commit()
    cur.close()
    conn.close()
    return total_variants
# 
# SYNC: CUSTOMERS
# 
def sync_customers(store_id, access_token):
    headers = make_api_headers(access_token)
    url = f"{NUVEMSHOP_API_BASE}/{store_id}/customers"
    all_customers = []
    page = 1
    per_page = 100
    while True:
        params = {'page': page, 'per_page': per_page}
        resp = requests.get(url, headers=headers, params=params)
        # 404 = não há mais páginas — parar sem erro
        if resp.status_code == 404:
            break
        if resp.status_code != 200:
            app.logger.error(f"Erro ao buscar clientes (página {page}): {resp.status_code} - {resp.text}")
            break
        data = resp.json()
        if not data:
            break
        all_customers.extend(data)
        # Se veio menos que per_page, é a última página
        if len(data) < per_page:
            break
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
    per_page = 50
    while True:
        params = {'page': page, 'per_page': per_page}
        resp = requests.get(url, headers=headers, params=params)
        # 404 = não há mais páginas — parar sem erro
        if resp.status_code == 404:
            break
        if resp.status_code != 200:
            app.logger.error(f"Erro ao buscar pedidos (página {page}): {resp.status_code} - {resp.text}")
            break
        data = resp.json()
        if not data:
            break
        all_orders.extend(data)
        # Se veio menos que per_page, é a última página
        if len(data) < per_page:
            break
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
# ENDPOINT: SYNC ALL (atualizado)
@app.route('/api/sync/<store_id>', methods=['POST'])
def sync_all(store_id):
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
        results['product_variants'] = sync_product_variants(store_id, access_token)
    except Exception as e:
        errors.append(f'product_variants: {str(e)}')
        results['product_variants'] = 0
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
# ENDPOINT: SYNC por entidade (atualizado)
@app.route('/api/sync/<store_id>/<entity>', methods=['POST'])
def sync_entity(store_id, entity):
    try:
        access_token = get_active_store_token(store_id)
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    sync_map = {
        'categories': sync_categories,
        'products': sync_products,
        'product_variants': sync_product_variants,
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
