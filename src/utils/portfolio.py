"""ポートフォリオ構築支援ユーティリティ

月次レポートの推奨銘柄に対し、以下を決定論的に算出する（LLM非依存・再現性あり）:
  - ポジションサイジング: 確信度（順位）× 逆ボラティリティで推奨配分%を提案
  - テーマ内相対バリュエーション: PER 中央値比で割安/割高を判定
  - データ品質評価: Stooq フォールバック等でデータが劣化した銘柄を明示
  - 集中リスク: 同一銘柄が複数テーマに重複していないか検出

いずれも投資助言ではなく、機械的なポートフォリオ構築の補助指標。
"""
from statistics import median
from typing import Dict, List, Optional


# ─────────────────────────────────────────────────────────────────────────────
# ボラティリティ
# ─────────────────────────────────────────────────────────────────────────────

def compute_volatility(price_history_6m: Optional[Dict]) -> Optional[float]:
    """日次終値リストから日次リターンの標準偏差（%）を算出する。

    Args:
        price_history_6m: {"closes": [float, ...], ...} 形式。終値が10本未満なら None。
    Returns:
        日次リターン標準偏差（%）。算出不能なら None。
    """
    if not price_history_6m:
        return None
    closes = price_history_6m.get("closes") if isinstance(price_history_6m, dict) else None
    if not closes or len(closes) < 10:
        return None

    returns = []
    for prev, cur in zip(closes[:-1], closes[1:]):
        if prev and prev != 0:
            returns.append((cur - prev) / prev)
    if len(returns) < 2:
        return None

    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return round(variance ** 0.5 * 100, 2)


# ─────────────────────────────────────────────────────────────────────────────
# ポジションサイジング
# ─────────────────────────────────────────────────────────────────────────────

def suggest_position_weights(
    items: List[Dict], max_weight: float = 0.30
) -> List[int]:
    """推奨配分（%）を算出する。

    確信度（順位が上ほど高い）と逆ボラティリティ（変動が小さいほど高い）を掛け合わせ、
    合計100%に正規化する。1銘柄が突出しないよう max_weight で上限を設ける。

    Args:
        items: [{"rank": int, "volatility": float|None}, ...]
               rank は 1 始まり（小さいほど高確信）。volatility は日次リターン標準偏差(%)。
        max_weight: 1銘柄あたりの配分上限（0〜1）。
    Returns:
        items と同じ並びの整数パーセントのリスト（合計100、items 空なら []）。
    """
    n = len(items)
    if n == 0:
        return []

    # ボラティリティの代表値（欠損銘柄のフォールバック）
    known_vols = [it.get("volatility") for it in items if it.get("volatility")]
    fallback_vol = median(known_vols) if known_vols else 1.0

    raw: List[float] = []
    for it in items:
        rank = it.get("rank") or 1
        rank_w = 1.0 / max(1, rank)
        vol = it.get("volatility")
        if not vol or vol <= 0:
            vol = fallback_vol
        raw.append(rank_w / vol)

    total = sum(raw)
    if total <= 0:
        # 全て0なら均等配分
        weights = [1.0 / n] * n
    else:
        weights = [r / total for r in raw]

    # 上限キャップ → 余剰を未キャップ銘柄へ比例再配分（1パス）
    # 銘柄数が少ないと max_weight が実現不能（< 1/n）になるため下限を 1/n に補正する。
    effective_cap = max(max_weight, 1.0 / n)
    if effective_cap < 1.0:
        capped = [min(w, effective_cap) for w in weights]
        excess = sum(weights) - sum(capped)
        if excess > 1e-9:
            room = [effective_cap - c for c in capped]
            room_total = sum(room)
            if room_total > 1e-9:
                capped = [c + excess * (r / room_total) for c, r in zip(capped, room)]
        weights = capped

    # 整数%へ丸め、合計100に調整（端数は最大配分に寄せる）
    pct = [round(w * 100) for w in weights]
    diff = 100 - sum(pct)
    if pct and diff != 0:
        idx = max(range(n), key=lambda i: weights[i])
        pct[idx] += diff
    return pct


# ─────────────────────────────────────────────────────────────────────────────
# テーマ内相対バリュエーション
# ─────────────────────────────────────────────────────────────────────────────

def theme_relative_valuation(stocks: List[Dict]) -> Dict[str, Optional[str]]:
    """テーマ内の PER 中央値比で各銘柄の割安/割高を判定する。

    PER が取得できる銘柄が2件未満の場合は判定不能（全て None）。

    Returns:
        {code: "割安" | "割高" | "中立" | None} の dict。
    """
    pers = {}
    for s in stocks:
        code = str(s.get("code", "")).strip()
        per = s.get("per")
        if code and isinstance(per, (int, float)) and per > 0:
            pers[code] = float(per)

    result: Dict[str, Optional[str]] = {
        str(s.get("code", "")).strip(): None for s in stocks if s.get("code")
    }
    if len(pers) < 2:
        return result

    med = median(pers.values())
    if med <= 0:
        return result

    for code, per in pers.items():
        if per <= med * 0.9:
            result[code] = "割安"
        elif per >= med * 1.1:
            result[code] = "割高"
        else:
            result[code] = "中立"
    return result


# ─────────────────────────────────────────────────────────────────────────────
# データ品質評価
# ─────────────────────────────────────────────────────────────────────────────

def assess_data_quality(stock: Dict) -> Dict:
    """銘柄データの信頼度を評価する。

    Stooq フォールバック銘柄や、PER/PBR/セクターが揃わない銘柄は「低」と判定する。

    Returns:
        {"level": "高"|"低", "reasons": [str, ...]} の dict。
    """
    reasons: List[str] = []

    if stock.get("data_source") == "stooq":
        reasons.append("Stooqフォールバック（財務指標なし）")

    per = stock.get("per")
    pbr = stock.get("pbr")
    if (per is None or per == 0) and (pbr is None or pbr == 0):
        reasons.append("バリュエーション指標欠落")

    if not stock.get("sector"):
        reasons.append("セクター不明")

    if stock.get("current_price") is None:
        reasons.append("現在価格不明")

    level = "低" if reasons else "高"
    return {"level": level, "reasons": reasons}


# ─────────────────────────────────────────────────────────────────────────────
# 集中リスク: 複数テーマに重複する銘柄
# ─────────────────────────────────────────────────────────────────────────────

def find_duplicate_stocks(stock_data: List[Dict]) -> List[Dict]:
    """複数テーマに登場する同一銘柄（コード）を検出する。

    Args:
        stock_data: [{"theme_name": str, "stocks": [{"code","name",...}]}, ...]
    Returns:
        [{"code","name","themes": [theme_name, ...]}, ...]（2テーマ以上に出るもののみ）。
    """
    by_code: Dict[str, Dict] = {}
    for theme in stock_data:
        theme_name = theme.get("theme_name", "")
        for stock in theme.get("stocks", []):
            code = str(stock.get("code", "")).strip()
            if not code:
                continue
            entry = by_code.setdefault(
                code, {"code": code, "name": stock.get("name", code), "themes": []}
            )
            if theme_name and theme_name not in entry["themes"]:
                entry["themes"].append(theme_name)

    return [e for e in by_code.values() if len(e["themes"]) >= 2]
