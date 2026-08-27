"""Prompts, kept as data rather than prose embedded in call sites.

Two reasons they live in their own package. A prompt is the layer's least stable
part and the one most worth diffing, so it should not be tangled with control
flow. And the intent prompt's few-shot examples are *measured* - swapping the model
means re-running that measurement, and it has to be obvious where to look.
"""
