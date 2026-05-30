"""売買プラン（エントリー/損切り/目標株価/リスクリワード）算出ユーティリティ

yfinance から取得済みのテクニカル指標（MA25/MA75/RSI14）と 52週高安を使って、
各銘柄の「いつ・いくらで買い、どこで損切り、どこを目標にするか」を決定論的に算出する。
LLM に依存しないため再現性があり、単体テスト可能。

設計方針:
  - エントリー帯: 直近サポート（MA25 が現値より下ならそれ、無ければ現値の -3%）〜現値。
    過熱（RSI>70）局面では高値掴みを避けるため押し目（サポート寄り）を推奨する。
  - 損切り: サポートの少し下（MA75 が更に下ならそれを優先）。
  - 目標株価: 52週高値（現値より十分上にあれば）、無ければ現値からの想定上昇。
  - リスクリワード比 = (目標 - 現値) / (現値 - 損切り)。1:2 以上を「良好」とする。
"""
from typing import Dict, Optional

# 損切りはサポートからこの比率だけ下に置く（8% のバッファ）
_STOP_BUFFER = 0.92
# サポートが取れない場合のエントリー下限（現値からの押し目想定）
_DEFAULT_PULLBACK = 0.97
# 52週高値が使えない場合の目標株価（現値からの想定上昇）
_DEFAULT_UPSIDE = 1.15
# RSI のしきい値
_RSI_OVERBOUGHT = 70.0
_RSI_OVERSOLD = 30.0
# 「良好なリスクリワード」の基準
GOOD_RR_THRESHOLD = 2.0


def _round_price(price: float) -> float:
    """価格帯に応じて見やすく丸める（日本株の整数 / 米国株の小数2桁を吸収）。"""
    if price >= 1000:
        return round(price)
    if price >= 100:
        return round(price, 1)
    return round(price, 2)


def _rsi_signal(rsi14: Optional[float]) -> str:
    """RSI から過熱/売られすぎ/中立を判定する。"""
    if rsi14 is None:
        return "不明"
    if rsi14 >= _RSI_OVERBOUGHT:
        return "過熱"
    if rsi14 <= _RSI_OVERSOLD:
        return "売られすぎ"
    return "中立"


def compute_trade_levels(
    current_price: Optional[float],
    technicals: Optional[Dict] = None,
    price_52w_high: Optional[float] = None,
    price_52w_low: Optional[float] = None,
) -> Optional[Dict]:
    """売買プランを算出する。

    Args:
        current_price: 現在株価。None または 0 以下なら算出不能（None を返す）。
        technicals: yfinance_fetcher が付与する {ma25, ma75, rsi14, ...} の dict。
        price_52w_high: 52週高値。
        price_52w_low: 52週安値。

    Returns:
        以下のキーを持つ dict。算出不能時は None。
        {
            "entry_low", "entry_high": エントリー推奨帯,
            "stop_loss": 損切りライン,
            "target": 目標株価,
            "risk_reward": リスクリワード比（小数1桁, 算出不能なら None）,
            "risk_reward_good": bool（1:2 以上か）,
            "rsi": RSI14（参考）,
            "rsi_signal": "過熱" | "中立" | "売られすぎ" | "不明",
        }
    """
    if current_price is None or current_price <= 0:
        return None

    technicals = technicals or {}
    ma25 = technicals.get("ma25")
    ma75 = technicals.get("ma75")
    rsi14 = technicals.get("rsi14")

    # ── サポート（エントリー下限）の決定 ──
    # MA25 が現値より下にあれば押し目の目安として使う。無ければ現値の -3%。
    if ma25 is not None and 0 < ma25 < current_price:
        support = ma25
    else:
        support = current_price * _DEFAULT_PULLBACK

    entry_low = min(support, current_price)
    entry_high = current_price

    # ── 損切りライン ──
    # サポートの少し下。MA75 が更に下ならそちらを優先（より保守的）。
    stop_loss = support * _STOP_BUFFER
    if ma75 is not None and 0 < ma75 < stop_loss:
        stop_loss = ma75

    # ── 目標株価 ──
    # 52週高値が現値より十分（+3%超）上にあればそれを目標。無ければ想定上昇率。
    if price_52w_high is not None and price_52w_high > current_price * 1.03:
        target = price_52w_high
    else:
        target = current_price * _DEFAULT_UPSIDE

    # ── リスクリワード比 ──
    risk = entry_high - stop_loss
    reward = target - entry_high
    if risk > 0 and reward > 0:
        risk_reward = round(reward / risk, 1)
    else:
        risk_reward = None

    return {
        "entry_low": _round_price(entry_low),
        "entry_high": _round_price(entry_high),
        "stop_loss": _round_price(stop_loss),
        "target": _round_price(target),
        "risk_reward": risk_reward,
        "risk_reward_good": risk_reward is not None and risk_reward >= GOOD_RR_THRESHOLD,
        "rsi": round(rsi14, 1) if rsi14 is not None else None,
        "rsi_signal": _rsi_signal(rsi14),
    }
