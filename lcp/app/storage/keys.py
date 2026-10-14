SESSION = "lcp:v1:session:{session_id}"
REPLAY = "lcp:v1:replay:{session_id}"
SUBSCRIPTIONS = "lcp:v1:subs:{session_id}"
IDEMPOTENCY = "lcp:v1:idem:{user_id}:{method}:{key}"
RATE_LIMIT = "lcp:v1:rate:{principal}:{method}:{window}"
GATEWAY = "lcp:v1:gateway:{gateway_id}"


def session(session_id: str) -> str:
    return SESSION.format(session_id=session_id)


def replay(session_id: str) -> str:
    return REPLAY.format(session_id=session_id)


def subscriptions(session_id: str) -> str:
    return SUBSCRIPTIONS.format(session_id=session_id)


def idempotency(user_id: str, method: str, key: str) -> str:
    return IDEMPOTENCY.format(user_id=user_id, method=method, key=key)


def rate_limit(principal: str, method: str, window: int) -> str:
    return RATE_LIMIT.format(principal=principal, method=method, window=window)


def gateway(gateway_id: str) -> str:
    return GATEWAY.format(gateway_id=gateway_id)


TOPIC_SUBSCRIPTIONS = "lcp:v1:topic-subs:{topic}"
REPLAY_SEEN = "lcp:v1:replay-seen:{session_id}"
SUBSCRIPTIONS_PREFIX = "lcp:v1:subs:"


def topic_subscriptions(topic: str) -> str:
    return TOPIC_SUBSCRIPTIONS.format(topic=topic)

def replay_seen(session_id: str) -> str:
    return REPLAY_SEEN.format(session_id=session_id)

EVENT_STREAM = "lcp:v1:event-stream"
EVENT_GROUP = "lcp-fanout-v1"
LEGACY_LEADER = "lcp:v1:legacy-ingress-owner"
