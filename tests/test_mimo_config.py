import os
import unittest
from unittest.mock import patch, AsyncMock
import asyncio
from types import SimpleNamespace
from pydantic import ValidationError

from src.agents import build_agent_team, is_mimo_endpoint, task_timeout
from src.models import BookingFinancialReview, BookingSpeech, CaseStatus, CouncilDecision, TravelCase


class MimoConfigurationTests(unittest.TestCase):
    def test_mimo_options_reach_all_three_agents_without_sdk_retries(self):
        with patch.dict(os.environ, {"LLM_API_KEY": "test-key", "LLM_BASE_URL": "https://api.xiaomimimo.com/v1",
                                    "LLM_MODEL": "mimo-v2.6-pro"}), patch(
            "src.agents.OpenAIChatCompletionClient") as client, patch("src.agents.Agent") as agent:
            original_sdk = client.return_value.client
            build_agent_team()
            original_sdk.with_options.assert_called_once_with(timeout=75, max_retries=0)
            self.assertEqual(agent.call_count, 3)
            for call in agent.call_args_list:
                self.assertEqual(call.kwargs["default_options"]["extra_body"]["thinking"]["type"], "disabled")
            self.assertEqual(task_timeout(8), 30)
            self.assertEqual(task_timeout(60), 60)

    def test_provider_specific_options_are_not_sent_to_other_providers(self):
        with patch.dict(os.environ, {"LLM_API_KEY": "test-key", "LLM_BASE_URL": "https://api.deepseek.com/v1",
                                    "LLM_MODEL": "deepseek-chat"}), patch(
            "src.agents.OpenAIChatCompletionClient"), patch("src.agents.Agent") as agent:
            build_agent_team()
            self.assertTrue(all(call.kwargs["default_options"] == {} for call in agent.call_args_list))
            self.assertEqual(task_timeout(8), 8)

    def test_hostname_detection_does_not_match_url_paths_or_similar_domains(self):
        self.assertFalse(is_mimo_endpoint("https://example.com/api.xiaomimimo.com"))
        self.assertFalse(is_mimo_endpoint("https://api.xiaomimimo.com.example.com/v1"))
        self.assertTrue(is_mimo_endpoint("https://api.xiaomimimo.com/v1/"))

    def test_financial_review_cannot_select_resources_and_still_keeps_handoff(self):
        from src.procurement import start_procurement, advance_supplier, finish_turn, request_booking_speech
        case = TravelCase(customer_request="虚构测试", status=CaseStatus.COMPLETED,
            council_decision=CouncilDecision(theme="测试", summary="测试", schedule=["室内交流"], merged_ideas=[], rationale=[]))
        order = start_procurement(case, 9000, 1, 2)
        for _ in range(3):
            advance_supplier(order, now=order.last_step_at + 2)
        finish_turn(order, 3, BookingSpeech(message="候选", package_id="far"), "agent")
        finish_turn(order, 4, BookingSpeech(message="行程保持"), "agent")
        with patch("src.procurement.build_agent_team", return_value=SimpleNamespace(manager=object())), patch(
            "src.procurement.run_structured", new=AsyncMock(return_value=BookingFinancialReview(message="成本按程序核对，待下一步选择资源"))) as model:
            speech, source = asyncio.run(request_booking_speech(order))
        model.assert_not_awaited()
        self.assertEqual(source, "rule")
        self.assertIsNone(speech.package_id)
        self.assertEqual(speech.adjustments, [])
        self.assertEqual(speech.handoffs[0].status, "completed")
        self.assertEqual(speech.handoffs[0].source, "rule")
        self.assertEqual(order.selected_package, "far")
        with self.assertRaises(ValidationError):
            BookingFinancialReview(message="核查", package_id="near_budget")
