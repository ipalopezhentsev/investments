from __future__ import annotations

import datetime
import os.path
from dataclasses import dataclass
import csv
from math import sqrt
from typing import List, Optional, Callable, Any, Mapping

from iwantitmore.utils import find_root_newton

YEAR_BASE = 365


@dataclass(frozen=True)
class CouponScheduleEntry:
    coupon_date: datetime.date
    """date of holders fixing. can be missing"""
    record_date: Optional[datetime.date]
    """start of coupon accrual date"""
    start_date: datetime.date
    """in notional ccy. takes into account amortized notional"""
    value: float
    """in real percents, not in fractions of 1. Percents should be taken from notional active on coupon date"""
    yearly_prc: float

    def __post_init__(self):
        if self.coupon_date is None or self.start_date is None or self.value is None:
            raise ValueError("All arguments except record_date are mandatory")
        if self.value < 0.0 or self.yearly_prc < 0.0:
            raise ValueError("Value and percent should be positive")
        if self.record_date is not None and self.record_date > self.coupon_date:
            raise ValueError("Record date cannot be past coupon date")
        if self.start_date > self.coupon_date:
            raise ValueError("Cannot have start date past coupon date")


@dataclass(frozen=True)
class AmortizationScheduleEntry:
    """date after (!) which notional is amortized. i.e. if coupon fails on amortization date,
    the amount is paid without regard to this amortization, it will be seen only on next coupon"""
    amort_date: datetime.date
    """Percent of initial notional amortized. in real percents, not in fractions of 1"""
    value_prc: float
    """Amount on which notional is decreased in this date. in notional ccy"""
    value: float

    def __post_init__(self):
        if self.value < 0.0 or self.value_prc < 0.0:
            raise ValueError("Value and percent should be positive")


@dataclass(frozen=True)
class Bond:
    """coupons, ordered by date ascending"""
    coupons: List[CouponScheduleEntry]
    """amortizations (notional changes), ordered by date ascending. Last entry corresponds 
    to notional repayment on settlement date, so even bonds without amortization will always 
    contain one entry in this list"""
    amortizations: List[AmortizationScheduleEntry]
    isin: Optional[str] = None
    name: Optional[str] = None
    initial_notional: float = 1000.0
    notional_ccy: str = "RUB"

    # TODO: offers

    def __post_init__(self):
        if self.initial_notional is None or self.notional_ccy is None or \
                self.coupons is None or self.amortizations is None:
            raise ValueError("Mandatory fields not set")
        # check schedules go in increasing order (we depend on it while searching)
        self.__check_dates_ascending(self.amortizations, lambda e: e.amort_date)
        self.__check_dates_ascending(self.coupons, lambda e: e.coupon_date)
        self.__check_dates_ascending(self.coupons, lambda e: e.start_date)
        # check amortization schedule eventually repays notional (i.e. notional repayment at
        # settlement is also treated as amortization and must be present)
        percent_notional_amortized = 0.0
        notional_amortized = 0.0
        for am in self.amortizations:
            percent_notional_amortized += am.value_prc
            notional_amortized += am.value
        if percent_notional_amortized != 100.0:
            raise ValueError(f"Amortization schedule doesn't net to 100% of notional but to "
                             f"{percent_notional_amortized} (check that you have entry for settlement?)")
        if notional_amortized != self.initial_notional:
            raise ValueError(f"Amortization schedule doesn't return initial notional of {self.initial_notional}"
                             f" but to {notional_amortized}")

    @staticmethod
    def __check_dates_ascending(objs: List[Any], date_accessor: Callable[[Any], datetime.date]):
        prev_dt = None
        for entry in objs:
            dt = date_accessor(entry)
            if prev_dt is not None and dt <= prev_dt:
                raise ValueError(f"Schedule dates must be ascending, they are not: {prev_dt} vs {dt}")
            prev_dt = dt

    def notional_on_date(self, date: datetime.date) -> float:
        """returns effective notional on specified date, taking in account amortization schedule"""
        if date < self.amortizations[0].amort_date:
            return self.initial_notional
        elif date > self.amortizations[-1].amort_date:
            return 0
        else:
            accum_prc = 0.0
            for am in self.amortizations:
                if am.amort_date < date:
                    accum_prc += am.value_prc
                else:
                    break
            return self.initial_notional * (1.0 - accum_prc / 100.0)

    def accrued_interest_on_date(self, dt: datetime.date) -> float:
        """returns coupon or its part accrued on dt (in ccy).
        "Накопленный купонный доход" in Russian. Is paid in addition to bond price when we buy it.
        """
        idx, closest_coupon = next(filter(lambda c: c[1].coupon_date >= dt, enumerate(self.coupons)), None)
        if closest_coupon is None:
            return 0
        eff_notional = self.notional_on_date(dt)
        # why? see attached test RU000A0JWSQ7.xml - coupon for 2018-06-08 has start date 2018-03-12, giving
        # 88 days diff, but the cited coupon value doesn't satisfy it, it's calculated from standard 91 days
        # diff, which appear only if we take previous coupon date 2018-03-09
        coupon_start = closest_coupon.start_date if idx == 0 else self.coupons[idx - 1].coupon_date
        year_fract = (dt - coupon_start).days / YEAR_BASE
        return round(eff_notional * (closest_coupon.yearly_prc / 100.0) * year_fract, 2)

    def payments_since_date(self, dt: datetime.date) -> (List[CouponScheduleEntry], List[AmortizationScheduleEntry]):
        """Returns tuple where first element means coupons expected starting from the passed date (inclusively)
        and second element means the same for notional amortizations"""
        coupons = [coupon for coupon in self.coupons if coupon.coupon_date >= dt]
        amortizations = [amort for amort in self.amortizations if amort.amort_date >= dt]
        return coupons, amortizations

    def yield_to_maturity(self, price_buy_prc: float, date_buy: datetime.date, date_settle: datetime.date,
                          accrued_coupon: float = None, coupon_tax_prc=None, commission=None):
        """YTM is the (theoretical) internal rate of return (IRR, overall interest rate)
        earned by an investor who buys the bond today at the market price, assuming that
        the bond is held until maturity, and that all coupon and principal payments are
        made on schedule.
         https://en.wikipedia.org/wiki/Yield_to_maturity

         Arguments to match MOEX figures:
             price_buy: price in percentage (0-100) of notional active on date_buy.
             There is NO need to add accrued coupon yourself - we take care of it.
             date_buy: day on which you bought
             date_settle: day when you actually received the bond (usually date_buy + 1 business day)
             accrued_coupon: coupon accrued on settle date, if not present will be calculated from coupon schedule.
             If you want precision, pass here explicit value from broker.
             coupon_tax_prc: tax on coupons (in fractions of 1)
             commission: in ccy, commission paid on date_buy
         """
        if price_buy_prc < 0.0:
            raise ValueError("price_buy_prc must be positive")
        if date_buy > date_settle:
            raise ValueError("Settle must be after buy date")
        # you don't own bond before settlement...
        coupons, amortizations = self.payments_since_date(date_settle)
        if accrued_coupon is None:
            # for some dates it's date_buy which matches MOEX quotes, for some it's date_settle...
            accrued_coupon = self.accrued_interest_on_date(date_buy)
        # when we buy, we must also pay coupon accrued up to this date
        price_buy = (price_buy_prc / 100.0) * self.notional_on_date(date_buy) + accrued_coupon
        if commission is not None:
            price_buy += commission
        flows = [CashFlow(date_buy, -price_buy)]
        for coupon in coupons:
            val = coupon.value if coupon_tax_prc is None else coupon.value * (1.0 - coupon_tax_prc)
            flows.append(CashFlow(coupon.coupon_date, val))
        for amort in amortizations:
            flows.append(CashFlow(amort.amort_date, amort.value))
        return round(100.0 * CashFlows(flows).irr(), 2) / 100.0

#TODO: extract this and next to separate module?
@dataclass(frozen=True)
class CashFlow:
    """Flow of money at specified date"""
    date: datetime.date
    """Negative - we pay, positive - we receive"""
    flow: float

    def years_since(self, base_date: datetime.date) -> float:
        return (self.date - base_date).days / YEAR_BASE


class CashFlows:
    flows: List[CashFlow]

    def __init__(self, flows: List[CashFlow]):
        # TODO: actually these are constraints just for IRR, maybe move it there...
        if len(flows) < 2:
            raise ValueError("There must be at least 2 flows")
        if not any(map(lambda flow: flow.flow > 0.0, flows)):
            raise ValueError("There must be positive flows")
        if not any(map(lambda flow: flow.flow < 0.0, flows)):
            raise ValueError("There must be negative flows")
        self.flows = flows

    def irr(self) -> float:
        """
        Returns internal rate of return (in terms of 1)
        For irr to be present, there must be both positive and negative flows.

        The internal rate of return on an investment or project is the "annualized effective
        compounded return rate" or rate of return that sets the net present value of all
        cash flows (both positive and negative) from the investment equal to zero.

        It is the discount rate at which the net present value of the future cash flows
        is equal to the initial investment, and it is also the discount rate at which the
        total present value of costs (negative cash flows) equals the total present value
        of the benefits (positive cash flows).
        https://en.wikipedia.org/wiki/Internal_rate_of_return#Exact_dates_of_cash_flows
        """
        r0 = 0.0
        for flow in self.flows[1:]:
            r0 += flow.flow
        r0 = (-1.0 / self.flows[0].flow) * r0 - 1.0
        irr, _, _ = find_root_newton(f=lambda r: self.npv(r), init_guess=r0,
                                     f_der=lambda r: self.npv_der(r))
        return irr

    def npv(self, rate: float) -> float:
        """Calculates net present worth of the project at specified interest rate
        (in fractions of 1), assuming the project starts at the date of the first cash flow"""
        res = 0.0
        for flow in self.flows:
            year_fract = flow.years_since(self.flows[0].date)
            res += flow.flow / (1.0 + rate) ** year_fract
        return res

    def npv_der(self, rate: float) -> float:
        """returns derivative of npv by interest rate (in fractions of 1)
        https://en.wikipedia.org/wiki/Internal_rate_of_return#Exact_dates_of_cash_flows
        """
        res = 0.0
        for flow in self.flows[1:]:
            year_fract = flow.years_since(self.flows[0].date)
            res -= flow.flow * year_fract / ((1.0 + rate) ** (year_fract + 1))
        return res


field_date = "DATE"
field_open = "OPEN"
field_low = "LOW"
field_high = "HIGH"
field_close = "CLOSE"
field_num_trades = "NUM_TRADES"
field_volume = "VOLUME"
field_waprice = "WAPRICE"
ohlc_fieldnames = [field_date, field_open, field_high, field_low, field_close,
                   field_num_trades, field_volume, field_waprice]


@dataclass(frozen=True)
class OHLC:
    date: datetime.date
    open: float
    high: float
    low: float
    close: float
    num_trades: int
    """in roubles (FX), in lots (bonds, shares)"""
    volume: float
    """average price in this interval weighted by volume of trades inside this interval"""
    waprice: float

    def __post_init__(self):
        if self.date is None:
            raise ValueError("Date must be filled")
        if self.low > self.high:
            raise ValueError(f"Low ({self.low}) should be >= High ({self.high})")
        if not (self.low <= self.open <= self.high):
            raise ValueError(f"Open ({self.open}) must be between Low ({self.low}) and High ({self.high})")
        if not (self.low <= self.close <= self.high):
            raise ValueError(f"Close ({self.close}) must be between Low ({self.low}) and High ({self.high})")
        # cannot check this as many bonds violate it
        # if not (self.low <= self.waprice <= self.high):
        #     raise ValueError(f"WAPrice ({self.waprice}) must be between Low ({self.low}) and High ({self.high})")

    def to_csv_row(self) -> Mapping[str, Any]:
        return {field_date: self.date.isoformat(), field_open: str(self.open), field_high: str(self.high),
                field_low: str(self.low), field_close: str(self.close), field_num_trades: str(self.num_trades),
                field_volume: str(self.volume), field_waprice: str(self.waprice)}

    @staticmethod
    def from_csv_row(row: Mapping[str, Any]) -> OHLC:
        return OHLC(date=datetime.date.fromisoformat(row[field_date]), open=float(row[field_open]),
                    high=float(row[field_high]), low=float(row[field_low]), close=float(row[field_close]),
                    num_trades=int(row[field_num_trades]), volume=float(row[field_volume]),
                    waprice=float(row[field_waprice]))


@dataclass()
class OHLCSeries:
    """it's meant to be just string, to not bring dependency on MOEX classes via Instrument"""
    instr_code: str
    """ordered by date ascending"""
    ohlc_series: List[OHLC]
    """longer user friendly name from exchange"""
    # TODO: persist to csv
    name: Optional[str] = None

    def __post_init__(self):
        prev_date: Optional[datetime.date] = None
        for entry in self.ohlc_series:
            cur_date = entry.date
            if prev_date is not None and prev_date >= cur_date:
                raise ValueError(f"Series must have ascending dates, they are not: {prev_date} vs {cur_date}")
            prev_date = cur_date

    def __str__(self) -> str:
        return "OHLCSeries[" + self.name if self.name is not None else self.instr_code + "]"

    def is_empty(self):
        return len(self.ohlc_series) == 0

    def append(self, other: OHLCSeries):
        """appends to this instance contents of the other table.
        Its dates must not intersect our dates and be higher than our last date."""
        if other.instr_code != self.instr_code:
            raise ValueError(f"Instruments do not match: {self.instr_code} vs {self.instr_code}")
        if other.is_empty():
            return
        if not self.is_empty():
            our_last_date: datetime.date = self.ohlc_series[-1].date
            their_last_date: datetime.date = other.ohlc_series[0].date
            if our_last_date >= their_last_date:
                raise ValueError(f"First date ({their_last_date}) must be > {our_last_date}")
        self.ohlc_series.extend(other.ohlc_series)
        if other.name is not None:
            self.name = other.name

    def save_to_csv(self, filename):
        with open(filename, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=ohlc_fieldnames)
            writer.writeheader()
            for ohlc in self.ohlc_series:
                row = ohlc.to_csv_row()
                writer.writerow(row)

    @staticmethod
    def load_from_csv(filename) -> OHLCSeries:
        instr_code = os.path.splitext(os.path.basename(filename))[0]
        series: List[OHLC] = []
        with open(filename, 'r', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                ohlc = OHLC.from_csv_row(row)
                series.append(ohlc)
        # TODO: persist name
        return OHLCSeries(instr_code, series, name=None)

    def mean_of_last_elems(self, num_elems: int,
                           field_getter: Callable[[OHLC], float] = lambda x: x.close) -> float:
        """gets average of last num_elems values of this series, for field specified by field_getter"""
        if len(self.ohlc_series) < num_elems:
            raise ValueError(f"Series has less than {num_elems}")
        agg_val = 0.0
        for ohlc in self.ohlc_series[-num_elems:]:
            agg_val += field_getter(ohlc)
        return agg_val / num_elems

    def std_dev_of_last_elems(self, num_elems,
                              field_getter: Callable[[OHLC], float] = lambda x: x.close,
                              mean: Optional[float] = None) -> float:
        """gets standard deviation of last num_elems values of this series, for field specified by field_getter"""
        if mean is None:
            mean = self.mean_of_last_elems(num_elems, field_getter)
        if len(self.ohlc_series) < num_elems:
            raise ValueError(f"Series has less than {num_elems}")
        agg_val = 0.0
        # "Corrected sample standard deviation"
        # TODO: std dev of log returns instead?
        for ohlc in self.ohlc_series[-num_elems:]:
            agg_val += (field_getter(ohlc) - mean) ** 2
        return sqrt(agg_val / (num_elems - 1))


@dataclass(frozen=True)
class IntradayQuote:
    instrument: str
    # price of last trade
    last: float
    num_trades: int
    # True if is trading at time of query
    is_trading: bool
    # time of latest trade?
    time: datetime.time
