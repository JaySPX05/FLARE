"""
Flood-aware custom Dijkstra routing for JalDrishti.

This file contains:
- A real OSM road graph loader
- Nearest road-node lookup
- Custom Dijkstra routing
- Flood-risk-weighted edge costs

routing_api.py loads the flood-risk JSON, maps its risk scores onto
OSM road edges, then calls compute_route() from this file.
"""
import heapq

import osmnx as ox


def load_osm_graph(
    center_lat=12.935,
    center_lon=77.627,
    dist_meters=3000,
):
    
    graph = ox.graph_from_point(
        (center_lat, center_lon),
        dist=dist_meters,
        dist_type="bbox",
        network_type="drive",
    )

    for _, _, _, edge_data in graph.edges(keys=True, data=True):
        edge_data["risk_score"] = 0.0

    return graph


def _nearest_node(graph, longitude, latitude):
    """
    Snap coordinates to the closest real road-network node.

    OSMnx:
    X = longitude
    Y = latitude
    """
    return ox.distance.nearest_nodes(
        graph,
        X=longitude,
        Y=latitude,
    )


def _edge_cost(
    edge_data,
    risk_penalty_factor,
    risk_threshold,
):
    """
    Calculate the flood-aware Dijkstra cost for one OSM edge.

    Roads whose flood risk reaches the threshold are treated as unsafe,
    so None is returned and the edge will be excluded.

    For usable roads:
    cost = length_in_metres * (1 + risk_penalty_factor * risk_score)
    """
    road_length = float(
        edge_data.get("length", 1.0)
    )

    risk_score = float(
        edge_data.get("risk_score", 0.0)
    )

    if risk_score >= risk_threshold:
        return None

    return road_length * (
        1 + risk_penalty_factor * risk_score
    )


def _make_adjacency_graph(
    osm_graph,
    risk_penalty_factor,
    risk_threshold,
):
    """
    Convert OSMnx MultiDiGraph into a normal adjacency-list graph.

    The custom Dijkstra function below expects:

    {
        node_id: [
            (neighbour_node_id, weight),
            ...
        ]
    }

    OSM may have several parallel edges from A to B. Keep only the
    lowest-cost one for each (source, destination) pair.
    """
    adjacency_graph = {
        node: []
        for node in osm_graph.nodes
    }

    best_costs = {}

    for source, destination, _, edge_data in osm_graph.edges(
        keys=True,
        data=True,
    ):
        edge_cost = _edge_cost(
            edge_data=edge_data,
            risk_penalty_factor=risk_penalty_factor,
            risk_threshold=risk_threshold,
        )

        if edge_cost is None:
            continue

        edge_pair = (
            source,
            destination,
        )

        old_cost = best_costs.get(edge_pair)

        if old_cost is None or edge_cost < old_cost:
            best_costs[edge_pair] = edge_cost

    for (source, destination), edge_cost in best_costs.items():
        adjacency_graph[source].append(
            (destination, edge_cost)
        )

    return adjacency_graph


def dijkstra(graph, start, goal):
    """
    Custom Dijkstra implementation.

    Returns:
        (node_path, total_cost)

    If there is no route:
        (None, infinity)
    """
    if start not in graph or goal not in graph:
        return None, float("inf")

    distances = {
        node: float("inf")
        for node in graph
    }

    previous = {
        node: None
        for node in graph
    }

    distances[start] = 0.0

    priority_queue = [
        (0.0, start)
    ]

    while priority_queue:
        current_cost, current_node = heapq.heappop(
            priority_queue
        )

        if current_cost > distances[current_node]:
            continue

        if current_node == goal:
            break

        for neighbour, edge_cost in graph[current_node]:
            new_cost = current_cost + edge_cost

            if new_cost < distances[neighbour]:
                distances[neighbour] = new_cost
                previous[neighbour] = current_node

                heapq.heappush(
                    priority_queue,
                    (new_cost, neighbour),
                )

    if distances[goal] == float("inf"):
        return None, float("inf")

    path = []
    current_node = goal

    while current_node is not None:
        path.append(current_node)
        current_node = previous[current_node]

    path.reverse()

    return path, distances[goal]


def _route_nodes_to_coordinates(
    osm_graph,
    route_nodes,
):
    """
    Convert OSM node IDs to the frontend coordinate format:

    [[longitude, latitude], ...]
    """
    if route_nodes is None:
        return []

    return [
        [
            float(osm_graph.nodes[node]["x"]),
            float(osm_graph.nodes[node]["y"]),
        ]
        for node in route_nodes
    ]


def _get_avoided_segments(
    osm_graph,
    route_nodes,
    risk_threshold,
):
    """
    Create a list of OSM edges with severe flood risk that were not used.

    This is for displaying the avoided-road information in the dashboard.
    """
    if route_nodes is None:
        return []

    route_edges = set(
        zip(route_nodes, route_nodes[1:])
    )

    avoided_segments = []

    for source, destination, key, edge_data in osm_graph.edges(
        keys=True,
        data=True,
    ):
        risk_score = float(
            edge_data.get("risk_score", 0.0)
        )

        if (
            risk_score >= risk_threshold
            and (source, destination) not in route_edges
        ):
            avoided_segments.append(
                f"{source}-{destination}-{key}"
            )

    return avoided_segments


def compute_route(
    G,
    from_lng,
    from_lat,
    to_lng,
    to_lat,
    risk_penalty_factor=15.0,
    risk_threshold=0.55,
):
    """
    Build and calculate a flood-aware route over the real OSM road graph.

    Important:
    - `G` must already have edge_data["risk_score"] values set by
      routing_api.py.
    - The default threshold is 0.55 because your supplied lookup data
      peaks at approximately 0.59. A threshold of 0.8 would block nothing.

    Returns:
    {
        "path": [[longitude, latitude], ...],
        "avoided_segments": [...],
        "risk_penalty_applied": true,
        "route_cost": number
    }
    """
    source = _nearest_node(
        G,
        longitude=from_lng,
        latitude=from_lat,
    )

    target = _nearest_node(
        G,
        longitude=to_lng,
        latitude=to_lat,
    )

    adjacency_graph = _make_adjacency_graph(
        osm_graph=G,
        risk_penalty_factor=risk_penalty_factor,
        risk_threshold=risk_threshold,
    )

    route_nodes, route_cost = dijkstra(
        graph=adjacency_graph,
        start=source,
        goal=target,
    )

    return {
        "path": _route_nodes_to_coordinates(
            G,
            route_nodes,
        ),
        "avoided_segments": _get_avoided_segments(
            osm_graph=G,
            route_nodes=route_nodes,
            risk_threshold=risk_threshold,
        ),
        "risk_penalty_applied": (
            risk_penalty_factor > 0
        ),
        "route_cost": route_cost
        if route_nodes is not None
        else None,
    }
