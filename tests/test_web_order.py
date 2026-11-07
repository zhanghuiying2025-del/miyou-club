"""Verify the React-facing API against the real workflow and simulation stores."""
from datetime import date, timedelta
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from src import company_store, performance, storage
from src.models import CouncilDecision, FeasibilityReview, ManagedPlan
from src.web_api import create_app
from test_compact_planning import checks
from test_style_request_flow import TEAM, brief


class WebOrderTests(unittest.TestCase):
    def test_customer_and_owner_complete_same_order(self):
        plan = CouncilDecision(theme="室内团队协作", summary="室内协作后聚餐",
                               schedule=["09:00 室内小组协作", "12:00 午餐"],
                               merged_ideas=[], rationale=["低强度"])
        model = AsyncMock(side_effect=[brief(), FeasibilityReview(summary="核对活动条件"),
                                       ManagedPlan(plan=plan, checks=checks())])
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(storage, "CASE_FILE", root / "cases.json"),
                patch.object(storage, "DATA_DIR", root),
                patch.object(company_store, "COMPANY_DB", root / "company.sqlite3"),
                patch.object(performance, "PERFORMANCE_FILE", root / "performance.json"),
                patch.dict(os.environ, {"MIYOU_OWNER_PASSWORD": "local-test-password"}),
                patch("src.web_flow.build_agent_team", return_value=TEAM),
                patch("src.workflow.run_structured", new=model),
            ):
                self.run_order()

    def run_order(self):
        app = create_app()
        customer, owner, stranger = TestClient(app), TestClient(app), TestClient(app)
        self.assertEqual(stranger.get("/api/owner/dashboard").status_code, 401)
        self.assertEqual(owner.post("/api/owner/session", json={"password": "bad"}).status_code, 401)
        self.assertEqual(owner.post("/api/owner/session", json={"password": "local-test-password"}).status_code, 200)
        self.assertEqual(customer.get("/api/customer/session").status_code, 200)
        payload = {
            "request_id": str(uuid4()),
            "brief": {
                "name": "React 双端验收", "people": 15, "team": "团队同事",
                "city": "天津", "destination": "市区", "event_city": "北京",
                "date": (date.today() + timedelta(days=5)).isoformat(),
                "duration": "1天", "budget": 700, "goals": ["增进默契"],
            },
        }

        def read(response):
            self.assertLess(response.status_code, 300, response.text)
            return response.json()

        created = read(customer.post("/api/customer/activities", json=payload))
        self.assertEqual(read(customer.post("/api/customer/activities", json=payload))["id"], created["id"])
        self.assertEqual(len(read(customer.get("/api/customer/activities"))), 1)
        path = "/api/customer/activities/" + created["id"]
        order_path = "/api/owner/orders/case:" + created["id"]
        stranger.get("/api/customer/session")
        self.assertEqual(stranger.get(path).status_code, 404)
        self.assertEqual(customer.get(order_path).status_code, 401)

        def wait_for(status):
            for _ in range(100):
                item = read(customer.get(path))
                if item["status"] == status and not item["running"]:
                    return item
                time.sleep(0.025)
            self.fail(item)

        def action(client, route, name, item, **values):
            booking = item.get("booking") or {}
            return client.post(route + "/actions", json={
                "action": name, "revision": item["revision"],
                "order_revision": booking.get("revision"),
                "signature": (booking.get("contract") or {}).get("signature"), **values,
            })

        item = wait_for("waiting_style_feedback")
        self.assertEqual(len(item["plans"]), 1)
        self.assertEqual(item["requirements"]["city"], "北京")
        self.assertEqual(item["requirements"]["budget_yuan"], 10500)
        self.assertNotIn("audit", item)
        read(action(customer, path, "confirm_direction", item))
        item = wait_for("waiting_draft")
        read(action(customer, path, "confirm_draft", item))
        item = wait_for("ready_for_customer")
        read(action(customer, path, "accept_plan", item, outcome="satisfied"))
        item = wait_for("completed")
        item = read(action(customer, path, "request_quotes", item, meal_names=["午餐"]))
        quote = next(q for q in item["booking"]["quotes"] if q["available"] and q["kind"] == "restaurant")
        item = read(action(customer, path, "select_quotes", item, options=[quote["id"]]))
        item = read(action(customer, path, "confirm_contract", item,
                           options=list(item["booking"]["contract"]["conditions"])))
        unpaid = item
        item = read(action(customer, path, "pay_deposit", item))
        self.assertEqual(item["booking"]["status"], "paid")
        self.assertGreaterEqual(action(customer, path, "pay_deposit", unpaid).status_code, 400)
        boss_item = read(owner.get(order_path))
        self.assertEqual(boss_item["case_id"], created["id"])
        self.assertEqual(boss_item["actions"], ["authorize_deposit"])
        self.assertEqual(action(customer, order_path, "authorize_deposit", boss_item).status_code, 401)
        boss_item = read(action(owner, order_path, "authorize_deposit", boss_item))
        self.assertEqual(boss_item["actions"], ["submit_booking"])
        boss_item = read(action(owner, order_path, "submit_booking", boss_item))
        item = read(customer.get(path))
        self.assertEqual(item["booking"]["receipts"], boss_item["booking"]["receipts"])
        self.assertEqual(len(item["booking"]["receipts"]), 1)
        item = read(action(customer, path, "confirm_delivery", item))
        self.assertEqual(item["booking"]["status"], "delivered")
        boss_item = read(owner.get(order_path))
        self.assertEqual(boss_item["actions"], ["settle"])
        read(action(owner, order_path, "settle", boss_item))
        self.assertGreaterEqual(action(owner, order_path, "settle", boss_item).status_code, 400)
        self.assertEqual(read(customer.get(path))["booking"]["status"], "completed")
        dashboard = read(owner.get("/api/owner/dashboard"))
        self.assertEqual(dashboard["main_orders"], {"paid": 1, "settled": 1})
        cash = company_store.list_records("supplier_order")
        self.assertEqual(len(cash), 1)
        self.assertEqual(dashboard["supplier_cash"]["incoming_cents"], item["booking"]["contract"]["total_cents"])
        with company_store.transaction() as ledger:
            self.assertEqual(len(ledger.events("cash")), 4)
