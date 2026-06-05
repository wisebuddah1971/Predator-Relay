from fastapi import FastAPI, Request
import httpx

app = FastAPI()

TP_WEBHOOK = "https://webhooks.traderspost.io/trading/webhook/023be3ba-bb69-41ec-a267-0ba8d41b18c8/8941a2a686d316d532f233097388fe30"
SYMBOL_MAP = {"MNQ1!": "MNQM2026", "NQ1!": "NQM2026"}

# Fixed contract size
ENTRY_QTY = 4

# In-memory position tracker: ticker -> entry action ("buy" or "sell")
position = {}

@app.post("/tp")
async def tp(req: Request):
    d = await req.json()
    ev = d.get("event")
    ticker = SYMBOL_MAP.get(d["symbol"], d["symbol"])

    if ev == "entry":
        # Remember entry direction
        position[ticker] = d["action"]
        out = {
            "ticker":     ticker,
            "action":     d["action"],
            "quantity":   ENTRY_QTY,
            "stopLoss":   {"type": "stop", "stopPrice": float(d["sl"])},
            "takeProfit": {"limitPrice": float(d["tp3"])},
        }

    elif ev == "tp1_hit":
        # Exit 2 contracts
        entry_action = position.get(ticker)
        if not entry_action:
            return {"ok": False, "error": "tp1_hit: no position tracked", "event": ev}
        exit_action = "sell" if entry_action == "buy" else "buy"
        out = {
            "ticker":   ticker,
            "action":   exit_action,
            "quantity": 2,
        }

    elif ev == "tp2_hit":
        # Exit 1 contract
        entry_action = position.get(ticker)
        if not entry_action:
            return {"ok": False, "error": "tp2_hit: no position tracked", "event": ev}
        exit_action = "sell" if entry_action == "buy" else "buy"
        out = {
            "ticker":   ticker,
            "action":   exit_action,
            "quantity": 1,
        }

    elif ev in ("tp3_hit", "sl_hit", "trail_exit", "dd_recovery_exit", "time_stop"):
        # Flatten remaining 1 contract and clear memory
        position.pop(ticker, None)
        out = {
            "ticker": ticker,
            "action": "exit",
        }

    else:
        return {"ok": True, "skipped": ev}

    async with httpx.AsyncClient() as c:
        r = await c.post(TP_WEBHOOK, json=out, timeout=10)
    return {"ok": True, "status": r.status_code, "sent": out}
