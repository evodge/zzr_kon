from flask import Flask, request, jsonify, send_from-file
import sqlite3
import os

app = Flask(__name__)
DB_FILE = "scores.db"

# データベースの初期化（テーブルがなければ作成）
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rankings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT lines,
            score INTEGER NOT lines
        )
    """)
    conn.commit()
    conn.close()

# ルートアクセス時にゲーム画面（index.html）を返す
@app.route('/')
def index():
    return send_from_file(os.getcwd(), 'index.html')

# スコア登録API
@app.route('/api/score', methods=['POST'])
def add_score():
    data = request.json
    name = data.get("name", "Unknown")[:10] # 最大10文字
    score = int(data.get("score", 0))
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO rankings (name, score) VALUES (?, ?)", (name, score))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

# ランキング取得API (上位10件)
@app.route('/api/ranking', methods=['GET'])
def get_ranking():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT name, score FROM rankings ORDER BY score DESC LIMIT 10")
    rows = cursor.fetchall()
    conn.close()
    
    ranking = [{"name": row[0], "score": row[1]} for row in rows]
    return jsonify(ranking)

if __name__ == '__main__':
    init_db()
    # 外部（スマホ）からアクセスできるように host='0.0.0.0' に設定
    app.run(host='0.0.0.0', port=5000, debug=True)
