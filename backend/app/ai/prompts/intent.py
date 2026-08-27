"""The intent prompt, and the schema that constrains it.

The few-shot examples below are not decoration. Measured against ``qwen3:4b`` on
nine representative questions:

===========================================  ==============  =================
prompt                                       intent correct  "null" leaked
===========================================  ==============  =================
enum plus field descriptions only            4/9             5/9
the same schema plus these examples          9/9             0/9
===========================================  ==============  =================

Both produced valid JSON every time - the schema constraint is reliable, the
*semantics* are not without examples. So one example per intent is the floor, and
the set must keep at least one instance of every ``IntentKind``; a test asserts
that, because an intent with no example is the one the model gets wrong.

Note what is absent. There is no ``confidence`` field: the model answered "high"
on every question including the ones it got wrong, so a self-reported confidence
is worse than none - it invites the caller to trust it. Confidence here is
whether resolution actually matched something, which ``resolve`` computes.
"""

from typing import Any

from app.ai.models import IntentKind

# The JSON Schema handed to the provider. Every field is required and explicitly
# nullable rather than optional: a small model omits an optional key far more often
# than it fills a required one with null, and a missing key is harder to sanitise
# than a null.
INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": [kind.value for kind in IntentKind]},
        "kpi_hint": {"type": ["string", "null"]},
        "period_hint": {"type": ["string", "null"]},
        "dimension_hint": {"type": ["string", "null"]},
        "segment_hint": {"type": ["string", "null"]},
    },
    "required": ["intent", "kpi_hint", "period_hint", "dimension_hint", "segment_hint"],
}

# One per intent, in the order a reader would learn them. Kept as (question, json)
# pairs rather than a formatted blob so a test can assert the coverage.
FEW_SHOT: tuple[tuple[str, str], ...] = (
    (
        "Why did revenue decrease in July?",
        '{"intent":"ROOT_CAUSE_ANALYSIS","kpi_hint":"revenue","period_hint":"July",'
        '"dimension_hint":null,"segment_hint":null}',
    ),
    (
        "What is revenue this month?",
        '{"intent":"KPI_ANALYSIS","kpi_hint":"revenue","period_hint":"this month",'
        '"dimension_hint":null,"segment_hint":null}',
    ),
    (
        "Was there anything unusual about profit?",
        '{"intent":"ANOMALY_ANALYSIS","kpi_hint":"profit","period_hint":null,'
        '"dimension_hint":null,"segment_hint":null}',
    ),
    (
        "Which product drove the decline?",
        '{"intent":"CONTRIBUTION_ANALYSIS","kpi_hint":null,"period_hint":null,'
        '"dimension_hint":"product","segment_hint":null}',
    ),
    (
        "Break orders down by region.",
        '{"intent":"DIMENSION_ANALYSIS","kpi_hint":"orders","period_hint":null,'
        '"dimension_hint":"region","segment_hint":null}',
    ),
    (
        "What happened in Cairo?",
        '{"intent":"DRILL_DOWN","kpi_hint":null,"period_hint":null,'
        '"dimension_hint":null,"segment_hint":"Cairo"}',
    ),
    (
        "Summarise the investigation.",
        '{"intent":"INVESTIGATION_SUMMARY","kpi_hint":null,"period_hint":null,'
        '"dimension_hint":null,"segment_hint":null}',
    ),
    (
        "And how does that compare with the quarter before?",
        '{"intent":"FOLLOW_UP_ANALYSIS","kpi_hint":null,"period_hint":"the quarter before",'
        '"dimension_hint":null,"segment_hint":null}',
    ),
)

_INTENT_GLOSSARY = """intent, pick exactly one:
KPI_ANALYSIS          asks only for a value or a change, with no explanation
ROOT_CAUSE_ANALYSIS   asks WHY something changed
ANOMALY_ANALYSIS      asks whether something is unusual, a spike, an outlier
CONTRIBUTION_ANALYSIS asks WHICH or WHO moved it most
DIMENSION_ANALYSIS    asks how one named dimension breaks down
DRILL_DOWN            asks about one named segment value
INVESTIGATION_SUMMARY asks to recap an existing investigation
FOLLOW_UP_ANALYSIS    continues the previous question without naming a new metric"""


def build(
    *,
    kpi_names: tuple[str, ...],
    dimensions: tuple[str, ...],
    has_previous: bool = False,
) -> str:
    """The system prompt for one dataset.

    The available KPI and dimension names are included so the model copies real
    words rather than paraphrasing - but they are a *hint to the model*, not a
    constraint on the system: ``resolve`` validates every hint against the same
    lists again, because a model told what exists will still occasionally invent.
    """
    kpis = ", ".join(kpi_names) if kpi_names else "none configured"
    dims = ", ".join(dimensions) if dimensions else "none configured"
    examples = "\n".join(f"Q: {question}\n{answer}" for question, answer in FEW_SHOT)
    previous = (
        "\nThis question follows an earlier one in the same conversation, so it may "
        "refer back to it without naming the metric again."
        if has_previous
        else ""
    )
    return f"""You label a business-analytics question. You never compute numbers.

Dataset KPIs: {kpis}
Dataset dimensions: {dims}{previous}

{_INTENT_GLOSSARY}

Hints: copy the exact words from the question. Use JSON null, never the text "null".
kpi_hint is a metric name only, never the whole question.
Give a hint only when the question actually contains it.

Examples:
{examples}"""
