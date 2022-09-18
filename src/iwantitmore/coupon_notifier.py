import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.utils import formatdate
from email import encoders
import datetime
import argparse
from typing import List

from icalendar import Calendar, Event, Alarm

from iwantitmore.moex import load_bond
from iwantitmore.instruments import CouponScheduleEntry, AmortizationScheduleEntry, Bond


def generate_event(subject: str, payment_date: datetime.date) -> Event:
    evt = Event()
    evt.add("summary", subject)
    notification_time = datetime.time(11, 0)
    payment_datetime = datetime.datetime.combine(payment_date, notification_time)
    evt.add("dtstart", payment_datetime)
    evt.add("dtend", payment_datetime + datetime.timedelta(hours=1))
    evt.add("dtstamp", datetime.datetime.now())
    alarm = Alarm()
    alarm.add("trigger", datetime.timedelta(minutes=0))
    evt.add_component(alarm)
    return evt


def ccy_to_char(ccy: str) -> str:
    """converts well-known iso ccy codes (RUB, USD, ...) to one-symbol chars ($, ...)"""
    if ccy == "RUB":
        return "\u20BD"
    elif ccy == "USD":
        return "$"
    elif ccy == "EUR":
        return "\u20AC"
    else:
        return ccy


def generate_calendar(bond: Bond,
                      coupons: List[CouponScheduleEntry],
                      amorts: List[AmortizationScheduleEntry]) -> Calendar:
    """returns tuple with first element a calendar with events of payments for a bond
    and second element as a textual description"""
    cal = Calendar()
    cal.add("prodid", "-//Coupon notifier")
    cal.add("version", "2.0")

    ccy_char = ccy_to_char(bond.notional_ccy)
    for coupon in coupons:
        event = generate_event(f'"{bond.name}" coupon of {coupon.value}{ccy_char} ({bond.isin})',
                               coupon.coupon_date)
        cal.add_component(event)

    for amort in amorts:
        event = generate_event(f'"{bond.name}" notional repayment of {amort.value}{ccy_char} ({bond.isin})',
                               amort.amort_date)
        cal.add_component(event)
    return cal


def send_to_email(cal: Calendar, bond: Bond, from_addr: str, to_addr: str) -> None:
    eml_body = "Please accept the invite to get expected coupons and notional repayment schedule in your calendar"
    msg = MIMEMultipart('mixed')
    msg['Reply-To'] = from_addr
    msg['Date'] = formatdate(localtime=True)
    msg['Subject'] = f"Schedule for {bond.name}"
    msg['From'] = from_addr
    msg['To'] = to_addr

    ical = cal.to_ical()
    sical = str(ical, encoding="utf-8")

    part_email = MIMEText(eml_body, "html")
    part_cal = MIMEText(sical, 'calendar;method=REQUEST')

    msg_alternative = MIMEMultipart('alternative')
    msg.attach(msg_alternative)

    ical_atch = MIMEBase('application/ics', ' ;name="%s"' % "invite.ics")
    ical_atch.set_payload(sical)
    encoders.encode_base64(ical_atch)
    ical_atch.add_header('Content-Disposition', 'attachment; filename="%s"' % "invite.ics")

    eml_atch = MIMEText('', 'plain')
    encoders.encode_base64(eml_atch)
    eml_atch.add_header('Content-Transfer-Encoding', "")

    msg_alternative.attach(part_email)
    msg_alternative.attach(part_cal)

#TODO: take from env
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
    mail_server.sendmail(from_addr, [to_addr], msg.as_string())
    mail_server.close()


def send_payment_schedule_invites(isins: List[str], to_email: str):
    for isin in isins:
        print(f"Processing {isin}")
        bond = load_bond(isin)
        coupons, amorts = bond.payments_since_date(datetime.date.today())
        cal = generate_calendar(bond, coupons, amorts)
        print("Sending email")
        send_to_email(cal, bond, to_email, to_email)
    print("Done")


def main():
    parser = argparse.ArgumentParser(
        description="Downloads schedule for specified bonds from Moscow Exchange (MOEX) "
                    "and sends meeting invites to specified e-mail that correspond to "
                    "bond's coupons and notional amortizations",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
# TODO: add email server address
    parser.add_argument("--email", required=True, help="E-mail address to which send invites")
    parser.add_argument("isins", help="ISIN codes of bonds on MOEX", nargs="+", metavar="ISIN")
    args = parser.parse_args()
    email = args.email
    send_payment_schedule_invites(args.isins, email)


if __name__ == "__main__":
    main()
