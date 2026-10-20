#!/usr/bin/env bash
#
# Export aggregate MedLog statistics from ClickHouse.
#
# Prints each table and writes it as CSV, for reporting ingest volume, record
# completeness and storage footprint without a dashboard. Every query is an
# aggregate over metadata: no prompts, outputs, target identifiers or URIs are
# read, so the CSVs are safe to share off a host holding sensitive records.
#
# Usage:
#   scripts/medlog_summary_stats.sh
#   scripts/medlog_summary_stats.sh --output-dir /tmp/stats

set -euo pipefail

OUT=""
while [ $# -gt 0 ]; do
    case "$1" in
        --output-dir|-o) OUT="$2"; shift 2 ;;
        --help|-h) sed -n '3,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
[ -n "$OUT" ] || OUT="$REPO/output/medlog-stats"
mkdir -p "$OUT"

COMPOSE="docker compose -f ClickStack/docker-compose.yml -f docker-compose.yml"
export MEDLOG_API_KEY="${MEDLOG_API_KEY:-stats-placeholder}"
export HYPERDX_API_KEY="${HYPERDX_API_KEY:-}"

if ! $COMPOSE exec -T ch-server clickhouse-client --query "SELECT 1" >/dev/null 2>&1; then
    echo "error: cannot reach ch-server - is the stack running?" >&2
    exit 1
fi

query() {
    name="$1"; title="$2"; sql="$3"
    printf '\n== %s ==\n' "$title"
    $COMPOSE exec -T ch-server clickhouse-client --query "$sql" --format PrettyCompactMonoBlock
    $COMPOSE exec -T ch-server clickhouse-client --query "$sql" --format CSVWithNames > "$OUT/$name.csv"
}

query overview "Overview" "
SELECT
    count() AS events,
    uniqExact(RunId) AS runs,
    uniqExact(EventType) AS event_types,
    uniqExact(LogAttributes['medlog.model_instance.model_id']) - 1 AS models,
    min(Timestamp) AS first_event,
    max(Timestamp) AS last_event
FROM medlog.medlog_events"

query events_by_type "Events by type" "
SELECT EventType, count() AS events, uniqExact(RunId) AS runs
FROM medlog.medlog_events
GROUP BY EventType
ORDER BY events DESC"

query events_per_day "Events per day" "
SELECT toDate(Timestamp) AS day, count() AS events, uniqExact(RunId) AS runs
FROM medlog.medlog_events
GROUP BY day
ORDER BY day"

query runs_by_model "Runs by model" "
SELECT model_id, model_version, count() AS runs, sum(event_count) AS events
FROM medlog.medlog_run_summary
GROUP BY model_id, model_version
ORDER BY runs DESC"

query runs_by_caller "Runs by caller and target type" "
SELECT caller_type, target_type, count() AS runs
FROM medlog.medlog_run_summary
GROUP BY caller_type, target_type
ORDER BY runs DESC"

# How complete the merged per-run record is: outcome and feedback fragments arrive
# late (or never), so this is the coverage figure to report alongside run counts.
query run_completeness "Run record completeness" "
SELECT
    count() AS runs,
    countIf(length(artifact_uris) > 0) AS with_artifact,
    countIf(output_uri != '') AS with_output,
    countIf(outcome_type != '') AS with_outcome,
    countIf(feedback_type != '') AS with_feedback,
    round(100 * countIf(outcome_type != '') / count(), 1) AS pct_with_outcome,
    round(100 * countIf(feedback_type != '') / count(), 1) AS pct_with_feedback
FROM medlog.medlog_run_summary"

query events_per_run "Events per run" "
SELECT
    min(event_count) AS min,
    round(avg(event_count), 2) AS mean,
    quantileExact(0.5)(event_count) AS median,
    quantileExact(0.95)(event_count) AS p95,
    max(event_count) AS max
FROM medlog.medlog_run_summary"

query run_duration "Run span (first to last event, ms)" "
WITH dateDiff('millisecond', first_seen, last_seen) AS span_ms
SELECT
    min(span_ms) AS min_ms,
    round(avg(span_ms), 1) AS mean_ms,
    quantileExact(0.5)(span_ms) AS median_ms,
    quantileExact(0.95)(span_ms) AS p95_ms,
    max(span_ms) AS max_ms
FROM medlog.medlog_run_summary"

query latency "Reported inference latency (ms)" "
WITH toFloat64OrNull(LogAttributes['medlog.system_metadata.latency_ms']) AS latency_ms
SELECT
    count(latency_ms) AS events_with_latency,
    round(avg(latency_ms), 1) AS mean_ms,
    round(quantileExact(0.5)(latency_ms), 1) AS median_ms,
    round(quantileExact(0.95)(latency_ms), 1) AS p95_ms,
    round(max(latency_ms), 1) AS max_ms
FROM medlog.medlog_events
WHERE latency_ms IS NOT NULL"

query outcomes "Outcome and feedback types" "
SELECT 'outcome' AS kind, outcome_type AS value, count() AS runs
FROM medlog.medlog_run_summary WHERE outcome_type != '' GROUP BY value
UNION ALL
SELECT 'feedback' AS kind, feedback_type AS value, count() AS runs
FROM medlog.medlog_run_summary WHERE feedback_type != '' GROUP BY value
ORDER BY kind, runs DESC"

# Storage footprint per table: the figure to quote for retention-tier cost.
query storage "Storage footprint" "
SELECT
    table,
    sum(rows) AS total_rows,
    formatReadableSize(sum(bytes_on_disk)) AS on_disk,
    formatReadableSize(sum(data_uncompressed_bytes)) AS uncompressed,
    round(sum(data_uncompressed_bytes) / greatest(sum(bytes_on_disk), 1), 2) AS compression_ratio,
    round(sum(bytes_on_disk) / greatest(sum(rows), 1), 1) AS bytes_per_row
FROM system.parts
WHERE database = 'medlog' AND active
GROUP BY table
ORDER BY table"

echo
echo "CSVs written to $OUT:"
ls -1 "$OUT"
