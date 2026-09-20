"""
Flood-aware custom Dijkstra routing for JalDrishti.

This file contains:
- A real OSM road graph loader (largest connected component only),
  with an on-disk GraphML cache so it can run without internet
- Road-edge snapping (click point -> nearest point on a real road)
- Custom Dijkstra routing            <- unchanged hand-written algorithm
- Flood-risk-weighted edge costs
- Conversion of the node path back into real, road-following geometry

routing_api.py loads the flood-risk JSON, maps its risk scores onto
OSM road edges, then calls compute_route() from this file.

Why the geometry / snapping code exists
---------------------------------------
OSMnx *simplifies* the graph: nodes are only kept at intersections and
dead ends, and the curve of the road between two intersections is stored
on the edge as a `geometry` LineString. If a route is drawn from node
coordinates only, Leaflet joins consecutive intersections with a straight
chord that cuts across parks and buildings. The path is therefore rebuilt
from each edge's `geometry`.

Likewise, snapping a click to the nearest *node* can land hundreds of
metres away on sparse roads, so clicks are snapped to the nearest point
on the nearest *edge*, and that point joins the graph through two
temporary connector edges.
"""
import heapq
from pathlib import Path

import networkx as nx
import osmnx as ox
from shapely import STRtree
from shapely.geometry import LineString, Point
from shapely.ops import substring

# Temporary nodes for the snapped click points. They must be ints (OSM node
# ids are always positive ints) so heapq can break cost ties between them
# and real nodes without comparing a str to an int.
START_NODE = -1
GOAL_NODE = -2

_EDGE_INDEX_KEY = "_jaldrishti_edge_index"


def download_osm_graph(
    center_lat=12.935,
    center_lon=77.627,
    dist_meters=3000,
):
    """
    Download the drivable OSM road network around a point (needs internet
    and the Overpass API) and keep only its strongly connected core.
    """
    graph = ox.graph_from_point(
        (center_lat, center_lon),
        dist=dist_meters,
        dist_type="bbox",
        network_type="drive",
    )

    # A bbox download cuts roads at the boundary and can leave small
    # disconnected fragments (and one-way dead ends). A click that snaps
    # into a fragment can never reach the destination, so keep only the
    # strongly connected core where every node can reach every other.
    largest = max(nx.strongly_connected_components(graph), key=len)

    return graph.subgraph(largest).copy()


def load_osm_graph(
    center_lat=12.935,
    center_lon=77.627,
    dist_meters=3000,
    graph_path=None,
):
    """
    Return the road graph the router runs on.

    If `graph_path` points at a saved GraphML file (see
    scripts/08_cache_osm_graph.py) it is loaded from disk - fast, offline,
    and identical on every start, which is what the Docker image relies on.
    Otherwise the network is downloaded live, and saved to `graph_path`
    (when one was given) so the next start can skip the download.
    """
    if graph_path and Path(graph_path).exists():
        graph = ox.load_graphml(graph_path)
    else:
        graph = download_osm_graph(center_lat, center_lon, dist_meters)

        if graph_path:
            try:
                Path(graph_path).parent.mkdir(parents=True, exist_ok=True)
                ox.save_graphml(graph, graph_path)
            except OSError as error:
                print(f"Could not cache road graph to {graph_path}: {error}")

    for _, _, _, edge_data in graph.edges(keys=True, data=True):
        edge_data["risk_score"] = 0.0

    return graph


# ---------------------------------------------------------------------------
# Edge geometry + snapping
# ---------------------------------------------------------------------------

def _sq_dist(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def edge_line(G, u, v, key):
    """
    The real road shape of edge (u -> v, key) as a LineString, oriented so
    it starts at node u and ends at node v.

    Edges OSMnx kept straight have no `geometry`; those fall back to a
    straight line between the two nodes (which is exactly their shape).
    """
    u_xy = (float(G.nodes[u]["x"]), float(G.nodes[u]["y"]))
    v_xy = (float(G.nodes[v]["x"]), float(G.nodes[v]["y"]))

    geometry = G[u][v][key].get("geometry")

    if geometry is None:
        return LineString([u_xy, v_xy])

    coords = list(geometry.coords)

    # Guard against a stored geometry running v -> u.
    if _sq_dist(coords[0], u_xy) > _sq_dist(coords[-1], u_xy):
        coords.reverse()

    return LineString(coords)


def build_edge_index(G):
    """
    Build (once) and cache a spatial index over every road edge so that
    clicks can be snapped quickly. Safe to call repeatedly.
    """
    cached = G.graph.get(_EDGE_INDEX_KEY)

    if cached is not None:
        return cached

    edge_ids = []
    lines = []

    for u, v, key in G.edges(keys=True):
        edge_ids.append((u, v, key))
        lines.append(edge_line(G, u, v, key))

    index = (STRtree(lines), edge_ids, lines)
    G.graph[_EDGE_INDEX_KEY] = index

    return index


def _snap_to_edge(G, longitude, latitude):
    """
    Snap a coordinate to the closest point on the closest real road edge.

    Returns a dict describing where on which edge the point landed.
    """
    tree, edge_ids, lines = build_edge_index(G)

    point = Point(longitude, latitude)
    index = int(tree.nearest(point))

    u, v, key = edge_ids[index]
    line = lines[index]

    # Two-way roads are stored as two overlapping edges. Always use the
    # low-id -> high-id one so two clicks on the same block agree on
    # which edge they are on.
    if u > v and G.has_edge(v, u, key):
        u, v = v, u
        line = edge_line(G, u, v, key)

    distance_along = line.project(point)

    fraction = (
        distance_along / line.length
        if line.length > 0
        else 0.0
    )

    return {
        "u": u,
        "v": v,
        "key": key,
        "line": line,
        "distance": distance_along,
        "fraction": fraction,
        "length_m": float(G[u][v][key].get("length", 1.0)),
    }


def _piece(line, from_distance, to_distance):
    """
    Coordinates of the stretch of `line` between two distances along it.
    If from_distance > to_distance the stretch is returned reversed.
    """
    if abs(from_distance - to_distance) < 1e-12:
        p = line.interpolate(from_distance)
        return [(p.x, p.y)]

    return list(substring(line, from_distance, to_distance).coords)


# ---------------------------------------------------------------------------
# Costs and adjacency
# ---------------------------------------------------------------------------

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
    lowest-cost one for each (source, destination) pair, and remember
    WHICH one it was so the route can be drawn along that exact road.

    Returns (adjacency_graph, best_edge_keys).
    """
    adjacency_graph = {
        node: []
        for node in osm_graph.nodes
    }

    best_edges = {}

    for source, destination, key, edge_data in osm_graph.edges(
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

        old = best_edges.get(edge_pair)

        if old is None or edge_cost < old[0]:
            best_edges[edge_pair] = (edge_cost, key)

    best_edge_keys = {}

    for (source, destination), (edge_cost, key) in best_edges.items():
        adjacency_graph[source].append(
            (destination, edge_cost)
        )
        best_edge_keys[(source, destination)] = key

    return adjacency_graph, best_edge_keys


def _attach_endpoints(
    G,
    adjacency_graph,
    start_snap,
    end_snap,
    risk_penalty_factor,
):
    """
    Join the snapped start/end points to the graph with temporary edges.

    A snap point sits part-way along a road, so the start connects to the
    end of that road (and to its beginning too, if the road is two-way),
    and the destination is reached the same way in reverse.

    Connector edges are never blocked: someone standing on a flooded road
    still has to be able to leave it. They are still risk-weighted.

    Returns {(from_node, to_node): (coords, edge_id, metres)} for drawing.
    """
    connectors = {}
    adjacency_graph[START_NODE] = []
    adjacency_graph[GOAL_NODE] = []

    def add(from_node, to_node, snap, metres, coords):
        edge_data = G[snap["u"]][snap["v"]][snap["key"]]
        risk = float(edge_data.get("risk_score", 0.0))
        cost = max(metres, 0.0) * (1 + risk_penalty_factor * risk)

        adjacency_graph[from_node].append((to_node, cost))
        connectors[(from_node, to_node)] = (
            coords,
            (snap["u"], snap["v"], snap["key"]),
            metres,
        )

    s = start_snap
    t = end_snap

    # START -> end of its road (always), and -> start of its road (two-way).
    add(
        START_NODE, s["v"], s,
        s["length_m"] * (1 - s["fraction"]),
        _piece(s["line"], s["distance"], s["line"].length),
    )
    if G.has_edge(s["v"], s["u"], s["key"]):
        add(
            START_NODE, s["u"], s,
            s["length_m"] * s["fraction"],
            _piece(s["line"], s["distance"], 0.0),
        )

    # start of its road -> GOAL (always), end of its road -> GOAL (two-way).
    add(
        t["u"], GOAL_NODE, t,
        t["length_m"] * t["fraction"],
        _piece(t["line"], 0.0, t["distance"]),
    )
    if G.has_edge(t["v"], t["u"], t["key"]):
        add(
            t["v"], GOAL_NODE, t,
            t["length_m"] * (1 - t["fraction"]),
            _piece(t["line"], t["line"].length, t["distance"]),
        )

    # Both clicks on the same block: drive straight along it instead of
    # looping out to the intersections and back.
    same_edge = (s["u"], s["v"], s["key"]) == (t["u"], t["v"], t["key"])

    if same_edge:
        forward = t["distance"] >= s["distance"]

        if forward or G.has_edge(s["v"], s["u"], s["key"]):
            add(
                START_NODE, GOAL_NODE, s,
                s["length_m"] * abs(t["fraction"] - s["fraction"]),
                _piece(s["line"], s["distance"], t["distance"]),
            )

    return connectors


# ---------------------------------------------------------------------------
# Dijkstra (hand-written, unchanged)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Turning the node path back into a real, road-following line
# ---------------------------------------------------------------------------

def _route_to_geometry(
    osm_graph,
    route_nodes,
    best_edge_keys,
    connectors,
):
    """
    Build the frontend coordinate list [[longitude, latitude], ...] by
    walking the route and, for every hop, following the actual road shape
    (edge geometry) rather than jumping node to node.

    Also returns the ids of the real road edges the route travels on.
    """
    if route_nodes is None:
        return [], []

    coords = []
    edge_ids = []

    def extend(points):
        for x, y in points:
            if (
                coords
                and abs(coords[-1][0] - x) < 1e-9
                and abs(coords[-1][1] - y) < 1e-9
            ):
                continue
            coords.append((float(x), float(y)))

    for source, destination in zip(route_nodes, route_nodes[1:]):
        if (source, destination) in connectors:
            points, edge_id, metres = connectors[(source, destination)]
        else:
            key = best_edge_keys[(source, destination)]
            points = edge_line(
                osm_graph, source, destination, key
            ).coords
            edge_id = (source, destination, key)
            metres = None

        extend(points)

        # A zero-length connector (click right on an intersection) is not
        # really "travelling on" that road, so don't report the edge.
        travelled = metres is None or metres > 1.0

        if travelled and edge_id not in edge_ids:
            edge_ids.append(edge_id)

    return [[x, y] for x, y in coords], edge_ids


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
        "path": [[longitude, latitude], ...],   # follows the real roads
        "edge_ids": [(u, v, key), ...],         # road edges travelled
        "avoided_segments": [...],
        "risk_penalty_applied": true,
        "route_cost": number
    }
    """
    start_snap = _snap_to_edge(
        G,
        longitude=from_lng,
        latitude=from_lat,
    )

    end_snap = _snap_to_edge(
        G,
        longitude=to_lng,
        latitude=to_lat,
    )

    adjacency_graph, best_edge_keys = _make_adjacency_graph(
        osm_graph=G,
        risk_penalty_factor=risk_penalty_factor,
        risk_threshold=risk_threshold,
    )

    connectors = _attach_endpoints(
        G=G,
        adjacency_graph=adjacency_graph,
        start_snap=start_snap,
        end_snap=end_snap,
        risk_penalty_factor=risk_penalty_factor,
    )

    route_nodes, route_cost = dijkstra(
        graph=adjacency_graph,
        start=START_NODE,
        goal=GOAL_NODE,
    )

    path, edge_ids = _route_to_geometry(
        osm_graph=G,
        route_nodes=route_nodes,
        best_edge_keys=best_edge_keys,
        connectors=connectors,
    )

    return {
        "path": path,
        "edge_ids": edge_ids,
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
