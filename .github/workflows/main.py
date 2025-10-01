import json
import os
import sys
from datetime import datetime, timedelta, timezone
from dateutil import tz
import requests
import yfinance as yf

STATE_PATH = "state.json"
HOLDINGS_PATH = "holdings.json"
CONFIG_PATH = "config.json"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Defaults voor holdings
DEFAULT_DROP_PCT = float(os.getenv("DEFAULT_DROP_PCT", "10"))   # watch: -10%
DEFAULT_RISE_PCT = float(os.getenv("DEFAULT_RISE_PCT", "5"))    # owned: +5%
COOLDOWN_MINUTES_WATCH = int(os.getenv("COOLDOWN_MINUTES_WATCH", "720"))
COOLDOWN_MINUTES_OWNED = int(os.getenv("COOLDOWN_MINUTES_OWNED", "1440"))

if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
    print("ERROR: TELEGRAM_TOKEN en/of TELEGRAM_CHAT_ID ontbreken als secrets.", file=sys.stderr)
    sys.exit(1)

# ---------- Helpers ----------
def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def send_telegram(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True}
    r = requests.post(url, json=payload, timeout=20)
    r.raise_for_status()

def get_last_price(ticker: str) -> float | None:
    try:
        t = yf.Ticker(ticker)
        p = None
        try:
            p = float(t.fast_info["last_price"])
        except Exception:
            p = None
        if p is None or p <= 0:
            hist = t.history(period="1d", interval="1m")
            if not hist.empty:
                p = float(hist["Close"].iloc[-1])
        return p
    except Exception as e:
        print(f"Waarschuwing: prijs ophalen mislukt voor {ticker}: {e}", file=sys.stderr)
        return None

def get_prev_close(ticker: str) -> float | None:
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="5d", interval="1d")
        if hist.shape[0] >= 2:
            return float(hist["Close"].iloc[-2])
        elif hist.shape[0] == 1:
            return float(hist["Close"].iloc[-1])
        return None
    except Exception as e:
        print(f"Waarschuwing: vorige slotkoers ophalen mislukt voor {ticker}: {e}", file=sys.stderr)
        return None

def ams_now():
    return datetime.now(tz=tz.gettz("Europe/Amsterdam"))

def within_cooldown(last_time_iso: str, cooldown_minutes: int) -> bool:
    try:
        last_dt = datetime.fromisoformat(last_time_iso)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        return ams_now() - last_dt < timedelta(minutes=cooldown_minutes)
    except Exception:
        return False

def s_key(*parts) -> str:
    return "::".join(parts)

# ---------- Telegram commands ----------
def get_updates(offset: int | None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    params = {"timeout": 0}
    if offset is not None:
        params["offset"] = offset
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json().get("result", [])

def normalize_symbol(sym: str) -> str:
    return sym.strip().upper()

def add_or_update_owned(holdings: list, symbol: str, entry: float, shares: float | None, rise_pct: float | None):
    symbol = normalize_symbol(symbol)
    found = False
    for h in holdings:
        if normalize_symbol(h.get("symbol","")) == symbol:
            h["status"] = "owned"
            h["entry_price"] = float(entry)
            if shares is not None:
                h["shares"] = shares
            if rise_pct is not None:
                h["rise_pct"] = rise_pct
            found = True
            break
    if not found:
        item = {"symbol": symbol, "status": "owned", "entry_price": float(entry)}
        if shares is not None:
            item["shares"] = shares
        if rise_pct is not None:
            item["rise_pct"] = rise_pct
        holdings.append(item)

def add_or_update_watch(holdings: list, symbol: str, baseline: float, drop_pct: float | None):
    symbol = normalize_symbol(symbol)
    found = False
    for h in holdings:
        if normalize_symbol(h.get("symbol","")) == symbol:
            h["status"] = "watch"
            h["baseline"] = float(baseline)
            if drop_pct is not None:
                h["drop_pct"] = drop_pct
            found = True
            break
    if not found:
        item = {"symbol": symbol, "status": "watch", "baseline": float(baseline)}
        if drop_pct is not None:
            item["drop_pct"] = drop_pct
        holdings.append(item)

def remove_symbol(holdings: list, symbol: str) -> bool:
    symbol = normalize_symbol(symbol)
    n_before = len(holdings)
    holdings[:] = [h for h in holdings if normalize_symbol(h.get("symbol","")) != symbol]
    return len(holdings) < n_before

def process_telegram_commands(state: dict) -> bool:
    changed = False
    last_update_id = state.get("telegram_last_update_id")
    updates = get_updates((last_update_id + 1) if isinstance(last_update_id, int) else None)

    if not updates:
        return False

    holdings = load_json(HOLDINGS_PATH, [])
    for upd in updates:
        uid = upd.get("update_id")
        msg = upd.get("message") or upd.get("edited_message")
        if not msg:
            last_update_id = uid
            continue

        chat = msg.get("chat", {})
        chat_id = str(chat.get("id", ""))
        text = (msg.get("text") or "").strip()

        if chat_id != str(TELEGRAM_CHAT_ID):
            last_update_id = uid
            continue

        parts = text.split()
        if not parts:
            last_update_id = uid
            continue

        cmd = parts[0].lower()
        try:
            if cmd in ("/buy", "/owned"):
                if len(parts) < 3:
                    send_telegram("Gebruik: /buy SYMBOL PRICE [SHARES]\nBijv: /buy ASML.AS 850 5")
                else:
                    symbol = parts[1]
                    entry = float(parts[2].replace(",", "."))
                    shares = float(parts[3]) if len(parts) >= 4 else None
                    add_or_update_owned(holdings, symbol, entry, shares, None)
                    changed = True
                    send_telegram(f"✅ OWNED: {symbol} @ {entry}" + (f" ({shares} stuks)" if shares else ""))

            elif cmd == "/watch":
                if len(parts) < 3:
                    send_telegram("Gebruik: /watch SYMBOL BASELINE [DROP_PCT]\nBijv: /watch ADYEN.AS 1200 10")
                else:
                    symbol = parts[1]
                    baseline = float(parts[2].replace(",", "."))
                    drop = float(parts[3]) if len(parts) >= 4 else None
                    add_or_update_watch(holdings, symbol, baseline, drop)
                    changed = True
                    dp = drop if drop is not None else DEFAULT_DROP_PCT
                    send_telegram(f"👀 WATCH: {symbol} baseline {baseline} (drop {dp}%)")

            elif cmd in ("/sell", "/remove"):
                if len(parts) < 2:
                    send_telegram("Gebruik: /sell SYMBOL\nBijv: /sell ASML.AS")
                else:
                    symbol = parts[1]
                    if remove_symbol(holdings, symbol):
                        changed = True
                        send_telegram(f"🗑️ Verwijderd: {symbol}")
                    else:
                        send_telegram(f"ℹ️ {symbol} stond niet in holdings.")

            elif cmd == "/help":
                send_telegram(
                    "📋 Commando's:\n"
                    "/buy SYMBOL PRICE [SHARES]\n"
                    "/owned SYMBOL PRICE [SHARES]\n"
                    "/watch SYMBOL BASELINE [DROP_PCT]\n"
                    "/sell SYMBOL\n"
                    "/remove SYMBOL\n"
                    "Voorbeeld: /buy ASML.AS 850 5"
                )
            else:
                pass
        except Exception as e:
            send_telegram(f"❌ Fout: {e}")

        last_update_id = uid

    if changed:
        save_json(HOLDINGS_PATH, holdings)

    state["telegram_last_update_id"] = last_update_id
    return changed

# ---------- Alerts ----------
def handle_owned(pos, state):
    symbol = pos["symbol"].strip()
    entry = float(pos["entry_price"])
    rise_pct = float(pos.get("rise_pct", DEFAULT_RISE_PCT))
    shares = pos.get("shares")
    last_price = get_last_price(symbol)
    if last_price is None:
        return False
    target = entry * (1 + rise_pct / 100.0)
    hit = last_price >= target
    if not hit:
        return False

    key = s_key(symbol, "owned_rise")
    last_alert = state.get(key, {}).get("last_alert_iso")
    if last_alert and within_cooldown(last_alert, COOLDOWN_MINUTES_OWNED):
        return False

    pct_move = (last_price / entry - 1.0) * 100.0
    ts = ams_now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"📈 <b>{symbol}</b> staat +{pct_move:.2f}% t.o.v. jouw entry (€{entry:,.2f}).",
        f"Huidige prijs: €{last_price:,.2f}  |  Doel (≥ {rise_pct:.0f}%): €{target:,.2f}",
        (f"Aantal: {shares}" if shares is not None else ""),
        f"⏰ {ts} (Europe/Amsterdam)",
        "",
        "👉 Alert: take-profit drempel bereikt."
    ]
    send_telegram("\n".join([l for l in lines if l]))
    state[key] = {"last_alert_iso": ams_now().isoformat()}
    return True

def handle_watch_fixed(pos, state):
    symbol = pos["symbol"].strip()
    baseline = float(pos["baseline"])
    drop_pct = float(pos.get("drop_pct", DEFAULT_DROP_PCT))
    last_price = get_last_price(symbol)
    if last_price is None:
        return False
    target = baseline * (1 - drop_pct / 100.0)
    hit = last_price <= target
    if not hit:
        return False

    key = s_key(symbol, "watch_drop_fixed")
    last_alert = state.get(key, {}).get("last_alert_iso")
    if last_alert and within_cooldown(last_alert, COOLDOWN_MINUTES_WATCH):
        return False

    pct_move = (last_price / baseline - 1.0) * 100.0
    ts = ams_now().strftime("%Y-%m-%d %H:%M")
    msg = (
        f"🔻 <b>{symbol}</b> is {pct_move:.2f}% onder je baseline (€{baseline:,.2f}).\n"
        f"Huidige prijs: €{last_price:,.2f}  |  Doel (≤ {drop_pct:.0f}%): €{target:,.2f}\n"
        f"⏰ {ts} (Europe/Amsterdam)\n\n"
        f"👉 Alert: koers is ≥{drop_pct:.0f}% gedaald vanaf baseline."
    )
    send_telegram(msg)
    state[key] = {"last_alert_iso": ams_now().isoformat()}
    return True

def handle_universe(universe_cfg, state):
    name = universe_cfg.get("name", "UNIVERSE")
    file = universe_cfg["file"]
    drop_pct = float(universe_cfg.get("drop_pct", 10))
    baseline_mode = universe_cfg.get("baseline_mode", "prev_close")
    cooldown_minutes = int(universe_cfg.get("cooldown_minutes", 720))

    tickers = load_json(file, [])
    if not tickers:
        print(f"Universe '{name}' leeg of niet gevonden: {file}")
        return False

    any_changed = False
    for symbol in tickers:
        symbol = symbol.strip()
        if baseline_mode == "prev_close":
            baseline = get_prev_close(symbol)
            if baseline is None or baseline <= 0:
                continue
        else:
            continue

        last_price = get_last_price(symbol)
        if last_price is None:
            continue

        target = baseline * (1 - drop_pct / 100.0)
        hit = last_price <= target
        if not hit:
            continue

        key = s_key("universe", name, symbol, f"drop{int(drop_pct)}")
        last_alert = state.get(key, {}).get("last_alert_iso")
        if last_alert and within_cooldown(last_alert, cooldown_minutes):
            continue

        pct_move = (last_price / baseline - 1.0) * 100.0
        ts = ams_now().strftime("%Y-%m-%d %H:%M")
        msg = (
            f"🔻 <b>{symbol}</b> ({name}) is {pct_move:.2f}% onder vorige slotkoers (€{baseline:,.2f}).\n"
            f"Huidige prijs: €{last_price:,.2f}  |  Doel (≤ {drop_pct:.0f}%): €{target:,.2f}\n"
            f"⏰ {ts} (Europe/Amsterdam)\n\n"
            f"👉 Universe-scan: daling ≥{drop_pct:.0f}% t.o.v. vorige close."
        )
        send_telegram(msg)
        state[key] = {"last_alert_iso": ams_now().isoformat()}
        any_changed = True

    return any_changed

# ---------- Main ----------
def main():
    holdings = load_json(HOLDINGS_PATH, [])
    config = load_json(CONFIG_PATH, {"universes": []})
    state = load_json(STATE_PATH, {})

    changed_files = False
    alerts_sent = False

    try:
        if process_telegram_commands(state):
            changed_files = True
    except Exception as e:
        print(f"Fout in commandoprocessing: {e}", file=sys.stderr)

    for pos in holdings:
        status = str(pos.get("status","")).strip().lower()
        if status == "owned":
            alerts_sent |= handle_owned(pos, state)
        elif status == "watch":
            if "baseline" in pos:
                alerts_sent |= handle_watch_fixed(pos, state)
            else:
                print(f"Watch zonder 'baseline' overgeslagen voor {pos.get('symbol')}.")
        else:
            print(f"Overgeslagen {pos.get('symbol')}: onbekende status '{status}'.")

    for uni in config.get("universes", []):
        alerts_sent |= handle_universe(uni, state)

    save_json(STATE_PATH, state)
    print("Klaar.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
