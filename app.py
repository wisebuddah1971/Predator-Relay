from fastapi import FastAPI, Request
import httpx, math

app = FastAPI()

TP_WEBHOOK = "https://webhooks.traderspost.io/trading/webhook/023be3ba-bb69-41ec-a267-0ba8d41b18c8/8941a2a686d316d532f233097388fe30"
SYMBOL_MAP = {"MNQ1!": "MNQM2026", "NQ1!": "NQM2026"}

@app.post("/tp")
async def tp(req: Request):
    d = await req.json()
    ev = d.get("event")
    ticker = SYMBOL_MAP.get(d["symbol"], d["symbol"])

    if ev == "entry":
        out = {
            "ticker":     ticker,
            "action":     d["action"],
            "quantity":   math.floor(float(d["qty"])),
            "stopLoss":   {"type": "stop", "stopPrice": float(d["sl"])},
            "takeProfit": {"limitPrice": float(d["tp3"])},
        }

    elif ev in ("tp3_hit", "sl_hit", "trail_exit", "dd_recovery_exit", "time_stop"):
        out = {
            "ticker":    ticker,
            "action":    "exit",
            "sentiment": "flat",
        }

    else:
        # tp1_hit, tp2_hit, and anything else — skip
        return {"ok": True, "skipped": ev}

    async with httpx.AsyncClient() as c:
        r = await c.post(TP_WEBHOOK, json=out, timeout=10)
    return {"ok": True, "status": r.status_code, "sent": out}
