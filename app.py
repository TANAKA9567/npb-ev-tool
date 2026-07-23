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
    "東京アクルトスワローズ": "ヤクルト",
    "アクルトスワローズ": "ヤクルト",
    "アクルト": "ヤクルト",
    "中目ドラゴンズ": "中日",
    "中ロドラゴンズ": "中日",
    "中日ドラゴン": "中日",
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
        rows.append({"チーム1": a[0], "オッズ1": None, "オッズ1±": None,
                     "チーム2": b[0], "オッズ2": None, "オッズ2±": None,
                     "出しチーム": giver, "ハンデ": display_handicap(handicap),
                     "出し2点差以上(%)": None, "もらい2点差以上(%)": None})
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
        detected_team1 = norm_team(detected.get("チーム1", ""))
        detected_team2 = norm_team(detected.get("チーム2", ""))
        known_teams = {team for team in (detected_team1, detected_team2) if team}
        candidates = [row for row in merged if known_teams and known_teams.issubset({
            norm_team(row.get("チーム1", "")), norm_team(row.get("チーム2", ""))
        })]
        match = candidates[0] if len(candidates) == 1 else None
        if match is None:
            # 貼り付け表がある場合、OCRだけで推測した不明な組み合わせは追加しない。
            continue
        # 上側または下側の片方だけ読めた場合も、画像内の位置から向きを確定する。
        if detected_team1:
            same_order = norm_team(match["チーム1"]) == detected_team1
        else:
            same_order = norm_team(match["チーム2"]) == detected_team2
        if same_order:
            match["オッズ1"], match["オッズ2"] = detected["オッズ1"], detected["オッズ2"]
            match["オッズ1±"], match["オッズ2±"] = detected.get("オッズ1±"), detected.get("オッズ2±")
        else:
            match["オッズ1"], match["オッズ2"] = detected["オッズ2"], detected["オッズ1"]
            match["オッズ1±"], match["オッズ2±"] = detected.get("オッズ2±"), detected.get("オッズ1±")
        giver = norm_team(match.get("出しチーム", ""))
        hcap = parse_handicap(str(match.get("ハンデ", "0")))
        favorite_slot = detected.get("_ocr_2plus_slot")
        if favorite_slot in (1, 2):
            favorite_is_match_team1 = (favorite_slot == 1) if same_order else (favorite_slot == 2)
            detected_favorite = norm_team(match["チーム1"] if favorite_is_match_team1 else match["チーム2"])
        else:
            detected_favorite = norm_team(detected.get("_ocr_2plus_team", ""))
        detected_2plus_pct = detected.get("_ocr_2plus_pct")
        if detected_2plus_pct is not None and detected_favorite:
            match_team1 = norm_team(match["チーム1"])
            match_team2 = norm_team(match["チーム2"])
            p_match_team1 = fair_probability(float(match["オッズ1"]), float(match["オッズ2"]))
            p_detected_ml = p_match_team1 if detected_favorite == match_team1 else 1 - p_match_team1
            # 2点差以上勝率が通常勝率を超える場合は、+1.5側を-1.5側と
            # 取り違えているため、反対チームと補確率へ直す。
            if float(detected_2plus_pct) / 100 > p_detected_ml + 0.005:
                detected_favorite = match_team2 if detected_favorite == match_team1 else match_team1
                detected_2plus_pct = 100 - float(detected_2plus_pct)
        if abs(hcap) > 1e-9 and detected_2plus_pct is not None:
            receiver = (norm_team(match["チーム2"]) if giver == norm_team(match["チーム1"])
                        else norm_team(match["チーム1"]))
            if giver == detected_favorite:
                match["出し2点差以上(%)"] = round(float(detected_2plus_pct), 2)
            elif receiver == detected_favorite:
                match["もらい2点差以上(%)"] = round(float(detected_2plus_pct), 2)
    # チーム文字が崩れても、貼り付け表と画像の試合数が同じなら上から順に補完する。
    if ordered_numeric and len(ordered_numeric) == len(merged):
        for row, values in zip(merged, ordered_numeric):
            if len(values) < 2:
                continue
            row["オッズ1"], row["オッズ2"] = values[0], values[1]
            if len(values) >= 4:
                row["オッズ1±"], row["オッズ2±"] = values[2], values[3]
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
                favorite_ml_probability = (fair_probability(values[0], values[1])
                                           if favorite == team1
                                           else fair_probability(values[1], values[0])) * 100
                if probability <= favorite_ml_probability + 0.5:
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
    # 携帯スクリーンショットは縦長だけでなく、上下を切り取るとほぼ正方形になる。
    # PinnacleのPC版は横長なので、縦横比0.70以上を携帯配置として扱う。
    is_mobile_layout = original.height / max(original.width, 1) >= 0.70
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
                          "オッズ1±": values[2] if len(values) >= 4 else None,
                          "チーム2": second["team"], "オッズ2": values[1],
                          "オッズ2±": values[3] if len(values) >= 4 else None,
                          "出しチーム": first["team"], "ハンデ": "0",
                          "出し2点差以上(%)": None, "もらい2点差以上(%)": None,
                          "_ocr_2plus_team": two_plus_team,
                          "_ocr_2plus_pct": two_plus_pct})

    # PC版も数字の横一列を1試合として扱う。全体のチーム名読み順が一部欠けても、
    # 同じ高さのブロックで片方を認識できれば貼り付け対戦カードから相手を補完できる。
    desktop_coordinate_detected = []
    if not is_mobile_layout and numeric_groups:
        group_centers = [sum(item["y"] for item in group) / len(group) for group in numeric_groups]
        for group_index, group in enumerate(numeric_groups):
            center = group_centers[group_index]
            if group_index == 0:
                low = center - (group_centers[1] - center) / 2 if len(group_centers) > 1 else center - 50 * scale
            else:
                low = (group_centers[group_index - 1] + center) / 2
            if group_index == len(group_centers) - 1:
                high = center + (center - group_centers[group_index - 1]) / 2 if group_index else center + 50 * scale
            else:
                high = (center + group_centers[group_index + 1]) / 2

            nearby = []
            for team_item in teams:
                if low <= team_item["y"] < high and team_item["team"] not in [item["team"] for item in nearby]:
                    nearby.append(team_item)
            nearby = sorted(nearby, key=lambda item: item["y"])
            if len(nearby) > 2:
                nearby = sorted(nearby, key=lambda item: abs(item["y"] - center))[:2]
                nearby.sort(key=lambda item: item["y"])

            values = []
            for item in sorted(group, key=lambda item: item["x"]):
                values.extend(item["values"])
            if not nearby or len(values) < 2:
                continue
            if len(nearby) >= 2:
                team1, team2 = nearby[0]["team"], nearby[1]["team"]
            elif nearby[0]["y"] <= center:
                team1, team2 = nearby[0]["team"], ""
            else:
                team1, team2 = "", nearby[0]["team"]
            ml1, ml2 = values[:2]
            two_plus_slot, two_plus_pct = None, None
            if len(values) >= 4:
                two_plus_slot = 1 if ml1 < ml2 else 2
                if two_plus_slot == 1:
                    two_plus_pct = fair_probability(values[2], values[3]) * 100
                else:
                    two_plus_pct = fair_probability(values[3], values[2]) * 100
            desktop_coordinate_detected.append({
                "チーム1": team1, "オッズ1": ml1,
                "オッズ1±": values[2] if len(values) >= 4 else None,
                "チーム2": team2, "オッズ2": ml2,
                "オッズ2±": values[3] if len(values) >= 4 else None,
                "出しチーム": team1, "ハンデ": "0",
                "出し2点差以上(%)": None, "もらい2点差以上(%)": None,
                "_ocr_2plus_slot": two_plus_slot,
                "_ocr_2plus_pct": two_plus_pct,
            })
        if desktop_coordinate_detected:
            found = desktop_coordinate_detected
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
    if is_mobile_layout:
        # 携帯版の読み順 [ML1, HC1, ML2, HC2] を共通形式へ変換。
        raw_ordered = [[row[0], row[2], row[1], row[3]] for row in raw_ordered]
    if is_mobile_layout or len(raw_ordered) > len(ordered_numeric):
        ordered_numeric = raw_ordered
    if desktop_coordinate_detected:
        # PC版もチーム名で照合し、貼り付け欄の順番による誤配を防ぐ。
        ordered_numeric = []

    if is_mobile_layout:
        # 携帯版は各試合の境界線が画面幅いっぱいに入る。境界ごとに右半分を
        # 切り出して再OCRし、全体OCRで欠落した数字を補う。
        gray_original = ImageOps.grayscale(original)
        separator_rows = []
        for y in range(50, gray_original.height):
            pixels = list(gray_original.crop((0, y, gray_original.width, y + 1)).getdata())
            mean = sum(pixels) / len(pixels)
            if max(pixels) - min(pixels) <= 3 and mean < 240:
                separator_rows.append(y)
        separator_groups = []
        for y in separator_rows:
            if not separator_groups or y - separator_groups[-1][-1] > 2:
                separator_groups.append([y])
            else:
                separator_groups[-1].append(y)
        boundaries = [round(sum(group) / len(group)) for group in separator_groups]
        # 近すぎる装飾線を除き、試合ブロックを作る。
        filtered_boundaries = []
        for y in boundaries:
            if not filtered_boundaries or y - filtered_boundaries[-1] > 80:
                filtered_boundaries.append(y)
        # 携帯スクリーンショットの先頭・末尾が途中で切れていても、
        # 画像端を仮の境界として最初／最後の試合を処理する。
        if not filtered_boundaries or filtered_boundaries[0] > 80:
            filtered_boundaries.insert(0, 0)
        if filtered_boundaries[-1] < original.height - 80:
            filtered_boundaries.append(original.height)
        mobile_rows = []
        mobile_detected = []
        mobile_debug = []
        for top, bottom in zip(filtered_boundaries, filtered_boundaries[1:]):
            if bottom - top < 100:
                continue
            middle = (top + bottom) // 2
            boxes = [
                (0.52, top + 20, 0.76, middle),
                (0.52, middle, 0.76, bottom - 15),
                (0.76, top + 20, 0.995, middle),
                (0.76, middle, 0.995, bottom - 15),
            ]
            values = []
            runline_signs = [None, None]
            debug_parts = []
            for box_index, (x1, y1, x2, y2) in enumerate(boxes):
                # HC欄は上部の「±1.5」を除き、下側のオッズだけ読む。
                if box_index >= 2:
                    sign_crop = original.crop((
                        int(original.width * x1), y1,
                        int(original.width * x2), y1 + int((y2 - y1) * 0.48),
                    ))
                    sign_crop = sign_crop.resize(
                        (sign_crop.width * 4, sign_crop.height * 4), Image.Resampling.LANCZOS
                    )
                    sign_text = pytesseract.image_to_string(
                        ImageOps.autocontrast(ImageOps.grayscale(sign_crop)),
                        lang="eng",
                        config="--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789.+-",
                    ).strip()
                    sign_match = re.search(r"([+-])\s*1[.,]5", sign_text)
                    if sign_match:
                        runline_signs[box_index - 2] = sign_match.group(1)
                    y1 += int((y2 - y1) * 0.38)
                source_crop = original.crop((int(original.width * x1), y1,
                                             int(original.width * x2), y2))
                enlarged = source_crop.resize((source_crop.width * 3, source_crop.height * 3),
                                              Image.Resampling.LANCZOS)
                gray_crop = ImageOps.autocontrast(ImageOps.grayscale(enlarged))
                attempts = [
                    (gray_crop.point(lambda pixel: 0 if pixel < 170 else 255), "7"),
                    (gray_crop, "6"),
                    (enlarged, "7"),
                ]
                detected_value = None
                attempt_texts = []
                for attempt_image, psm in attempts:
                    numeric_text = pytesseract.image_to_string(
                        attempt_image, lang="eng",
                        config=f"--oem 3 --psm {psm} -c tessedit_char_whitelist=0123456789.+-",
                    )
                    attempt_texts.append(numeric_text.strip())
                    matches = re.findall(r"[1-9][.,]\d{3}", numeric_text)
                    if matches:
                        detected_value = float(matches[-1].replace(",", "."))
                        break
                    compact = re.findall(r"(?<!\d)([1-9]\d{3})(?!\d)", numeric_text)
                    if compact:
                        digits = compact[-1]
                        detected_value = float(f"{digits[0]}.{digits[1:]}")
                        break
                # 個別OCRでも欠落した場合、全体の座標OCRから同じ枠内の数字を探す。
                if detected_value is None:
                    x_low, x_high = original.width * x1 * scale, original.width * x2 * scale
                    y_low, y_high = y1 * scale, y2 * scale
                    coordinate_hits = [item for item in numeric_odds
                                       if x_low <= item["x"] <= x_high
                                       and y_low <= item["y"] <= y_high]
                    if coordinate_hits:
                        center_x = (x_low + x_high) / 2
                        center_y = (y_low + y_high) / 2
                        nearest_hit = min(coordinate_hits,
                                          key=lambda item: abs(item["x"] - center_x)
                                          + abs(item["y"] - center_y))
                        if nearest_hit["values"]:
                            detected_value = nearest_hit["values"][-1]
                debug_parts.append(" -> ".join(attempt_texts))
                values.append(detected_value)

            # ±1.5市場は必ず反対符号の組なので、片方だけ読めた場合は補完する。
            if runline_signs[0] and not runline_signs[1]:
                runline_signs[1] = "+" if runline_signs[0] == "-" else "-"
            elif runline_signs[1] and not runline_signs[0]:
                runline_signs[0] = "+" if runline_signs[1] == "-" else "-"

            # 符号が欠落・誤認識した場合は「2点差以上勝率 <= 通常勝率」を使って補正する。
            if all(value is not None for value in values):
                ml1, ml2, runline1, runline2 = values
                p_ml1 = fair_probability(ml1, ml2)
                p_minus1 = fair_probability(runline1, runline2)
                valid_minus = [
                    p_minus1 <= p_ml1 + 0.005,
                    (1 - p_minus1) <= (1 - p_ml1) + 0.005,
                ]
                minus_index = (runline_signs.index("-") if "-" in runline_signs else None)
                if minus_index is not None and not valid_minus[minus_index] and valid_minus[1 - minus_index]:
                    runline_signs = ["+", "-"] if minus_index == 0 else ["-", "+"]
                elif minus_index is None and valid_minus.count(True) == 1:
                    runline_signs = ["-", "+"] if valid_minus[0] else ["+", "-"]

            # 携帯版は貼り付け欄と画像の試合順が異なることがある。
            # 左側を試合単位でOCRし、数字を必ずその2チームへ結び付ける。
            team_texts = []
            block_height = bottom - top
            block_teams = [None, None]
            # チーム名2行は試合ブロック上部にあるため、行ごとに狭く切り出す。
            team_bands = ((0.16, 0.40), (0.36, 0.61))
            for slot, (start_rate, end_rate) in enumerate(team_bands):
                y1 = top + int(block_height * start_rate)
                y2 = top + int(block_height * end_rate)
                source_team = original.crop((0, y1, int(original.width * 0.53), y2))
                enlarged_team = source_team.resize(
                    (source_team.width * 3, source_team.height * 3), Image.Resampling.LANCZOS
                )
                gray_team = ImageOps.autocontrast(ImageOps.grayscale(enlarged_team))
                best_team, best_score = None, 0.0
                slot_texts = []
                for attempt_image, psm in ((gray_team, "7"), (enlarged_team, "7"), (gray_team, "6")):
                    team_text = pytesseract.image_to_string(
                        attempt_image, lang="jpn+eng", config=f"--oem 3 --psm {psm}"
                    ).strip()
                    slot_texts.append(team_text.replace("\n", " / "))
                    team, score = _match_team(team_text)
                    if team and score > best_score:
                        best_team, best_score = team, score
                # 曖昧な文字列を別チームと決めつけない。
                if best_score >= 0.68:
                    block_teams[slot] = best_team
                team_texts.append(" -> ".join(slot_texts))

            mobile_debug.append(
                " / ".join(debug_parts)
                + " | SIGNS: " + ", ".join(sign or "?" for sign in runline_signs)
                + " | TEAMS: " + " -> ".join(team_texts)
                + " => " + ", ".join(team or "?" for team in block_teams)
            )
            if all(value is not None for value in values):
                # 4分割済みなので共通形式 [ML1, ML2, HC1, HC2] の順。
                mobile_rows.append(values)
                if any(block_teams):
                    team1, team2 = block_teams
                    ml1, ml2, runline1, runline2 = values
                    if runline_signs[0] == "-":
                        two_plus_team = team1 or ""
                        two_plus_slot = 1
                        two_plus_pct = fair_probability(runline1, runline2) * 100
                    elif runline_signs[1] == "-":
                        two_plus_team = team2 or ""
                        two_plus_slot = 2
                        two_plus_pct = fair_probability(runline2, runline1) * 100
                    elif ml1 < ml2:
                        two_plus_team = team1 or ""
                        two_plus_slot = 1
                        two_plus_pct = fair_probability(runline1, runline2) * 100
                    else:
                        two_plus_team = team2 or ""
                        two_plus_slot = 2
                        two_plus_pct = fair_probability(runline2, runline1) * 100
                    sign1 = runline_signs[0] or "±"
                    sign2 = runline_signs[1] or "±"
                    mobile_detected.append({
                        "チーム1": team1 or "", "オッズ1": ml1,
                        "オッズ1±": f"{sign1}1.5 / {runline1:.3f}",
                        "チーム2": team2 or "", "オッズ2": ml2,
                        "オッズ2±": f"{sign2}1.5 / {runline2:.3f}",
                        "出しチーム": team1 or "", "ハンデ": "0",
                        "出し2点差以上(%)": None, "もらい2点差以上(%)": None,
                        "_ocr_2plus_team": two_plus_team,
                        "_ocr_2plus_slot": two_plus_slot,
                        "_ocr_2plus_pct": two_plus_pct,
                    })
        expected_mobile_rows = sum(1 for top, bottom in zip(filtered_boundaries, filtered_boundaries[1:])
                                   if bottom - top >= 100)
        if mobile_detected:
            # 対戦カード名で照合するため、順番だけの数値割り当ては無効にする。
            # 全体OCRの誤った組み合わせで、正しい携帯ブロック結果を上書きさせない。
            found = mobile_detected
            ordered_numeric = []
        elif mobile_rows and len(mobile_rows) == expected_mobile_rows:
            # チーム名を認識できなかった場合も、誤った試合へ割り当てない。
            ordered_numeric = []
        raw_text += "\n[MOBILE ODDS] " + " | ".join(mobile_debug)
    return raw_text, found, ordered_numeric


def fair_probability(odds_a: float, odds_b: float) -> float:
    ia, ib = 1 / odds_a, 1 / odds_b
    return ia / (ia + ib)


def parse_runline_input(value) -> tuple[str | None, float | None]:
    """「-1.5 / 2.390」などの手入力から符号とオッズを取り出す。"""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None, None
    text = str(value).strip().replace("＋", "+").replace("−", "-").replace("－", "-")
    sign_match = re.search(r"([+-])\s*1(?:[.,]5)?", text)
    sign = sign_match.group(1) if sign_match else None
    decimal_values = re.findall(r"(?<!\d)([1-9](?:[.,]\d{2,3}))(?!\d)", text)
    odds = float(decimal_values[-1].replace(",", ".")) if decimal_values else None
    if odds is None:
        compact = re.findall(r"(?<!\d)([1-9]\d{3})(?!\d)", text)
        if compact:
            digits = compact[-1]
            odds = float(f"{digits[0]}.{digits[1:]}")
    return sign, odds


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


SPECIAL_HANDICAP_KEYS = {
    1.5 + 1 / 6,            # 1半
    1.3 + 1 / 6,            # 1半3
    1.5 + 1 / 6 + 0.01,     # 1半5
    1.7 + 1 / 6,            # 1半7
    2.0,
}


def _special_handicap_key(handicap: float) -> float | None:
    return next((key for key in SPECIAL_HANDICAP_KEYS
                 if abs(handicap - key) < 0.0001), None)


def _giver_margin_rate(handicap: float, margin: int) -> float:
    """出し側の精算率。+1=丸勝ち、-1=丸負け、0=返金。"""
    special_key = _special_handicap_key(handicap)
    if special_key is not None:
        return HANDICAP[special_key][min(margin, 3)]
    if margin <= 0:
        return -min(max(handicap, 0), 1)
    if margin == 1:
        return (1 - handicap) if handicap <= 1 else -min(handicap - 1, 1)
    # 通常の小数ハンデは、ユーザー定義により2点差以上で丸勝ち。
    return 1.0


def _settlement_value(rate: float, win_return: float, loss_cost: float) -> float:
    if rate > 0:
        return win_return * rate
    if rate < 0:
        return loss_cost * rate
    return 0.0


def needs_two_run_split(handicap: float) -> bool:
    """2点差と3点差以上で精算が変わる特殊ハンデか。"""
    key = _special_handicap_key(handicap)
    return key is not None and HANDICAP[key][2] != HANDICAP[key][3]


def calc_side(p_ml: float, p_hc: float, handicap: float, giver_bet: bool,
              win_return: float, loss_cost: float, draw_probability: float = 0.0,
              two_run_share: float = 0.5) -> float:
    """A=2点差以上、B=1点差、C=相手勝利、D=引き分け。"""
    if not 0 <= draw_probability < 1:
        raise ValueError("引き分け確率は0%以上100%未満にしてください")
    if not 0 <= p_hc <= p_ml <= 1 - draw_probability:
        raise ValueError("2点差以上の確率は、勝利確率以下にしてください")
    if not 0 <= two_run_share <= 1:
        raise ValueError("2点差勝ちの比率は0%以上100%以下にしてください")
    pattern_a2 = p_hc * two_run_share
    pattern_a3 = p_hc - pattern_a2
    pattern_b = p_ml - p_hc
    pattern_c = 1 - p_ml - draw_probability
    pattern_d = draw_probability
    giver_rates = {
        0: _giver_margin_rate(handicap, 0),
        1: _giver_margin_rate(handicap, 1),
        2: _giver_margin_rate(handicap, 2),
        3: _giver_margin_rate(handicap, 3),
        "loss": -1.0,
    }
    probabilities = (
        (pattern_d, giver_rates[0]),
        (pattern_b, giver_rates[1]),
        (pattern_a2, giver_rates[2]),
        (pattern_a3, giver_rates[3]),
        (pattern_c, giver_rates["loss"]),
    )
    return sum(
        probability * _settlement_value(
            rate if giver_bet else -rate, win_return, loss_cost
        )
        for probability, rate in probabilities
    )


def classify(ev_pct: float) -> tuple[str, int]:
    # 判定は表示用に丸める前のEVを使用する。
    if ev_pct >= 8:
        return "大", 3
    if ev_pct >= 4:
        return "中", 2
    if ev_pct >= 1.5:
        return "小", 1
    return "見送り", 0


st.set_page_config(page_title="NPB 期待値ツール", page_icon="⚾", layout="wide")
st.title("⚾ NPB スポーツベット期待値ツール")
st.caption("Pinnacleのマネーラインを市場確率に変換し、ハンデ精算と資金管理ルールを適用します。")

with st.sidebar:
    st.header("計算設定")
    win_return = st.number_input("勝ち利益率 (%)", 0.0, 200.0, 92.0, 1.0) / 100
    loss_cost = st.number_input("負け支払率 (%)", 0.0, 200.0, 98.0, 1.0) / 100
    draw_probability = st.number_input(
        "引き分け確率 (%)", 0.0, 99.0, 5.0, 0.5,
        help="9回終了時点の引き分け確率です。",
    ) / 100
    st.caption("勝敗確率は、引き分けを除いた残りの確率へマネーライン比率で配分します。")
    bankroll = st.number_input("総資金 (円)", 0, value=1000000, step=10000)

tab1, tab2 = st.tabs(["入力・計算", "計算方法"])
with tab1:
    left, right = st.columns(2)
    with left:
        st.subheader("別サイトの対戦カード")
        pasted = st.text_area("文章を貼り付け", height=180,
            placeholder="巨人\n18:00\n中日\n\n横浜<03>\n18:00\nヤクルト")
        if st.button("貼り付け内容を表へ反映"):
            st.session_state.rows = parse_other_site(pasted)
            st.session_state.games_version = st.session_state.get("games_version", 0) + 1
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
                        # 前回OCRの誤認識行を残さず、現在の貼り付け文章を正本にする。
                        pasted_rows = parse_other_site(pasted) if pasted.strip() else []
                        base_rows = pasted_rows or st.session_state.get("rows")
                        merged_rows = _merge_ocr_rows(
                            detected, base_rows, ordered_numeric)
                        st.session_state.rows = merged_rows
                        st.session_state.games_version = st.session_state.get("games_version", 0) + 1
                        hc_count = sum(
                            not pd.isna(row.get("出し2点差以上(%)"))
                            or not pd.isna(row.get("もらい2点差以上(%)"))
                            for row in merged_rows
                        )
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
        {"チーム1": "阪神", "オッズ1": 1.467, "オッズ1±": "-1.5 / 1.909", "チーム2": "広島", "オッズ2": 2.850, "オッズ2±": "+1.5 / 1.925", "出しチーム": "阪神", "ハンデ": "1.7", "出し2点差以上(%)": 50.5, "もらい2点差以上(%)": None},
        {"チーム1": "横浜", "オッズ1": 1.769, "オッズ1±": None, "チーム2": "ヤクルト", "オッズ2": 2.160, "オッズ2±": None, "出しチーム": "横浜", "ハンデ": "0.3", "出し2点差以上(%)": None, "もらい2点差以上(%)": None},
        {"チーム1": "巨人", "オッズ1": 1.854, "オッズ1±": None, "チーム2": "中日", "オッズ2": 2.040, "オッズ2±": None, "出しチーム": "巨人", "ハンデ": "0", "出し2点差以上(%)": None, "もらい2点差以上(%)": None},
    ]
    st.subheader("対戦カードとオッズ（抽出後に必ず確認）")
    table_columns = [
        "チーム1", "オッズ1", "オッズ1±",
        "チーム2", "オッズ2", "オッズ2±",
        "出しチーム", "ハンデ", "出し2点差以上(%)", "もらい2点差以上(%)",
    ]
    initial_df = pd.DataFrame(initial).reindex(columns=table_columns)
    edited = st.data_editor(initial_df, num_rows="dynamic", hide_index=True,
                            use_container_width=True,
                            key=f"games_{st.session_state.get('games_version', 0)}")

    if st.session_state.get("runline_notice"):
        st.success(st.session_state.pop("runline_notice"))
    if st.session_state.get("runline_warning"):
        st.warning(st.session_state.pop("runline_warning"))

    if st.button("±1.5オッズから2点差以上％を計算"):
        updated_rows = edited.to_dict("records")
        calculated_count = 0
        skipped_matches = []
        for updated_row in updated_rows:
            updated_row["出し2点差以上(%)"] = None
            updated_row["もらい2点差以上(%)"] = None
            sign1, runline1 = parse_runline_input(updated_row.get("オッズ1±"))
            sign2, runline2 = parse_runline_input(updated_row.get("オッズ2±"))
            try:
                ml1 = float(updated_row["オッズ1"])
                ml2 = float(updated_row["オッズ2"])
            except (TypeError, ValueError):
                skipped_matches.append(
                    f"{updated_row.get('チーム1', '?')} vs {updated_row.get('チーム2', '?')}"
                )
                continue
            if runline1 is None or runline2 is None:
                skipped_matches.append(
                    f"{updated_row.get('チーム1', '?')} vs {updated_row.get('チーム2', '?')}"
                )
                continue
            if sign1 and not sign2:
                sign2 = "+" if sign1 == "-" else "-"
            elif sign2 and not sign1:
                sign1 = "+" if sign2 == "-" else "-"

            p_ml1 = fair_probability(ml1, ml2)
            p_minus1 = fair_probability(runline1, runline2)
            valid_minus = [
                p_minus1 <= p_ml1 + 0.005,
                (1 - p_minus1) <= (1 - p_ml1) + 0.005,
            ]
            minus_index = 0 if sign1 == "-" else (1 if sign2 == "-" else None)
            if minus_index is not None and not valid_minus[minus_index] and valid_minus[1 - minus_index]:
                minus_index = 1 - minus_index
            elif minus_index is None and valid_minus.count(True) == 1:
                minus_index = 0 if valid_minus[0] else 1
            if minus_index is None:
                skipped_matches.append(
                    f"{updated_row.get('チーム1', '?')} vs {updated_row.get('チーム2', '?')}"
                )
                continue

            sign1, sign2 = (("-", "+") if minus_index == 0 else ("+", "-"))
            updated_row["オッズ1±"] = f"{sign1}1.5 / {runline1:.3f}"
            updated_row["オッズ2±"] = f"{sign2}1.5 / {runline2:.3f}"
            two_plus_probability = (
                fair_probability(runline1, runline2) if minus_index == 0
                else fair_probability(runline2, runline1)
            ) * 100
            minus_team = norm_team(
                updated_row["チーム1"] if minus_index == 0 else updated_row["チーム2"]
            )
            giver = norm_team(updated_row.get("出しチーム", ""))
            if minus_team == giver:
                updated_row["出し2点差以上(%)"] = round(two_plus_probability, 2)
            else:
                updated_row["もらい2点差以上(%)"] = round(two_plus_probability, 2)
            calculated_count += 1

        st.session_state.rows = updated_rows
        st.session_state.runline_notice = f"{calculated_count}試合の2点差以上％を計算しました。"
        if skipped_matches:
            st.session_state.runline_warning = (
                "計算できなかった試合: " + " / ".join(skipped_matches)
                + "。±1.5の両側オッズを確認してください。"
            )
        st.session_state.games_version = st.session_state.get("games_version", 0) + 1
        st.rerun()

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
                conditional_pg = p1 if giver == t1 else 1 - p1
                pg = conditional_pg * (1 - draw_probability)
                missing_2plus = False
                if p_2plus is None:
                    if abs(hcap) < 1e-9:
                        p_2plus = pg
                    else:
                        missing_2plus = True
                split_2plus = needs_two_run_split(hcap)
                use_ev_range = missing_2plus or split_2plus
                if p_2plus is not None and p_2plus > pg + 0.0001:
                    st.error(
                        f"{t1} vs {t2}: 2点差以上勝率（{p_2plus:.1%}）が"
                        f"通常勝率（{pg:.1%}）を超えています。画像を再抽出するか数値を確認してください。"
                    )
                    continue
                for team, is_giver in ((giver, True), (t2 if giver == t1 else t1, False)):
                    if use_ev_range:
                        p_hc_candidates = [0.0, pg] if missing_2plus else [p_2plus]
                        two_run_candidates = [0.0, 1.0] if split_2plus else [0.5]
                        endpoint_evs = [
                            calc_side(
                                pg, candidate_p_hc, hcap, is_giver,
                                win_return, loss_cost, draw_probability,
                                two_run_share=candidate_two_run,
                            )
                            for candidate_p_hc in p_hc_candidates
                            for candidate_two_run in two_run_candidates
                        ]
                        ev_low, ev_high = min(endpoint_evs), max(endpoint_evs)
                        # 未知の2点差確率を都合よく仮定せず、最低EVで判定する。
                        rank, stake = classify(ev_low * 100)
                        ev_display = f"{ev_low*100:+.1f}% ～ {ev_high*100:+.1f}%"
                        if missing_2plus and split_2plus:
                            calculation_type = "2点差確率・内訳不明"
                        elif missing_2plus:
                            calculation_type = "2点差以上確率なし"
                        else:
                            calculation_type = "2点差/3点差内訳不明"
                    else:
                        ev = calc_side(pg, p_2plus, hcap, is_giver, win_return, loss_cost,
                                       draw_probability)
                        rank, stake = classify(ev * 100)
                        ev_display = f"{ev*100:+.1f}%"
                        calculation_type = "通常"
                    results.append({"対戦": f"{t1} vs {t2}", "ベット": team,
                                    "区分": "出し" if is_giver else "もらい",
                                    "ハンデ": display_handicap(hcap),
                                    "市場勝率": f"{(pg if is_giver else 1-draw_probability-pg):.1%}",
                                    "引分確率": f"{draw_probability:.1%}",
                                    "EV": ev_display, "計算区分": calculation_type, "判定": rank,
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

1. マネーライン両側のオッズを正規化し、設定した引き分け確率を除いた残りへ
   両チームの勝率を配分して、出し側の真の勝率 `P_ML` を求めます。
2. -1.5市場の両側を正規化して「出し側2点差以上の確率」を求めます。
3. A=`P_HC`、B=`P_ML-P_HC`、C=`1-P_ML` の3パターンへ分解します。
4. 分勝ち・分負けにも手数料を適用します（7分勝ちなら `92%×0.7=64.4%`）。
   ハンデ1.8の1点差勝ちは8分負けなので `-98%×0.8=-78.4%`、2点差以上は丸勝ちです。
5. 引き分けは独立した確率として計算し、ハンデ0.3なら出し側3分負け・
   もらい側3分勝ちのように精算します。
6. EV 8%以上＝大3%、4%以上8%未満＝中2%、1.5%以上4%未満＝小1%、
   1.5%未満＝見送りです。判定には丸める前のEVを使用します。

`出し2点差以上(%)`がない場合は、あり得る最小EV～最大EVを表示します。
判定と推奨額は、未知の確率を有利に仮定しない「最小EV」を基準にします。
`1半3`は2点差7分勝ち・3点差以上丸勝ち、`1半5`は2点差5分勝ち、
`1半7`は2点差3分勝ち、`2`は2点差返金として処理します。
±1.5市場では2点差と3点差以上の内訳が分からないため、これらは正しいEV範囲を表示します。
`もらい2点差以上(%)`は画像市場の確認用です。現在の精算式では出し側が勝てなかった
ケースの配当が共通なので、出し側2点差以上確率の代用にはしません。
表の±1.5オッズを手入力・修正した場合は、
「±1.5オッズから2点差以上％を計算」ボタンで確率欄を更新できます。
    """)
