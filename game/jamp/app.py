from flask import Flask, request, jsonify, send_from_directory
import sqlite3
import os

app = Flask(__name__)
DB_FILE = "scores.db"

# データベースの初期化
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rankings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            score INTEGER NOT NULL
        )
    """)
    conn.commit()
    conn.close()

# CORS対策ヘッダーを付与
@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'POST, GET, OPTIONS'
    return response

# ルートアクセス時にメインメニュー（index.html）を返す
@app.route('/')
def index():
    return send_from_directory(os.getcwd(), 'index.html')

# 各種静的ファイルへのルーティングを整理
@app.route('/<path:filename>')
def serve_file(filename):
    # メインのindex.htmlに記載されたパスと実際の配置ファイルをマッピング
    if filename == 'jamp/jamp.html':
        return send_from_directory(os.getcwd(), 'jamp.html')
    elif filename == 'game_no8.html':
        return send_from_directory(os.getcwd(), 'exit_no8.html')
    return send_from_directory(os.getcwd(), filename)

# スコア登録API（安全な削除ロジックに修正）
@app.route('/api/score', methods=['POST'])
def add_score():
    data = request.json
    name = data.get("name", "Unknown")[:10]  # 最大10文字
    score = int(data.get("score", 0))
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 1. スコアを挿入
    cursor.execute("INSERT INTO rankings (name, score) VALUES (?, ?)", (name, score))
    conn.commit()
    
    # 2. 上位3番目のスコアを取得して、それ未満のデータを安全に削除（SQLiteエラー回避）
    cursor.execute("SELECT score FROM rankings ORDER BY score DESC LIMIT 1 OFFSET 2")
    row = cursor.fetchone()
    if row:
        cutoff_score = row[0]
        cursor.execute("DELETE FROM rankings WHERE score < ?", (cutoff_score,))
        conn.commit()
        
    conn.close()
    return jsonify({"status": "success"})

# ランキング取得API（上位3件）
@app.route('/api/ranking', methods=['GET'])
def get_ranking():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT name, score FROM rankings ORDER BY score DESC LIMIT 3")
    rows = cursor.fetchall()
    conn.close()
    
    ranking = [{"name": row[0], "score": row[1]} for row in rows]
    return jsonify(ranking)

if __name__ == '__main__':
    init_db()
    # 外部（スマホ）からアクセスできるように host='0.0.0.0' に設定
    app.run(host='0.0.0.0', port=5000, debug=True)
