### Hoping to solve pre/post/regular market hours alerts on big stock moves(indicating a catalyst has occured).


Script will use yahoo api via lib, and SMS texts from a email account via a lib to help keep you in the know for when a big move happens(FDA Approval/Deny, Data good/bad for biotech plays). I personally keep my phone on silent during the day and have overriden that setting when these texts from my emails come to vibrate and alarm like crazy so i won't miss the catalyst(good or bad news)!

1. Compare premarket/postmarket/regular % change >= .10 (10%) movement either way means we need to alert. This can be configurable.
2. Alert will SMS text you the ticker and current seen price and the % moveement it saw at the time of alert/query.

This code should just run in the background on your computer making the checks.

<b>Note, for text from gmail functionality to work, enable the "allow less secure apps" login feature tied to your gmail account.</b> Maybe use a spare gmail if you have security concerns.</b>

<b>NOTE: I have updated the default mailserver to be for hotmail/live/outlook Microsoft owned emails due to Verizons rate limits placed on GMAIL SMTP servers.</b>

How to run the app:
```bash
python main.py \
  --stock_symbols MSFT AAPL GOOG \
  --percent_change_threshold .10 \
  --sender_email your_email@example.com \
  --sender_email_host smtp.office365.com \
  --sender_email_host_port 587 \
  --sender_email_password password \
  --carrier verizon_pics \
  --phone_numbers 0000000000 1111111111 \
  --yahoo_api_key apikeyhere
```

Or better yet just fork this repo and setup proper github secrets and the github action ```cron.yml``` included in this repo will run Market hours 4am EST to 8pm EST(pre and regular and post market) to alert
you on any stock moves. Secrets look like so when setup:

![image](https://github.com/user-attachments/assets/babe896d-7121-4d88-8e02-e1ec98140f8a)



<br />
<br />
<br />
<b>Tools needed</b><br /><br />
I recommend running the app if on windows using pycharm, can get free community edition: https://www.jetbrains.com/pycharm/<br />
Also get latest version of python on your machine: https://www.python.org/downloads/release/python-396/

<br />
Notes on Yahoo API Rate limit limitations:

> Rate Limitation
> There’re some limitations by making the call to Yahoo Finance API:
> Using the Public API (without authentication), you are limited to 2,000 requests per hour per IP (or up to a total of 48,000 requests a day).
> I’m not sure it’s precisely for Financial data. But please use time.sleep(1) to avoid your IP getting blocked.
> If you authenticate with API Key then you get 100,000 requests a day


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

<br />
Credits of external scripts and libs I used: <br />
https://github.com/dpguthrie/yahooquery<br />
https://github.com/acamso/demos/blob/master/_email/send_txt_msg.py<br />
