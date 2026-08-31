from src.backend.agents.request_resolver_agent import ORDER_ID_PATTERN, REQUEST_ID_PATTERN


def _matches(pattern, text):
    return [m.group() for m in pattern.finditer(text)]


# --- ORDER_ID_PATTERN: fixture shape (ORD + 10 digits) ---

def test_matches_fixture_shaped_id():
    assert _matches(ORDER_ID_PATTERN, "我的订单 ORD2026080001 什么时候发货") == ["ORD2026080001"]


def test_matches_fixture_shaped_id_lowercase_and_normalizes_via_upper():
    match = ORDER_ID_PATTERN.search("订单号 ord2026080001 麻烦查一下")
    assert match is not None
    assert match.group().upper() == "ORD2026080001"


# --- ORDER_ID_PATTERN: V2 shape (ORD-RW- + 5 digits) ---

def test_matches_v2_shaped_id():
    assert _matches(ORDER_ID_PATTERN, "订单 ORD-RW-00001 到哪了") == ["ORD-RW-00001"]


def test_matches_v2_shaped_id_lowercase_and_normalizes_via_upper():
    match = ORDER_ID_PATTERN.search("ord-rw-00001 什么时候到")
    assert match is not None
    assert match.group().upper() == "ORD-RW-00001"


# --- boundary / partial-match rejection ---

def test_does_not_match_id_with_extra_trailing_digits():
    # a longer number must not be silently truncated into a valid-looking id
    assert _matches(ORDER_ID_PATTERN, "编号 ORD20260800011 不是订单号") == []


def test_does_not_match_v2_id_with_extra_trailing_digit():
    assert _matches(ORDER_ID_PATTERN, "编号 ORD-RW-000012 不是订单号") == []


def test_does_not_match_id_embedded_in_a_longer_token():
    assert _matches(ORDER_ID_PATTERN, "XORD2026080001 不是订单号") == []
    assert _matches(ORDER_ID_PATTERN, "ORD2026080001X 不是订单号") == []


def test_does_not_match_v2_id_embedded_in_a_longer_hyphenated_token():
    assert _matches(ORDER_ID_PATTERN, "XORD-RW-00001 不是订单号") == []
    assert _matches(ORDER_ID_PATTERN, "ORD-RW-00001X 不是订单号") == []


def test_does_not_loosen_to_arbitrary_ord_dash_shapes():
    # A third id shape must be added explicitly -- never silently accepted
    # by a generic ORD-.* wildcard.
    assert _matches(ORDER_ID_PATTERN, "ORD-XY-12345 不是已知格式") == []
    assert _matches(ORDER_ID_PATTERN, "ORD-RW-0001 位数不对") == []  # 4 digits, not 5
    assert _matches(ORDER_ID_PATTERN, "ORD-RW-000001 位数不对") == []  # 6 digits, not 5


# --- realistic chat phrasing / punctuation / multiple ids ---

def test_matches_with_surrounding_punctuation():
    assert _matches(ORDER_ID_PATTERN, "订单号：ORD-RW-00001，麻烦查一下") == ["ORD-RW-00001"]
    assert _matches(ORDER_ID_PATTERN, "（ORD2026080001）什么情况") == ["ORD2026080001"]


def test_finds_multiple_ids_of_mixed_shapes_in_one_message():
    text = "我有两个订单 ORD2026080001 和 ORD-RW-00001 都想查一下"
    assert _matches(ORDER_ID_PATTERN, text) == ["ORD2026080001", "ORD-RW-00001"]


def test_no_match_on_plain_text_without_an_order_id():
    assert _matches(ORDER_ID_PATTERN, "我的快递到哪了") == []


# --- REQUEST_ID_PATTERN: unchanged, same shape regardless of order source
# (SR + YYYYMM + 4-digit sequence, minted by create_service_request()'s own
# INSERT regardless of whether the underlying order is fixture or V2) ---

def test_request_id_pattern_still_matches_its_one_shape():
    assert _matches(REQUEST_ID_PATTERN, "申请编号 SR2026080001 处理得怎么样了") == ["SR2026080001"]


def test_request_id_pattern_rejects_extra_trailing_digit():
    assert _matches(REQUEST_ID_PATTERN, "SR20260800011 不是申请编号") == []
