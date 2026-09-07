"""Explicit reserved evaluation metadata; not a recursive/prose sanitizer."""
LABEL_FIELDS = frozenset({
    "label", "labels", "ground_truth", "ground_truth_label", "reference_label", "expected_verdict",
    "expected_severity", "evaluation", "evaluation_label", "evaluation_result", "evaluation_outcome",
    "label_source_kind", "label_source_ref", "label_ai_visible", "is_correct", "answer", "answers",
    "difficulty", "case_name", "important_evidence", "verification_notes_ko", "rationale_ko",
})
