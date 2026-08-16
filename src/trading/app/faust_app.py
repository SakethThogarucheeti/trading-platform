from __future__ import annotations

import faust

from trading.config.settings import Settings


def build_faust_app(app_id: str, settings: Settings) -> faust.App:
    """
    Construct a faust App for either the ingestor (produce-only) or a
    worker process (consumes ``ticks``, runs one algo's pipeline).

    ``app_id`` must be stable per logical role (e.g. ``"ingestor"``,
    ``"worker-rsi_mean_reversion"``) — faust uses it to derive Kafka
    consumer group names and internal topic prefixes.
    """
    return faust.App(
        app_id,
        broker=settings.kafka_broker_url,
        store="memory://",
        topic_partitions=8,
        # Each algo runs its own worker process/App instance; faust's built-in
        # aiohttp monitoring server (default port 6066) would collide across
        # them on the same host. Nothing consumes it — TickAgentComponent
        # only needs the Kafka consumer, not an HTTP API.
        web_enabled=False,
        # TickAgentComponent has no leader-only logic (no @app.timer, no
        # leader-gated work) — disables the internal leader-election topic
        # faust otherwise creates and coordinates over on every startup.
        topic_disable_leader=True,
    )


def ticks_topic(app: faust.App) -> faust.types.topics.TopicT:
    """
    Shared ``ticks`` topic definition, used on the worker (consumer) side.

    Value is a JSON-encoded ``TickEvent`` (raw bytes in, ``TickEvent`` schema
    lives in trading-types — kept as plain JSON rather than a faust.Record so
    the wire format matches the rest of the codebase's pydantic serialization).
    Keyed by instrument_token (as str) so all ticks for one instrument land on
    the same partition and stay ordered.

    The ingestor (producer) side does not build a faust App at all — it has
    no agents to run, so it produces directly via aiokafka.AIOKafkaProducer
    (see trading.tick_ingest.service.publisher.TickPublisher) rather than
    paying for a faust App's consumer/rebalancing machinery it would never use.
    """
    return app.topic(
        "ticks",
        key_type=str,
        key_serializer="raw",
        value_type=bytes,
        # App.conf.value_serializer defaults to "json", which would decode
        # each message into a dict before TickAgentComponent ever sees it —
        # breaking its TickEvent.model_validate_json(raw) call. Override to
        # "raw" so the topic actually yields bytes, matching value_type.
        value_serializer="raw",
        partitions=8,
    )


TICKS_TOPIC_NAME = "ticks"
TICKS_TOPIC_PARTITIONS = 8
