from fastapi import FastAPI, Request
import httpx, math

app = FastAPI()

TP_WEBHOOK = "https://webhooks.traderspost.io/trading/webhook/023be3ba-bb69-41ec-a267-0ba8d41b18c8/8941a2a686d316d532f233097388fe30"
SYMBOL_MAP = {"MNQ1!": "MNQM2026", "NQ1!": "NQM2026"}

# In-memory position tracker: symbol -> {"action": "buy"/"sell", "qty": N}
position = {}

@app.post("/tp")
async def tp(req: Request):
    d = await req.json()
    ev = d.get("event")
    ticker = SYMBOL_MAP.get(d["symbol"], d["symbol"])

    if ev == "entry":
        # Remember entry direction and qty for this symbol
        position[ticker] = {
            "action": d["action"],
            "qty": math.floor(float(d["qty"]))
        }
        out = {
            "ticker":     ticker,
            "action":     d["action"],
            "quantity":   position[ticker]["qty"],
            "stopLoss":   {"type": "stop", "stopPrice": float(d["sl"])},
            "takeProfit": {"limitPrice": float(d["tp3"])},
        }

    elif ev in ("tp1_hit", "tp2_hit"):
        # Partial exit — use payload action if present, else fall back to memory
        entry_action = d.get("action") or (position.get(ticker, {}).get("action"))
        if not entry_action:
            return {"ok": False, "error": "tp partial exit: no entry direction known", "event": ev}
        exit_action = "sell" if entry_action == "buy" else "buy"
        qty = math.floor(float(d.get("qty", 1)))
        out = {
            "ticker":   ticker,
            "action":   exit_action,
            "quantity": qty,
        }

    elif ev in ("tp3_hit", "sl_hit", "trail_exit", "dd_recovery_exit", "time_stop"):
        # Full close — flatten and clear memory
        position.pop(ticker, None)
        out = {
            "ticker":    ticker,
            "action":    "exit",
            "sentiment": "flat",
        }

    else:
        return {"ok": True, "skipped": ev}

    async with httpx.AsyncClient() as c:
        r = await c.post(TP_WEBHOOK, json=out, timeout=10)
    return {"ok": True, "status": r.status_code, "sent": out}
