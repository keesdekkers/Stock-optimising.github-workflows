# Stocks Alerts v2 (Telegram commands + Universe Scanner)

- Universe-scan (AEX): alert bij daling ≥10% t.o.v. vorige slotkoers (cooldown 12h).
- Owned (+5%): alert bij stijging ≥5% t.o.v. entry (cooldown 24h).
- Watch (baseline): alert bij daling ≥10% t.o.v. baseline (cooldown 12h).
- NIEUW: beheer holdings via Telegram-commando's (geen GitHub-edit nodig).

## Telegram-commando's
- `/buy SYMBOL PRICE [SHARES]`  (alias: `/owned`)  
- `/watch SYMBOL BASELINE [DROP_PCT]`  
- `/sell SYMBOL`  (alias: `/remove`)  
- `/help`

Alleen berichten van jouw TELEGRAM_CHAT_ID worden geaccepteerd.

## Snelstart
1) Secrets zetten: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID.
2) (Optioneel) Pas `config.json` en `universes/aex.json` aan.
3) `holdings.json` mag leeg blijven (`[]`); beheer via Telegram.
4) Run workflow via Actions om te testen.
