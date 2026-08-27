"""Step 6: the bundle, as prose. The model's second and last appearance.

Always returns an answer. If the model is unreachable, or its prose quotes a figure
the evidence does not contain, the template takes over and the caller is told which
happened. An unavailable LLM must not cost a reader the analysis - that is the whole
reason the structured pipeline exists ahead of this step.
"""

from dataclasses import dataclass

from app.ai import render, verify
from app.ai.models import EvidenceBundle
from app.ai.prompts import explain as explain_prompt
from app.ai.providers import LlmProvider, Message
from app.core.exceptions import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Six short sentences plus JSON overhead. Bounded because an unbounded generation
# on a local model is the one way a request outlives its timeout for a reason
# nobody can see.
ANSWER_MAX_TOKENS = 400


@dataclass(frozen=True)
class Explanation:
    """The answer, and how it came to be.

    ``is_template`` is surfaced all the way to the API on purpose: a reader should
    never be shown generated prose and template prose as if they were the same
    thing.
    """

    answer: str
    is_template: bool
    limitations: tuple[str, ...] = ()


def explain(bundle: EvidenceBundle, *, provider: LlmProvider) -> Explanation:
    """Generate, check, and fall back if either step fails."""
    messages = [
        Message("system", explain_prompt.SYSTEM_PROMPT),
        Message("user", explain_prompt.build(bundle)),
    ]

    try:
        # Schema-constrained, like the intent call. Asked for the paragraph as free
        # text, a small model writes its deliberation instead of the answer - the
        # measurement is in ``providers.base``.
        generated = provider.complete_json(
            messages, explain_prompt.ANSWER_SCHEMA, max_tokens=ANSWER_MAX_TOKENS
        )
        answer = str(generated.get("answer") or "")
    except AppError as exc:
        logger.warning(
            "ai_explanation_unavailable",
            extra={"code": exc.code, "investigation_id": str(bundle.investigation_id)},
        )
        return Explanation(
            answer=render.template_answer(bundle),
            is_template=True,
            limitations=(
                "The written explanation was assembled from the evidence rather than generated, "
                f"because the language model was unavailable ({exc.code}).",
            ),
        )

    if not answer.strip():
        return Explanation(
            answer=render.template_answer(bundle),
            is_template=True,
            limitations=(
                "The written explanation was assembled from the evidence rather than generated, "
                "because the language model returned nothing.",
            ),
        )

    verdict = verify.check(answer, bundle)
    if not verdict.grounded:
        # The one failure mode this layer exists to prevent. The generated text is
        # discarded rather than shown with a warning: a number that was never
        # measured has no place on the page at all.
        return Explanation(
            answer=render.template_answer(bundle),
            is_template=True,
            limitations=(
                "The written explanation was assembled from the evidence rather than generated, "
                "because the generated text quoted figures the analysis did not produce.",
            ),
        )

    causal = verify.causal_phrases(answer)
    if causal:
        # The house vocabulary rule, made mechanical rather than merely asked for.
        # A contribution is arithmetic about a decomposition, and prose asserting it
        # is a cause overstates every number above it.
        logger.warning(
            "ai_answer_rejected_causal_claim",
            extra={
                "investigation_id": str(bundle.investigation_id),
                "phrases": list(causal),
            },
        )
        return Explanation(
            answer=render.template_answer(bundle),
            is_template=True,
            limitations=(
                "The written explanation was assembled from the evidence rather than generated, "
                "because the generated text claimed causation rather than contribution.",
            ),
        )

    return Explanation(answer=answer.strip(), is_template=False)
