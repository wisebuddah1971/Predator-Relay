from fastapi import FastAPI, Request
import httpx
import os

app = FastAPI()

# TradersPost webhook
TP_WEBHOOK = "https://webhooks.traderspost.io/trading/webhook/023be3ba-bb69-41ec-a267-0ba8d41b18c8/8941a2a686d316d532f233097388fe30"

# Symbol map - September 2026 contract
SYMBOL_MAP = {"MNQ1!": "MNQU2026", "NQ1!": "NQU2026"}

# Tradovate API credentials
TV_CID = os.getenv("TV_CID", "").strip()
TV_SECRET = os.getenv("TV_SECRET", "").strip()
TV_ACCOUNT_ID = 1955595

# Position tracking
positions = {}

async def get_tradovate_token():
    async with httpx.AsyncClient() as c:
        r = await c.post(
            "https://live.tradovateapi.com/v1/auth/accesstokenrequest",
            json={
                "name": TV_CID,
                "password": TV_SECRET,
                "appId": "Predator-Relay",
                "appVersion": "1.0",
                "cid": int(TV_CID),
                "sec": TV_SECRET,
            }
        )
    data = r.json()
    return data.get("accessToken")

async def move_stop_to_breakeven(entry_price, remaining_qty):
    try:
        token = await get_tradovate_token()
        if not token:
            print("BE move failed: could not get Tradovate token")
            return

        headers = {"Authorization": f"Bearer {token}"}

        # Get open orders
        async with httpx.AsyncClient() as c:
            r = await c.get(
                "https://live.tradovateapi.com/v1/order/list",
                headers=headers
            )
        orders = r.json()

        # Find the working stop order
        stop_order = None
        for order in orders:
            if (order.get("orderType") == "Stop" and
                order.get("status") in ["Working", "Accepted"]):
                stop_order = order
                break

        if not stop_order:
            print("BE move failed: no working stop order found")
            return

        order_id = stop_order["id"]

        # Modify stop to breakeven
        async with httpx.AsyncClient() as c:
            r = await c.post(
                "https://live.tradovateapi.com/v1/order/modifyorder",
                headers=headers,
                json={
                    "orderId": order_id,
                    "orderType": "Stop",
                    "stopPrice": entry_price,
                    "qty": remaining_qty,
                }
            )
        print(f"BE move result: {r.json()}")

    except Exception as e:
        print(f"BE move exception: {str(e)}")


@app.post("/tp")
async def tp_endpoint(req: Request):
    d = await req.json()
    ev = d.get("event")
    ticker = SYMBOL_MAP.get(d.get("symbol", ""), d.get("symbol", ""))

    if ev == "entry":
        entry_price = float(d.get("entry", 0))
        positions[ticker] = {
            "action": d["action"],
            "entry_price": entry_price,
            "qty": 4
        }
        out = {
            "ticker":     ticker,
            "action":     d["action"],
            "quantity":   4,
            "stopLoss":   {"type": "stop", "stopPrice": float(d["sl"])},
            "takeProfit": {"limitPrice": float(d["tp3"])},
        }

    elif ev == "tp1_hit":
        pos = positions.get(ticker)
        if not pos:
            return {"ok": False, "error": "tp1_hit: no position tracked"}
        exit_action = "sell" if pos["action"] == "buy" else "buy"
        # Exit 2 contracts, leave SL in place
        out = {"ticker": ticker, "action": exit_action, "quantity": 2}
        positions[ticker]["qty"] = 2

    elif ev == "tp2_hit":
        pos = positions.get(ticker)
        if not pos:
            return {"ok": False, "error": "tp2_hit: no position tracked"}
        exit_action = "sell" if pos["action"] == "buy" else "buy"
        # Exit 1 contract
        out = {"ticker": ticker, "action": exit_action, "quantity": 1}
        positions[ticker]["qty"] = 1
        # Now move SL to BE on the 1 remaining runner
        await move_stop_to_breakeven(pos["entry_price"], 1)

    elif ev in ("tp3_hit", "sl_hit", "trail_exit", "dd_recovery_exit", "time_stop",
                "sl_post_tp1", "sl_post_tp2", "be_exit", "sl_be"):
        positions.pop(ticker, None)
        out = {"ticker": ticker, "action": "exit"}

    else:
        # Unknown event — treat as full exit to be safe
        print(f"Unknown event received: {ev} — treating as exit")
        positions.pop(ticker, None)
        out = {"ticker": ticker, "action": "exit"}

    async with httpx.AsyncClient() as c:
        r = await c.post(TP_WEBHOOK, json=out, timeout=10)
    return {"ok": True, "status": r.status_code, "sent": out}
