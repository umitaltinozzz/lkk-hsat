from __future__ import annotations

import itertools
import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class CapacityInstance:
    demands: tuple[int, ...]
    capacities: tuple[int, ...]
    reachable: tuple[tuple[int, ...], ...]


@dataclass(frozen=True)
class CutWitness:
    reachable_boxes: tuple[int, ...]
    reachable_resources: tuple[int, ...]
    source_cut_capacity: int
    box_resource_cut_capacity: int
    resource_cut_capacity: int
    cut_capacity: int
    total_demand: int


@dataclass(frozen=True)
class FlowResult:
    result: str
    maximum_flow: int
    total_demand: int
    flow_nodes: int
    flow_edges: int
    elapsed_ms: float
    witness: CutWitness | None


class Dinic:
    def __init__(self, nodes: int):
        self.graph: list[list[list[int]]] = [[] for _ in range(nodes)]
        self.forward_edges: list[tuple[int, int, int]] = []

    def add_edge(self, source: int, target: int, capacity: int) -> None:
        forward = [target, capacity, len(self.graph[target]), capacity]
        reverse = [source, 0, len(self.graph[source]), 0]
        self.graph[source].append(forward)
        self.graph[target].append(reverse)
        self.forward_edges.append((source, target, capacity))

    def max_flow(self, source: int, sink: int) -> int:
        total = 0
        nodes = len(self.graph)
        while True:
            level = [-1] * nodes
            level[source] = 0
            queue = deque([source])
            while queue:
                node = queue.popleft()
                for target, capacity, _, _ in self.graph[node]:
                    if capacity > 0 and level[target] < 0:
                        level[target] = level[node] + 1
                        queue.append(target)
            if level[sink] < 0:
                return total
            cursor = [0] * nodes

            def send(node: int, amount: int) -> int:
                if node == sink:
                    return amount
                while cursor[node] < len(self.graph[node]):
                    edge = self.graph[node][cursor[node]]
                    target, capacity, reverse_index, _ = edge
                    if capacity > 0 and level[target] == level[node] + 1:
                        pushed = send(target, min(amount, capacity))
                        if pushed:
                            edge[1] -= pushed
                            self.graph[target][reverse_index][1] += pushed
                            return pushed
                    cursor[node] += 1
                return 0

            while True:
                pushed = send(source, 1 << 60)
                if not pushed:
                    break
                total += pushed

    def residual_reachable(self, source: int) -> set[int]:
        reached = {source}
        queue = deque([source])
        while queue:
            node = queue.popleft()
            for target, capacity, _, _ in self.graph[node]:
                if capacity > 0 and target not in reached:
                    reached.add(target)
                    queue.append(target)
        return reached


def flow_box_test(instance: CapacityInstance) -> FlowResult:
    start = time.perf_counter_ns()
    box_count = len(instance.demands)
    resource_count = len(instance.capacities)
    if len(instance.reachable) != box_count:
        raise ValueError("one reachability list is required per box")
    source = 0
    box_offset = 1
    resource_offset = box_offset + box_count
    sink = resource_offset + resource_count
    network = Dinic(sink + 1)
    for box, demand in enumerate(instance.demands):
        if demand < 0:
            raise ValueError("negative demand")
        network.add_edge(source, box_offset + box, demand)
        for resource in instance.reachable[box]:
            if not 0 <= resource < resource_count:
                raise ValueError("resource index out of range")
            network.add_edge(box_offset + box, resource_offset + resource, 1)
    for resource, capacity in enumerate(instance.capacities):
        if capacity < 0:
            raise ValueError("negative capacity")
        network.add_edge(resource_offset + resource, sink, capacity)
    demand = sum(instance.demands)
    maximum = network.max_flow(source, sink)
    witness = None
    result = "FEASIBLE"
    if maximum < demand:
        result = "UNSAT"
        reached = network.residual_reachable(source)
        reached_boxes = tuple(
            box for box in range(box_count) if box_offset + box in reached
        )
        reached_resources = tuple(
            resource
            for resource in range(resource_count)
            if resource_offset + resource in reached
        )
        source_cut = sum(
            instance.demands[box]
            for box in range(box_count)
            if box not in reached_boxes
        )
        edge_cut = sum(
            1
            for box in reached_boxes
            for resource in instance.reachable[box]
            if resource not in reached_resources
        )
        resource_cut = sum(instance.capacities[r] for r in reached_resources)
        cut_capacity = source_cut + edge_cut + resource_cut
        witness = CutWitness(
            reached_boxes,
            reached_resources,
            source_cut,
            edge_cut,
            resource_cut,
            cut_capacity,
            demand,
        )
        if cut_capacity != maximum or cut_capacity >= demand:
            raise AssertionError("invalid max-flow/min-cut witness")
    elapsed = (time.perf_counter_ns() - start) / 1e6
    return FlowResult(
        result,
        maximum,
        demand,
        sink + 1,
        len(network.forward_edges),
        elapsed,
        witness,
    )


def exhaustive_box_test(instance: CapacityInstance) -> tuple[str, tuple[int, ...] | None, float]:
    start = time.perf_counter_ns()
    box_count = len(instance.demands)
    for subset_size in range(1, box_count + 1):
        for subset in itertools.combinations(range(box_count), subset_size):
            demand = sum(instance.demands[box] for box in subset)
            capacity = 0
            for resource, resource_capacity in enumerate(instance.capacities):
                incident = sum(resource in instance.reachable[box] for box in subset)
                capacity += min(resource_capacity, incident)
            if demand > capacity:
                return "UNSAT", subset, (time.perf_counter_ns() - start) / 1e6
    return "FEASIBLE", None, (time.perf_counter_ns() - start) / 1e6
