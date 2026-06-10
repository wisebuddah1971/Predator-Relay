from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, HTMLResponse
import httpx
from datetime import datetime, time
import pytz
import os

app = FastAPI()

# Webhooks
TP_WEBHOOK_5M = "https://webhooks.traderspost.io/trading/webhook/023be3ba-bb69-41ec-a267-0ba8d41b18c8/8941a2a686d316d532f233097388fe30"
TP_WEBHOOK_1M = "https://webhooks.traderspost.io/trading/webhook/5d99688f-8c18-418e-bc98-9ffee1737080/6852e59848818e02e9faca661ea9d470"

SYMBOL_MAP = {"MNQ1!": "MNQM2026", "NQ1!": "NQM2026"}
ENTRY_QTY = 4

# TradeStation API credentials (stored as env vars on Render)
TS_CLIENT_ID = os.getenv("TS_CLIENT_ID")
TS_CLIENT_SECRET = os.getenv("TS_CLIENT_SECRET")

TS_ACCOUNT = "210VMQ73"
TS_REDIRECT_URI = "https://predator-relay.onrender.com/callback"
TS_REFRESH_TOKEN = os.getenv("TS_REFRESH_TOKEN", "")

# Position tracking
position_5m = {}
position_1m = {}

# Trading hours (ET): 9:45 AM - 3:45 PM
MARKET_OPEN = time(9, 45)
MARKET_CLOSE = time(15, 45)
ET = pytz.timezone("America/New_York")

def is_trading_hours():
    now = datetime.now(ET).time()
    return MARKET_OPEN <= now <= MARKET_CLOSE

# --- TradeStation OAuth ---

@app.get("/auth")
async def auth():
    url = (
        f"https://signin.tradestation.com/authorize"
        f"?response_type=code"
        f"&client_id={TS_CLIENT_ID}"
        f"&redirect_uri={TS_REDIRECT_URI}"
        f"&scope=openid profile MarketData ReadAccount Trade offline_access"

        f"&audience=https://api.tradestation.com"
    )
    return RedirectResponse(url)

@app.get("/callback")
async def callback(code: str):
    async with httpx.AsyncClient() as c:
        r = await c.post(
            "https://signin.tradestation.com/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": TS_CLIENT_ID,
                "client_secret": TS_CLIENT_SECRET,
                "code": code,
                "redirect_uri": TS_REDIRECT_URI,
            }
        )
    data = r.json()
    refresh_token = data.get("refresh_token", "NOT FOUND")
    return HTMLResponse(f"""
        <h2>Auth successful!</h2>
        <p>Copy this refresh token and add it to Render as environment variable <b>TS_REFRESH_TOKEN</b>:</p>
        <textarea rows="4" cols="80">{refresh_token}</textarea>
    """)

async def get_ts_access_token():
    async with httpx.AsyncClient() as c:
        r = await c.post(
            "https://signin.tradestation.com/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": TS_CLIENT_ID,
                "client_secret": TS_CLIENT_SECRET,
                "refresh_token": TS_REFRESH_TOKEN,
            }
        )
    return r.json().get("access_token")

async def move_stop_to_breakeven(ticker, entry_price):
    if not TS_REFRESH_TOKEN:
        return {"ok": False, "error": "No refresh token configured"}
    try:
        access_token = await get_ts_access_token()
        # Get open orders for the account
        async with httpx.AsyncClient() as c:
            r = await c.get(
                f"https://api.tradestation.com/v3/brokerage/accounts/{TS_ACCOUNT}/orders",
                headers={"Authorization": f"Bearer {access_token}"}
            )
        orders = r.json().get("Orders", [])
        # Find the stop order for this symbol
        stop_order = None
        for order in orders:
            if (order.get("Symbol") == ticker and 
                order.get("OrderType") == "StopMarket" and 
                order.get("Status") in ["OPN", "ACK"]):
                stop_order = order
                break
        if not stop_order:
            return {"ok": False, "error": "No open stop order found"}
        order_id = stop_order["OrderID"]
        # Cancel and replace with breakeven stop
        async with httpx.AsyncClient() as c:
            r = await c.put(
                f"https://api.tradestation.com/v3/orderexecution/orders/{order_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                json={
                    "OrderType": "StopMarket",
                    "StopPrice": str(entry_price),
                    "Quantity": str(stop_order.get("Quantity", ENTRY_QTY - 2)),
                }
            )
        return {"ok": True, "result": r.json()}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# --- Signal handling ---

async def handle_signal(d, webhook_url, position, is_live=False):
    ev = d.get("event")
    ticker = SYMBOL_MAP.get(d["symbol"], d["symbol"])

    # Time filter — only trade during market hours
    if ev == "entry" and not is_trading_hours():
        return {"ok": True, "skipped": "outside trading hours", "event": ev}

    if ev == "entry":
        position[ticker] = {
            "action": d["action"],
            "entry_price": float(d.get("entry", 0))
        }
        out = {
            "ticker":     ticker,
            "action":     d["action"],
            "quantity":   ENTRY_QTY,
            "stopLoss":   {"type": "stop", "stopPrice": float(d["sl"])},
            "takeProfit": {"limitPrice": float(d["tp3"])},
        }

    elif ev == "tp2_hit":
        pos = position.get(ticker)
        if not pos:
            return {"ok": False, "error": "tp2_hit: no position tracked", "event": ev}
        exit_action = "sell" if pos["action"] == "buy" else "buy"
        out = {"ticker": ticker, "action": exit_action, "quantity": 2}

    elif ev == "tp1_hit":
        pos = position.get(ticker)
        if not pos:
            return {"ok": False, "error": "tp1_hit: no position tracked", "event": ev}
        exit_action = "sell" if pos["action"] == "buy" else "buy"
        out = {"ticker": ticker, "action": exit_action, "quantity": 1}
        # Move stop to breakeven on live account
        if is_live and pos.get("entry_price"):
            await move_stop_to_breakeven(ticker, pos["entry_price"])

    elif ev in ("tp3_hit", "sl_hit", "trail_exit", "dd_recovery_exit", "time_stop"):
        position.pop(ticker, None)
        out = {"ticker": ticker, "action": "exit"}

    else:
        return {"ok": True, "skipped": ev}

    async with httpx.AsyncClient() as c:
        r = await c.post(webhook_url, json=out, timeout=10)
    return {"ok": True, "status": r.status_code, "sent": out}


@app.post("/tp")
async def tp_5m(req: Request):
    d = await req.json()
    return await handle_signal(d, TP_WEBHOOK_5M, position_5m, is_live=True)


@app.post("/tp1m")
async def tp_1m(req: Request):
    d = await req.json()
    return await handle_signal(d, TP_WEBHOOK_1M, position_1m, is_live=False)
