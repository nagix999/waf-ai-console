"""Input names controlled by the server, never editable schema fields."""

SERVER_CONTROL_FIELDS = frozenset({
    "source_system", "analysis_purpose", "ingest_channel", "analysis_scope", "purpose",
    "verdict", "severity", "status", "result", "result_json", "confidence_score",
    "summary_ko", "event_fingerprint", "id", "analysis_id", "prompt_version", "model_profile",
    "started_at", "completed_at", "created_at", "total_elapsed_ms", "queue_wait_ms",
    "processing_duration_ms", "input_truncated", "threat_category",
    "provenance", "is_test", "base_url", "api_key", "instructions", "system_prompt",
    "enable_thinking", "run_id", "attempt_count", "lease_owner", "lease_expires_at",
    "payload_ciphertext", "encryption_key_version", "error_code", "error_message",
    "prompt_policy_version_id", "prompt_snapshot_ciphertext", "policy_text", "policy_version_id",
    "fixed_rules_version", "primary_instructions", "verifier_instructions",
    "model_test_run_id", "include_dataset", "dataset_version", "dataset_hash",
    "test_run_id", "test_category", "test_difficulty", "idempotency_key", "test_name",
    "input_schema_version_id", "input_schema_snapshot_ciphertext", "input_schema_metadata",
    "input_schema", "field_definitions", "schema_snapshot", "selection_origin",
})
