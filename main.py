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
    """Vorige slotkoers (gisteren) – robuuster dan fast_info voor baseline."""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="5d", interval="1d")
        if hist.shape[0] >= 2:
            # op één-na-laatste rij = vorige handelsdag
            return float(hist["Close"].iloc[-2])
        elif hist.shape[0] == 1:
            # fallback: enige dag = neem die close (kan eerste dag noteren)
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
    send_telegram("\\n".join([l for l in lines if l]))
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
        f"🔻 <b>{symbol}</b> is {pct_move:.2f}% onder je baseline (€{baseline:,.2f}).\\n"
        f"Huidige prijs: €{last_price:,.2f}  |  Doel (≤ {drop_pct:.0f}%): €{target:,.2f}\\n"
        f"⏰ {ts} (Europe/Amsterdam)\\n\\n"
        f"👉 Alert: koers is ≥{drop_pct:.0f}% gedaald vanaf baseline."
    )
    send_telegram(msg)
    state[key] = {"last_alert_iso": ams_now().isoformat()}
    return True

def handle_universe(universe_cfg, state):
    name = universe_cfg.get("name", "UNIVERSE")
    file = universe_cfg["file"]
    drop_pct = float(universe_cfg.get("drop_pct", 10))
    baseline_mode = universe_cfg.get("baseline_mode", "prev_close")  # only mode implemented
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
            # andere modes kunnen later
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
            f"🔻 <b>{symbol}</b> ({name}) is {pct_move:.2f}% onder vorige slotkoers (€{baseline:,.2f}).\\n"
            f"Huidige prijs: €{last_price:,.2f}  |  Doel (≤ {drop_pct:.0f}%): €{target:,.2f}\\n"
            f"⏰ {ts} (Europe/Amsterdam)\\n\\n"
            f"👉 Universe-scan: daling ≥{drop_pct:.0f}% t.o.v. vorige close."
        )
        send_telegram(msg)
        state[key] = {"last_alert_iso": ams_now().isoformat()}
        any_changed = True

    return any_changed

def main():
    holdings = load_json(HOLDINGS_PATH, [])
    config = load_json(CONFIG_PATH, {"universes": []})
    state = load_json(STATE_PATH, {})

    changed = False

    # 1) Jouw holdings
    for pos in holdings:
        status = pos.get("status", "").strip().lower()
        if status == "owned":
            changed |= handle_owned(pos, state)
        elif status == "watch":
            # Alleen fixed-baseline watch hier; universe-watch gebeurt los
            if "baseline" in pos:
                changed |= handle_watch_fixed(pos, state)
            else:
                print(f"Watch zonder 'baseline' overgeslagen voor {pos.get('symbol')}. Gebruik universe-scan of zet 'baseline'.")
        else:
            print(f"Overgeslagen {pos.get('symbol')}: onbekende status '{status}' (use 'owned' of 'watch').")

    # 2) Universes
    for uni in config.get("universes", []):
        changed |= handle_universe(uni, state)

    if changed:
        save_json(STATE_PATH, state)

    print("Klaar.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
