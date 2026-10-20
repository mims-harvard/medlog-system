-- Raw MedLog event table: one row per event received by the collector.
-- Column set mirrors the clickhouse exporter's standard log schema
-- (see ClickStack/docker/clickhouse/local/init-db.sh for the equivalent
-- otel_logs table); MedLog-specific columns are MATERIALIZED from
-- LogAttributes so the exporter's fixed INSERT statement still works.
--
-- Short TTL: this is the "detailed artifact" tier from the MedLog paper's
-- tiered/lifecycle-aware retention guidance. Long-term summaries live in
-- medlog_run_state (002) instead.

CREATE DATABASE IF NOT EXISTS medlog;

CREATE TABLE IF NOT EXISTS medlog.medlog_events
(
    `Timestamp` DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    `TraceId` String CODEC(ZSTD(1)),
    `SpanId` String CODEC(ZSTD(1)),
    `TraceFlags` UInt8,
    `SeverityText` LowCardinality(String) CODEC(ZSTD(1)),
    `SeverityNumber` UInt8,
    `ServiceName` LowCardinality(String) CODEC(ZSTD(1)),
    `Body` String CODEC(ZSTD(1)),
    `ResourceSchemaUrl` LowCardinality(String) CODEC(ZSTD(1)),
    `ResourceAttributes` Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    `ScopeSchemaUrl` LowCardinality(String) CODEC(ZSTD(1)),
    `ScopeName` String CODEC(ZSTD(1)),
    `ScopeVersion` LowCardinality(String) CODEC(ZSTD(1)),
    `ScopeAttributes` Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    `LogAttributes` Map(LowCardinality(String), String) CODEC(ZSTD(1)),

    `EventType` LowCardinality(String) MATERIALIZED LogAttributes['medlog.event_type'] CODEC(ZSTD(1)),
    `EventId` String MATERIALIZED LogAttributes['medlog.event_id'] CODEC(ZSTD(1)),
    `RunId` String MATERIALIZED LogAttributes['medlog.run_id'] CODEC(ZSTD(1)),
    `ParentEventId` String MATERIALIZED LogAttributes['medlog.parent_event_id'] CODEC(ZSTD(1)),

    INDEX idx_event_id EventId TYPE bloom_filter(0.001) GRANULARITY 1,
    INDEX idx_run_id RunId TYPE bloom_filter(0.001) GRANULARITY 1,
    INDEX idx_parent_event_id ParentEventId TYPE bloom_filter(0.001) GRANULARITY 1,
    INDEX idx_log_attr_key mapKeys(LogAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_log_attr_value mapValues(LogAttributes) TYPE bloom_filter(0.01) GRANULARITY 1
)
ENGINE = MergeTree
PARTITION BY toDate(Timestamp)
ORDER BY (EventType, RunId, Timestamp)
TTL toDateTime(Timestamp) + INTERVAL 90 DAY
SETTINGS index_granularity = 8192, ttl_only_drop_parts = 1;
