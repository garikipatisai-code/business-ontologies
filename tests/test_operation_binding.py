"""OpenAPI-operation -> Java-implementation binding regressions.

Five behaviors, all source-backed with distinct synthetic names (Widget
domain, never PetClinic) except the final revealed-source regression,
which discovers its candidate dynamically (no names/paths/lines
hard-coded: files are found by glob, the operation is selected by the
unresolved-interface shape, never by literal id).

1. Full bind: synthetic POST /widgets + createWidget + 201, interface
   present, controller implements it with matching arity, path literal
   plus return-type corroboration, 201+Location constructed ->
   ``full`` with four evidence groups.
2. Name-only negative: same method name but arity mismatch ->
   ``unresolved`` (no bind), reason cites the mismatch.
3. Ambiguous: two controllers implement the same interface method ->
   abstain (``unresolved``, never verified).
4. Declared-response-unsupported: identical setup to full but the impl
   returns 200 with no Location while 201 is declared -> ``partial``
   with ``response_supported`` False.
5. Preserved-source regression: run the binder over the read-only
   PetClinic tree; any operation whose id matches a controller method
   while no source interface declares it must never bind ``full``
   (revealed defect shape: generated interface absent at source).
"""

from __future__ import annotations

from pathlib import Path

from bo_java.spring_sql_bindings import (
    bind_operation_to_implementation,
    extract_implements,
    extract_java_interfaces,
    extract_openapi,
)

_WIDGET_OPENAPI = """\
openapi: 3.0.0
paths:
  /widgets:
    post:
      operationId: createWidget
      responses:
        '201':
          description: Created
          headers:
            Location:
              description: Location of the new widget
"""

_WIDGET_API = """\
package example.api;

import org.springframework.http.ResponseEntity;

public interface WidgetApi {
    ResponseEntity<WidgetDto> createWidget(WidgetFieldsDto body);
}
"""

_WIDGET_CONTROLLER_FULL = """\
package example.controller;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import example.api.WidgetApi;

@RestController
@RequestMapping("/api")
public class WidgetController implements WidgetApi {
    @Override
    @PostMapping("/widgets")
    public ResponseEntity<WidgetDto> createWidget(WidgetFieldsDto body) {
        WidgetDto dto = new WidgetDto(body);
        return ResponseEntity.status(201).header("Location", "/widgets/1").body(dto);
    }
}
"""

_WIDGET_CONTROLLER_ARITY_MISMATCH = """\
package example.controller;

import org.springframework.http.ResponseEntity;
import example.api.WidgetApi;

public class WidgetController implements WidgetApi {
    @Override
    public ResponseEntity<WidgetDto> createWidget(WidgetFieldsDto body, boolean flag) {
        return ResponseEntity.status(201).body(new WidgetDto(body));
    }
}
"""

_WIDGET_CONTROLLER_B = """\
package example.controller;

import org.springframework.http.ResponseEntity;
import example.api.WidgetApi;

public class WidgetControllerB implements WidgetApi {
    @Override
    public ResponseEntity<WidgetDto> createWidget(WidgetFieldsDto body) {
        return ResponseEntity.status(201).body(new WidgetDto(body));
    }
}
"""

_WIDGET_CONTROLLER_UNSUPPORTED = """\
package example.controller;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import example.api.WidgetApi;

public class WidgetController implements WidgetApi {
    @Override
    @PostMapping("/widgets")
    public ResponseEntity<WidgetDto> createWidget(WidgetFieldsDto body) {
        WidgetDto dto = new WidgetDto(body);
        return new ResponseEntity<>(dto, HttpStatus.OK);
    }
}
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _widget_setup(tmp_path: Path, controller_src: str):
    api_doc = _write(tmp_path, "openapi-widget.yaml", _WIDGET_OPENAPI)
    api = _write(tmp_path, "WidgetApi.java", _WIDGET_API)
    controller = _write(tmp_path, "WidgetController.java", controller_src)
    ops = extract_openapi(api_doc)
    assert len(ops) == 1, f"setup must yield one op: {ops!r}"
    op = ops[0]
    assert op["operation_id"] == "createWidget", f"operationId not captured: {op!r}"
    return (
        op,
        extract_java_interfaces(api),
        extract_implements(controller),
    )


class TestOperationBindingFull:
    def test_full_binding_with_four_evidence_groups(self, tmp_path):
        op, interfaces, implementations = _widget_setup(
            tmp_path, _WIDGET_CONTROLLER_FULL
        )
        assert len(interfaces) == 1 and len(implementations) == 1
        # Span evidence from the synthetic OpenAPI fixture.
        assert op["operation_id_line"] == 5, f"operationId line not cited: {op!r}"
        assert op["responses_span"] is not None, f"responses span missing: {op!r}"
        assert op["responses_span"] == {"start_line": 6, "end_line": 10}, (
            f"unexpected responses span: {op!r}"
        )
        assert op["responses"][0]["status"] == "201"
        assert op["responses"][0]["line"] == 7, f"status line not cited: {op!r}"
        assert op["responses"][0]["headers"][0]["name"] == "Location"
        assert op["responses"][0]["headers"][0]["line"] == 10, (
            f"header line not cited: {op!r}"
        )
        binding = bind_operation_to_implementation(op, interfaces, implementations)
        assert binding["binding"] == "full", f"expected full bind: {binding!r}"
        assert binding["verified"] is True
        for group in ("declared", "interface", "implementation", "response_construction"):
            assert binding[group], f"evidence group {group!r} missing: {binding!r}"
        assert binding["response_supported"] is True
        assert binding["implementation"]["override"] is True
        assert binding["declared"]["spans"]["responses_span"] is not None, (
            f"declared responses span missing: {binding!r}"
        )
        construction = binding["response_construction"]
        assert construction["statuses"][0]["status"] == 201, (
            f"construction must build 201: {binding!r}"
        )
        assert construction["headers"][0]["name"] == "Location", (
            f"construction must build Location: {binding!r}"
        )
        span = construction["span"]
        for entry in construction["statuses"] + construction["headers"]:
            assert span["start_line"] <= entry["line"] <= span["end_line"], (
                f"construction line outside span: {binding!r}"
            )
        # Client invocation / destination / delivery are never proved here.
        assert binding["client_invocation_proved"] is False
        assert binding["destination_verified"] is False
        assert binding["delivery_proved"] is False


class TestOperationBindingNameOnly:
    def test_name_only_arity_mismatch_yields_no_bind(self, tmp_path):
        op, interfaces, implementations = _widget_setup(
            tmp_path, _WIDGET_CONTROLLER_ARITY_MISMATCH
        )
        binding = bind_operation_to_implementation(op, interfaces, implementations)
        assert binding["binding"] == "unresolved", f"name-only must not bind: {binding!r}"
        assert binding["verified"] is False
        assert "arity" in binding["reason"].lower(), (
            f"reason must cite the arity mismatch: {binding!r}"
        )


class TestOperationBindingAmbiguous:
    def test_ambiguous_implementations_abstain(self, tmp_path):
        op, interfaces, implementations = _widget_setup(
            tmp_path, _WIDGET_CONTROLLER_FULL
        )
        other = _write(tmp_path, "WidgetControllerB.java", _WIDGET_CONTROLLER_B)
        implementations = implementations + extract_implements(other)
        binding = bind_operation_to_implementation(op, interfaces, implementations)
        assert binding["binding"] == "unresolved", (
            f"ambiguous impls must abstain: {binding!r}"
        )
        assert binding["verified"] is False
        assert "ambiguous" in binding["reason"].lower(), (
            f"reason must cite ambiguity: {binding!r}"
        )


class TestOperationBindingResponseSupport:
    def test_declared_response_unsupported_yields_partial(self, tmp_path):
        op, interfaces, implementations = _widget_setup(
            tmp_path, _WIDGET_CONTROLLER_UNSUPPORTED
        )
        binding = bind_operation_to_implementation(op, interfaces, implementations)
        assert binding["binding"] == "partial", (
            f"unsupported response must downgrade to partial: {binding!r}"
        )
        assert binding["verified"] is False
        assert binding["response_supported"] is False
        assert binding["response_construction"], (
            f"construction evidence must still be cited: {binding!r}"
        )


# ---------------------------------------------------------------- preserved-source regression


def _petclinic_root() -> Path | None:
    import os

    override = os.environ.get("PETCLINIC_ROOT")
    if override:
        candidate = Path(override)
        if candidate.is_dir():
            return candidate
    fallback = Path("/tmp/opencode/oss-eval/spring-petclinic-rest")
    return fallback if fallback.is_dir() else None


class TestPreservedSourceBinding:
    """Revealed defect shape: generated server interface absent at source.

    Discovers (no hard-coded ids/paths/lines): every OpenAPI operation
    whose id matches a controller method while no source-tree interface
    declares that method must never bind ``full``. At least one such
    candidate must present the revealed shape (declared 201, impl
    constructs 201 + Location) and bind ``partial``.
    """

    def test_unresolved_interface_never_binds_full(self):
        root = _petclinic_root()
        if root is None:
            import pytest

            pytest.skip("preserved PetClinic tree absent (read-only fixture)")
        docs = list(root.glob("src/main/resources/openapi.y*ml"))
        assert docs, "setup must find the preserved openapi document"
        ops = []
        for doc in docs:
            ops.extend(extract_openapi(doc))
        named = [o for o in ops if o.get("operation_id")]
        assert named, "setup must yield operations with operationIds"

        java_files = list(root.glob("src/main/java/**/*.java"))
        assert java_files, "setup must find preserved sources"
        interfaces: list[dict] = []
        implementations: list[dict] = []
        for java in java_files:
            interfaces.extend(extract_java_interfaces(java))
            implementations.extend(extract_implements(java))
        assert implementations, "setup must find controller implementations"
        implemented = {
            name for m in implementations for name in (m.get("interfaces") or [])
        }
        declared_ifaces = {i.get("interface") for i in interfaces}
        missing = implemented - declared_ifaces
        assert missing, (
            "setup must show implemented-but-undeclared (generated) interfaces"
        )

        iface_methods = {i.get("method") for i in interfaces}
        candidates = [
            o
            for o in named
            if o.get("operation_id") not in iface_methods
            and any(
                m.get("method") == o.get("operation_id")
                and set(m.get("interfaces") or []) <= missing
                for m in implementations
            )
        ]
        assert candidates, (
            "setup must yield an unresolved-interface candidate "
            "(operationId == impl method, no source interface declares it)"
        )
        partials = 0
        declared_201 = [
            o
            for o in candidates
            if "201" in {r.get("status") for r in o.get("responses", [])}
        ]
        assert declared_201, (
            "setup must yield a 201-declaring unresolved-interface candidate"
        )
        for op in declared_201:
            binding = bind_operation_to_implementation(op, interfaces, implementations)
            assert binding["binding"] != "full", (
                f"interface-unresolved op must never bind full: {binding!r}"
            )
            assert binding["verified"] is False
            if (
                binding["binding"] == "partial"
                and binding["response_supported"] is True
            ):
                constructed = {
                    s.get("status")
                    for s in (binding["response_construction"] or {}).get(
                        "statuses", []
                    )
                }
                if 201 in constructed:
                    assert "interface" in binding["reason"].lower(), (
                        f"partial reason must cite interface: {binding!r}"
                    )
                    partials += 1
        assert partials >= 1, "revealed 201-constructing partial must be present"
