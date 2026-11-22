"""Fluent WebClient/RestClient invocation + URI composition (synthetic, distinct names).

Covers: direct literal .uri() with .get(); single-assigned base var/field +
literal path; template + query-string normalization; negatives (reassigned
base, dynamic path, wrong method, ambiguous duplicates, non-HTTP/custom
.uri(), unknown verb); PetClinic exposed regression (read-only /tmp).
"""
from pathlib import Path

import pytest

from bo_java.spring_sql_bindings import (
    emit_annotation_operations,
    extract_fluent_client_urls,
    extract_mapping_routes,
    link_client_to_annotation,
    link_client_url,
)


def _write(tmp_path, name, src):
    path = tmp_path / name
    path.write_text(src, encoding="utf-8")
    return path


ORDER_CONTROLLER = """\
package com.example.fluentorders;
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping("/api/orders")
public class FluentOrderController {
    @GetMapping("/{orderId}")
    public String find(@PathVariable String orderId) { return "ok"; }
}
"""

ORDER_FLUENT_CLIENT = """\
package com.example.fluentorders;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
@Component
public class FluentOrderClient {
    private final WebClient.Builder webClientBuilder;
    public FluentOrderClient(WebClient.Builder webClientBuilder) {
        this.webClientBuilder = webClientBuilder;
    }
    public Mono<String> findOrder(String orderId) {
        return webClientBuilder.build().get()
            .uri("http://order-svc.local/api/orders/{orderId}", orderId)
            .retrieve()
            .bodyToMono(String.class);
    }
}
"""

ORDER_BASE_CLIENT = """\
package com.example.fluentorders;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
@Component
public class FluentOrderBaseClient {
    private final WebClient.Builder webClientBuilder;
    private String orderBase = "http://order-svc.local";
    public FluentOrderBaseClient(WebClient.Builder webClientBuilder) {
        this.webClientBuilder = webClientBuilder;
    }
    public Mono<String> findOrder(String orderId) {
        return webClientBuilder.build().get()
            .uri(orderBase + "/api/orders/{orderId}", orderId)
            .retrieve()
            .bodyToMono(String.class);
    }
}
"""

INVOICE_CONTROLLER = """\
package com.example.fluentinvoices;
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping("/api/invoices")
public class FluentInvoiceController {
    @GetMapping
    public String search(@RequestParam String status) { return "ok"; }
}
"""

INVOICE_BASE_CLIENT = """\
package com.example.fluentinvoices;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
@Component
public class FluentInvoiceClient {
    private final WebClient.Builder webClientBuilder;
    public FluentInvoiceClient(WebClient.Builder webClientBuilder) {
        this.webClientBuilder = webClientBuilder;
    }
    public Mono<String> search(String status) {
        String invoiceBase = "http://invoice-svc.local";
        return webClientBuilder.build().get()
            .uri(invoiceBase + "/api/invoices?status={status}", status)
            .retrieve()
            .bodyToMono(String.class);
    }
}
"""

REASSIGNED_CLIENT = """\
package com.example.fluentorders;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
@Component
public class ReassignedBaseClient {
    private final WebClient.Builder webClientBuilder;
    private String svcBase = "http://order-svc.local";
    public ReassignedBaseClient(WebClient.Builder webClientBuilder) {
        this.webClientBuilder = webClientBuilder;
    }
    public Mono<String> findOrder(String orderId) {
        svcBase = "http://order-svc.local/v2";
        return webClientBuilder.build().get()
            .uri(svcBase + "/api/orders/{orderId}", orderId)
            .retrieve()
            .bodyToMono(String.class);
    }
}
"""

DYNAMIC_CLIENT = """\
package com.example.fluentorders;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
@Component
public class DynamicPathClient {
    private final WebClient.Builder webClientBuilder;
    public DynamicPathClient(WebClient.Builder webClientBuilder) {
        this.webClientBuilder = webClientBuilder;
    }
    public Mono<String> find(String prefix, String suffixVar) {
        return webClientBuilder.build().get()
            .uri(prefix + suffixVar)
            .retrieve()
            .bodyToMono(String.class);
    }
}
"""

CUSTOM_BUILDER_CLIENT = """\
package com.example.fluentorders;
public class CustomBuilderClient {
    private final CustomBuilder customBuilder;
    public CustomBuilderClient(CustomBuilder customBuilder) {
        this.customBuilder = customBuilder;
    }
    public String find(String id) {
        return customBuilder.get()
            .uri("http://order-svc.local/api/orders/{orderId}", id)
            .retrieve();
    }
}
"""

UNKNOWN_VERB_CLIENT = """\
package com.example.fluentorders;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
@Component
public class UnknownVerbClient {
    private final WebClient.Builder webClientBuilder;
    public UnknownVerbClient(WebClient.Builder webClientBuilder) {
        this.webClientBuilder = webClientBuilder;
    }
    public Mono<String> find(String id) {
        return webClientBuilder.build()
            .uri("http://order-svc.local/api/orders/{orderId}", id)
            .retrieve()
            .bodyToMono(String.class);
    }
}
"""

ARBITRARY_RECEIVER_CLIENT = """\
package com.example.fluentorders;
import org.springframework.web.reactive.function.client.WebClient;
public class ArbitraryReceiverClient {
    private final WebClient.Builder webClientBuilder;
    private final LegacyFetcher fetcher;
    public ArbitraryReceiverClient(WebClient.Builder b, LegacyFetcher f) {
        this.webClientBuilder = b;
        this.fetcher = f;
    }
    public String find(String id) {
        return fetcher.get()
            .uri("http://order-svc.local/api/orders/{orderId}", id)
            .retrieve();
    }
}
"""

TRAILING_LITERAL_CLIENT = """\
package com.example.fluentorders;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
@Component
public class TrailingLiteralClient {
    private final WebClient.Builder webClientBuilder;
    public TrailingLiteralClient(WebClient.Builder webClientBuilder) {
        this.webClientBuilder = webClientBuilder;
    }
    public Mono<String> find(String orderId) {
        return webClientBuilder.build().get()
            .uri("http://order-svc.local/api/" + orderId)
            .retrieve()
            .bodyToMono(String.class);
    }
}
"""

TRAILING_BASE_CLIENT = """\
package com.example.fluentorders;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
@Component
public class TrailingBaseClient {
    private final WebClient.Builder webClientBuilder;
    private String orderBase = "http://order-svc.local";
    public TrailingBaseClient(WebClient.Builder webClientBuilder) {
        this.webClientBuilder = webClientBuilder;
    }
    public Mono<String> find(String orderId) {
        return webClientBuilder.build().get()
            .uri(orderBase + "/api/orders/" + orderId)
            .retrieve()
            .bodyToMono(String.class);
    }
}
"""

FORWARD_BASE_CLIENT = """\
package com.example.fluentorders;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
@Component
public class ForwardBaseClient {
    private final WebClient.Builder webClientBuilder;
    public ForwardBaseClient(WebClient.Builder webClientBuilder) {
        this.webClientBuilder = webClientBuilder;
    }
    public Mono<String> findOrder(String orderId) {
        return webClientBuilder.build().get()
            .uri(orderBase + "/api/orders/{orderId}", orderId)
            .retrieve()
            .bodyToMono(String.class);
    }
    private String orderBase = "http://order-svc.local";
}
"""

BUILDER_INLINE_CLIENT = """\
package com.example.fluentorders;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
@Component
public class BuilderInlineClient {
    public Mono<String> findOrder(String orderId) {
        return WebClient.builder().build().get()
            .uri("http://order-svc.local/api/orders/{orderId}", orderId)
            .retrieve()
            .bodyToMono(String.class);
    }
}
"""

BUILDER_ASSIGNED_CLIENT = """\
package com.example.fluentorders;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
@Component
public class BuilderAssignedClient {
    public Mono<String> findOrder(String orderId) {
        WebClient client = WebClient.builder().build();
        return client.get()
            .uri("http://order-svc.local/api/orders/{orderId}", orderId)
            .retrieve()
            .bodyToMono(String.class);
    }
}
"""

MULTILINE_URI_CLIENT = """\
package com.example.fluentorders;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
@Component
public class MultilineUriClient {
    private final WebClient.Builder webClientBuilder;
    public MultilineUriClient(WebClient.Builder webClientBuilder) {
        this.webClientBuilder = webClientBuilder;
    }
    public Mono<String> findOrder(String orderId) {
        return webClientBuilder.build().get()
            .uri(
                "http://order-svc.local/api/orders/{orderId}",
                orderId
            )
            .retrieve()
            .bodyToMono(String.class);
    }
}
"""

PREFIX_VERB_CLIENT = """\
package com.example.fluentorders;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
@Component
public class PrefixVerbClient {
    public Mono<String> find(String orderId) {
        return WebClient.create("http://order-svc.local").getFoo("x").uri("http://order-svc.local/api/orders/{orderId}", orderId).get()
            .retrieve()
            .bodyToMono(String.class);
    }
}
"""


def _ops_for(tmp_path, name, controller_src):
    routes = extract_mapping_routes(_write(tmp_path, name, controller_src))
    return emit_annotation_operations(routes)


def test_direct_literal_get_resolves_and_links(tmp_path):
    ops = _ops_for(tmp_path, "FluentOrderController.java", ORDER_CONTROLLER)
    urls = extract_fluent_client_urls(
        _write(tmp_path, "FluentOrderClient.java", ORDER_FLUENT_CLIENT)
    )
    assert len(urls) == 1, urls
    url = urls[0]
    assert url["url"] == "http://order-svc.local/api/orders/{orderId}", url
    assert url["client_method"] == "get", url
    assert url["method"] == "get", url
    assert isinstance(url["call_line"], int) and url["call_line"] > 0, url
    links = link_client_to_annotation(url, ops)
    assert links, links
    assert links[0]["compatible"] is True, links
    assert links[0]["destination_verified"] is False, links
    assert links[0]["equivalent"] is False, links
    assert links[0].get("source") == "spring_annotation", links


def test_direct_literal_openapi_link(tmp_path):
    urls = extract_fluent_client_urls(
        _write(tmp_path, "FluentOrderClient.java", ORDER_FLUENT_CLIENT)
    )
    assert len(urls) == 1, urls
    op = {
        "openapi_path": "/api/orders/{orderId}",
        "method": "get",
        "file": "openapi.yaml",
        "line": 3,
        "span": {"start_line": 2, "end_line": 3},
    }
    links = link_client_url(urls[0], [op])
    assert links and links[0]["compatible"] is True, links
    assert links[0]["destination_verified"] is False, links
    assert links[0]["equivalent"] is False, links


def test_base_var_plus_literal_path_cites_both_spans(tmp_path):
    ops = _ops_for(tmp_path, "FluentOrderController.java", ORDER_CONTROLLER)
    client = _write(tmp_path, "FluentOrderBaseClient.java", ORDER_BASE_CLIENT)
    urls = extract_fluent_client_urls(client)
    assert len(urls) == 1, urls
    url = urls[0]
    assert url["url"] == "http://order-svc.local/api/orders/{orderId}", url
    assert url["client_method"] == "get", url
    assert url.get("var") == "orderBase", url
    assert url.get("base_span") == {"start_line": 8, "end_line": 8}, url
    assert url["span"] == {"start_line": 8, "end_line": 14}, url
    links = link_client_to_annotation(url, ops)
    assert links and links[0]["compatible"] is True, links


def test_template_query_path_matches_query_cited(tmp_path):
    ops = _ops_for(tmp_path, "FluentInvoiceController.java", INVOICE_CONTROLLER)
    urls = extract_fluent_client_urls(
        _write(tmp_path, "FluentInvoiceClient.java", INVOICE_BASE_CLIENT)
    )
    assert len(urls) == 1, urls
    url = urls[0]
    assert url["url"] == "http://invoice-svc.local/api/invoices?status={status}", url
    assert "status={status}" in str(url.get("uri_expr", "")), url
    links = link_client_to_annotation(url, ops)
    assert links and links[0]["compatible"] is True, links
    assert links[0]["client_path"] == "/api/invoices", links


def test_reassigned_base_abstains(tmp_path):
    urls = extract_fluent_client_urls(
        _write(tmp_path, "ReassignedBaseClient.java", REASSIGNED_CLIENT)
    )
    assert urls == [], urls


def test_dynamic_path_abstains(tmp_path):
    urls = extract_fluent_client_urls(
        _write(tmp_path, "DynamicPathClient.java", DYNAMIC_CLIENT)
    )
    assert urls == [], urls


def test_wrong_method_is_incompatible(tmp_path):
    ops = _ops_for(tmp_path, "FluentOrderController.java", ORDER_CONTROLLER)
    post_only = [dict(op, method="post") for op in ops]
    urls = extract_fluent_client_urls(
        _write(tmp_path, "FluentOrderClient.java", ORDER_FLUENT_CLIENT)
    )
    assert len(urls) == 1, urls
    links = link_client_to_annotation(urls[0], post_only)
    assert links, links
    assert all(link["compatible"] is False for link in links), links
    assert any("method" in str(link.get("reason", "")).lower() for link in links), links
    assert all(link["destination_verified"] is False for link in links), links
    assert all(link["equivalent"] is False for link in links), links


def test_ambiguous_duplicate_routes_abstain(tmp_path):
    ops = _ops_for(tmp_path, "FluentOrderController.java", ORDER_CONTROLLER)
    ops = ops + [dict(op) for op in ops]
    urls = extract_fluent_client_urls(
        _write(tmp_path, "FluentOrderClient.java", ORDER_FLUENT_CLIENT)
    )
    assert len(urls) == 1, urls
    links = link_client_to_annotation(urls[0], ops)
    assert links, links
    assert all(link["compatible"] is False for link in links), links
    assert any("ambiguous" in str(link.get("reason", "")).lower() for link in links), links


def test_non_http_custom_builder_abstains(tmp_path):
    urls = extract_fluent_client_urls(
        _write(tmp_path, "CustomBuilderClient.java", CUSTOM_BUILDER_CLIENT)
    )
    assert urls == [], urls


def test_unknown_verb_abstains(tmp_path):
    urls = extract_fluent_client_urls(
        _write(tmp_path, "UnknownVerbClient.java", UNKNOWN_VERB_CLIENT)
    )
    assert urls == [], urls


def test_arbitrary_receiver_abstains(tmp_path):
    urls = extract_fluent_client_urls(
        _write(tmp_path, "ArbitraryReceiverClient.java", ARBITRARY_RECEIVER_CLIENT)
    )
    assert urls == [], urls


def test_index_includes_fluent_urls(tmp_path):
    from bo_java.spring_sql_bindings import index

    _write(tmp_path, "FluentOrderClient.java", ORDER_FLUENT_CLIENT)
    result = index([tmp_path])
    fluent = [u for u in result["records"]["client_urls"] if u.get("fluent") is True]
    assert len(fluent) == 1, result["records"]["client_urls"]
    assert fluent[0]["client_method"] == "get", fluent


def test_literal_plus_var_abstains_no_truncated_record(tmp_path):
    """HIGH: .uri("http://h/api/" + id) must not emit truncated http://h/api/."""
    urls = extract_fluent_client_urls(
        _write(tmp_path, "TrailingLiteralClient.java", TRAILING_LITERAL_CLIENT)
    )
    assert urls == [], urls


def test_base_plus_literal_plus_var_abstains(tmp_path):
    """HIGH: base + "/api/orders/" + orderId is dynamic concat: no record."""
    urls = extract_fluent_client_urls(
        _write(tmp_path, "TrailingBaseClient.java", TRAILING_BASE_CLIENT)
    )
    assert urls == [], urls


def test_index_dedupes_fluent_line_single_record(tmp_path):
    """MED: literal scan also fires on fluent .uri lines; index keeps one
    fluent record per (file, line, url)."""
    from bo_java.spring_sql_bindings import index

    _write(tmp_path, "FluentOrderClient.java", ORDER_FLUENT_CLIENT)
    result = index([tmp_path])
    recs = [
        u for u in result["records"]["client_urls"]
        if u["url"] == "http://order-svc.local/api/orders/{orderId}"
    ]
    assert recs, result["records"]["client_urls"]
    by_line: dict[int, list] = {}
    for record in recs:
        by_line.setdefault(record["line"], []).append(record)
    for line, same in by_line.items():
        assert len(same) == 1, (line, same)
        assert same[0].get("fluent") is True, (line, same)


def test_forward_base_abstains_no_inverted_span(tmp_path):
    """MED: base assigned after use (use-before-def) abstains; no {20,10} span."""
    urls = extract_fluent_client_urls(
        _write(tmp_path, "ForwardBaseClient.java", FORWARD_BASE_CLIENT)
    )
    assert urls == [], urls


def test_builder_inline_recognised(tmp_path):
    """MED: WebClient.builder().build().get().uri(...) yields a record."""
    urls = extract_fluent_client_urls(
        _write(tmp_path, "BuilderInlineClient.java", BUILDER_INLINE_CLIENT)
    )
    assert len(urls) == 1, urls
    assert urls[0]["url"] == "http://order-svc.local/api/orders/{orderId}", urls
    assert urls[0]["client_method"] == "get", urls


def test_builder_assigned_recognised(tmp_path):
    """MED: WebClient client = WebClient.builder().build(); client.get() ..."""
    urls = extract_fluent_client_urls(
        _write(tmp_path, "BuilderAssignedClient.java", BUILDER_ASSIGNED_CLIENT)
    )
    assert len(urls) == 1, urls
    assert urls[0]["url"] == "http://order-svc.local/api/orders/{orderId}", urls
    assert urls[0]["client_method"] == "get", urls


def test_multiline_uri_abstains_documented(tmp_path):
    """LOW: multi-line .uri( is single-line-only by design: abstain, not crash."""
    urls = extract_fluent_client_urls(
        _write(tmp_path, "MultilineUriClient.java", MULTILINE_URI_CLIENT)
    )
    assert urls == [], urls


def test_prefix_verb_same_line_abstains(tmp_path):
    """LOW: .getFoo on the same line must not satisfy the .get() verb check."""
    urls = extract_fluent_client_urls(
        _write(tmp_path, "PrefixVerbClient.java", PREFIX_VERB_CLIENT)
    )
    assert urls == [], urls


def _find_petclinic_clients():
    wanted = {}
    for pattern in ("**/CustomersServiceClient.java", "**/VisitsServiceClient.java",
                    "**/OwnerResource.java"):
        for path in Path("/tmp").glob("opencode/oss-eval-micro/" + pattern):
            try:
                if path.is_file():
                    wanted.setdefault(path.name, path)
            except OSError:
                continue
    return wanted


def test_petclinic_exposed_regression_readonly():
    """REGRESSION (read-only /tmp, no build): Customers literal URI resolves
    with get; Visits hostname+path abstains (reassignable base via setter)."""
    wanted = _find_petclinic_clients()
    if "CustomersServiceClient.java" not in wanted or "VisitsServiceClient.java" not in wanted:
        pytest.skip("PetClinic clients absent under /tmp (no download attempted)")
    customers = extract_fluent_client_urls(wanted["CustomersServiceClient.java"])
    assert len(customers) == 1, customers
    assert customers[0]["url"] == "http://customers-service/owners/{ownerId}", customers
    assert customers[0]["client_method"] == "get", customers
    visits = extract_fluent_client_urls(wanted["VisitsServiceClient.java"])
    assert visits == [], visits
    if "OwnerResource.java" not in wanted:
        pytest.skip("OwnerResource absent under /tmp (no download attempted)")
    ops = emit_annotation_operations(
        extract_mapping_routes(wanted["OwnerResource.java"])
    )
    links = link_client_to_annotation(customers[0], ops)
    assert links, links
    assert any(link["compatible"] is True for link in links), links
    assert all(link["destination_verified"] is False for link in links), links
    assert all(link["equivalent"] is False for link in links), links
