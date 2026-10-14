from collections import Counter


class Metrics:
    def __init__(self) -> None:
        self.values: Counter[str] = Counter()
        self.gauges: dict[str, int] = {"lcp_connections_active": 0}
        self.rpc_duration_seconds_sum = 0.0
        self.rpc_duration_seconds_count = 0

    def inc(self, name: str, value: int = 1) -> None:
        self.values[name] += value

    def set_connections(self, value: int) -> None:
        self.gauges["lcp_connections_active"] = value

    def observe_rpc(self, seconds: float) -> None:
        self.rpc_duration_seconds_sum += seconds
        self.rpc_duration_seconds_count += 1

    def render_prometheus(self) -> str:
        lines = [f"{name} {value}" for name, value in sorted(self.gauges.items())]
        lines.extend(f"{name} {value}" for name, value in sorted(self.values.items()))
        lines.append(f"lcp_rpc_duration_seconds_count {self.rpc_duration_seconds_count}")
        lines.append(f"lcp_rpc_duration_seconds_sum {self.rpc_duration_seconds_sum:.6f}")
        return "\n".join(lines) + "\n"
