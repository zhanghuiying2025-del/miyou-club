"""Three real role calls; cached checks apply only to exactly matching inputs."""
import hashlib
import json
import time

from src.models import FeasibilityReview, ManagedPlan, ReviewResult, ReviewFinding
from src.policy import blocks_review, program_review
from src.result_cache import configuration_key, is_fresh, policy_key


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def proposal_payload(case):
    return json.dumps([p.model_dump(exclude_defaults=True) for p in case.creative_opinions], ensure_ascii=False)


def input_fingerprint(case):
    return digest({"request": case.customer_request,
        "configuration": configuration_key(), "policy": case.cache_policy_fingerprint,
        "requirements": case.requirements.model_dump(mode="json") if case.requirements else None,
        "style": case.style_feedback.model_dump(mode="json") if case.style_feedback else None,
        "assessment": case.feedback_assessment.model_dump(mode="json") if case.feedback_assessment else None,
        "constraints": case.applied_constraints, "lead": case.lead_agent,
        "learning": [item.lesson.model_dump(mode="json") for item in case.learning_applications],
        "history": [{k: v for k, v in item.items() if k != "supplied_to"} for item in case.history_references],
        "proposals": [p.model_dump(mode="json") for p in case.creative_opinions],
        "sources": [s.model_dump(mode="json") for s in case.activity_sources]})


def plan_fingerprint(case):
    return digest({"inputs": input_fingerprint(case), "version": case.plan_version,
        "changes": case.draft_change_requests,
        "plan": case.council_decision.model_dump(mode="json") if case.council_decision else None,
        "feasibility": case.feasibility_review.model_dump(mode="json") if case.feasibility_review else None})


def normalize_review(review):
    result = review.model_copy(deep=True)
    for finding in result.findings:
        finding.decision_owner = {"老板": "owner", "客户": "customer"}.get(finding.decision_owner, finding.decision_owner)
        if finding.decision_owner not in {"owner", "customer"}:
            finding.decision_owner = None
        if finding.severity not in {"low", "medium", "high"}:
            finding.severity, finding.can_auto_fix = "high", False
    if any(blocks_review(result, f) for f in result.findings):
        result.passed = False
    return result


def executor_review(case, mandatory_only=False):
    findings = []
    if case.feasibility_review:
        for finding in case.feasibility_review.findings:
            converted = ReviewFinding.model_validate(finding.model_dump())
            converted.description += f"；依据：{finding.evidence}；解除条件：{finding.release_condition}"
            findings.append(converted)
    review = normalize_review(ReviewResult(reviewer_name="履约执行可行性检查", passed=True, findings=findings))
    if mandatory_only:
        review.findings = [f for f in review.findings if f.decision_owner in {"owner", "customer"}
            or (f.severity == "high" and not f.can_auto_fix)]
        review.passed = not review.findings
    return review


async def run_feasibility(workflow, case):
    from src.models import CaseStatus
    case.cache_policy_fingerprint = policy_key(workflow.policy)
    workflow._load_learning(case)
    workflow._load_history(case)
    fingerprint = input_fingerprint(case)
    if case.feasibility_review is None or case.feasibility_fingerprint != fingerprint or not is_fresh(case.feasibility_cached_at):
        result = await workflow._call(case, "fulfillment",
            "本轮只做策划阶段执行可行性检查，不预订、不审批、不重新创作完整方案。\n"
            f"已确认需求：{case.requirements.model_dump_json() if case.requirements else case.customer_request}\n"
            f"客户选择：{case.style_feedback.model_dump_json() if case.style_feedback else case.lead_agent}\n"
            f"当前有效约束：{json.dumps(case.applied_constraints,ensure_ascii=False)}\n"
            f"当前策划提案：{proposal_payload(case)}\n"
            "围绕客户选择的方向检查时间、资源依赖、参与限制和预算可行性；其他提案仅作备选。"
            "只报告有证据的具体问题，每项给出evidence、release_condition和至多两个替代。"
            "报价、容量、场地未预订在此阶段通常是pending_resources，不自动升级为高风险或要求老板审批。"
            "已有明确的硬性冲突或安全问题不能因此忽略。decision_owner只能为owner/customer/null；"
            "改变客户硬要求交customer，超经营权限交owner。没有阻塞则findings为空，不为制造讨论找问题。",
            FeasibilityReview)
        case.feasibility_review = result
        case.feasibility_fingerprint = fingerprint
        case.feasibility_cached_at = time.time()
        workflow._publish(case, "履约执行", "执行条件检查", result.summary)
        workflow._log(case, "履约执行", "feasibility_checked", result.summary)
    else:
        workflow._log(case, "程序", "feasibility_reused", "输入未变，复用已保存履约检查，无模型调用")
    case.status = CaseStatus.DELIBERATION


async def run_deliberation(workflow, case):
    from src.models import CaseStatus
    if case.feasibility_review is None or case.feasibility_fingerprint != input_fingerprint(case) or not is_fresh(case.feasibility_cached_at):
        await run_feasibility(workflow, case)
    result = await workflow._call(case, "manager",
        f"客户需求：{case.requirements.model_dump_json() if case.requirements else case.customer_request}\n"
        f"客户选择：{case.style_feedback.model_dump_json() if case.style_feedback else case.lead_agent}\n"
        f"已生效约束：{json.dumps(case.applied_constraints,ensure_ascii=False)}\n"
        f"策划提案：{proposal_payload(case)}\n"
        f"履约实际检查：{case.feasibility_review.model_dump_json()}\n"
        "没有策划互评，不得编造同事对话或共识。贡献只使用activity映射到原提案活动下标；不用compromise。"
        "逐条处理履约问题，不得通过删除问题绕过客户或老板决定。硬性冲突保留在findings或plan.customer_questions。"
        "普通分歧由你解决；这是方案整合与检查阶段，不宣称已落实资源。",
        ManagedPlan)
    case.council_decision = result.plan
    case.manager_resolutions = result.resolutions
    result.checks.operations = normalize_review(result.checks.operations)
    result.checks.risk = normalize_review(result.checks.risk)
    case.manager_checks = result.checks
    case.manager_checks_source = "manager"
    review = executor_review(case)
    # A manager's claim to have repaired a blocking condition needs a fresh
    # executor check of the final text. Human decisions can never be erased.
    case.final_recheck_required = any(blocks_review(review, f) for f in review.findings)
    case.manager_check_fingerprint = plan_fingerprint(case)
    case.manager_checks_cached_at = time.time()
    workflow._customer_objections(case, result.plan.customer_concerns,
        f"style:{case.plan_version}", "客户风格反馈",
        case.style_feedback.model_dump_json() if case.style_feedback else "",
        "经理整合结果的业务判断")
    workflow._publish(case, "项目经理", "整合与检查完成", result.plan.summary)
    for resolution in result.resolutions[:3]:
        workflow._publish(case, "项目经理", "修改依据", resolution)
    workflow._log(case, "项目经理", "managed_plan", result.plan.summary)
    if result.plan.unresolved_customer_decisions:
        case.pending_customer_step = CaseStatus.DELIBERATION
        workflow._ask_customer(case, result.plan.unresolved_customer_decisions)
    else:
        case.status = CaseStatus.WAITING_DRAFT


def cached_reviews(case):
    if not case.manager_checks or not is_fresh(case.manager_checks_cached_at) or case.manager_check_fingerprint != plan_fingerprint(case):
        return None
    checks = [normalize_review(case.manager_checks.operations), normalize_review(case.manager_checks.risk)]
    prefix = "履约最终复核" if case.manager_checks_source == "fulfillment" else "经理整合时"
    checks[0].reviewer_name, checks[1].reviewer_name = prefix + "运营检查", prefix + "风险检查"
    mandatory = executor_review(case, mandatory_only=True)
    if mandatory.findings:
        return checks + [mandatory, program_review(case)]
    if case.final_recheck_required:
        return None
    return checks + [program_review(case)]
