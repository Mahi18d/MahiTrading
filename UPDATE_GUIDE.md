# Mahi Trading update

Run from the MT folder:

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Checks (no real broker orders):

```powershell
python -m unittest -v test_app.py test_update_batch.py test_ui_batch.py
```

## New features

- Equity's universe, horizon and refresh controls are inside Equity. F&O has a separate paper/live selector.
- Long-term delivery plans have editable stop/target prices, percentages, potential rupee P&L and risk-to-reward. These are planning levels, not broker-attached exits.
- Equity and F&O detect completed-candle breakout/breakdown, retest, engulfing and support/resistance rejection setups. Stale, incomplete or unconfirmed evidence displays WAIT or DATA REQUIRED. Results are not win-rate predictions.
- Market in 30 Seconds shows short linked positive, negative and watch cues, with source dates. Full official headlines remain expandable. Coverage is limited to the configured sources.
- Optional sidebar budgets warn on planned per-trade risk, recorded paper daily loss and paper trade count.
- A paper trade journal stores reasons, setup and lessons. Export JSON/CSV and optional screenshots before resetting the browser session. Setup performance uses recorded closed paper trades only.
- Broker order/position status is read only and fetched on request. It distinguishes submissions from fills. Missing SL/target orders are never inferred from planning values.

## Paper storage

The public server shares its filesystem across visitors. Paper accounts and screenshots now belong to each browser session. Export before refreshing or closing that session. Existing local `paper_trades.json` remains untouched but is not loaded automatically.

Intraday equity paper exits are checked on recent feed observations only while the app runs. F&O and long-term paper positions can be closed manually at an available quote in the Portfolio tab. Costs/slippage are excluded. No process monitors paper positions while the app sleeps.

## Saved broker credentials

Manual login remains available. Server credentials are never prefilled or used to connect every visitor automatically.

To use saved owner credentials, configure this privately in Streamlit Cloud Settings → Secrets (or local `.streamlit/secrets.toml`). Never commit credentials:

```toml
[angel_one]
api_key = "YOUR_NEW_API_KEY"
client_id = "YOUR_CLIENT_CODE"
mpin = "YOUR_MPIN"
totp_secret = "YOUR_TOTP_SECRET"

[app_access]
password_sha256 = "YOUR_64_CHARACTER_PASSWORD_HASH"
```

Generate the hash locally for a strong, unique owner-access password:

```powershell
python -c "import getpass,hashlib; print(hashlib.sha256(getpass.getpass('Owner access password: ').encode()).hexdigest())"
```

Open **Connect using saved owner credentials**, unlock it with your password, then click **Connect saved owner broker**. Without a configured hash, saved credentials stay locked; manual login still works. This protects the saved connection, not the public read-only pages. Use hosting access controls if the entire app should be private.

## Deployment and limits

Local edits do not update Streamlit Cloud until the reviewed files are pushed to its configured branch. Retain the correctly named `requirements.txt`.

Tests verify calculations, UI interactions and mocked broker responses. Real broker execution, margins, entitlement and fills still need verification with the broker. Live short-option entry remains unavailable. An LTP response without an exchange trade timestamp is a broker snapshot, not proof of a live trade.
