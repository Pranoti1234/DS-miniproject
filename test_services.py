import threading
import time

import requests as http


def test_health_and_seeded_inventory(services):
    inventory, _, _ = services
    assert inventory.get("/health").json["status"] == "healthy"
    assert inventory.get("/api/banks").json["count"] == 10
    summary = inventory.get("/api/inventory/summary").json["data"]
    assert summary["total_banks"] == 10
    assert set(summary["groups"]) == {"A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"}
    assert summary["grand_total"] > 0


def test_reserve_and_invalid_payload(services):
    inventory, _, _ = services
    response = inventory.post("/api/reserve", json={"blood_group": "O+", "units": 4})
    assert response.status_code == 200
    assert response.json["reservation"]["units_allocated"] == 4
    assert inventory.post("/api/reserve", json={"blood_group": "O+", "units": 0}).status_code == 400
    assert inventory.post("/api/reserve", json={"blood_group": "AB-", "units": 9999}).status_code == 422


def test_concurrent_reservations_never_oversell(services):
    inventory, _, _ = services
    starting_total = sum(row["units"] for row in inventory.get("/api/inventory?blood_group=O%2B").json["data"])
    results = []
    barrier = threading.Barrier(12)

    def reserve():
        barrier.wait()
        client = inventory.application.test_client()
        results.append(client.post("/api/reserve", json={"blood_group": "O+", "units": 1}).status_code)

    threads = [threading.Thread(target=reserve) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert results.count(200) == 12
    remaining = inventory.get("/api/inventory?blood_group=O%2B").json["data"]
    assert all(row["units"] >= 0 for row in remaining)
    assert starting_total - sum(row["units"] for row in remaining) == 12


def test_request_validation_and_success_flow(services, monkeypatch):
    inventory, request_client, notification_client = services

    def local_post(url, json, timeout):
        target = inventory if url.endswith("/api/reserve") else notification_client
        result = target.post(url[url.find("/api/"):], json=json)
        response = http.Response()
        response.status_code = result.status_code
        response._content = result.data
        return response

    monkeypatch.setattr("services.request_service.app.requests.post", local_post)
    assert request_client.post("/api/requests", json={"hospital_name": "", "blood_group": "O+", "units": 2}).status_code == 400
    result = request_client.post("/api/requests", json={
        "hospital_name": "Demo Hospital", "blood_group": "O+", "units": 2, "urgency": "Urgent",
    })
    assert result.status_code == 201
    assert result.json["data"]["status"] == "FULFILLED"
    assert request_client.get("/api/requests/stats").json["data"]["total"] == 1
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and notification_client.get("/api/notifications").json["count"] == 0:
        time.sleep(0.01)
    assert notification_client.get("/api/notifications").json["count"] == 1


def test_notification_deduplication(services):
    _, _, notifications = services
    event = {"event_id": "EVT-1", "event_type": "ALLOCATION_SUCCESS", "hospital_name": "Demo",
             "blood_group": "O+", "units": 2}
    assert notifications.post("/api/notifications", json=event).status_code == 201
    duplicate = notifications.post("/api/notifications", json=event)
    assert duplicate.status_code == 200
    assert duplicate.json["is_duplicate"] is True
    assert notifications.get("/api/notifications").json["count"] == 1
