# TASK-013: Broker State Instrumentation

**Feature**: expose-queue-threads
**Spec**: `sdd/specs/expose-queue-threads.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-009
**Assigned-to**: unassigned

---

## Context

> The RabbitMQ broker (BrokerConsumer) processes tasks via callbacks. This task instruments
> the callback wrapper to report task state to the shared StateTracker. The broker is
> independent from QWorker's event loop, so the StateTracker must be injected optionally.
> Implements: Spec Module 5 (Broker State Instrumentation).

---

## Scope

- Modify `qw/broker/consumer.py`:
  - Update `BrokerConsumer.__init__()` to accept optional `state_tracker` kwarg (default None)
    Store as `self._state`
- Modify `qw/broker/rabbit.py`:
  - Update `RabbitMQConnection.wrap_callback()` to accept optional `state_tracker` parameter
  - Inside `wrapped_callback()`:
    - Before calling the user callback (line 308-311): if state_tracker is set, call
      `state_tracker.task_executing(task_id, source="broker")` where task_id is extracted
      from message properties (correlation_id or message_id, or generate one)
    - After successful callback (line 313): call
      `state_tracker.task_completed(task_id, "success", source="broker")`
    - In the exception handler (line 317+): call
      `state_tracker.task_completed(task_id, "error", source="broker")`
  - Pass state_tracker through from `BrokerConsumer.subscribe_to_events()` → `wrap_callback()`
- All state calls must be guarded with `if state_tracker:` / `if self._state:`
- Must be fully backward-compatible — broker works identically when no state_tracker is provided

**NOT in scope**:
- BrokerManager (producer) instrumentation — only consumer needs state tracking
- Modifying BrokerConsumer.setup() or aiohttp integration
- CLI (TASK-014)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/broker/consumer.py` | MODIFY | Accept state_tracker kwarg in __init__ |
| `qw/broker/rabbit.py` | MODIFY | Instrument wrap_callback with state tracking |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# No new imports in broker files — StateTracker is passed as constructor arg
# Optional type annotation:
# from qw.state import StateTracker  # (optional, for type hints)
```

### Existing Signatures to Use
```python
# qw/broker/consumer.py:20
class BrokerConsumer(RabbitMQConnection):
    _name_: str = "broker_consumer"  # line 24
    def __init__(self, dsn, timeout, callback, **kwargs):  # line 26-40
        self._callback_ = callback if callback else self.subscriber_callback  # line 40
        # CHANGE: add self._state = kwargs.pop('state_tracker', None)

    async def subscribe_to_events(self, exchange, queue_name, routing_key,
                                  callback, exchange_type='topic',
                                  durable=True, prefetch_count=1,
                                  requeue_on_fail=True, max_retries=3,
                                  **kwargs):  # line 72-83
        # Calls self.wrap_callback(callback, ...) at line 107
        # CHANGE: pass state_tracker=self._state to wrap_callback

    async def start(self, app):  # line 121
        # Calls self.subscribe_to_events(..., callback=self._callback_, ...)  # line 127-136
        # No change needed here — state_tracker flows through subscribe_to_events

# qw/broker/rabbit.py:294
class RabbitMQConnection:
    def wrap_callback(self, callback, requeue_on_fail=False,
                      max_retries=3) -> Callable:  # line 294-298
        # CHANGE: add state_tracker=None parameter

        async def wrapped_callback(message):  # line 304
            # properties = message.header.properties  # line 306
            # body = await self.process_message(...)   # line 307
            # callback(message, body)                  # line 309-311
            # await self._channel.basic_ack(...)       # line 313
            # CHANGE: add state tracking before/after callback

            # Exception handler at line 317:
            # retry logic at lines 320-368
            # CHANGE: add state.task_completed on error
```

### Does NOT Exist
- ~~`BrokerConsumer._state`~~ — does not exist yet; this task adds it
- ~~`RabbitMQConnection._state`~~ — does not exist; state_tracker is passed per-call
- ~~`wrap_callback` state_tracker parameter~~ — does not exist yet; this task adds it
- ~~message.task_id~~ — no such attribute on aiormq messages; use message_id from properties

---

## Implementation Notes

### Pattern to Follow
```python
# In BrokerConsumer.__init__:
self._state = kwargs.pop('state_tracker', None)

# In subscribe_to_events, pass to wrap_callback:
await self._channel.basic_consume(
    queue=queue_name,
    consumer_callback=self.wrap_callback(
        callback,
        requeue_on_fail=requeue_on_fail,
        max_retries=max_retries,
        state_tracker=self._state  # NEW
    ),
    **kwargs
)

# In wrap_callback:
def wrap_callback(self, callback, requeue_on_fail=False,
                  max_retries=3, state_tracker=None):

    async def wrapped_callback(message):
        properties = message.header.properties or aiormq.spec.Basic.Properties()
        # Extract task_id from message properties
        task_id = str(properties.message_id or properties.correlation_id or id(message))

        try:
            if state_tracker:
                state_tracker.task_executing(task_id, source="broker")
            body = await self.process_message(message.body, properties)
            # ... call callback ...
            await self._channel.basic_ack(message.delivery_tag)
            if state_tracker:
                state_tracker.task_completed(task_id, "success", source="broker")
        except Exception as e:
            if state_tracker:
                state_tracker.task_completed(task_id, "error", source="broker")
            # ... existing retry logic ...
```

### Key Constraints
- `wrap_callback` is defined on `RabbitMQConnection`, not `BrokerConsumer` — the
  state_tracker parameter must be added to the base class method
- Task ID extraction from AMQP messages: use `properties.message_id` first, fallback to
  `properties.correlation_id`, then `str(id(message))` as last resort
- Function name for broker tasks: use the routing key or exchange name as a proxy since
  broker messages don't carry Python function references
- The `state_tracker.task_executing` call for broker tasks does NOT go through `task_queued`
  first — broker messages are consumed and executed immediately (no local queue)
- Must not break the `BrokerManager` (producer) — it also calls `wrap_callback` but
  that's in a different context (producer ack handling). The `state_tracker=None` default
  ensures backward compatibility

### References in Codebase
- `qw/broker/consumer.py:26-40` — BrokerConsumer.__init__
- `qw/broker/consumer.py:72-119` — subscribe_to_events
- `qw/broker/rabbit.py:294-369` — wrap_callback

---

## Acceptance Criteria

- [ ] `BrokerConsumer.__init__()` accepts optional `state_tracker` kwarg
- [ ] `wrap_callback()` accepts optional `state_tracker` parameter
- [ ] Broker task execution calls `state.task_executing()` before callback
- [ ] Broker task completion calls `state.task_completed()` with correct result
- [ ] Task ID extracted from message properties (message_id or correlation_id)
- [ ] All state calls guarded with `if state_tracker:`
- [ ] BrokerConsumer works normally when state_tracker is None
- [ ] BrokerManager (producer) is unaffected

---

## Test Specification

No new test file — broker instrumentation is best tested via integration tests.
Verify by confirming the broker consumer still processes messages normally and
state appears in the shared dict when state_tracker is provided.

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/expose-queue-threads.spec.md` for full context
2. **Check dependencies**:
   - TASK-009 completed: `qw/state.py` exists with StateTracker
3. **Verify the Codebase Contract**:
   - Read `qw/broker/consumer.py` — confirm __init__ and subscribe_to_events
   - Read `qw/broker/rabbit.py` — confirm wrap_callback structure
4. **Update status** in `tasks/.index.json` → `"in-progress"`
5. **Implement** changes to `qw/broker/consumer.py` and `qw/broker/rabbit.py`
6. **Verify** no import errors, broker still initializes correctly
7. **Move this file** to `tasks/completed/TASK-013-broker-state-instrumentation.md`
8. **Update index** → `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:
**Deviations from spec**: none | describe if any
