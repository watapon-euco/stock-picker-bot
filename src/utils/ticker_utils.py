"""銘柄コードの市場判定・ティッカー正規化ユーティリティ"""
import re
from typing import Optional


def detect_market(code: str) -> str:
    """銘柄コードから市場を推定する。

    Rules:
    - 4桁の数字（オプションで .T または .OS サフィックス）→ "JP"
    - 1〜5文字の英字（大文字小文字問わず、ドットを含む BRK.B のような例も）→ "US"
    - その他 → ValueError
    """
    code = str(code).strip()
    # .T / .OS サフィックスを除いた形で判定
    bare = re.sub(r"\.(T|OS)$", "", code)
    if re.fullmatch(r"\d{4}", bare):
        return "JP"
    if re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", code.upper()):
        return "US"
    raise ValueError(f"Cannot detect market for code: {code!r}")


def normalize_ticker(code: str, market: str = None) -> str:
    """yfinance で使うティッカー形式に正規化する。

    JP: "7203" → "7203.T"（既に .T/.OS が付いている場合はそのまま）
    US: "AAPL" → "AAPL"（そのまま）
        "BRK.B" → "BRK-B"（yfinance の慣例でドットをハイフンに）
    """
    code = str(code).strip()
    if market is None:
        try:
            market = detect_market(code)
        except ValueError:
            market = "JP"

    if market == "JP":
        if code.endswith(".T") or code.endswith(".OS"):
            return code
        return code + ".T"

    # US
    return code.upper().replace(".", "-")


def get_currency(market: str) -> str:
    """市場から通貨コードを返す。"""
    if market == "US":
        return "USD"
    return "JPY"


def format_price(price: Optional[float], currency: str) -> str:
    """金額を通貨記号付きでフォーマットする。

    JPY: "¥2,850"（小数なし）
    USD: "$185.50"（小数2桁）
    None: "—"
    """
    if price is None:
        return "—"
    if currency == "USD":
        return f"${price:,.2f}"
    return f"¥{price:,.0f}"
