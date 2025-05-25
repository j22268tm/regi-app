from flask import Flask, render_template, request, redirect, url_for, make_response, jsonify
import redis
import csv
import io
from datetime import datetime
import os
from werkzeug.utils import secure_filename
import logging
logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
app.secret_key = 'your_secret_key'

# Redis接続
r = redis.StrictRedis(host='localhost', port=6379, db=0, decode_responses=True)

# 画像アップロード
UPLOAD_FOLDER = 'static/images'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# Allowed_file in 勉強がてら
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ホーム (メインのページ)
@app.route('/')
def index():
    # 商品一覧を取得
    product_keys = r.keys('product:*')
    products = []
    for key in product_keys: # idをひたすら投げるやつ
        product = r.hgetall(key)
        product['id'] = key.split(':')[1]
        products.append(product)
    return render_template('home.html', products=products)

# 商品を販売用のAPI -> 簡潔に書いたので許して
@app.route('/sell_product', methods=['POST'])
def sell_product():
    product_id = request.json.get('product_id')
    if not product_id:
        return jsonify({'success': False, 'message': '商品IDが提供されていません。'}), 400

    # 在庫チェック
    stock = int(r.hget(f'product:{product_id}', 'stock') or 0)
    if stock < 1:
        return jsonify({'success': False, 'message': '在庫が不足しています。'}), 400

    # 在庫の更新
    r.hincrby(f'product:{product_id}', 'stock', -1)

    # 更新後の残り在庫を取得
    remaining_stock = int(r.hget(f'product:{product_id}', 'stock') or 0)

    # 販売IDを自動生成
    sale_id = r.incr('sale_id')

    # 商品情報を取得
    product = r.hgetall(f'product:{product_id}')
    product_name = product.get('name', '不明な商品')
    product_price = product.get('price', '0')

    # 販売情報をRedisに保存
    sale_data = {
        'product_id': product_id,
        'product_name': product_name,
        'price': product_price,
        'quantity': 1,
        'remaining_stock': remaining_stock,  # 残り在庫を追加
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    r.hset(f'sale:{sale_id}', mapping=sale_data)

    return jsonify({'success': True, 'message': '販売が記録されました。', 'sale_id': sale_id, 'product_name': product_name})


# Undo実装 hincrbyとdeleteが動いてる・・だけ・・・
@app.route('/undo_sale', methods=['POST'])
def undo_sale():
    sale_id = request.json.get('sale_id')
    if not sale_id:
        return jsonify({'success': False, 'message': '販売IDが提供されていません。'}), 400

    sale_key = f'sale:{sale_id}'
    if not r.exists(sale_key):
        return jsonify({'success': False, 'message': '販売記録が見つかりません。'}), 404

    sale = r.hgetall(sale_key)
    product_id = sale['product_id']
    quantity = int(sale['quantity'])

    # 在庫を元に戻す
    r.hincrby(f'product:{product_id}', 'stock', quantity)

    # 販売記録を削除
    r.delete(sale_key)

    return jsonify({'success': True, 'message': '販売が取り消されました。'})

# 商品管理ページ
@app.route('/product-management', methods=['GET'])
def product_management():
    # 商品一覧を取得　時間なかったのでqiitaのやつ持ってきただけです。
    product_keys = r.keys('product:*')
    products = []
    for key in product_keys:
        product = r.hgetall(key)
        product['id'] = key.split(':')[1]
        products.append(product)
    return render_template('product_management.html', products=products)

# # sum-calculate
# @app.route('/sum_calculate', methods=['GET'])
# def sum_calculate();


# 商品追加
@app.route('/add_product', methods=['POST'])
def add_product():
    product_name = request.form['name']
    price = request.form['price']
    stock = request.form['stock']
    image = request.files.get('image')

    # データバリデーション -> DB破壊防止策 redisの場合はなんかしらのバリデーションが必要?
    if not product_name or not price or not stock:
        error = "商品名、価格、在庫数を入力してください"
        return redirect(url_for('product_management'))

    # product_IDを自動生成 -> あんまりうまくいってないから直す（多分）
    product_id = r.incr('product_id')

    # 商品情報をRedisに投げる
    r.hmset(f'product:{product_id}', {'name': product_name, 'price': price, 'stock': stock})

    # 画像の保存 -> allowed_file使いたかっただけ
    if image and allowed_file(image.filename):
        filename = secure_filename(f"{product_id}.{image.filename.rsplit('.', 1)[1].lower()}")
        image.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        # 画像パスを商品情報に追加
        r.hset(f'product:{product_id}', 'image', filename)
    else:
        # 画像がアップロードされなかった場合、デフォルトの画像を設定
        r.hset(f'product:{product_id}', 'image', 'default.png')

    return redirect(url_for('product_management'))

@app.route('/edit_product', methods=['POST'])
def edit_product():
    try:
        product_id = request.form['product_id']
        name = request.form.get('name')
        price = request.form.get('price')
        stock = request.form.get('stock')
        image = request.files.get('image')

        if not product_id:
            return jsonify({'success': False, 'message': '商品を選択してください'}), 400

        if name:
            r.hset(f'product:{product_id}', 'name', name)
        if price:
            r.hset(f'product:{product_id}', 'price', price)
        if stock:
            r.hset(f'product:{product_id}', 'stock', stock)
        
        if image and allowed_file(image.filename):
            old_image = r.hget(f'product:{product_id}', 'image')
            if old_image and old_image != 'default.png':
                old_image_path = os.path.join(app.config['UPLOAD_FOLDER'], old_image)
                if os.path.exists(old_image_path):
                    os.remove(old_image_path)

            filename = secure_filename(f"{product_id}.{image.filename.rsplit('.', 1)[1].lower()}")
            image.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            r.hset(f'product:{product_id}', 'image', filename)

        return jsonify({'success': True, 'message': '商品情報が更新されました'})

    except Exception as e:
        logging.exception("商品変更時にエラーが発生しました")
        return jsonify({'success': False, 'message': 'エラーが発生しました'}), 500

# 商品削除
@app.route('/delete_product', methods=['POST'])
def delete_product():
    product_id = request.form['product_id']

    # データバリデーション
    if not product_id:
        error = "商品を選択してください"
        return redirect(url_for('product_management'))

    # 商品情報を削除
    r.delete(f'product:{product_id}')

    return redirect(url_for('product_management'))

# 販売履歴ページ
@app.route('/sales-history')
def sales_history():
    # 販売データを取得
    sale_keys = r.keys('sale:*')
    sales = []
    for key in sale_keys:
        sale = r.hgetall(key)
        sales.append({
            'sale_id': int(key.split(':')[1]),
            'product_id': sale['product_id'],
            'product_name': sale.get('product_name', '不明な商品'),
            'price': sale.get('price', '0'),
            'quantity': sale['quantity'],
            'remaining_stock': sale.get('remaining_stock', 'N/A'),  # 残り在庫を取得
            'timestamp': sale['timestamp']
        })
    # Sale ID でソート（降順）
    sales.sort(key=lambda x: x['sale_id'], reverse=True)
    return render_template('sales_history.html', sales=sales)

# CSVエクスポート機能
@app.route('/export')
def export():
    # 販売データを取得
    sale_keys = r.keys('sale:*')
    sales = []
    for key in sale_keys:
        sale = r.hgetall(key)
        sales.append({
            'sale_id': key.split(':')[1],
            'product_id': sale['product_id'],
            'product_name': sale.get('product_name', '不明な商品'),
            'price': sale.get('price', '0'),
            'quantity': sale['quantity'],
            'remaining_stock': sale.get('remaining_stock', 'N/A'),  # 残り在庫を取得
            'timestamp': sale['timestamp']
        })

    # Sale ID でソート（オプション）
    sales.sort(key=lambda x: int(x['sale_id']), reverse=False)

    # CSVファイルを生成
    si = io.StringIO()
    cw = csv.writer(si)
    # ヘッダーに「Remaining Stock」を追加
    cw.writerow(['Sale ID', 'Product ID', '商品名', '値段', '数量', '在庫数', 'Timestamp'])
    for sale in sales:
        cw.writerow([
            sale['sale_id'],
            sale['product_id'],
            sale['product_name'],
            sale['price'],
            sale['quantity'],
            sale['remaining_stock'],  # 残り在庫を追加
            sale['timestamp']
        ])

    # UTF-8文字化け対策
    output = make_response(si.getvalue().encode('utf-8-sig'))
    output.headers["Content-Disposition"] = "attachment; filename=sales.csv"
    output.headers["Content-type"] = "text/csv; charset=UTF-8"
    return output


# 販売記録の削除
@app.route('/delete_sale', methods=['POST'])
def delete_sale():
    sale_id = request.form.get('sale_id')
    if not sale_id:
        return redirect(url_for('sales_history'))

    sale_key = f'sale:{sale_id}' # fallback to 
    if not r.exists(sale_key):
        return redirect(url_for('sales_history'))
    # TODO: Undo実装
    # sale = r.hgetall(sale_key)
    # product_id = sale['product_id']
    # quantity = int(sale['quantity'])
    # r.hincrby(f'product:{product_id}', 'stock', quantity)

    # 販売記録を削除
    r.delete(sale_key)

    return redirect(url_for('sales_history'))


# 設定・管理ページ
@app.route('/settings', methods=['GET'])
def settings():
    return render_template('settings.html')

# データベースのパージ機能
@app.route('/purge', methods=['POST'])
def purge():
    try:
        r.flushdb()
        message = 'データベースを全て消去しました。'
    except Exception as e:
        message = f'エラーが発生しました: {str(e)}'
    return render_template('settings.html', message=message)

if __name__ == '__main__':
    # 画像保存フォルダが存在しない場合は作成
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    app.run(debug=True)
