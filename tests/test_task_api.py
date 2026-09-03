import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from packages.database.session import get_db
from packages.shared.config import get_settings
from apps.obx_tasks.main import create_task_app


@pytest.fixture(scope="function")
def task_client(test_engine, db_session):
    app = create_task_app()

    SessionLocal = sessionmaker(
        bind=test_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    def override_get_db():
        session = SessionLocal()
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers():
    settings = get_settings()
    return {"X-Internal-Token": settings.OBX_CORE_INTERNAL_AUTH_TOKEN}


def test_tasks_health_endpoint(task_client):
    response = task_client.get("/tasks/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_create_task_admin_auth_required(task_client):
    payload = {
        "title": "Auth Test",
        "description": "Desc",
        "task_type": "RETWEET",
        "target_url": "https://x.com/post",
        "reward_per_user": 100,
        "total_reward_pool": 1000,
        "created_by": "admin_1",
    }
    # No auth header -> 401
    resp_no_auth = task_client.post("/tasks", json=payload)
    assert resp_no_auth.status_code == 401

    # Bad auth header -> 401
    resp_bad_auth = task_client.post("/tasks", json=payload, headers={"X-Internal-Token": "wrong"})
    assert resp_bad_auth.status_code == 401


def test_task_creation_and_retrieval(task_client, auth_headers):
    payload = {
        "title": "API Task 1",
        "description": "Retweet the release",
        "task_type": "RETWEET",
        "target_url": "https://x.com/obx/status/1",
        "reward_per_user": 200,
        "total_reward_pool": 1000,
        "created_by": "admin_founder",
    }

    create_resp = task_client.post("/tasks", json=payload, headers=auth_headers)
    assert create_resp.status_code == 201
    task_data = create_resp.json()
    task_id = task_data["id"]
    assert task_data["title"] == "API Task 1"
    assert task_data["remaining_reward_pool"] == 1000

    # Retrieve by ID
    get_resp = task_client.get(f"/tasks/{task_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == task_id

    # List tasks
    list_resp = task_client.get("/tasks")
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] >= 1


def test_task_edit_and_audit_logs_api(task_client, auth_headers):
    # 1. Create task
    task_resp = task_client.post("/tasks", json={
        "title": "Editable Task",
        "description": "Initial desc",
        "task_type": "RETWEET",
        "target_url": "https://x.com/1",
        "reward_per_user": 100,
        "total_reward_pool": 5000,
        "created_by": "admin",
    }, headers=auth_headers)
    task_id = task_resp.json()["id"]

    # 2. Edit task (increase pool, change reward, pause)
    patch_resp = task_client.patch(
        f"/tasks/{task_id}",
        json={
            "admin_id": "editor_admin",
            "title": "Edited Task Title",
            "total_reward_pool": 10000,
            "reward_per_user": 250,
            "status": "PAUSED",
        },
        headers=auth_headers,
    )
    assert patch_resp.status_code == 200
    updated_data = patch_resp.json()
    assert updated_data["title"] == "Edited Task Title"
    assert updated_data["total_reward_pool"] == 10000
    assert updated_data["reward_per_user"] == 250
    assert updated_data["status"] == "PAUSED"

    # 3. Retrieve audit logs
    audit_resp = task_client.get(f"/tasks/{task_id}/audit-logs", headers=auth_headers)
    assert audit_resp.status_code == 200
    audit_data = audit_resp.json()
    assert audit_data["total"] >= 4


def test_full_submission_and_approval_api_flow(task_client, auth_headers):
    # 1. Admin creates task
    task_resp = task_client.post("/tasks", json={
        "title": "Flow Task",
        "description": "Comment on tweet",
        "task_type": "COMMENT",
        "target_url": "https://x.com/obx/status/2",
        "reward_per_user": 100,
        "total_reward_pool": 500,
        "created_by": "admin",
    }, headers=auth_headers)
    task_id = task_resp.json()["id"]

    # 2. User submits proof
    sub_resp = task_client.post(f"/tasks/{task_id}/submissions", json={
        "discord_user_id": "api_user_flow_1",
        "x_username": "flow_x_user",
        "proof_url": "https://x.com/flow_x/status/99",
        "proof_text": "Great update!",
    })
    assert sub_resp.status_code == 201
    sub_data = sub_resp.json()
    sub_id = sub_data["id"]
    assert sub_data["status"] == "PENDING"

    # 3. Duplicate submission fails -> 409
    dup_resp = task_client.post(f"/tasks/{task_id}/submissions", json={
        "discord_user_id": "api_user_flow_1",
        "x_username": "flow_x_user",
        "proof_url": "https://x.com/flow_x/status/99",
        "proof_text": "Duplicate attempt",
    })
    assert dup_resp.status_code == 409

    # 4. Admin approves submission
    approve_resp = task_client.post(
        f"/submissions/{sub_id}/approve",
        json={"reviewer_discord_id": "admin_reviewer"},
        headers=auth_headers,
    )
    assert approve_resp.status_code == 200
    approved_data = approve_resp.json()
    assert approved_data["status"] == "APPROVED"
    assert approved_data["reward_amount"] == 100
    assert approved_data["obx_transaction_id"] is not None

    # 5. Check user submissions endpoint
    user_subs_resp = task_client.get("/users/api_user_flow_1/submissions")
    assert user_subs_resp.status_code == 200
    assert user_subs_resp.json()["total"] == 1


def test_edit_task_unauthorized_rejected(task_client):
    resp = task_client.patch(
        "/tasks/00000000-0000-0000-0000-000000000000",
        json={"title": "Unauthorized"},
    )
    assert resp.status_code == 401
