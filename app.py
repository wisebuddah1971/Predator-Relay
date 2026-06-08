from fastapi import FastAPI, Request
import httpx

app = FastAPI()

# 5 min webhook
TP_WEBHOOK_5M = "https://webhooks.traderspost.io/trading/webhook/023be3ba-bb69-41ec-a267-0ba8d41b18c8/8941a2a686d316d532f233097388fe30"

# 1 min webhook
TP_WEBHOOK_1M = "https://webhooks.traderspost.io/trading/webhook/5d99688f-8c18-418e-bc98-9ffee1737080/6852e59848818e02e9faca661ea9d470"

SYMBOL_MAP = {"MNQ1!": "MNQM2026", "NQ1!": "NQM2026"}

ENTRY_QTY = 4

# Separate position tracking for each timeframe
position_5m = {}
position_1m = {}

async def handle_signal(d, webhook_url, position):
    ev = d.get("event")
    ticker = SYMBOL_MAP.get(d["symbol"], d["symbol"])

    if ev == "entry":
        position[ticker] = d["action"]
        out = {
            "ticker":     ticker,
            "action":     d["action"],
            "quantity":   ENTRY_QTY,
            "stopLoss":   {"type": "stop", "stopPrice": float(d["sl"])},
            "takeProfit": {"limitPrice": float(d["tp3"])},
        }

    elif ev == "tp2_hit":
        entry_action = position.get(ticker)
        if not entry_action:
            return {"ok": False, "error": "tp2_hit: no position tracked", "event": ev}
        exit_action = "sell" if entry_action == "buy" else "buy"
        out = {"ticker": ticker, "action": exit_action, "quantity": 2}

    elif ev == "tp1_hit":
        entry_action = position.get(ticker)
        if not entry_action:
            return {"ok": False, "error": "tp1_hit: no position tracked", "event": ev}
        exit_action = "sell" if entry_action == "buy" else "buy"
        out = {"ticker": ticker, "action": exit_action, "quantity": 1}

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
    return await handle_signal(d, TP_WEBHOOK_5M, position_5m)


@app.post("/tp1m")
async def tp_1m(req: Request):
    d = await req.json()
    return await handle_signal(d, TP_WEBHOOK_1M, position_1m)
