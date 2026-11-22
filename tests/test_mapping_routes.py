"""Spring mapping-route extraction + annotation-backed linking (no OpenAPI).

Synthetic controllers use distinct Widget/Order names (never PetClinic
names/paths). PetClinic files are read-only REGRESSION inputs when present
under /tmp (no download/build); skipped when absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bo_java.spring_sql_bindings import (
    emit_annotation_operations,
    extract_mapping_routes,
    link_client_to_annotation,
)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


WIDGET_CONTROLLER = """\
package com.example.widgets;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RequestMapping("/api/widgets")
@RestController
public class WidgetController {

    @PostMapping("/create")
    public String createWidget() {
        return "ok";
    }
}
"""

ALL_VERBS_CONTROLLER = """\
package com.example.widgets;

import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class WidgetVerbsController {

    @GetMapping("/widgets")
    public String listWidgets() {
        return "ok";
    }

    @PostMapping("/widgets")
    public String createWidget() {
        return "ok";
    }

    @PutMapping("/widgets")
    public String replaceWidget() {
        return "ok";
    }

    @DeleteMapping("/widgets")
    public String deleteWidget() {
        return "ok";
    }

    @PatchMapping("/widgets")
    public String patchWidget() {
        return "ok";
    }
}
"""

EXPLICIT_METHOD_CONTROLLER = """\
package com.example.orders;

import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class OrderExplicitController {

    @RequestMapping(value = "/explicit", method = RequestMethod.GET)
    public String explicit() {
        return "ok";
    }

    @RequestMapping(value = "/implicit")
    public String implicit() {
        return "ok";
    }
}
"""

DYNAMIC_CONTROLLER = """\
package com.example.widgets;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class WidgetDynamicController {

    private static final String PATH_CONST = "/dynamic";

    @GetMapping(PATH_CONST)
    public String byConst() {
        return "ok";
    }

    @GetMapping(value = BASE + "/suffix")
    public String byConcat() {
        return "ok";
    }
}
"""

ORDER_CREATE_CONTROLLER = """\
package com.example.orders;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RequestMapping("/api/orders")
@RestController
public class OrderCreateController {

    @PostMapping("/create")
    public String createOrder() {
        return "ok";
    }
}
"""

ORDER_CLIENT = """\
package com.example.orders;

import org.springframework.web.client.RestTemplate;

public class OrderCreateClient {

    private final RestTemplate restTemplate = new RestTemplate();

    public String create() {
        String url = "http://order-svc.local/api/orders/create";
        return restTemplate.postForObject(url, new Object(), String.class);
    }
}
"""

ORDER_CLIENT_WRONG_METHOD = """\
package com.example.orders;

import org.springframework.web.client.RestTemplate;

public class OrderReadClient {

    private final RestTemplate restTemplate = new RestTemplate();

    public String read() {
        String url = "http://order-svc.local/api/orders/create";
        return restTemplate.getForObject(url, String.class);
    }
}
"""

DUP_A = """\
package com.example.dupa;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class DupAlphaController {

    @PostMapping("/api/dup/create")
    public String create() {
        return "ok";
    }
}
"""

DUP_B = """\
package com.example.dupb;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class DupBetaController {

    @PostMapping("/api/dup/create")
    public String create() {
        return "ok";
    }
}
"""

DUP_CLIENT = """\
package com.example.dup;

import org.springframework.web.client.RestTemplate;

public class DupClient {

    private final RestTemplate restTemplate = new RestTemplate();

    public String create() {
        String url = "http://dup-svc.local/api/dup/create";
        return restTemplate.postForObject(url, new Object(), String.class);
    }
}
"""

TEMPLATE_CONTROLLER = """\
package com.example.widgets;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RequestMapping("/api/widgets")
@RestController
public class WidgetTemplateController {

    @GetMapping("/{widgetId}")
    public String byId() {
        return "ok";
    }
}
"""

TEMPLATE_CLIENT = """\
package com.example.widgets;

import org.springframework.web.client.RestTemplate;

public class WidgetReadClient {

    private final RestTemplate restTemplate = new RestTemplate();

    public String read() {
        String url = "http://widget-svc.local/api/widgets/123";
        return restTemplate.getForObject(url, String.class);
    }
}
"""

MULTI_CONTROLLER = """\
package com.example.widgets;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RequestMapping(value={"/api/a","/api/b"})
@RestController
public class WidgetMultiController {

    @GetMapping({"/x","/y"})
    public String multi() {
        return "ok";
    }
}
"""

ARRAY_DYNAMIC_CONTROLLER = """\
package com.example.widgets;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class WidgetArrayDynamicController {

    private static final String PATH_CONST = "/dynamic";

    @GetMapping(value={PATH_CONST})
    public String byConstArray() {
        return "ok";
    }
}
"""

VIA_PATH_CONTROLLER = """\
package com.example.orders;

import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class OrderViaPathController {

    @RequestMapping(path="/via-path", method=RequestMethod.GET)
    public String viaPath() {
        return "ok";
    }
}
"""

ORDER_PUT_CONTROLLER = """\
package com.example.orders;

import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RequestMapping("/api/orders")
@RestController
public class OrderPutController {

    @PutMapping("/{orderId}")
    public String replaceOrder() {
        return "ok";
    }
}
"""

ORDER_PUT_CLIENT = """\
package com.example.orders;

import org.springframework.web.client.RestTemplate;

public class OrderPutClient {

    private final RestTemplate restTemplate = new RestTemplate();

    public void replace() {
        String url = "http://order-svc.local/api/orders/123";
        restTemplate.put(url, new Object());
    }
}
"""


def _routes_for(tmp_path: Path, name: str, text: str):
    path = _write(tmp_path, name, text)
    return extract_mapping_routes(path), path


def test_class_method_join_with_both_spans(tmp_path):
    routes, _ = _routes_for(tmp_path, "WidgetController.java", WIDGET_CONTROLLER)
    assert len(routes) == 1, routes
    route = routes[0]
    assert route["kind"] == "mapping_route"
    assert route["class"] == "WidgetController"
    assert route["http_methods"] == ["post"]
    assert route["path"] == "/api/widgets/create"
    assert route["source"] == "spring_annotation"
    assert route["method_annotation"]["line"] < route["line"] + 1
    assert route["class_annotation"] is not None
    assert route["method_annotation"]["span"]["start_line"] <= route["line"]
    assert route["class_annotation"]["span"]["start_line"] < route["method_annotation"]["span"]["start_line"]


def test_all_verbs(tmp_path):
    routes, _ = _routes_for(tmp_path, "WidgetVerbsController.java", ALL_VERBS_CONTROLLER)
    by_verb = {r["http_methods"][0]: r["path"] for r in routes}
    assert by_verb == {
        "get": "/widgets",
        "post": "/widgets",
        "put": "/widgets",
        "delete": "/widgets",
        "patch": "/widgets",
    }, routes


def test_multi_value_expands_to_four_routes(tmp_path):
    routes, _ = _routes_for(tmp_path, "WidgetMultiController.java", MULTI_CONTROLLER)
    assert len(routes) == 4, routes
    paths = sorted(r["path"] for r in routes)
    assert paths == ["/api/a/x", "/api/a/y", "/api/b/x", "/api/b/y"], routes
    assert len(set(paths)) == 4, routes
    assert all(r["kind"] == "mapping_route" for r in routes), routes
    assert all(r["http_methods"] == ["get"] for r in routes), routes
    assert all(r["source"] == "spring_annotation" for r in routes), routes
    spans = {(r["span"]["start_line"], r["span"]["end_line"]) for r in routes}
    assert len(spans) == 1, routes
    method_lines = {r["method_annotation"]["line"] for r in routes}
    assert len(method_lines) == 1, routes


def test_array_dynamic_value_abstains(tmp_path):
    routes, _ = _routes_for(
        tmp_path, "WidgetArrayDynamicController.java", ARRAY_DYNAMIC_CONTROLLER
    )
    assert routes, "dynamic array must still yield a record with a reason"
    assert all(r["path"] is None for r in routes), routes
    assert all("dynamic" in str(r.get("reason", "")).lower() for r in routes), routes


def test_requestmapping_path_alias(tmp_path):
    routes, _ = _routes_for(
        tmp_path, "OrderViaPathController.java", VIA_PATH_CONTROLLER
    )
    assert len(routes) == 1, routes
    assert routes[0]["path"] == "/via-path", routes
    assert routes[0]["http_methods"] == ["get"], routes
    assert routes[0]["verb_unknown"] is False, routes


def test_put_client_compatible_declared_only(tmp_path):
    from bo_java.spring_sql_bindings import extract_client_urls

    routes, _ = _routes_for(
        tmp_path, "OrderPutController.java", ORDER_PUT_CONTROLLER
    )
    assert [r["path"] for r in routes] == ["/api/orders/{orderId}"], routes
    ops = emit_annotation_operations(routes)
    client_src = _write(tmp_path, "OrderPutClient.java", ORDER_PUT_CLIENT)
    urls = extract_client_urls(client_src)
    assert len(urls) == 1, urls
    links = link_client_to_annotation(urls[0], ops)
    assert links, links
    link = links[0]
    assert link["compatible"] is True, link
    assert link["destination_verified"] is False, link
    assert link["equivalent"] is False, link
    assert link.get("source") == "spring_annotation", link


def test_requestmapping_explicit_method(tmp_path):
    routes, _ = _routes_for(tmp_path, "OrderExplicitController.java", EXPLICIT_METHOD_CONTROLLER)
    by_path = {r["path"]: r for r in routes}
    assert by_path["/explicit"]["http_methods"] == ["get"], routes
    assert by_path["/explicit"]["verb_unknown"] is False, routes


def test_requestmapping_without_method_never_compatible(tmp_path):
    from bo_java.spring_sql_bindings import extract_client_urls

    routes, _ = _routes_for(tmp_path, "OrderExplicitController.java", EXPLICIT_METHOD_CONTROLLER)
    implicit = next(r for r in routes if r["path"] == "/implicit")
    assert implicit["http_methods"] is None, routes
    assert implicit["verb_unknown"] is True, routes
    ops = emit_annotation_operations(routes)
    target = next(o for o in ops if o.get("openapi_path") == "/implicit")
    assert target.get("linkable") is False, ops
    client_src = _write(
        tmp_path,
        "ImplicitClient.java",
        "package com.example.orders;\n"
        "import org.springframework.web.client.RestTemplate;\n"
        "public class ImplicitClient {\n"
        "    private final RestTemplate restTemplate = new RestTemplate();\n"
        "    public String call() {\n"
        '        String url = "http://order-svc.local/implicit";\n'
        "        return restTemplate.postForObject(url, new Object(), String.class);\n"
        "    }\n"
        "}\n",
    )
    urls = extract_client_urls(client_src)
    assert urls, "expected a client URL record"
    links = link_client_to_annotation(urls[0], ops)
    assert links, "path matches so links must be emitted"
    assert all(link["compatible"] is False for link in links), links


def test_dynamic_non_literal_abstains(tmp_path):
    from bo_java.spring_sql_bindings import extract_client_urls

    routes, _ = _routes_for(tmp_path, "WidgetDynamicController.java", DYNAMIC_CONTROLLER)
    assert routes, "dynamic annotations must still yield records with reasons"
    assert all(r["path"] is None for r in routes), routes
    ops = emit_annotation_operations(routes)
    assert all(o.get("linkable") is False for o in ops), ops
    assert all(o.get("method") is not None for o in ops), ops  # verb known, path unknown
    client_src = _write(
        tmp_path,
        "DynamicClient.java",
        "package com.example.widgets;\n"
        "import org.springframework.web.client.RestTemplate;\n"
        "public class DynamicClient {\n"
        "    private final RestTemplate restTemplate = new RestTemplate();\n"
        "    public String call() {\n"
        '        String url = "http://widget-svc.local/dynamic";\n'
        "        return restTemplate.getForObject(url, String.class);\n"
        "    }\n"
        "}\n",
    )
    urls = extract_client_urls(client_src)
    links = link_client_to_annotation(urls[0], ops)
    assert links == [] or all(link["compatible"] is False for link in links), links


def test_openapi_absent_linking_compatible_declared_only(tmp_path):
    from bo_java.spring_sql_bindings import extract_client_urls

    routes, _ = _routes_for(tmp_path, "OrderCreateController.java", ORDER_CREATE_CONTROLLER)
    ops = emit_annotation_operations(routes)
    assert ops and all(o.get("source") == "spring_annotation" for o in ops)
    resolvable = [o for o in ops if o.get("linkable")]
    assert len(resolvable) == 1, ops
    assert resolvable[0]["openapi_path"] == "/api/orders/create"
    assert resolvable[0]["method"] == "post"
    client_src = _write(tmp_path, "OrderCreateClient.java", ORDER_CLIENT)
    urls = extract_client_urls(client_src)
    assert len(urls) == 1, urls
    links = link_client_to_annotation(urls[0], ops)
    assert links, links
    link = links[0]
    assert link["compatible"] is True, link
    assert link["destination_verified"] is False, link
    assert link["equivalent"] is False, link
    assert link.get("source") == "spring_annotation", link
    assert "spring_annotation" in str(link.get("reason", "")), link


def test_wrong_method_is_incompatible(tmp_path):
    from bo_java.spring_sql_bindings import extract_client_urls

    routes, _ = _routes_for(tmp_path, "OrderCreateController.java", ORDER_CREATE_CONTROLLER)
    ops = emit_annotation_operations(routes)
    client_src = _write(tmp_path, "OrderReadClient.java", ORDER_CLIENT_WRONG_METHOD)
    urls = extract_client_urls(client_src)
    links = link_client_to_annotation(urls[0], ops)
    assert links, links
    assert all(link["compatible"] is False for link in links), links
    assert any("method" in str(link.get("reason", "")).lower() for link in links), links
    assert all(link["destination_verified"] is False for link in links), links
    assert all(link["equivalent"] is False for link in links), links
    assert all(link.get("source") == "spring_annotation" for link in links), links


def test_ambiguous_duplicate_routes_abstain(tmp_path):
    from bo_java.spring_sql_bindings import extract_client_urls

    path_a = _write(tmp_path, "DupAlphaController.java", DUP_A)
    path_b = _write(tmp_path, "DupBetaController.java", DUP_B)
    routes = extract_mapping_routes(path_a) + extract_mapping_routes(path_b)
    ops = emit_annotation_operations(routes)
    assert len([o for o in ops if o.get("linkable")]) == 2, ops
    client_src = _write(tmp_path, "DupClient.java", DUP_CLIENT)
    urls = extract_client_urls(client_src)
    links = link_client_to_annotation(urls[0], ops)
    assert links, links
    assert all(link["compatible"] is False for link in links), links
    assert any("ambiguous" in str(link.get("reason", "")).lower() for link in links), links
    assert all(link["destination_verified"] is False for link in links), links
    assert all(link["equivalent"] is False for link in links), links
    assert all(link.get("source") == "spring_annotation" for link in links), links


def test_path_template_match_supported(tmp_path):
    from bo_java.spring_sql_bindings import extract_client_urls

    routes, _ = _routes_for(tmp_path, "WidgetTemplateController.java", TEMPLATE_CONTROLLER)
    assert [r["path"] for r in routes] == ["/api/widgets/{widgetId}"], routes
    ops = emit_annotation_operations(routes)
    client_src = _write(tmp_path, "WidgetReadClient.java", TEMPLATE_CLIENT)
    urls = extract_client_urls(client_src)
    links = link_client_to_annotation(urls[0], ops)
    assert links and links[0]["compatible"] is True, links
    assert all(link["destination_verified"] is False for link in links), links
    assert all(link["equivalent"] is False for link in links), links
    assert all(link.get("source") == "spring_annotation" for link in links), links


STATIC_IMPORT_SINGLE_CONTROLLER = """\
package com.example.widgets;

import static org.springframework.web.bind.annotation.RequestMethod.POST;

import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class WidgetStaticImportController {

    @RequestMapping(path = "/static-post", method = POST)
    public String staticPost() {
        return "ok";
    }
}
"""

QUALIFIED_PUT_CONTROLLER = """\
package com.example.widgets;

import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class WidgetQualifiedPutController {

    @RequestMapping(value = "/qualified-put", method = RequestMethod.PUT)
    public String qualifiedPut() {
        return "ok";
    }
}
"""

ARRAY_QUALIFIED_CONTROLLER = """\
package com.example.widgets;

import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class WidgetArrayQualifiedController {

    @RequestMapping(value = "/multi-verb", method = {RequestMethod.GET, RequestMethod.POST})
    public String multiVerb() {
        return "ok";
    }
}
"""

ARRAY_BARE_CONTROLLER = """\
package com.example.orders;

import static org.springframework.web.bind.annotation.RequestMethod.POST;
import static org.springframework.web.bind.annotation.RequestMethod.PUT;

import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class OrderArrayBareController {

    @RequestMapping(value = "/bare-multi", method = {POST, PUT})
    public String bareMulti() {
        return "ok";
    }
}
"""

ARRAY_MIXED_CONTROLLER = """\
package com.example.orders;

import static org.springframework.web.bind.annotation.RequestMethod.POST;

import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class OrderArrayMixedController {

    @RequestMapping(value = "/mixed-multi", method = {RequestMethod.GET, POST})
    public String mixedMulti() {
        return "ok";
    }
}
"""

HEAD_OPTIONS_CONTROLLER = """\
package com.example.widgets;

import static org.springframework.web.bind.annotation.RequestMethod.OPTIONS;

import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class WidgetHeadOptionsController {

    @RequestMapping(value = "/head-route", method = RequestMethod.HEAD)
    public String headRoute() {
        return "ok";
    }

    @RequestMapping(value = "/options-route", method = OPTIONS)
    public String optionsRoute() {
        return "ok";
    }
}
"""

UNKNOWN_METHOD_CONTROLLER = """\
package com.example.widgets;

import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class WidgetUnknownMethodController {

    @RequestMapping(value = "/mystery", method = FOO)
    public String mystery() {
        return "ok";
    }
}
"""

DYNAMIC_METHOD_CONTROLLER = """\
package com.example.widgets;

import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class WidgetDynamicMethodController {

    @RequestMapping(value = "/dyn-verb", method = someVar)
    public String dynVerb() {
        return "ok";
    }
}
"""

BITWISE_METHOD_CONTROLLER = """\
package com.example.widgets;

import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class WidgetBitwiseMethodController {

    @RequestMapping(value = "/bitwise", method = RequestMethod.GET | RequestMethod.POST)
    public String bitwise() {
        return "ok";
    }
}
"""


def test_static_import_single_resolves_post(tmp_path):
    routes, _ = _routes_for(
        tmp_path, "WidgetStaticImportController.java", STATIC_IMPORT_SINGLE_CONTROLLER
    )
    assert len(routes) == 1, routes
    assert routes[0]["http_methods"] == ["post"], routes
    assert routes[0]["verb_unknown"] is False, routes
    assert routes[0]["path"] == "/static-post", routes


def test_qualified_single_put(tmp_path):
    routes, _ = _routes_for(
        tmp_path, "WidgetQualifiedPutController.java", QUALIFIED_PUT_CONTROLLER
    )
    assert len(routes) == 1, routes
    assert routes[0]["http_methods"] == ["put"], routes
    assert routes[0]["verb_unknown"] is False, routes


def test_array_qualified_get_post_order_preserved(tmp_path):
    from bo_java.spring_sql_bindings import extract_client_urls

    routes, _ = _routes_for(
        tmp_path, "WidgetArrayQualifiedController.java", ARRAY_QUALIFIED_CONTROLLER
    )
    assert len(routes) == 1, routes
    assert routes[0]["http_methods"] == ["get", "post"], routes
    assert set(routes[0]["http_methods"]) == {"get", "post"}, routes
    assert routes[0]["verb_unknown"] is False, routes
    # 2-verb routes never link as single-method operations: abstain.
    ops = emit_annotation_operations(routes)
    assert len(ops) == 1 and ops[0].get("linkable") is False, ops
    assert ops[0].get("method") is None, ops
    client_src = _write(
        tmp_path,
        "MultiVerbClient.java",
        "package com.example.widgets;\n"
        "import org.springframework.web.client.RestTemplate;\n"
        "public class MultiVerbClient {\n"
        "    private final RestTemplate restTemplate = new RestTemplate();\n"
        "    public String call() {\n"
        '        String url = "http://widget-svc.local/multi-verb";\n'
        "        return restTemplate.getForObject(url, String.class);\n"
        "    }\n"
        "}\n",
    )
    urls = extract_client_urls(client_src)
    links = link_client_to_annotation(urls[0], ops)
    assert links, links
    assert all(link["compatible"] is False for link in links), links


def test_array_bare_post_put(tmp_path):
    routes, _ = _routes_for(
        tmp_path, "OrderArrayBareController.java", ARRAY_BARE_CONTROLLER
    )
    assert len(routes) == 1, routes
    assert set(routes[0]["http_methods"] or []) == {"post", "put"}, routes
    assert routes[0]["verb_unknown"] is False, routes


def test_array_mixed_qualified_and_bare(tmp_path):
    routes, _ = _routes_for(
        tmp_path, "OrderArrayMixedController.java", ARRAY_MIXED_CONTROLLER
    )
    assert len(routes) == 1, routes
    assert set(routes[0]["http_methods"] or []) == {"get", "post"}, routes
    assert routes[0]["verb_unknown"] is False, routes


def test_head_options_single_verbs(tmp_path):
    routes, _ = _routes_for(
        tmp_path, "WidgetHeadOptionsController.java", HEAD_OPTIONS_CONTROLLER
    )
    by_path = {r["path"]: r for r in routes}
    assert by_path["/head-route"]["http_methods"] == ["head"], routes
    assert by_path["/options-route"]["http_methods"] == ["options"], routes
    assert all(r["verb_unknown"] is False for r in routes), routes


def test_unknown_method_abstains(tmp_path):
    routes, _ = _routes_for(
        tmp_path, "WidgetUnknownMethodController.java", UNKNOWN_METHOD_CONTROLLER
    )
    assert len(routes) == 1, routes
    assert routes[0]["http_methods"] is None, routes
    assert routes[0]["verb_unknown"] is True, routes


def test_dynamic_method_var_abstains(tmp_path):
    routes, _ = _routes_for(
        tmp_path, "WidgetDynamicMethodController.java", DYNAMIC_METHOD_CONTROLLER
    )
    assert len(routes) == 1, routes
    assert routes[0]["http_methods"] is None, routes
    assert routes[0]["verb_unknown"] is True, routes


def test_bitwise_method_abstains(tmp_path):
    routes, _ = _routes_for(
        tmp_path, "WidgetBitwiseMethodController.java", BITWISE_METHOD_CONTROLLER
    )
    assert len(routes) == 1, routes
    assert routes[0]["http_methods"] is None, routes
    assert routes[0]["verb_unknown"] is True, routes


LOWERCASE_VAR_CONTROLLER = """\
package com.example.widgets;

import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class WidgetLowercaseVarController {

    @RequestMapping(value = "/lower", method = get)
    public String lower() {
        return "ok";
    }
}
"""


SINGLE_COMMA_CONTROLLER = """\
package com.example.widgets;

import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class WidgetSingleCommaController {

    @RequestMapping(value = "/comma", method = RequestMethod.GET, POST)
    public String comma() {
        return "ok";
    }
}
"""


POSITIONAL_MULTI_CONTROLLER = """\
package com.example.widgets;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class WidgetPositionalMultiController {

    @GetMapping("/x","/y")
    public String multi() {
        return "ok";
    }
}
"""


QUOTED_METHOD_CONTROLLER = """\
package com.example.widgets;

import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class WidgetQuotedMethodController {

    @RequestMapping(value = "/quoted", method = "GET")
    public String quoted() {
        return "ok";
    }
}
"""


EMPTY_METHOD_CONTROLLER = """\
package com.example.widgets;

import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class WidgetEmptyMethodController {

    @RequestMapping(value = "/empty", method = {})
    public String empty() {
        return "ok";
    }
}
"""


def test_lowercase_bare_var_abstains_never_compatible(tmp_path):
    from bo_java.spring_sql_bindings import extract_client_urls

    routes, _ = _routes_for(
        tmp_path, "WidgetLowercaseVarController.java", LOWERCASE_VAR_CONTROLLER
    )
    assert len(routes) == 1, routes
    assert routes[0]["http_methods"] is None, routes
    assert routes[0]["verb_unknown"] is True, routes
    ops = emit_annotation_operations(routes)
    assert ops and ops[0].get("linkable") is False, ops
    client_src = _write(
        tmp_path,
        "LowerClient.java",
        "package com.example.widgets;\n"
        "import org.springframework.web.client.RestTemplate;\n"
        "public class LowerClient {\n"
        "    private final RestTemplate restTemplate = new RestTemplate();\n"
        "    public String call() {\n"
        '        String url = "http://widget-svc.local/lower";\n'
        "        return restTemplate.getForObject(url, String.class);\n"
        "    }\n"
        "}\n",
    )
    urls = extract_client_urls(client_src)
    links = link_client_to_annotation(urls[0], ops)
    assert links, links
    assert all(link["compatible"] is False for link in links), links


def test_single_without_braces_comma_abstains(tmp_path):
    routes, _ = _routes_for(
        tmp_path, "WidgetSingleCommaController.java", SINGLE_COMMA_CONTROLLER
    )
    assert len(routes) == 1, routes
    assert routes[0]["http_methods"] is None, routes
    assert routes[0]["verb_unknown"] is True, routes


def test_positional_multi_without_braces_abstains(tmp_path):
    routes, _ = _routes_for(
        tmp_path,
        "WidgetPositionalMultiController.java",
        POSITIONAL_MULTI_CONTROLLER,
    )
    assert routes, "positional multi must still yield a record with a reason"
    assert all(r["path"] is None for r in routes), routes
    assert all("dynamic" in str(r.get("reason", "")).lower() for r in routes), routes


def test_quoted_method_abstains_never_compatible(tmp_path):
    from bo_java.spring_sql_bindings import extract_client_urls

    routes, _ = _routes_for(
        tmp_path, "WidgetQuotedMethodController.java", QUOTED_METHOD_CONTROLLER
    )
    assert len(routes) == 1, routes
    assert routes[0]["http_methods"] is None, routes
    assert routes[0]["verb_unknown"] is True, routes
    ops = emit_annotation_operations(routes)
    assert ops and ops[0].get("linkable") is False, ops
    client_src = _write(
        tmp_path,
        "QuotedClient.java",
        "package com.example.widgets;\n"
        "import org.springframework.web.client.RestTemplate;\n"
        "public class QuotedClient {\n"
        "    private final RestTemplate restTemplate = new RestTemplate();\n"
        "    public String call() {\n"
        '        String url = "http://widget-svc.local/quoted";\n'
        "        return restTemplate.getForObject(url, String.class);\n"
        "    }\n"
        "}\n",
    )
    urls = extract_client_urls(client_src)
    links = link_client_to_annotation(urls[0], ops)
    assert links, links
    assert all(link["compatible"] is False for link in links), links


def test_empty_method_array_abstains_never_compatible(tmp_path):
    from bo_java.spring_sql_bindings import extract_client_urls

    routes, _ = _routes_for(
        tmp_path, "WidgetEmptyMethodController.java", EMPTY_METHOD_CONTROLLER
    )
    assert len(routes) == 1, routes
    assert routes[0]["http_methods"] is None, routes
    assert routes[0]["verb_unknown"] is True, routes
    ops = emit_annotation_operations(routes)
    assert ops and ops[0].get("linkable") is False, ops
    client_src = _write(
        tmp_path,
        "EmptyClient.java",
        "package com.example.widgets;\n"
        "import org.springframework.web.client.RestTemplate;\n"
        "public class EmptyClient {\n"
        "    private final RestTemplate restTemplate = new RestTemplate();\n"
        "    public String call() {\n"
        '        String url = "http://widget-svc.local/empty";\n'
        "        return restTemplate.getForObject(url, String.class);\n"
        "    }\n"
        "}\n",
    )
    urls = extract_client_urls(client_src)
    links = link_client_to_annotation(urls[0], ops)
    assert links, links
    assert all(link["compatible"] is False for link in links), links


def test_static_import_single_is_linkable_compatible(tmp_path):
    from bo_java.spring_sql_bindings import extract_client_urls

    routes, _ = _routes_for(
        tmp_path, "WidgetStaticImportController.java", STATIC_IMPORT_SINGLE_CONTROLLER
    )
    ops = emit_annotation_operations(routes)
    assert len(ops) == 1 and ops[0].get("linkable") is True, ops
    assert ops[0].get("method") == "post", ops
    client_src = _write(
        tmp_path,
        "StaticPostClient.java",
        "package com.example.widgets;\n"
        "import org.springframework.web.client.RestTemplate;\n"
        "public class StaticPostClient {\n"
        "    private final RestTemplate restTemplate = new RestTemplate();\n"
        "    public String call() {\n"
        '        String url = "http://widget-svc.local/static-post";\n'
        "        return restTemplate.postForObject(url, new Object(), String.class);\n"
        "    }\n"
        "}\n",
    )
    urls = extract_client_urls(client_src)
    links = link_client_to_annotation(urls[0], ops)
    assert links and links[0]["compatible"] is True, links


def test_unknown_method_never_compatible(tmp_path):
    from bo_java.spring_sql_bindings import extract_client_urls

    routes, _ = _routes_for(
        tmp_path, "WidgetUnknownMethodController.java", UNKNOWN_METHOD_CONTROLLER
    )
    ops = emit_annotation_operations(routes)
    assert ops and ops[0].get("linkable") is False, ops
    client_src = _write(
        tmp_path,
        "MysteryClient.java",
        "package com.example.widgets;\n"
        "import org.springframework.web.client.RestTemplate;\n"
        "public class MysteryClient {\n"
        "    private final RestTemplate restTemplate = new RestTemplate();\n"
        "    public String call() {\n"
        '        String url = "http://widget-svc.local/mystery";\n'
        "        return restTemplate.postForObject(url, new Object(), String.class);\n"
        "    }\n"
        "}\n",
    )
    urls = extract_client_urls(client_src)
    links = link_client_to_annotation(urls[0], ops)
    assert links, links
    assert all(link["compatible"] is False for link in links), links


def _find_realworld_files():
    """Shape-based discovery (no literal filenames): all ``*.java`` files
    containing ``@RequestMapping`` plus a ``method=POST`` marker (qualified
    ``RequestMethod.POST`` or bare ``POST`` static import)."""
    import os
    import re

    override = os.environ.get("REALWORLD_ROOT")
    roots: list[Path] = []
    if override:
        candidate = Path(override)
        if candidate.is_dir():
            roots.append(candidate)
    for base in (
        "/tmp/opencode/oss-eval-realworld/spring-boot-realworld-example-app",
        "/tmp/realworld",
        "/tmp/spring-boot-realworld-example-app",
    ):
        root = Path(base)
        if root.is_dir():
            roots.append(root)
    marker = re.compile(r"method\s*=\s*(?:RequestMethod\s*\.\s*)?POST\b")
    found: list[Path] = []
    for root in roots:
        for path in root.glob("**/*.java"):
            try:
                if not os.path.isfile(path):
                    continue
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if "@RequestMapping" in text and marker.search(text):
                found.append(path)
    return found


WRAPPED_CONTROLLER = """\
package com.example.orders;

import javax.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping(path = "/shipments")
public class ShipmentWrappedController {

    @PostMapping
    public ResponseEntity createShipment(
            @Valid @RequestBody NewShipmentParam newShipmentParam,
            @AuthenticationPrincipal User user) {
        return ResponseEntity.ok("");
    }

    @GetMapping(path = "feed")
    public ResponseEntity listFeed(
            @RequestParam(value = "offset", defaultValue = "0") int offset,
            @RequestParam(value = "limit", defaultValue = "20") int limit) {
        return ResponseEntity.ok("");
    }
}
"""

UNCLOSED_CONTROLLER = """\
package com.example.orders;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class ShipmentUnclosedController {

    @GetMapping("/half")
    public String halfOpen(String a,
            String b,
            String c,
            String d,
            String e,
            String f,
            String g,
            String h,
            String i,
            String j,
            String k) {
        return "ok";
    }

    @GetMapping("/whole")
    public String whole() {
        return "ok";
    }
}
"""

WRAPPED_DYNAMIC_CONTROLLER = """\
package com.example.orders;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class ShipmentWrappedDynamicController {

    private static final String PATH_CONST = "/dynamic";

    @GetMapping(PATH_CONST)
    public String byConst(
            String a) {
        return "ok";
    }

    @RequestMapping(value = "/novel")
    public String novel(
            String a) {
        return "ok";
    }
}
"""


def test_wrapped_signatures_join_continuation_lines(tmp_path):
    routes, _ = _routes_for(
        tmp_path, "ShipmentWrappedController.java", WRAPPED_CONTROLLER
    )
    assert len(routes) == 2, routes
    by_method = {r["method"]: r for r in routes}
    create = by_method["createShipment"]
    assert create["http_methods"] == ["post"], create
    assert create["path"] == "/shipments", create
    assert create["span"]["start_line"] == create["method_annotation"]["line"], create
    assert create["span"]["end_line"] > create["line"], create
    feed = by_method["listFeed"]
    assert feed["http_methods"] == ["get"], feed
    assert feed["path"] == "/shipments/feed", feed
    assert feed["span"]["end_line"] > feed["line"], feed
    for route in routes:
        assert route["kind"] == "mapping_route", route
        assert route["source"] == "spring_annotation", route


def test_unclosed_decl_beyond_bound_abstains_single_line_survives(tmp_path):
    routes, _ = _routes_for(
        tmp_path, "ShipmentUnclosedController.java", UNCLOSED_CONTROLLER
    )
    assert [r["method"] for r in routes] == ["whole"], routes
    assert routes[0]["path"] == "/whole", routes
    assert routes[0]["http_methods"] == ["get"], routes


PAREN_IN_STRING_CONTROLLER = """\
package com.example.orders;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/punct")
public class PunctController {

    @GetMapping("/by")
    public String by(@RequestParam(value = ")") String sep) {
        return "ok";
    }
}
"""


def test_paren_in_string_decl_parses_single_line(tmp_path):
    """LOW: a ``)`` inside a string literal (``value = ")"``) must not trick
    the wrap detector into joining continuation lines and dropping the route."""
    routes, _ = _routes_for(
        tmp_path, "PunctController.java", PAREN_IN_STRING_CONTROLLER
    )
    assert [r["method"] for r in routes] == ["by"], routes
    assert routes[0]["path"] == "/punct/by", routes
    assert routes[0]["http_methods"] == ["get"], routes


def test_wrapped_dynamic_and_verb_unknown_still_abstain(tmp_path):
    routes, _ = _routes_for(
        tmp_path,
        "ShipmentWrappedDynamicController.java",
        WRAPPED_DYNAMIC_CONTROLLER,
    )
    assert len(routes) == 2, routes
    by_method = {r["method"]: r for r in routes}
    assert by_method["byConst"]["path"] is None, routes
    assert "dynamic" in str(by_method["byConst"].get("reason", "")).lower(), routes
    assert by_method["novel"]["http_methods"] is None, routes
    assert by_method["novel"]["verb_unknown"] is True, routes


def test_realworld_exposed_regression_requestmapping_post():
    """REGRESSION (read-only): exposed tree bare static-import POST resolves."""
    candidates = _find_realworld_files()
    if not candidates:
        pytest.skip("RealWorld sources absent under /tmp (no download attempted)")
    routes = [r for jf in candidates for r in extract_mapping_routes(jf)]
    assert routes, "setup must find exposed mapping routes"
    posts = [
        r
        for r in routes
        if (r.get("http_methods") or []) == ["post"] and r.get("path")
    ]
    assert posts, routes
    assert all(r.get("verb_unknown") is False for r in posts), routes


def _find_petclinic_files():
    """Shape-based discovery (no literal filenames): all ``*.java`` files
    containing a Spring mapping marker (``@RequestMapping``/``@GetMapping``
    etc). Callers filter further by route/path shape, never by filename."""
    import os

    candidates: list[Path] = []
    for base in ("/tmp", "/tmp/petclinic", "/tmp/spring-petclinic"):
        root = Path(base)
        if not root.exists():
            continue
        try:
            matches = list(root.glob("**/*.java"))
        except OSError:
            continue
        for path in matches:
            try:
                if not os.path.isfile(path):
                    continue
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if (
                "@RequestMapping" in text
                or "@GetMapping" in text
                or "@PostMapping" in text
                or "@PutMapping" in text
            ):
                candidates.append(path)
    # Deduplicate by resolved path.
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def test_petclinic_exposed_regression_readonly():
    """REGRESSION (read-only /tmp, no build): owners routes join; gateway
    WebClient composition is unsupported so 0 verified annotation links
    asserted (all links compatible False)."""
    from bo_java.spring_sql_bindings import extract_client_urls

    candidates = _find_petclinic_files()
    if not candidates:
        pytest.skip("PetClinic sources absent under /tmp (no download attempted)")
    all_routes = [r for jf in candidates for r in extract_mapping_routes(jf)]
    if not all_routes:
        pytest.skip("PetClinic mapping routes absent (no download attempted)")
    owner_paths = {(tuple(r.get("http_methods") or []), r.get("path")) for r in all_routes}
    assert (("post",), "/owners") in owner_paths, owner_paths
    assert any(
        methods == ("get",) and (path or "").endswith("{ownerId}")
        for methods, path in owner_paths
    ), owner_paths
    assert any(
        methods == ("put",) and (path or "").endswith("{ownerId}")
        for methods, path in owner_paths
    ), owner_paths
    gateway_paths = {
        (tuple(r.get("http_methods") or []), r.get("path")) for r in all_routes
    }
    assert any(
        methods == ("get",) and "owners" in (path or "") and "{ownerId}" in (path or "")
        for methods, path in gateway_paths
    ), gateway_paths
    ops = emit_annotation_operations(all_routes)
    assert any(o.get("source") == "spring_annotation" for o in ops), ops
    gateway_urls = [u for jf in candidates for u in extract_client_urls(jf)]
    if not gateway_urls:
        pytest.skip("PetClinic client URLs absent (no download attempted)")
    gateway_links = [
        link
        for url_record in gateway_urls
        for link in link_client_to_annotation(url_record, ops)
    ]
    assert all(link["compatible"] is False for link in gateway_links), gateway_links
    assert not any(link["compatible"] is True for link in gateway_links), gateway_links
