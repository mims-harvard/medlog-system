-- Per-run "wide event" state: one row per run_id, folding in whichever
-- MedLog fragments (inference-start, artifacts, human-output, outcome,
-- feedback) have arrived so far - outcome/feedback often arrive hours or
-- days after the original inference, per the MedLog paper.
--
-- Every *State column uses the -If combinator so that events which don't
-- carry a given field (e.g. an outcome event has no model_instance) don't
-- overwrite an already-known value with an empty string once merged; see
-- 003_run_state_mv.sql for how these are populated incrementally.
--
-- No TTL: this is the "long-term summary" tier from the paper's tiered
-- retention guidance, kept far longer than the raw medlog_events rows.

CREATE TABLE IF NOT EXISTS medlog.medlog_run_state
(
    `RunId` String,

    `FirstSeenState` AggregateFunction(min, DateTime64(9)),
    `LastSeenState` AggregateFunction(max, DateTime64(9)),
    `EventCountState` AggregateFunction(count),

    `ModelIdState` AggregateFunction(argMaxIf, String, DateTime64(9), UInt8),
    `ModelVersionState` AggregateFunction(argMaxIf, String, DateTime64(9), UInt8),
    `CallerTypeState` AggregateFunction(argMaxIf, String, DateTime64(9), UInt8),
    `CallerIdState` AggregateFunction(argMaxIf, String, DateTime64(9), UInt8),
    `TargetTypeState` AggregateFunction(argMaxIf, String, DateTime64(9), UInt8),
    `TargetIdState` AggregateFunction(argMaxIf, String, DateTime64(9), UInt8),
    `PromptState` AggregateFunction(argMaxIf, String, DateTime64(9), UInt8),
    `InputUriState` AggregateFunction(argMaxIf, String, DateTime64(9), UInt8),

    `ArtifactUrisState` AggregateFunction(groupArrayIf, String, UInt8),

    `OutputTypeState` AggregateFunction(argMaxIf, String, DateTime64(9), UInt8),
    `OutputUriState` AggregateFunction(argMaxIf, String, DateTime64(9), UInt8),

    `OutcomeTypeState` AggregateFunction(argMaxIf, String, DateTime64(9), UInt8),
    `OutcomeOccurredAtState` AggregateFunction(argMaxIf, String, DateTime64(9), UInt8),
    `OutcomeEvidenceUriState` AggregateFunction(argMaxIf, String, DateTime64(9), UInt8),

    `FeedbackTypeState` AggregateFunction(argMaxIf, String, DateTime64(9), UInt8),
    `FeedbackRatingState` AggregateFunction(argMaxIf, String, DateTime64(9), UInt8),
    `FeedbackCommentState` AggregateFunction(argMaxIf, String, DateTime64(9), UInt8)
)
ENGINE = AggregatingMergeTree
ORDER BY RunId;
