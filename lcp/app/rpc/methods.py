from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MethodMetadata:
    auth_required: bool
    idempotency: bool
    timeout_seconds: float


METHODS: dict[str, MethodMetadata] = {
    "nearby.events.list": MethodMetadata(False, False, 5),
    "nearby.events.get": MethodMetadata(False, False, 5),
    "nearby.routes.analyze": MethodMetadata(False, False, 10),
    "nearby.favorites.set": MethodMetadata(True, True, 10),
    "nearby.favorites.remove": MethodMetadata(True, True, 10),
    "session.logout": MethodMetadata(False, False, 5),
    "realtime.subscribe": MethodMetadata(False, False, 5),
    "realtime.unsubscribe": MethodMetadata(False, False, 5),
}


def metadata_for(method: str) -> MethodMetadata | None:
    return METHODS.get(method)
