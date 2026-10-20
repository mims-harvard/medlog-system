-- Queryable, always-current "merged MedLog record" per run: correct
-- regardless of which fragments (start/artifacts/output/outcome/feedback)
-- have arrived so far, and updates automatically as later ones land.

CREATE VIEW IF NOT EXISTS medlog.medlog_run_summary AS
SELECT
    RunId,
    minMerge(FirstSeenState) AS first_seen,
    maxMerge(LastSeenState) AS last_seen,
    countMerge(EventCountState) AS event_count,

    argMaxIfMerge(ModelIdState) AS model_id,
    argMaxIfMerge(ModelVersionState) AS model_version,
    argMaxIfMerge(CallerTypeState) AS caller_type,
    argMaxIfMerge(CallerIdState) AS caller_id,
    argMaxIfMerge(TargetTypeState) AS target_type,
    argMaxIfMerge(TargetIdState) AS target_id,
    argMaxIfMerge(PromptState) AS prompt,
    argMaxIfMerge(InputUriState) AS input_uri,

    groupArrayIfMerge(ArtifactUrisState) AS artifact_uris,

    argMaxIfMerge(OutputTypeState) AS output_type,
    argMaxIfMerge(OutputUriState) AS output_uri,

    argMaxIfMerge(OutcomeTypeState) AS outcome_type,
    argMaxIfMerge(OutcomeOccurredAtState) AS outcome_occurred_at,
    argMaxIfMerge(OutcomeEvidenceUriState) AS outcome_evidence_uri,

    argMaxIfMerge(FeedbackTypeState) AS feedback_type,
    toFloat64OrNull(argMaxIfMerge(FeedbackRatingState)) AS feedback_rating,
    argMaxIfMerge(FeedbackCommentState) AS feedback_comment
FROM medlog.medlog_run_state
GROUP BY RunId;
