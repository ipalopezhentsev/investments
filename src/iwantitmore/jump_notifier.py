import os
import sys
import signal
import argparse
import smtplib
from dataclasses import dataclass
from email.mime.text import MIMEText
import sched
from typing import List, Dict, Optional, Set, Tuple
from enum import Enum

from iwantitmore import moex, instruments
import logging
import datetime
from iwantitmore.utils import MovingAvgCalculator

logger = logging.getLogger(__name__)


def instrument_to_filename(instr: moex.Instrument):
    return f"data/{instr.code}.csv"


def get_initial_series(instrums: List[moex.Instrument]) -> Dict[moex.Instrument, instruments.OHLCSeries]:
    res = {}
    for instrument in instrums:
        try:
            fname = instrument_to_filename(instrument)
            if os.path.exists(fname):
                series = instruments.OHLCSeries.load_from_csv(fname)
            else:
                series = instruments.OHLCSeries(instrument.code, [], name=None)
        except Exception as e:
            logger.error(f"Error loading initial data for {instrument}", exc_info=e, stack_info=True)
            # give it chance to load from http
            series = instruments.OHLCSeries(instrument.code, [], name=None)
        res[instrument] = series
    return res


def refresh_series(cur_series: Dict[moex.Instrument, instruments.OHLCSeries]) \
        -> (Dict[moex.Instrument, instruments.OHLCSeries], Dict[moex.Instrument, Exception]):
    """gets new entries into series from web. Returns dict of instrument to loading error"""
    good_series = {}
    errors = {}
    for instr, series in cur_series.items():
        try:
            instr.update_ohlc_table(series)
            good_series[instr] = series
        except Exception as ex:
            errors[instr] = ex
    return good_series, errors


def send_mail(email_address: str, msg_header: str, msg_text: str) -> None:
    msg = MIMEText(msg_text)
    msg['Subject'] = msg_header
    msg['From'] = "jump-notifier"
    # msg['From'] = email_address
    msg['To'] = email_address
    #TODO: put to env args
    mail_server = smtplib.SMTP('localhost')

    # {
    # mail_server = smtplib.SMTP('smtp.mail.ru', 587)
    # mail_server.ehlo()
    # mail_server.starttls()
    # mail_server.ehlo()
    # login = ""
    # password = ""
    # mail_server.login(login, password)
    # }

    mail_server.sendmail(msg['From'], [email_address], msg.as_string())
    mail_server.quit()


def get_mail_text_triggered(instr: moex.Instrument, sig: str) -> (str, str):
    header = f"WARN: Jump found for {instr}"
    msg = f"Timestamp: {datetime.datetime.now()}\n"
    msg += "Triggered instrument:\n"
    msg += f"{instr}: {sig}\n"
    return header, msg


def get_mail_text_not_triggered(not_triggered_instruments: Dict[moex.Instrument, str]) -> (str, str):
    header = "INFO: Not triggered instruments"
    msg = f"Timestamp: {datetime.datetime.now()}\n"
    for instr, txt in not_triggered_instruments.items():
        msg += f"{instr}: {txt}\n"
    return header, msg


@dataclass
class IntradayState:
    time_of_last_trade: Optional[datetime.time]
    moving_avg: MovingAvgCalculator
    time_last_save: Optional[datetime.datetime]
    time_last_email_sent: Optional[datetime.datetime]


class Outcome(Enum):
    TRIGGERED = 1
    NOT_TRIGGERED_DUE_TO_THRESHOLD = 2
    NOT_TRIGGERED_DUE_TO_NOT_READY = 3


class Ticker:
    def __init__(self, initial_series: Dict[moex.Instrument, instruments.OHLCSeries],
                 email: str, hist_window_size: int, intraday_window_size: int, num_std_devs_thresh: float,
                 scheduler: sched.scheduler, ticks_freq: int, saving_freq: datetime.timedelta):
        self.cur_series = initial_series
        self.email = email
        self.hist_window_size = hist_window_size
        self.intraday_window_size = intraday_window_size
        self.num_std_devs_thresh = num_std_devs_thresh
        self.scheduler = scheduler
        self.ticks_freq = ticks_freq
        self.saving_freq = saving_freq
        self.intraday_states: Dict[moex.Instrument, IntradayState] = {}
        self.time_last_sent_not_triggered: Optional[datetime.datetime] = None

    def tick(self, /, *args, **kwargs):
        now = datetime.datetime.now()
        logger.info("Start tick")
        try:
            not_triggered: Dict[moex.Instrument, str] = {}
            for instr, series in self.cur_series.items():
                try:
                    if instr in self.intraday_states:
                        intraday_state = self.intraday_states[instr]
                    else:
                        intraday_state = IntradayState(None,
                                                       MovingAvgCalculator(
                                                           self.intraday_window_size), None, None)
                    self.intraday_states[instr] = intraday_state
                    outcome, msg = self.get_triggered_signals(instr, series, intraday_state)
                    day_changed_since_last_notification = intraday_state.time_last_email_sent is None or \
                                                          intraday_state.time_last_email_sent.date() != now.date()
                    logger.info(f"Outcome: {outcome}")
                    if outcome == Outcome.TRIGGERED:
                        if day_changed_since_last_notification:
                            (header, msg) = get_mail_text_triggered(instr, msg)
                            logger.info(header)
                            logger.info(msg)
                            send_mail(self.email, header, msg)
                            intraday_state.time_last_email_sent = now
                        else:
                            logger.info(f"Triggered but skip sending email for {instr} as already sent today.")
                    elif outcome == Outcome.NOT_TRIGGERED_DUE_TO_THRESHOLD:
                        not_triggered[instr] = msg
                    # else - we ignore NOT_TRIGGERED_DUE_TO_NOT_READY to not send 1 mail with useless not ready info

                    self.save_series(instr, series, intraday_state, now)
                except Exception as ex:
                    logger.error(f"Error happened for {instr}", exc_info=ex, stack_info=True)

            day_changed_since_last_not_triggered_notification = self.time_last_sent_not_triggered is None or \
                                                                self.time_last_sent_not_triggered.date() != now.date()
            if len(not_triggered) != 0:
                if day_changed_since_last_not_triggered_notification:
                    (header, msg) = get_mail_text_not_triggered(not_triggered)
                    logger.info(header)
                    logger.info(msg)
                    send_mail(self.email, header, msg)
                    self.time_last_sent_not_triggered = now
                else:
                    logger.info("Skipping sending not triggered instruments as already sent today")
            else:
                logger.info("Skipping sending not triggered instruments as all of them were not ready")
        except Exception as ex:
            logger.error("Error happened on tick", exc_info=ex, stack_info=True)
        finally:
            self.scheduler.enter(self.ticks_freq, 1, self.tick, argument=(self,))
            logger.info("End tick")

    def get_triggered_signals(self, instr: moex.Instrument, ser: instruments.OHLCSeries,
                              intraday_state: IntradayState) -> (Outcome, str):
        """returns tuple whose first element is true if triggered and second is message with details"""
        # TODO: for bonds: add notification its price gets < 100.
        try:
            instr.update_ohlc_table(ser)
            quote = instr.load_intraday_quotes()
            if not quote.is_trading:
                return Outcome.NOT_TRIGGERED_DUE_TO_NOT_READY, f"Skipping {instr} as it's not currently trading"
            time_of_last_trade = quote.time
            if intraday_state.time_of_last_trade is not None and \
                    time_of_last_trade < intraday_state.time_of_last_trade:
                # trading day switched
                logger.info(f"Trading day switched for {instr}, resetting intraday averager")
                intraday_state.moving_avg = MovingAvgCalculator(self.intraday_window_size)
            intraday_state.time_of_last_trade = time_of_last_trade
            # TODO: add only if time of last trade differs, to track only deals, not ticks
            intraday_state.moving_avg.add(quote.last)

            hist_mean = ser.mean_of_last_elems(self.hist_window_size)
            std_dev = ser.std_dev_of_last_elems(self.hist_window_size, mean=hist_mean)
            intra_mean = intraday_state.moving_avg.avg()
            if intra_mean is None:
                return Outcome.NOT_TRIGGERED_DUE_TO_NOT_READY, f"Skipping {instr} as it hasn't accumulated " \
                                                               f"{self.intraday_window_size} intraday quotes yet"
            rel_diff = (intra_mean - hist_mean) / max(intra_mean, hist_mean)
            nstd_devs = self.num_std_devs_thresh * std_dev
            observed_num_std_devs_jump = abs(intra_mean - hist_mean) / std_dev
            if intra_mean > hist_mean + nstd_devs:
                return Outcome.TRIGGERED, f"Jump UP {round(rel_diff * 100.0, 2)}% to {round(intra_mean, 2)} " \
                                          f"({self.intraday_window_size} tick avg) " \
                                          f"from average of {round(hist_mean, 2)} of last {self.hist_window_size} days " \
                                          f"(jump of {round(observed_num_std_devs_jump, 2)} std. devs from mean)"
            elif intra_mean < hist_mean - nstd_devs:
                return Outcome.TRIGGERED, f"Jump DOWN {round(rel_diff * 100.0, 2)}% to {round(intra_mean, 2)} " \
                                          f"({self.intraday_window_size} tick avg) " \
                                          f"from average of {round(hist_mean, 2)} of last {self.hist_window_size} days " \
                                          f"(jump of {round(observed_num_std_devs_jump, 2)} std. devs from mean)"
            else:
                return Outcome.NOT_TRIGGERED_DUE_TO_THRESHOLD, f"{round(intra_mean, 2)} ({self.intraday_window_size} tick avg) is " \
                        f"{round(rel_diff * 100.0, 2)}% jump over last " \
                        f"{self.hist_window_size} days average of {round(hist_mean, 2)} " \
                        f"(jump of {round(observed_num_std_devs_jump, 2)} std. devs from mean)"
        except Exception as ex:
            logger.error(f"Error happened checking {instr}", exc_info=ex, stack_info=True)
            return True, f"Could not check: {ex}"

    def save_series(self, instr: moex.Instrument, ser: instruments.OHLCSeries,
                    intraday_state: IntradayState, now: datetime.datetime):
        if intraday_state.time_last_save is None or now - intraday_state.time_last_save > self.saving_freq:
            logger.info(f"Saving series for {instr}")
            try:
                fname = instrument_to_filename(instr)
                ser.save_to_csv(fname)
                logger.info("Done saving")
                intraday_state.time_last_save = now
            except Exception as ex:
                logger.error(f"Failed to save series for {instr}", exc_info=ex, stack_info=True)


def main():
    parser = argparse.ArgumentParser(
        description="Sends mail if avg of intraday quotes for specified instruments deviate "
                    "significantly from previous days average",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--email", default="root", help="E-mail address to which send notifications")
    parser.add_argument("--hist-window", type=int, default=5,
                        help="Number of business days to calculate historical average")
    parser.add_argument("--intra-window", type=int, default=3,
                        help="Number of ticks (see --ticks-freq-seconds) to calculate intraday average")
    parser.add_argument("--ticks-freq-seconds", type=int, default=60,
                        help="Check intraday rates with this frequency, in seconds")
    parser.add_argument("--saving-freq-hours", type=int, default=24,
                        help="Save collected EOD rates to disk with this frequency, in hours")
    parser.add_argument("--num-std-devs-thresh", type=float, default=2.0,
                        help="Minimum number of standard deviations intraday rate should jump from the mean to "
                             "send warning email")
    parser.add_argument("--fx-codes",
                        help="Security ID's of FX instruments on MOEX (e.g. EUR_RUB__TOM USD000UTSTOM). "
                             "To get it, check "
                             "https://iss.moex.com/iss/history/engines/currency/markets/selt/boards/cets/securities.csv"
                             " - you need SECID column", nargs="+", metavar="FX_CODE")
    parser.add_argument("--bond-codes",
                        help="ISIN's of bond instruments on MOEX (e.g. RU000A100YG1). ",
                        nargs="+", metavar="BOND_CODE")
    parser.add_argument("--share-codes",
                        help="Security ID's (not ISIN's!) of share instruments on MOEX (e.g. SBMX). ",
                        nargs="+", metavar="SHARE_CODE")
    parser.add_argument("--index-codes",
                        help="Security ID's of indexes on MOEX (e.g. MREDC).",
                        nargs="+", metavar="INDEX_CODE")

    args = parser.parse_args()
    email = args.email
    fx_codes = args.fx_codes
    bond_codes = args.bond_codes
    share_codes = args.share_codes
    index_codes = args.index_codes
    if fx_codes is None and bond_codes is None and share_codes is None and index_codes is None:
        raise ValueError("At least one of --fx-codes, --bond-codes, --share-codes, --index-codes must be specified")
    instrums: List[moex.Instrument] = []
    if fx_codes is not None:
        instrums.extend([moex.FXInstrument(secid) for secid in fx_codes])
    if bond_codes is not None:
        instrums.extend([moex.BondInstrument(isin) for isin in bond_codes])
    if share_codes is not None:
        instrums.extend([moex.ShareInstrument(secid) for secid in share_codes])
    if index_codes is not None:
        instrums.extend([moex.IndexInstrument(secid) for secid in index_codes])
    hist_window_size = args.hist_window
    intraday_window_size = args.intra_window
    num_std_devs_thresh = args.num_std_devs_thresh
    ticks_freq = args.ticks_freq_seconds
    saving_freq = datetime.timedelta(hours=args.saving_freq_hours)
    logger.info(f"PID {os.getpid()}")

    s = sched.scheduler()
    initial_series = get_initial_series(instrums)
    ticker = Ticker(initial_series, email, hist_window_size, intraday_window_size,
                    num_std_devs_thresh, s, ticks_freq, saving_freq)
    s.enter(delay=0, priority=1, action=ticker.tick, argument=(ticker,))

    def on_sigterm(sig, stack):
        logger.info(f"Received SIGTERM ({sig}), shutting down\n{stack}")
        # TODO: save series? but should not wait for next tick which can be too far away
        exit(0)

    signal.signal(signal.SIGTERM, on_sigterm)

    s.run()


def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logger.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))


if __name__ == "__main__":
    sys.excepthook = handle_exception
    main()
