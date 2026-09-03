# Discord Bot Setup & Private Testing Guide

This guide provides instructions for configuring and running the **OBX Social Task Bot** in a private Discord test server.

---

## 1. Create Discord Application & Bot

1. Navigate to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click **New Application** in the top right. Name it `OBX Economy Bot` and accept the Terms of Service.
3. In the left sidebar, navigate to **Bot**:
   - Click **Reset Token** (or **Add Bot**) to generate your `DISCORD_BOT_TOKEN`.
   - Copy the token and save it safely in your `.env` file (never commit tokens).
4. Under **Privileged Gateway Intents**:
   - Enable **Server Members Intent** (required for admin role verification).
   - Enable **Message Content Intent**.
5. Save Changes.

---

## 2. Generate Bot Invite URL

1. In the left sidebar, navigate to **OAuth2** $\to$ **URL Generator**.
2. Under **SCOPES**, select:
   - `bot`
   - `applications.commands`
3. Under **BOT PERMISSIONS**, select:
   - `Send Messages`
   - `Embed Links`
   - `Attach Files`
   - `Read Message History`
   - `Use Slash Commands`
   - `Manage Roles` (Optional)
4. Copy the generated URL at the bottom of the page.
5. Open the URL in your browser and invite the bot to your private test server.

---

## 3. Configure Server & Roles

1. In your Discord Test Server:
   - Create an **Admin** role (e.g. `@OBX Admin`).
   - Assign the role to your test admin account.
   - Right-click the server icon $\to$ **Copy Server ID** (enable Developer Mode in Discord User Settings $\to$ Advanced if needed).
   - Right-click the Admin role $\to$ **Copy Role ID**.

---

## 4. Environment Variables (`.env`)

Add the following values to your `.env` file:

```env
# Database Configuration
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/obx_economy

# OBX Core Configuration
OBX_CORE_API_URL=http://localhost:8000
OBX_CORE_INTERNAL_AUTH_TOKEN=development_secret_token_123

# Discord Bot Configuration
DISCORD_BOT_TOKEN=your_real_bot_token_here
DISCORD_GUILD_ID=123456789012345678
DISCORD_ADMIN_ROLE_IDS=987654321098765432
```

> [!NOTE]
> Setting `DISCORD_GUILD_ID` immediately synchronizes slash commands to your test guild upon startup, bypassing Discord's 1-hour global command caching delay.

---

## 5. Running the Complete System

### Step 1: Start PostgreSQL
```bash
brew services start postgresql@16
# Or via Docker:
# docker compose up -d db
```

### Step 2: Apply Migrations
```bash
alembic -c packages/database/alembic.ini upgrade head
```

### Step 3: Start OBX Core API (Port 8000)
```bash
python3 apps/obx_core/main.py
```

### Step 4: Start OBX Tasks API (Port 8001)
```bash
python3 apps/obx_tasks/main.py
```

### Step 5: Start Discord Bot
```bash
python3 apps/obx_tasks/bot/main.py
```

---

## 6. Available Slash Commands

### User Commands
- `/tasks`: Lists all active social tasks and available reward pools.
- `/task <task_id>`: Displays detailed task instructions, reward per user, and end dates.
- `/submit [task_id]`: Opens interactive proof submission modal (`X handle`, `proof URL`, `context`).
- `/my-submissions`: Displays user's submission history and awarded OBX.

### Administrator Commands (Restricted by Role)
- `/admin-create-task`: Creates a new social task with reward pool.
- `/admin-submissions`: Displays pending submissions with interactive `[✅ Approve]` and `[❌ Reject]` buttons.
- `/admin-review <submission_id> <action> [reason]`: Approves or rejects a submission.
- `/admin-task-edit <task_id>`: Modifies pool size, reward per user, title, or status.
- `/admin-task-status <task_id> <status>`: Pauses, resumes, completes, or cancels a task.
- `/admin-task-history <task_id>`: Displays configuration change audit trail.
