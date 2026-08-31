from src.backend.graph.workflow import load_logistics_node


def test_missing_order_reports_an_error():
    result = load_logistics_node({"order": None})
    assert "errors" in result


def test_missing_order_id_reports_an_error():
    result = load_logistics_node({"order": {}})
    assert "errors" in result


def test_populates_logistics_with_the_signal_wrapped_shape():
    # ORD2026080002: real fixture order with an unresolved 物流停滞超72小时
    result = load_logistics_node({"order": {"order_id": "ORD2026080002"}})
    logistics = result["logistics"]
    assert logistics["order_id"] == "ORD2026080002"
    assert logistics["tracking_stalled_over_72h"]["availability"] == "AVAILABLE"
    assert logistics["tracking_stalled_over_72h"]["value"] is True
    assert "exceptions" in logistics


def test_unknown_order_id_reports_an_error_not_a_crash():
    result = load_logistics_node({"order": {"order_id": "ORD-DOES-NOT-EXIST-AT-ALL"}})
    assert "errors" in result
