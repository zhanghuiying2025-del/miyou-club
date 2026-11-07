"""Bounded per-case cache of validated planning outputs, never external actions."""
import hashlib
import json
import math
import os
import time

from pydantic import ValidationError
from src.models import ValidatedResult


CACHE_VERSION = "planning-results-v1"
CACHE_TTL = 900
ALLOWED = {"BriefCreativeRound", "CreativeRound", "ProposalBrief", "FeasibilityReview", "ManagedPlan", "ManagerReview", "CouncilDecision"}


def policy_key(policy):
    return hashlib.sha256(policy.model_dump_json().encode()).hexdigest()


def configuration_key():
    from src.agents import response_prefix, role_instructions
    from src.models import ManagedPlan, ManagerReview, FeasibilityReview
    values = [CACHE_VERSION, os.getenv("LLM_MODEL", ""), os.getenv("LLM_BASE_URL", ""),
        *[response_prefix(m) for m in (ManagedPlan, ManagerReview, FeasibilityReview)],
        *[role_instructions(r) for r in ("manager", "planner", "fulfillment")]]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def is_fresh(created_at, now=None):
    now = time.time() if now is None else now
    return math.isfinite(created_at) and 0 <= now - created_at < CACHE_TTL


def cache_key(case, role, prompt, response_model, policy, instructions):
    if case.service_mode != "travel_planning" or response_model.__name__ not in ALLOWED:
        return None
    from src.agents import _prompt
    value = {"version": CACHE_VERSION, "case_id": case.id, "role": role,
        "model": os.getenv("LLM_MODEL", ""), "endpoint": os.getenv("LLM_BASE_URL", ""),
        "instructions": instructions, "request": _prompt(prompt, response_model),
        "policy": policy.model_dump(mode="json"), "plan_version": case.plan_version,
        "stage": case.status.value, "draft_revision": case.draft_revision_count,
        "revision": case.revision_count,
        "protocol": case.planning_protocol,
        "requirements": case.requirements.model_dump(mode="json") if case.requirements else None,
        "constraints": case.applied_constraints, "customer_request": case.customer_request,
        "sources": [x.model_dump(mode="json") for x in case.activity_sources]}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode()).hexdigest()


def valid_result(value):
    name = type(value).__name__
    if name == "ProposalBrief":
        return bool(value.concept_name.strip() and value.one_sentence_pitch.strip() and
                    value.recommended_activities and all(item.strip() for item in value.recommended_activities))
    plan = value.plan if name == "ManagedPlan" else value if name == "CouncilDecision" else None
    if plan is not None and (not plan.schedule or any(not x.strip() for x in plan.schedule)):
        return False
    checks = value.checks if name == "ManagedPlan" else value if name == "ManagerReview" else None
    reviews = [checks.operations, checks.risk] if checks else [value] if name == "FeasibilityReview" else []
    for review in reviews:
        for finding in review.findings:
            if finding.severity not in {"low", "medium", "high"} or finding.decision_owner not in {None, "owner", "customer", "老板", "客户"}:
                return False
            if not finding.description.strip():
                return False
            if name == "FeasibilityReview" and (not finding.evidence.strip() or not finding.release_condition.strip()):
                return False
    return name in ALLOWED


def get_result(case, key, response_model, now=None):
    if key is None:
        return None
    now = time.time() if now is None else now
    for entry in reversed(case.result_cache):
        if (entry.key != key or entry.response_type != response_model.__name__
            or not math.isfinite(entry.expires_at) or not math.isfinite(entry.created_at)
            or not entry.created_at <= now < entry.expires_at):
            continue
        try:
            result = response_model.model_validate(entry.payload)
        except (ValidationError, TypeError, ValueError):
            continue
        if valid_result(result):
            return result
    return None


def store_result(case, key, value, now=None):
    if key is None or not valid_result(value):
        return
    now = time.time() if now is None else now
    case.result_cache = [x for x in case.result_cache if x.key != key and x.expires_at > now][-23:]
    case.result_cache.append(ValidatedResult(key=key, response_type=type(value).__name__,
        payload=value.model_dump(mode="json"), created_at=now, expires_at=now + CACHE_TTL))
