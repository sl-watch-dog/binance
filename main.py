import time
import hmac
import hashlib
import requests
from urllib.parse import urlencode
from datetime import datetime, timedelta
from flask import Flask
from threading import Thread

# --- Flask Setup ---
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Binance SL Notifier is Running"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# --- Your Credentials ---
API_KEY = "your_binance_api_key"
API_SECRET = "your_binance_secret"
PUSHBULLET_TOKEN = "your_pushbullet_token"
BINANCE_BASE_URL = "https://fapi.binance.com"

# --- Send Alert ---
def send_pushbullet_alert(title, message):
    data = {"type": "note", "title": title, "body": message}
    response = requests.post(
        'https://api.pushbullet.com/v2/pushes',
        json=data,
        headers={'Access-Token': PUSHBULLET_TOKEN}
    )
    if response.status_code == 200:
        print("🔔 Alert sent:", message)
    else:
        print("❌ Alert failed:", response.text)

# --- Signed Binance Request ---
def signed_request(endpoint, params=None):
    if not params:
        params = {}
    params['timestamp'] = int(time.time() * 1000)
    query_string = urlencode(params)
    signature = hmac.new(API_SECRET.encode(), query_string.encode(), hashlib.sha256).hexdigest()
    url = f"{BINANCE_BASE_URL}{endpoint}?{query_string}&signature={signature}"
    headers = {'X-MBX-APIKEY': API_KEY}
    response = requests.get(url, headers=headers)
    try:
        return response.json()
    except:
        return {"error": "Invalid JSON", "response": response.text}

# --- Position Tracking ---
open_positions = {}

def run_notifier():
    while True:
        try:
            positions = signed_request("/fapi/v2/positionRisk")

            if not isinstance(positions, list):
                print("⚠️ Binance Error:", positions)
                send_pushbullet_alert("❌ Binance Error", str(positions))
                time.sleep(60)
                continue

            for pos in positions:
                symbol = pos['symbol']
                side = pos['positionSide']
                entry_price = float(pos['entryPrice'])
                position_amt = float(pos['positionAmt'])

                key = (symbol, side)
                if abs(position_amt) < 1e-8 or entry_price == 0.0:
                    open_positions.pop(key, None)
                    continue

                if key not in open_positions:
                    open_positions[key] = {
                        'entry_time': datetime.now(),
                        'sl_checked': False,
                        'last_alert': None,
                        'sl_set': False,
                        'last_sl_price': None
                    }
                    send_pushbullet_alert(
                        f"🚀 New trade opened: {symbol}",
                        f"Entry Price: {entry_price}, Size: {position_amt}, Side: {side}"
                    )

                pos_state = open_positions[key]
                time_open = datetime.now() - pos_state['entry_time']
                orders = signed_request("/fapi/v1/openOrders", {"symbol": symbol})

                if isinstance(orders, list):
                    sl_orders = [o for o in orders if o.get('type') in ["STOP", "STOP_MARKET"]]
                    if not sl_orders:
                        if pos_state['sl_set']:
                            send_pushbullet_alert(
                                f"❌ SL REMOVED: {symbol}",
                                f"SL removed for trade at {pos_state['entry_time'].strftime('%H:%M:%S')}"
                            )
                            pos_state['sl_set'] = False
                            pos_state['last_sl_price'] = None
                        elif time_open > timedelta(minutes=5):
                            now = datetime.now()
                            if pos_state['last_alert'] is None or (now - pos_state['last_alert']) > timedelta(hours=1):
                                send_pushbullet_alert(
                                    f"⚠️ SL MISSING: {symbol}",
                                    f"No SL set 5 mins after entry. Time: {pos_state['entry_time'].strftime('%H:%M:%S')}"
                                )
                                pos_state['last_alert'] = now
                    else:
                        sl_order = sl_orders[0]
                        sl_price = float(sl_order.get('stopPrice') or sl_order.get('price'))
                        if not pos_state['sl_set']:
                            pos_state['sl_set'] = True
                            abs_pct = abs(entry_price - sl_price) / entry_price * 100
                            if time_open > timedelta(minutes=5):
                                send_pushbullet_alert(
                                    f"✅ SL SET LATE: {symbol}",
                                    f"SL set after 5 mins. Distance: {abs_pct:.2f}%"
                                )
                            else:
                                send_pushbullet_alert(
                                    f"✅ SL SET ON TIME: {symbol}",
                                    f"SL set within 5 mins. Distance: {abs_pct:.2f}%"
                                )

                        if pos_state['last_sl_price'] is not None:
                            if abs(sl_price - pos_state['last_sl_price']) > 1e-6:
                                prev_pct = abs(entry_price - pos_state['last_sl_price']) / entry_price * 100
                                new_pct = abs(entry_price - sl_price) / entry_price * 100
                                send_pushbullet_alert(
                                    f"🔁 SL UPDATED: {symbol}",
                                    f"Previous: {prev_pct:.2f}%, New: {new_pct:.2f}%"
                                )
                        pos_state['last_sl_price'] = sl_price

        except Exception as e:
            print("⚠️ Exception:", str(e))

        time.sleep(60)

# --- Run Everything ---
if __name__ == '__main__':
    keep_alive()
    notifier_thread = Thread(target=run_notifier)
    notifier_thread.daemon = True
    notifier_thread.start()
    while True:
        time.sleep(10)
