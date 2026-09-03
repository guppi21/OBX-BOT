import pytest


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["database"] == "connected"


def test_get_or_create_user_endpoint(client):
    response = client.post("/users", json={"discord_user_id": "api_user_1"})
    assert response.status_code == 200
    data = response.json()
    assert data["discord_user_id"] == "api_user_1"
    assert "id" in data


def test_get_balance_endpoint(client):
    discord_id = "api_user_bal"
    client.post("/users", json={"discord_user_id": discord_id})

    # Credit initial funds
    client.post("/wallets/credit", json={
        "discord_user_id": discord_id,
        "amount": 750,
        "reference_type": "api_credit",
        "idempotency_key": "api_bal_k1",
    })

    response = client.get(f"/users/{discord_id}/balance")
    assert response.status_code == 200
    data = response.json()
    assert data["discord_user_id"] == discord_id
    assert data["available_balance"] == 750
    assert data["locked_balance"] == 0
    assert data["total_balance"] == 750


def test_get_transactions_endpoint(client):
    discord_id = "api_user_txs"
    client.post("/users", json={"discord_user_id": discord_id})

    for i in range(3):
        client.post("/wallets/credit", json={
            "discord_user_id": discord_id,
            "amount": 100 * (i + 1),
            "reference_type": "api_credit",
            "idempotency_key": f"tx_key_{i}",
        })

    response = client.get(f"/users/{discord_id}/transactions?limit=2&offset=0")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert len(data["transactions"]) == 2
    assert data["limit"] == 2
    assert data["offset"] == 0


def test_api_credit_and_debit_flow(client):
    discord_id = "api_user_cd_flow"
    
    # Credit 1000
    resp1 = client.post("/wallets/credit", json={
        "discord_user_id": discord_id,
        "amount": 1000,
        "reference_type": "airdrop",
        "idempotency_key": "cd_key_1",
        "description": "Community airdrop",
    })
    assert resp1.status_code == 200
    assert resp1.json()["amount"] == 1000

    # Debit 350
    resp2 = client.post("/wallets/debit", json={
        "discord_user_id": discord_id,
        "amount": 350,
        "reference_type": "shop_purchase",
        "idempotency_key": "cd_key_2",
    })
    assert resp2.status_code == 200
    assert resp2.json()["amount"] == 350

    # Check Balance
    resp3 = client.get(f"/users/{discord_id}/balance")
    assert resp3.json()["available_balance"] == 650
    assert resp3.json()["locked_balance"] == 0


def test_api_lock_and_release_flow(client):
    discord_id = "api_user_lr_flow"
    
    client.post("/wallets/credit", json={
        "discord_user_id": discord_id,
        "amount": 2000,
        "reference_type": "deposit",
        "idempotency_key": "lr_key_1",
    })

    # Lock 800
    lock_resp = client.post("/wallets/lock", json={
        "discord_user_id": discord_id,
        "amount": 800,
        "reference_type": "auction_bid",
        "idempotency_key": "lr_key_2",
    })
    assert lock_resp.status_code == 200

    bal1 = client.get(f"/users/{discord_id}/balance").json()
    assert bal1["available_balance"] == 1200
    assert bal1["locked_balance"] == 800

    # Release 300
    rel_resp = client.post("/wallets/release", json={
        "discord_user_id": discord_id,
        "amount": 300,
        "reference_type": "auction_outbid",
        "idempotency_key": "lr_key_3",
    })
    assert rel_resp.status_code == 200

    bal2 = client.get(f"/users/{discord_id}/balance").json()
    assert bal2["available_balance"] == 1500
    assert bal2["locked_balance"] == 500


def test_api_insufficient_funds_400(client):
    discord_id = "api_user_insufficient"
    client.post("/wallets/credit", json={
        "discord_user_id": discord_id,
        "amount": 100,
        "reference_type": "test",
        "idempotency_key": "insuf_key_1",
    })

    resp = client.post("/wallets/debit", json={
        "discord_user_id": discord_id,
        "amount": 500,
        "reference_type": "test",
        "idempotency_key": "insuf_key_2",
    })
    assert resp.status_code == 400
    data = resp.json()
    assert data["code"] == "INSUFFICIENT_FUNDS"
    assert data["details"]["required"] == 500
    assert data["details"]["available"] == 100


def test_api_idempotency_conflict_409(client):
    discord_id = "api_user_conflict"
    key = "conflict_idem_api"

    client.post("/wallets/credit", json={
        "discord_user_id": discord_id,
        "amount": 500,
        "reference_type": "test",
        "idempotency_key": key,
    })

    # Re-send with different amount -> 409 Conflict
    resp = client.post("/wallets/credit", json={
        "discord_user_id": discord_id,
        "amount": 900,
        "reference_type": "test",
        "idempotency_key": key,
    })
    assert resp.status_code == 409
    assert resp.json()["code"] == "IDEMPOTENCY_CONFLICT"


def test_api_user_not_found_404(client):
    resp = client.get("/users/non_existent_user_12345/balance")
    assert resp.status_code == 404
    assert resp.json()["code"] == "USER_NOT_FOUND"
