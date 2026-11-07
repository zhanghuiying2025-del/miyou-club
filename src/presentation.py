"""Short, factual progress projections; never expose prompts or private finances."""
TASK_LABELS = {
    "DocumentIntakeResult": "读取文档中的已有信息，整理必要缺项",
    "ActivityAudit": "核对原活动方案的事实、规则与缺项",
    "ActivityExecutionPlan": "细化原活动的筹备分工与现场执行清单",
    "IntakeResult": "整理需求与硬性条件", "AttachmentInsight": "核对附件中的参考信息",
    "CreativeRound": "准备三种风格提案", "PeerReviewRound": "核对各风格的取舍与折中",
    "BriefCreativeRound": "准备三种精简风格提案", "ProposalBrief": "按客户选定的风格拟定提案",
    "FeasibilityReview": "核查所选方向的执行条件",
    "ManagedPlan": "整合方案并同步检查运营与风险",
    "FeedbackAssessment": "核查你提出的质疑与投诉", "CouncilDecision": "整理可执行行程",
    "ManagerReview": "检查行程衔接、需求与风险",
}


def task_message(schema, finished=False):
    task = TASK_LABELS.get(schema, "处理当前安排")
    return f"我已完成{task}，正在交给下一环节。" if finished else f"我正在{task}，完成后会展示需要你确认的重点。"


def short_text(value, limit=100):
    value = " ".join(value.split())
    return value if len(value) <= limit else value[:limit - 1] + "…"
