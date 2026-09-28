# Private Mahi Trading setup

This update is locked until owner login and database configuration are present.
Configure before deploying. Never commit secrets or paste keys into chat.

## 1. Supabase

Create your own Supabase project. Review the selected plan and pricing before
creating it; this code does not purchase or provision a service.
Run `supabase_setup.sql` in that project's SQL editor. Keep the project URL and
server secret key (or legacy service_role key) in Streamlit Secrets only.
Do not use a publishable/anon key. Tables and RPCs deny anonymous/authenticated
client access; the verified-owner server accesses them using its secret key.

The account is keyed by a hash of the configured owner email. Changing that email
creates a different account, not a migration. Database revisions reject conflicting
saves from other tabs. Failed/ambiguous saves require a reload to check the actual
stored record before retrying. Database availability is required; there is no
silent empty-account fallback.

## 2. Google owner login

Create a Google OAuth web client and configure its consent screen. Add the exact
authorized redirect URI `https://mahi18.streamlit.app/oauth2callback`.
For local testing use a separate client/config with
`http://localhost:8501/oauth2callback`. Restrict testing users to your own account
where applicable. Mahi additionally checks the verified Google email and issuer
before showing data or making broker/database calls.

## 3. Streamlit Secrets

Merge these sections in the app's private Secrets settings; replace placeholders.
Keep existing Angel One credentials private. All TOML string values need quotes.

```toml
[auth]
redirect_uri = "https://mahi18.streamlit.app/oauth2callback"
cookie_secret = "REPLACE_WITH_A_LONG_RANDOM_SECRET"
client_id = "YOUR_GOOGLE_CLIENT_ID"
client_secret = "YOUR_GOOGLE_CLIENT_SECRET"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"

[app_access]
owner_email = "YOUR_GOOGLE_EMAIL"

[supabase]
url = "https://YOUR_PROJECT.supabase.co"
service_key = "YOUR_SERVER_SECRET_KEY"

[angel_one]
api_key = "YOUR_API_KEY"
client_id = "YOUR_CLIENT_CODE"
mpin = "YOUR_MPIN"
totp_secret = "YOUR_TOTP_SECRET"
auto_connect = true
```

Install updated requirements before starting. Sign in with the owner account,
create a small paper trade, reload the browser, then confirm its ID and journal
are unchanged. Test an unauthorized Google account: no trading data should show.
Keep JSON exports as independent backups. Existing local paper_trades.json is not
read, overwritten or uploaded automatically. Import a valid export explicitly in
the portfolio tab. Lost browser-only trades cannot be reconstructed by this update.

## Connection and refresh limits

Broker objects persist across browser refreshes within the same app process.
Saved server credentials allow reconnection after process restarts; manually typed
credentials do not survive process restarts. Authentication failures on reads can
trigger one token refresh/reconnect, with a cooldown. Orders are never automatically
retried. Broker outages, entitlement errors, or expired credentials still need
attention; no perpetual connection is guaranteed.

Auto refresh runs only while the browser app is active. It does not create an
always-on trading service. Quotes, candles and fallback public data retain separate
source/freshness labels. A connected login is not proof of a live quote. Paper P&L
excludes fees/slippage and is unavailable overall when any position lacks a quote.

Optional uploaded journal screenshots remain session-only; trade records and text
journal fields are saved in the database. No orders are submitted by setup/tests.

Before release, verify real owner login, Supabase persistence and broker read-only
quotes in the deployed environment. Offline tests cannot verify those services.
