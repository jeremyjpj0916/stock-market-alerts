import classes
from datetime import datetime
import datetime as dt
import time
from pytz import timezone
import asyncio
import re
from email.message import EmailMessage
from typing import Tuple, Union
from yahooquery import Ticker
import aiosmtplib

# Email script from https://github.com/acamso/demos/blob/master/_email/send_txt_msg.py
#HOST = "smtp.gmail.com"
HOST = "smtp.office365.com"

CARRIER_MAP = {
    "verizon": "vtext.com",
    "tmobile": "tmomail.net",
    "sprint": "messaging.sprintpcs.com",
    "at&t": "txt.att.net",
    "boost": "smsmyboostmobile.com",
    "cricket": "sms.cricketwireless.net",
    "uscellular": "email.uscc.net",
}


def send_message(_subj, _msg):
    # Alert sending portion config
    _num_list = ["XXXXXXXXXX"]
    _carrier = "verizon"
    _email = "XXXXXXXX@live.com"
    _password = "XXXXXXXX"
    # Alert SMS
    for _num in _num_list:
        coro = send_txt(_num, _carrier, _email, _password, _msg, _subj)
        asyncio.run(coro)
        # Sleep 3 seconds between texts to diff numbers(unsure googles rate limit or protections for rapid texts).
        time.sleep(3)


# pylint: disable=too-many-arguments
async def send_txt(
        num: Union[str, int], carrier: str, email: str, password: str, msg: str, subj: str
) -> Tuple[dict, str]:
    to_email = CARRIER_MAP[carrier]

    # build message
    message = EmailMessage()
    message["From"] = email
    message["To"] = f"{num}@{to_email}"
    message["Subject"] = subj
    message.set_content(msg)

    # send
    send_kws = dict(username=email, password=password, hostname=HOST, port=587, start_tls=True)
    res = await aiosmtplib.send(message, **send_kws)  # type: ignore
    msg = "failed" if not re.search(r"\sOK\s", res[1]) else "succeeded"
    print(msg)
    return res


def is_now_in_time_period(start_time, end_time, now_time):
    if start_time < end_time:
        return start_time <= now_time <= end_time
    else:
        # Over midnight:
        return now_time >= start_time or now_time <= end_time


def evaluate_watch_list(watch_list, market_phase, change_percent_threshold):
    print(market_phase + " market analysis checks in progress")
    for company in watch_list:
        if not company.alerted_already:  # Have not alerted on ticker at runtime yet? Then check.
            company_stock = company.stock_ticker
            yahoo_stock_price_details = Ticker(company_stock).price
            print(yahoo_stock_price_details)
            if market_phase == "pre" and "preMarketChangePercent" in yahoo_stock_price_details[company_stock]:
                # Ex: -0.0012641911 / 0.0012641911
                pre_market_change_percent = yahoo_stock_price_details[company_stock]["preMarketChangePercent"]
                if pre_market_change_percent >= change_percent_threshold:
                    send_message(company_stock + " ALERT UP", company_stock + " Price: " + str(
                        yahoo_stock_price_details[company_stock]["preMarketPrice"]) + ", Change: " + str((100*round(pre_market_change_percent, 4))) + "%")
                    company.alerted_already = True
                elif pre_market_change_percent <= -change_percent_threshold:
                    send_message(company_stock + " ALERT DOWN", company_stock + " Price: " + str(
                        yahoo_stock_price_details[company_stock]["preMarketPrice"]) + ", Change: " + str((100*round(pre_market_change_percent, 4))) + "%")
                    company.alerted_already = True
            elif market_phase == "regular" and "regularMarketChangePercent" in yahoo_stock_price_details[company_stock]:
                # Ex: -0.0012641911 / 0.0012641911
                regular_market_change_percent = yahoo_stock_price_details[company_stock]["regularMarketChangePercent"]
                if regular_market_change_percent >= change_percent_threshold:
                    send_message(company_stock + " ALERT UP", company_stock + " Price: " + str(
                        yahoo_stock_price_details[company_stock]["regularMarketPrice"]) + ", Change: " + str((100*round(regular_market_change_percent, 4))) + "%")
                    company.alerted_already = True
                elif regular_market_change_percent <= -change_percent_threshold:
                    send_message(company_stock + " ALERT DOWN", company_stock + " Price: " + str(
                        yahoo_stock_price_details[company_stock]["regularMarketPrice"]) + ", Change: " + str((100*round(regular_market_change_percent, 4))) + "%")
                    company.alerted_already = True
            elif market_phase == "post" and "postMarketChangePercent" in yahoo_stock_price_details[company_stock]:
                # Ex: -0.0012641911 / 0.0012641911
                post_market_change_percent = yahoo_stock_price_details[company_stock]["postMarketChangePercent"]
                if post_market_change_percent >= change_percent_threshold:
                    send_message(company_stock + " ALERT UP", company_stock + " Price: " + str(
                        yahoo_stock_price_details[company_stock]["postMarketPrice"]) + ", Change: " + str((100*round(post_market_change_percent, 4))) + "%")
                    company.alerted_already = True
                elif post_market_change_percent <= -change_percent_threshold:
                    send_message(company_stock + " ALERT DOWN", company_stock + " Price: " + str(
                        yahoo_stock_price_details[company_stock]["postMarketPrice"]) + ", Change: " + str((100*round(post_market_change_percent, 4))) + "%")
                    company.alerted_already = True


if __name__ == '__main__':
    # define eastern timezone (will be working in EST timezone w the US stock market data)
    eastern = timezone('US/Eastern')

    # Make your stock ticker list. Max limit 5 or else we will truncate to first 5(this is to protect you from
    # getting IP banned for too many queries)
    tickerList = ["ardx", "infi"]

    # Alert threshold % (.10 for 10% up/down movement, .05 for 5% up/down movement etc.)
    alert_threshold_percent = .10

    if len(tickerList) > 5:
        print("**Ticker list > 5 detected, taking first 5 tickers to monitor! Printing the tickers monitored below:**")
        del tickerList[5:]
        # printing shortened list
        print("**The truncated ticker list is : " + str(tickerList) + "**")

    # We build out watchlist at start of program execution.
    watchList = []
    for stock in tickerList:
        tempTicker = classes.Stock(stock, False)  # Set that we have not alerted yet to false. Will be made true
        # if we alert on it (to prevent spamming alerts constantly when it reaches our jump % threshold).
        watchList.append(tempTicker)

    # Infinite execution loop checker on our stock
    while True:
        # est datetime
        est_dt = datetime.now(eastern).time()
        weekday = datetime.now(eastern).today().weekday()  # 5 Monday is 0 and Sunday is 6.

        # Do nothing from 8pm EST to 4am EST.
        if is_now_in_time_period(dt.time(20, 00), dt.time(4, 00), est_dt) or (weekday >= 5):
            if weekday >= 5:
                print("**Do nothing on closed market days saturday and sunday weekend**")
            else:
                print("**Do nothing between 8PM EST and 4AM EST market hours**")
            time.sleep(60)  # Sleep 1 minute checks when in the quiet period
            continue
        elif is_now_in_time_period(dt.time(4, 00), dt.time(9, 30), est_dt):  # Premarket analysis
            evaluate_watch_list(watchList, "pre", alert_threshold_percent)
        elif is_now_in_time_period(dt.time(9, 30), dt.time(16, 00), est_dt):  # Regular Market analysis
            evaluate_watch_list(watchList, "regular", alert_threshold_percent)
        elif is_now_in_time_period(dt.time(16, 00), dt.time(9, 30), est_dt):  # Post Market analysis
            evaluate_watch_list(watchList, "post", alert_threshold_percent)

        # Sleep 10 seconds between active hour checks(pre/post/regular market hours)
        time.sleep(10)
