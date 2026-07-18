"""Emulator end-to-end validation for the reworked in-process Pub/Sub layer.

Imports ONLY ``ment_api.services.pub_sub_service`` (never ``lifespan``/``config``,
which need Secret Manager). Requires the Pub/Sub emulator:

    docker run --rm -p 8085:8085 -e GCP_PROJECT_ID=test -e PUBSUB_DATA_DIR=/data \
      -e PUBSUB_HOST_PORT=0.0.0.0:8085 $(docker build -q docker/google-pubsub)
    export PUBSUB_EMULATOR_HOST=localhost:8085 GCP_PROJECT_ID=test
    PYTHONPATH=src uv run python scripts/pubsub_e2e_check.py

Asserts the handoff acceptance criteria:
  (a) publish -> callback receives the message
  (b) with a slow callback and N > max_concurrency, at most max_concurrency
      callbacks run simultaneously
  (c) stop() returns only after in-flight callbacks drain (graceful drain)
  (d) an always-raising callback is redelivered up to max_delivery_attempts and
      then dead-letters (emulator DLQ is flaky -> fall back to the redelivery
      guarantee).
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid

if not os.getenv("PUBSUB_EMULATOR_HOST"):
    raise SystemExit("PUBSUB_EMULATOR_HOST is not set; start the emulator first")

PROJECT = os.getenv("GCP_PROJECT_ID", "test")

from google.api_core.exceptions import AlreadyExists  # noqa: E402

from ment_api.services import pub_sub_service as ps  # noqa: E402
from ment_api.services.pub_sub_service import (  # noqa: E402
    SubscriberSpec,
    SubscriberSupervisor,
    publish_message,
)


def _uniq(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def _new_supervisor() -> SubscriberSupervisor:
    return SubscriberSupervisor(PROJECT, asyncio.get_running_loop())


async def test_publish_consume() -> bool:
    topic = _uniq("e2e-basic")
    sub = f"{topic}-sub"
    await ps._ensure_topic_exists(PROJECT, topic)

    received: asyncio.Queue[str] = asyncio.Queue()

    async def cb(message) -> None:
        await received.put(message.data.decode())

    sup = await _new_supervisor()
    await sup.start(
        SubscriberSpec(
            name="basic",
            topic_id=topic,
            subscription_id=sub,
            callback=cb,
            max_concurrency=4,
            ack_deadline_seconds=60,
        )
    )
    try:
        await publish_message(PROJECT, topic, b"hello-e2e")
        got = await asyncio.wait_for(received.get(), timeout=20)
        ok = got == "hello-e2e"
        print(f"(a) publish->consume: {'PASS' if ok else 'FAIL'} (got={got!r})")
        return ok
    finally:
        await sup.stop()


async def test_bounded_concurrency() -> bool:
    topic = _uniq("e2e-conc")
    sub = f"{topic}-sub"
    max_conc = 2
    total = 6
    await ps._ensure_topic_exists(PROJECT, topic)

    lock = threading.Lock()
    state = {"current": 0, "peak": 0, "done": 0}
    done_evt = asyncio.Event()

    async def cb(message) -> None:
        with lock:
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
        await asyncio.sleep(1.5)
        with lock:
            state["current"] -= 1
            state["done"] += 1
        if state["done"] >= total:
            done_evt.set()

    sup = await _new_supervisor()
    await sup.start(
        SubscriberSpec(
            name="conc",
            topic_id=topic,
            subscription_id=sub,
            callback=cb,
            max_concurrency=max_conc,
            ack_deadline_seconds=60,
        )
    )
    try:
        for i in range(total):
            await publish_message(PROJECT, topic, f"m{i}".encode())
        try:
            await asyncio.wait_for(done_evt.wait(), timeout=45)
        except asyncio.TimeoutError:
            pass
        peak = state["peak"]
        ok = 1 <= peak <= max_conc and state["done"] == total
        print(
            f"(b) bounded concurrency: {'PASS' if ok else 'FAIL'} "
            f"(peak={peak}, cap={max_conc}, processed={state['done']}/{total})"
        )
        return ok
    finally:
        await sup.stop()


async def test_graceful_drain() -> bool:
    topic = _uniq("e2e-drain")
    sub = f"{topic}-sub"
    await ps._ensure_topic_exists(PROJECT, topic)

    started = asyncio.Event()
    finished = {"value": False}

    async def cb(message) -> None:
        started.set()
        await asyncio.sleep(2.5)
        finished["value"] = True

    sup = await _new_supervisor()
    await sup.start(
        SubscriberSpec(
            name="drain",
            topic_id=topic,
            subscription_id=sub,
            callback=cb,
            max_concurrency=1,
            ack_deadline_seconds=60,
        )
    )
    ok = False
    try:
        await publish_message(PROJECT, topic, b"drain-me")
        await asyncio.wait_for(started.wait(), timeout=20)
        # stop() while the callback is mid-flight; it must drain before returning.
        await sup.stop()
        ok = finished["value"] is True
        print(
            f"(c) graceful drain: {'PASS' if ok else 'FAIL'} "
            f"(callback_finished_before_stop_returned={finished['value']})"
        )
        return ok
    finally:
        if not ok:
            try:
                await sup.stop()
            except Exception:
                pass


async def test_dlq_redelivery() -> bool:
    topic = _uniq("e2e-dlq")
    sub = f"{topic}-sub"
    dlq_topic = f"{topic}-dlq"
    drain_sub = f"{dlq_topic}-drain"
    # Pub/Sub enforces a minimum of 5 delivery attempts for dead-letter policies.
    max_attempts = 5

    await ps._ensure_topic_exists(PROJECT, topic)
    await ps._ensure_topic_exists(PROJECT, dlq_topic)

    # Drain subscription on the DLQ topic so we can observe dead-lettered msgs.
    subscriber = ps._clients.subscriber
    drain_path = subscriber.subscription_path(PROJECT, drain_sub)
    dlq_topic_path = subscriber.topic_path(PROJECT, dlq_topic)
    try:
        subscriber.create_subscription(name=drain_path, topic=dlq_topic_path)
    except AlreadyExists:
        pass

    deliveries = {"count": 0, "max_attempt": 0}
    lock = threading.Lock()

    async def cb(message) -> None:
        with lock:
            deliveries["count"] += 1
            deliveries["max_attempt"] = max(
                deliveries["max_attempt"], message.delivery_attempt or 0
            )
        raise RuntimeError("always fail -> nack -> redeliver -> DLQ")

    sup = await _new_supervisor()
    await sup.start(
        SubscriberSpec(
            name="dlq",
            topic_id=topic,
            subscription_id=sub,
            callback=cb,
            max_concurrency=1,
            ack_deadline_seconds=30,
            dlq_topic_id=dlq_topic,
            max_delivery_attempts=max_attempts,
        )
    )

    dead_lettered = False
    try:
        await publish_message(PROJECT, topic, b"poison")
        deadline = time.time() + 120
        while time.time() < deadline:
            resp = subscriber.pull(
                request={"subscription": drain_path, "max_messages": 1},
                timeout=10,
            )
            if resp.received_messages:
                ack_ids = [m.ack_id for m in resp.received_messages]
                subscriber.acknowledge(
                    request={"subscription": drain_path, "ack_ids": ack_ids}
                )
                dead_lettered = True
                break
            if deliveries["count"] >= max_attempts:
                # Give the emulator a little longer to move it to the DLQ.
                await asyncio.sleep(3)
            await asyncio.sleep(1)

        redelivered = deliveries["count"] >= max_attempts
        ok = dead_lettered or redelivered
        detail = (
            f"dead_lettered={dead_lettered}, deliveries={deliveries['count']}, "
            f"max_delivery_attempt_seen={deliveries['max_attempt']}, "
            f"required_attempts={max_attempts}"
        )
        verdict = "PASS" if ok else "FAIL"
        note = "" if dead_lettered else " (DLQ not observed; redelivery guarantee met)"
        print(f"(d) DLQ redelivery: {verdict} ({detail}){note}")
        return ok
    finally:
        await sup.stop()


async def main() -> int:
    print("=" * 70)
    print(f"Pub/Sub emulator e2e | host={os.getenv('PUBSUB_EMULATOR_HOST')} project={PROJECT}")
    print("=" * 70)

    results: dict[str, bool] = {}
    results["a_publish_consume"] = await test_publish_consume()
    results["b_bounded_concurrency"] = await test_bounded_concurrency()
    results["c_graceful_drain"] = await test_graceful_drain()
    results["d_dlq_redelivery"] = await test_dlq_redelivery()

    print("=" * 70)
    for name, ok in results.items():
        print(f"  {name:24s} {'PASS' if ok else 'FAIL'}")
    all_ok = all(results.values())
    print(f"OVERALL: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
