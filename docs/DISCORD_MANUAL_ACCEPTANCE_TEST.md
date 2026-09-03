# Discord Bot Manual Acceptance Test Checklist (Phase 2B.4 — Button-First UX)

This checklist is the official manual acceptance test plan to be executed inside a private Discord test server by the operator.

---

## 1. Roles & Accounts Required

- **`OBX Admin` Role**: Configured in `.env` as `DISCORD_ADMIN_ROLE_ID` (e.g. `Haveli Owner`).
- **Account 1 (Admin)**: Real Discord user account assigned the `OBX Admin` role.
- **Account 2 (User A)**: Real Discord user account without administrative permissions (standard Member).
- **Account 3 (User B)**: Second standard Member account (recommended).

---

## 2. Manual Acceptance Scenarios

### Scenario A — Post Dashboard & Button Visibility
- [ ] **A.1**: As **Admin**, run `/admin-post-dashboard` in your designated tasks channel.
- [ ] **A.2**: Verify the **🏆 OBX TASK CENTER** embed appears with 7 interactive buttons:
  - Row 0: `[📋 Browse Tasks]`, `[📤 Submit Task]`, `[💰 My OBX]`, `[📜 My Submissions]`
  - Row 1: `[➕ Create Task]`, `[🔍 Review Submissions]`, `[🛠️ System Health]`

---

### Scenario B — Interactive Task Creation via Button
- [ ] **B.1**: As **User A** (non-admin), click `[➕ Create Task]` $\to$ verify ephemeral message: `❌ Permission Denied: Only administrators with the configured OBX Admin role can create tasks.`
- [ ] **B.2**: As **Admin**, click `[➕ Create Task]` $\to$ verify modal opens with:
  - Task Title, Target URL, Reward Per User, Total Pool, Instructions.
- [ ] **B.3**: Complete and submit the modal $\to$ verify green confirmation card is returned with Task ID, Pool, and Max Approvals.

---

### Scenario C — Browse Tasks & Direct Submission
- [ ] **C.1**: As **User A**, click `[📋 Browse Tasks]` $\to$ verify ephemeral list of active tasks with dropdown selector.
- [ ] **C.2**: Select the task from the dropdown $\to$ verify task instructions, reward, pool, and target URL appear with an enabled `[📤 Submit This Task]` button.
- [ ] **C.3**: Click `[📤 Submit This Task]` $\to$ verify `TaskSubmitModal` opens with the selected task pre-bound (no copying UUIDs needed).
- [ ] **C.4**: Enter X Handle, Proof URL, and Context $\to$ submit $\to$ verify ephemeral confirmation with Status `PENDING REVIEW`.

---

### Scenario D — Direct Submit Button & Fast Submission
- [ ] **D.1**: As **User B**, click `[📤 Submit Task]` on the dashboard.
- [ ] **D.2**: Select the task from the dropdown $\to$ verify `TaskSubmitModal` immediately opens.
- [ ] **D.3**: Complete proof submission $\to$ verify status `PENDING`.

---

### Scenario E — Wallet Balance & Submission Tracking Buttons
- [ ] **E.1**: As **User A**, click `[💰 My OBX]` $\to$ verify ephemeral card displays Available Balance, Locked Balance, and Total Balance.
- [ ] **E.2**: As **User A**, click `[📜 My Submissions]` $\to$ verify your pending submission is displayed with status badge `⏳ PENDING`.

---

### Scenario F — Admin Review & Approval via Dashboard Button
- [ ] **F.1**: As **User A** (non-admin), click `[🔍 Review Submissions]` $\to$ verify ephemeral access denial.
- [ ] **F.2**: As **Admin**, click `[🔍 Review Submissions]` $\to$ verify pending review queue appears with `[✅ Approve (Distribute OBX)]` & `[❌ Reject]` buttons.
- [ ] **F.3**: Click `[✅ Approve (Distribute OBX)]` on User A's submission.
- [ ] **F.4**: Verify confirmation embed: `Successfully approved submission! Awarded 100 OBX. OBX Transaction ID: <uuid>`.
- [ ] **F.5**: Verify review buttons become disabled.
- [ ] **F.6**: As **User A**, click `[💰 My OBX]` $\to$ verify wallet balance increased by the reward amount.
- [ ] **F.7**: As **User A**, click `[📜 My Submissions]` $\to$ verify submission badge is now `✅ APPROVED`.

---

### Scenario G — Anti-Self-Approval & Duplicate Rejection
- [ ] **G.1**: As **Admin**, click `[📤 Submit Task]` and submit proof for yourself.
- [ ] **G.2**: As **Admin**, click `[🔍 Review Submissions]` and click `[✅ Approve]` on your own submission.
- [ ] **G.3**: Verify rejection: `Anti-Self-Approval Rule: You cannot approve your own submission.`
- [ ] **G.4**: As **User A**, click `[📤 Submit Task]` and attempt to submit for the same task again $\to$ verify duplicate submission rejection.

---

### Scenario H — Persistent Dashboard Across Bot Restarts
- [ ] **H.1**: Restart the Discord bot process.
- [ ] **H.2**: Without posting a new dashboard, click `[📋 Browse Tasks]` or `[💰 My OBX]` on the **existing dashboard message**.
- [ ] **H.3**: Verify buttons respond instantly (persistent view with stable custom IDs).

---

### Scenario I — System-Wide Wallet Reconciliation
- [ ] **I.1**: Run reconciliation: `python3 apps/obx_core/cli.py reconcile`.
- [ ] **I.2**: Verify: `Reconciliation Passed: All user wallets are 100% consistent (0 mismatches)`.
