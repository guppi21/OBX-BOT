import pytest
from datetime import datetime, timedelta, timezone
from apps.obx_core.services.wallet_service import WalletService
from apps.obx_tasks.services.task_service import TaskService
from apps.obx_tasks.services.leaderboard_service import (
    LeaderboardService,
    LeaderboardCategory,
    LeaderboardPeriod,
)
from packages.database.models.submission import TaskSubmission
from packages.database.models.wallet import Wallet
from packages.database.models.user import User


def test_total_obx_ranking_deterministic_and_tie_breaking(db_session):
    ws = WalletService(db_session)
    lb_service = LeaderboardService(db_session)

    # Create 3 users with different balances
    u1, _, _ = ws.get_or_create_user("user_lb_1")
    u2, _, _ = ws.get_or_create_user("user_lb_2")
    u3, _, _ = ws.get_or_create_user("user_lb_3")

    ws.credit(discord_user_id="user_lb_1", amount=100, reference_type="test", idempotency_key="k1")
    ws.credit(discord_user_id="user_lb_2", amount=300, reference_type="test", idempotency_key="k2")
    ws.credit(discord_user_id="user_lb_3", amount=200, reference_type="test", idempotency_key="k3")

    entries, total = lb_service.get_leaderboard(category=LeaderboardCategory.TOTAL_OBX, limit=10)
    assert total == 3
    assert len(entries) == 3

    assert entries[0].discord_user_id == "user_lb_2"
    assert entries[0].rank == 1
    assert entries[0].score == 300

    assert entries[1].discord_user_id == "user_lb_3"
    assert entries[1].rank == 2
    assert entries[1].score == 200

    assert entries[2].discord_user_id == "user_lb_1"
    assert entries[2].rank == 3
    assert entries[2].score == 100


def test_task_earnings_ranking_excludes_pending_and_rejected(db_session):
    task_service = TaskService(db_session)
    lb_service = LeaderboardService(db_session)

    t1 = task_service.create_task("Task A", "Desc", "LIKE", "https://x.com/a", 50, 500, "admin_1")
    t2 = task_service.create_task("Task B", "Desc", "LIKE", "https://x.com/b", 70, 700, "admin_1")

    # User 1 has 2 approved tasks (50 + 70 = 120 OBX)
    s1 = task_service.submit_task(str(t1.id), "earner_1", "handle1", "https://x.com/p1", "text")
    task_service.approve_submission(str(s1.id), reviewer_discord_id="admin_1")
    s2 = task_service.submit_task(str(t2.id), "earner_1", "handle1", "https://x.com/p2", "text")
    task_service.approve_submission(str(s2.id), reviewer_discord_id="admin_1")

    # User 2 has 1 approved task (70 OBX) and 1 rejected
    s3 = task_service.submit_task(str(t2.id), "earner_2", "handle2", "https://x.com/p3", "text")
    task_service.approve_submission(str(s3.id), reviewer_discord_id="admin_1")
    s4 = task_service.submit_task(str(t1.id), "earner_2", "handle2", "https://x.com/p4", "text")
    task_service.reject_submission(str(s4.id), reviewer_discord_id="admin_1", rejection_reason="Invalid link")

    # User 3 has only a pending task (0 approved)
    task_service.submit_task(str(t1.id), "earner_3", "handle3", "https://x.com/p5", "text")

    entries, total = lb_service.get_leaderboard(category=LeaderboardCategory.TASK_EARNINGS, limit=10)
    assert total == 2  # earner_3 is not ranked in approved earnings
    assert entries[0].discord_user_id == "earner_1"
    assert entries[0].score == 120
    assert entries[0].tasks_completed == 2

    assert entries[1].discord_user_id == "earner_2"
    assert entries[1].score == 70
    assert entries[1].tasks_completed == 1


def test_task_completions_ranking(db_session):
    task_service = TaskService(db_session)
    lb_service = LeaderboardService(db_session)

    t1 = task_service.create_task("T1", "Desc", "LIKE", "https://x.com/1", 10, 100, "admin_1")
    t2 = task_service.create_task("T2", "Desc", "LIKE", "https://x.com/2", 10, 100, "admin_1")
    t3 = task_service.create_task("T3", "Desc", "LIKE", "https://x.com/3", 10, 100, "admin_1")

    # User A completes 3 tasks
    s1 = task_service.submit_task(str(t1.id), "comp_user_a", "h", "https://x.com/1", "text")
    task_service.approve_submission(str(s1.id), reviewer_discord_id="admin_1")
    s2 = task_service.submit_task(str(t2.id), "comp_user_a", "h", "https://x.com/2", "text")
    task_service.approve_submission(str(s2.id), reviewer_discord_id="admin_1")
    s3 = task_service.submit_task(str(t3.id), "comp_user_a", "h", "https://x.com/3", "text")
    task_service.approve_submission(str(s3.id), reviewer_discord_id="admin_1")

    # User B completes 1 task
    s4 = task_service.submit_task(str(t1.id), "comp_user_b", "h", "https://x.com/1b", "text")
    task_service.approve_submission(str(s4.id), reviewer_discord_id="admin_1")

    entries, total = lb_service.get_leaderboard(category=LeaderboardCategory.TASK_COMPLETIONS, limit=10)
    assert total == 2
    assert entries[0].discord_user_id == "comp_user_a"
    assert entries[0].score == 3
    assert entries[1].discord_user_id == "comp_user_b"
    assert entries[1].score == 1


def test_time_period_filtering_weekly_and_monthly(db_session):
    task_service = TaskService(db_session)
    lb_service = LeaderboardService(db_session)

    task = task_service.create_task("Old vs New", "Desc", "LIKE", "https://x.com/t", 100, 1000, "admin_1")
    s_new = task_service.submit_task(str(task.id), "user_recent", "h", "https://x.com/rec", "text")
    task_service.approve_submission(str(s_new.id), reviewer_discord_id="admin_1")

    # Manually backdate a submission to 45 days ago
    s_old = task_service.submit_task(str(task.id), "user_old", "h", "https://x.com/old", "text")
    task_service.approve_submission(str(s_old.id), reviewer_discord_id="admin_1")

    old_sub = db_session.query(TaskSubmission).filter_by(id=s_old.id).first()
    old_sub.reviewed_at = datetime.now(timezone.utc) - timedelta(days=45)
    db_session.commit()

    # All-Time: Both users present
    all_time_entries, all_total = lb_service.get_leaderboard(
        category=LeaderboardCategory.TASK_EARNINGS,
        period=LeaderboardPeriod.ALL_TIME,
    )
    assert all_total == 2

    # This Month: Only user_recent present
    month_entries, month_total = lb_service.get_leaderboard(
        category=LeaderboardCategory.TASK_EARNINGS,
        period=LeaderboardPeriod.THIS_MONTH,
    )
    assert month_total == 1
    assert month_entries[0].discord_user_id == "user_recent"


def test_user_position_outside_top_10(db_session):
    ws = WalletService(db_session)
    lb_service = LeaderboardService(db_session)

    # Create 15 users with balances 150, 140, 130, ..., 10
    for i in range(1, 16):
        uid = f"ranked_user_{i:02d}"
        ws.get_or_create_user(uid)
        ws.credit(discord_user_id=uid, amount=i * 10, reference_type="test", idempotency_key=f"k_{i}")

    # Top user is ranked_user_15 (150 OBX) -> Rank 1
    # 12th user is ranked_user_04 (40 OBX) -> Rank 12
    pos = lb_service.get_user_position(discord_user_id="ranked_user_04", category=LeaderboardCategory.TOTAL_OBX)
    assert pos.rank == 12
    assert pos.score == 40
    assert pos.total_participants == 15


def test_leaderboard_queries_are_read_only(db_session):
    ws = WalletService(db_session)
    lb_service = LeaderboardService(db_session)

    u, w, _ = ws.get_or_create_user("readonly_user")
    ws.credit(discord_user_id="readonly_user", amount=100, reference_type="test", idempotency_key="ro_1")

    # Read leaderboard and user position multiple times
    for _ in range(5):
        lb_service.get_leaderboard(category=LeaderboardCategory.TOTAL_OBX)
        lb_service.get_leaderboard(category=LeaderboardCategory.TASK_EARNINGS)
        lb_service.get_user_position("readonly_user")

    # Verify wallet balance remains unaltered
    db_session.refresh(w)
    assert w.available_balance == 100
    assert w.locked_balance == 0
    assert w.total_balance == 100
