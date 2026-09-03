# Phase 2D: OBX Multi-Winner Ranked Bid Whitelist Auction System Report

**Deployment Date/Time**: 2026-09-02T07:29:50Z  
**Bot Identity**: `OBX test bot` (Application ID: `1544504783015125044`)  
**Target Discord Server**: `Haveli ka raasta bhool toh nahi gaye?` (Guild ID: `1542965409383321660`)  
**Configured Admin Role**: `Haveli Owner` (Role ID: `1542982329603985489`)  
**Database**: PostgreSQL 16 (`obx_economy`)  
**Alembic Migration Head**: `004_whitelist_auctions`  
**Automated Tests**: **122 / 122 Passed (100%)** on SQLite and PostgreSQL 16

---

## 1. Core Rule & Architecture: Multi-Winner Ranked Bidding

This system is strictly a **Multi-Winner Ranked Bid Auction**. It is **NOT** a lottery, raffle, weighted probability system, or random winner system.

### Ranking & Settlement Mechanics:
1. **Guaranteed Whitelist Spots ($N$)**: The administrator configures the exact number of available whitelist spots (e.g. 5 spots).
2. **Deterministic Winner Selection**:
   - At auction settlement, all unique bidders are sorted deterministically:
     1. Valid Bid Amount `DESC`
     2. Earliest Bid Update Timestamp `ASC`
     3. Stable Discord User ID `ASC`
   - The top $N$ unique bidders win the available whitelist spots.
3. **Pay-As-Bid Settlement**:
   - Each winner pays their actual submitted winning bid. Their locked funds are debited via `WalletService.debit` with double-entry ledger tracking (`ReferenceType.AUCTION_WIN`).
4. **Full Refund for Non-Winners**:
   - Every non-winning bidder has their full locked bid amount unlocked and returned to their available balance via `WalletService.release_funds` (`ReferenceType.AUCTION_REFUND`).
   - Zero lost funds.

---

## 2. Safe Bid Locking & Bid Updates

1. **Initial Bid Placement**:
   - Bidding locks funds from available balance (`available -> locked`).
   - Locked funds remain part of the user's total balance.
2. **Bid Increase**:
   - When a user raises their bid (e.g. 500 $\to$ 800 OBX), only the delta difference (`300 OBX`) is locked from available balance. No double-locking occurs.
3. **Bid Decrease**:
   - When a user lowers their bid (e.g. 800 $\to$ 500 OBX, provided it remains $\ge$ minimum bid), the delta difference (`300 OBX`) is automatically unlocked back to their available balance.
4. **One Active Bid Per User**:
   - Database unique constraint `uq_auction_bids_user` ensures one authoritative active bid per user per auction.

---

## 3. Live Winning Position & Cutoff Visibility

The auction browser displays real-time standings directly in Discord:
- **`📊 Winning Cutoff`**: The current $N$-th bid required to enter the winning spots.
- **`📍 Your Position`**: Shows user's current rank (e.g. `#12`), active bid (`1,500 OBX`), and status:
  - `🟢 Currently Winning (Rank #8 of 20 winning spots)`
  - `🔴 Outside Winning Positions (Rank #27 • Need to enter Top 20)`
- **`[📊 View Rankings]` Button**: Interactive leaderboard embed displaying the top bidders with medals (`🥇`, `🥈`, `🥉`) and bid amounts.

---

## 4. Custom Admin Rewards (`🎁 Grant Custom Reward`)

- **Interactive Modal & Slash Command**: `/admin-grant-reward` and Admin Hub button `[🎁 Grant Reward]`.
- **Authoritative Credit**: Admin can grant custom OBX rewards to any Discord user with an explicit audit reason/note.
- **Double-Entry Ledger Backed**: Credits are processed through `WalletService.credit` with `ReferenceType.ADMIN` and unique idempotency keys.
- **Member Notification**: Sends a direct message embed to the recipient when server permissions allow.

---

## 5. Automated Test Results (`122 / 122 Passed — 100%`)

```
============================= test session starts ==============================
rootdir: /Users/guppi/.gemini/antigravity/scratch/obx-ecosystem
configfile: pyproject.toml
testpaths: tests
plugins: asyncio-1.4.0, anyio-4.14.2
collected 122 items

tests/test_api.py (9 passed)
tests/test_auction_service.py (13 passed)
tests/test_balance_operations.py (8 passed)
tests/test_cli.py (5 passed)
tests/test_concurrency.py (1 passed)
tests/test_config.py (3 passed)
tests/test_discord_auctions.py (3 passed)
tests/test_discord_bot.py (2 passed)
tests/test_discord_bot_e2e.py (1 passed)
tests/test_discord_dashboard.py (4 passed)
tests/test_discord_leaderboard.py (3 passed)
tests/test_discord_views.py (5 passed)
tests/test_idempotency.py (7 passed)
tests/test_leaderboard_service.py (6 passed)
tests/test_premium_ux.py (7 passed)
tests/test_reconciliation.py (5 passed)
tests/test_submissions.py (6 passed)
tests/test_task_api.py (6 passed)
tests/test_task_concurrency.py (1 passed)
tests/test_task_editability.py (7 passed)
tests/test_task_rewards.py (5 passed)
tests/test_tasks.py (6 passed)
tests/test_users.py (4 passed)
tests/test_wallets.py (5 passed)

======================== 122 passed, 1 warning in 6.42s ========================
```
