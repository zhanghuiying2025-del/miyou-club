import asyncio
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from src import history_archive as archive, history_search as history
from src.models import (TravelCase, CaseStatus, RequirementSpec, CouncilDecision, OrganizationState,
    FeasibilityReview, FeedbackLesson, LearningScope, PlanFeedbackRecord, PlanAcceptanceFeedback, OutcomeFeedback)
from src.storage import save_case_to_disk, load_case_from_disk, delete_case_from_disk
from src.company_store import transaction


def sample(title="旧单", *, status=CaseStatus.COMPLETED, city="南京"):
    return TravelCase(customer_request=title, status=status,
        requirements=RequirementSpec(event_name=title, city=city, team_profile="混龄员工",
            preferred_style="城市游玩", hard_constraints=["雨天室内备用", "不强迫成员表演"]),
        council_decision=CouncilDecision(theme="交流", summary="雨天改用室内交流，成员自愿参与",
            schedule=["09:00室内交流，可选择观察员", "12:00午餐休息"], merged_ideas=[],
            rationale=["避免强迫成员参与，照顾不同体力"]),
        manager_resolutions=["雨天改用室内活动，控制成本"])


class HistoryArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for target, value in [("src.storage.CASE_FILE", self.root/"cases.json"),
            ("src.company_store.COMPANY_DB", self.root/"company.sqlite3"),
            ("src.performance.PERFORMANCE_FILE", self.root/"performance.json")]:
            p = patch(target, value); p.start(); self.addCleanup(p.stop)
        p = patch.dict(os.environ, {"HISTORY_REDIS_URL":"", "HISTORY_EMBEDDING_MODEL_PATH":""})
        p.start(); self.addCleanup(p.stop)

    def test_full_archive_is_unchanged_by_index_and_search(self):
        old = sample(); save_case_to_disk(old)
        before = (self.root/"cases.json").read_bytes()
        docs, rows = history.sync_index()
        result = history.search("雨天 室内")
        self.assertTrue(result["rows"])
        self.assertEqual((self.root/"cases.json").read_bytes(), before)
        self.assertEqual(docs[0]["snapshot"], old.model_dump(mode="json"))
        self.assertFalse((self.root/"company.sqlite3").exists())
        self.assertFalse((self.root/"performance.json").exists())
        self.assertTrue(all(r["source_id"] == old.id and r["path"] for r in rows))

    def test_confirmed_plan_is_not_reported_as_delivered(self):
        old = sample(); save_case_to_disk(old)
        self.assertEqual(archive.documents()[0]["stage"], "plan_confirmed")
        with transaction() as tx:
            tx.save("supplier_order", "s1", {"id":"s1", "case_id":old.id, "need":{"city":"南京"},
                "status":"completed", "delivered":True, "customer_id":"test"})
        docs = archive.documents()
        self.assertEqual({d["stage"] for d in docs}, {"completed"})
        self.assertEqual(len(next(d for d in docs if d["kind"] == "case")["related_orders"]), 1)

    def test_fragments_are_business_units_with_stable_ids(self):
        old = sample(); save_case_to_disk(old)
        first = history.sync_index()[1]
        self.assertEqual({r["kind"] for r in first}, {"requirements", "plan", "decision"})
        second = history.sync_index()[1]
        self.assertEqual([r["id"] for r in first], [r["id"] for r in second])
        old.council_decision.schedule[0] = "09:00改为室内桌游"
        save_case_to_disk(old)
        third = history.sync_index()[1]
        self.assertFalse(any("09:00室内交流" in r["text"] for r in third))
        self.assertTrue(any("桌游" in r["text"] for r in third))

    def test_disabled_fragment_is_not_retrieved_or_lost_when_rebuilt(self):
        old = sample(); save_case_to_disk(old)
        result = history.search("观察员")
        row = next(r for r in result["rows"] if "观察员" in r["text"])
        history.set_active(row["id"], False)
        self.assertNotIn(row["id"], [r["id"] for r in history.search("观察员")["rows"]])
        history.sync_index()
        self.assertFalse(next(r for r in history.sync_index()[1] if r["id"] == row["id"])["active"])
        self.assertIn("观察员", load_case_from_disk(old.id).council_decision.schedule[0])
        history.set_active(row["id"], True)
        self.assertIn(row["id"], [r["id"] for r in history.search("观察员")["rows"]])

    def test_deleted_source_cannot_leave_searchable_ghosts(self):
        old = sample(); save_case_to_disk(old)
        self.assertTrue(history.search("观察员")["rows"])
        delete_case_from_disk(old)
        self.assertEqual(history.search("观察员")["rows"], [])

    def test_city_filter_and_excluding_current_case_and_drafts(self):
        current, old, other, draft = sample("新单"), sample("历史"), sample("异地", city="北京"), sample("草案", status=CaseStatus.WAITING_DRAFT)
        for item in (current, old, other, draft): save_case_to_disk(item)
        result = history.references_for(current)
        self.assertTrue(result["rows"])
        self.assertEqual({r["source_id"] for r in result["rows"]}, {old.id})
        self.assertLessEqual(len(result["rows"]), 2)
        self.assertEqual({r["city"] for r in history.search("雨天", city="北京")["rows"]}, {"北京"})

    def test_generic_no_matching_query_does_not_invent_memories(self):
        save_case_to_disk(sample())
        self.assertEqual(history.search("完全无关的火星基地")["rows"], [])

    def test_missing_redis_uses_local_index_and_hides_credentials(self):
        save_case_to_disk(sample())
        with patch.dict(os.environ, {"HISTORY_REDIS_URL":"redis://:SECRET@127.0.0.1:1/0"}), \
             patch.object(history, "redis_search", side_effect=RuntimeError("SECRET")):
            result = history.search("观察员")
        self.assertTrue(result["rows"])
        self.assertIn("Redis 不可用", result["notice"])
        self.assertNotIn("SECRET", json.dumps(result))

    def test_missing_embedding_model_uses_keyword_search(self):
        save_case_to_disk(sample())
        with patch.dict(os.environ, {"HISTORY_EMBEDDING_MODEL_PATH":str(self.root/"missing")}):
            result = history.search("观察员")
        self.assertTrue(result["rows"])
        self.assertIn("向量模型不可用", result["notice"])

    def test_redis_search_creates_indexes_and_atomically_replaces_own_keys(self):
        import redis
        save_case_to_disk(sample())
        rows = history.sync_index()[1]
        store, members, calls = {}, {}, []
        class FakeRedis:
            created = False
            closed = False
            def execute_command(self, *args):
                calls.append(args)
                if args[0] == "FT.INFO" and not self.created:
                    raise redis.ResponseError("Unknown Index name")
                if args[0] == "FT.CREATE": self.created = True
                if args[0] == "FT.SEARCH":
                    return [1, b"document-key", [b"id", rows[0]["id"].encode()]]
                return b"OK"
            def get(self, key): return store.get(key)
            def smembers(self, key): return members.get(key, set())
            def pipeline(self, transaction):
                self.transactional = transaction
                return self
            def hset(self, key, mapping): store[key] = mapping; return self
            def delete(self, *keys):
                for key in keys: store.pop(key, None); members.pop(key, None)
                return self
            def sadd(self, key, *items): members[key] = {x.encode() for x in items}; return self
            def set(self, key, value): store[key] = value.encode(); return self
            def execute(self): return []
            def close(self): self.closed = True
        fake = FakeRedis()
        with patch.dict(os.environ, {"HISTORY_REDIS_URL":"redis://localhost:6379/15"}), \
             patch("redis.Redis.from_url", return_value=fake):
            keys, backend = history.redis_search(rows, "雨天", {}, None, "")
            self.assertEqual(keys, [rows[0]["id"]])
            self.assertEqual(backend, "Redis 关键词检索")
            self.assertTrue(fake.transactional and fake.closed)
            key = next(k for k in store if k.endswith(":doc:" + rows[0]["id"]))
            rows[0]["active"] = False
            history.redis_search(rows, "雨天", {}, None, "")
            self.assertNotIn(key, store)
            self.assertFalse(any(c[0] in {"FLUSHDB", "FLUSHALL"} for c in calls))
            vectors = {r["id"]:[1.0, 0.0] for r in rows}
            history.redis_search(rows, "雨天", vectors, [1.0, 0.0], "test-model")
        creates = [c for c in calls if c[0] == "FT.CREATE"]
        # Use a fresh client/index fixture below to inspect the vector schema.
        fake.created = False
        with patch.dict(os.environ, {"HISTORY_REDIS_URL":"redis://localhost:6379/15"}), \
             patch("redis.Redis.from_url", return_value=fake):
            history.redis_search(rows, "雨天", vectors, [1.0, 0.0], "new-model")
        self.assertTrue(any("VECTOR" in c and "FLAT" in c for c in calls if c[0] == "FT.CREATE"))
        self.assertTrue(any("PARAMS" in c for c in calls if c[0] == "FT.SEARCH"))

    def test_vectors_are_cached_and_model_identity_change_reembeds(self):
        save_case_to_disk(sample())
        class Vector(list):
            def tolist(self): return list(self)
        model = SimpleNamespace(encode=lambda texts, **kwargs: [Vector([1.0, 0.0]) for _ in texts])
        with patch.dict(os.environ, {"HISTORY_EMBEDDING_MODEL_PATH":str(self.root)}), \
             patch.object(history, "embedding_identity", return_value="model1"), \
             patch.object(history, "_embedding_model", return_value=model):
            first = history.search("观察员")
            with patch.object(model, "encode", wraps=model.encode) as call:
                history.search("观察员")
                self.assertEqual(call.call_count, 1)  # Only the new query, not all old documents.
            with patch.object(history, "embedding_identity", return_value="model2"), \
                 patch.object(model, "encode", wraps=model.encode) as call:
                history.search("观察员")
                self.assertEqual(call.call_count, 2)
        self.assertIn("向量", first["backend"])

    def test_context_is_bounded_valid_json_and_does_not_claim_completion(self):
        save_case_to_disk(sample())
        rows = history.references_for(sample("新需求"))["rows"]
        rendered = history.context(rows)
        payload = rendered.split("\n", 2)[2].split("\n", 1)[0]
        self.assertTrue(json.loads(payload))
        self.assertLess(len(rendered), 6500)
        self.assertIn("不代表已履约", rendered)
        self.assertIn("必须重新查询", rendered)

    def test_workflow_reuses_same_references_and_reloads_after_disable(self):
        from src.workflow import TravelAgencyWorkflow
        save_case_to_disk(sample("历史订单"))
        new = sample("新订单", status=CaseStatus.WAITING_DRAFT); save_case_to_disk(new)
        with patch("src.workflow.load_organization_state", return_value=OrganizationState()):
            flow = TravelAgencyWorkflow(SimpleNamespace(manager="m", planner="p", fulfillment="f"))
        with patch.object(history, "references_for", wraps=history.references_for) as query:
            flow._load_history(new)
            self.assertTrue(new.history_references)
            save_case_to_disk(new)  # Own checkpoints must not cause another retrieval.
            flow._load_history(new)
            self.assertEqual(query.call_count, 1)
            removed = new.history_references[0]["id"]
            history.set_active(removed, False)
            flow._load_history(new)
            self.assertEqual(query.call_count, 2)
            self.assertNotIn(removed, [r["id"] for r in new.history_references])

    def test_workflow_index_failure_does_not_block_business(self):
        from src.workflow import TravelAgencyWorkflow
        with patch("src.workflow.load_organization_state", return_value=OrganizationState()):
            flow = TravelAgencyWorkflow(SimpleNamespace())
        new = sample()
        new.history_references = [{"stale":"should be cleared"}]
        with patch.object(history, "retrieval_key", side_effect=OSError("bad index")):
            flow._load_history(new)
        self.assertEqual(new.history_references, [])
        self.assertIn("跳过历史参考", new.history_search_notice)

    def test_three_roles_receive_same_sources_without_an_extra_mimo_call(self):
        from src.workflow import TravelAgencyWorkflow
        old = sample("历史已确认方案"); save_case_to_disk(old)
        new = sample("新需求", status=CaseStatus.WAITING_DRAFT); save_case_to_disk(new)
        with patch("src.workflow.load_organization_state", return_value=OrganizationState()):
            flow = TravelAgencyWorkflow(SimpleNamespace(manager="m", planner="p", fulfillment="f"))
        model = AsyncMock(return_value=FeasibilityReview(summary="检查本单条件"))
        async def exercise():
            with patch("src.workflow.run_structured", new=model), \
                 patch.object(history, "references_for", wraps=history.references_for) as retrieval:
                for role in ("planner", "fulfillment", "manager"):
                    await flow._call(new, role, "测试任务", FeasibilityReview)
                self.assertEqual(retrieval.call_count, 1)
        asyncio.run(exercise())
        self.assertEqual(model.await_count, 3)
        self.assertTrue(all(old.id in call.args[1] for call in model.call_args_list))
        self.assertEqual(set(new.history_references[0]["supplied_to"]), {"多角色策划", "履约执行", "项目经理"})

    def test_history_screen_needs_no_llm_config_and_resumes_exact_case(self):
        from streamlit.testing.v1 import AppTest
        from src.storage import save_visit
        old = sample("未完成草案", status=CaseStatus.WAITING_DRAFT); save_case_to_disk(old)
        save_visit("history-owner", {"identity":"公司老板", "work_area":"历史订单管理"})
        def visit(): return patch("src.visits._browser_visit", return_value=SimpleNamespace(visit={"id":"history-owner"}))
        with visit(), patch("src.agents.build_agent_team", side_effect=AssertionError("must not build agents")):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/"app.py"), default_timeout=30).run()
            self.assertFalse(app.exception)
            self.assertTrue(any(x.label == "查看完整档案" for x in app.selectbox))
        team = SimpleNamespace(manager="m", planner="p", fulfillment="f")
        with visit(), patch("src.agents.build_agent_team", return_value=team), \
             patch("src.workflow.load_organization_state", return_value=OrganizationState()):
            next(b for b in app.button if b.label == "恢复到客户页面").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.sidebar.radio[0].value, "客户")
        self.assertEqual(app.session_state["case"].id, old.id)
        self.assertEqual(app.session_state["case"].status, CaseStatus.WAITING_DRAFT)

    def test_customer_cannot_browse_company_archive(self):
        from streamlit.testing.v1 import AppTest
        from src.storage import save_visit
        save_case_to_disk(sample("不应泄露的旧单"))
        save_visit("history-customer", {"identity":"客户", "work_area":"历史订单管理"})
        with patch("src.visits._browser_visit", return_value=SimpleNamespace(visit={"id":"history-customer"})), \
             patch("src.agents.build_agent_team", return_value=SimpleNamespace()):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/"app.py"), default_timeout=30).run()
        self.assertFalse(app.exception)
        self.assertFalse(any(x.label == "查看完整档案" for x in app.selectbox))
        self.assertEqual([r.label for r in app.sidebar.radio], ["当前身份"])
        self.assertTrue(any(x.label == "活动城市 *" for x in app.text_input))
