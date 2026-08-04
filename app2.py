from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

PINNACLE_CONFIG_URL = "https://www.pinnacle.com/config/app.json"
PINNACLE_API_ROOT = "https://guest.api.arcadia.pinnacle.com/0.1"
PINNACLE_BASEBALL_SPORT_ID = 3
PINNACLE_TIMEOUT_SECONDS = 20

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

    # Pinnacleの読み取りデータは英語名で返る場合がある。
    "Yomiuri Giants": "巨人", "Tokyo Yomiuri Giants": "巨人",
    "Chunichi Dragons": "中日",
    "Yokohama DeNA BayStars": "横浜", "Yokohama BayStars": "横浜",
    "Tokyo Yakult Swallows": "ヤクルト", "Yakult Swallows": "ヤクルト",
    "Hanshin Tigers": "阪神",
    "Hiroshima Toyo Carp": "広島", "Hiroshima Carp": "広島",
    "Tohoku Rakuten Golden Eagles": "楽天", "Rakuten Golden Eagles": "楽天",
    "Chiba Lotte Marines": "ロッテ", "Lotte Marines": "ロッテ",
    "Hokkaido Nippon-Ham Fighters": "日本ハム", "Hokkaido Nippon Ham Fighters": "日本ハム",
    "Nippon-Ham Fighters": "日本ハム", "Nippon Ham Fighters": "日本ハム",
    "Saitama Seibu Lions": "西武", "Seibu Lions": "西武",
    "Orix Buffaloes": "オリックス",
    "Fukuoka SoftBank Hawks": "ソフトバンク", "Fukuoka Softbank Hawks": "ソフトバンク",
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
    if value in TEAM_TYPOS:
        return TEAM_TYPOS[value]
    if value in TEAM_ALIASES:
        return TEAM_ALIASES[value]
    folded = value.casefold()
    for alias, normalized in TEAM_ALIASES.items():
        if re.sub(r"\s+", "", alias).casefold() == folded:
            return normalized
    return value


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
                     "合計ライン": None, "オーバー": None, "アンダー": None,
                     "出しチーム": giver, "ハンデ": display_handicap(handicap),
                     "出し2点差以上(%)": None, "もらい2点差以上(%)": None})
    return rows


NPB_TEAMS = {
    "巨人", "中日", "横浜", "ヤクルト", "阪神", "広島",
    "楽天", "ロッテ", "日本ハム", "西武", "オリックス", "ソフトバンク",
}


class PinnacleFetchError(RuntimeError):
    """Pinnacleの公開オッズを安全に表示できる形で通知する例外。"""


def _read_json(url: str, headers: dict[str, str] | None = None) -> object:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; NPB-EV-Tool/2.0)",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=PINNACLE_TIMEOUT_SECONDS) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise PinnacleFetchError(
                "Pinnacle側のアクセス上限に達しました。少し待ってから再取得してください。"
            ) from exc
        raise PinnacleFetchError(
            f"Pinnacleから取得できませんでした（HTTP {exc.code}）。"
        ) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise PinnacleFetchError(
            "Pinnacleへの接続に失敗しました。通信状態を確認して再取得してください。"
        ) from exc


def _pinnacle_guest_headers() -> dict[str, str]:
    config = _read_json(PINNACLE_CONFIG_URL)
    try:
        api_key = config["api"]["haywire"]["apiKey"]
    except (KeyError, TypeError):
        raise PinnacleFetchError(
            "Pinnacleの公開設定から読取キーを確認できませんでした。"
        ) from None
    if not isinstance(api_key, str) or not api_key:
        raise PinnacleFetchError(
            "Pinnacleの公開設定から読取キーを確認できませんでした。"
        )
    return {
        "Referer": "https://www.pinnacle.com/ja/baseball/matchups/",
        "X-API-Key": api_key,
        "X-Language": "ja-JP",
    }


def _american_to_decimal(price: object) -> float | None:
    try:
        american = float(price)
    except (TypeError, ValueError):
        return None
    if american == 0:
        return None
    decimal = 1 + american / 100 if american > 0 else 1 + 100 / abs(american)
    return round(decimal, 3)


def _prices_by_designation(market: dict | None) -> dict[str, dict]:
    if not market:
        return {}
    return {
        str(price.get("designation")): price
        for price in market.get("prices", [])
        if price.get("designation")
    }


def _primary_market(markets: list[dict], market_type: str) -> dict | None:
    candidates = [
        market for market in markets
        if market.get("type") == market_type
        and market.get("period") == 0
        and market.get("status", "open") == "open"
        and not market.get("isAlternate", False)
    ]
    if market_type == "spread":
        candidates = [
            market for market in candidates
            if market.get("prices")
            and all(
                abs(abs(float(price.get("points", 0))) - 1.5) < 1e-9
                for price in market["prices"]
            )
        ]
    return candidates[0] if candidates else None


def _runline_text(price: dict | None) -> str | None:
    if not price:
        return None
    decimal = _american_to_decimal(price.get("price"))
    try:
        points = float(price.get("points"))
    except (TypeError, ValueError):
        return None
    if decimal is None:
        return None
    return f"{points:+g} / {decimal:.3f}"


def fetch_pinnacle_npb_odds() -> tuple[list[dict], dict]:
    """Pinnacleの公開画面用データから、受付中のNPB主要市場を取得する。"""
    headers = _pinnacle_guest_headers()
    matchups_url = (
        f"{PINNACLE_API_ROOT}/sports/{PINNACLE_BASEBALL_SPORT_ID}/matchups"
        "?withSpecials=false&brandId=0"
    )
    markets_url = (
        f"{PINNACLE_API_ROOT}/sports/{PINNACLE_BASEBALL_SPORT_ID}/markets/straight"
        "?primaryOnly=false&brandId=0"
    )
    matchups = _read_json(matchups_url, headers)
    markets = _read_json(markets_url, headers)
    if not isinstance(matchups, list) or not isinstance(markets, list):
        raise PinnacleFetchError("Pinnacleの返却形式を読み取れませんでした。")

    markets_by_matchup: dict[int, list[dict]] = {}
    for market in markets:
        if not isinstance(market, dict):
            continue
        matchup_id = market.get("matchupId")
        if isinstance(matchup_id, int):
            markets_by_matchup.setdefault(matchup_id, []).append(market)

    rows: list[dict] = []
    npb_matchup_count = 0
    for matchup in matchups:
        if not isinstance(matchup, dict) or matchup.get("type") != "matchup":
            continue
        participants = {
            participant.get("alignment"): participant
            for participant in matchup.get("participants", [])
            if participant.get("alignment") in {"home", "away"}
        }
        if "home" not in participants or "away" not in participants:
            continue
        home = norm_team(participants["home"].get("name", ""))
        away = norm_team(participants["away"].get("name", ""))
        if home not in NPB_TEAMS or away not in NPB_TEAMS:
            continue
        npb_matchup_count += 1

        matchup_markets = markets_by_matchup.get(matchup.get("id"), [])
        moneyline = _prices_by_designation(_primary_market(matchup_markets, "moneyline"))
        home_odds = _american_to_decimal(moneyline.get("home", {}).get("price"))
        away_odds = _american_to_decimal(moneyline.get("away", {}).get("price"))
        if home_odds is None or away_odds is None:
            continue

        spread = _prices_by_designation(_primary_market(matchup_markets, "spread"))
        total = _prices_by_designation(_primary_market(matchup_markets, "total"))
        over = total.get("over")
        under = total.get("under")
        over_odds = _american_to_decimal(over.get("price")) if over else None
        under_odds = _american_to_decimal(under.get("price")) if under else None
        total_line = over.get("points") if over else (under.get("points") if under else None)
        favorite = home if home_odds < away_odds else away

        rows.append({
            "チーム1": home,
            "オッズ1": home_odds,
            "オッズ1±": _runline_text(spread.get("home")),
            "チーム2": away,
            "オッズ2": away_odds,
            "オッズ2±": _runline_text(spread.get("away")),
            "合計ライン": total_line,
            "オーバー": over_odds,
            "アンダー": under_odds,
            "出しチーム": favorite,
            "ハンデ": "0",
            "出し2点差以上(%)": None,
            "もらい2点差以上(%)": None,
        })

    rows.sort(key=lambda row: (row["チーム1"], row["チーム2"]))
    return rows, {
        "取得時刻": datetime.now().astimezone().isoformat(timespec="seconds"),
        "野球全体": len(matchups),
        "NPB対戦": npb_matchup_count,
        "オッズ取得": len(rows),
    }


def merge_pinnacle_rows(
    fetched_rows: list[dict], existing_rows: list[dict] | None,
) -> tuple[list[dict], int, list[str]]:
    """貼り付けた対戦カードを正本にし、同じ2チームの市場だけ更新する。"""
    if not existing_rows:
        return [dict(row) for row in fetched_rows], len(fetched_rows), []

    fetched_by_teams = {
        frozenset((norm_team(row.get("チーム1", "")), norm_team(row.get("チーム2", "")))): row
        for row in fetched_rows
    }
    merged: list[dict] = []
    matched_count = 0
    unmatched: list[str] = []
    for existing in existing_rows:
        row = dict(existing)
        team1 = norm_team(row.get("チーム1", ""))
        team2 = norm_team(row.get("チーム2", ""))
        fetched = fetched_by_teams.get(frozenset((team1, team2)))
        if fetched is None:
            unmatched.append(f"{team1} vs {team2}")
            merged.append(row)
            continue

        same_order = norm_team(fetched.get("チーム1", "")) == team1
        if same_order:
            row["オッズ1"], row["オッズ2"] = fetched["オッズ1"], fetched["オッズ2"]
            row["オッズ1±"], row["オッズ2±"] = fetched["オッズ1±"], fetched["オッズ2±"]
        else:
            row["オッズ1"], row["オッズ2"] = fetched["オッズ2"], fetched["オッズ1"]
            row["オッズ1±"], row["オッズ2±"] = fetched["オッズ2±"], fetched["オッズ1±"]
        row["合計ライン"] = fetched["合計ライン"]
        row["オーバー"] = fetched["オーバー"]
        row["アンダー"] = fetched["アンダー"]
        row["出し2点差以上(%)"] = None
        row["もらい2点差以上(%)"] = None
        matched_count += 1
        merged.append(row)
    return merged, matched_count, unmatched


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


def _poisson_probabilities(mean: float, size: int):
    import numpy as np

    probabilities = np.empty(size, dtype=float)
    probabilities[0] = math.exp(-mean)
    for score in range(1, size):
        probabilities[score] = probabilities[score - 1] * mean / score
    return probabilities


def _baseline_score_matrix(ml_team1: float, over_probability: float,
                           total_line: float, size: int = 26):
    """MLとO/Uから独立ポアソンの基準得点分布を作る。"""
    import numpy as np

    if abs(total_line * 2 - round(total_line * 2)) > 1e-6 or int(round(total_line * 2)) % 2 == 0:
        raise ValueError("自動同点率は0.5刻みの合計ラインに対応しています")
    cutoff = math.floor(total_line)

    def poisson_over(total_mean: float) -> float:
        term = math.exp(-total_mean)
        cumulative = term
        for score in range(1, cutoff + 1):
            term *= total_mean / score
            cumulative += term
        return 1 - cumulative

    low, high = 0.2, 25.0
    if not poisson_over(low) <= over_probability <= poisson_over(high):
        raise ValueError("O/U市場から合計得点を推定できません")
    for _ in range(80):
        middle = (low + high) / 2
        if poisson_over(middle) < over_probability:
            low = middle
        else:
            high = middle
    total_mean = (low + high) / 2
    scores = np.arange(size)

    def matrix_for_share(team1_share: float):
        p1 = _poisson_probabilities(total_mean * team1_share, size)
        p2 = _poisson_probabilities(total_mean * (1 - team1_share), size)
        matrix = np.outer(p1, p2)
        return matrix / matrix.sum()

    low, high = 0.05, 0.95
    for _ in range(80):
        middle = (low + high) / 2
        matrix = matrix_for_share(middle)
        team1_win = matrix[scores[:, None] > scores[None, :]].sum()
        conditional_win = team1_win / (1 - np.trace(matrix))
        if conditional_win < ml_team1:
            low = middle
        else:
            high = middle
    return matrix_for_share((low + high) / 2)


def estimate_draw_probability(
    ml1: float, ml2: float,
    runline_sign1: str | None, runline1: float,
    runline_sign2: str | None, runline2: float,
    total_line: float, over_odds: float, under_odds: float,
    fixed_draw: float | None = None,
) -> dict:
    """3市場と採用同点率を同時に満たす、最終9回得点分布を作る。"""
    import numpy as np

    if min(ml1, ml2, runline1, runline2, over_odds, under_odds) <= 1:
        raise ValueError("すべてのオッズは1より大きい必要があります")
    ml_team1 = fair_probability(ml1, ml2)
    over_probability = fair_probability(over_odds, under_odds)
    if runline_sign1 == "-":
        minus_index = 0
        cover_probability = fair_probability(runline1, runline2)
    elif runline_sign2 == "-":
        minus_index = 1
        cover_probability = fair_probability(runline2, runline1)
    else:
        raise ValueError("±1.5市場のマイナス側を特定できません")

    base = _baseline_score_matrix(ml_team1, over_probability, total_line)
    size = base.shape[0]
    score1, score2 = np.indices((size, size))
    team1_win = score1 > score2
    non_draw = score1 != score2
    cover = ((score1 - score2 >= 2) if minus_index == 0 else (score2 - score1 >= 2))
    over = score1 + score2 > total_line
    preliminary_features = np.column_stack((
        (team1_win.astype(float) - ml_team1 * non_draw.astype(float)).ravel(),
        cover.astype(float).ravel(), over.astype(float).ravel(),
    ))
    preliminary_targets = np.array([0.0, cover_probability, over_probability])
    base_flat = np.maximum(base.ravel(), 1e-300)
    log_base = np.log(base_flat)

    def calibrate(features, targets):
        theta = np.zeros(features.shape[1])

        def distribution(parameters):
            log_weight = log_base + features @ parameters
            log_weight -= log_weight.max()
            weights = np.exp(log_weight)
            return weights / weights.sum()

        converged = False
        for _ in range(100):
            q = distribution(theta)
            expected = q @ features
            gradient = expected - targets
            if float(np.max(np.abs(gradient))) < 1e-10:
                converged = True
                break
            centered = features - expected
            covariance = centered.T @ (centered * q[:, None])
            try:
                step = np.linalg.solve(covariance + np.eye(features.shape[1]) * 1e-10, gradient)
            except np.linalg.LinAlgError as exc:
                raise ValueError("市場校正行列を解けません") from exc
            current_error = float(gradient @ gradient)
            accepted = False
            for reduction in range(16):
                candidate = theta - step * (0.5 ** reduction)
                candidate_q = distribution(candidate)
                if float(((candidate_q @ features - targets) ** 2).sum()) < current_error:
                    theta = candidate
                    accepted = True
                    break
            if not accepted:
                break
        q = distribution(theta)
        residual = float(np.max(np.abs(q @ features - targets)))
        if not converged or residual > 0.001:
            raise ValueError(f"市場を整合できません（再現誤差{residual:.2%}）")
        return q, residual

    # 1回目：3市場だけで未補正の市場モデル同点率を求める。
    preliminary_q, preliminary_residual = calibrate(
        preliminary_features, preliminary_targets
    )
    preliminary_probability = preliminary_q.reshape((size, size))
    raw_draw = float(np.trace(preliminary_probability))

    # 過去データで検証していないため、恣意的な高・中判定は行わず一律30%だけ反映する。
    if fixed_draw is None:
        adopted_draw = min(0.10, max(0.01, 0.05 + (raw_draw - 0.05) * 0.30))
        reflection_rate = 0.30
    else:
        adopted_draw = min(0.99, max(0.0, float(fixed_draw)))
        reflection_rate = 0.0

    # 2回目：採用同点率も制約へ追加し、確率条件が統一された最終分布を作る。
    draw_indicator = (score1 == score2).astype(float)
    final_features = np.column_stack((preliminary_features, draw_indicator.ravel()))
    final_targets = np.append(preliminary_targets, adopted_draw)
    final_q, final_residual = calibrate(final_features, final_targets)
    probability = final_q.reshape((size, size))
    q = np.maximum(final_q, 1e-300)
    information_distance = float(np.sum(q * np.log(q / base_flat)))

    team1_one = score1 - score2 == 1
    team1_two = score1 - score2 == 2
    team1_three_plus = score1 - score2 >= 3
    team2_one = score2 - score1 == 1
    team2_two = score2 - score1 == 2
    team2_three_plus = score2 - score1 >= 3
    reasons = []
    reasons.append("低得点傾向" if total_line <= 7.5 else "高得点傾向")
    reasons.append("接戦傾向" if abs(ml_team1 - 0.5) <= 0.08 else "実力差あり")
    return {
        "raw": raw_draw, "adopted": adopted_draw,
        "reflection_rate": reflection_rate,
        "reason": "・".join(reasons),
        "information_distance": information_distance,
        "market_residual": max(preliminary_residual, final_residual),
        "probability": probability,
        "team1_win": float(probability[team1_win].sum()),
        "team2_win": float(probability[score2 > score1].sum()),
        "team1_one": float(probability[team1_one].sum()),
        "team1_two": float(probability[team1_two].sum()),
        "team1_three_plus": float(probability[team1_three_plus].sum()),
        "team2_one": float(probability[team2_one].sum()),
        "team2_two": float(probability[team2_two].sum()),
        "team2_three_plus": float(probability[team2_three_plus].sum()),
    }


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


def score_distribution_ev(probability, handicap: float, giver_is_team1: bool,
                          giver_bet: bool, win_return: float, loss_cost: float) -> float:
    """最終9回得点分布の各スコアへハンデ精算を適用する。"""
    expected_value = 0.0
    for team1_score in range(probability.shape[0]):
        for team2_score in range(probability.shape[1]):
            giver_margin = (
                team1_score - team2_score if giver_is_team1
                else team2_score - team1_score
            )
            giver_rate = (
                _giver_margin_rate(handicap, giver_margin)
                if giver_margin >= 0 else -1.0
            )
            rate = giver_rate if giver_bet else -giver_rate
            expected_value += float(probability[team1_score, team2_score]) * _settlement_value(
                rate, win_return, loss_cost
            )
    return expected_value


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


TEAM_BADGES = {
    "巨人": ("G", "#F97700"), "阪神": ("T", "#F5C400"),
    "中日": ("D", "#1672C4"), "横浜": ("DB", "#0877BE"),
    "ヤクルト": ("YS", "#159447"), "広島": ("C", "#E6002D"),
    "ソフトバンク": ("H", "#F2C500"), "ロッテ": ("M", "#222222"),
    "西武": ("L", "#1256A0"), "楽天": ("E", "#8B1538"),
    "日本ハム": ("F", "#1B9BD1"), "オリックス": ("B", "#1C2340"),
}

# Wikimedia Commonsで公開されている各球団の実ロゴ／帽章。
# 個人利用画面ではこちらを優先し、未登録チームだけカラーアイコンへ戻す。
TEAM_LOGOS = {
    "巨人": "https://commons.wikimedia.org/wiki/Special:Redirect/file/Yomiuri_Giants_logo.svg",
    "阪神": "https://commons.wikimedia.org/wiki/Special:Redirect/file/Hanshin_tigers_insignia.svg",
    "中日": "https://commons.wikimedia.org/wiki/Special:Redirect/file/Chunichi_Dragons_insignia.svg",
    "横浜": "https://commons.wikimedia.org/wiki/Special:Redirect/file/Yokohama_DeNA_BayStars_insignia.svg",
    "ヤクルト": "https://commons.wikimedia.org/wiki/Special:Redirect/file/Tokyo_Yakult_Swallows_insignia.svg",
    "広島": "https://commons.wikimedia.org/wiki/Special:Redirect/file/Hiroshima_Toyo_Carp_insignia.svg",
    "ソフトバンク": "https://commons.wikimedia.org/wiki/Special:Redirect/file/Fukuoka_SoftBank_Hawks_insignia.svg",
    "ロッテ": "https://commons.wikimedia.org/wiki/Special:Redirect/file/Chiba_Lotte_Marines_insignia.svg",
    "西武": "https://commons.wikimedia.org/wiki/Special:Redirect/file/Seibu_lions_insignia.svg",
    "楽天": "https://commons.wikimedia.org/wiki/Special:Redirect/file/Rakuten_eagles_2024_logo.svg",
    "日本ハム": "https://commons.wikimedia.org/wiki/Special:Redirect/file/Hokkaido_Nippon-Ham_Fighters_insignia.svg",
    "オリックス": "https://commons.wikimedia.org/wiki/Special:Redirect/file/Orix_Buffaloes_insignia.svg",
}

HANDICAP_SELECT_OPTIONS = ["0"] + [f"{value / 10:g}" for value in range(1, 21)] + [
    "1半", "1半3", "1半5", "1半7",
]


def team_badge_svg(team: str) -> str:
    label, color = TEAM_BADGES.get(norm_team(team), ("NPB", "#475569"))
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="56" height="56" viewBox="0 0 56 56">
    <defs><filter id="s" x="-20%" y="-20%" width="140%" height="140%">
    <feDropShadow dx="0" dy="2" stdDeviation="2" flood-opacity="0.22"/></filter></defs>
    <circle cx="28" cy="28" r="24" fill="{color}" filter="url(#s)"/>
    <circle cx="28" cy="28" r="20" fill="none" stroke="white" stroke-opacity="0.75" stroke-width="2"/>
    <text x="28" y="34" text-anchor="middle" font-family="Arial,sans-serif" font-size="17" font-weight="800" fill="white">{label}</text>
    </svg>"""
    return svg


def render_team_icon(container, team: str) -> None:
    normalized = norm_team(team)
    logo_url = TEAM_LOGOS.get(normalized)
    if logo_url:
        container.image(logo_url, width=48)
    else:
        container.markdown(team_badge_svg(normalized), unsafe_allow_html=True)


def handicap_option_label(value: str) -> str:
    if re.fullmatch(r"0\.[1-9]", value):
        return value.replace("0.", "0")
    return value


def render_result_cards(result_df: pd.DataFrame) -> None:
    """計算結果を試合単位のスポーツ分析カードとして表示する。"""
    badge_colors = {"大": "red", "中": "orange", "小": "green", "見送り": "gray"}
    for matchup, match_rows in result_df.groupby("対戦", sort=False):
        teams = matchup.split(" vs ", 1)
        with st.container(border=True):
            header_logo1, header_name1, versus, header_logo2, header_name2 = st.columns(
                [0.08, 0.32, 0.12, 0.08, 0.40], vertical_alignment="center"
            )
            render_team_icon(header_logo1, teams[0])
            header_name1.markdown(f"### {teams[0]}")
            versus.markdown("#### VS", text_alignment="center")
            if len(teams) > 1:
                render_team_icon(header_logo2, teams[1])
                header_name2.markdown(f"### {teams[1]}")

            side_columns = st.columns(2, gap="medium")
            for side_column, (_, row) in zip(side_columns, match_rows.iterrows()):
                with side_column.container(border=True):
                    label_area, rank_area = st.columns([0.72, 0.28], vertical_alignment="center")
                    label_area.markdown(f"### {row['ベット']}")
                    label_area.caption(f"{row['区分']}｜ハンデ {row['ハンデ']}")
                    rank_area.badge(
                        row["判定"],
                        color=badge_colors.get(row["判定"], "gray"),
                        icon=":material/bolt:" if row["判定"] != "見送り" else ":material/remove_circle_outline:",
                    )

                    ev_col, win_col, draw_col = st.columns(3)
                    ev_col.metric("期待値 EV", row["EV"])
                    win_col.metric("9回勝率", row["9回勝利確率"])
                    draw_col.metric("採用同点率", row["採用同点率"])

                    if int(row["推奨額"]) > 0:
                        st.success(
                            f"{row['判定']}｜総資金の {row['推奨率']}｜{int(row['推奨額']):,}円",
                            icon=":material/trending_up:",
                        )
                    else:
                        st.caption("今回は見送り。基準EVに届いていません。")

                    with st.expander("確率とモデル詳細", icon=":material/analytics:"):
                        probability_columns = st.columns(4)
                        probability_columns[0].metric("1点差", row["1点差勝利"])
                        probability_columns[1].metric("2点差", row["2点差勝利"])
                        probability_columns[2].metric("3点差以上", row["3点差以上勝利"])
                        probability_columns[3].metric("ノービグ勝率", row["条件付きノービグ勝率"])
                        st.caption(
                            f"計算区分: {row['計算区分']} ｜ 推定根拠: {row['推定根拠']} ｜ "
                            f"市場再現誤差: {row['市場再現誤差']}"
                        )


st.set_page_config(page_title="NPB期待値ラボ", page_icon="⚾", layout="wide")
st.markdown("""
<style>
.stApp {
  background:
    radial-gradient(circle at 88% 2%, rgba(59,130,246,.12) 0, transparent 28rem),
    radial-gradient(circle at 15% 100%, rgba(14,165,233,.09) 0, transparent 34rem),
    linear-gradient(145deg,#f8fbff 0%,#edf3fa 52%,#f5f8fc 100%);
  background-attachment: fixed;
}
[data-testid="stSidebar"] {
  background:linear-gradient(180deg,#eaf0f8 0%,#e2eaf5 100%);
}
[data-testid="stSidebar"] label,[data-testid="stSidebar"] p,[data-testid="stSidebar"] h2 {color:#142033!important;}
.stButton>button,.stLinkButton>a {
  border-radius:999px!important;
  font-weight:750!important;
  min-height:2.8rem;
  box-shadow:0 7px 20px rgba(37,99,235,.13);
}
[data-testid="stForm"] {
  background:rgba(255,255,255,.90);
  border:1px solid #b8c6d9;
  border-radius:20px;
  backdrop-filter:blur(14px);
}
h1,h2,h3 {letter-spacing:-.035em;}

/* 明るい背景上で確実に読める文字色 */
[data-testid="stMain"] h1,
[data-testid="stMain"] h2,
[data-testid="stMain"] h3,
[data-testid="stMain"] h4,
[data-testid="stMain"] h5,
[data-testid="stMain"] h6 {
  color:#10223b!important;
}
[data-testid="stMain"] [data-testid="stMarkdownContainer"] > p,
[data-testid="stMain"] [data-testid="stWidgetLabel"] p,
[data-testid="stMain"] [data-testid="stExpander"] summary p,
[data-testid="stMain"] [role="tab"] p {
  color:#22344d!important;
}
[data-testid="stMain"] [data-testid="stCaptionContainer"] p {
  color:#566a84!important;
}
[data-testid="stMain"] [data-testid="stMetricLabel"] p {
  color:#536780!important;
}
[data-testid="stMain"] [data-testid="stMetricValue"] {
  color:#10223b!important;
}
/* カードと折りたたみ領域の枠を明確にする */
[data-testid="stMain"] [data-testid="stVerticalBlockBorderWrapper"] {
  background:rgba(255,255,255,.88);
  border-color:#aebed2!important;
  box-shadow:0 10px 28px rgba(40,69,108,.08);
}
[data-testid="stMain"] [data-testid="stExpander"] {
  background:rgba(255,255,255,.72);
  border-color:#b8c6d9!important;
}
/* 入力部品とボタンのコントラスト */
[data-testid="stMain"] input,
[data-testid="stMain"] textarea,
[data-testid="stMain"] [data-baseweb="select"] *,
[data-testid="stMain"] .stLinkButton a,
[data-testid="stMain"] .stLinkButton a p,
[data-testid="stMain"] .stButton button[kind="secondary"],
[data-testid="stMain"] .stButton button[kind="secondary"] p {
  color:#102033!important;
}
[data-testid="stMain"] .stButton button[kind="primary"] p {
  color:#ffffff!important;
}
</style>
""", unsafe_allow_html=True)

with st.container(horizontal=True, vertical_alignment="center"):
    st.title("NPB期待値ラボ")
    st.badge("3市場統一モデル", color="blue", icon=":material/verified:")
st.caption("PinnacleのML・±1.5・O/Uを統合し、9回得点分布からハンデ別EVを算出します。")

with st.sidebar:
    st.header("計算設定")
    win_return = st.number_input("勝ち利益率 (%)", 0.0, 200.0, 92.0, 1.0) / 100
    loss_cost = st.number_input("負け支払率 (%)", 0.0, 200.0, 98.0, 1.0) / 100
    draw_mode = st.segmented_control(
        "引き分け計算", ["3市場から自動推定", "5%固定"], default="3市場から自動推定"
    )
    draw_probability = st.number_input(
        "引き分け確率 (%)", 0.0, 99.0, 5.0, 0.5,
        help="5%固定、または自動推定できない試合の予備値です。",
    ) / 100
    st.caption("自動推定は基準5%へ補正し、採用値を1～10%に制限します。")
    bankroll = st.number_input("総資金 (円)", 0, value=1000000, step=10000)

tab1, tab2 = st.tabs([":material/calculate: 入力・計算", ":material/menu_book: 計算方法"])
with tab1:
    initial = st.session_state.get("rows") or [
        {"チーム1": "阪神", "オッズ1": 1.467, "オッズ1±": "-1.5 / 1.909", "チーム2": "広島", "オッズ2": 2.850, "オッズ2±": "+1.5 / 1.925", "合計ライン": None, "オーバー": None, "アンダー": None, "出しチーム": "阪神", "ハンデ": "1.7", "出し2点差以上(%)": 50.5, "もらい2点差以上(%)": None},
        {"チーム1": "横浜", "オッズ1": 1.769, "オッズ1±": None, "チーム2": "ヤクルト", "オッズ2": 2.160, "オッズ2±": None, "合計ライン": None, "オーバー": None, "アンダー": None, "出しチーム": "横浜", "ハンデ": "0.3", "出し2点差以上(%)": None, "もらい2点差以上(%)": None},
        {"チーム1": "巨人", "オッズ1": 1.854, "オッズ1±": None, "チーム2": "中日", "オッズ2": 2.040, "オッズ2±": None, "合計ライン": None, "オーバー": None, "アンダー": None, "出しチーム": "巨人", "ハンデ": "0", "出し2点差以上(%)": None, "もらい2点差以上(%)": None},
    ]

    if st.session_state.get("fetch_notice"):
        st.success(st.session_state.pop("fetch_notice"), icon=":material/check_circle:")
    if st.session_state.get("fetch_warning"):
        st.warning(st.session_state.pop("fetch_warning"), icon=":material/warning:")

    left, right = st.columns([0.9, 1.35], gap="large", vertical_alignment="top")
    with left:
        with st.container(border=True, height="stretch"):
            st.subheader(":material/cloud_download: Pinnacleオッズ取得")
            st.caption("公開中のNPB主要市場を取得し、ML・±1.5・O/Uを一括反映します。")
            with st.expander("別サイトの対戦カードを先に貼り付ける", icon=":material/content_paste:"):
                pasted = st.text_area(
                    "対戦カード", height=150, key="pasted_cards",
                    placeholder="巨人\n18:00\n中日\n\n横浜<03>\n18:00\nヤクルト",
                )
                if st.button("対戦カードを反映", icon=":material/add_task:", width="stretch"):
                    st.session_state.rows = parse_other_site(pasted)
                    st.session_state.games_version = st.session_state.get("games_version", 0) + 1
                    st.rerun()
            if st.button(
                "最新オッズを取得", type="primary", icon=":material/refresh:", width="stretch"
            ):
                try:
                    with st.spinner("Pinnacleから取得しています…"):
                        fetched_rows, fetch_meta = fetch_pinnacle_npb_odds()
                    st.session_state.pinnacle_meta = fetch_meta
                    if not fetched_rows:
                        st.warning("現在、受付中のNPB主要オッズがありません。")
                    else:
                        pasted_rows = parse_other_site(pasted) if pasted.strip() else []
                        base_rows = pasted_rows or st.session_state.get("rows")
                        merged_rows, matched_count, unmatched = merge_pinnacle_rows(
                            fetched_rows, base_rows
                        )
                        st.session_state.rows = merged_rows
                        st.session_state.games_version = st.session_state.get("games_version", 0) + 1
                        runline_count = sum(
                            row.get("オッズ1±") is not None and row.get("オッズ2±") is not None
                            for row in fetched_rows
                        )
                        totals_count = sum(
                            row.get("合計ライン") is not None and row.get("オーバー") is not None
                            and row.get("アンダー") is not None for row in fetched_rows
                        )
                        st.session_state.fetch_notice = (
                            f"{len(fetched_rows)}試合取得・{matched_count}試合反映｜"
                            f"±1.5 {runline_count}試合｜O/U {totals_count}試合"
                        )
                        if unmatched:
                            st.session_state.fetch_warning = "未照合: " + " / ".join(unmatched)
                        st.rerun()
                except PinnacleFetchError as exc:
                    st.error(str(exc), icon=":material/error:")
                except (TypeError, ValueError) as exc:
                    st.error(f"対戦カードを確認してください: {exc}", icon=":material/error:")
            fetch_meta = st.session_state.get("pinnacle_meta")
            if fetch_meta:
                st.caption(f"最終取得 {fetch_meta['取得時刻']}｜{fetch_meta['オッズ取得']}試合")
            st.link_button(
                "Pinnacleを開く", "https://www.pinnacle.com/ja/baseball/matchups/",
                icon=":material/open_in_new:", width="stretch",
            )

    with right:
        with st.container(border=True):
            st.subheader(":material/tune: ハンデ入力")
            st.caption("出しチームとハンデを対戦ごとに選択してください。01は0.1、02は0.2です。")
            handicap_values = []
            with st.form("handicap_form", border=False):
                for game_index, row in enumerate(initial):
                    team1, team2 = norm_team(row.get("チーム1", "")), norm_team(row.get("チーム2", ""))
                    with st.container(border=True):
                        teams_col, giver_col, handicap_col = st.columns(
                            [1.45, 0.9, 0.72], vertical_alignment="center"
                        )
                        with teams_col:
                            icon1, name1, icon2, name2 = st.columns([0.16, 0.34, 0.16, 0.34], vertical_alignment="center")
                            render_team_icon(icon1, team1)
                            name1.markdown(f"**{team1}**  \n:blue-badge[{row.get('オッズ1') or '-'}]")
                            render_team_icon(icon2, team2)
                            name2.markdown(f"**{team2}**  \n:blue-badge[{row.get('オッズ2') or '-'}]")
                        giver_options = [team1, team2]
                        current_giver = norm_team(row.get("出しチーム", team1))
                        giver_index = giver_options.index(current_giver) if current_giver in giver_options else 0
                        selected_giver = giver_col.selectbox(
                            "出しチーム", giver_options, index=giver_index,
                            key=f"giver_{game_index}_{st.session_state.get('games_version', 0)}",
                        )
                        current_handicap = str(row.get("ハンデ", "0"))
                        handicap_index = (
                            HANDICAP_SELECT_OPTIONS.index(current_handicap)
                            if current_handicap in HANDICAP_SELECT_OPTIONS else 0
                        )
                        selected_handicap = handicap_col.selectbox(
                            "ハンデ", HANDICAP_SELECT_OPTIONS, index=handicap_index,
                            format_func=handicap_option_label,
                            key=f"handicap_{game_index}_{st.session_state.get('games_version', 0)}",
                        )
                        handicap_values.append((selected_giver, selected_handicap))
                apply_handicap = st.form_submit_button(
                    "ハンデ設定を反映", type="primary", icon=":material/check:", width="stretch"
                )
            if apply_handicap:
                updated_rows = [dict(row) for row in initial]
                for row, (selected_giver, selected_handicap) in zip(updated_rows, handicap_values):
                    row["出しチーム"] = selected_giver
                    row["ハンデ"] = selected_handicap
                    row["出し2点差以上(%)"] = None
                    row["もらい2点差以上(%)"] = None
                st.session_state.rows = updated_rows
                st.session_state.games_version = st.session_state.get("games_version", 0) + 1
                st.toast("ハンデ設定を反映しました", icon=":material/check_circle:")
                st.rerun()

    with st.expander("詳細オッズ・手入力", icon=":material/table_edit:"):
        st.caption("通常は上の取得・ハンデ選択だけで使えます。必要な場合のみ修正してください。")
        table_columns = [
            "チーム1", "オッズ1", "オッズ1±",
            "チーム2", "オッズ2", "オッズ2±",
            "合計ライン", "オーバー", "アンダー",
            "出しチーム", "ハンデ", "出し2点差以上(%)", "もらい2点差以上(%)",
        ]
        initial_df = pd.DataFrame(initial).reindex(columns=table_columns)
        edited = st.data_editor(
            initial_df,
            num_rows="dynamic",
            hide_index=True,
            width="stretch",
            row_height=42,
            column_config={
                "チーム1": st.column_config.TextColumn("チーム1", pinned=True, width="small"),
                "オッズ1": st.column_config.NumberColumn("ML 1", format="%.3f", width="small"),
                "オッズ1±": st.column_config.TextColumn("±1.5 1", width="small"),
                "チーム2": st.column_config.TextColumn("チーム2", width="small"),
                "オッズ2": st.column_config.NumberColumn("ML 2", format="%.3f", width="small"),
                "オッズ2±": st.column_config.TextColumn("±1.5 2", width="small"),
                "合計ライン": st.column_config.NumberColumn("合計", format="%.1f", width="small"),
                "オーバー": st.column_config.NumberColumn("O", format="%.3f", width="small"),
                "アンダー": st.column_config.NumberColumn("U", format="%.3f", width="small"),
                "出しチーム": st.column_config.TextColumn("出し", width="small"),
                "ハンデ": st.column_config.TextColumn("ハンデ", width="small"),
                "出し2点差以上(%)": st.column_config.NumberColumn("出し2点差+", format="%.1f%%"),
                "もらい2点差以上(%)": st.column_config.NumberColumn("もらい2点差+", format="%.1f%%"),
            },
            key=f"games_{st.session_state.get('games_version', 0)}",
        )

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
        draw_fallbacks = []
        for _, row in edited.iterrows():
            try:
                t1, t2 = norm_team(row["チーム1"]), norm_team(row["チーム2"])
                o1, o2 = float(row["オッズ1"]), float(row["オッズ2"])
                giver = norm_team(row["出しチーム"])
                hcap = parse_handicap(str(row["ハンデ"]))
                row_draw = draw_probability
                draw_raw = None
                draw_reason = "基準値"
                score_model = None
                try:
                    sign1, runline1 = parse_runline_input(row.get("オッズ1±"))
                    sign2, runline2 = parse_runline_input(row.get("オッズ2±"))
                    if sign1 and not sign2:
                        sign2 = "+" if sign1 == "-" else "-"
                    elif sign2 and not sign1:
                        sign1 = "+" if sign2 == "-" else "-"
                    if runline1 is None or runline2 is None:
                        raise ValueError("±1.5オッズ不足")
                    score_model = estimate_draw_probability(
                        o1, o2, sign1, runline1, sign2, runline2,
                        float(row.get("合計ライン")), float(row.get("オーバー")),
                        float(row.get("アンダー")),
                        fixed_draw=(None if draw_mode.startswith("3市場") else draw_probability),
                    )
                    row_draw = score_model["adopted"]
                    draw_raw = score_model["raw"]
                    draw_reason = score_model["reason"]
                except (TypeError, ValueError, ArithmeticError):
                    draw_fallbacks.append(f"{t1} vs {t2}")

                if score_model is not None:
                    no_vig_team1 = fair_probability(o1, o2)
                    giver_is_team1 = giver == t1
                    for team, is_giver in ((giver, True), (t2 if giver == t1 else t1, False)):
                        team_is_team1 = team == t1
                        prefix = "team1" if team_is_team1 else "team2"
                        ev = score_distribution_ev(
                            score_model["probability"], hcap, giver_is_team1,
                            is_giver, win_return, loss_cost,
                        )
                        rank, stake = classify(ev * 100)
                        results.append({
                            "対戦": f"{t1} vs {t2}", "ベット": team,
                            "区分": "出し" if is_giver else "もらい",
                            "ハンデ": display_handicap(hcap),
                            "条件付きノービグ勝率": f"{(no_vig_team1 if team_is_team1 else 1-no_vig_team1):.1%}",
                            "9回勝利確率": f"{score_model[f'{prefix}_win']:.1%}",
                            "1点差勝利": f"{score_model[f'{prefix}_one']:.1%}",
                            "2点差勝利": f"{score_model[f'{prefix}_two']:.1%}",
                            "3点差以上勝利": f"{score_model[f'{prefix}_three_plus']:.1%}",
                            "市場モデル同点率": f"{draw_raw:.1%}",
                            "採用同点率": f"{row_draw:.1%}",
                            "市場反映率": f"{score_model['reflection_rate']:.0%}",
                            "推定根拠": draw_reason,
                            "モデル補正量": f"{score_model['information_distance']:.4f}",
                            "市場再現誤差": f"{score_model['market_residual']:.3%}",
                            "EV": f"{ev*100:+.1f}%", "計算区分": "3市場統一分布",
                            "判定": rank, "推奨率": f"{stake}%",
                            "推奨額": int(bankroll * stake / 100),
                        })
                    continue
                raw_2plus = row.get("出し2点差以上(%)")
                p_2plus = None if pd.isna(raw_2plus) else float(raw_2plus) / 100
                p1 = fair_probability(o1, o2)
                conditional_pg = p1 if giver == t1 else 1 - p1
                pg = conditional_pg * (1 - row_draw)
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
                        f"通常勝率（{pg:.1%}）を超えています。オッズを再取得するか数値を確認してください。"
                    )
                    continue
                for team, is_giver in ((giver, True), (t2 if giver == t1 else t1, False)):
                    if use_ev_range:
                        p_hc_candidates = [0.0, pg] if missing_2plus else [p_2plus]
                        two_run_candidates = [0.0, 1.0] if split_2plus else [0.5]
                        endpoint_evs = [
                            calc_side(
                                pg, candidate_p_hc, hcap, is_giver,
                                win_return, loss_cost, row_draw,
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
                                       row_draw)
                        rank, stake = classify(ev * 100)
                        ev_display = f"{ev*100:+.1f}%"
                        calculation_type = "通常"
                    results.append({"対戦": f"{t1} vs {t2}", "ベット": team,
                                    "区分": "出し" if is_giver else "もらい",
                                    "ハンデ": display_handicap(hcap),
                                    "条件付きノービグ勝率": f"{conditional_pg if is_giver else 1-conditional_pg:.1%}",
                                    "9回勝利確率": f"{(pg if is_giver else 1-row_draw-pg):.1%}",
                                    "1点差勝利": "-", "2点差勝利": "-",
                                    "3点差以上勝利": "-",
                                    "市場モデル同点率": "-",
                                    "採用同点率": f"{row_draw:.1%}",
                                    "市場反映率": "0%",
                                    "推定根拠": draw_reason,
                                    "モデル補正量": "-", "市場再現誤差": "-",
                                    "EV": ev_display, "計算区分": calculation_type,
                                    "判定": rank,
                                    "推奨率": f"{stake}%", "推奨額": int(bankroll * stake / 100)})
            except (TypeError, ValueError, KeyError, ZeroDivisionError):
                st.error(f"入力を確認してください: {dict(row)}")
        st.session_state.results = pd.DataFrame(results)
        if draw_fallbacks:
            st.warning(
                "3市場の取得不足・不整合により、設定値を使った試合: "
                + " / ".join(dict.fromkeys(draw_fallbacks))
            )
        if results:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            try:
                pd.DataFrame(results).to_csv(
                    DATA_DIR / f"ev_{stamp}.csv", index=False, encoding="utf-8-sig"
                )
            except OSError:
                # OneDrive同期中やクラウド環境で書き込めない場合も画面結果は表示する。
                pass

    if "results" in st.session_state:
        result_df = st.session_state.results
        st.subheader(":material/query_stats: 計算結果")
        positives = result_df[result_df["判定"] != "見送り"]
        if not positives.empty:
            total_stake = int(positives["推奨額"].sum())
            positive_ev_values = (
                positives["EV"].astype(str).str.extract(r"([+-]?\d+(?:\.\d+)?)")[0].astype(float)
            )
            summary_cols = st.columns(3)
            summary_cols[0].metric("推奨ベット", f"{len(positives)}件")
            summary_cols[1].metric("本日の投資額", f"{total_stake:,}円")
            summary_cols[2].metric("最大EV", f"{positive_ev_values.max():+.1f}%")
            st.success(
                "推奨: " + " / ".join(
                    f"{r['ベット']} {r['判定']}（{r['推奨額']:,}円）" for _, r in positives.iterrows()
                ),
                icon=":material/recommend:",
            )
        else:
            st.info("本日の基準を満たすベットはありません。", icon=":material/shield:")

        render_result_cards(result_df)

        with st.expander("全計算データ", icon=":material/table_chart:"):
            st.caption("検証が必要なときだけ開いてください。通常は上のカード表示だけで判断できます。")
            st.dataframe(result_df, hide_index=True, width="stretch", row_height=40)
        st.download_button(
            "結果CSVを保存",
            result_df.to_csv(index=False).encode("utf-8-sig"),
            "ev_result.csv",
            "text/csv",
            icon=":material/download:",
        )

with tab2:
    st.markdown("""
### 計算の考え方

1. ML・±1.5・O/Uの両側オッズをそれぞれ正規化し、手数料を除きます。
2. 3市場を満たす仮の9回得点分布から、市場モデル同点率を求めます。
3. 自動時は基準5%との差を一律30%だけ反映し、採用同点率を1～10%に制限します。
4. 採用同点率も制約へ追加して最終得点分布を作り直します。この同じ分布から
   9回勝利・同点・1点差・2点差・3点差以上を集計します。
5. 最終分布の各スコアへハンデ表を直接適用してEVを計算します。
6. 分勝ち・分負けにも手数料を適用します（7分勝ちなら `92%×0.7=64.4%`）。
   ハンデ1.8の1点差勝ちは8分負けなので `-98%×0.8=-78.4%`、2点差以上は丸勝ちです。
7. 引き分けにもハンデ表を適用し、ハンデ0.3なら出し側3分負け・
   もらい側3分勝ちのように精算します。
8. EV 8%以上＝大3%、4%以上8%未満＝中2%、1.5%以上4%未満＝小1%、
   1.5%未満＝見送りです。判定には丸める前のEVを使用します。

3市場が揃って最終分布を作れる場合、表の`出し2点差以上(%)`には依存せず単一EVを表示します。
3市場が不足する場合は、`出し2点差以上(%)`を使い、なければ最小EV～最大EVを表示します。
判定と推奨額は、未知の確率を有利に仮定しない「最小EV」を基準にします。
`1半3`は2点差7分勝ち・3点差以上丸勝ち、`1半5`は2点差5分勝ち、
`1半7`は2点差3分勝ち、`2`は2点差返金として処理します。
±1.5市場では2点差と3点差以上の内訳が分からないため、これらは正しいEV範囲を表示します。
`もらい2点差以上(%)`はPinnacle市場の確認用です。現在の精算式では出し側が勝てなかった
ケースの配当が共通なので、出し側2点差以上確率の代用にはしません。
表の±1.5オッズを手入力・修正した場合は、
「±1.5オッズから2点差以上％を計算」ボタンで確率欄を更新できます。
    """)
