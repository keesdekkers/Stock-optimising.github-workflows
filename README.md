# Stocks Alerts (Watch −10%, Owned +5%, Universe Scanner)

Deze repo checkt:
- **Owned** posities: pushmelding bij **+5%** t.o.v. je `entry_price` (configureerbaar).
- **Watch** met vaste baseline: pushmelding bij **−10%** t.o.v. je `baseline` (configureerbaar).
- **Universe-scan** (AEX voorbeeld): pushmelding als een ticker **≥10%** onder de **vorige slotkoers** staat.

Pushmeldingen via **Telegram**. Scheduler via **GitHub Actions** (standaard elke 10 min op werkdagen, UTC).

## Setup

1. **Fork/clone** deze repo naar je eigen GitHub.
2. Zet repo **Secrets** (Settings → Secrets and variables → Actions → New repository secret):
   - `TELEGRAM_TOKEN`
   - `TELEGRAM_CHAT_ID`
3. **Telegram bot** maken:
   - In Telegram: zoek `@BotFather` → `/start` → `/newbot` → naam + username kiezen → token kopiëren.
   - Stuur 1 bericht naar je bot.
   - Chat ID ophalen via `@RawDataBot` of `@myidbot`.
4. (Optioneel) Pas **cron** aan in `.github/workflows/monitor.yml` (GitHub gebruikt **UTC**).

## Configuratie

### holdings.json
Voor je persoonlijke posities/regeltjes:

```json
[
  { "symbol": "ASML.AS", "status": "watch", "baseline": 900.00, "drop_pct": 10 },
  { "symbol": "AAPL",    "status": "owned", "entry_price": 180.00, "rise_pct": 5, "shares": 10 }
]
```

- `status: "owned"` → alert bij `rise_pct` (default 5) t.o.v. `entry_price`.
- `status: "watch"` → alert bij `drop_pct` (default 10) t.o.v. `baseline`.

### config.json + universes/
Voor index/universe scans (voorbeeld **AEX**):

```json
{
  "universes": [
    {
      "name": "AEX",
      "file": "universes/aex.json",
      "drop_pct": 10,
      "baseline_mode": "prev_close",
      "cooldown_minutes": 720
    }
  ]
}
```

`universes/aex.json` bevat de tickers (Euronext: `.AS` suffix). Voeg zelf tickers toe of maak extra universes (bijv. S&P 500).

## Env defaults (optioneel via workflow)

- `DEFAULT_DROP_PCT` (watch; default 10)
- `DEFAULT_RISE_PCT` (owned; default 5)
- `COOLDOWN_MINUTES_WATCH` (default 720)
- `COOLDOWN_MINUTES_OWNED` (default 1440)

## Notities

- Koersen via `yfinance`. Voor universes gebruiken we `prev_close` als baseline (gisteren), zodat je geen handmatige baselines hoeft te kiezen.
- Cooldowns voorkomen spam; alerts loggen in `state.json` (wordt committed door de workflow).
- Euronext Amsterdam tickers gebruiken vaak `.AS` (bijv. `ADYEN.AS`, `ASML.AS`, `PHIA.AS`).

## Starten

- Push deze repo naar GitHub, zet de secrets, en laat de workflow draaien. Je kunt ook handmatig runnen via het **Actions** tabje (workflow_dispatch).
