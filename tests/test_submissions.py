import pytest
from datetime import datetime, timezone, timedelta
from packages.shared.enums import TaskStatus, TaskType, SubmissionStatus
from packages.shared.exceptions import (
    TaskNotActiveError,
    TaskExpiredError,
    DuplicateSubmissionError,
    SubmissionNotFoundError,
)
from apps.obx_tasks.services.task_service import TaskService


@pytest.fixture
def task_service(db_session):
    return TaskService(db_session)


def test_submit_active_task_success(task_service):
    task = task_service.create_task("T1", "D1", TaskType.RETWEET, "https://x.com/1", 100, 1000, "adm")
    sub = task_service.submit_task(
        task_id=task.id,
        discord_user_id="user_sub_1",
        x_username="alice_x",
        proof_url="https://x.com/alice_x/status/999",
        proof_text="I retweeted this!",
        proof_screenshot_url="https://cdn.discord.com/proof1.png",
    )

    assert sub.id is not None
    assert sub.task_id == task.id
    assert sub.discord_user_id == "user_sub_1"
    assert sub.x_username == "alice_x"
    assert sub.status == SubmissionStatus.PENDING
    assert sub.reward_amount is None
    assert sub.obx_transaction_id is None


def test_submit_inactive_task_rejected(task_service):
    task = task_service.create_task("T1", "D1", TaskType.RETWEET, "https://x.com/1", 100, 1000, "adm", status=TaskStatus.PAUSED)
    with pytest.raises(TaskNotActiveError):
        task_service.submit_task(
            task_id=task.id,
            discord_user_id="user_sub_2",
            x_username="bob_x",
            proof_url="https://x.com/bob_x/status/888",
            proof_text="Proof text",
        )


def test_submit_expired_task_rejected(task_service):
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    task = task_service.create_task(
        "T1", "D1", TaskType.COMMENT, "https://x.com/1", 100, 1000, "adm",
        ends_at=past,
    )

    with pytest.raises(TaskExpiredError):
        task_service.submit_task(
            task_id=task.id,
            discord_user_id="user_sub_3",
            x_username="carol_x",
            proof_url="https://x.com/carol_x/status/777",
            proof_text="Proof text",
        )


def test_duplicate_submission_rejected(task_service):
    task = task_service.create_task("T1", "D1", TaskType.RETWEET, "https://x.com/1", 100, 1000, "adm")
    
    # First submission
    task_service.submit_task(
        task_id=task.id,
        discord_user_id="user_dup_1",
        x_username="dave_x",
        proof_url="https://x.com/dave_x/status/111",
        proof_text="Proof 1",
    )

    # Duplicate submission
    with pytest.raises(DuplicateSubmissionError):
        task_service.submit_task(
            task_id=task.id,
            discord_user_id="user_dup_1",
            x_username="dave_x_alt",
            proof_url="https://x.com/dave_x/status/222",
            proof_text="Proof 2",
        )


def test_get_submission_not_found(task_service):
    with pytest.raises(SubmissionNotFoundError):
        task_service.get_submission("00000000-0000-0000-0000-000000000000")


def test_list_submissions_filters(task_service):
    task1 = task_service.create_task("T1", "D1", TaskType.RETWEET, "https://x.com/1", 100, 1000, "adm")
    task2 = task_service.create_task("T2", "D2", TaskType.COMMENT, "https://x.com/2", 100, 1000, "adm")

    task_service.submit_task(task1.id, "u1", "x1", "https://p1", "txt1")
    task_service.submit_task(task1.id, "u2", "x2", "https://p2", "txt2")
    task_service.submit_task(task2.id, "u1", "x1", "https://p3", "txt3")

    # Filter by task1
    subs_t1, total_t1 = task_service.list_submissions(task_id=task1.id)
    assert total_t1 == 2

    # Filter by user u1
    subs_u1, total_u1 = task_service.list_submissions(discord_user_id="u1")
    assert total_u1 == 2
