from __future__ import annotations

import io
import re
import shutil
from difflib import SequenceMatcher
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from PIL import Image, ImageEnhance, ImageFilter, ImageOps


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

TEAM_ALIASES = {
    # セ・リーグ（Pinnacle表記 → ツール内の表示名）
    "読売ジャイアンツ": "巨人", "東京読売ジャイアンツ": "巨人", "ジャイアンツ": "巨人",
    "中日ドラゴンズ": "中日", "ドラゴンズ": "中日",
    "横浜ＤｅＮＡベイスターズ": "横浜", "DeNA": "横浜", "ベイスターズ": "横浜",
    "東京ヤクルトスワローズ": "ヤクルト", "ヤクルトスワローズ": "ヤクルト", "ヤクルト": "ヤクルト",
    "阪神タイガース": "阪神", "タイガース": "阪神",
    "広島東洋カープ": "広島", "広島カープ": "広島", "カープ": "広島",

    # パ・リーグ（Pinnacle表記 → ツール内の表示名）
    "東北楽天ゴールデンイーグルス": "楽天", "楽天ゴールデンイーグルス": "楽天", "楽天": "楽天",
    "千葉ロッテマリーンズ": "ロッテ", "ロッテマリーンズ": "ロッテ", "千葉": "ロッテ",
    "北海道日本ハムファイターズ": "日本ハム", "日本ハムファイターズ": "日本ハム", "ファイターズ": "日本ハム",
    "埼玉西武ライオンズ": "西武", "西武ライオンズ": "西武", "西武": "西武",
    "オリックス・バファローズ": "オリックス", "オリックスバファローズ": "オリックス", "オリックス": "オリックス",
    "福岡ソフトバンクホークス": "ソフトバンク", "ソフトバンクホークス": "ソフトバンク", "ホークス": "ソフトバンク", "ソフト": "ソフトバンク",
}

# TEAM_ALIASESは正式ルールとして固定し、画像OCR特有の崩れだけをこちらで補正する。
OCR_TEAM_ALIASES = {
    "ゴールデンイーグルス": "楽天",
    "ゴオールデンイーグルス": "楽天",
    "東北楽天": "楽天",
    "楽天イーグルス": "楽天",
    "バファローズ": "オリックス",
    "パバファローズ": "オリックス",
    "オリックスバファローズ": "オリックス",
}

TEAM_TYPOS = {
    "西部": "西武",
}

# 出し側の精算倍率。+は勝ち、-は負け、0は返金。
HANDICAP = {
    0.0: {0: 0.0, 1: 1.0, 2: 1.0, 3: 1.0},
    0.3: {0: -0.3, 1: 0.7, 2: 1.0, 3: 1.0},
    0.5: {0: -0.5, 1: 0.5, 2: 1.0, 3: 1.0},
    0.7: {0: -0.7, 1: 0.3, 2: 1.0, 3: 1.0},
    1.0: {0: -1.0, 1: 0.0, 2: 1.0, 3: 1.0},
    1.3: {0: -1.0, 1: -0.3, 2: 1.0, 3: 1.0},
    1.5: {0: -1.0, 1: -0.5, 2: 1.0, 3: 1.0},
    1.7: {0: -1.0, 1: -0.7, 2: 1.0, 3: 1.0},
    1.5 + 1 / 6: {0: -1.0, 1: -1.0, 2: 1.0, 3: 1.0},  # 1半
    1.3 + 1 / 6: {0: -1.0, 1: -1.0, 2: 0.7, 3: 1.0},  # 1半3
    1.5 + 1 / 6 + 0.01: {0: -1.0, 1: -1.0, 2: 0.5, 3: 1.0}, # 1半5
    1.7 + 1 / 6: {0: -1.0, 1: -1.0, 2: 0.3, 3: 1.0},  # 1半7
    2.0: {0: -1.0, 1: -1.0, 2: 0.0, 3: 1.0},
}


def norm_team(value: str) -> str:
    value = re.sub(r"\s+", "", str(value))
    return TEAM_TYPOS.get(value, TEAM_ALIASES.get(value, value))


def parse_handicap(token: str) -> float:
    token = token.strip().replace("０", "0").replace("．", ".")
    special = {"1半": 1.5 + 1 / 6, "1半3": 1.3 + 1 / 6,
               "1半5": 1.5 + 1 / 6 + 0.01, "1半7": 1.7 + 1 / 6}
    if token in special:
        return special[token]
    if re.fullmatch(r"0[1-9]", token):
        token = "0." + token[-1]
    return float(token or 0)


def display_handicap(value: float) -> str:
    specials = {
        round(1.5 + 1 / 6, 3): "1半", round(1.3 + 1 / 6, 3): "1半3",
        round(1.5 + 1 / 6 + .01, 3): "1半5", round(1.7 + 1 / 6, 3): "1半7",
    }
    return specials.get(round(value, 3), f"{value:g}")


def parse_other_site(text: str) -> list[dict]:
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    teams = []
    for line in lines:
        # 時刻の後ろに <0> が付く入力もチーム名として扱わない。
        if re.fullmatch(r"\d{1,2}:\d{2}(?:[<＜][^>＞]+[>＞])?", line):
            continue
        m = re.match(r"^(.*?)(?:[<＜]([^>＞]+)[>＞])?$", line)
        if m and m.group(1):
            teams.append((norm_team(m.group(1)), parse_handicap(m.group(2)) if m.group(2) else 0.0,
                          bool(m.group(2))))
    rows = []
    for i in range(0, len(teams) - 1, 2):
        a, b = teams[i], teams[i + 1]
        giver = a[0] if a[2] else (b[0] if b[2] else a[0])
        handicap = a[1] if a[2] else (b[1] if b[2] else 0.0)
        rows.append({"チーム1": a[0], "オッズ1": None, "チーム2": b[0], "オッズ2": None,
                     "出しチーム": giver, "ハンデ": display_handicap(handicap)})
    return rows


def _configure_tesseract(pytesseract) -> None:
    if shutil.which("tesseract"):
        return
    for candidate in (
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ):
        if candidate.exists():
            pytesseract.pytesseract.tesseract_cmd = str(candidate)
            return


def _clean_ocr(value: str) -> str:
    return re.sub(r"[\s|｜・･·.,。:：/\\_\-]+", "", value).lower()


def _match_team(text: str) -> tuple[str | None, float]:
    cleaned = _clean_ocr(text)
    if not cleaned or re.fullmatch(r"[\d:：]+", cleaned):
        return None, 0.0
    best_team, best_score = None, 0.0
    for alias, short_name in {**TEAM_ALIASES, **OCR_TEAM_ALIASES}.items():
        target = _clean_ocr(alias)
        if target in cleaned:
            # 同一行の右側にオッズが連結されていても正式名が含まれれば採用。
            score = 0.98
        elif cleaned in target:
            score = len(cleaned) / len(target)
        else:
            score = SequenceMatcher(None, cleaned, target).ratio()
        if score > best_score:
            best_team, best_score = short_name, score
    return (best_team, best_score) if best_score >= 0.52 else (None, best_score)


def _merge_ocr_rows(found: list[dict], existing: list[dict] | None,
                    ordered_numeric: list[list[float]] | None = None) -> list[dict]:
    if not existing:
        return [{k: v for k, v in row.items() if not k.startswith("_ocr_")} for row in found]
    merged = [dict(row) for row in existing]
    for detected in found:
        detected_pair = {norm_team(detected["チーム1"]), norm_team(detected["チーム2"])}
        match = next((row for row in merged if
                      {norm_team(row.get("チーム1", "")), norm_team(row.get("チーム2", ""))} == detected_pair), None)
        if match is None:
            merged.append({k: v for k, v in detected.items() if not k.startswith("_ocr_")})
            continue
        if norm_team(match["チーム1"]) == norm_team(detected["チーム1"]):
            match["オッズ1"], match["オッズ2"] = detected["オッズ1"], detected["オッズ2"]
        else:
            match["オッズ1"], match["オッズ2"] = detected["オッズ2"], detected["オッズ1"]
        giver = norm_team(match.get("出しチーム", ""))
        hcap = parse_handicap(str(match.get("ハンデ", "0")))
        if (abs(hcap) > 1e-9
                and giver == norm_team(detected.get("_ocr_2plus_team", ""))
                and detected.get("_ocr_2plus_pct") is not None):
            match["出し2点差以上(%)"] = round(float(detected["_ocr_2plus_pct"]), 2)
    # チーム文字が崩れても、貼り付け表と画像の試合数が同じなら上から順に補完する。
    if ordered_numeric and len(ordered_numeric) == len(merged):
        for row, values in zip(merged, ordered_numeric):
            if len(values) < 2:
                continue
            row["オッズ1"], row["オッズ2"] = values[0], values[1]
            hcap = parse_handicap(str(row.get("ハンデ", "0")))
            if abs(hcap) < 1e-9 or len(values) < 4:
                continue
            team1, team2 = norm_team(row.get("チーム1", "")), norm_team(row.get("チーム2", ""))
            giver = norm_team(row.get("出しチーム", ""))
            favorite = team1 if values[0] < values[1] else team2
            if giver == favorite:
                if favorite == team1:
                    probability = fair_probability(values[2], values[3]) * 100
                else:
                    probability = fair_probability(values[3], values[2]) * 100
                row["出し2点差以上(%)"] = round(probability, 2)
    return merged


def ocr_moneylines(upload) -> tuple[str, list[dict], list[list[float]]]:
    """OCR座標からチーム2行と同じ試合帯の左側2オッズを結合する。"""
    import pytesseract
    from pytesseract import Output

    _configure_tesseract(pytesseract)
    installed_languages = set(pytesseract.get_languages(config=""))
    if "jpn" not in installed_languages:
        raise RuntimeError(
            "Tesseractの日本語データ（jpn）がありません。"
            "TesseractインストーラーでJapaneseを追加してから、アプリを再起動してください。"
        )
    original = Image.open(io.BytesIO(upload.getvalue())).convert("RGB")
    scale = max(2, min(4, 1800 // max(original.width, 1)))
    image = original.resize((original.width * scale, original.height * scale), Image.Resampling.LANCZOS)
    image = ImageOps.grayscale(image)
    image = ImageEnhance.Contrast(image).enhance(1.8)
    image = image.filter(ImageFilter.SHARPEN)
    data = pytesseract.image_to_data(image, lang="jpn+eng", config="--oem 3 --psm 6",
                                     output_type=Output.DICT)
    # 数字は日本語OCRと分離し、英数字限定でもう一度読むと精度が大きく上がる。
    numeric_data = pytesseract.image_to_data(
        image, lang="eng",
        config="--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789.+-",
        output_type=Output.DICT,
    )

    grouped: dict[tuple[int, int, int], list[int]] = {}
    for i, raw in enumerate(data["text"]):
        if str(raw).strip():
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            grouped.setdefault(key, []).append(i)

    lines = []
    for indexes in grouped.values():
        indexes.sort(key=lambda i: data["left"][i])
        text = " ".join(str(data["text"][i]).strip() for i in indexes)
        left = min(data["left"][i] for i in indexes)
        top = min(data["top"][i] for i in indexes)
        right = max(data["left"][i] + data["width"][i] for i in indexes)
        bottom = max(data["top"][i] + data["height"][i] for i in indexes)
        lines.append({"text": text, "x": (left + right) / 2, "y": (top + bottom) / 2})
    lines.sort(key=lambda line: (line["y"], line["x"]))

    teams = []
    odds = []
    circled_digits = str.maketrans({
        "⓪": "0", "①": "1", "②": "2", "③": "3", "④": "4",
        "⑤": "5", "⑥": "6", "⑦": "7", "⑧": "8", "⑨": "9",
        "⑩": "10", "⑪": "11", "⑫": "12", "⑬": "13", "⑭": "14",
        "⑮": "15", "⑯": "16", "⑰": "17", "⑱": "18", "⑲": "19", "⑳": "20",
    })
    odds_pattern = re.compile(r"(?<!\d)([1-9](?:[.,]\d{2,3}))(?!\d)")
    for line in lines:
        values = []
        numeric_text = line["text"].translate(circled_digits)
        for token in odds_pattern.findall(numeric_text):
            try:
                value = float(token.replace(",", "."))
                if 1.01 <= value <= 20:
                    values.append(value)
            except ValueError:
                pass
        if values:
            odds.append({**line, "values": values})
        team, score = _match_team(line["text"])
        # 下段チーム名と2個のオッズが同じOCR行になる場合もチームとして残す。
        if team:
            # 同じチーム名がML行とハンデ行で連続して出ることがあるため、
            # 距離に関係なく連続重複は1件へまとめる。
            if not teams or teams[-1]["team"] != team:
                teams.append({**line, "team": team, "score": score})

    numeric_odds = []
    simple_odds_pattern = re.compile(r"[1-9][.,]\d{3}")
    compact_odds_pattern = re.compile(r"(?<![\d.,])([1-9]\d{3})(?!\d)")
    joined_odds_pattern = re.compile(r"(?<!\d)([1-9]\d{3})(?=[1-9][.,]\d{3})")
    for i, raw in enumerate(numeric_data["text"]):
        text_value = str(raw).translate(circled_digits)
        candidates = []
        for match in simple_odds_pattern.finditer(text_value):
            candidates.append((match.start(), float(match.group().replace(",", "."))))
        # 数字限定OCRは小数点を落として「2.430」を「2430」と読む場合がある。
        for pattern in (compact_odds_pattern, joined_odds_pattern):
            for match in pattern.finditer(text_value):
                digits = match.group(1)
                candidates.append((match.start(), float(f"{digits[0]}.{digits[1:]}")))
        values = [value for _, value in sorted(set(candidates)) if 1.01 <= value <= 20]
        if values:
            numeric_odds.append({
                "text": text_value,
                "x": numeric_data["left"][i] + numeric_data["width"][i] / 2,
                "y": numeric_data["top"][i] + numeric_data["height"][i] / 2,
                "values": values,
            })
    if len(numeric_odds) >= 2:
        odds = numeric_odds

    # 数字だけをY座標で試合行にまとめる。チーム名認識に失敗した場合の予備ルート。
    numeric_groups: list[list[dict]] = []
    for item in sorted(numeric_odds, key=lambda value: (value["y"], value["x"])):
        if not numeric_groups:
            numeric_groups.append([item])
            continue
        group_y = sum(value["y"] for value in numeric_groups[-1]) / len(numeric_groups[-1])
        if abs(item["y"] - group_y) <= 20 * scale:
            numeric_groups[-1].append(item)
        else:
            numeric_groups.append([item])
    ordered_numeric = []
    for group in numeric_groups:
        row_values = []
        for item in sorted(group, key=lambda value: value["x"]):
            row_values.extend(item["values"])
        if len(row_values) >= 2:
            ordered_numeric.append(row_values[:4])

    found = []
    for i in range(0, len(teams) - 1, 2):
        first, second = teams[i], teams[i + 1]
        # Pinnacleの配置規則：上段チーム＝左オッズ、下段チーム＝右オッズ。
        # オッズはOCR上で下段チームと同じ行になりやすいため、下段のY座標を優先する。
        nearest = min(odds, key=lambda item: abs(item["y"] - second["y"]), default=None)
        values = []
        if nearest:
            # 同じ試合行の数値を左から収集：ML1, ML2, HC1, HC2。
            same_height = [item for item in odds
                           if abs(item["y"] - nearest["y"]) <= 20 * scale]
            for item in sorted(same_height, key=lambda item: item["x"]):
                values.extend(item["values"])
        if len(values) >= 2:
            two_plus_team = None
            two_plus_pct = None
            if len(values) >= 4:
                ml1, ml2, runline1, runline2 = values[:4]
                # Pinnacleの通常表示ではML本命側が-1.5、もう一方が+1.5。
                if ml1 < ml2:
                    two_plus_team = first["team"]
                    two_plus_pct = fair_probability(runline1, runline2) * 100
                else:
                    two_plus_team = second["team"]
                    two_plus_pct = fair_probability(runline2, runline1) * 100
            found.append({"チーム1": first["team"], "オッズ1": values[0],
                          "チーム2": second["team"], "オッズ2": values[1],
                          "出しチーム": first["team"], "ハンデ": "0",
                          "出し2点差以上(%)": None,
                          "_ocr_2plus_team": two_plus_team,
                          "_ocr_2plus_pct": two_plus_pct})
    raw_text = "\n".join(line["text"] for line in lines)
    # 座標グループが崩れた場合、OCR生テキストに残ったオッズを上から4個ずつ復元する。
    normalized_raw = raw_text.translate(circled_digits)
    normalized_raw = re.sub(r"\b\d{1,2}:\d{2}\b", " ", normalized_raw)
    normalized_raw = re.sub(r"(?<!\d)([1-4])\s+(\d{3})(?!\d)", r"\1.\2", normalized_raw)
    raw_candidates = []
    for match in re.finditer(r"[1-9][.,]\d{3}", normalized_raw):
        raw_candidates.append((match.start(), float(match.group().replace(",", "."))))
    for match in re.finditer(r"(?<![\d.,])([1-9]\d{3})(?!\d)", normalized_raw):
        digits = match.group(1)
        raw_candidates.append((match.start(), float(f"{digits[0]}.{digits[1:]}")))
    raw_values = [value for _, value in sorted(set(raw_candidates)) if 1.01 <= value <= 20]
    raw_ordered = [raw_values[i:i + 4] for i in range(0, len(raw_values), 4)
                   if len(raw_values[i:i + 4]) == 4]
    if len(raw_ordered) > len(ordered_numeric):
        ordered_numeric = raw_ordered
    return raw_text, found, ordered_numeric


def fair_probability(odds_a: float, odds_b: float) -> float:
    ia, ib = 1 / odds_a, 1 / odds_b
    return ia / (ia + ib)


def profit(rate: float, win_return: float, loss_cost: float) -> float:
    """丸勝ちは92%、丸負けは98%。分勝ち・分負けは表記どおりの率。"""
    if rate >= 0.999:
        return win_return
    if rate <= -0.999:
        return -loss_cost
    if rate != 0:
        return rate
    return 0.0


def outcome_rate(handicap: float, giver_bet: bool, winner_is_giver: bool, margin: int) -> float:
    key = min(HANDICAP, key=lambda x: abs(x - handicap))
    giver_rate = HANDICAP[key][min(margin, 3)] if winner_is_giver else -1.0
    # 受け側は同じ精算区分の反対側。勝ちと負けで料率はprofit()が切替える。
    return giver_rate if giver_bet else -giver_rate


def calc_side(p_ml: float, p_hc: float, handicap: float, giver_bet: bool,
              win_return: float, loss_cost: float) -> float:
    """新式：A=2点差以上、B=1点差、C=それ以外。引き分けは独立させない。"""
    if not 0 <= p_hc <= p_ml <= 1:
        raise ValueError("2点差以上の確率は、勝利確率以下にしてください")
    pattern_a = p_hc
    pattern_b = p_ml - p_hc
    pattern_c = 1 - p_ml
    if giver_bet:
        return (pattern_a * win_return
                + pattern_b * (win_return * (1 - handicap))
                - pattern_c * loss_cost)
    return (pattern_c * win_return
            - pattern_b * (loss_cost * (1 - handicap))
            - pattern_a * loss_cost)


def classify(ev_pct: float) -> tuple[str, int]:
    if ev_pct >= 5:
        return "大", 3
    if ev_pct >= 3:
        return "中", 2
    if ev_pct > 0:
        return "小", 1
    return "見送り", 0


st.set_page_config(page_title="NPB 期待値ツール", page_icon="⚾", layout="wide")
st.title("⚾ NPB スポーツベット期待値ツール")
st.caption("Pinnacleのマネーラインを市場確率に変換し、ハンデ精算と資金管理ルールを適用します。")

with st.sidebar:
    st.header("計算設定")
    win_return = st.number_input("勝ち利益率 (%)", 0.0, 200.0, 92.0, 1.0) / 100
    loss_cost = st.number_input("負け支払率 (%)", 0.0, 200.0, 98.0, 1.0) / 100
    st.caption("引き分けは独立計算せず『本命勝利以外』へ含めます。")
    bankroll = st.number_input("総資金 (円)", 0, value=100000, step=10000)

tab1, tab2 = st.tabs(["入力・計算", "計算方法"])
with tab1:
    left, right = st.columns(2)
    with left:
        st.subheader("別サイトの対戦カード")
        pasted = st.text_area("文章を貼り付け", height=180,
            placeholder="巨人\n18:00\n中日\n\n横浜<03>\n18:00\nヤクルト")
        if st.button("貼り付け内容を表へ反映"):
            st.session_state.rows = parse_other_site(pasted)
            st.session_state.pop("games", None)
    with right:
        st.subheader("Pinnacleスクリーンショット")
        upload = st.file_uploader("PNG/JPGを選択", type=["png", "jpg", "jpeg"])
        if upload:
            st.image(upload, use_container_width=True)
            if st.button("画像からマネーラインを抽出", type="primary"):
                try:
                    raw_text, detected, ordered_numeric = ocr_moneylines(upload)
                    st.session_state.ocr = raw_text
                    if detected or ordered_numeric:
                        merged_rows = _merge_ocr_rows(
                            detected, st.session_state.get("rows"), ordered_numeric)
                        st.session_state.rows = merged_rows
                        st.session_state.pop("games", None)
                        hc_count = sum(not pd.isna(row.get("出し2点差以上(%)"))
                                       for row in merged_rows)
                        game_count = len(ordered_numeric) or len(detected)
                        st.success(
                            f"{game_count}試合のマネーラインを抽出しました。"
                            f" うち{hc_count}試合で±1.5市場も認識しました。"
                        )
                    else:
                        st.error("試合とオッズを組み合わせられませんでした。下のOCR結果を確認してください。")
                except Exception as exc:
                    st.error(f"画像認識に失敗しました: {exc}")
        if st.session_state.get("ocr"):
            with st.expander("OCRの生データを確認"):
                st.text_area("認識された文字", st.session_state.ocr, height=180)

    initial = st.session_state.get("rows") or [
        {"チーム1": "阪神", "オッズ1": 1.467, "チーム2": "広島", "オッズ2": 2.850, "出しチーム": "阪神", "ハンデ": "1.7", "出し2点差以上(%)": 50.5},
        {"チーム1": "横浜", "オッズ1": 1.769, "チーム2": "ヤクルト", "オッズ2": 2.160, "出しチーム": "横浜", "ハンデ": "0.3", "出し2点差以上(%)": None},
        {"チーム1": "巨人", "オッズ1": 1.854, "チーム2": "中日", "オッズ2": 2.040, "出しチーム": "巨人", "ハンデ": "0", "出し2点差以上(%)": None},
    ]
    st.subheader("対戦カードとオッズ（抽出後に必ず確認）")
    edited = st.data_editor(pd.DataFrame(initial), num_rows="dynamic", hide_index=True,
                            use_container_width=True, key="games")

    if st.button("期待値を計算", type="primary"):
        results = []
        for _, row in edited.iterrows():
            try:
                t1, t2 = norm_team(row["チーム1"]), norm_team(row["チーム2"])
                o1, o2 = float(row["オッズ1"]), float(row["オッズ2"])
                giver = norm_team(row["出しチーム"])
                hcap = parse_handicap(str(row["ハンデ"]))
                raw_2plus = row.get("出し2点差以上(%)")
                p_2plus = None if pd.isna(raw_2plus) else float(raw_2plus) / 100
                p1 = fair_probability(o1, o2)
                pg = p1 if giver == t1 else 1 - p1
                if p_2plus is None:
                    if abs(hcap) < 1e-9:
                        p_2plus = pg
                    else:
                        st.error(f"{t1} vs {t2}: 『出し2点差以上(%)』を入力してください。")
                        continue
                for team, is_giver in ((giver, True), (t2 if giver == t1 else t1, False)):
                    ev = calc_side(pg, p_2plus, hcap, is_giver, win_return, loss_cost)
                    rank, stake = classify(ev * 100)
                    results.append({"対戦": f"{t1} vs {t2}", "ベット": team,
                                    "区分": "出し" if is_giver else "もらい",
                                    "ハンデ": display_handicap(hcap), "市場勝率": f"{(pg if is_giver else 1-pg):.1%}",
                                    "EV": f"{ev*100:+.1f}%", "判定": rank,
                                    "推奨率": f"{stake}%", "推奨額": int(bankroll * stake / 100)})
            except (TypeError, ValueError, KeyError, ZeroDivisionError):
                st.error(f"入力を確認してください: {dict(row)}")
        st.session_state.results = pd.DataFrame(results)
        if results:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            pd.DataFrame(results).to_csv(DATA_DIR / f"ev_{stamp}.csv", index=False, encoding="utf-8-sig")

    if "results" in st.session_state:
        result_df = st.session_state.results
        st.subheader("計算結果")
        st.dataframe(result_df, hide_index=True, use_container_width=True)
        positives = result_df[result_df["判定"] != "見送り"]
        if not positives.empty:
            st.success("推奨: " + " / ".join(f"{r['ベット']} {r['判定']}（{r['推奨額']:,}円）" for _, r in positives.iterrows()))
        st.download_button("結果CSVを保存", result_df.to_csv(index=False).encode("utf-8-sig"),
                           "ev_result.csv", "text/csv")

with tab2:
    st.markdown("""
### 計算の考え方

1. マネーライン両側のオッズを正規化し、出し側の真の勝率 `P_ML` を求めます。
2. -1.5市場の両側を正規化して求めた「出し側2点差以上の確率」を入力します。
3. A=`P_HC`、B=`P_ML-P_HC`、C=`1-P_ML` の3パターンへ分解します。
4. 分勝ち・分負けにも手数料を適用します（7分勝ちなら `92%×0.7=64.4%`）。
5. 引き分けは独立させずCへ含めます。
6. EV 5%以上＝大3%、3%以上＝中2%、0%超＝小1%、0%以下＝見送りです。

ハンデが0以外の場合、`出し2点差以上(%)`は必須です。
    """)
