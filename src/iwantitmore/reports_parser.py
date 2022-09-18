from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Set, Mapping

from bs4 import BeautifulSoup
import os
import os.path
import glob
import re
import datetime
from enum import Enum, unique

logger = logging.getLogger(__name__)


@unique
class Side(Enum):
    BUY = 1
    SELL = 2

    def __str__(self):
        return self.name


@dataclass(frozen=True)
class CashflowReportLine:
    """date of cashflow"""
    date: datetime.date
    """'subaccount' where the flow happened, e.g. fund market or fx market"""
    trading_platform: str
    description: str
    ccy: str
    """sum in [ccy] that came into the account. Is always positive. TODO: maybe make it signed?"""
    amt_debit: float
    """sum in [ccy] that came out of the account. Is always positive. TODO: maybe make it signed?"""
    amt_credit: float
    """=[amt_debit]-[amt_credit]. TODO: if made them signed, correct formula"""
    amt_total: float


@dataclass(frozen=True)
class EquityTradesReportLine:
    """when trade was made"""
    trade_datetime: datetime.datetime
    """when trade will be settled"""
    settle_date: datetime.date
    security_name: str
    isin: str
    ccy: str
    """buy/sell. Unlike original report, affects sign of following fields"""
    side: Side
    """num shares. Positive if buying, negative if selling"""
    num_shares: float
    """price per 1 share. Always positive. Note for bonds it's not literal price. It's value like 102.38 which should 
    be interpreted like this:
    1. at [trade_datetime] the bond has some notional N. Note it's not always 1000, you have to track amortization 
    schedule to find correct notional for date.
    2. The [unit_price] shows percentage of notional N that the bond is worth at this date.
    2. So when at date X you pay price like 102.38 for 1 bond that has notional N on that date, it means you pay
    N * (102.38/100). Additionally you pay accumulated unpaid coupon (NKD) which is also calculated from N and 
    coupon schedule and is given in separate field
    """
    unit_price: float
    """Real amount of [ccy] paid/received for the whole deal. Is NOT always equal to num_shares * unit_price - read the 
    description for unit_price for bonds specifics.
    Negative if [side] was BUY, positive if it was SELL
    """
    amount: float
    """NKD for the whole trade (not for 1 share). If you bought a bond, you will also pay NKD since beginning of the
    current coupon period, based on notional active on trade date. If you sold a bond, you will receive NKD from buyer.
    Negative if [side] was BUY, positive if it was SELL"""
    accum_unpaid_coupon: float
    """broker fee for the whole trade. Always negative"""
    broker_fee: float
    """exchange fee for the whole trade. Always negative"""
    exchange_fee: float
    trade_id: str
    comment: str
    status: str

    def __post_init__(self):
        # TODO: add test
        if self.unit_price <= 0.0:
            raise ValueError(f"unit_price must be positive. trade_id={self.trade_id}")
        if self.side == Side.BUY:
            if self.num_shares <= 0.0:
                raise ValueError(f"num_shares must be positive. trade_id={self.trade_id}")
            if self.amount > 0.0:
                raise ValueError(f"Inconsistent sign of amount vs side. trade_id={self.trade_id}")
            if self.accum_unpaid_coupon > 0.0:
                raise ValueError(f"Inconsistent sign of accum_unpaid_coupon vs side. trade_id={self.trade_id}")
        elif self.side == Side.SELL:
            if self.num_shares >= 0.0:
                raise ValueError(f"num_shares must be negative. trade_id={self.trade_id}")
            if self.amount < 0.0:
                raise ValueError(f"Inconsistent sign of amount vs side. trade_id={self.trade_id}")
            if self.accum_unpaid_coupon < 0.0:
                raise ValueError(f"Inconsistent sign of accum_unpaid_coupon vs side. trade_id={self.trade_id}")
        if self.broker_fee > 0.0:
            raise ValueError(f"broker_fee cannot be positive. trade_id={self.trade_id}")
        if self.exchange_fee > 0.0:
            raise ValueError(f"exchange_fee cannot be positive. trade_id={self.trade_id}")


@dataclass(frozen=True)
class FXTradesReportLine:
    """when trade was made"""
    trade_datetime: datetime.datetime
    """when trade will be settled"""
    settle_date: datetime.date
    instrument_name: str
    """buy/sell"""
    side: Side
    """price of 1 under_ccy in acc_ccy for the deal. always positive"""
    strike: float
    """if side=buy, we are buying this for acc_ccy. if side=sell, we're selling this for acc_ccy"""
    und_ccy: str
    """if side=buy, we are buying this amount of und_ccy. if side=sell, we're selling this amount of und_ccy"""
    und_amt: float
    """if side=buy, we are selling this for und_ccy. if side=sell, we're buying this for und_ccy"""
    acc_ccy: str
    """if side=buy, we are selling this amount of acc_ccy. if side=sell, we're buying this amount of acc_ccy"""
    acc_amt: float
    """broker fee for the whole trade"""
    broker_fee: float
    """exchange fee for the whole trade"""
    exchange_fee: float
    trade_id: str
    comment: str

    def __post_init__(self):
        # TODO: add test
        if self.strike <= 0.0:
            raise ValueError(f"strike must be positive. trade_id={self.trade_id}")
        if self.side == Side.BUY:
            if self.und_amt <= 0.0:
                raise ValueError(f"Inconsistent sign of und_amt vs side. trade_id={self.trade_id}")
            if self.acc_amt >= 0.0:
                raise ValueError(f"Inconsistent sign of acc_amt vs side. trade_id={self.trade_id}")
        elif self.side == Side.SELL:
            if self.und_amt >= 0.0:
                raise ValueError(f"Inconsistent sign of und_amt vs side. trade_id={self.trade_id}")
            if self.acc_amt <= 0.0:
                raise ValueError(f"Inconsistent sign of acc_amt vs side. trade_id={self.trade_id}")
        if self.broker_fee > 0.0:
            raise ValueError(f"broker_fee cannot be positive. trade_id={self.trade_id}")
        if self.exchange_fee > 0.0:
            raise ValueError(f"exchange_fee cannot be positive. trade_id={self.trade_id}")


@dataclass(frozen=True)
class Report:
    client_name: str
    account_id: str
    acc_start_date: datetime.date
    cashflows: List[CashflowReportLine]
    equity_trades: List[EquityTradesReportLine]
    fx_trades: List[FXTradesReportLine]
    source_file: str

    @staticmethod
    def join_reports(reports: List[Report]) -> Report:
        client_names: Set[str] = set()
        account_ids: Set[str] = set()
        start_dates: Set[datetime.date] = set()
        fnames: Set[str] = set()
        # as cashflows don't have unique id, we just join everything to one list and later will sort it by date
        joined_cashflows: List[CashflowReportLine] = []
        equity_trades_by_trade_id: Mapping[str, EquityTradesReportLine] = {}
        fx_trades_by_trade_id: Mapping[str, FXTradesReportLine] = {}
        for rep in reports:
            logger.info(f"Joining report from {rep.source_file}")
            client_names.add(rep.client_name)
            account_ids.add(rep.account_id)
            start_dates.add(rep.acc_start_date)
            fnames.add(rep.source_file)

            for eq_tr in rep.equity_trades:
                trade_id = eq_tr.trade_id
                if trade_id in equity_trades_by_trade_id:
                    # raise RuntimeError(f"Already saw equity trade_id {trade_id}")
                    logger.info(f"Already saw equity trade_id {trade_id}, skipping it")
                    # when parsing daily reports for trades settled not today, day 1 will contain this trade but
                    # without a cashflow, and day 2 will contain this trade again, and the cash flow.
                    continue
                equity_trades_by_trade_id[trade_id] = eq_tr

            for fx_tr in rep.fx_trades:
                trade_id = fx_tr.trade_id
                if trade_id in fx_trades_by_trade_id:
                    logger.info(f"Already saw FX trade_id {trade_id}")
                    continue
                fx_trades_by_trade_id[trade_id] = fx_tr

            # as there is no cash flow id, we cannot fully check for doubles...
            for cf in rep.cashflows:
                joined_cashflows.append(cf)

        cashflows = sorted(joined_cashflows, key=lambda cf: (cf.date, cf.trading_platform, cf.ccy))

        sorted_equity_trade_ids = sorted(equity_trades_by_trade_id.keys(), key=lambda trade_id: int(trade_id))
        equity_trades = []
        for trade_id in sorted_equity_trade_ids:
            equity_trade = equity_trades_by_trade_id[trade_id]
            equity_trades.append(equity_trade)

        sorted_fx_trade_ids = sorted(fx_trades_by_trade_id.keys(), key=lambda trade_id: int(trade_id))
        fx_trades = []
        for trade_id in sorted_fx_trade_ids:
            fx_trade = fx_trades_by_trade_id[trade_id]
            fx_trades.append(fx_trade)

        return Report(", ".join(client_names), ", ".join(account_ids), min(start_dates),
                      cashflows, equity_trades, fx_trades,
                      ", ".join(fnames))


investor_regex = re.compile(r" Инвестор:\s(?P<client_name>.+)")
account_details_regex = re.compile(
    r"Договор\s(?P<account>\w+)\sот\s(?P<acc_start_date>\d{2}\.\d{2}\.\d{4}).*$")


def traverse_reports(dirnames: List[str]) -> Report:
    reports: List[Report] = []
    for dirname in dirnames:
        daily_reps_pattern = os.path.join(dirname, "*.html")
        for fname in glob.glob(daily_reps_pattern):
            logger.info(f"Parsing {fname}")
            # need "b" to open _some_ of report files that have 'binary' data after closing </html> tag
            with open(fname, 'rb') as f:
                rep_html = f.read()
                rep = parse_report(fname, rep_html)
                reports.append(rep)
    return Report.join_reports(reports)


def parse_report(fname: str, rep_html: bytes) -> Report:
    soup = BeautifulSoup(rep_html, 'html.parser')
    investor = soup.find(lambda tag: tag.name == "p" and "Инвестор" in tag.get_text())
    # ' Инвестор: Фамилия Имя Отчество'
    match_investor = investor_regex.search(investor.contents[0])
    client_name = match_investor["client_name"]
    # 'Договор 1234ABC от DD.MM.YYYY\n'
    match_acct_details = account_details_regex.search(investor.contents[2])
    account_id = match_acct_details["account"]
    acc_start_date = parse_date(match_acct_details["acc_start_date"])

    cashflows = parse_cashflows(soup)
    equity_trades = parse_equity_trades(soup)
    fx_trades = parse_fx_trades(soup)
    return Report(client_name, account_id, acc_start_date, cashflows, equity_trades, fx_trades, fname)


def parse_cashflows(soup) -> List[CashflowReportLine]:
    """
    unique info in this table:
     - money in/out
     - transfers
     - taxes
     - coupons
     - amortizations

     info NOT in this table:
     - accumulated unpaid coupon
     - yield to maturity (it's nowhere, needs to be calculated)
    """
    p_of_cashflows = soup.find(lambda tag: tag.name == "p" and "Движение денежных средств за период" in tag.get_text())
    if p_of_cashflows is None:
        return []
    cash_table_rows = p_of_cashflows.find_next_sibling("table").find_all("tr")
    cash_table_header = cash_table_rows[0]
    idx_date = None
    idx_trading_platform = None
    idx_operation_description = None
    idx_ccy = None
    idx_amt_debit = None
    idx_amt_credit = None
    for idx, col in enumerate(cash_table_header.find_all("td")):
        col_name = col.get_text()
        match col_name:
            case "Дата":
                idx_date = idx
            case "Торговая площадка":
                idx_trading_platform = idx
            case "Описание операции":
                idx_operation_description = idx
            case "Валюта":
                idx_ccy = idx
            # see wikipedia for debit/credit - for active accounts it's like this.
            # and our account is active because it's assets not liabilities.
            # for passive accounts its vice versa.
            case "Сумма зачисления":
                idx_amt_debit = idx
            case "Сумма списания":
                idx_amt_credit = idx
    cashflows: List[CashflowReportLine] = []
    for row in cash_table_rows[1:]:
        cols = row.find_all("td")
        if "Итого" in cols[0].get_text():
            # we continue instead of break because summaries are given for trading platforms
            # and we could have operations for several platforms
            continue
        date = parse_date(cols[idx_date].get_text())
        trading_platform = cols[idx_trading_platform].get_text()
        description = cols[idx_operation_description].get_text()
        ccy = cols[idx_ccy].get_text()
        # TODO: maybe make them signed like in trades object?
        amt_debit = parse_num(cols[idx_amt_debit].get_text())
        amt_credit = parse_num(cols[idx_amt_credit].get_text())
        amt_total = amt_debit - amt_credit
        if "Сделка" not in description and "Комиссия Биржи" not in description and \
                "Комиссия Брокера" not in description and \
                "Перевод д/с для проведения расчетов по клирингу" not in description:
            entry = CashflowReportLine(date, trading_platform, description, ccy, amt_debit, amt_credit, amt_total)
            cashflows.append(entry)
    return cashflows


def parse_equity_trades(soup) -> List[EquityTradesReportLine]:
    p_of_equity_trades = soup.find(
        lambda tag: tag.name == "p" and "Сделки купли/продажи ценных бумаг" in tag.get_text())
    if p_of_equity_trades is None:
        return []
    equity_trades_table_rows = p_of_equity_trades.find_next_sibling("table").find_all("tr")
    equity_trades_table_header = equity_trades_table_rows[0]
    idx_trade_date = None
    idx_settle_date = None
    idx_trade_time = None
    idx_security_name = None
    idx_isin = None
    idx_ccy = None
    idx_side = None
    idx_notional = None
    idx_unit_price = None
    idx_amount = None
    idx_accum_unpaid_coupon = None
    idx_broker_fee = None
    idx_exchange_fee = None
    idx_trade_id = None
    idx_comment = None
    idx_status = None
    for idx, col in enumerate(equity_trades_table_header.find_all("td")):
        col_name = col.get_text()
        match col_name:
            case "Дата заключения":
                idx_trade_date = idx
            case "Дата расчетов":
                idx_settle_date = idx
            case "Время заключения":
                idx_trade_time = idx
            case "Наименование ЦБ":
                idx_security_name = idx
            case "Код ЦБ":
                idx_isin = idx
            case "Валюта":
                idx_ccy = idx
            case "Вид":
                idx_side = idx
            case "Количество, шт.":
                idx_notional = idx
            case "Цена**":
                idx_unit_price = idx
            case "Сумма":
                idx_amount = idx
            case "НКД":
                idx_accum_unpaid_coupon = idx
            case "Комиссия Брокера":
                idx_broker_fee = idx
            case "Комиссия Биржи":
                idx_exchange_fee = idx
            case "Номер сделки":
                idx_trade_id = idx
            case "Комментарий":
                idx_comment = idx
            case "Статус сделки*****":
                idx_status = idx
    equity_trades: List[EquityTradesReportLine] = []
    for row in equity_trades_table_rows[1:]:
        cols = row.find_all("td")
        first_col = cols[0].get_text()
        # TODO: shouldn't ignore Площадка, it's a subaccount column extracted to top level.
        if "Площадка" in first_col or "Итого" in first_col:
            # we continue instead of break because summaries are given for trading platforms
            # and we could have operations for several platforms
            continue
        trade_datetime = parse_datetime(cols[idx_trade_date].get_text(), cols[idx_trade_time].get_text())
        settle_date = parse_date(cols[idx_settle_date].get_text())
        security_name = cols[idx_security_name].get_text()
        # TODO: for SBMX, it's not ISIN but "SBMX" which has its own ISIN...
        isin = cols[idx_isin].get_text()
        ccy = cols[idx_ccy].get_text()
        side = parse_side(cols[idx_side].get_text())
        num_shares_positive = parse_num(cols[idx_notional].get_text())
        num_shares = num_shares_positive if side == Side.BUY else -num_shares_positive
        unit_price = parse_num(cols[idx_unit_price].get_text())
        amount_positive = parse_num(cols[idx_amount].get_text())
        amount = -amount_positive if side == Side.BUY else amount_positive
        accum_unpaid_coupon_positive = parse_num(cols[idx_accum_unpaid_coupon].get_text())
        accum_unpaid_coupon = -accum_unpaid_coupon_positive if side == Side.BUY else accum_unpaid_coupon_positive
        broker_fee = -parse_num(cols[idx_broker_fee].get_text())
        exchange_fee = -parse_num(cols[idx_exchange_fee].get_text())
        trade_id = cols[idx_trade_id].get_text()
        comment = cols[idx_comment].get_text()
        status = cols[idx_status].get_text()
        entry = EquityTradesReportLine(
            trade_datetime, settle_date, security_name, isin, ccy, side, num_shares, unit_price, amount,
            accum_unpaid_coupon, broker_fee, exchange_fee, trade_id, comment, status)
        equity_trades.append(entry)
    return equity_trades


def parse_fx_trades(soup) -> List[FXTradesReportLine]:
    p_of_fx_trades = soup.find(
        lambda tag: tag.name == "p" and "Сделки с валютными инструментами за период" in tag.get_text())
    if p_of_fx_trades is None:
        return []
    fx_trades_table_rows = p_of_fx_trades.find_next_sibling("table").find_all("tr")
    fx_trades_table_header = fx_trades_table_rows[0]
    idx_trade_date = None
    idx_settle_date = None
    idx_trade_time = None
    idx_security_name = None
    idx_side = None
    idx_und_amt = None
    idx_strike = None
    idx_acc_amt = None
    idx_broker_fee = None
    idx_exchange_fee = None
    idx_trade_id = None
    idx_comment = None
    for idx, col in enumerate(fx_trades_table_header.find_all("td")):
        col_name = col.get_text()
        match col_name:
            case "Дата заключения":
                idx_trade_date = idx
            case "Дата расчетов":
                idx_settle_date = idx
            case "Время заключения":
                idx_trade_time = idx
            case "Валютный инструмент":
                idx_security_name = idx
            case "Вид":
                idx_side = idx
            case "Количество базовой валюты лота":
                idx_und_amt = idx
            case "Цена":
                idx_strike = idx
            case "Сумма сделки в сопряженной валюте":
                idx_acc_amt = idx
            case "Комиссия Брокера оборотная, руб":
                idx_broker_fee = idx
            case "Комиссия Биржи, руб":
                idx_exchange_fee = idx
            case "Номер сделки":
                idx_trade_id = idx
            case "Комментарий":
                idx_comment = idx

    fx_trades: List[FXTradesReportLine] = []
    for row in fx_trades_table_rows[1:]:
        cols = row.find_all("td")
        first_col = cols[0].get_text()
        if "Оборот" in first_col:
            continue
        trade_datetime = parse_datetime(cols[idx_trade_date].get_text(), cols[idx_trade_time].get_text())
        settle_date = parse_date(cols[idx_settle_date].get_text())
        security_name = cols[idx_security_name].get_text().strip()
        und_ccy = security_name[0:3]
        acc_ccy = security_name[3:6]
        side = parse_side(cols[idx_side].get_text())
        und_amt_positive = parse_num(cols[idx_und_amt].get_text())
        und_amount = und_amt_positive if side == Side.BUY else -und_amt_positive
        strike = parse_num(cols[idx_strike].get_text())
        acc_amt_positive = parse_num(cols[idx_acc_amt].get_text())
        acc_amt = -acc_amt_positive if side == Side.BUY else acc_amt_positive
        broker_fee = -parse_num(cols[idx_broker_fee].get_text())
        exchange_fee = -parse_num(cols[idx_exchange_fee].get_text())
        trade_id = cols[idx_trade_id].get_text()
        comment = cols[idx_comment].get_text()
        entry = FXTradesReportLine(
            trade_datetime, settle_date, security_name, side, strike, und_ccy, und_amount, acc_ccy,
            acc_amt, broker_fee, exchange_fee, trade_id, comment)
        fx_trades.append(entry)
    return fx_trades


def parse_date(date: str) -> datetime.date:
    """Parses date in format dd.mm.yyyy e.g. 01.02.2021"""
    day = int(date[0:2])
    month = int(date[3:5])
    year = int(date[6:10])
    return datetime.date(year, month, day)


def parse_datetime(date: str, time: str) -> datetime.datetime:
    """Parses date in format dd.mm.yyyy e.g. 01.02.2021 and time in format HH24:MM:SS e.g. 17:01:05"""
    day = int(date[0:2])
    month = int(date[3:5])
    year = int(date[6:10])
    hh24 = int(time[0:2])
    mm = int(time[3:5])
    ss = int(time[6:8])
    return datetime.datetime(year, month, day, hh24, mm, ss)


def parse_num(str_num: str) -> float:
    return float(str_num.replace(' ', ''))


def parse_side(str_side: str) -> Side:
    match str_side:
        case "Покупка":
            return Side.BUY
        case "Продажа":
            return Side.SELL
        case _:
            raise ValueError(f"Unknown side {str_side}")
