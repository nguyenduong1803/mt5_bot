from enum import Enum
from typing import Optional, Union

from pydantic import BaseModel, field_validator


class OrderId(str, Enum):
    OPEN_LONG = "openLong"
    CLOSE_LONG = "closeLong"
    OPEN_SHORT = "openShort"
    CLOSE_SHORT = "closeShort"
    CLOSE_ALL = "closeAll" 


class TradingViewAlert(BaseModel):
    symbol: str
    price: float
    alert_name: Optional[str] = None
    timenow: Optional[str] = None
    order_id: OrderId
    order_action: Optional[str] = None
    comment: Optional[str] = None
    alert_message: Optional[str] = None
    order_ratio: float
    strategy: str

    @field_validator("price", "order_ratio", mode="before")
    @classmethod
    def parse_numeric_string(cls, v: Union[str, float, int]) -> float:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                raise ValueError("value must not be empty")
            try:
                return float(v)
            except ValueError as e:
                raise ValueError(f"cannot parse '{v}' as a number") from e
        return v

    @field_validator("price")
    @classmethod
    def price_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("price must be greater than 0")
        return v

    @field_validator("order_ratio")
    @classmethod
    def order_ratio_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("order_ratio must be greater than 0")
        return v

    @field_validator("symbol", "strategy")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


class OrderResult(BaseModel):
    success: bool
    dry_run: bool
    strategy: str
    symbol: str
    action: str
    volume: Optional[float] = None
    price: Optional[float] = None
    order_ticket: Optional[int] = None
    message: str
