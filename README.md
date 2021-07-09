### Hoping to solve pre/post/regular market hours alerts on big stock moves(indicating a catalyst has occured).


Script will use yahoo api via lib, and SMS texts from a GMAIL account via a lib to help keep you in the know for when a big move happens(FDA Approval/Deny, Data good/bad for biotech plays). I personally keep my phone on silent during the day and have overriden that setting when these texts from my emails come to vibrate and alarm like crazy so i won't miss the catalyst(good or bad news)!

1. Compare premarket/postmarket/regular % change >= .10 (10%) movement either way means we need to alert. This can be configurable.
2. Alert will SMS text you the ticker and current seen price and the % moveement it saw at the time of alert/query.

This code should just run in the background on your computer making the checks.

<b>Note, for text from gmail functionality to work, enable the "allow less secure apps" login feature tied to your gmail account.</b> Maybe use a spare gmail if you have security concerns.

<b>Configurable fields within the main.py</b><br /><br />
SMS + GMAIL Details:

>    _num = "XXXXXXXXXX"<br />
    _carrier = "verizon"<br />
    _email = "XXXXXXX@gmail.com"<br />
    _password = "XXXXXXX"<br />

Your stocks to watch(5 maximum to prevent IP ban by yahoo):
> tickerList

Alert threshold % in decimal format(Currently defaulted to 10%):
> alert_threshold_percent


<br />
<br />
<br />
Notes on Yahoo API Rate limit limitations:

> Rate Limitation
> There’re some limitations by making the call to Yahoo Finance API:
> Using the Public API (without authentication), you are limited to 2,000 requests per hour per IP (or up to a total of 48,000 requests a day).
> I’m not sure it’s precisely for Financial data. But please use time.sleep(1) to avoid your IP getting blocked.


Sample data the yahoo api gives us(ardx.price):
```
   {
     'ardx': {
     'maxAge': 1, 
     'preMarketChangePercent': -0.0012641911, 
     'preMarketChange': -0.009999752, 
     'preMarketTime': '2021-07-08 04:28:52', 
     'preMarketPrice': 7.9, 
     'preMarketSource': 'FREE_REALTIME', 
     'postMarketChangePercent': 0.0012642514, 
     'postMarketChange': 0.010000229, 
     'postMarketTime': 1625700530, 
     'postMarketPrice': 7.92, 
     'postMarketSource': 'DELAYED', 
     'regularMarketChangePercent': -0.031823773, 
     'regularMarketChange': -0.26000023, 
     'regularMarketTime': '2021-07-07 16:00:03', 
     'priceHint': 2, 
     'regularMarketPrice': 7.91, 
     'regularMarketDayHigh': 8.23, 
     'regularMarketDayLow': 7.865, 
     'regularMarketVolume': 1630126, 
     'regularMarketPreviousClose': 8.17, 
     'regularMarketSource': 'FREE_REALTIME', 
     'regularMarketOpen': 8.17, 
     'exchange': 'NMS', 
     'exchangeName': 'NasdaqGS', 
     'exchangeDataDelayedBy': 0, 
     'marketState': 'PRE', 
     'quoteType': 'EQUITY', 
     'symbol': 'ARDX', 
     'underlyingSymbol': None, 
     'shortName': 'Ardelyx, Inc.', 
     'longName': 'Ardelyx, Inc.', 
     'currency': 'USD', 
     'quoteSourceName': 'Nasdaq Real Time Price', 
     'currencySymbol': '$', 
     'fromCurrency': None, 
     'toCurrency': None, 
     'lastMarket': None, 
     'marketCap': 786726976
     }
   }
```
