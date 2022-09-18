import dataclasses
import datetime
import logging
from typing import Mapping

import pytest
import iwantitmore.reports_parser as subj
import io
import csv
import time

from iwantitmore.instruments import Bond
from iwantitmore.moex import FXInstrument, ShareInstrument, BondInstrument, Instrument

logger = logging.getLogger(__name__)


class TestReportsParser:

    def test_parse_report(self, sample_report_html: str):
        rep = subj.parse_report("test", sample_report_html)
        report_to_csvs(rep)

    def test_parse_all_daily_reports(self):
        # todo:
        # 1. with non-used cash balance (i.e. coupons not spent on new buys), total doesn't reflect them!
        # 2. account for accrued interest at current date
        # 3. make option to use not live but last eod prices, to work on weekends
        agg_rep = subj.traverse_reports([
        ])
        # logger.info(agg_rep)
        report_to_csvs(agg_rep)

        domestic = "RUB"

        ccy_to_fx_amt, fx_fees_dom = get_fx_totals(agg_rep, domestic)
        logger.info("Balance of FX account (doesn't include money which never went into deals!):")
        for ccy, amt in ccy_to_fx_amt.items():
            logger.info(f"{amt} {ccy}")

        instr_usdrub = FXInstrument("USD000UTSTOM")
        instr_eurrub = FXInstrument("EUR_RUB__TOM")
        isin_to_instr: Mapping[str, Instrument] = {
        }

        isin_to_num_shares, ccy_to_amt_for_eq, eq_fees_dom = get_eq_totals(agg_rep, domestic)
        logger.info("Balance of Equity account (num shares):")
        for isin, num_shares in isin_to_num_shares.items():
            if isin not in isin_to_instr:
                raise ValueError(f"no mapping for {isin}")
            instr = isin_to_instr[isin]
            instr.resolve_shortname()
            logger.info(f"{num_shares} {isin} {instr.name}")
        logger.info(
            f"Balance of Equity account (money paid - {domestic} also includes broker/exchange fees {eq_fees_dom}):")
        for ccy, amt in ccy_to_amt_for_eq.items():
            logger.info(f"{amt} {ccy}")

        # we need cash flows for stuff which e.g. was debited to account but never spent on any deals or appeared there
        # by itself, i.e. coupons. if we don't consider it we lose interest.
        (trading_platform_to_ccy_to_total_cf_balance, trading_platform_to_ccy_to_my_money_inflow) = get_cash_flow_totals(agg_rep)

        while True:
            non_dom_ccys_in_dom = 0.0
            for ccy, amt in ccy_to_fx_amt.items():
                match ccy:
                    case "RUB":
                        continue
                    case "EUR":
                        eurrub_spot = instr_eurrub.load_intraday_quotes()
                        non_dom_ccys_in_dom += amt * eurrub_spot.last
                    case "USD":
                        usdrub_spot = instr_usdrub.load_intraday_quotes()
                        non_dom_ccys_in_dom += amt * usdrub_spot.last
                    case _:
                        raise ValueError(f"not supported ccy {ccy}")

            # negative
            paid_fx_dom = ccy_to_fx_amt[domestic]
            # all cash in fx acct _including_ money which never went to fx deals but is on fx acct
            # TODO: there can also be resting amounts in all other ccys
            CASH_FLOW_FX_MARKET = "Валютный рынок"
            total_cash_fx_dom = trading_platform_to_ccy_to_total_cf_balance[CASH_FLOW_FX_MARKET][domestic]
            # free balance on fx acct not in fx ccys, i.e. including money not part of deals
            fx_remaining_balance_dom = total_cash_fx_dom - abs(paid_fx_dom)
            my_money_inflow_to_fx_dom = trading_platform_to_ccy_to_my_money_inflow[CASH_FLOW_FX_MARKET][domestic]

            logger.info(
                "====================================FX=========================================================")
            logger.info(
                f"Paid for FX deals in domestic: {paid_fx_dom} {domestic} (includes fees of {fx_fees_dom} {domestic})")
            logger.info(f"My money inflow into FX acct: {my_money_inflow_to_fx_dom} {domestic}")
            logger.info(f"Free remaining balance in FX acct: {fx_remaining_balance_dom} {domestic}")
            logger.info(f"Current value of FX deals in domestic: {non_dom_ccys_in_dom} {domestic}")
            logger.info(
                f"Return on FX deals: {(abs((non_dom_ccys_in_dom + fx_remaining_balance_dom) / my_money_inflow_to_fx_dom) - 1.0) * 100.0}%")
            logger.info(
                f"Earned (lost) on FX deals: {abs(non_dom_ccys_in_dom + fx_remaining_balance_dom) - abs(my_money_inflow_to_fx_dom)}")
            logger.info(
                "====================================/FX=========================================================")

            today = datetime.date.today()
            cur_eq_val_dom = 0.0
            for isin, num_shares in isin_to_num_shares.items():
                instr = isin_to_instr[isin]
                # TODO: quote.last will return 0 when trading hasn't started in the morning
                quote = instr.load_intraday_quotes()
                if isinstance(instr, BondInstrument):
                    bond: Bond = instr.load_bond()
                    notional = bond.notional_on_date(today)
                    cur_eq_val_dom += notional * quote.last / 100.0 * num_shares
                    accrued_interest = bond.accrued_interest_on_date(today)  # ACI
                    if num_shares < 0.0:
                        accrued_interest = -accrued_interest
                    cur_eq_val_dom += accrued_interest * num_shares
                else:
                    cur_eq_val_dom += num_shares * quote.last

            logger.info(
                "================================EQUITIES=============================================================")
            logger.info(f"Current value of equities in domestic: {cur_eq_val_dom} {domestic}")
            paid_eq_dom = ccy_to_amt_for_eq[domestic]
            # all cash in equities acct _including_ money which never went to eq deals but is
            # on eq acct (i.e. unspent amortizations, coupons)
            # TODO: there can also be resting amounts in all other ccys
            CASH_FLOW_EQ_MARKET = "Фондовый рынок"
            total_cash_eq_dom = trading_platform_to_ccy_to_total_cf_balance[CASH_FLOW_EQ_MARKET][domestic]
            # free balance on eq account, i.e. including moeny which were not spent in deals, unspent coupons,
            # amortizations etc
            eq_remaining_balance_dom = total_cash_eq_dom - abs(paid_eq_dom)
            my_money_inflow_to_eq_dom = trading_platform_to_ccy_to_my_money_inflow[CASH_FLOW_EQ_MARKET][domestic]

            logger.info(
                f"Paid for equity deals in domestic: {paid_eq_dom} {domestic} (includes fees of {eq_fees_dom} {domestic})")
            logger.info(f"My money inflow into Equities acct: {my_money_inflow_to_eq_dom} {domestic}")
            logger.info(f"Free remaining balance in Equities acct: {eq_remaining_balance_dom} {domestic}")
            logger.info(
                f"Return on equity deals: {(abs((cur_eq_val_dom + eq_remaining_balance_dom) / my_money_inflow_to_eq_dom) - 1.0) * 100.0}%")
            logger.info(
                f"Earned (lost) on equity deals: {abs(cur_eq_val_dom + eq_remaining_balance_dom) - abs(my_money_inflow_to_eq_dom)} {domestic}")
            logger.info(
                "================================/EQUITIES=============================================================")

            logger.info(
                "================================TOTAL=============================================================")
            pv_total = non_dom_ccys_in_dom + fx_remaining_balance_dom + cur_eq_val_dom + eq_remaining_balance_dom
            # todo: should divide not by how much I paid but by how much I _myself_ put into accounts
            total_my_money_inflow = my_money_inflow_to_fx_dom + my_money_inflow_to_eq_dom
            logger.info(f"Current portfolio value: {pv_total} {domestic}")
            logger.info(f"Total my money inflow to FX+Equities accts: {total_my_money_inflow} {domestic}")
            logger.info(f"Return on portfolio: {(abs(pv_total / total_my_money_inflow) - 1.0) * 100.0}%")
            logger.info(f"Earned (lost) on portfolio: {abs(pv_total) - abs(total_my_money_inflow)} {domestic}")
            logger.info(
                "================================/TOTAL=============================================================")

            time.sleep(600)


@pytest.fixture()
def sample_report_html() -> str:
    # TODO: don't commit
    fname = r""
    with open(fname, "rb") as f:
        yield f.read()


def report_to_csvs(rep: subj.Report):
    logger.info("Cashflows:")
    cf_csv = dataclass_to_csv(subj.CashflowReportLine, rep.cashflows)
    logger.info(cf_csv)
    with open("cf.csv", "w", encoding="utf-8", newline='') as f:
        f.write(cf_csv)

    logger.info("Equity trades:")
    eq_csv = dataclass_to_csv(subj.EquityTradesReportLine, rep.equity_trades)
    logger.info(eq_csv)
    with open("equity.csv", "w", encoding="utf-8", newline='') as f:
        f.write(eq_csv)

    logger.info("FX trades:")
    fx_csv = dataclass_to_csv(subj.FXTradesReportLine, rep.fx_trades)
    logger.info(fx_csv)
    with open("fx.csv", "w", encoding="utf-8", newline='') as f:
        f.write(fx_csv)


def dataclass_to_csv(dataclass_name, entries) -> str:
    fieldnames = [f.name for f in dataclasses.fields(dataclass_name)]
    with io.StringIO() as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for entry in entries:
            writer.writerow(dataclasses.asdict(entry))
        return f.getvalue()


def get_cash_flow_totals(agg_rep: subj.Report) -> \
        (Mapping[str, Mapping[str, float]], Mapping[str, Mapping[str, float]]):
    trading_platform_to_ccy_to_cf_total: Mapping[str, Mapping[str, float]] = {}
    trading_platform_to_ccy_to_my_money_inflow: Mapping[str, Mapping[str, float]] = {}
    for cash_flow in agg_rep.cashflows:
        trading_platform = cash_flow.trading_platform
        if trading_platform not in trading_platform_to_ccy_to_cf_total:
            trading_platform_to_ccy_to_cf_total[trading_platform] = {}
        ccy_to_total = trading_platform_to_ccy_to_cf_total[trading_platform]
        ccy = cash_flow.ccy
        if ccy not in ccy_to_total:
            ccy_to_total[ccy] = cash_flow.amt_total
        else:
            ccy_to_total[ccy] += cash_flow.amt_total

        desc = cash_flow.description
        if desc == "Зачисление д/с" or desc == "Списание д/с" or "Перевод д/с" in desc:
            if trading_platform not in trading_platform_to_ccy_to_my_money_inflow:
                trading_platform_to_ccy_to_my_money_inflow[trading_platform] = {}
            ccy_to_my_money_inflow = trading_platform_to_ccy_to_my_money_inflow[trading_platform]
            if ccy not in ccy_to_my_money_inflow:
                ccy_to_my_money_inflow[ccy] = cash_flow.amt_total
            else:
                ccy_to_my_money_inflow[ccy] += cash_flow.amt_total
    return trading_platform_to_ccy_to_cf_total, trading_platform_to_ccy_to_my_money_inflow


def get_fx_totals(agg_rep: subj.Report, domestic: str) -> (Mapping[str, float], float):
    ccy_to_amt: Mapping[str, float] = {}
    ccy_to_amt_for_wa: Mapping[str, float] = {}
    fees_dom = 0.0
    for fx_tr in agg_rep.fx_trades:
        und = fx_tr.und_ccy
        if und not in ccy_to_amt:
            ccy_to_amt[und] = fx_tr.und_amt
        else:
            ccy_to_amt[und] += fx_tr.und_amt
        acc = fx_tr.acc_ccy
        if acc not in ccy_to_amt:
            ccy_to_amt[acc] = fx_tr.acc_amt
        else:
            ccy_to_amt[acc] += fx_tr.acc_amt
        fees_dom += fx_tr.broker_fee
        fees_dom += fx_tr.exchange_fee

        if und not in ccy_to_amt_for_wa:
            ccy_to_amt_for_wa[und] = 0.0
        ccy_to_amt_for_wa[und] += fx_tr.und_amt * fx_tr.strike

    if domestic not in ccy_to_amt:
        ccy_to_amt[domestic] = fees_dom
    else:
        ccy_to_amt[domestic] += fees_dom

    ccy_to_strike_wa: Mapping[str, float] = {}
    for ccy in ccy_to_amt.keys():
        if ccy == domestic:
            continue
        amt = ccy_to_amt[ccy]
        ccy_to_strike_wa[ccy] = ccy_to_amt_for_wa[ccy] / amt
    for ccy, strike_wa in ccy_to_strike_wa.items():
        logger.info(f"Weighted average strike for {ccy}: {strike_wa}")
    return ccy_to_amt, fees_dom


def get_eq_totals(agg_rep: subj.Report, domestic: str) -> (Mapping[str, float], Mapping[str, float], float):
    isin_to_num_shares: Mapping[str, float] = {}
    ccy_to_amount: Mapping[str, float] = {}
    fees_dom = 0.0
    for eq_tr in agg_rep.equity_trades:
        isin = eq_tr.isin
        if isin not in isin_to_num_shares:
            isin_to_num_shares[isin] = eq_tr.num_shares
        else:
            isin_to_num_shares[isin] += eq_tr.num_shares
        ccy = eq_tr.ccy
        paid_or_received_wo_fees = eq_tr.amount + eq_tr.accum_unpaid_coupon
        if ccy not in ccy_to_amount:
            ccy_to_amount[ccy] = paid_or_received_wo_fees
        else:
            ccy_to_amount[ccy] += paid_or_received_wo_fees
        fees_dom += eq_tr.broker_fee
        fees_dom += eq_tr.exchange_fee
    if domestic not in ccy_to_amount:
        ccy_to_amount[domestic] = fees_dom
    else:
        ccy_to_amount[domestic] += fees_dom
    for k in list(isin_to_num_shares.keys()):
        if isin_to_num_shares[k] == 0.0:
            del isin_to_num_shares[k]
    for k in list(ccy_to_amount.keys()):
        if ccy_to_amount[k] == 0.0:
            del ccy_to_amount[k]
    return isin_to_num_shares, ccy_to_amount, fees_dom
