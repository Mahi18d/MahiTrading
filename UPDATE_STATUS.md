# Mahi Trading update checkpoint — 28 September 2026

Implemented locally, not committed/pushed/deployed:

- Verified Google owner-only gate before market, database or broker access.
- Supabase account persistence with revision conflicts, failed-save handling,
  confirmed-account export, explicit import/reset confirmations.
- Session-independent broker holder; saved-owner reconnect and read-only token
  refresh. Manual broker sessions never reconnect as a different saved account.
- Positive/negative equity colours; separate broker quote and public candle status.
- Five-index header and broker-preferred index overview prices.
- Browser-active auto refresh, per-tab refresh controls, clearer CE/PE selection.
- Paper realized/open/overall P&L with incomplete-price handling.

Verification: 22 offline unittest/AppTest checks passed, including persistence
across fresh mocked browser sessions, denied-user access, malformed journals,
revision conflicts, unavailable quotes, and read recovery without order retries.
No real broker orders were sent. Syntax and whitespace checks passed.

Remaining release steps: user-owned Google OAuth and Supabase setup per
PRIVATE_SETUP.md; execute supabase_setup.sql; privately configure Streamlit
Secrets; test real login, storage/reload and read-only broker quotes. Actual
external integrations and SQL execution are not verified by offline mocks.
No claim is made that existing Angel One quote failures are fixed until that
read-only integration test succeeds. Browser-only lost paper trades have not
been recovered. Original local paper_trades.json remains untouched.

Preserve unrelated .gitignore and logo modifications. Do not stage all files;
private .streamlit, logs, paper_trades.json and trading files must stay out of Git.
