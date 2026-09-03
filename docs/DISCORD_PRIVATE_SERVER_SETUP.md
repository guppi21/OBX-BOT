# Discord Private Server Setup & Deployment Guide

This guide is designed for developers and administrators setting up the **OBX Social Task Bot** in a private Discord server for the first time.

---

## Prerequisites
- A Discord account.
- A Discord server where you have Administrator permissions (create a free server for testing if needed).
- Python 3.12+ and PostgreSQL 16 running locally or in Docker.

---

## Step-by-Step Discord Bot Setup

### Step 1: Open the Discord Developer Portal
Navigate to [https://discord.com/developers/applications](https://discord.com/developers/applications) and log in with your Discord account.

### Step 2: Create a New Application
1. Click the blue **New Application** button in the top-right corner.
2. Enter a name for your application (e.g., `OBX Task Bot`).
3. Agree to the Developer Terms of Service and click **Create**.

### Step 3: Add a Bot to Your Application
1. In the left navigation menu, click **Bot**.
2. Click **Add Bot** (if prompted) or proceed to the Bot settings.

### Step 4: Copy the Application ID
1. In the left navigation menu, click **General Information**.
2. Locate **Application ID** and click **Copy**. Save this ID.

### Step 5: Reset and Copy the Bot Token
1. Return to the **Bot** tab on the left.
2. Under the bot's username, click **Reset Token** (or **Copy**).
3. If prompted, enter your Discord password / 2FA code.
4. Copy the generated token immediately.

> [!CAUTION]
> **Keep Your Token Secret!**
> The bot token is like a password with full control of your bot. Never share it publicly, commit it to GitHub, or post it in chat channels. Store it safely in your local `.env` file only.

### Step 6: Configure Privileged Gateway Intents
1. On the **Bot** page, scroll down to the **Privileged Gateway Intents** section.
2. Toggle on:
   - **Server Members Intent** (Required to verify user roles for admin authorization).
   - **Message Content Intent**.
3. Click **Save Changes** at the bottom of the page.

### Step 7: Configure OAuth2 Installation & Permissions
1. In the left navigation menu, click **OAuth2** $\to$ **URL Generator**.
2. Under **SCOPES**, check:
   - `bot`
   - `applications.commands` (Enables slash commands).
3. Under **BOT PERMISSIONS**, check:
   - `Send Messages`
   - `Embed Links`
   - `Attach Files`
   - `Read Message History`
   - `Use Slash Commands`
4. Copy the generated invite link at the bottom of the page.

### Step 8: Install the Bot into Your Private Test Server
1. Paste the generated OAuth2 invite link into your web browser.
2. Select your private Discord test server from the dropdown list.
3. Click **Continue**, review the requested permissions, and click **Authorize**.
4. Complete the Captcha if prompted.
5. You should now see the bot appear in the member list of your Discord server (initially offline).

---

## Step 9: Configure Discord Server & Role IDs

### Step 9.1: Enable Developer Mode in Discord
1. In the Discord desktop or web app, click the gear icon (⚙️) next to your profile at the bottom-left to open **User Settings**.
2. Under **App Settings** in the left sidebar, click **Advanced**.
3. Toggle ON **Developer Mode**.

### Step 9.2: Copy Your Server ID (`DISCORD_GUILD_ID`)
1. In Discord, right-click on your private server's icon in the left server list.
2. Click **Copy Server ID**.
3. This is your `DISCORD_GUILD_ID` (e.g., `123456789012345678`).

### Step 9.3: Create and Assign the `OBX Admin` Role
1. In your Discord server, open **Server Settings** (click server name dropdown at top-left $\to$ **Server Settings**).
2. Go to **Roles** $\to$ Click **Create Role**.
3. Name the role `OBX Admin` (or `Admin`).
4. Give it a distinctive color and save changes.
5. In **Server Settings** $\to$ **Members**, assign the `OBX Admin` role to your administrator test account.

### Step 9.4: Copy Your Role ID (`DISCORD_ADMIN_ROLE_ID`)
1. In **Server Settings** $\to$ **Roles**, find your `OBX Admin` role.
2. Click the three dots (`...`) next to the role and select **Copy Role ID**.
3. This is your `DISCORD_ADMIN_ROLE_ID` (e.g., `987654321098765432`).

---

## Step 10: Configure Environment Variables (`.env`)

Create or update the `.env` file in the root of the project:

```env
# Database URL
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/obx_economy

# Application Environment
ENVIRONMENT=development
LOG_LEVEL=INFO

# OBX Core Service (Port 8000)
API_HOST=0.0.0.0
API_PORT=8000
OBX_CORE_API_URL=http://localhost:8000
OBX_CORE_INTERNAL_AUTH_TOKEN=development_secret_token_123

# OBX Tasks Service (Port 8001)
TASK_SERVICE_PORT=8001

# Discord Bot Credentials
DISCORD_BOT_TOKEN=your_copied_bot_token_here
DISCORD_GUILD_ID=your_copied_server_id_here
DISCORD_ADMIN_ROLE_ID=your_copied_role_id_here
```

---

## Step 11: Launch the Services & Confirm Operation

### Step 11.1: Start PostgreSQL and Apply Migrations
```bash
# Start PostgreSQL (Homebrew or Docker)
brew services start postgresql@16

# Apply migrations
alembic -c packages/database/alembic.ini upgrade head
```

### Step 11.2: Start OBX Core API (Terminal 1)
```bash
python3 apps/obx_core/main.py
```

### Step 11.3: Start OBX Tasks API (Terminal 2)
```bash
python3 apps/obx_tasks/main.py
```

### Step 11.4: Start the Discord Bot (Terminal 3)
```bash
python3 apps/obx_tasks/bot/main.py
```

---

## Confirming Success

When the bot starts, the console logs should display:

```
[INFO] OBX Discord bot starting...
[INFO] Database connection status: OK (5432/obx_economy)
[INFO] Configured test guild ID: 123456789012345678 (instant command sync enabled)
[INFO] Configured admin role IDs: ['987654321098765432']
[INFO] Test guild synchronized: Guild ID=123456789012345678
[INFO] Slash commands synchronized successfully: 10 commands registered
[INFO] Discord connected successfully
[INFO] Connected as: OBX Task Bot#1234 (ID: 112233445566778899)
[INFO] OBX Discord bot ready
```

In Discord:
1. The bot's status indicator should show a green online dot.
2. Typing `/` in any channel will display the registered slash commands:
   - `/tasks`
   - `/task`
   - `/submit`
   - `/my-submissions`
   - `/admin-create-task`
   - `/admin-submissions`
   - `/admin-review`
   - `/admin-task-edit`
   - `/admin-task-status`
   - `/admin-task-history`

---

## Troubleshooting Guide

| Issue | Cause | Solution |
| :--- | :--- | :--- |
| **Bot is Offline** | Bot process is not running or wrong token | Verify `python3 apps/obx_tasks/bot/main.py` is running and `DISCORD_BOT_TOKEN` in `.env` is correct. |
| **Invalid Token Error** | Token was reset or typo during copy | Reset token in Developer Portal $\to$ Bot tab and update `.env`. |
| **Slash Commands Do Not Appear** | `DISCORD_GUILD_ID` missing or incorrect | Check `DISCORD_GUILD_ID` in `.env`. Without a guild ID, global registration takes up to 1 hour to propagate. |
| **Permission Denied / Admin Role Not Detected** | User lacks `OBX Admin` role or wrong `DISCORD_ADMIN_ROLE_ID` | Check that your Discord user has the role assigned in Server Settings, and that `DISCORD_ADMIN_ROLE_ID` matches the copied Role ID. |
| **Privileged Intents Error (`PrivilegedIntentsRequired`)** | Gateway intents not enabled | Go to Discord Developer Portal $\to$ Bot tab $\to$ Enable **Server Members Intent** and **Message Content Intent**. |
| **Database Connection Failure** | PostgreSQL is stopped or invalid URL | Check that PostgreSQL is running (`brew services list` or `docker ps`) and `DATABASE_URL` is reachable. |
| **Interactions Expire / Fail** | Slow response exceeding Discord 3s timeout | Ensure `defer(ephemeral=...)` is active in bot command callbacks (already handled by bot architecture). |
