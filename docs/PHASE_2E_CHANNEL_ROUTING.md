# Phase 2E: OBX Channel Routing & Community Announcement System

## 1. Overview & Architecture

The OBX Channel Routing & Community Announcement System transforms the OBX Discord application into an organized, multi-channel community platform. Feature dashboards, public rankings, whitelist drop cards, and winner allocations are automatically dispatched and refreshed in their assigned Discord channels.

### Architecture Highlights:
- **Database-Backed Guild Configuration**: Channel bindings persist across restarts and redeployments in the `guild_configs` PostgreSQL table.
- **Published Message Outbox / Tracking**: Every public persistent card (Task Dashboard, Leaderboard, Auction Card, Winner Result) is indexed in `published_messages` with a unique constraint on `(guild_id, feature_type, source_id)` to ensure **zero duplicate spam**.
- **Interactive Native Channel Selectors**: Administrators configure destination channels with Discord's native `discord.ui.ChannelSelect` component with immediate validation of `View Channel`, `Send Messages`, and `Embed Links` permissions.
- **Winner Privacy Enforcement**: Public winner announcements display only sanitized public identifiers, ranks, and winning cutoff prices. Private financial identifiers, wallet addresses, ledger IDs, and loser identities are never revealed publicly. Individual bidders inspect their private results via the persistent `[📍 View My Result]` button.
- **Financial Settlement Independence**: Settlement occurs first in the database. If a Discord announcement fails due to network or channel permissions, financial reconciliation is never broken or rolled back.

---

## 2. Database Schema & Migration (`005_guild_channel_configuration`)

### `guild_configs` Table
| Column | Type | Description |
|---|---|---|
| `guild_id` | `VARCHAR(32)` | Primary Key (Discord Guild Snowflake) |
| `tasks_channel_id` | `VARCHAR(32)` | Configured channel for OBX Task Center |
| `leaderboard_channel_id` | `VARCHAR(32)` | Configured channel for Public Leaderboard |
| `auctions_channel_id` | `VARCHAR(32)` | Configured channel for Active Auctions & Whitelist Drops |
| `winners_channel_id` | `VARCHAR(32)` | Configured channel for Winner Announcements & Settlement Cards |
| `admin_channel_id` | `VARCHAR(32)` | Configured channel for Admin Control & Alerts |
| `economy_channel_id` | `VARCHAR(32)` | Optional channel for economy activity notifications |
| `updated_at` | `TIMESTAMPTZ` | Timestamp of last configuration update |
| `updated_by` | `VARCHAR(64)` | Admin Discord User ID who performed update |

### `published_messages` Table
| Column | Type | Description |
|---|---|---|
| `id` | `UUID` | Primary Key |
| `guild_id` | `VARCHAR(32)` | Discord Guild Snowflake (Indexed) |
| `feature_type` | `VARCHAR(64)` | `TASK_DASHBOARD`, `LEADERBOARD`, `AUCTION`, `AUCTION_RESULTS` |
| `source_id` | `VARCHAR(64)` | Optional entity ID (e.g. Auction UUID), defaults to `DEFAULT` |
| `channel_id` | `VARCHAR(32)` | Channel snowflake where message was posted |
| `message_id` | `VARCHAR(32)` | Discord message snowflake |
| `created_at` | `TIMESTAMPTZ` | Creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | Last edit/refresh timestamp |

**Unique Constraint**: `uq_published_msg_guild_feature_src` on `(guild_id, feature_type, source_id)`

---

## 3. Channel Routing Rules & Workflow

```
       Admin Hub -> [🏗️ Configure Channels]
                        ↓
            Select Target Channel
                        ↓
         Verify Bot Channel Permissions
          (View, Send, Embed Links)
                        ↓
          Save to PostgreSQL Database
                        ↓
         Auto-Deploy / Edit Existing Card
```

### Destination Channels
1. **🎯 Tasks Channel (`#obx-tasks`)**:
   - Houses the persistent interactive Task Center.
   - Allows members to browse tasks, submit work via modals, and check balances.
2. **🏆 Leaderboard Channel (`#obx-leaderboard`)**:
   - Houses the persistent public leaderboard.
   - Categorized by Total OBX, Task Earnings, and Completed Tasks.
3. **🔨 Auctions Channel (`#obx-auctions`)**:
   - Active FCFS Whitelist Sales and Multi-Winner GTD Auctions automatically post here on creation.
   - Members place and update bids or claim spots directly.
4. **🏅 Winners Channel (`#obx-winners`)**:
   - Multi-winner GTD auction settlement summaries automatically post here upon conclusion.
   - Features `[📍 View My Result]` for ephemeral personalized outcome lookup.
5. **🔒 Admin Channel (`#obx-admin`)**:
   - Administrative review queue and alerts.

---

## 4. Bot Startup Recovery & Persistent Views

On bot startup (`on_ready` / `setup_hook`):
1. `OBXDashboardView`, `LeaderboardView`, `AuctionCenterView`, and `AuctionWinnerResultView` are registered with `timeout=None`.
2. All configured channels are fetched and verified.
3. Public dashboards and leaderboards are refreshed in-place without spamming duplicate messages.
4. If a channel is inaccessible or deleted, the error is safely caught and logged without crashing the daemon.

---

## 5. Automated Verification Results

- **Unit & Integration Tests**: 134/134 passed (100%) on SQLite and PostgreSQL 16.
- **Double-Entry Ledger Reconciliation**: 100% consistent across all accounts.
