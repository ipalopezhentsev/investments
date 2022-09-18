from datetime import date, timedelta

from iwantitmore.instruments import Bond, AmortizationScheduleEntry, CouponScheduleEntry, YEAR_BASE, OHLC, OHLCSeries, \
    CashFlows, CashFlow
import pytest


class TestCouponScheduleEntry:
    def test_cannot_put_negative_value(self):
        with pytest.raises(ValueError):
            CouponScheduleEntry(date(2019, 9, 6), date(2019, 9, 5), date(2019, 9, 3),
                                value=-1.0, yearly_prc=10.0)

    def test_cannot_put_negative_prc(self):
        with pytest.raises(ValueError):
            CouponScheduleEntry(date(2019, 9, 6), date(2019, 9, 5), date(2019, 9, 3),
                                value=1.0, yearly_prc=-10.0)

    def test_cannot_put_record_date_after_coupon_date(self):
        with pytest.raises(ValueError):
            CouponScheduleEntry(coupon_date=date(2019, 9, 6), record_date=date(2019, 9, 7),
                                start_date=date(2019, 9, 3), value=1.0, yearly_prc=10.0)

    def test_cannot_put_start_date_after_coupon_date(self):
        with pytest.raises(ValueError):
            CouponScheduleEntry(coupon_date=date(2019, 9, 6), record_date=date(2019, 9, 5),
                                start_date=date(2019, 9, 7), value=1.0, yearly_prc=10.0)


class TestBond:
    def test_cannot_create_bond_with_not_increasing_amort_dates(self):
        with pytest.raises(ValueError):
            amortizations = [AmortizationScheduleEntry(date(2019, 9, 6), 30.0, 300.0),
                             # note date goes before first date, this is wrong
                             AmortizationScheduleEntry(date(2019, 9, 3), 70.0, 700.0)]
            Bond(coupons=[], amortizations=amortizations)

    def test_cannot_create_bond_with_not_increasing_coupon_dates(self):
        with pytest.raises(ValueError):
            amortizations = [AmortizationScheduleEntry(date(2019, 9, 3), 100.0, 1000.0)]
            coupons = [CouponScheduleEntry(date(2018, 1, 1), None, date(2017, 1, 1), 10.0, 10.0),
                       # note date goes before first date, this is wrong
                       CouponScheduleEntry(date(2017, 12, 1), None, date(2017, 11, 30), 10.0, 10.0)]
            Bond(coupons=coupons, amortizations=amortizations)

    def test_cannot_create_bond_with_not_fully_amortized_notional_prc(self):
        with pytest.raises(ValueError):
            amortizations = [AmortizationScheduleEntry(date(2019, 9, 3), 10.0, 100.0)]
            Bond(coupons=[], amortizations=amortizations)

    def test_cannot_create_bond_with_not_fully_amortized_notional(self):
        with pytest.raises(ValueError):
            amortizations = [AmortizationScheduleEntry(date(2019, 9, 3), 100.0, 999.0)]
            Bond(coupons=[], amortizations=amortizations)

    def test_notional_on_date(self):
        initial_notional = 1000
        am_dt1 = date(2019, 9, 6)
        am_dt2 = date(2020, 9, 4)
        am_dt3 = date(2021, 9, 3)
        amortizations = [AmortizationScheduleEntry(am_dt1, 30.0, 300.0),
                         AmortizationScheduleEntry(am_dt2, 30.0, 300.0),
                         AmortizationScheduleEntry(am_dt3, 40.0, 400.0)]
        b = Bond(initial_notional=initial_notional, coupons=[], amortizations=amortizations)
        # date before first amortization - notional should equal initial notional
        one_day = timedelta(days=1)

        assert b.notional_on_date(am_dt1 - one_day) == initial_notional
        # on amortization date - notional still stays the same, it affects only next coupons
        assert b.notional_on_date(am_dt1) == initial_notional
        notional_after_am_dt1 = initial_notional * (1 - amortizations[0].value_prc / 100.0)
        assert b.notional_on_date(am_dt1 + one_day) == pytest.approx(notional_after_am_dt1)

        assert b.notional_on_date(am_dt2 - one_day) == notional_after_am_dt1
        assert b.notional_on_date(am_dt2) == notional_after_am_dt1
        notional_after_am_dt2 = initial_notional * (
                1 - (amortizations[0].value_prc + amortizations[0].value_prc) / 100.0)
        assert b.notional_on_date(am_dt2 + one_day) == pytest.approx(notional_after_am_dt2)

        assert b.notional_on_date(am_dt3 - one_day) == notional_after_am_dt2
        assert b.notional_on_date(am_dt3) == notional_after_am_dt2
        # last amortization date is settlement date
        notional_after_settlement = 0.0
        assert b.notional_on_date(am_dt3 + one_day) == pytest.approx(notional_after_settlement)

    def test_coupon_on_date(self):
        initial_notional = 1000
        coup1_start_date = date(2017, 12, 8)
        coup1_date = date(2018, 3, 9)
        # note start coupon date != prev coupon date! due to holidays.
        # but coupon value disregards this start date and takes it from
        # prev. coupon date
        coup2_start_date = date(2018, 3, 12)
        coup2_date = date(2018, 6, 8)
        coupons = [CouponScheduleEntry(coup1_date, None, coup1_start_date, 0.0, 11.7),
                   CouponScheduleEntry(coup2_date, None, coup2_start_date, 0.0, 11.7)]
        amortizations = [AmortizationScheduleEntry(date(2018, 1, 1), 30.0, 300.0),
                         AmortizationScheduleEntry(coup2_date, 70.0, 700.0)]
        b = Bond(initial_notional=initial_notional, coupons=coupons, amortizations=amortizations)
        notional_on_coup2 = b.notional_on_date(coup2_date)
        assert notional_on_coup2 == 700.0
        # note we find days between coupon date 1 & 2, not from coupon 2 start/end date, because otherwise they
        # won't match due to different day counts due to holidays
        coupon_days = (coup2_date - coup1_date).days
        expected_coupon1 = round((coupon_days / YEAR_BASE) *
                                 (coupons[0].yearly_prc / 100.0) * notional_on_coup2, 2)
        coupon_days_wrong = (coup2_date - coupons[1].start_date).days
        expected_coupon1_wrong = round((coupon_days_wrong / YEAR_BASE) *
                                       (coupons[0].yearly_prc / 100.0) * notional_on_coup2, 2)
        assert b.accrued_interest_on_date(coup2_date) == pytest.approx(expected_coupon1)
        assert expected_coupon1 != pytest.approx(expected_coupon1_wrong)

    def test_payments_since_date(self):
        coup1_start_date = date(2017, 12, 8)
        coup1_date = date(2018, 3, 9)
        coup2_start_date = date(2018, 3, 12)
        coup2_date = date(2018, 6, 8)
        coupons = [CouponScheduleEntry(coup1_date, None, coup1_start_date, 0.0, 11.7),
                   CouponScheduleEntry(coup2_date, None, coup2_start_date, 0.0, 11.7)]
        amortizations = [AmortizationScheduleEntry(date(2018, 1, 1), 30.0, 300.0),
                         AmortizationScheduleEntry(coup2_date, 70.0, 700.0)]
        b = Bond(coupons=coupons, amortizations=amortizations)
        cps, ams = b.payments_since_date(date(2018, 5, 8))
        assert cps == [coupons[1]]
        assert ams == [amortizations[1]]

    def test_ytm(self):
        coup1_start_date = date(2017, 3, 20)
        coup1_date = date(2018, 3, 20)
        coup2_start_date = date(2018, 3, 21)
        coup2_date = date(2019, 3, 20)
        coupons = [CouponScheduleEntry(coup1_date, None, coup1_start_date, 100.0, 0.0),
                   CouponScheduleEntry(coup2_date, None, coup2_start_date, 100.0, 0.0)]
        amortizations = [AmortizationScheduleEntry(coup2_date, 100.0, 1000.0)]
        b = Bond(coupons=coupons, amortizations=amortizations)
        # assume we buy slightly after last coupon period start
        buy_price = 103.0
        buy_date = date(2018, 5, 20)
        ytm = b.yield_to_maturity(buy_price, buy_date, date(2018, 5, 21))
        assert ytm == pytest.approx(0.0821)
        # assert ytm definition holds
        yf = ((coup2_date - buy_date).days / YEAR_BASE)
        accrued = b.accrued_interest_on_date(buy_date)
        fv_initial_investment = (buy_price * 10 + accrued) * (1.0 + ytm) ** yf
        # no need to accrue since they already are on correct date
        fv_payments = coupons[1].value + amortizations[0].value
        assert fv_initial_investment == pytest.approx(fv_payments, rel=5E-5)


class TestOHLC:
    def test_cannot_create_ohlc_with_low_greater_than_high(self):
        with pytest.raises(ValueError):
            OHLC(date.today(), open=10.0, low=9.0, high=8.0, close=11.0, num_trades=1, volume=10.0, waprice=9.0)

    def test_cannot_create_ohlc_with_open_not_within_high_and_low(self):
        with pytest.raises(ValueError):
            OHLC(date.today(), open=8.9, low=9.0, high=15.0, close=11.0, num_trades=1, volume=10.0, waprice=9.0)

    def test_cannot_create_ohlc_with_close_not_within_high_and_low(self):
        with pytest.raises(ValueError):
            OHLC(date.today(), open=10.0, low=9.0, high=15.0, close=16.0, num_trades=1, volume=10.0, waprice=9.0)

    def test_can_create_ohlc_with_waprice_not_within_high_and_low(self):
        OHLC(date.today(), open=9.0, low=9.0, high=15.0, close=11.0, num_trades=1, volume=10.0, waprice=90.0)

    def test_csv_parse_unparse(self):
        ohlc = OHLC(date.today(), open=9.0, low=9.0, high=15.0, close=11.0, num_trades=1, volume=10.0, waprice=12.0)
        csv_row = ohlc.to_csv_row()
        ohlc_parsed = OHLC.from_csv_row(csv_row)
        assert ohlc == ohlc_parsed


class TestOHLCSeries:
    def test_can_create_empty_series(self):
        series = OHLCSeries("instr", [])
        assert series.is_empty()

    def test_cannot_create_with_dates_not_ascending(self):
        with pytest.raises(ValueError):
            OHLCSeries("instr", [
                OHLC(date(2020, 12, 5), open=9.0, low=9.0, high=15.0, close=11.0, num_trades=1, volume=10.0,
                     waprice=13.0),
                OHLC(date(2020, 12, 4), open=9.0, low=9.0, high=15.0, close=11.0, num_trades=1, volume=10.0,
                     waprice=13.0)
            ])

    def test_can_create_good_instance(self):
        series = OHLCSeries("instr", [
            OHLC(date(2020, 12, 4), open=9.0, low=9.0, high=15.0, close=11.0, num_trades=1, volume=10.0, waprice=13.0),
            OHLC(date(2020, 12, 5), open=9.0, low=9.0, high=15.0, close=11.0, num_trades=1, volume=10.0, waprice=13.0),
        ])
        assert not series.is_empty()

    def test_cannot_append_non_adjacent_series(self):
        with pytest.raises(ValueError) as e:
            series1 = OHLCSeries("instr", [
                OHLC(date(2020, 12, 4), open=9.0, low=9.0, high=15.0, close=11.0, num_trades=1, volume=10.0,
                     waprice=13.0),
                OHLC(date(2020, 12, 5), open=9.0, low=9.0, high=15.0, close=11.0, num_trades=1, volume=10.0,
                     waprice=13.0),
            ])
            series2 = OHLCSeries("instr", [
                OHLC(date(2020, 12, 5), open=9.0, low=9.0, high=15.0, close=11.0, num_trades=1, volume=10.0,
                     waprice=13.0),
                OHLC(date(2020, 12, 6), open=9.0, low=9.0, high=15.0, close=11.0, num_trades=1, volume=10.0,
                     waprice=13.0),
            ])
            series1.append(series2)
        assert "First date (2020-12-05) must be > 2020-12-05" in str(e.value)

    def test_cannot_join_series_of_different_instruments(self):
        with pytest.raises(ValueError) as e:
            series1 = OHLCSeries("instr1", [])
            series2 = OHLCSeries("instr2", [])
            series1.append(series2)
        assert "Instruments do not match" in str(e.value)

    def test_can_grow_series(self):
        series1 = OHLCSeries("instr", [
            OHLC(date(2020, 12, 4), open=9.0, low=9.0, high=15.0, close=11.0, num_trades=1, volume=10.0, waprice=13.0),
            OHLC(date(2020, 12, 5), open=9.0, low=9.0, high=15.0, close=11.0, num_trades=1, volume=10.0, waprice=13.0),
        ])
        series2 = OHLCSeries("instr", [
            OHLC(date(2020, 12, 6), open=9.0, low=9.0, high=15.0, close=11.0, num_trades=1, volume=10.0, waprice=13.0),
            OHLC(date(2020, 12, 7), open=9.0, low=9.0, high=15.0, close=11.0, num_trades=1, volume=10.0, waprice=13.0),
        ])
        series1.append(series2)
        assert len(series1.ohlc_series) == 4
        assert series1.ohlc_series[2:] == series2.ohlc_series

    def test_empty_processing(self):
        s1 = OHLCSeries("i", [])
        s1.append(OHLCSeries("i", []))
        assert s1.is_empty()

        s_tmp = OHLCSeries("i", [
            OHLC(date(2020, 12, 6), open=9.0, low=9.0, high=15.0, close=11.0, num_trades=1, volume=10.0, waprice=13.0)])
        s2 = OHLCSeries("i", [])
        s2.append(s_tmp)
        assert not s2.is_empty()
        assert s2 == s_tmp

        s_tmp.append(OHLCSeries("i", []))
        assert not s_tmp.is_empty()

    def test_avg_of_last_elems(self):
        close = [1.0, 2.0, 3.0, 4.0]
        open = [2.0, 3.0, 4.0, 5.0]
        ohlcs = [OHLC(date(2021, 1, 1) + timedelta(days=i), open=open[i], low=0.0, high=10.0,
                      close=close[i], num_trades=1, volume=1.0, waprice=1.0)
                 for i in range(len(close))]
        series = OHLCSeries("i", ohlcs)
        assert series.mean_of_last_elems(2) == (close[-2] + close[-1]) / 2
        assert series.mean_of_last_elems(2, lambda ohlc: ohlc.open) == (open[-2] + open[-1]) / 2
        assert series.mean_of_last_elems(4) == (close[0] + close[1] + close[2] + close[3]) / 4
        with pytest.raises(ValueError):
            series.mean_of_last_elems(5)


class TestCashFlows:
    def test_irr_no_outflows_or_inflows(self):
        with pytest.raises(ValueError):
            CashFlows([
                CashFlow(date(2021, 3, 20), 100.0),
                CashFlow(date(2022, 3, 20), 100.0)
            ])
        with pytest.raises(ValueError):
            CashFlows([
                CashFlow(date(2021, 3, 20), -100.0),
                CashFlow(date(2022, 3, 20), -100.0)
            ])

    def test_irr_degenerate(self):
        flows = CashFlows([
            CashFlow(date(2021, 3, 20), -100.0),
            CashFlow(date(2022, 3, 20), 100.0)
        ])
        assert flows.irr() == 0.0

    def test_irr_simple(self):
        flows = CashFlows([
            CashFlow(date(2021, 3, 20), -100.0),
            CashFlow(date(2022, 3, 20), 110.0)
        ])
        assert flows.irr() == pytest.approx(0.1)

    def test_irr_means_yearly_compounded(self):
        flows = CashFlows([
            CashFlow(date(2021, 3, 20), -100.0),
            # irr internally uses yearly compounded discounting so such flow should lead to irr 10%
            CashFlow(date(2023, 3, 20), 100.0 * (1 + 0.1) ** 2)
        ])
        assert flows.irr() == pytest.approx(0.1)
        flows = CashFlows([
            CashFlow(date(2021, 3, 20), -100.0),
            # while if we receive dividends in simple compounding, it would lead to lower effective irr
            CashFlow(date(2023, 3, 20), 100.0 * (1 + 2 * 0.1))
        ])
        assert flows.irr() == pytest.approx(0.0954451)
