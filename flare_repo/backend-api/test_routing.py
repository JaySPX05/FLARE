"""
Offline tests for routing.py - no internet or OSM download needed.

Run from backend-api:
    python -m pytest test_routing.py -q

The synthetic graph mimics what OSMnx produces after simplification:
nodes only at intersections, curved roads carried as edge `geometry`.
"""
import random

import networkx as nx
import osmnx as ox
import pytest
from shapely.geometry import LineString, Point

import routing
from routing import compute_route, dijkstra


def _add_road(G, u, v, coords, oneway=False, risk=0.0):
    """Add a road u->v (and v->u unless oneway) with a real shape."""
    line = LineString(coords)
    length_m = line.length * 111_000
    G.add_edge(u, v, 0, length=length_m, geometry=line, risk_score=risk)
    if not oneway:
        G.add_edge(
            v, u, 0,
            length=length_m,
            geometry=LineString(list(line.coords)[::-1]),
            risk_score=risk,
        )


def make_graph():
    """
        C (77.610, 12.910)
        |
        |  (straight, 1 km)
        |
        B (77.610, 12.900) ---- D (77.620, 12.900)    <- alternative leg
        |
        |  curved road with 2 bends between A and B
        A (77.600, 12.900)
    """
    G = nx.MultiDiGraph()
    G.graph["crs"] = "EPSG:4326"
    for n, (x, y) in {
        1: (77.600, 12.900),   # A
        2: (77.610, 12.900),   # B
        3: (77.610, 12.910),   # C
        4: (77.620, 12.900),   # D
    }.items():
        G.add_node(n, x=x, y=y)

    _add_road(G, 1, 2, [(77.600, 12.900), (77.603, 12.9035),
                        (77.607, 12.9035), (77.610, 12.900)])
    _add_road(G, 2, 3, [(77.610, 12.900), (77.610, 12.910)])
    _add_road(G, 2, 4, [(77.610, 12.900), (77.620, 12.900)])
    _add_road(G, 4, 3, [(77.620, 12.900), (77.620, 12.910), (77.610, 12.910)])
    return G


def _on_a_road(G, x, y, tol=1e-7):
    p = Point(x, y)
    return any(
        routing.edge_line(G, u, v, k).distance(p) < tol
        for u, v, k in G.edges(keys=True)
    )


def test_route_follows_curved_road_not_straight_chord():
    G = make_graph()
    result = compute_route(G, 77.600, 12.900, 77.610, 12.910,
                           risk_penalty_factor=0, risk_threshold=1.1)
    path = result["path"]

    # the two bend vertices of the A-B curve must be in the drawn route
    assert [77.603, 12.9035] in path
    assert [77.607, 12.9035] in path


def test_every_route_point_lies_on_a_real_road():
    G = make_graph()
    result = compute_route(G, 77.6012, 12.9004, 77.6151, 12.9098,
                           risk_penalty_factor=0, risk_threshold=1.1)
    assert result["path"]
    for x, y in result["path"]:
        assert _on_a_road(G, x, y), f"({x}, {y}) is off the road network"


def test_off_road_click_snaps_onto_the_road():
    G = make_graph()
    # click ~700 m north of the A-B curve, in the middle of nowhere
    result = compute_route(G, 77.605, 12.9100, 77.610, 12.9050,
                           risk_penalty_factor=0, risk_threshold=1.1)
    x, y = result["path"][0]
    assert _on_a_road(G, x, y)


def test_route_starts_and_ends_at_the_snapped_click_not_a_far_node():
    G = make_graph()
    # start half-way along the long B-C road; nearest *node* is 500 m away
    result = compute_route(G, 77.6101, 12.9050, 77.6099, 12.9020,
                           risk_penalty_factor=0, risk_threshold=1.1)
    (sx, sy), (ex, ey) = result["path"][0], result["path"][-1]
    assert sx == pytest.approx(77.610, abs=1e-6) and sy == pytest.approx(12.905, abs=1e-4)
    assert ex == pytest.approx(77.610, abs=1e-6) and ey == pytest.approx(12.902, abs=1e-4)


def test_same_block_route_is_direct_not_a_loop():
    G = make_graph()
    result = compute_route(G, 77.610, 12.9030, 77.610, 12.9070,
                           risk_penalty_factor=0, risk_threshold=1.1)
    ys = [p[1] for p in result["path"]]
    assert min(ys) >= 12.9030 - 1e-9 and max(ys) <= 12.9070 + 1e-9


def test_blocked_road_forces_detour_and_shortest_ignores_it():
    G = make_graph()
    # flood the direct B-C road (both directions)
    for u, v in [(2, 3), (3, 2)]:
        G[u][v][0]["risk_score"] = 0.9

    shortest = compute_route(G, 77.600, 12.900, 77.610, 12.910,
                             risk_penalty_factor=0.0, risk_threshold=1.1)
    safe = compute_route(G, 77.600, 12.900, 77.610, 12.910,
                         risk_penalty_factor=15.0, risk_threshold=0.55)

    assert (2, 3, 0) in shortest["edge_ids"]
    assert (2, 3, 0) not in safe["edge_ids"]
    assert (3, 2, 0) not in safe["edge_ids"]
    # the detour via D exists and is longer
    assert any(abs(x - 77.620) < 1e-9 for x, _ in safe["path"])
    assert safe["route_cost"] > shortest["route_cost"]


def test_no_safe_route_when_every_path_is_blocked():
    G = make_graph()
    for u, v, k in list(G.edges(keys=True)):
        if {u, v} in ({2, 3}, {2, 4}):
            G[u][v][k]["risk_score"] = 0.9
    safe = compute_route(G, 77.600, 12.900, 77.6150, 12.9100,
                         risk_penalty_factor=15.0, risk_threshold=0.55)
    assert safe["path"] == []
    assert safe["route_cost"] is None


def test_one_way_road_is_respected():
    G = nx.MultiDiGraph()
    G.graph["crs"] = "EPSG:4326"
    for n, xy in {1: (77.600, 12.900), 2: (77.610, 12.900), 3: (77.605, 12.905)}.items():
        G.add_node(n, x=xy[0], y=xy[1])
    _add_road(G, 1, 2, [(77.600, 12.900), (77.610, 12.900)], oneway=True)   # only 1 -> 2
    _add_road(G, 2, 3, [(77.610, 12.900), (77.605, 12.905)])
    _add_road(G, 3, 1, [(77.605, 12.905), (77.600, 12.900)])

    # 2 -> 1 must go round via 3, never backwards along the one-way road
    result = compute_route(G, 77.610, 12.900, 77.600, 12.900,
                           risk_penalty_factor=0, risk_threshold=1.1)
    assert (2, 1, 0) not in result["edge_ids"]
    assert [77.605, 12.905] in result["path"]


def test_custom_dijkstra_matches_networkx_on_random_graphs():
    rng = random.Random(7)
    for trial in range(30):
        n = 40
        D = nx.gnp_random_graph(n, 0.12, seed=trial, directed=True)
        for u, v in D.edges:
            D[u][v]["weight"] = rng.uniform(1, 50)
        adjacency = {u: [(v, D[u][v]["weight"]) for v in D[u]] for u in D}
        start, goal = rng.sample(range(n), 2)
        path, cost = dijkstra(adjacency, start, goal)
        try:
            expected = nx.dijkstra_path_length(D, start, goal)
        except nx.NetworkXNoPath:
            assert path is None and cost == float("inf")
            continue
        assert cost == pytest.approx(expected)
        assert path[0] == start and path[-1] == goal


def test_saved_graph_gives_the_same_routes_as_the_original(tmp_path):
    """The Docker image ships a GraphML copy of the road network instead of
    downloading it at start-up - routes on the reloaded copy must match."""
    G = make_graph()
    path = tmp_path / "roads.graphml"
    ox.save_graphml(G, path)

    H = routing.load_osm_graph(graph_path=path)      # loads from disk, no internet

    assert all(d["risk_score"] == 0.0 for _, _, _, d in H.edges(keys=True, data=True))

    args = (77.6012, 12.9004, 77.6151, 12.9098)
    before = compute_route(G, *args, risk_penalty_factor=0, risk_threshold=1.1)
    after = compute_route(H, *args, risk_penalty_factor=0, risk_threshold=1.1)

    flat = lambda path: [c for point in path for c in point]
    assert flat(after["path"]) == pytest.approx(flat(before["path"]))
    assert after["route_cost"] == pytest.approx(before["route_cost"])
    assert [77.603, 12.9035] in after["path"]     # curve shape survived the round trip
