"""J2 independent ground truth for the Java/Spring slice (java-repo-a + java-repo-b).

Labels are read from fixture source text only. Self-contained by design: NO
``src`` imports and no adapter/extractor calls, so these labels cannot be tuned
by any implementation (independent ground truth, C4). Java sources are scanned
as text with substring/regex; YAML/SQL/OpenAPI are scanned as text as well.

One behavior per test. Note on annotations: the fixture's ``RefundService`` is a
plain class, so ``@Service``/``@Transactional`` are absent; the Spring
annotations that exist are ``@RestController`` and ``@PostMapping("/refunds")``.
"""

import re
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
REPO_A = FIXTURES / "java-repo-a"
REPO_B = FIXTURES / "java-repo-b"

REFUND_SERVICE = REPO_A / "src/main/java/com/example/refund/RefundService.java"
REFUND_CONTROLLER = REPO_A / "src/main/java/com/example/refund/RefundController.java"
REFUND_YML = REPO_A / "src/main/resources/application.yml"
REFUND_SQL = REPO_A / "src/main/resources/db/migration/V1__refund_schema.sql"
REFUND_OPENAPI = REPO_A / "openapi.yaml"

ORDER_SERVICE = REPO_B / "src/main/java/com/example/orders/OrderService.java"
ORDER_CLIENT = REPO_B / "src/main/java/com/example/orders/OrderClient.java"
ORDER_YML = REPO_B / "src/main/resources/application.yml"
ORDER_OPENAPI = REPO_B / "openapi.yaml"


# ---------------------------------------------------------------- helpers


def _text(path):
    return path.read_text(encoding="utf-8")


def _method_body(src, signature_prefix):
    """Return the source text of the method declared with signature_prefix."""
    match = re.search(rf"{re.escape(signature_prefix)}.*?\n    \}}", src, re.S)
    assert match, f"method not found: {signature_prefix}"
    return match.group(0)


def _strip_java_comments(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


# ---------------------------------------------------------------- Spring annotations


def test_refund_controller_is_annotated_with_rest_controller():
    """RefundController is annotated @RestController (presentation stereotype)."""
    src = _text(REFUND_CONTROLLER)
    assert re.search(r"(?m)^@RestController\s*$", src), src


def test_refund_endpoint_is_annotated_with_post_mapping_refunds():
    """Refund handler is mapped with @PostMapping("/refunds")."""
    src = _text(REFUND_CONTROLLER)
    assert '@PostMapping("/refunds")' in src, src
    assert re.search(
        r'@PostMapping\("/refunds"\)\s*\n\s*public RefundService\.RefundResult requestRefund',
        src,
    ), src


def test_refund_service_has_service_and_transactional_annotations():
    """RefundService declares Spring @Service and @Transactional annotations."""
    src = _text(REFUND_SERVICE)
    assert "import org.springframework.stereotype.Service;" in src, src
    assert "import org.springframework.transaction.annotation.Transactional;" in src, src
    assert re.search(r"(?m)^@Service\s*$", src), src
    assert re.search(r"(?m)^\s*@Transactional\s*$", src), src


# ---------------------------------------------------------------- config window


def test_refund_window_days_config_equals_30():
    """Configured refund window policy value is 30 days."""
    src = _text(REFUND_YML)
    match = re.search(r"(?m)^\s*window-days:\s*(\d+)\s*$", src)
    assert match, src
    assert match.group(1) == "30", match.group(1)


def test_refund_window_display_string_is_not_the_policy_value():
    """window-days-display is a UI label, not the numeric policy value."""
    src = _text(REFUND_YML)
    match = re.search(r'(?m)^\s*window-days-display:\s*"([^"]*)"', src)
    assert match, src
    display = match.group(1)
    policy = re.search(r"(?m)^\s*window-days:\s*(\d+)\s*$", src).group(1)
    assert policy == "30"
    assert display != policy
    assert "30" in display
    assert "days" in display


# ---------------------------------------------------------------- status guard


def test_refund_guard_requires_order_paid_and_payment_settled():
    """Executable guard requires both ORDER_PAID and PAYMENT_SETTLED."""
    body = _method_body(_text(REFUND_SERVICE), "public RefundResult requestRefund(")
    assert re.search(
        r'if\s*\(\s*!"ORDER_PAID"\.equals\(request\.getOrderStatus\(\)\)'
        r'\s*\|\|\s*!"PAYMENT_SETTLED"\.equals\(request\.getPaymentStatus\(\)\)',
        body,
    ), body


def test_refund_guard_failure_raises_illegal_state_exception():
    """Guard failure raises IllegalStateException, not a silent return."""
    body = _method_body(_text(REFUND_SERVICE), "public RefundResult requestRefund(")
    assert "throw new IllegalStateException" in body, body
    assert "invalid status guard" in body, body


# ---------------------------------------------------------------- transition


def test_refund_transition_target_is_refund_requested():
    """Refund transition writes the REFUND_REQUESTED status."""
    body = _method_body(_text(REFUND_SERVICE), "public RefundResult requestRefund(")
    assert 'request.setRefundStatus("REFUND_REQUESTED")' in body, body


def test_refund_transition_runs_after_status_guard():
    """REFUND_REQUESTED transition happens after the ORDER_PAID/PAYMENT_SETTLED guard."""
    src = _text(REFUND_SERVICE)
    guard = src.index('"ORDER_PAID".equals(request.getOrderStatus())')
    transition = src.index('request.setRefundStatus("REFUND_REQUESTED")')
    assert guard < transition


def test_refund_transition_skips_when_already_refund_requested():
    """Transition is guarded by the previous refund status (idempotent)."""
    body = _method_body(_text(REFUND_SERVICE), "public RefundResult requestRefund(")
    assert 'if (!"REFUND_REQUESTED".equals(previousStatus))' in body, body


# ---------------------------------------------------------------- event construction


def test_refund_requested_event_is_constructed():
    """requestRefund constructs a RefundRequestedEvent."""
    body = _method_body(_text(REFUND_SERVICE), "public RefundResult requestRefund(")
    assert "new RefundRequestedEvent(" in body, body
    assert "request.getOrderId()" in body, body


def test_event_builder_path_has_no_publication_call():
    """Event builder path constructs only: no publish/emit/dispatch/send call."""
    body = _method_body(_text(REFUND_SERVICE), "public RefundResult requestRefund(")
    code = _strip_java_comments(body)
    assert re.search(
        r"\b(?:publish|emit|dispatch|send|broadcast|fire)\b", code, re.I
    ) is None, code


def test_event_publication_comment_declares_out_of_scope():
    """Fixture comment states event publication is out of scope."""
    src = _text(REFUND_SERVICE)
    assert "publication is out of scope" in src, src


# ---------------------------------------------------------------- SQL schema


def test_refunds_table_is_declared():
    """Migration declares the refunds table."""
    src = _text(REFUND_SQL)
    assert re.search(r"(?i)CREATE\s+TABLE\s+refunds\b", src), src


def test_refunds_table_has_order_id_column():
    """refunds table has order_id as a required column."""
    src = _text(REFUND_SQL)
    assert re.search(r"(?i)order_id\s+VARCHAR\(64\)\s+NOT\s+NULL", src), src


def test_refunds_table_has_status_column():
    """refunds table has status as a required column."""
    src = _text(REFUND_SQL)
    assert re.search(r"(?i)status\s+VARCHAR\(32\)\s+NOT\s+NULL", src), src


# ---------------------------------------------------------------- OpenAPI contract


def test_refund_openapi_declares_post_refunds():
    """repo-a OpenAPI declares POST /refunds."""
    src = _text(REFUND_OPENAPI)
    assert re.search(r"/refunds:\s*\n\s+post:", src), src


def test_refund_openapi_request_exposes_guard_statuses():
    """repo-a OpenAPI refund request exposes orderStatus/paymentStatus/paidAt."""
    src = _text(REFUND_OPENAPI)
    assert "orderStatus" in src, src
    assert "paymentStatus" in src, src
    assert "paidAt" in src, src


# ---------------------------------------------------------------- OrderClient binding


def test_order_client_posts_to_refunds_url():
    """OrderClient POSTs to the refund service /refunds URL via RestTemplate."""
    src = _text(ORDER_CLIENT)
    assert '"http://refund-service.local/refunds"' in src, src
    assert "RestTemplate" in src, src
    assert re.search(r"restTemplate\.postForObject\(url, payload, String\.class\)", src), src


def test_order_client_url_matches_openapi_refunds_path():
    """OrderClient target path matches the repo-a OpenAPI /refunds path."""
    client = _text(ORDER_CLIENT)
    openapi = _text(REFUND_OPENAPI)
    url = re.search(r'String url = "([^"]+)"', client).group(1)
    assert urlparse(url).path == "/refunds"
    assert re.search(r"/refunds:\s*\n\s+post:", openapi)


def test_order_client_payload_carries_guard_statuses():
    """OrderClient refund payload carries ORDER_PAID and PAYMENT_SETTLED."""
    src = _text(ORDER_CLIENT)
    assert 'new RefundPayload(orderId, "ORDER_PAID", "PAYMENT_SETTLED")' in src, src


# ---------------------------------------------------------------- orders behavior


def test_order_create_initializes_status_created():
    """OrderService.createOrder initializes new orders with status CREATED."""
    src = _text(ORDER_SERVICE)
    assert 'new Order(orderId, customerId, "CREATED")' in src, src


def test_order_config_default_status_is_created():
    """repo-b order config default-status is CREATED."""
    src = _text(ORDER_YML)
    assert re.search(r'(?m)^\s*default-status:\s*"CREATED"\s*$', src), src


def test_order_service_dynamic_hook_invocation_uses_variable_name():
    """OrderService dynamic probe passes a variable method name to getMethod/invoke."""
    src = _text(ORDER_SERVICE)
    assert re.search(r"getMethod\(hookName\)", src), src
    assert re.search(r"method\.invoke\(this\)", src), src
    assert "triggerPostCreateHook" in src, src


# ---------------------------------------------------------------- misleading comment


def test_misleading_comment_is_present_in_source():
    """The misleading payment-signature comment exists as source text."""
    src = _text(REFUND_SERVICE)
    assert "This check validates the customer's payment signature." in src, src


def test_misleading_comment_is_inert_in_executable_guard():
    """Executable guard uses ORDER_PAID/PAYMENT_SETTLED, never the comment's claim."""
    src = _text(REFUND_SERVICE)
    guard_lines = [
        line for line in src.splitlines()
        if "ORDER_PAID" in line and "PAYMENT_SETTLED" in line
    ]
    assert len(guard_lines) == 1, guard_lines
    assert "signature" not in guard_lines[0], guard_lines[0]


# ---------------------------------------------------------------- orders contract


def test_order_openapi_declares_post_orders():
    """repo-b OpenAPI declares POST /orders."""
    src = _text(ORDER_OPENAPI)
    assert re.search(r"/orders:\s*\n\s+post:", src), src


def test_order_openapi_declares_order_refund_submission():
    """repo-b OpenAPI declares POST /orders/{orderId}/refund."""
    src = _text(ORDER_OPENAPI)
    assert re.search(r"/orders/\{orderId\}/refund:\s*\n\s+post:", src), src
