import axios from 'axios';
import { EmailMessage } from 'email-message';
import { simpleParser } from 'mailparser';
import { createTransport } from 'nodemailer';

// Define command-line arguments
const args = process.argv.slice(2);

const stockSymbols = args[0].split(',');
const percentChangeThreshold = parseFloat(args[1]);
const senderEmail = args[2];
const senderEmailHost = args[3];
const senderEmailHostPort = parseInt(args[4]);
const senderEmailPassword = args[5];
const carrier = args[6];
const phoneNumbers = args[7].split(',');
const yahooApiKey = args[8];

const CARRIER_MAP = {
  "verizon": "vtext.com",
  "verizon_pics": "vzwpix.com",
  "tmobile": "tmomail.net",
  "sprint": "messaging.sprintpcs.com",
  "at&t": "txt.att.net",
  "boost": "smsmyboostmobile.com",
  "cricket": "sms.cricketwireless.net",
  "uscellular": "email.uscc.net",
};

async function send_message(_subj, _msg) {
  const _num_list = phoneNumbers;
  const _carrier = carrier;
  const _email = senderEmail;
  const _password = senderEmailPassword;
  for (const _num of _num_list) {
    const to_email = CARRIER_MAP[_carrier];
    const message = new EmailMessage();
    message.from = _email;
    message.to = `${_num}@${to_email}`;
    message.subject = _subj;
    message.body = _msg;
    const transport = createTransport({
      host: senderEmailHost,
      port: senderEmailHostPort,
      secure: false, // or 'STARTTLS'
      auth: {
        user: _email,
        pass: _password,
      },
    });
    const res = await transport.sendMail(message);
    const msg = res.accepted ? 'succeeded' : 'failed';
    console.log(msg);
  }
}

async function send_txt(num, carrier, email, password, msg, subj) {
  const to_email = CARRIER_MAP[carrier];
  const message = new EmailMessage();
  message.from = email;
  message.to = `${num}@${to_email}`;
  message.subject = subj;
  message.body = msg;
  const transport = createTransport({
    host: senderEmailHost,
    port: senderEmailHostPort,
    secure: false, // or 'STARTTLS'
    auth: {
      user: email,
      pass: password,
    },
  });
  const res = await transport.sendMail(message);
  const msg = res.accepted ? 'succeeded' : 'failed';
  console.log(msg);
  return res;
}

function is_now_in_time_period(start_time, end_time, now_time) {
  if (start_time < end_time) {
    return start_time <= now_time && now_time <= end_time;
  }
  return now_time >= start_time || now_time <= end_time;
}

function evaluate_watch_list(watch_list, market_phase, change_percent_threshold) {
  console.log(`${market_phase} market analysis checks in progress`);
  for (const company of watch_list) {
    if (!company.alerted_already) {
      const company_stock = company.stock_ticker;
      const yahoo_stock_price_details = await axios.get(`https://query1.finance.yahoo.com/v7/finance/quote?symbols=${company_stock}&token=${yahooApiKey}`);
      const pre_market_change_percent = yahoo_stock_price_details.data.quote.summaryDetail.preMarketChangePercent;
      const regular_market_change_percent = yahoo_stock_price_details.data.quote.summaryDetail.regularMarketChangePercent;
      const post_market_change_percent = yahoo_stock_price_details.data.quote.summaryDetail.postMarketChangePercent;
      
      if (market_phase === "pre" && pre_market_change_percent >= change_percent_threshold) {
        send_message(`${company_stock} ALERT UP`, `${company_stock} Price: ${yahoo_stock_price_details.data.quote.summaryDetail.preMarketPrice}, Change: ${pre_market_change_percent}%`);
        company.alerted_already = true;
      } else if (market_phase === "pre" && pre_market_change_percent <= -change_percent_threshold) {
        send_message(`${company_stock} ALERT DOWN`, `${company_stock} Price: ${yahoo_stock_price_details.data.quote.summaryDetail.preMarketPrice}, Change: ${pre_market_change_percent}%`);
        company.alerted_already = true;
      } else if (market_phase === "regular" && regular_market_change_percent >= change_percent_threshold) {
        send_message(`${company_stock} ALERT UP`, `${company_stock} Price: ${yahoo_stock_price_details.data.quote.summaryDetail.regularMarketPrice}, Change: ${pre_market_change_percent}%`);
      } else if (market_phase === "regular" && regular_market_change_percent <= -change_percent_threshold) {
        send_message(`${company_stock} ALERT DOWN`, `${company_stock} Price: ${yahoo_stock_price_details.data.quote.summaryDetail.regularMarketPrice}, Change: ${regular_market_change_percent}%`);
        company.alerted_already = true;
      } else if (market_phase === "post" && post_market_change_percent >= change_percent_threshold) {
        send_message(`${company_stock} ALERT UP`, `${company_stock} Price: ${yahoo_stock_price_details.data.quote.summaryDetail.postMarketPrice}, Change: ${post_market_change_percent}%`);
        company.alerted_already = true;
      } else if (market_phase === "post" && post_market_change_percent <= -change_percent_threshold) {
        send_message(`${company_stock} ALERT DOWN`, `${company_stock} Price: ${yahoo_stock_price_details.data.quote.summaryDetail.postMarketPrice}, Change: ${post_market_change_percent}%`);
        company.alerted_already = true;
      }
    }
  }
}

if (require.main === module) {
  const eastern = 'US/Eastern';
  const tickerList = stockSymbols;
  const alert_threshold_percent = percentChangeThreshold;

  if (tickerList.length > 5) {
    console.log("**Ticker list > 5 detected, taking first 5 tickers to monitor! Printing the tickers monitored below:**");
    tickerList.splice(5);
    console.log("**The truncated ticker list is : " + tickerList + "**");
  }

  const watchList = [];
  for (const stock of tickerList) {
    const tempTicker = { stock_ticker: stock, alerted_already: false };
    watchList.push(tempTicker);
  }

  while (true) {
    const est_dt = new Date().toLocaleString('en-US', { timeZone: eastern });
    const weekday = new Date().getDay();

    if (is_now_in_time_period('20:00:00', '04:00:00', est_dt) || weekday >= 5) {
      if (weekday >= 5) {
        console.log("**Do nothing on closed market days Saturday and Sunday weekend**");
      } else {
        console.log("**Do nothing between 8PM EST and 4AM EST market hours**");
      }
      await new Promise(resolve => setTimeout(resolve, 60000)); // Sleep 1 minute
      continue;
    } else if (is_now_in_time_period('04:00:00', '09:30:00', est_dt)) { // Premarket analysis
      evaluate_watch_list(watchList, "pre", alert_threshold_percent);
    } else if (is_now_in_time_period('09:30:00', '16:00:00', est_dt)) { // Regular Market analysis
      evaluate_watch_list(watchList, "regular", alert_threshold_percent);
    } else if (is_now_in_time_period('16:00:00', '20:00:00', est_dt)) { // Post Market analysis
      evaluate_watch_list(watchList, "post", alert_threshold_percent);
    }

    await new Promise(resolve => setTimeout(resolve, 10000)); // Sleep 10 seconds
  }
}
