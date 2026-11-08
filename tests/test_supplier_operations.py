import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch

from src import operations as ops
from src import supplier_simulation as business
from src.company_store import transaction,finance_summary,list_records,SimulationConflict
from src.supplier_models import BookingNeed,MealSlot,ContactPlan,ContactAction


def dashboard_order_button(app, key):
    app.segmented_control(key="owner_dashboard_page").set_value("订单与风险").run()
    app.selectbox(key="owner_dashboard_order_selection").set_value(key).run()
    return app.button(key=f"owner_dashboard_open:{key}")


def need(**changes):
    values=dict(city="模拟目的地",check_in="2026-11-06",check_out="2026-11-08",men=100,women=50,
        meals=[MealSlot(day=date(2026,11,d),meal=m) for d in (6,7,8) for m in ("午餐","晚餐")],
        daily_meal_budget_cents=20000,require_bonfire=True,require_fireworks=True)
    values.update(changes)
    return BookingNeed(**values)


class SupplierOperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        p=patch("src.company_store.COMPANY_DB",Path(self.temp.name)/"simulation.sqlite3")
        p.start();self.addCleanup(p.stop)

    def create(self,customer="demo-150",**kwargs):
        ops.register_lead(customer,"模拟酒店餐饮需求")
        return business.create_order(need(),customer,**kwargs)

    def choose(self,order,hotel="hotel-b",restaurant="meal-b"):
        order=business.contact_all(order.id)
        ids=[next(q.id for q in reversed(order.quotes) if q.supplier_id==supplier) for supplier in (hotel,restaurant)]
        order=business.select_quotes(order.id,ids)
        return order

    def agreed(self,order):
        return business.confirm_contract(order.id,list(business.contract(order)["conditions"]),"customer")

    def paid(self,order,coupon=None):
        order=self.agreed(self.choose(order))
        return business.pay_customer_deposit(order.id,coupon)

    def test_historical_case_counts_and_same_sex_odd_rooms(self):
        n=need()
        self.assertEqual(n.rooms,75)
        self.assertEqual(len(n.nights),2)
        self.assertEqual(n.nights,["2026-11-06","2026-11-07"])
        self.assertEqual(need(men=101,women=49).rooms,76)

    def test_invalid_dates_duplicate_meals_and_negative_stock_rejected(self):
        with self.assertRaises(ValueError):need(check_out="2026-11-05")
        with self.assertRaises(ValueError):need(meals=[MealSlot(day=date(2026,11,6),meal="午餐")]*2)
        n=need();world=business.fixed_world(n)
        world.hotels[0].rooms_by_night[n.nights[0]]=-1
        with self.assertRaises(ValueError):business.validate_world(world,n)

    def test_second_night_and_meal_capacity_are_independent_blockers(self):
        order=business.contact_all(self.create().id)
        hotel=next(q for q in order.quotes if q.supplier_id=="hotel-a")
        meal=next(q for q in order.quotes if q.supplier_id=="meal-a")
        self.assertFalse(hotel.capacity_ok)
        self.assertIn("2026-11-07房量不足",hotel.blockers)
        self.assertFalse(meal.capacity_ok)
        self.assertEqual(len(meal.blockers),6)
        with self.assertRaises(ValueError):business.select_quotes(order.id,[hotel.id,meal.id])

    def test_hotel_conditions_require_customer_and_owner_cannot_substitute(self):
        order=self.choose(self.create())
        self.assertTrue(business.contract(order)["conditions"])
        with self.assertRaises(ValueError):business.confirm_contract(order.id,[],"customer")
        with self.assertRaises(ValueError):business.confirm_contract(order.id,list(business.contract(order)["conditions"]),"owner")
        with self.assertRaises(ValueError):business.pay_customer_deposit(order.id)

    def test_day_budget_overrun_adds_explicit_customer_conditions(self):
        order=self.create()
        with transaction() as tx:
            raw,revision=tx.get("supplier_order",order.id)
            raw["need"]["daily_meal_budget_cents"]=10000
            tx.save("supplier_order",order.id,raw,revision)
        order=self.choose(business.load_order(order.id)[0])
        conditions=business.contract(order)["conditions"]
        self.assertEqual(len([k for k in conditions if k.startswith("meal_budget:")]),3)

    def test_full_booking_stock_cash_and_idempotency(self):
        order=self.paid(self.create())
        self.assertEqual(order.customer_paid_cents,9300000)
        with self.assertRaises(ValueError):business.fulfill_bookings(order.id)
        with self.assertRaises(ValueError):business.pay_supplier_deposit(order.id,"customer")
        order=business.pay_supplier_deposit(order.id,"owner")
        self.assertEqual(order.supplier_paid_cents,4500000)
        order=business.fulfill_bookings(order.id)
        stock=order.world.hotels[1].rooms_by_night.copy()
        self.assertEqual(len(order.confirmations),2)
        again=business.fulfill_bookings(order.id)
        self.assertEqual(again.world.hotels[1].rooms_by_night,stock)
        business.pay_customer_deposit(order.id)
        business.pay_supplier_deposit(order.id,"owner")
        order=business.finish_service(order.id)
        business.finish_service(order.id)
        self.assertEqual(order.customer_paid_cents,15500000)
        order=business.settle_suppliers(order.id,"owner")
        business.settle_suppliers(order.id,"owner")
        self.assertEqual(order.status,"completed")
        summary=ops.operating_summary()
        self.assertEqual(summary["finance"]["cash_cents"],5100000)
        self.assertEqual(len(summary["ledger"]),4)
        self.assertEqual(summary["service_income_cents"],500000)
        self.assertEqual(summary["advance_liability_cents"],0)
        self.assertEqual(summary["recognized_revenue_cents"],15500000)
        self.assertEqual(summary["recognized_supplier_cost_cents"],15000000)
        self.assertEqual(summary["realized_order_profit_cents"],500000)
        self.assertEqual(summary["supplier_payable_cents"],0)
        self.assertEqual(summary["project_finance"][0]["project_id"],order.id)
        self.assertTrue(all(row["project_id"] == order.id for row in summary["ledger"]))

    def test_customer_deposit_is_project_advance_not_income_or_profit(self):
        order=self.paid(self.create(case_id="case-finance-source"))
        summary=ops.operating_summary()
        self.assertEqual(summary["advance_liability_cents"],order.customer_paid_cents)
        self.assertEqual(summary["recognized_revenue_cents"],0)
        self.assertEqual(summary["realized_order_profit_cents"],0)
        project=summary["project_finance"][0]
        self.assertEqual(project["project_id"],"case-finance-source")
        self.assertEqual(project["status_label"],"履约中，客户付款仍为预收款")
        deposit=next(row for row in summary["ledger"] if row["flow_type"]=="客户预付款")
        self.assertEqual(deposit["project_id"],"case-finance-source")
        self.assertEqual(deposit["counterparty"],"客户 demo-150")
        self.assertEqual(deposit["recognition"],"客户预收款，尚未确认收入")

    def test_delivery_recognizes_revenue_and_accrues_unpaid_supplier_cost(self):
        order=self.paid(self.create(case_id="case-delivered-finance"))
        order=business.pay_supplier_deposit(order.id,"owner")
        business.fulfill_bookings(order.id)
        order=business.finish_service(order.id)
        summary=ops.operating_summary()
        project=summary["project_finance"][0]
        self.assertEqual(order.status,"delivered")
        self.assertEqual(project["advance_liability_cents"],0)
        self.assertEqual(project["recognized_revenue_cents"],15500000)
        self.assertEqual(project["recognized_supplier_cost_cents"],15000000)
        self.assertEqual(project["supplier_payable_cents"],10500000)
        self.assertEqual(project["realized_order_profit_cents"],500000)
        self.assertEqual(project["status_label"],"已交付并确认收入，供方待结清")

    def test_expired_quote_prevents_payment_and_preserves_state(self):
        order=self.agreed(self.choose(self.create()))
        business.simulate_change(order.id,"expire")
        with self.assertRaises(ValueError):business.pay_customer_deposit(order.id)
        self.assertEqual(business.load_order(order.id)[0].customer_paid_cents,0)
        self.assertEqual(ops.operating_summary()["finance"]["incoming_cents"],0)

    def test_inventory_changes_after_customer_deposit_preserve_paid_money(self):
        order=self.paid(self.create())
        business.simulate_change(order.id,"second_night_shortage")
        with self.assertRaises(ValueError):business.pay_supplier_deposit(order.id,"owner")
        latest=business.load_order(order.id)[0]
        self.assertEqual(latest.customer_paid_cents,9300000)
        latest=self.choose(latest,hotel="hotel-c")
        latest=self.agreed(latest)
        latest=business.pay_customer_deposit(order.id)
        self.assertGreater(latest.customer_paid_cents,9300000)

    def test_duplicate_meals_and_incomplete_coverage_are_rejected(self):
        order=business.contact_all(self.create().id)
        hotel=next(q.id for q in order.quotes if q.supplier_id=="hotel-b")
        meal=next(q.id for q in order.quotes if q.supplier_id=="meal-b")
        with self.assertRaises(ValueError):business.select_quotes(order.id,[hotel])
        with self.assertRaises(ValueError):business.select_quotes(order.id,[hotel,meal,meal])

    def test_agent_really_uses_fulfillment_and_tool_records(self):
        order=self.create()
        plan=ContactPlan(summary="比较可执行候选",actions=[ContactAction(kind="hotel",supplier_id="hotel-b",message="请模拟确认连续房量"),
            ContactAction(kind="restaurant",supplier_id="meal-b",message="请模拟报价指定餐次")])
        with patch("src.agents.build_agent_team",return_value=SimpleNamespace(fulfillment="executor")),patch(
                "src.agents.run_structured",new=AsyncMock(return_value=plan)) as model:
            result=asyncio.run(business.agent_contact(order.id))
        model.assert_awaited_once()
        self.assertEqual(model.call_args.args[0],"executor")
        self.assertEqual(len(result.contacts),2)
        self.assertEqual(result.contacts[0]["source"],"agent")
        self.assertIn("模拟供方工具回执",result.contacts[0]["reply"])
        self.assertEqual(result.confirmations,[])

    def test_unknown_supplier_plan_is_atomic_and_has_no_partial_quote(self):
        order=self.create()
        plan=ContactPlan(summary="错误测试",actions=[ContactAction(kind="hotel",supplier_id="hotel-b",message="询价"),
            ContactAction(kind="restaurant",supplier_id="invented",message="询价")])
        with self.assertRaises(ValueError):business.contact_with_plan(order.id,plan,"agent")
        self.assertEqual(business.load_order(order.id)[0].quotes,[])

    def test_stale_agent_result_cannot_overwrite_changed_world(self):
        order=self.create()
        _,revision=business.load_order(order.id)
        business.simulate_change(order.id)
        plan=ContactPlan(summary="旧结果",actions=[ContactAction(kind="hotel",supplier_id="hotel-b",message="询价")])
        with self.assertRaises(SimulationConflict):business.contact_with_plan(order.id,plan,"agent",revision)

    def test_generated_world_is_saved_once_and_not_regenerated(self):
        order=self.create(use_fixed=False)
        with patch("src.agents.build_agent_team",return_value=SimpleNamespace(fulfillment="executor")),patch(
                "src.agents.run_structured",new=AsyncMock(return_value=business.fixed_world(order.need))) as model:
            asyncio.run(business.generate_world(order.id))
            asyncio.run(business.generate_world(order.id))
        model.assert_awaited_once()
        self.assertEqual(business.load_order(order.id)[0].world_source,"model_simulation")

    def coupon(self,customer="demo-150",**changes):
        values=dict(title="模拟优惠",face_cents=10000,min_order_cents=100000,
            valid_from="2026-11-01",valid_until="2026-12-31",audience="首单",limit=20)
        values.update(changes)
        policy=ops.create_coupon_policy(**values)
        return ops.issue_coupon(policy["id"],customer)

    def test_coupon_changes_deposit_and_final_ledger_without_double_use(self):
        order=self.create();coupon=self.coupon()
        order=self.paid(order,coupon["id"])
        self.assertEqual(order.discount_cents,10000)
        self.assertEqual(order.customer_paid_cents,9294000)
        business.pay_customer_deposit(order.id,coupon["id"])
        other=self.agreed(self.choose(self.create()))
        with self.assertRaises(ValueError):business.pay_customer_deposit(other.id,coupon["id"])
        self.assertEqual(business.load_order(other.id)[0].customer_paid_cents,0)
        self.assertEqual(len([e for e in ops.operating_summary()["ledger"] if e["direction"]=="in"]),1)

    def test_expired_coupon_cannot_consume_ticket_or_create_cash(self):
        order=self.create();coupon=self.coupon(valid_from="2026-11-07")
        order=self.agreed(self.choose(order))
        with self.assertRaises(ValueError):business.pay_customer_deposit(order.id,coupon["id"])
        self.assertEqual(list_records("coupon")[0]["status"],"issued")
        self.assertEqual(ops.operating_summary()["finance"]["incoming_cents"],0)

    def test_coupon_cannot_discount_supplier_cost_or_go_below_fee_floor(self):
        order=self.create();coupon=self.coupon(face_cents=490000)
        order=self.agreed(self.choose(order))
        with self.assertRaises(ValueError):business.pay_customer_deposit(order.id,coupon["id"])
        self.assertEqual(list_records("coupon")[0]["status"],"issued")

    def test_coupon_customer_and_campaign_scope(self):
        order=self.create();coupon=self.coupon()
        other=self.agreed(self.choose(self.create("someone-else")))
        with self.assertRaises(ValueError):business.pay_customer_deposit(other.id,coupon["id"])

    def test_concurrent_coupon_redemptions_only_one_order_wins(self):
        order1=self.create();coupon=self.coupon()
        order1=self.agreed(self.choose(order1))
        order2=self.agreed(self.choose(self.create()))
        def pay(order):
            try:
                business.pay_customer_deposit(order.id,coupon["id"])
                return "paid"
            except ValueError:return "rejected"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(pay,[order1,order2]))
        self.assertCountEqual(results,["paid","rejected"])

    def test_campaign_approval_and_budget_cash_idempotency(self):
        report=ops.fixed_research()
        campaign=ops.create_campaign("模拟获客","企业行政","社群",10000,"模拟宣传",report["id"])
        with self.assertRaises(ValueError):ops.register_lead("x","团建",campaign["id"])
        with self.assertRaises(ValueError):ops.activate_campaign(campaign["id"],"customer")
        ops.activate_campaign(campaign["id"],"owner")
        ops.campaign_spend(campaign["id"],5000,"one","owner")
        ops.campaign_spend(campaign["id"],5000,"one","owner")
        with self.assertRaises(ValueError):ops.campaign_spend(campaign["id"],6000,"two","owner")
        self.assertEqual(ops.operating_summary()["finance"]["outgoing_cents"],5000)

    def test_acquisition_attribution_to_paid_order_and_no_fake_rate(self):
        campaign=ops.create_campaign("模拟获客","企业行政","社群",10000,"模拟宣传")
        ops.activate_campaign(campaign["id"],"owner")
        self.assertIsNone(ops.operating_summary()["campaigns"][0]["转化率"])
        ops.register_lead("acquired","团建",campaign["id"])
        order=business.create_order(need(),"acquired",campaign_id=campaign["id"])
        self.paid(order)
        row=ops.operating_summary()["campaigns"][0]
        self.assertEqual(row["潜客数"],1)
        self.assertEqual(row["成交客户数"],1)
        self.assertEqual(row["转化率"],1)

    def test_linked_case_has_one_order_and_budget_reserve_cannot_be_bypassed(self):
        self.create(case_id="case-link")
        with self.assertRaises(ValueError):self.create(case_id="case-link")
        campaign=ops.create_campaign("预算测试","行政","模拟",5000000,"模拟")
        ops.activate_campaign(campaign["id"],"owner")
        with self.assertRaises(ValueError):ops.campaign_spend(campaign["id"],4000000,"exceeds-cash","owner")

    def test_supplier_flow_and_detached_operations_both_render(self):
        from streamlit.testing.v1 import AppTest
        from src.models import OrganizationState
        app_path=Path(__file__).resolve().parents[1]/"app.py"
        with patch("src.agents.build_agent_team",return_value=SimpleNamespace()),patch(
            "src.storage.load_case_from_disk",return_value=None),patch(
            "src.workflow.load_organization_state",return_value=OrganizationState()):
            app=AppTest.from_file(str(app_path),default_timeout=30).run()
            self.assertFalse(any(b.label=="查看活动与优惠" for b in app.button))
            selector = next(r for r in app.radio if r.label == "需求类型")
            self.assertIn("酒店餐饮预订", selector.options)
            self.assertFalse(any(b.label == "酒店餐饮预订" for b in app.button))
            selector.set_value("酒店餐饮预订").run()
            self.assertEqual(len(app.exception),0)
            next(b for b in app.button if b.label=="下一步").click().run()
            self.assertFalse(any(r.label == "需求类型" for r in app.radio))
            next(p for p in app.pills if p.label=="活动风格").set_value("其他").run()
            self.assertTrue(any(t.label=="活动风格（其他）" for t in app.text_input))
            next(b for b in app.button if b.label=="下一步").click().run()
            self.assertTrue(app.error)
            next(p for p in app.pills if p.label=="活动风格").set_value("城市游玩")
            next(b for b in app.button if b.label=="下一步").click().run()
            next(b for b in app.button if b.label=="上一步").click().run()
            self.assertEqual(next(p for p in app.pills if p.label=="活动风格").value, "城市游玩")
            next(b for b in app.button if b.label=="下一步").click().run()
            next(b for b in app.button if b.label=="下一步").click().run()
            next(m for m in app.multiselect if m.label=="口味与忌口").set_value(["清淡","其他"]).run()
            next(t for t in app.text_area if t.label=="口味与忌口（其他）").set_value("花生过敏")
            next(b for b in app.button if b.label=="查询报价").click().run()
            self.assertEqual(len(app.exception),0)
            records=list_records("supplier_order")
            self.assertEqual(len(records),1)
            self.assertEqual(records[0]["world_source"],"fixed_simulation")
            self.assertEqual(records[0]["need"]["preferred_style"], "城市游玩")
            self.assertEqual(records[0]["need"]["dietary"], "清淡；花生过敏")
            self.assertTrue(records[0]["quotes"])
            self.assertFalse(any(b.key==f"contact_agent:{records[0]['id']}" for b in app.button))
            self.assertFalse(any(b.key==f"expire:{records[0]['id']}" for b in app.button))
            next(b for b in app.button if b.label=="查看预订明细").click().run()
            self.assertEqual(len(app.exception),0)
            self.assertTrue(any(b.label=="确认报价及预订条件" for b in app.button))
            app.sidebar.radio[0].set_value("公司老板").run()
            self.assertNotIn("公司运营模块",app.sidebar.radio[1].options)
            operations=AppTest.from_string(
                "from src.operations_ui import render_operations_workspace\nrender_operations_workspace(owner=True)",
                default_timeout=30).run()
            next(b for b in operations.button if b.label=="生成竞品对比").click().run()
            self.assertFalse(operations.exception)
            self.assertEqual(len(list_records("research")),1)

    def test_customer_owner_ui_complete_booking_and_settlement(self):
        from streamlit.testing.v1 import AppTest
        from src.models import OrganizationState
        order=self.choose(self.create())
        self.coupon()
        app_path=Path(__file__).resolve().parents[1]/"app.py"
        with patch("src.agents.build_agent_team",return_value=SimpleNamespace()),patch(
            "src.storage.load_case_from_disk",return_value=None),patch(
            "src.workflow.load_organization_state",return_value=OrganizationState()):
            app=AppTest.from_file(str(app_path),default_timeout=30).run()
            app.session_state["supplier_selected"]=order.id
            next(r for r in app.radio if r.label=="需求类型").set_value("酒店餐饮预订").run()
            for key in business.contract(order)["conditions"]:
                next(c for c in app.checkbox if c.key==f"condition:{order.id}:{key}").check()
            next(b for b in app.button if b.label=="确认报价及预订条件").click().run()
            self.assertEqual(len(app.exception),0)
            self.assertFalse(any(s.label=="优惠券" for s in app.selectbox))
            next(b for b in app.button if b.label.startswith("支付预付款")).click().run()
            self.assertEqual(len(app.exception),0)
            paid_order, _ = business.load_order(order.id)
            self.assertEqual(paid_order.discount_cents, 0)
            app.sidebar.radio[0].set_value("公司老板").run()
            app.segmented_control(key="owner_dashboard_page").set_value("经营复盘").run()
            metrics = {metric.label: metric.value for metric in app.metric}
            self.assertEqual(metrics["累计模拟收款"], f"¥{paid_order.customer_paid_cents / 100:,.2f}")
            self.assertEqual(metrics["累计模拟支出"], "¥0.00")
            self.assertEqual(metrics["模拟现金余额"], f"¥{46_000 + paid_order.customer_paid_cents / 100:,.2f}")
            self.assertTrue(any("已确认净服务费 ¥0.00" in caption.value for caption in app.caption))
            dashboard_order_button(app, f"supplier:{order.id}").click().run()
            next(b for b in app.button if b.label=="老板授权模拟支付酒店餐饮定金").click().run()
            next(b for b in app.button if b.label=="执行工具提交模拟预订并获取回执").click().run()
            self.assertEqual(len(app.exception),0)
            app.sidebar.radio[0].set_value("客户").run()
            next(c for c in app.checkbox if c.label=="酒店和餐饮服务已完成").check().run()
            next(b for b in app.button if b.label=="确认完成并支付尾款").click().run()
            app.sidebar.radio[0].set_value("公司老板").run()
            dashboard_order_button(app, f"supplier:{order.id}").click().run()
            next(b for b in app.button if b.label=="老板授权结清供方并完成本单").click().run()
            self.assertEqual(len(app.exception),0)
            self.assertEqual(business.load_order(order.id)[0].status,"completed")

    def test_old_contract_page_cannot_approve_new_price(self):
        order=self.choose(self.create())
        old=business.contract(order)["signature"]
        order=self.choose(order,hotel="hotel-c")
        with self.assertRaises(SimulationConflict):
            business.confirm_contract(order.id,list(business.contract(order)["conditions"]),"customer",old)

    def test_meals_only_order_with_zero_rounded_deposit_can_be_fulfilled(self):
        from streamlit.testing.v1 import AppTest

        meal = MealSlot(day="2026-11-06", meal="午餐")
        meal_need = need(check_out="2026-11-06", include_hotel=False, men=0, women=0,
                         participant_count=1, meals=[meal])
        world = business.fixed_world(meal_need)
        world.restaurants[1].price_per_person_cents = 1
        ops.register_lead("one-meal", "仅餐饮")
        with patch.object(business, "fixed_world", return_value=world):
            order = business.create_order(meal_need, "one-meal")
        order = business.contact_all(order.id)
        quote = next(q for q in order.quotes if q.supplier_id == "meal-b")
        order = self.agreed(business.select_quotes(order.id, [quote.id]))
        business.pay_customer_deposit(order.id)

        app = AppTest.from_string(
            "from src.supplier_ui import render_supplier_workspace\n"
            f"render_supplier_workspace(owner=True, order_id={order.id!r})"
        ).run()
        app.button(key=f"fulfill:{order.id}").click().run()
        self.assertFalse(app.exception)
        confirmed, _ = business.load_order(order.id)
        self.assertEqual(confirmed.status, "confirmed")
        self.assertEqual(confirmed.supplier_paid_cents, 0)

    def test_direct_linked_order_requires_itinerary_confirmation_before_tail_payment(self):
        from streamlit.testing.v1 import AppTest
        from src.models import CaseStatus, CouncilDecision, TravelCase
        from src.storage import save_case_to_disk

        case = TravelCase(customer_request="含烟花的团建", status=CaseStatus.COMPLETED,
                          booking_route="supplier",
                          council_decision=CouncilDecision(theme="活动", summary="活动",
                              schedule=["烟花"], merged_ideas=[], rationale=[]))
        with patch("src.storage.CASE_FILE", Path(self.temp.name) / "cases.json"):
            save_case_to_disk(case)
            order = self.paid(self.create(case_id=case.id))
            business.pay_supplier_deposit(order.id, "owner")
            business.fulfill_bookings(order.id)
            app = AppTest.from_string(
                "from src.supplier_ui import render_supplier_workspace\n"
                f"render_supplier_workspace(order_id={order.id!r})"
            ).run()
            self.assertFalse(app.exception)
            self.assertTrue(any(b.key == f"resume_linked_delivery:{order.id}" for b in app.button))
            self.assertFalse(any(b.key == f"tail:{order.id}" for b in app.button))
            with self.assertRaisesRegex(ValueError, "确认更新后的执行行程"):
                business.finish_service(order.id)
            self.assertEqual(business.load_order(order.id)[0].customer_paid_cents, order.customer_paid_cents)

            case.supplier_order_id = order.id
            business.confirm_linked_execution(case)
            save_case_to_disk(case)
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(any(b.key == f"tail:{order.id}" for b in app.button))
            self.assertEqual(business.finish_service(order.id).status, "delivered")

    def test_day_trip_books_and_settles_meals_inside_same_case(self):
        from streamlit.testing.v1 import AppTest
        from src.models import CaseStatus,CouncilDecision,RequirementSpec,TravelCase
        from src.orchestration import lifecycle
        from src.procurement import start_procurement
        from src.storage import load_case_from_disk,save_case_to_disk
        case=TravelCase(customer_request="南京一日团建",status=CaseStatus.COMPLETED,planning_mode="style_branch",
            requirements=RequirementSpec(city="南京",event_date="2026-11-20",participant_count=15,
                duration="1天",preferred_style="城市游玩",hard_constraints=["需要素食选择"],budget_yuan=4000),
            council_decision=CouncilDecision(theme="城市协作",summary="一日团建",schedule=["小组探索","午餐"],
                merged_ideas=[],rationale=[]))
        save_case_to_disk(case)
        app_path=Path(__file__).resolve().parents[1]/"app.py"
        with patch("src.agents.build_agent_team",return_value=SimpleNamespace()):
            app=AppTest.from_file(str(app_path),default_timeout=30)
            app.session_state["customer_case_id"]=case.id
            app.run()
            self.assertFalse(app.exception)
            self.assertFalse(next(c for c in app.checkbox if c.label=="同时预订住宿").value)
            self.assertEqual(next(m for m in app.multiselect if m.label=="口味与忌口").value,["素食"])
            next(b for b in app.button if b.label=="查询本团建的报价").click().run()
            self.assertFalse(app.exception)
            saved=load_case_from_disk(case.id)
            order_id=saved.supplier_order_id
            order,_=business.load_order(order_id)
            self.assertEqual(order.case_id,case.id)
            self.assertEqual(order.need.people,15)
            self.assertEqual(order.need.dietary,"需要素食选择")
            self.assertEqual(order.need.check_in,order.need.check_out)
            self.assertEqual(order.need.nights,[])
            self.assertFalse(any(q.kind=="hotel" for q in order.quotes))
            self.assertFalse(any(b.label=="新建预订" for b in app.button))
            with self.assertRaisesRegex(ValueError,"已进入酒店餐饮预订"):
                start_procurement(saved,4000,0,2)
            # Restore the case pointer if a prior visit ended after recording the booking.
            saved.supplier_order_id=None
            save_case_to_disk(saved)
            app.run()
            self.assertEqual(load_case_from_disk(case.id).supplier_order_id,order_id)
            self.assertEqual(len(list_records("supplier_order")),1)
            next(b for b in app.button if b.label=="查看预订明细").click().run()
            next(c for c in app.checkbox if "超过团建原总预算" in c.label).check()
            next(b for b in app.button if b.label=="确认报价及预订条件").click().run()
            next(b for b in app.button if b.label.startswith("支付预付款")).click().run()
            app.sidebar.radio[0].set_value("公司老板").run()
            dashboard_order_button(app, f"case:{case.id}").click().run()
            next(b for b in app.button if b.label=="老板授权模拟支付酒店餐饮定金").click().run()
            app.button(key="owner_dashboard_return").click().run()
            app.segmented_control(key="owner_dashboard_page").set_value("经营总览").run()
            self.assertTrue(any("累计已付 1 笔" in item.value for item in app.caption))
            submit_booking = dashboard_order_button(app, f"case:{case.id}")
            self.assertEqual(submit_booking.label,"提交预订")
            submit_booking.click().run()
            next(b for b in app.button if b.label=="执行工具提交模拟预订并获取回执").click().run()
            next(b for b in app.button if b.label=="在客户页面继续此案件").click().run()
            next(c for c in app.checkbox if c.label=="餐饮服务已完成").check().run()
            next(b for b in app.button if b.label=="确认完成并支付尾款").click().run()
            app.sidebar.radio[0].set_value("公司老板").run()
            dashboard_order_button(app, f"case:{case.id}").click().run()
            next(b for b in app.button if b.label=="老板授权结清供方并完成本单").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(lifecycle(load_case_from_disk(case.id)),"订单完成并结算")
            app.button(key="owner_dashboard_return").click().run()
            app.segmented_control(key="owner_dashboard_page").set_value("经营总览").run()
            self.assertEqual(next(m for m in app.metric if m.label=="已结清团建主单").value,"1 笔")
            app.button(key="owner_dashboard_go_orders").click().run()
            app.button(key=f"owner_dashboard_open:case:{case.id}").click().run()
            next(b for b in app.button if b.label=="在客户页面继续此案件").click().run()
            next(b for b in app.button if b.label=="提交履约评价").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any(h.value=="订单已完成" for h in app.subheader))
            self.assertTrue(any(e.label=="预订回执" for e in app.expander))
            details=next(e for e in app.expander if e.label=="查看行程细节")
            self.assertFalse(details.proto.expanded)
            next(b for b in app.button if b.label=="开启新订单").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any(t.label=="活动城市 *" and not t.value for t in app.text_input))
            self.assertIsNone(app.session_state.get("customer_case_id"))
            self.assertIsNotNone(load_case_from_disk(case.id).delivery_feedback)
            self.assertEqual(business.load_order(order_id)[0].status,"completed")

    def test_overnight_trip_inherits_dates_and_checks_room_split(self):
        from streamlit.testing.v1 import AppTest
        from src.models import CaseStatus,CouncilDecision,RequirementSpec,TravelCase
        from src.storage import load_case_from_disk,save_case_to_disk
        case=TravelCase(customer_request="南京两日团建",status=CaseStatus.COMPLETED,planning_mode="style_branch",
            requirements=RequirementSpec(city="南京",event_date="2026-11-20",participant_count=15,
                duration="2天1晚",preferred_style="城市游玩"),
            council_decision=CouncilDecision(theme="城市协作",summary="过夜团建",schedule=["小组探索","午餐"],
                merged_ideas=[],rationale=[]))
        save_case_to_disk(case)
        app_path=Path(__file__).resolve().parents[1]/"app.py"
        with patch("src.agents.build_agent_team",return_value=SimpleNamespace()):
            app=AppTest.from_file(str(app_path),default_timeout=30)
            app.session_state["customer_case_id"]=case.id
            app.run()
            self.assertTrue(next(c for c in app.checkbox if c.label=="同时预订住宿").value)
            next(b for b in app.button if b.label=="查询本团建的报价").click().run()
            self.assertTrue(any("合计须等于团建人数" in e.value for e in app.error))
            self.assertEqual(list_records("supplier_order"),[])
            next(n for n in app.number_input if n.label=="参与住宿的男性人数").set_value(7)
            next(n for n in app.number_input if n.label=="参与住宿的女性人数").set_value(8)
            next(b for b in app.button if b.label=="查询本团建的报价").click().run()
            self.assertFalse(app.exception)
            order,_=business.load_order(load_case_from_disk(case.id).supplier_order_id)
            self.assertEqual(order.need.nights,["2026-11-20"])
            self.assertEqual(order.need.rooms,8)
            self.assertEqual([m.key for m in order.need.meals],["2026-11-20:午餐","2026-11-21:午餐"])
            self.assertTrue(any(q.kind=="hotel" for q in order.quotes))

    def test_disabled_coupon_cannot_be_claimed_or_redeemed(self):
        order=self.create();coupon=self.coupon()
        ops.set_coupon_active(coupon["policy_id"],False,"owner")
        with self.assertRaises(ValueError):ops.issue_coupon(coupon["policy_id"],order.customer_id)
        order=self.agreed(self.choose(order))
        with self.assertRaises(ValueError):business.pay_customer_deposit(order.id,coupon["id"])

    def test_new_booking_gate_and_lifecycle_link_to_core_case(self):
        from src.models import TravelCase,CaseStatus,CouncilDecision
        from src.journey import start_demo
        order=self.create(case_id="linked-core")
        case=TravelCase(customer_request="已确认活动",status=CaseStatus.COMPLETED,supplier_order_id=order.id,
            council_decision=CouncilDecision(theme="活动",summary="已确认",schedule=["室内交流","午餐"],merged_ideas=[],rationale=[]))
        with self.assertRaises(ValueError):start_demo(case)
        order=self.paid(order)
        business.pay_supplier_deposit(order.id,"owner")
        business.fulfill_bookings(order.id)
        self.assertEqual(start_demo(case).order_id,order.id)

    def test_confirmed_booking_does_not_reenable_cancelled_fireworks(self):
        from src.models import TravelCase,CaseStatus,CouncilDecision
        from src.journey import start_demo
        order=self.paid(self.create())
        business.pay_supplier_deposit(order.id,"owner")
        business.fulfill_bookings(order.id)
        case=TravelCase(customer_request="已确认活动",status=CaseStatus.COMPLETED,supplier_order_id=order.id,
            council_decision=CouncilDecision(theme="活动",summary="活动",schedule=["午餐","晚上篝火与烟花"],merged_ideas=[],rationale=[]))
        with self.assertRaises(ValueError):start_demo(case)
        with self.assertRaises(ValueError):business.confirm_linked_execution(case,"owner")
        business.confirm_linked_execution(case,"customer")
        demo=start_demo(case)
        self.assertIn("自愿休息",demo.active_schedule[1])
        self.assertEqual(case.council_decision.schedule[1],"晚上篝火与烟花")

    def test_expired_quotes_render_without_selectable_stale_inventory(self):
        from streamlit.testing.v1 import AppTest
        from src.models import OrganizationState
        order=self.choose(self.create())
        business.simulate_change(order.id)
        with patch("src.agents.build_agent_team",return_value=SimpleNamespace()),patch(
            "src.storage.load_case_from_disk",return_value=None),patch(
            "src.workflow.load_organization_state",return_value=OrganizationState()):
            app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/"app.py"),default_timeout=30).run()
            app.session_state["supplier_selected"]=order.id
            next(r for r in app.radio if r.label=="需求类型").set_value("酒店餐饮预订").run()
            self.assertEqual(len(app.exception),0)
            self.assertTrue(any("过期" in w.value for w in app.warning))
            self.assertFalse(any(s.label=="酒店" for s in app.selectbox))
            next(b for b in app.button if b.label=="重新查询报价").click().run()
            self.assertTrue(next(s for s in app.selectbox if s.label=="酒店").options)

    def test_updated_core_plan_does_not_reuse_an_old_execution_schedule(self):
        from src.models import TravelCase,CaseStatus,CouncilDecision
        from src.journey import start_demo
        order=self.paid(self.create())
        business.pay_supplier_deposit(order.id,"owner")
        business.fulfill_bookings(order.id)
        case=TravelCase(customer_request="活动",status=CaseStatus.COMPLETED,supplier_order_id=order.id,
            council_decision=CouncilDecision(theme="活动",summary="活动",schedule=["烟花"],merged_ideas=[],rationale=[]))
        business.confirm_linked_execution(case)
        case.council_decision.schedule=["新的室内交流安排"]
        case.plan_version+=1
        self.assertEqual(start_demo(case).active_schedule,["新的室内交流安排"])

    def test_fixed_supplier_works_without_model_config_from_legacy_workspace(self):
        from streamlit.testing.v1 import AppTest
        from src.agents import AgentConfigurationError
        with patch("src.agents.build_agent_team",side_effect=AgentConfigurationError("测试：未配置API")) as factory,patch(
            "src.storage.load_case_from_disk",return_value=None):
            app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/"app.py"),default_timeout=30)
            app.session_state["work_area"]="公司运营模块"
            app.run()
            self.assertEqual(app.session_state["work_area"],"团建策划与交付")
            next(r for r in app.radio if r.label=="需求类型").set_value("酒店餐饮预订").run()
            self.assertEqual(len(app.exception),0)
            self.assertTrue(any(b.label=="下一步" for b in app.button))
            app.sidebar.radio[0].set_value("公司老板").run()
            app.sidebar.radio[0].set_value("客户").run()
            self.assertEqual(len(app.exception),0)
            self.assertTrue(any(b.label=="下一步" for b in app.button))
            factory.assert_called_once()
