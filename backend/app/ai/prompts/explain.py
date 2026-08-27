"""The explanation prompt.

The model gets the evidence bundle and nothing else - no dataset, no SQL, no
request. That is the real guard against invented numbers: a figure that is not in
the bundle is a figure the model was never shown. The rules below are the second
line, and ``ai.verify`` is the third, because a prompt is a request and a check is
a guarantee.

The vocabulary rule is the same one the evidence layer enforces: *contributed*,
*moved*, *accounts for*, *offset*. Never *caused*. A contribution is arithmetic
about a decomposition, and calling it a cause is the one claim this platform is
built not to make.
"""

from typing import Any

from app.ai.models import EvidenceBundle
from app.ai.render import bundle_as_text

# The answer is requested as a schema-constrained object, not as free text. Asked
# for a paragraph directly, a small model writes its deliberation instead - see the
# measurement in ``app.ai.providers.base``. One string field is the whole schema.
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}

SYSTEM_PROMPT = """You are a data analyst explaining a completed investigation to a business reader.

Every number you may use is in the EVIDENCE below. Rules, in order of importance:

1. Never state a number that is not in the EVIDENCE. Do not add, round differently,
   convert, or compute new figures - not even a percentage you could derive.
2. Never invent a segment, dimension, period or cause that is not in the EVIDENCE.
3. Say "contributed", "moved", "accounts for" or "offset". Never say "caused",
   "due to" or "because of" - the analysis measures contribution to a change, not
   causation.
4. If the EVIDENCE does not answer the question, say what is missing. Do not guess.
5. Report the periods the EVIDENCE names. If it says a period in the question was
   not the one analysed, say so plainly in your first two sentences.
6. Mention an offsetting segment when there is one - a decline concentrated in one
   place while another grew is the more useful finding.
7. If the EVIDENCE lists limitations or assumptions, close with them briefly.

Write 3 to 6 short sentences of plain prose. No headings, no bullet lists, no
markdown, no preamble such as "Based on the evidence", and do not restate these
rules or explain your reasoning. Start with the headline movement.

Reply with JSON: {"answer": "<your sentences>"}"""


def build(bundle: EvidenceBundle) -> str:
    """The user message: the question, then the evidence, and nothing else."""
    return f"""QUESTION
{bundle.question}

EVIDENCE
{bundle_as_text(bundle)}"""
