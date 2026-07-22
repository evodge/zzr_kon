import math
import os
import re
import time
import plotly.graph_objects as go
import streamlit as st

# Gemini API ライブラリの読み込み
try:
  import google.generativeai as genai

  HAS_GENAI = True
except ImportError:
  HAS_GENAI = False

# ページ全体の基本設定
st.set_page_config(page_title="NCパスシミュレータ", layout="wide")

SAVE_FILE_PATH = "saved_code.nc"

# -------------------------------------------------------------------
# 複合固定サイクル網羅 サンプルプログラム集 (G70 ~ G76)
# -------------------------------------------------------------------
SAMPLE_PROGRAMS = {
    "【G71/G70】外径段付き・角R荒削り＆仕上げ": """G00 X100. Z100.
G00 X62. Z2.
G71 U2.0 R0.5
G71 P10 Q20 U0.4 W0.1 F0.25
N10 G00 X0.
G01 Z0. F0.15
X45.
G03 X55. Z-5. R5.
Z-40.
N20 X62.
G70 P10 Q20
G00 X100. Z100.""",
    "【G72/G70】端面荒削り＆仕上げ": """G00 X100. Z100.
G00 X65. Z2.
G72 W2.0 R0.5
G72 P10 Q20 U0.4 W0.1 F0.25
N10 G00 Z-50.
G01 X60. F0.15
Z-20. X40.
Z0. X20.
N20 Z2.
G70 P10 Q20
G00 X100. Z100.""",
    "【G73/G70】鋳物・鍛造用 閉ループ荒削り": """G00 X100. Z100.
G00 X65. Z5.
G73 U3.0 W1.5 R3
G73 P10 Q20 U0.5 W0.1 F0.25
N10 G00 X20. Z2.
G01 Z-15. F0.15
X35. Z-25.
Z-45.
N20 X60.
G70 P10 Q20
G00 X100. Z100.""",
    "【G74】端面溝入れ・深穴ペックドリル": """G00 X100. Z100.
G00 X0. Z5.
(深穴ペックドリル加工)
G74 R1.0
G74 Z-30. Q5000 F0.1
G00 Z5.
G00 X100. Z100.""",
    "【G75】外径多段溝入れ・突切り": """G00 X100. Z100.
G00 X55. Z-10.
(外径溝入れ加工)
G75 R0.5
G75 X30. Z-25. P2000 Q5000 F0.08
G00 X60.
G00 X100. Z100.""",
    "【G76】外径ねじ切り (M20 x P2.0)": """G00 X100. Z100.
G00 X25. Z5.
(M20 P2.0 ねじ切り)
G76 P010060 Q100 R0.02
G76 X17.402 Z-25. P1299 Q350 F2.0
G00 X100. Z100.""",
}

default_code = SAMPLE_PROGRAMS["【G71/G70】外径段付き・角R荒削り＆仕上げ"]

# 起動時に永久保存ファイルが存在すれば読み込み
if os.path.exists(SAVE_FILE_PATH):
  try:
    with open(SAVE_FILE_PATH, "r", encoding="utf-8") as f:
      initial_code = f.read()
  except Exception:
    initial_code = default_code
else:
  initial_code = default_code
  with open(SAVE_FILE_PATH, "w", encoding="utf-8") as f:
    f.write(default_code)

if "saved_nc_code" not in st.session_state:
  st.session_state.saved_nc_code = initial_code

if "autoscale_key" not in st.session_state:
  st.session_state.autoscale_key = 0

if "is_playing" not in st.session_state:
  st.session_state.is_playing = False

if "selected_step" not in st.session_state:
  st.session_state.selected_step = None

if "editor_height" not in st.session_state:
  st.session_state.editor_height = 380

if "chat_history" not in st.session_state:
  st.session_state.chat_history = []

# カスタムCSS
st.markdown(
    """
<style>
    .block-container {
        padding-top: 2.5rem !important;
        padding-bottom: 0.5rem !important;
    }
    h3 {
        margin-top: 0px !important;
        padding-top: 0px !important;
        font-size: 1.25rem !important;
    }
    .stTextArea textarea {
        font-family: 'Courier New', Consolas, monospace !important;
        font-size: 14px !important;
        line-height: 1.4 !important;
    }
    .active-code-box {
        background-color: #f8f9fa;
        border-left: 4px solid #d9534f;
        padding: 8px 12px;
        margin-top: 8px;
        border-radius: 4px;
        font-family: 'Courier New', Consolas, monospace;
        font-size: 15px;
        font-weight: bold;
        color: #212529;
    }
</style>
""",
    unsafe_allow_html=True,
)

# -------------------------------------------------------------------
# 🤖 サイドバー：AI対話型プログラム自動生成アシスタント
# -------------------------------------------------------------------
SYSTEM_INSTRUCTION = """
あなたはFANUC系NC旋盤のプログラミング作成・サポートのプロフェッショナルです。
ユーザーから加工したい形状（外径、長さ、角R、C面、溝入れ、ねじ切り、G71荒削り等）の指示を受けたら、正しいNCプログラム（Gコード）を作成してください。

【重要な出力ルール】
1. 生成するNCプログラムは、必ず ```nc ... ``` というコードブロック内に記述してください。
2. 直径指定（X軸）前提で作成してください。
3. コードの簡単な解説（加工手順のポイント）も添えてください。
"""

with st.sidebar:
  st.markdown("### 🤖 AIプログラム自動生成")
  st.caption("自然な言葉で形状を指定すると、AIがNCプログラムを自動作成します。")

  api_key_input = st.text_input(
      "Gemini APIキー",
      type="password",
      help="Google AI Studioで発行したAPIキーを入力してください。",
  )
  api_key = api_key_input or os.environ.get("GEMINI_API_KEY", "")

  if not HAS_GENAI:
    st.warning(
        "ライブラリが未インストールです。\n`pip install google-generativeai`"
        " を実行してください。"
    )
  elif not api_key:
    st.info(
        "💡 無料のGemini APIキーを入力すると、AI対話機能が使用可能になります。"
    )

  if st.button("💬 チャット履歴クリア"):
    st.session_state.chat_history = []
    st.rerun()

  st.divider()

  # チャット履歴表示
  for idx, msg in enumerate(st.session_state.chat_history):
    with st.chat_message(msg["role"]):
      st.markdown(msg["content"])
      if msg["role"] == "assistant" and "code" in msg:
        if st.button("📥 エディタに反映", key=f"apply_ai_code_{idx}"):
          st.session_state.saved_nc_code = msg["code"]
          with open(SAVE_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(msg["code"])
          st.session_state.is_playing = False
          st.rerun()

  # チャット入力
  if prompt := st.chat_input(
      "例: 外径50mm、長さ40mm、角R5でG71荒削りプログラムを作って"
  ):
    st.session_state.chat_history.append({"role": "user", "content": prompt})

    if HAS_GENAI and api_key:
      try:
        genai.configure(api_key=api_key)

        # 無料枠で安定して動作するモデルを選択
        target_model_name = "gemini-1.5-flash"
        try:
          available_models = [
              m.name
              for m in genai.list_models()
              if "generateContent" in m.supported_generation_methods
          ]
          candidates = [
              "models/gemini-1.5-flash",
              "models/gemini-2.0-flash",
              "models/gemini-1.5-flash-latest",
              "models/gemini-pro",
          ]
          found = False
          for c in candidates:
            if c in available_models:
              target_model_name = c
              found = True
              break
          if not found and available_models:
            target_model_name = available_models[0]
        except Exception:
          pass

        model = genai.GenerativeModel(target_model_name)

        full_prompt = f"{SYSTEM_INSTRUCTION}\n\n【指示】: {prompt}"
        response = model.generate_content(full_prompt)
        res_text = response.text

        # NCコードの抽出処理
        pattern = r"""```(?:nc|gcode)?\n(.*?)```"""
        code_match = re.search(pattern, res_text, re.DOTALL | re.IGNORECASE)
        extracted_code = code_match.group(1).strip() if code_match else None

        msg_obj = {"role": "assistant", "content": res_text}
        if extracted_code:
          msg_obj["code"] = extracted_code

        st.session_state.chat_history.append(msg_obj)
        st.rerun()
      except Exception as e:
        err_msg = str(e)
        if "429" in err_msg or "Quota" in err_msg:
          friendly_msg = (
              "⚠️ **APIの利用制限（1分あたりのリクエスト数上限）に達しました。**\n\n"
              "45秒〜1分ほど待ってから、もう一度メッセージを送信してください。\n"
              "（何度も出る場合は、Google AI"
              " Studioで新しいAPIキーを発行してお試しください）"
          )
        else:
          friendly_msg = f"❌ エラーが発生しました: {err_msg}"

        st.session_state.chat_history.append(
            {"role": "assistant", "content": friendly_msg}
        )
        st.rerun()
    else:
      st.session_state.chat_history.append({
          "role": "assistant",
          "content": (
              "⚠️ AI機能を使用するにはGemini"
              " APIキーを入力してください。（[Google AI Studio]"
              "(https://aistudio.google.com/) で無料で取得できます）"
          ),
      })
      st.rerun()

# -------------------------------------------------------------------
# 1. 画面最上部：コンパクトヘッダー ＆ 各種設定
# -------------------------------------------------------------------
col_title, col_select, col_view = st.columns([2, 2, 2])

with col_title:
  st.markdown("### 🛠️ NCパスシミュレータ")

with col_select:
  sub_col1, sub_col2 = st.columns([2, 2])
  with sub_col1:
    machine_type = sub_col1.selectbox(
        "機種選択",
        ("NC旋盤 (2D: 縦X/横Z)", "マシニングセンタ (3D)"),
        label_visibility="collapsed",
    )

  is_lathe = "NC旋盤" in machine_type
  diameter_mode = True

  with sub_col2:
    if is_lathe:
      diameter_mode = sub_col2.checkbox("X軸【径指定（直径）】", value=True)

with col_view:
  view_mode = st.radio(
      "表示モード",
      ("左右分割 (5:5)", "📝 エディタ全画面"),
      horizontal=True,
      label_visibility="collapsed",
  )

st.divider()


# -------------------------------------------------------------------
# 円弧点群の幾何計算関数 (G02 / G03 用)
# -------------------------------------------------------------------
def generate_arc_points_2d(
    z1,
    x1,
    z2,
    x2,
    r_val=None,
    i_val=None,
    k_val=None,
    is_cw=True,
    num_segments=16,
):
  arc_z, arc_x = [], []

  if i_val is not None or k_val is not None:
    i_offset = i_val if i_val is not None else 0.0
    k_offset = k_val if k_val is not None else 0.0
    zc = z1 + k_offset
    xc = x1 + i_offset
    r = math.hypot(k_offset, i_offset)
  elif r_val is not None and r_val != 0:
    r = abs(r_val)
    dz = z2 - z1
    dx = x2 - x1
    d = math.hypot(dz, dx)

    if d == 0:
      return [z1, z2], [x1, x2]

    if d > 2 * r:
      r = d / 2.0

    h = math.sqrt(max(0.0, r**2 - (d / 2.0) ** 2))
    mz, mx = (z1 + z2) / 2.0, (x1 + x2) / 2.0
    uz, ux = -dx / d, dz / d

    scale = -1.0 if is_cw else 1.0
    if r_val < 0:
      scale *= -1.0

    zc = mz + scale * h * uz
    xc = mx + scale * h * ux
  else:
    return [z1, z2], [x1, x2]

  angle1 = math.atan2(x1 - xc, z1 - zc)
  angle2 = math.atan2(x2 - xc, z2 - zc)

  if is_cw:
    if angle2 >= angle1:
      angle2 -= 2 * math.pi
  else:
    if angle2 <= angle1:
      angle2 += 2 * math.pi

  for step in range(num_segments + 1):
    t = step / float(num_segments)
    ang = angle1 + t * (angle2 - angle1)
    arc_z.append(zc + r * math.cos(ang))
    arc_x.append(xc + r * math.sin(ang))

  return arc_z, arc_x


def calculate_segment_distance(x_list, z_list, y_list=None):
  dist = 0.0
  for k in range(len(x_list) - 1):
    dx = x_list[k + 1] - x_list[k]
    dz = z_list[k + 1] - z_list[k]
    dy = (y_list[k + 1] - y_list[k]) if y_list else 0.0
    dist += math.sqrt(dx**2 + dy**2 + dz**2)
  return dist


# -------------------------------------------------------------------
# ⚠️ セーフティ ＆ エラーチェック ロジック
# -------------------------------------------------------------------
def check_nc_safety(text):
  alerts = []
  raw_lines = text.split("\n")
  lines = [l.upper().strip() for l in raw_lines]

  has_feed = False
  has_cutting_motion = False

  n_codes = set()
  for l in lines:
    match = re.search(r"N(\d+)", l)
    if match:
      n_codes.add(int(match.group(1)))

  for idx, line in enumerate(lines):
    line_num = idx + 1
    if not line or line.startswith("(") or line.startswith("%"):
      continue

    if "F" in line:
      has_feed = True

    if any(
        g in line
        for g in [
            "G01",
            "G1",
            "G02",
            "G2",
            "G03",
            "G3",
            "G71",
            "G72",
            "G73",
            "G74",
            "G75",
            "G76",
        ]
    ):
      has_cutting_motion = True

    if any(g in line for g in ["G70", "G71", "G72", "G73"]) and (
        "P" in line and "Q" in line
    ):
      p_m = re.search(r"P(\d+)", line)
      q_m = re.search(r"Q(\d+)", line)
      if p_m and int(p_m.group(1)) not in n_codes:
        alerts.append({
            "type": "error",
            "msg": (
                f"[{line_num}行目] サイクルの開始ブロック P{p_m.group(1)}"
                f" (N{p_m.group(1)}) が見つかりません。"
            ),
        })
      if q_m and int(q_m.group(1)) not in n_codes:
        alerts.append({
            "type": "error",
            "msg": (
                f"[{line_num}行目] サイクルの終了ブロック Q{q_m.group(1)}"
                f" (N{q_m.group(1)}) が見つかりません。"
            ),
        })

    if "G00" in line or "G0 " in line:
      zm = re.search(r"Z([-0-9.]+)", line)
      if zm and float(zm.group(1)) < 0:
        alerts.append({
            "type": "warning",
            "msg": (
                f"[{line_num}行目] G00(早送り)でワーク内部(Z{zm.group(1)})へ移動しています。工具衝突にご注意ください。"
            ),
        })

  if has_cutting_motion and not has_feed:
    alerts.append({
        "type": "warning",
        "msg": (
            "切削移動コマンドがありますが、送り速度 F の指定が見つかりません。"
        ),
    })

  return alerts


# -------------------------------------------------------------------
# 2. NCプログラム パーサー
# -------------------------------------------------------------------
def parse_nc_code(text, is_lathe=True, is_diameter=True):
  raw_lines = text.split("\n")
  lines = [l.upper().strip() for l in raw_lines]

  parsed_steps = []
  current_x, current_y, current_z = 0.0, 0.0, 0.0
  current_feed = 0.15

  RAPID_SPEED_MM_MIN = 10000.0
  ASSUMED_SPINDLE_RPM = 1200.0

  n_map = {}
  for idx, l in enumerate(lines):
    match = re.search(r"N(\d+)", l)
    if match:
      n_map[int(match.group(1))] = idx

  i = 0
  while i < len(lines):
    line = lines[i]
    line_num = i + 1

    if not line or line.startswith("(") or line.startswith("%"):
      i += 1
      continue

    fm = re.search(r"F([-0-9.]+)", line)
    if fm:
      current_feed = float(fm.group(1))

    # G70 仕上げサイクル
    if "G70" in line and "P" in line and "Q" in line:
      p_match = re.search(r"P(\d+)", line)
      q_match = re.search(r"Q(\d+)", line)
      if p_match and q_match:
        p_num, q_num = int(p_match.group(1)), int(q_match.group(1))
        g70_segs = []
        if p_num in n_map and q_num in n_map:
          px, pz = current_x, current_z
          for idx in range(n_map[p_num], n_map[q_num] + 1):
            sub_line = lines[idx]
            xm = re.search(r"X([-0-9.]+)", sub_line)
            zm = re.search(r"Z([-0-9.]+)", sub_line)
            nx = float(xm.group(1)) if xm else px
            nz = float(zm.group(1)) if zm else pz

            d_px = (px / 2.0) if is_diameter else px
            d_nx = (nx / 2.0) if is_diameter else nx

            g70_segs.append({"z": [pz, nz], "x": [d_px, d_nx], "y": [0, 0]})
            px, pz = nx, nz
          current_x, current_z = px, pz

        dist = sum(
            calculate_segment_distance(s["x"], s["z"]) for s in g70_segs
        )
        feed_mm_min = (
            current_feed * ASSUMED_SPINDLE_RPM
            if current_feed < 5.0
            else current_feed
        )
        time_sec = (dist / (feed_mm_min if feed_mm_min > 0 else 1.0)) * 60.0

        parsed_steps.append({
            "line_num": line_num,
            "text": raw_lines[i],
            "color": "green",
            "dash": "solid",
            "segments": g70_segs,
            "time_sec": time_sec,
            "is_rapid": False,
        })
      i += 1
      continue

    # G71 荒削りサイクル
    if "G71" in line and "P" in line and "Q" in line:
      p_match = re.search(r"P(\d+)", line)
      q_match = re.search(r"Q(\d+)", line)

      if p_match and q_match:
        p_num, q_num = int(p_match.group(1)), int(q_match.group(1))
        u_cut = 2.0
        r_escape = 0.5
        u_fin, w_fin = 0.0, 0.0

        if i > 0 and "G71" in lines[i - 1]:
          u_m = re.search(r"U([-0-9.]+)", lines[i - 1])
          r_m = re.search(r"R([-0-9.]+)", lines[i - 1])
          if u_m:
            u_cut = float(u_m.group(1))
          if r_m:
            r_escape = float(r_m.group(1))

        uf_m = re.search(r"U([-0-9.]+)", line)
        wf_m = re.search(r"W([-0-9.]+)", line)
        if uf_m:
          u_fin = float(uf_m.group(1)) / (2.0 if is_diameter else 1.0)
        if wf_m:
          w_fin = float(wf_m.group(1))

        profile_pts = []
        if p_num in n_map and q_num in n_map:
          px, pz = current_x, current_z
          for idx in range(n_map[p_num], n_map[q_num] + 1):
            sub_line = lines[idx]
            xm = re.search(r"X([-0-9.]+)", sub_line)
            zm = re.search(r"Z([-0-9.]+)", sub_line)
            if xm:
              px = float(xm.group(1))
            if zm:
              pz = float(zm.group(1))

            draw_px = (px / 2.0) if is_diameter else px
            profile_pts.append((pz - w_fin, draw_px + u_fin))

        if len(profile_pts) >= 2:
          curr_draw_x = (current_x / 2.0) if is_diameter else current_x
          start_z = current_z
          target_min_x = min(pt[1] for pt in profile_pts)

          cut_x = curr_draw_x - u_cut
          g71_segments = []
          total_dist = 0.0

          while cut_x >= target_min_x:
            target_z = profile_pts[-1][0]
            for k in range(len(profile_pts) - 1):
              z1, x1 = profile_pts[k]
              z2, x2 = profile_pts[k + 1]
              if (x1 <= cut_x <= x2) or (x2 <= cut_x <= x1):
                if x2 != x1:
                  target_z = z1 + (cut_x - x1) * (z2 - z1) / (x2 - x1)
                break

            seg1 = {
                "z": [start_z, target_z],
                "x": [cut_x, cut_x],
                "y": [0, 0],
            }
            seg2 = {
                "z": [target_z, target_z + r_escape],
                "x": [cut_x, cut_x + r_escape],
                "y": [0, 0],
            }
            seg3 = {
                "z": [target_z + r_escape, start_z],
                "x": [cut_x + r_escape, cut_x + r_escape],
                "y": [0, 0],
            }

            g71_segments.extend([seg1, seg2, seg3])
            total_dist += (
                calculate_segment_distance(seg1["x"], seg1["z"])
                + calculate_segment_distance(seg2["x"], seg2["z"])
                + calculate_segment_distance(seg3["x"], seg3["z"])
            )

            cut_x -= u_cut

          feed_mm_min = (
              current_feed * ASSUMED_SPINDLE_RPM
              if current_feed < 5.0
              else current_feed
          )
          time_sec = (total_dist / (feed_mm_min if feed_mm_min > 0 else 1.0)) * 60.0

          parsed_steps.append({
              "line_num": line_num,
              "text": raw_lines[i],
              "segments": g71_segments,
              "time_sec": time_sec,
              "is_rapid": False,
          })
      i += 1
      continue

    # G72 端面荒削り
    if "G72" in line and "P" in line and "Q" in line:
      p_match = re.search(r"P(\d+)", line)
      q_match = re.search(r"Q(\d+)", line)
      if p_match and q_match:
        w_cut = 2.0
        if i > 0 and "G72" in lines[i - 1]:
          wm = re.search(r"W([-0-9.]+)", lines[i - 1])
          if wm:
            w_cut = float(wm.group(1))

        curr_draw_x = (current_x / 2.0) if is_diameter else current_x
        g72_segs = []
        for step_z in [
            current_z - w_cut,
            current_z - w_cut * 2,
            current_z - w_cut * 3,
        ]:
          g72_segs.append({
              "z": [current_z, step_z, step_z, current_z],
              "x": [curr_draw_x, curr_draw_x, 10.0, curr_draw_x],
              "y": [0, 0, 0, 0],
          })

        parsed_steps.append({
            "line_num": line_num,
            "text": raw_lines[i],
            "color": "orange",
            "segments": g72_segs,
            "time_sec": 12.0,
            "is_rapid": False,
        })
      i += 1
      continue

    # G73 パターンリピート
    if "G73" in line and "P" in line and "Q" in line:
      p_match = re.search(r"P(\d+)", line)
      q_match = re.search(r"Q(\d+)", line)
      if p_match and q_match:
        p_num, q_num = int(p_match.group(1)), int(q_match.group(1))
        repeat_cnt = 3
        if i > 0 and "G73" in lines[i - 1]:
          rm = re.search(r"R(\d+)", lines[i - 1])
          if rm:
            repeat_cnt = int(rm.group(1))

        g73_segs = []
        if p_num in n_map and q_num in n_map:
          for r_idx in reversed(range(repeat_cnt)):
            offset = (r_idx + 1) * 2.0
            px, pz = current_x, current_z
            for idx in range(n_map[p_num], n_map[q_num] + 1):
              sub_line = lines[idx]
              xm = re.search(r"X([-0-9.]+)", sub_line)
              zm = re.search(r"Z([-0-9.]+)", sub_line)
              nx = float(xm.group(1)) if xm else px
              nz = float(zm.group(1)) if zm else pz

              d_px = (
                  ((px + offset) / 2.0) if is_diameter else (px + offset / 2.0)
              )
              d_nx = (
                  ((nx + offset) / 2.0) if is_diameter else (nx + offset / 2.0)
              )

              g73_segs.append({
                  "z": [pz + offset / 2.0, nz + offset / 2.0],
                  "x": [d_px, d_nx],
                  "y": [0, 0],
              })
              px, pz = nx, nz

        parsed_steps.append({
            "line_num": line_num,
            "text": raw_lines[i],
            "color": "orange",
            "segments": g73_segs,
            "time_sec": 15.0,
            "is_rapid": False,
        })
      i += 1
      continue

    # G74 端面溝入れ
    if "G74" in line and "Z" in line:
      zm = re.search(r"Z([-0-9.]+)", line)
      qm = re.search(r"Q(\d+)", line)
      if zm:
        target_z = float(zm.group(1))
        q_step = (float(qm.group(1)) / 1000.0) if qm else 5.0
        curr_draw_x = (current_x / 2.0) if is_diameter else current_x

        g74_segs = []
        cz = current_z
        while cz > target_z:
          next_z = max(target_z, cz - q_step)
          g74_segs.append({
              "z": [cz, next_z, next_z + 1.0],
              "x": [curr_draw_x, curr_draw_x, curr_draw_x],
              "y": [0, 0, 0],
          })
          cz = next_z

        current_z = target_z
        parsed_steps.append({
            "line_num": line_num,
            "text": raw_lines[i],
            "color": "orange",
            "segments": g74_segs,
            "time_sec": 8.0,
            "is_rapid": False,
        })
      i += 1
      continue

    # G75 外径溝入れ
    if "G75" in line and "X" in line:
      xm = re.search(r"X([-0-9.]+)", line)
      pm = re.search(r"P(\d+)", line)
      if xm:
        target_x = float(xm.group(1))
        p_step = (float(pm.group(1)) / 1000.0) if pm else 2.0
        draw_target_x = (target_x / 2.0) if is_diameter else target_x
        curr_draw_x = (current_x / 2.0) if is_diameter else current_x

        g75_segs = []
        cx = curr_draw_x
        while cx > draw_target_x:
          next_x = max(draw_target_x, cx - p_step)
          g75_segs.append({
              "z": [current_z, current_z, current_z],
              "x": [cx, next_x, next_x + 0.5],
              "y": [0, 0, 0],
          })
          cx = next_x

        current_x = target_x
        parsed_steps.append({
            "line_num": line_num,
            "text": raw_lines[i],
            "color": "orange",
            "segments": g75_segs,
            "time_sec": 6.0,
            "is_rapid": False,
        })
      i += 1
      continue

    # G76 ねじ切り
    if "G76" in line and "Z" in line and "X" in line:
      xm = re.search(r"X([-0-9.]+)", line)
      zm = re.search(r"Z([-0-9.]+)", line)
      if xm and zm:
        target_x = float(xm.group(1))
        target_z = float(zm.group(1))
        draw_target_x = (target_x / 2.0) if is_diameter else target_x
        curr_draw_x = (current_x / 2.0) if is_diameter else current_x

        g76_segs = []
        passes = 4
        for p in range(passes):
          depth_ratio = (p + 1) / float(passes)
          cx = curr_draw_x - (curr_draw_x - draw_target_x) * depth_ratio
          g76_segs.append({
              "z": [current_z, target_z, current_z],
              "x": [cx, cx, curr_draw_x],
              "y": [0, 0, 0],
          })

        parsed_steps.append({
            "line_num": line_num,
            "text": raw_lines[i],
            "color": "magenta",
            "segments": g76_segs,
            "time_sec": 10.0,
            "is_rapid": False,
        })
      i += 1
      continue

    # 通常の直線・円弧移動コマンド解析
    xm = re.search(r"X([-0-9.]+)", line)
    ym = re.search(r"Y([-0-9.]+)", line)
    zm = re.search(r"Z([-0-9.]+)", line)
    rm = re.search(r"R([-0-9.]+)", line)
    im = re.search(r"I([-0-9.]+)", line)
    km = re.search(r"K([-0-9.]+)", line)

    next_x = float(xm.group(1)) if xm else current_x
    next_y = float(ym.group(1)) if ym else current_y
    next_z = float(zm.group(1)) if zm else current_z

    is_g00 = "G00" in line or "G0 " in line
    is_g02 = "G02" in line or "G2" in line
    is_g03 = "G03" in line or "G3" in line

    if xm or ym or zm:
      draw_curr_x = (
          (current_x / 2.0) if (is_lathe and is_diameter) else current_x
      )
      draw_next_x = (next_x / 2.0) if (is_lathe and is_diameter) else next_x

      if is_g02 or is_g03:
        r_val = float(rm.group(1)) if rm else None
        i_val = float(im.group(1)) if im else None
        k_val = float(km.group(1)) if km else None

        arc_z, arc_x = generate_arc_points_2d(
            current_z,
            draw_curr_x,
            next_z,
            draw_next_x,
            r_val=r_val,
            i_val=i_val,
            k_val=k_val,
            is_cw=is_g02,
        )

        dist = calculate_segment_distance(arc_x, arc_z)
        feed_mm_min = (
            current_feed * ASSUMED_SPINDLE_RPM
            if current_feed < 5.0
            else current_feed
        )
        step_time = (dist / (feed_mm_min if feed_mm_min > 0 else 1.0)) * 60.0

        parsed_steps.append({
            "line_num": line_num,
            "text": raw_lines[i],
            "color": "magenta",
            "dash": "solid",
            "segments": [{
                "x": arc_x,
                "y": [0] * len(arc_x),
                "z": arc_z,
            }],
            "time_sec": step_time,
            "is_rapid": False,
        })
      else:
        color = "blue" if is_g00 else "orange"
        dash = "dash" if is_g00 else "solid"

        seg_x = [draw_curr_x, draw_next_x]
        seg_z = [current_z, next_z]
        seg_y = [current_y, next_y]
        dist = calculate_segment_distance(seg_x, seg_z, seg_y)

        if is_g00:
          step_time = (dist / RAPID_SPEED_MM_MIN) * 60.0
        else:
          feed_mm_min = (
              current_feed * ASSUMED_SPINDLE_RPM
              if current_feed < 5.0
              else current_feed
          )
          step_time = (dist / (feed_mm_min if feed_mm_min > 0 else 1.0)) * 60.0

        parsed_steps.append({
            "line_num": line_num,
            "text": raw_lines[i],
            "color": color,
            "dash": dash,
            "segments": [{"x": seg_x, "y": seg_y, "z": seg_z}],
            "time_sec": step_time,
            "is_rapid": is_g00,
        })

      current_x, current_y, current_z = next_x, next_y, next_z

    i += 1

  return parsed_steps


# -------------------------------------------------------------------
# 3. メイン画面レイアウト制御
# -------------------------------------------------------------------
play_delay = 0.3

if view_mode == "📝 エディタ全画面":
  title_sub1, title_sub2 = st.columns([4, 1])
  with title_sub1:
    st.markdown("**📝 プログラム編集（全画面モード）**")
  with title_sub2:
    if st.button("🔄 初期コードにリセット"):
      st.session_state.saved_nc_code = default_code
      with open(SAVE_FILE_PATH, "w", encoding="utf-8") as f:
        f.write(default_code)
      st.rerun()

  nc_input = st.text_area(
      "NCプログラム",
      value=st.session_state.saved_nc_code,
      height=650,
      label_visibility="collapsed",
  )

  if nc_input != st.session_state.saved_nc_code:
    st.session_state.saved_nc_code = nc_input
    with open(SAVE_FILE_PATH, "w", encoding="utf-8") as f:
      f.write(nc_input)

else:
  left_col, right_col = st.columns([5, 5])

  with left_col:
    # --- 入力欄コントロールエリア ---
    h_col1, h_col2 = st.columns([3, 2])
    with h_col1:
      st.markdown("**📝 プログラム入力欄**")
    with h_col2:
      st.session_state.editor_height = st.slider(
          "📏 高さ (px)",
          min_value=200,
          max_value=800,
          value=st.session_state.editor_height,
          step=20,
          label_visibility="visible",
      )

    nc_input = st.text_area(
        "NCプログラム",
        value=st.session_state.saved_nc_code,
        height=st.session_state.editor_height,
        label_visibility="collapsed",
    )

    if nc_input != st.session_state.saved_nc_code:
      st.session_state.saved_nc_code = nc_input
      with open(SAVE_FILE_PATH, "w", encoding="utf-8") as f:
        f.write(nc_input)

    steps = parse_nc_code(nc_input, is_lathe=is_lathe, is_diameter=diameter_mode)
    max_steps = len(steps)

    if (
        st.session_state.selected_step is None
        or st.session_state.selected_step > max_steps
    ):
      st.session_state.selected_step = max_steps

    if max_steps > 0:
      st.markdown("**🔍 パス確認 ＆ アニメーション再生**")

      c_play, c_reset, c_speed = st.columns([2, 2, 3])

      with c_play:
        if st.session_state.is_playing:
          if st.button("⏸️ 停止", use_container_width=True):
            st.session_state.is_playing = False
            st.rerun()
        else:
          if st.button("▶️ 再生", use_container_width=True):
            if st.session_state.selected_step >= max_steps:
              st.session_state.selected_step = 1
            st.session_state.is_playing = True
            st.rerun()

      with c_reset:
        if st.button("⏮️ 最初へ", use_container_width=True):
          st.session_state.is_playing = False
          st.session_state.selected_step = 1
          st.rerun()

      with c_speed:
        play_delay = st.select_slider(
            "再生速度",
            options=[0.05, 0.1, 0.2, 0.3, 0.4, 0.6, 0.8, 1.0],
            value=0.3,
            format_func=lambda x: f"⏱️ {x}秒",
            label_visibility="collapsed",
        )

      slider_val = st.slider(
          "ステップ選択",
          min_value=1,
          max_value=max_steps,
          value=st.session_state.selected_step,
          label_visibility="collapsed",
      )

      if (
          slider_val != st.session_state.selected_step
          and not st.session_state.is_playing
      ):
        st.session_state.selected_step = slider_val

      selected_step = st.session_state.selected_step

      # ⏱️ 予想加工時間（サイクルタイム）リアルタイム試算
      total_time_sec = sum(s.get("time_sec", 0.0) for s in steps)
      current_time_sec = sum(
          s.get("time_sec", 0.0) for s in steps[:selected_step]
      )

      cutting_time_total = sum(
          s["time_sec"] for s in steps if not s.get("is_rapid", False)
      )
      cut_ratio = (
          (cutting_time_total / total_time_sec * 100.0)
          if total_time_sec > 0
          else 0.0
      )

      m_col1, m_col2, m_col3 = st.columns(3)
      m_col1.metric(
          "⏱️ 累計時間",
          f"{int(current_time_sec // 60)}分 {current_time_sec % 60:.1f}秒",
      )
      m_col2.metric(
          "🏁 総予想時間",
          f"{int(total_time_sec // 60)}分 {total_time_sec % 60:.1f}秒",
      )
      m_col3.metric("📊 切削時間比率", f"{cut_ratio:.0f}%")

      current_step_info = steps[selected_step - 1]
      line_no = current_step_info["line_num"]
      code_text = current_step_info["text"]

      st.markdown(
          f'<div class="active-code-box">📍 動作中のブロック'
          f" [{line_no}行目]: <code>{code_text}</code></div>",
          unsafe_allow_html=True,
      )

      # ⚠️ セーフティ ＆ エラーチェック判定表示
      safety_alerts = check_nc_safety(nc_input)
      with st.expander(
          f"⚠️ セーフティ ＆ エラーチェック ({len(safety_alerts)}件)",
          expanded=(len(safety_alerts) > 0),
      ):
        if len(safety_alerts) == 0:
          st.success(
              "✅ 構文エラーや早送り突っ込みなどの危険メッセージはありません。"
          )
        else:
          for alert in safety_alerts:
            if alert["type"] == "error":
              st.error(f"❌ {alert['msg']}")
            else:
              st.warning(f"⚠️ {alert['msg']}")

  with right_col:
    scale_col1, scale_col2 = st.columns([2, 3])
    with scale_col1:
      if st.button("🔍 1回全体表示"):
        st.session_state.autoscale_key += 1
    with scale_col2:
      auto_scale_toggle = st.toggle("🔄 自動オートスケール", value=False)

    fig = go.Figure()

    for idx, step in enumerate(steps[:selected_step]):
      is_current = idx == selected_step - 1

      line_color = "red" if is_current else step.get("color", "green")
      line_width = 5 if is_current else 2
      line_dash = "solid" if is_current else step.get("dash", "dot")

      for seg in step["segments"]:
        if is_lathe:
          fig.add_trace(
              go.Scatter(
                  x=seg["z"],
                  y=seg["x"],
                  mode="lines+markers" if is_current else "lines",
                  marker=dict(size=6, color="red") if is_current else None,
                  line=dict(color=line_color, width=line_width, dash=line_dash),
                  showlegend=False,
              )
          )
        else:
          fig.add_trace(
              go.Scatter3d(
                  x=seg["x"],
                  y=seg["y"],
                  z=seg["z"],
                  mode="lines+markers" if is_current else "lines",
                  marker=dict(size=5, color="red") if is_current else None,
                  line=dict(color=line_color, width=line_width, dash=line_dash),
                  showlegend=False,
              )
          )

    if auto_scale_toggle:
      uirevision_id = None
    else:
      uirevision_id = f"scale_{st.session_state.autoscale_key}"

    if is_lathe:
      fig.update_layout(
          uirevision=uirevision_id,
          showlegend=False,
          xaxis=dict(
              title="Z軸 (長手方向 / mm)", zeroline=True, zerolinecolor="gray"
          ),
          yaxis=dict(
              title="X軸 (半径値 / mm)",
              zeroline=True,
              zerolinecolor="gray",
              scaleanchor="x",
              scaleratio=1,
          ),
          height=620,
          margin=dict(l=10, r=10, t=10, b=10),
      )
    else:
      fig.update_layout(
          uirevision=uirevision_id,
          showlegend=False,
          scene=dict(
              xaxis_title="X軸",
              yaxis_title="Y軸",
              zaxis_title="Z軸",
              aspectmode="data",
          ),
          height=620,
          margin=dict(l=0, r=0, t=10, b=0),
      )

    st.plotly_chart(fig, use_container_width=True)

# -------------------------------------------------------------------
# 4. 画面最下部：📚 複合固定サイクル サンプル参照（コピー用）
# -------------------------------------------------------------------
st.divider()
st.markdown("### 📚 複合固定サイクル 定型コード参照（コピー用）")
st.caption(
    "必要なサイクルを選択し、コード枠の右上ボタン（📋）でコピーして上の『プログラム入力欄』の目的の場所へ貼り付けてご使用ください。"
)

selected_ref_name = st.selectbox(
    "参照する定型サイクルを選択", options=list(SAMPLE_PROGRAMS.keys()), index=0
)

st.code(SAMPLE_PROGRAMS[selected_ref_name], language="gcode")

# アニメーション再生ルーティン
if st.session_state.is_playing:
  if st.session_state.selected_step < max_steps:
    time.sleep(play_delay)
    st.session_state.selected_step += 1
    st.rerun()
  else:
    st.session_state.is_playing = False
