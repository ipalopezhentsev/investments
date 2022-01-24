import argparse
import logging
from typing import List

from iwantitmore import moex

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="Downloads EOD OLHC data from Moscow Exchange (MOEX) "
                    "and saves to CSV files",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
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

    args = parser.parse_args()
    fx_codes = args.fx_codes
    bond_codes = args.bond_codes
    share_codes = args.share_codes
    if fx_codes is None and bond_codes is None and share_codes is None:
        raise ValueError("At least one of --fx-codes, --bond-codes, --share-codes must be specified")
    instrums: List[moex.Instrument] = []
    if fx_codes is not None:
        instrums.extend([moex.FXInstrument(secid) for secid in fx_codes])
    if bond_codes is not None:
        instrums.extend([moex.BondInstrument(isin) for isin in bond_codes])
    if share_codes is not None:
        instrums.extend([moex.ShareInstrument(secid) for secid in share_codes])

    for instr in instrums:
        logger.info(f"Loading OHLC data for {instr}")
        olhc_series = instr.load_ohlc_table(from_date=None)
        olhc_series.save_to_csv(f"data/{instr.code}.csv")


if __name__ == "__main__":
    main()
