from __future__ import annotations
import logging
import xml.etree.ElementTree as ET
import datetime
from typing import Callable, Optional, Dict
from abc import ABC, abstractmethod

import requests
import csv

from iwantitmore.instruments import Bond, AmortizationScheduleEntry, CouponScheduleEntry, OHLC, OHLCSeries, \
    IntradayQuote

ISS_URL = "https://iss.moex.com/iss/"
logger = logging.getLogger(__name__)
one_day = datetime.timedelta(days=1)
http_params = {"timeout": 10}


def load_coupon_schedule_xml(isin: str) -> str:
    # Without "limit=unlimited" loads only first 20 coupons!
    url = f"{ISS_URL}securities/{isin}/bondization.xml?iss.meta=off&limit=unlimited"
    data = requests.get(url)
    return data.text


def __parse_am_entry(am_entry) -> AmortizationScheduleEntry:
    str_date = am_entry.get("amortdate")
    am_date = datetime.date.fromisoformat(str_date)
    value_prc = float(am_entry.get("valueprc"))
    value = float(am_entry.get("value"))
    return AmortizationScheduleEntry(am_date, value_prc, value)


def __parse_coupon_entry(cp_entry) -> CouponScheduleEntry:
    cp_date = datetime.date.fromisoformat(cp_entry.get("coupondate"))
    str_rec_date = cp_entry.get("recorddate")
    rec_date = datetime.date.fromisoformat(str_rec_date) if str_rec_date != "" else None
    st_date = datetime.date.fromisoformat(cp_entry.get("startdate"))
    val = float(cp_entry.get("value"))
    yearly_prc = float(cp_entry.get("valueprc"))
    return CouponScheduleEntry(cp_date, rec_date, st_date, val, yearly_prc)


def parse_coupon_schedule_xml(data: str) -> Bond:
    root = ET.fromstring(data)
    first_row = next(root.iter("row"))
    isin = first_row.get("isin")
    name = first_row.get("name")
    # note: reply rows contain "facevalue" but it's incorrect, it's "current facevalue"
    initial_notional = float(first_row.get("initialfacevalue"))
    notional_ccy = first_row.get("faceunit")

    am_schedule = [__parse_am_entry(am_entry) for am_entry in root.findall(".//data[@id='amortizations']//row")]
    cp_schedule = [__parse_coupon_entry(cp_entry) for cp_entry in root.findall(".//data[@id='coupons']//row")]

    return Bond(isin=isin, name=name, initial_notional=initial_notional, notional_ccy=notional_ccy,
                coupons=cp_schedule, amortizations=am_schedule)


# TODO: also make Bond instrument below?
def load_bond(isin: str) -> Bond:
    xml = load_coupon_schedule_xml(isin)
    return parse_coupon_schedule_xml(xml)


class Instrument(ABC):
    def __init__(self, code: str):
        self.code = code
        self.name = None

    def __eq__(self, o: Instrument) -> bool:
        return self.__class__.__name__ == o.__class__.__name__ and self.code == o.code

    def __hash__(self) -> int:
        return hash(self.code)

    def __str__(self) -> str:
        return self.code if self.name is None else f"{self.name} ({self.code})"

    @abstractmethod
    def get_exchange_coords(self):
        """returns 'middle' part for MOEX url's like engines/stock/markets/shares/boards/TQTF"""
        pass

    # NOTE: CANNOT BE PRIVATE - WON'T CALL DERIVED VARIANTS!
    def parse_volume(self, row: Dict[str, str]) -> float:
        return float(row["VOLUME"])

    def parse_num_trades(self, row: Dict[str, str]) -> int:
        return int(row["NUMTRADES"])

    def parse_waprice(self, row: Dict[str, str]) -> float:
        return float(row["WAPRICE"])

    def _parse_ohlc_csv(self, reply: str) -> OHLCSeries:
        lines = reply.split("\n")[2:]
        # note field names in moex reply are OLHC (parsed here), but our native csv
        # (in instruments module) is OHLC as market convention
        reader = csv.DictReader(lines, delimiter=";")
        series = []
        name = None
        for row in reader:
            try:
                num_trades = self.parse_num_trades(row)
                date = datetime.date.fromisoformat(row["TRADEDATE"])
                if num_trades != 0:
                    ohlc = OHLC(date=date, open=float(row["OPEN"]),
                                low=float(row["LOW"]), high=float(row["HIGH"]), close=float(row["CLOSE"]),
                                num_trades=num_trades, volume=self.parse_volume(row), waprice=self.parse_waprice(row))
                    series.append(ohlc)
                else:
                    # otherwise pandas import will change column type from double to object due to NA presence
                    logger.info(f"Skipping {date} for {self} as it had no trades")
                if name is None:
                    name = row["SHORTNAME"]
            except ValueError as e:
                raise ValueError(f"Error happened for row {row}", e)
        if self.name is None:
            self.name = name
        return OHLCSeries(self.code, series, name)

    def __load_partial_ohlc_table_csv(self, from_date: Optional[datetime.date]) -> OHLCSeries:
        """Loads certain number of lines from from_date. So to read the whole available
        data you must call this function until it returns empty table"""
        fr = ""
        if from_date is not None:
            fr = f"?from={from_date.isoformat()}"
        exchange_coords: str = self.get_exchange_coords()
        url = f"{ISS_URL}history/{exchange_coords}/securities/{self.code}/candleborders.csv{fr}"

        # will return not more than 100 entries from the beginning of history
        reply = requests.get(url, **http_params).text
        return self._parse_ohlc_csv(reply)

    def load_ohlc_table(self, from_date: Optional[datetime.date] = None,
                        partial_loader: Callable[[Instrument, Optional[datetime.date]], OHLCSeries]
                        = __load_partial_ohlc_table_csv) -> OHLCSeries:
        """loads OHLC table from web API of exchange, starting from the specified date or from beginning if empty.
        Note for some instruments MOEX API can give data starting from later date than available on their site"""
        series = OHLCSeries(self.code, [], None)
        date = from_date
        while True:
            logger.info(f"Loading {self} from {date if date is not None else 'beginning'}")
            addition = partial_loader(self, date)
            if addition.is_empty():
                break
            else:
                series.append(addition)
                date = addition.ohlc_series[-1].date + datetime.timedelta(days=1)
        return series

    def update_ohlc_table(self, existing_series: OHLCSeries) -> None:
        """Tries to load new values after the last date of existing series"""
        if self.code != existing_series.instr_code:
            raise ValueError(f"Incompatible instruments: {self.code} vs {existing_series.instr_code}")
        if not existing_series.is_empty():
            from_date = existing_series.ohlc_series[-1].date + one_day
        else:
            from_date = None
        addition = self.load_ohlc_table(from_date)
        existing_series.append(addition)

    def _parse_intraday_quotes(self, reply: str) -> IntradayQuote:
        try:
            root = ET.fromstring(reply)
            rows = root.findall(".//data[@id='marketdata']//row")
            if rows is None or len(rows) == 0:
                now = datetime.datetime.now()
                delayed_time = (now - datetime.timedelta(minutes=15, microseconds=now.microsecond)).time()
                return IntradayQuote(instrument=self.code, last=0.0, num_trades=0, is_trading=False, time=delayed_time)
            row = rows[0]

            # TRADINGSTATUS is "T"/"N"
            is_trading = True if row.get("TRADINGSTATUS") == "T" else False
            str_last = row.get("LAST")
            last = float(str_last) if str_last is not None and str_last != "" else 0.0
            str_num_trades = row.get("NUMTRADES")
            num_trades = int(str_num_trades) if str_num_trades is not None and str_num_trades != "" else 0
            if is_trading and last == 0.0:
                # happens at beginning of every day, i.e. there is no deals despite trading is started,
                # let's treat it as if trading has not started yet, otherwise there'll be jump to 0 reported.
                is_trading = False
            time = datetime.time.fromisoformat(row.get("TIME"))
            return IntradayQuote(instrument=row.get("SECID"), last=last, num_trades=num_trades,
                                 is_trading=is_trading, time=time)
        except Exception as ex:
            raise ValueError(f"Error while parsing reply {reply}") from ex

    def load_intraday_quotes(self) -> IntradayQuote:
        exchange_coords = self.get_exchange_coords()
        url = f"{ISS_URL}{exchange_coords}/securities/{self.code}.xml?iss.meta=off"
        data = requests.get(url, **http_params)
        return self._parse_intraday_quotes(data.text)


class FXInstrument(Instrument):
    def __init__(self, secid: str):
        super().__init__(secid)

    def get_exchange_coords(self):
        return f"engines/currency/markets/selt/boards/CETS"

    def parse_volume(self, row: Dict[str, str]) -> float:
        return float(row["VOLRUR"])


class BondInstrument(Instrument):
    def __init__(self, isin: str):
        super().__init__(isin)

    def get_exchange_coords(self):
        return f"engines/stock/markets/bonds/boards/TQCB"


class ShareInstrument(Instrument):
    def __init__(self, secid: str):
        """Note ISIN's are not supported, only SECID ('Код ценной бумаги' on moex.com)"""
        super().__init__(secid)

    def get_exchange_coords(self):
        return f"engines/stock/markets/shares/boards/TQTF"


class IndexInstrument(Instrument):
    def __init__(self, secid: str):
        """secid - e.g. MREDC"""
        super().__init__(secid)

    def get_exchange_coords(self):
        return f"engines/stock/markets/index/boards/RTSI"

    def parse_num_trades(self, row: Dict[str, str]) -> int:
        # cannot use 0 as otherwise parent loader will skip the row
        return 1

    def parse_volume(self, row: Dict[str, str]) -> float:
        return 0.0

    def parse_waprice(self, row: Dict[str, str]) -> float:
        return 0.0




