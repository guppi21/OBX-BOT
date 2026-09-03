import pytest
from datetime import datetime, timezone, timedelta
from packages.shared.enums import TaskStatus, TaskType
from packages.shared.exceptions import InvalidRewardPoolError, TaskNotFoundError
from apps.obx_tasks.services.task_service import TaskService


@pytest.fixture
def task_service(db_session):
    return TaskService(db_session)


def test_create_task_success(task_service):
    task = task_service.create_task(
        title="Retweet announcement",
        description="Retweet the main announcement tweet",
        task_type=TaskType.RETWEET,
        target_url="https://x.com/obx/status/123",
        reward_per_user=100,
        total_reward_pool=1000,
        created_by="admin_discord_1",
    )

    assert task.id is not None
    assert task.title == "Retweet announcement"
    assert task.reward_per_user == 100
    assert task.total_reward_pool == 1000
    assert task.distributed_reward == 0
    assert task.remaining_reward_pool == 1000
    assert task.max_approvals == 10
    assert task.approved_count == 0
    assert task.status == TaskStatus.ACTIVE


def test_create_task_invalid_reward_per_user(task_service):
    with pytest.raises(InvalidRewardPoolError):
        task_service.create_task(
            title="Invalid",
            description="desc",
            task_type=TaskType.RETWEET,
            target_url="https://x.com/obx",
            reward_per_user=0,
            total_reward_pool=1000,
            created_by="admin",
        )


def test_create_task_total_pool_smaller_than_reward_per_user(task_service):
    with pytest.raises(InvalidRewardPoolError):
        task_service.create_task(
            title="Invalid pool",
            description="desc",
            task_type=TaskType.COMMENT,
            target_url="https://x.com/obx",
            reward_per_user=500,
            total_reward_pool=200,
            created_by="admin",
        )


def test_get_task_not_found(task_service):
    with pytest.raises(TaskNotFoundError):
        task_service.get_task("00000000-0000-0000-0000-000000000000")


def test_list_tasks_filtering(task_service):
    task_service.create_task("T1", "D1", TaskType.RETWEET, "https://x.com/1", 100, 500, "adm", status=TaskStatus.ACTIVE)
    task_service.create_task("T2", "D2", TaskType.COMMENT, "https://x.com/2", 50, 200, "adm", status=TaskStatus.DRAFT)

    active_tasks, total_active = task_service.list_tasks(status=TaskStatus.ACTIVE)
    assert total_active == 1
    assert active_tasks[0].title == "T1"

    all_tasks, total_all = task_service.list_tasks()
    assert total_all == 2


def test_update_task_status(task_service):
    task = task_service.create_task("T1", "D1", TaskType.RETWEET, "https://x.com/1", 100, 500, "adm")
    assert task.status == TaskStatus.ACTIVE

    updated = task_service.update_task_status(task.id, TaskStatus.PAUSED)
    assert updated.status == TaskStatus.PAUSED
