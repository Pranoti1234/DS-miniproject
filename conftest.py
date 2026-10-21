import pytest

from services.inventory_service.app import create_app as create_inventory_app
from services.notification_service.app import create_app as create_notification_app
from services.request_service.app import create_app as create_request_app


@pytest.fixture
def services(tmp_path):
    inventory_path = str(tmp_path / "inventory.db")
    notification_path = str(tmp_path / "notifications.db")
    request_path = str(tmp_path / "requests.db")
    inventory = create_inventory_app(inventory_path).test_client()
    notifications = create_notification_app(notification_path).test_client()
    requests = create_request_app(request_path, "http://inventory.test", "http://notifications.test").test_client()
    return inventory, requests, notifications
