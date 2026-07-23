# 📄 PDF Utility Workstation

PDFファイルの傾き補正、OCRテキスト抽出、ページの並び替え・結合、およびOpenCVを用いた写真・図形の自動検出切り出しを行えるオールインワンツールです。
Webサーバー（Flask）およびデスクトップGUI（Tkinter）の2パターンで動作します。

---

## ✨ 主な機能

1. **📄 傾き自動補正**: スキャンされた画像PDFの罫線を検知し、水平に自動補正します。
2. **🔤 OCRテキスト抽出**: ノイズや不要なスペースを自動整形しながらテキストデータ化します。
3. **🔄 ページの並び替え・結合**: 複数PDF/画像を読み込み、自由なページ削除・入れ替え・番号印字が可能です。
4. **🖼️ 画像切り出し・選択保存**: OpenCVで「文章」と「写真枠・図形」を判別し、写真領域のみを自動カットして一括ZIP保存します。

---

## 🛠️ インストール方法

```bash
git clone [https://github.com/ユーザー名/pdf-utility-workstation.git](https://github.com/ユーザー名/pdf-utility-workstation.git)
cd pdf-utility-workstation
pip install -r requirements.txt
