from typing import TypedDict


class ZerodhaInstrument(TypedDict):
    instrument_token: int
    tradingsymbol: str
    exchange: str
    instrument_type: str


class ZerodhaCandle(TypedDict):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class ZerodhaSession(TypedDict):
    access_token: str


class ZerodhaProfile(TypedDict):
    user_id: str
    user_name: str
    email: str
    broker: str
