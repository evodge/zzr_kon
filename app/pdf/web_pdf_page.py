import os
import io
import uuid
import math
import base64
import zipfile
import cv2
import numpy as np
import fitz  # PyMuPDF
import gc
from PIL import Image, ImageOps
from flask import Flask, request, send_file, render_template_string, jsonify

# Tesseract OCRのインポートチェック (Linux/Raspberry Pi用)
try:
    import pytesseract
    HAS_TESSERACT = True
except ImportError:
    HAS_TESSERACT = False

app = Flask(__name__)

# アップロード・処理用の一時フォルダ
UPLOAD_FOLDER = './uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# 一時データのキャッシュ管理
EXTRACTED_IMAGES_CACHE = {}
REORDER_SESSIONS_CACHE = {}

# ==========================================
# 共通画像処理関数（傾き補正・切り出し・OCR）
# ==========================================
def get_angle_from_lines(image):
    """直線検出による傾き角度算出"""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(edges, 1, np.pi/720, threshold=100, minLineLength=300, maxLineGap=20)

    angles = []
    if lines is not None:
        for line in lines:
            x1, y1, x2, y2 = line.flatten()
            angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
            if -10 < angle < 10:
                angles.append(angle)
            elif 80 < angle < 100:
                angles.append(angle - 90)
            elif -100 < angle < -80:
                angles.append(angle + 90)

    return np.median(angles) if len(angles) > 0 else 0.0

def rotate_image(image, angle):
    """画像の回転処理（背景白）"""
    if abs(angle) < 0.01:
        return image
    (h, w) = image.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(image, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))

def clean_ocr_text(text):
    """OCRテキストの不要な半角スペース整形"""
    import re
    text = re.sub(r' +', ' ', text)
    jp_pattern = r'[ぁ-んァ-ヶー一-龠々〆ヵヶ]'
    text = re.sub(f'(?<={jp_pattern}) (?={jp_pattern})', '', text)
    text = re.sub(r'(?<=\d) (?=\d)', '', text)
    text = re.sub(f'(?<={jp_pattern}) (?=[a-zA-Z0-9])', '', text)
    text = re.sub(f'(?<=[a-zA-Z0-9]) (?={jp_pattern})', '', text)
    return text

def detect_and_crop_photos(pil_img):
    """OpenCVによる写真・図形領域の自動切り出し"""
    img_np = np.array(pil_img)
    if len(img_np.shape) == 2:
        img_bgr = cv2.cvtColor(img_np, cv2.COLOR_GRAY2BGR)
    elif img_np.shape[2] == 4:
        img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGBA2BGR)
    else:
        img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)

    h_orig, w_orig = img_bgr.shape[:2]
    page_area = h_orig * w_orig

    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = cv2.Canny(blurred, 30, 150)

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    dilated = cv2.dilate(edged, kernel, iterations=2)

    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    cropped_list = []
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        area = w * h

        if w > 120 and h > 120 and (area < page_area * 0.88) and (area > 10000):
            roi = img_bgr[y:y+h, x:x+w]
            if np.std(roi) > 15.0:
                crop_bgr = img_bgr[y:y+h, x:x+w]
                crop_rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
                cropped_list.append(Image.fromarray(crop_rgb))

    return cropped_list if len(cropped_list) > 0 else [pil_img]


# ==========================================
# フロントエンド (HTML/CSS/JS)
# ==========================================
HTML_PAGE = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <title>PDF Utility Web Station</title>
    <style>
        body { font-family: 'Helvetica Neue', Arial, sans-serif; background-color: #f5f7fa; color: #333; max-width: 900px; margin: 30px auto; padding: 20px; }
        h2 { color: #2c3e50; text-align: center; margin-bottom: 5px; }
        .subtitle { text-align: center; color: #7f8c8d; margin-bottom: 25px; font-size: 0.9em; }
        .tabs { display: flex; border-bottom: 2px solid #ddd; margin-bottom: 20px; flex-wrap: wrap; }
        .tab-btn { background: none; border: none; padding: 12px 18px; font-size: 14px; cursor: pointer; color: #666; transition: all 0.3s; border-bottom: 3px solid transparent; }
        .tab-btn:hover { color: #3498db; }
        .tab-btn.active { color: #3498db; font-weight: bold; border-bottom: 3px solid #3498db; }
        .tab-content { background: white; padding: 25px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); display: none; }
        .box { border: 2px dashed #cbd5e1; padding: 25px 20px; text-align: center; background: #f8fafc; border-radius: 8px; margin-bottom: 20px; }
        .btn-primary { background: #3498db; color: white; border: none; padding: 12px 24px; font-size: 15px; border-radius: 4px; cursor: pointer; font-weight: bold; width: 100%; transition: background 0.2s; margin-top: 10px; }
        .btn-primary:hover { background: #2980b9; }
        .status-msg { display: none; color: #e67e22; font-weight: bold; margin-top: 15px; text-align: center; font-size: 0.95em; }
        .reorder-wrapper { display: flex; gap: 20px; margin-top: 15px; text-align: left; }
        .reorder-left { flex: 1; min-width: 300px; }
        .reorder-right { flex: 1; min-width: 300px; display: flex; flex-direction: column; align-items: center; }
        .page-listbox { width: 100%; height: 280px; padding: 8px; border: 1px solid #cbd5e1; border-radius: 4px; font-size: 13px; margin-bottom: 10px; }
        .btn-row { display: flex; gap: 8px; margin-bottom: 15px; flex-wrap: wrap; }
        .btn-sub { background: #ecf0f1; border: 1px solid #bdc3c7; padding: 8px 12px; border-radius: 4px; cursor: pointer; font-size: 13px; font-weight: bold; flex: 1; text-align: center; }
        .btn-sub:hover { background: #dcdde1; }
        .btn-danger { background: #e74c3c; color: white; border: none; }
        .btn-danger:hover { background: #c0392b; }
        .preview-container { width: 100%; height: 320px; border: 1px solid #cbd5e1; background: #555; position: relative; overflow: hidden; border-radius: 4px; cursor: grab; }
        .preview-container:active { cursor: grabbing; }
        .preview-img { position: absolute; top: 0; left: 0; transform-origin: 0 0; transition: transform 0.05s ease-out; max-width: none; }
        .preview-controls { display: flex; gap: 8px; margin-top: 8px; width: 100%; justify-content: center; }
        .img-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; margin: 15px 0; max-height: 380px; overflow-y: auto; padding: 10px; border: 1px solid #e2e8f0; border-radius: 6px; background: #fafafa; }
        .img-card { background: white; border: 1px solid #cbd5e1; border-radius: 6px; padding: 8px; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.02); }
        .img-card img { max-width: 100%; height: 90px; object-fit: contain; border-radius: 4px; background: #eee; }
        .img-card label { display: block; margin-top: 6px; font-size: 11px; font-weight: bold; cursor: pointer; word-break: break-all; }
        .gallery-actions { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
        .opt-panel { background: #edf2f7; padding: 12px; border-radius: 6px; margin-top: 15px; font-size: 13px; }
        .opt-panel select { padding: 4px 8px; font-size: 13px; border-radius: 4px; border: 1px solid #ccc; }
    </style>
</head>
<body>

    <h2>📄 総合PDF処理ツール</h2>
    <div class="subtitle">Web Server Edition</div>

    <div class="tabs">
        <button class="tab-btn active" onclick="openTab(event, 'tab-deskew')">📄 傾き自動補正</button>
        <button class="tab-btn" onclick="openTab(event, 'tab-ocr')">🔤 OCRテキスト抽出</button>
        <button class="tab-btn" onclick="openTab(event, 'tab-reorder')">🔄 ページの並び替え・結合</button>
        <button class="tab-btn" onclick="openTab(event, 'tab-extract-img')">🖼️ 画像切り出し・選択保存</button>
    </div>

    <!-- タブ1: 傾き補正 -->
    <div id="tab-deskew" class="tab-content" style="display: block;">
        <p><b>【自動傾き補正】</b> 画像化されたPDFの罫線をスキャンし、水平に自動補正します。</p>
        <div class="box">
            <input type="file" id="deskew-file" accept=".pdf"><br>
            <button class="btn-primary" onclick="processDeskew()">傾き補正を実行してダウンロード</button>
            <div id="deskew-status" class="status-msg"></div>
        </div>
    </div>

    <!-- タブ2: OCR -->
    <div id="tab-ocr" class="tab-content">
        <p><b>【内蔵OCR】</b> 日本語テキストを抽出し、テキストファイルとして出力します。</p>
        <div class="box">
            <input type="file" id="ocr-file" accept=".pdf"><br>
            <button class="btn-primary" onclick="processOCR()">OCR処理を開始してダウンロード</button>
            <div id="ocr-status" class="status-msg"></div>
        </div>
    </div>

    <!-- タブ3: ページの並び替え・結合 -->
    <div id="tab-reorder" class="tab-content">
        <p><b>【並び替え・複数PDF結合】</b> 複数のPDFを読み込み、ページの追加・削除・順序変更を行えます。</p>
        <div class="box" style="padding:15px;">
            <input type="file" id="reorder-files" accept=".pdf" multiple>
            <div style="margin-top: 10px;">
                <button class="btn-sub" style="background:#34495e; color:white; border:none; display:inline-block; width:auto; padding:8px 20px;" onclick="loadPDFsForReorder(false)">新規読み込み</button>
                <button class="btn-sub" style="background:#27ae60; color:white; border:none; display:inline-block; width:auto; padding:8px 20px;" onclick="loadPDFsForReorder(true)">＋ 後から追加読み込み</button>
            </div>
            <div id="reorder-status" class="status-msg" style="display:block; color:#7f8c8d;">PDFファイルを1つ以上選択してください。（複数選択可）</div>
        </div>

        <div id="reorder-workspace" style="display:none;">
            <div class="reorder-wrapper">
                <div class="reorder-left">
                    <label style="font-weight:bold; font-size:13px;">📄 構成ページ一覧:</label>
                    <select id="page-listbox" class="page-listbox" size="12" onchange="onPageSelected()"></select>
                    <div class="btn-row">
                        <button class="btn-sub" onclick="moveUp()">▲ 上へ</button>
                        <button class="btn-sub" onclick="moveDown()">▼ 下へ</button>
                        <button class="btn-sub btn-danger" onclick="deleteSelectedPage()">🗑️ 削除</button>
                    </div>
                </div>

                <div class="reorder-right">
                    <label style="font-weight:bold; font-size:13px; margin-bottom: 4px;">🔍 選択ページのプレビュー:</label>
                    <div id="preview-container" class="preview-container">
                        <img id="preview-img" class="preview-img" src="" alt="プレビュー" style="display:none;">
                    </div>
                    <div class="preview-controls">
                        <button class="btn-sub" onclick="zoomPreview(1.2)">➕ 拡大</button>
                        <button class="btn-sub" onclick="zoomPreview(0.8)">➖ 縮小</button>
                        <button class="btn-sub" onclick="resetPreview()">🔄 リセット</button>
                    </div>
                </div>
            </div>

            <div class="opt-panel">
                <label style="font-weight:bold;">
                    <input type="checkbox" id="add-page-num" onchange="togglePageNumOptions()"> 新しくページ番号を印字する
                </label>
                <span id="page-num-style-box" style="margin-left: 15px; display:none;">
                    形式: 
                    <select id="page-num-style">
                        <option value="num">1, 2, 3 ...</option>
                        <option value="hyphen">- 1 -, - 2 - ...</option>
                        <option value="fraction">1 / 15, 2 / 15 ...</option>
                        <option value="page_of">Page 1 of 15 ...</option>
                    </select>
                </span>
            </div>
            <button class="btn-primary" style="background:#2ecc71; margin-top:20px;" onclick="saveReorderedPDF()">結合・並び替えを実行してダウンロード</button>
        </div>
    </div>

    <!-- タブ4: 画像切り出し -->
    <div id="tab-extract-img" class="tab-content">
        <p><b>【画像切り出し】</b> スキャンPDF等の文章を無視し、写真やイラストの枠を自動判定して切り出します。</p>
        <div class="box">
            <input type="file" id="extract-img-file" accept=".pdf">
            <button class="btn-sub" style="background:#34495e; color:white; border:none;" onclick="loadImagesFromPDF()">1. 写真・図を自動切り出し解析</button>
            <div id="extract-img-status" class="status-msg" style="display:block; color:#7f8c8d;">PDFファイルを指定してください。</div>
            
            <div id="gallery-area" style="display:none; margin-top: 15px;">
                <div class="gallery-actions">
                    <button class="btn-sub" onclick="toggleSelectAllImages(true)">全選択</button>
                    <button class="btn-sub" onclick="toggleSelectAllImages(false)">全解除</button>
                    <span id="img-count-badge" style="font-size:0.9em; font-weight:bold; color:#2c3e50;"></span>
                </div>
                <div id="img-grid" class="img-grid"></div>
                <button class="btn-primary" style="background:#e67e22;" onclick="downloadSelectedImages()">2. 選択した画像をZIPで保存</button>
            </div>
        </div>
    </div>

    <script>
        function openTab(evt, tabId) {
            const contents = document.getElementsByClassName("tab-content");
            for (let i = 0; i < contents.length; i++) contents[i].style.display = "none";
            const buttons = document.getElementsByClassName("tab-btn");
            for (let i = 0; i < buttons.length; i++) buttons[i].classList.remove("active");
            document.getElementById(tabId).style.display = "block";
            evt.currentTarget.classList.add("active");
        }

        function processDeskew() {
            const fileInput = document.getElementById('deskew-file');
            if (fileInput.files.length === 0) { alert('PDFファイルを選択してください。'); return; }
            const formData = new FormData();
            formData.append('pdf_file', fileInput.files[0]);
            const status = document.getElementById('deskew-status');
            status.style.display = 'block'; status.innerText = '⏳ 傾きを自動補正中...';
            
            fetch('./deskew', { method: 'POST', body: formData })
            .then(res => { if(!res.ok) throw new Error('補正処理に失敗しました。'); return res.blob(); })
            .then(blob => {
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url; a.download = "deskewed_" + fileInput.files[0].name;
                document.body.appendChild(a); a.click(); a.remove();
                status.innerText = '✅ 補正が完了しました！';
            })
            .catch(err => { alert(err.message); status.innerText = '❌ エラーが発生しました。'; });
        }

        function processOCR() {
            const fileInput = document.getElementById('ocr-file');
            if (fileInput.files.length === 0) { alert('PDFファイルを選択してください。'); return; }
            const formData = new FormData();
            formData.append('pdf_file', fileInput.files[0]);
            const status = document.getElementById('ocr-status');
            status.style.display = 'block'; status.innerText = '⏳ OCR文字解析中...';
            
            fetch('./ocr', { method: 'POST', body: formData })
            .then(res => { if(!res.ok) throw new Error('OCR処理に失敗しました。'); return res.blob(); })
            .then(blob => {
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url; a.download = fileInput.files[0].name.replace('.pdf', '_ocr.txt');
                document.body.appendChild(a); a.click(); a.remove();
                status.innerText = '✅ OCR抽出が完了しました！';
            })
            .catch(err => { alert(err.message); status.innerText = '❌ エラーが発生しました。'; });
        }

        let reorderSessionId = "";
        let pageDataList = [];

        function loadPDFsForReorder(isAppend) {
            const fileInput = document.getElementById('reorder-files');
            if (fileInput.files.length === 0) { alert('PDFファイルを1つ以上選択してください。'); return; }
            const formData = new FormData();
            for (let i = 0; i < fileInput.files.length; i++) formData.append('pdf_files', fileInput.files[i]);
            if (isAppend && reorderSessionId) formData.append('session_id', reorderSessionId);

            const status = document.getElementById('reorder-status');
            status.innerText = '⏳ PDFをスキャン＆解析中...';

            fetch('./reorder_load', { method: 'POST', body: formData })
            .then(res => res.json())
            .then(data => {
                if (data.error) { alert(data.error); status.innerText = 'エラー'; return; }
                reorderSessionId = data.session_id;
                pageDataList = isAppend ? pageDataList.concat(data.pages) : data.pages;
                renderPageListbox();
                document.getElementById('reorder-workspace').style.display = 'block';
                status.innerText = `✅ 読み込み完了: 現在合計 ${pageDataList.length} ページ`;
            })
            .catch(err => { status.innerText = '❌ 読み込みエラーが発生しました。'; });
        }

        function renderPageListbox() {
            const listbox = document.getElementById('page-listbox');
            const selectedIdx = listbox.selectedIndex;
            listbox.innerHTML = '';
            pageDataList.forEach((item, idx) => {
                const opt = document.createElement('option');
                opt.value = idx; opt.text = `${item.label}`;
                listbox.appendChild(opt);
            });
            if (pageDataList.length > 0) {
                const newIdx = (selectedIdx >= 0 && selectedIdx < pageDataList.length) ? selectedIdx : 0;
                listbox.selectedIndex = newIdx;
                onPageSelected();
            } else {
                document.getElementById('preview-img').style.display = 'none';
            }
        }

        function moveUp() {
            const listbox = document.getElementById('page-listbox');
            const idx = listbox.selectedIndex;
            if (idx <= 0) return;
            const temp = pageDataList[idx]; pageDataList[idx] = pageDataList[idx - 1]; pageDataList[idx - 1] = temp;
            renderPageListbox(); listbox.selectedIndex = idx - 1; onPageSelected();
        }

        function moveDown() {
            const listbox = document.getElementById('page-listbox');
            const idx = listbox.selectedIndex;
            if (idx < 0 || idx >= pageDataList.length - 1) return;
            const temp = pageDataList[idx]; pageDataList[idx] = pageDataList[idx + 1]; pageDataList[idx + 1] = temp;
            renderPageListbox(); listbox.selectedIndex = idx + 1; onPageSelected();
        }

        function deleteSelectedPage() {
            const listbox = document.getElementById('page-listbox');
            const idx = listbox.selectedIndex;
            if (idx < 0) return;
            pageDataList.splice(idx, 1);
            renderPageListbox();
            document.getElementById('reorder-status').innerText = `ページを削除しました。（残り ${pageDataList.length} ページ）`;
        }

        function togglePageNumOptions() {
            const chk = document.getElementById('add-page-num').checked;
            document.getElementById('page-num-style-box').style.display = chk ? 'inline' : 'none';
        }

        let scale = 1.0, pointX = 0, pointY = 0, startX = 0, startY = 0, isDragging = false;

        function onPageSelected() {
            const listbox = document.getElementById('page-listbox');
            const idx = listbox.selectedIndex;
            if (idx < 0 || !pageDataList[idx]) return;
            const imgEl = document.getElementById('preview-img');
            imgEl.src = pageDataList[idx].thumb;
            imgEl.style.display = 'block';
            resetPreview();
        }

        function updateTransform() {
            const imgEl = document.getElementById('preview-img');
            imgEl.style.transform = `translate(${pointX}px, ${pointY}px) scale(${scale})`;
        }

        function zoomPreview(factor) {
            scale *= factor; scale = Math.min(Math.max(0.3, scale), 5.0);
            updateTransform();
        }

        function resetPreview() {
            scale = 1.0; pointX = 20; pointY = 20; updateTransform();
        }

        const pContainer = document.getElementById('preview-container');
        pContainer.addEventListener('mousedown', (e) => {
            e.preventDefault(); startX = e.clientX - pointX; startY = e.clientY - pointY; isDragging = true;
        });
        window.addEventListener('mousemove', (e) => {
            if (!isDragging) return; pointX = e.clientX - startX; pointY = e.clientY - startY; updateTransform();
        });
        window.addEventListener('mouseup', () => { isDragging = false; });
        pContainer.addEventListener('wheel', (e) => {
            e.preventDefault();
            const xs = (e.clientX - pointX) / scale, ys = (e.clientY - pointY) / scale;
            (e.deltaY < 0) ? scale *= 1.1 : scale /= 1.1;
            scale = Math.min(Math.max(0.3, scale), 5.0);
            pointX = e.clientX - xs * scale; pointY = e.clientY - ys * scale;
            updateTransform();
        });

        function saveReorderedPDF() {
            if (pageDataList.length === 0) { alert('保存するページがありません。'); return; }
            const addPageNum = document.getElementById('add-page-num').checked;
            const pageNumStyle = document.getElementById('page-num-style').value;
            const status = document.getElementById('reorder-status');
            status.innerText = '⏳ 結合＆再構成PDFを出力中...';

            fetch('./reorder_save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: reorderSessionId, page_order: pageDataList, add_page_num: addPageNum, page_num_style: pageNumStyle })
            })
            .then(res => { if(!res.ok) throw new Error('PDFの再構成に失敗しました。'); return res.blob(); })
            .then(blob => {
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url; a.download = "combined_reordered.pdf";
                document.body.appendChild(a); a.click(); a.remove();
                status.innerText = '✅ 保存が完了しました！';
            })
            .catch(err => { alert(err.message); status.innerText = '❌ エラーが発生しました。'; });
        }

        let currentExtractFileId = "";

        function loadImagesFromPDF() {
            const fileInput = document.getElementById('extract-img-file');
            if (fileInput.files.length === 0) { alert('PDFファイルを選択してください。'); return; }
            const formData = new FormData();
            formData.append('pdf_file', fileInput.files[0]);
            const status = document.getElementById('extract-img-status');
            document.getElementById('gallery-area').style.display = 'none';
            status.innerText = '⏳ PDF内の写真をOpenCVで自動切出・解析中...';

            fetch('./extract_img_init', { method: 'POST', body: formData })
            .then(res => res.json())
            .then(data => {
                if (data.error) { alert(data.error); status.innerText = 'エラー'; return; }
                if (data.images.length === 0) { status.innerText = '⚠️ 写真・イラスト領域が検出できませんでした。'; return; }

                currentExtractFileId = data.file_id;
                const grid = document.getElementById('img-grid');
                grid.innerHTML = '';
                
                data.images.forEach((img, idx) => {
                    const card = document.createElement('div');
                    card.className = 'img-card';
                    card.innerHTML = `
                        <img src="${img.src}" alt="img_${idx}">
                        <label><input type="checkbox" class="img-chk" value="${img.id}" checked> P.${img.page} - 画像 ${idx + 1}</label>
                    `;
                    grid.appendChild(card);
                });

                document.getElementById('img-count-badge').innerText = `検出画像: ${data.images.length} 点`;
                document.getElementById('gallery-area').style.display = 'block';
                status.innerText = `✅ 写真・図の自動切り出しが完了しました。`;
            })
            .catch(err => { status.innerText = '❌ 抽出処理中にエラーが発生しました。'; });
        }

        function toggleSelectAllImages(selectAll) {
            document.querySelectorAll('.img-chk').forEach(chk => chk.checked = selectAll);
        }

        function downloadSelectedImages() {
            const checkboxes = document.querySelectorAll('.img-chk:checked');
            if (checkboxes.length === 0) { alert('保存したい画像を1つ以上選択してください。'); return; }

            const selectedIds = Array.from(checkboxes).map(chk => parseInt(chk.value));
            const status = document.getElementById('extract-img-status');
            status.innerText = '⏳ 選択画像をZIPに圧縮中...';

            fetch('./extract_img_save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ file_id: currentExtractFileId, selected_ids: selectedIds })
            })
            .then(res => { if(!res.ok) throw new Error('ZIP作成に失敗しました。'); return res.blob(); })
            .then(blob => {
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url; a.download = "extracted_images.zip";
                document.body.appendChild(a); a.click(); a.remove();
                status.innerText = '✅ ZIPファイルのダウンロードが完了しました！';
            })
            .catch(err => { alert(err.message); status.innerText = '❌ ダウンロードエラーが発生しました。'; });
        }
    </script>
</body>
</html>
"""

# ==========================================
# Flask ルーティング
# ==========================================
@app.route('/', methods=['GET'])
def index():
    return render_template_string(HTML_PAGE)

@app.route('/deskew', methods=['POST'])
def deskew_process():
    if 'pdf_file' not in request.files: return "ファイル不足", 400
    file = request.files['pdf_file']
    
    in_path = os.path.join(UPLOAD_FOLDER, f"deskew_in_{uuid.uuid4().hex}.pdf")
    out_path = os.path.join(UPLOAD_FOLDER, f"deskew_out_{uuid.uuid4().hex}.pdf")
    file.save(in_path)
    
    try:
        doc = fitz.open(in_path)
        output_doc = fitz.Document()
        
        mat_calc = fitz.Matrix(0.5, 0.5)
        mat_render = fitz.Matrix(1.5, 1.5)
        
        for i in range(len(doc)):
            page = doc.load_page(i)
            
            pix_calc = page.get_pixmap(matrix=mat_calc, alpha=False)
            img_calc = np.frombuffer(pix_calc.samples, dtype=np.uint8).reshape(pix_calc.height, pix_calc.width, pix_calc.n)
            if pix_calc.n == 3: img_calc = cv2.cvtColor(img_calc, cv2.COLOR_RGB2BGR)
            elif pix_calc.n == 1: img_calc = cv2.cvtColor(img_calc, cv2.COLOR_GRAY2BGR)
            angle = get_angle_from_lines(img_calc)
            
            pix = page.get_pixmap(matrix=mat_render, alpha=False)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if pix.n == 3: img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            elif pix.n == 1: img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            
            corrected_img = rotate_image(img, angle)
            
            is_success, buffer = cv2.imencode(".png", corrected_img)
            if is_success:
                img_bytes = buffer.tobytes()
                out_page = output_doc.new_page(width=page.rect.width, height=page.rect.height)
                out_page.insert_image(out_page.rect, stream=img_bytes)
                
            del pix_calc, img_calc, pix, img, corrected_img
            gc.collect()
                
        output_doc.save(out_path, garbage=4, deflate=True)
        output_doc.close()
        doc.close()
        
        if os.path.exists(in_path): os.remove(in_path)
        return send_file(out_path, as_attachment=True, download_name=f"deskewed_{file.filename}")
    except Exception as e:
        if os.path.exists(in_path): os.remove(in_path)
        return str(e), 500

@app.route('/ocr', methods=['POST'])
def ocr_process():
    if not HAS_TESSERACT: return "tesseract-ocr is not installed.", 500
    if 'pdf_file' not in request.files: return "ファイル不足", 400
    file = request.files['pdf_file']
    
    in_path = os.path.join(UPLOAD_FOLDER, f"ocr_in_{uuid.uuid4().hex}.pdf")
    out_path = os.path.join(UPLOAD_FOLDER, f"ocr_out_{uuid.uuid4().hex}.txt")
    file.save(in_path)
    
    try:
        doc = fitz.open(in_path)
        mat = fitz.Matrix(1.5, 1.5)
        
        with open(out_path, "w", encoding="utf-8") as f:
            for i in range(len(doc)):
                f.write(f"--- PAGE {i + 1} ---\n")
                try:
                    page = doc.load_page(i)
                    pix = page.get_pixmap(matrix=mat, alpha=False)
                    pil_img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    
                    raw_text = pytesseract.image_to_string(pil_img, lang="jpn")
                    cleaned_text = clean_ocr_text(raw_text)
                    f.write(cleaned_text)
                    
                    del pix, pil_img
                    gc.collect()
                except Exception as page_e:
                    f.write(f"[Page extraction failed: {str(page_e)}]\n")
                f.write("\n\n")
                
        doc.close()
        if os.path.exists(in_path): os.remove(in_path)
        return send_file(out_path, as_attachment=True, download_name="ocr_result.txt")
    except Exception as e:
        if os.path.exists(in_path): os.remove(in_path)
        return str(e), 500

@app.route('/reorder_load', methods=['POST'])
def reorder_load():
    files = request.files.getlist('pdf_files')
    session_id = request.form.get('session_id', uuid.uuid4().hex)
    
    if session_id not in REORDER_SESSIONS_CACHE:
        REORDER_SESSIONS_CACHE[session_id] = {}

    session_files = REORDER_SESSIONS_CACHE[session_id]
    extracted_pages = []

    try:
        for file in files:
            file_key = uuid.uuid4().hex
            file_path = os.path.join(UPLOAD_FOLDER, f"reorder_{session_id}_{file_key}.pdf")
            file.save(file_path)
            session_files[file_key] = {"path": file_path, "filename": file.filename}

            doc = fitz.open(file_path)
            mat = fitz.Matrix(0.6, 0.6)

            for page_idx in range(len(doc)):
                page = doc[page_idx]
                pix = page.get_pixmap(matrix=mat, alpha=False)
                b64_str = base64.b64encode(pix.tobytes("png")).decode('utf-8')
                thumb_src = f"data:image/png;base64,{b64_str}"

                label_text = f"{file.filename} - {page_idx + 1}ページ"
                extracted_pages.append({
                    "file_key": file_key,
                    "orig_page_idx": page_idx,
                    "label": label_text,
                    "thumb": thumb_src
                })
            doc.close()

        return jsonify({"session_id": session_id, "pages": extracted_pages})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/reorder_save', methods=['POST'])
def reorder_save():
    data = request.json
    session_id = data.get('session_id')
    page_order = data.get('page_order', [])
    add_page_num = data.get('add_page_num', False)
    page_num_style = data.get('page_num_style', 'num')

    if session_id not in REORDER_SESSIONS_CACHE:
        return "セッション期限切れ", 400

    session_files = REORDER_SESSIONS_CACHE[session_id]
    out_doc = fitz.Document()

    try:
        opened_docs = {}
        for key, info in session_files.items():
            opened_docs[key] = fitz.open(info["path"])

        for item in page_order:
            fk = item["file_key"]
            p_idx = item["orig_page_idx"]
            if fk in opened_docs:
                out_doc.insert_pdf(opened_docs[fk], from_page=p_idx, to_page=p_idx)

        for doc_obj in opened_docs.values():
            doc_obj.close()

        if add_page_num:
            total_pages = len(out_doc)
            for page_num, page in enumerate(out_doc):
                p_val = page_num + 1
                if page_num_style == 'num': text = f"{p_val}"
                elif page_num_style == 'hyphen': text = f"- {p_val} -"
                elif page_num_style == 'fraction': text = f"{p_val} / {total_pages}"
                elif page_num_style == 'page_of': text = f"Page {p_val} of {total_pages}"
                else: text = f"{p_val}"

                rect = page.rect
                x = rect.width / 2
                y = rect.height - 20
                page.insert_text(fitz.Point(x, y), text, fontsize=10, color=(0, 0, 0), align=1)

        out_path = os.path.join(UPLOAD_FOLDER, f"combined_{session_id}.pdf")
        out_doc.save(out_path)
        out_doc.close()

        for key, info in session_files.items():
            if os.path.exists(info["path"]): os.remove(info["path"])
        del REORDER_SESSIONS_CACHE[session_id]

        return send_file(out_path, as_attachment=True, download_name="combined_reordered.pdf")
    except Exception as e:
        return str(e), 500

@app.route('/extract_img_init', methods=['POST'])
def extract_img_init():
    if 'pdf_file' not in request.files: return jsonify({"error": "ファイル不足"}), 400
    file = request.files['pdf_file']
    
    file_id = uuid.uuid4().hex
    file_path = os.path.join(UPLOAD_FOLDER, f"extract_{file_id}.pdf")
    file.save(file_path)
    
    try:
        doc = fitz.open(file_path)
        extracted_list = []
        img_id_counter = 0
        
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            image_list = page.get_images(full=True)
            
            for img_info in image_list:
                xref = img_info[0]
                base_image = doc.extract_image(xref)
                image_bytes = base_image["image"]
                image_ext = base_image["ext"]
                
                try:
                    pil_raw = Image.open(io.BytesIO(image_bytes))
                    pil_raw = ImageOps.exif_transpose(pil_raw)
                except Exception:
                    continue

                sub_images = detect_and_crop_photos(pil_raw)

                for sub_img in sub_images:
                    buf = io.BytesIO()
                    save_fmt = "PNG" if image_ext.lower() == "png" else "JPEG"
                    if save_fmt == "JPEG" and sub_img.mode != "RGB":
                        sub_img = sub_img.convert("RGB")
                    sub_img.save(buf, format=save_fmt)
                    out_bytes = buf.getvalue()

                    b64_str = base64.b64encode(out_bytes).decode('utf-8')
                    src = f"data:image/{image_ext};base64,{b64_str}"
                    
                    extracted_list.append({
                        "id": img_id_counter,
                        "page": page_idx + 1,
                        "ext": image_ext,
                        "bytes": out_bytes,
                        "src": src
                    })
                    img_id_counter += 1
                
        doc.close()
        if os.path.exists(file_path): os.remove(file_path)
        
        EXTRACTED_IMAGES_CACHE[file_id] = extracted_list
        response_images = [{"id": item["id"], "page": item["page"], "src": item["src"]} for item in extracted_list]
        return jsonify({"file_id": file_id, "images": response_images})
    except Exception as e:
        if os.path.exists(file_path): os.remove(file_path)
        return jsonify({"error": str(e)}), 500

@app.route('/extract_img_save', methods=['POST'])
def extract_img_save():
    data = request.json
    file_id = data.get('file_id')
    selected_ids = data.get('selected_ids', [])
    
    if file_id not in EXTRACTED_IMAGES_CACHE:
        return "データ期限切れ", 400
        
    images_data = EXTRACTED_IMAGES_CACHE[file_id]
    zip_path = os.path.join(UPLOAD_FOLDER, f"images_{file_id}.zip")
    
    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for item in images_data:
                if item["id"] in selected_ids:
                    filename = f"page_{item['page']}_crop_{item['id'] + 1}.{item['ext']}"
                    zf.writestr(filename, item["bytes"])
                    
        del EXTRACTED_IMAGES_CACHE[file_id]
        return send_file(zip_path, as_attachment=True, download_name="extracted_images.zip")
    except Exception as e:
        return str(e), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
