-- Populates medlog_run_state incrementally: every INSERT into medlog_events
-- (whatever event type it is) contributes one partial-state row per run_id,
-- and the AggregatingMergeTree engine folds it into the existing state in
-- the background - this is the actual "merge events that arrive at
-- different times" mechanism described in the MedLog paper.

CREATE MATERIALIZED VIEW IF NOT EXISTS medlog.mv_medlog_run_state
TO medlog.medlog_run_state
AS
SELECT
    RunId,
    minState(Timestamp) AS FirstSeenState,
    maxState(Timestamp) AS LastSeenState,
    countState() AS EventCountState,

    argMaxIfState(LogAttributes['medlog.model_instance.model_id'], Timestamp, LogAttributes['medlog.model_instance.model_id'] != '') AS ModelIdState,
    argMaxIfState(LogAttributes['medlog.model_instance.model_version'], Timestamp, LogAttributes['medlog.model_instance.model_version'] != '') AS ModelVersionState,
    argMaxIfState(LogAttributes['medlog.user_identity.caller_type'], Timestamp, LogAttributes['medlog.user_identity.caller_type'] != '') AS CallerTypeState,
    argMaxIfState(LogAttributes['medlog.user_identity.caller_id'], Timestamp, LogAttributes['medlog.user_identity.caller_id'] != '') AS CallerIdState,
    argMaxIfState(LogAttributes['medlog.target_identity.target_type'], Timestamp, LogAttributes['medlog.target_identity.target_type'] != '') AS TargetTypeState,
    argMaxIfState(LogAttributes['medlog.target_identity.target_id'], Timestamp, LogAttributes['medlog.target_identity.target_id'] != '') AS TargetIdState,
    argMaxIfState(LogAttributes['medlog.inputs.prompt'], Timestamp, LogAttributes['medlog.inputs.prompt'] != '') AS PromptState,
    argMaxIfState(LogAttributes['medlog.inputs.input_uri'], Timestamp, LogAttributes['medlog.inputs.input_uri'] != '') AS InputUriState,

    groupArrayIfState(LogAttributes['medlog.artifact.artifact_uri'], LogAttributes['medlog.artifact.artifact_uri'] != '') AS ArtifactUrisState,

    argMaxIfState(LogAttributes['medlog.output.output_type'], Timestamp, LogAttributes['medlog.output.output_type'] != '') AS OutputTypeState,
    argMaxIfState(LogAttributes['medlog.output.output_uri'], Timestamp, LogAttributes['medlog.output.output_uri'] != '') AS OutputUriState,

    argMaxIfState(LogAttributes['medlog.outcome.outcome_type'], Timestamp, LogAttributes['medlog.outcome.outcome_type'] != '') AS OutcomeTypeState,
    argMaxIfState(LogAttributes['medlog.outcome.occurred_at'], Timestamp, LogAttributes['medlog.outcome.occurred_at'] != '') AS OutcomeOccurredAtState,
    argMaxIfState(LogAttributes['medlog.outcome.evidence_uri'], Timestamp, LogAttributes['medlog.outcome.evidence_uri'] != '') AS OutcomeEvidenceUriState,

    argMaxIfState(LogAttributes['medlog.feedback.feedback_type'], Timestamp, LogAttributes['medlog.feedback.feedback_type'] != '') AS FeedbackTypeState,
    argMaxIfState(LogAttributes['medlog.feedback.rating_value'], Timestamp, LogAttributes['medlog.feedback.rating_value'] != '') AS FeedbackRatingState,
    argMaxIfState(LogAttributes['medlog.feedback.comment'], Timestamp, LogAttributes['medlog.feedback.comment'] != '') AS FeedbackCommentState
FROM medlog.medlog_events
WHERE RunId != ''
GROUP BY RunId;
