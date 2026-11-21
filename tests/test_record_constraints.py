"""Record component constraint extraction + enforcement linkage (synthetic only)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.bo_java.spring_sql_bindings import (
    extract_record_constraints,
    has_valid_enforcement,
    link_record_enforcement,
)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


_SINGLE = """package demo;
public record ShipmentRequest(@NotNull String parcelId, String note) {
}
"""

_MULTI = """package demo;
public record Parcel(
    @NotBlank @Size(min = 1, max = 50) String label,
    @Digits(fraction = 0, integer = 12) String code,
    String memo
) {
}
"""

_BOUND = """package demo;
import jakarta.validation.Valid;
public class ParcelController {
    public String create(@Valid @RequestBody Parcel parcel) {
        return "ok";
    }
}
"""

_DECLARED_ONLY = """package demo;
public class ParcelController {
    public String create(Parcel parcel) {
        return "ok";
    }
}
"""


class TestSingleLineRecord:
    def test_annotated_component_emits_one_constraint(self, tmp_path):
        path = _write(tmp_path, "ShipmentRequest.java", _SINGLE)
        records = extract_record_constraints(path)
        assert len(records) == 1
        rec = records[0]
        assert rec["kind"] == "record_component_constraint"
        assert rec["record"] == "ShipmentRequest"
        assert rec["component"] == "parcelId"
        assert rec["annotation"] == "NotNull"
        assert rec["origin"] == "deterministic"
        assert rec["content_hash"] == _hash(path)

    def test_unannotated_component_emits_nothing(self, tmp_path):
        path = _write(tmp_path, "ShipmentRequest.java", _SINGLE)
        records = extract_record_constraints(path)
        assert [r for r in records if r["component"] == "note"] == []


class TestMultiLineRecord:
    def test_multiple_annotations_with_params_and_spans(self, tmp_path):
        path = _write(tmp_path, "Parcel.java", _MULTI)
        records = extract_record_constraints(path)
        assert len(records) == 3
        by_ann = {(r["annotation"], r["component"]): r for r in records}
        assert ("NotBlank", "label") in by_ann
        assert ("Size", "label") in by_ann
        assert ("Digits", "code") in by_ann
        assert by_ann[("Size", "label")]["params"] == {"min": "1", "max": "50"}
        assert by_ann[("Digits", "code")]["params"] == {
            "fraction": "0",
            "integer": "12",
        }
        for rec in records:
            span = rec["span"]
            assert span["start_line"] <= rec["line"] <= span["end_line"]
            assert span["end_line"] >= rec["line"]
        assert [r for r in records if r["component"] == "memo"] == []


class TestEnforcementLinkage:
    def test_valid_bound_record_verifies(self, tmp_path):
        ctrl = _write(tmp_path, "ParcelController.java", _BOUND)
        result = link_record_enforcement("Parcel", [ctrl])
        assert result["verified"] is True
        assert "400" not in result["reason"]
        assert result["evidence_spans"] != []

    def test_declared_only_does_not_verify(self, tmp_path):
        ctrl = _write(tmp_path, "ParcelController.java", _DECLARED_ONLY)
        result = link_record_enforcement("Parcel", [ctrl])
        assert result["verified"] is False
        assert "400" not in result["reason"]


class TestNegatives:
    def test_non_validation_annotation_yields_nothing(self, tmp_path):
        path = _write(
            tmp_path,
            "Parcel.java",
            "package demo;\n"
            "public record Parcel(@JsonProperty String label) {\n}\n",
        )
        assert extract_record_constraints(path) == []

    def test_unannotated_component_yields_nothing(self, tmp_path):
        path = _write(
            tmp_path,
            "Parcel.java",
            "package demo;\npublic record Parcel(String label) {\n}\n",
        )
        assert extract_record_constraints(path) == []

    def test_method_param_is_not_a_component(self, tmp_path):
        path = _write(
            tmp_path,
            "Parcel.java",
            "package demo;\n"
            "public class Parcel {\n"
            "    public String load(@NotNull String parcelId) {\n"
            "        return parcelId;\n"
            "    }\n"
            "}\n",
        )
        assert extract_record_constraints(path) == []

    def test_compact_constructor_not_double_counted(self, tmp_path):
        path = _write(
            tmp_path,
            "Parcel.java",
            "package demo;\n"
            "public record Parcel(@NotNull String label) {\n"
            "    public Parcel {\n"
            "    }\n"
            "}\n",
        )
        records = extract_record_constraints(path)
        assert len(records) == 1
        assert records[0]["component"] == "label"

    def test_malformed_unbalanced_abstains(self, tmp_path):
        path = _write(
            tmp_path,
            "Parcel.java",
            "package demo;\npublic record Parcel(@NotNull String label,\n",
        )
        assert extract_record_constraints(path) == []


class TestSiblingValidDoesNotVerify:
    def test_valid_on_sibling_param_abstains(self, tmp_path):
        ctrl = _write(
            tmp_path,
            "ParcelController.java",
            "package demo;\n"
            "import jakarta.validation.Valid;\n"
            "public class ParcelController {\n"
            "    public String create(@Valid OtherDto o, Parcel parcel) {\n"
            '        return "ok";\n'
            "    }\n"
            "}\n",
        )
        result = link_record_enforcement("Parcel", [ctrl])
        assert result["verified"] is False
        assert "400" not in result["reason"]

    def test_valid_on_same_param_verifies(self, tmp_path):
        ctrl = _write(
            tmp_path,
            "ParcelController.java",
            "package demo;\n"
            "import jakarta.validation.Valid;\n"
            "public class ParcelController {\n"
            "    public String create(OtherDto o, @Valid Parcel parcel) {\n"
            '        return "ok";\n'
            "    }\n"
            "}\n",
        )
        result = link_record_enforcement("Parcel", [ctrl])
        assert result["verified"] is True


class TestGenericRecord:
    def test_generic_type_params_parse(self, tmp_path):
        path = _write(
            tmp_path,
            "Pair.java",
            "package demo;\n"
            "public record Pair<K,V>(@NotNull K first, @NotNull V second) {\n}\n",
        )
        records = extract_record_constraints(path)
        assert len(records) == 2
        assert {r["component"] for r in records} == {"first", "second"}
        assert {r["record"] for r in records} == {"Pair"}


class TestMalformedTwoRecord:
    def test_inner_record_rejects_outer(self, tmp_path):
        path = _write(
            tmp_path,
            "Two.java",
            "package demo;\n"
            "public record A(String x,\n"
            "public record B(@NotNull String y) {\n}\n",
        )
        records = extract_record_constraints(path)
        assert [r for r in records if r["record"] == "A"] == []
        by_b = [r for r in records if r["record"] == "B"]
        assert len(by_b) == 1
        assert by_b[0]["component"] == "y"


class TestNestedAndQuotedParams:
    def test_nested_call_and_quoted_comma(self, tmp_path):
        path = _write(
            tmp_path,
            "Parcel.java",
            "package demo;\n"
            "public record Parcel(@Size(min=Math.max(1,2), max=50) String label,\n"
            ' @Pattern(regexp="a,b") String code) {\n}\n',
        )
        records = extract_record_constraints(path)
        by_ann = {(r["annotation"], r["component"]): r for r in records}
        assert by_ann[("Size", "label")]["params"]["min"] == "Math.max(1,2)"
        assert by_ann[("Size", "label")]["params"]["max"] == "50"
        assert by_ann[("Pattern", "code")]["params"] == {"regexp": "a,b"}


class TestComponentComment:
    def test_trailing_comment_not_component(self, tmp_path):
        path = _write(
            tmp_path,
            "Parcel.java",
            "package demo;\n"
            "public record Parcel(@NotNull String label /* note */) {\n}\n",
        )
        records = extract_record_constraints(path)
        assert len(records) == 1
        assert records[0]["component"] == "label"


class TestFactoryAlone:
    def test_factory_without_validate_does_not_verify(self, tmp_path):
        ctrl = _write(
            tmp_path,
            "ParcelController.java",
            "package demo;\n"
            "public class ParcelController {\n"
            "    public String create(Parcel parcel) {\n"
            "        var f = Validation.buildDefaultValidatorFactory();\n"
            '        return "ok";\n'
            "    }\n"
            "}\n",
        )
        result = link_record_enforcement("Parcel", [ctrl])
        assert result["verified"] is False
        file_result = has_valid_enforcement(ctrl)
        assert file_result["verified"] is False

    def test_factory_plus_validate_verifies(self, tmp_path):
        ctrl = _write(
            tmp_path,
            "ParcelController.java",
            "package demo;\n"
            "public class ParcelController {\n"
            "    public String create(Parcel parcel) {\n"
            "        var f = Validation.buildDefaultValidatorFactory();\n"
            "        validator.validate(parcel);\n"
            '        return "ok";\n'
            "    }\n"
            "}\n",
        )
        result = link_record_enforcement("Parcel", [ctrl])
        assert result["verified"] is True


class TestSubstringFalsePositive:
    def test_string_literal_does_not_verify(self, tmp_path):
        ctrl = _write(
            tmp_path,
            "ParcelController.java",
            "package demo;\n"
            "import jakarta.validation.Valid;\n"
            "public class ParcelController {\n"
            '    public String create(@Valid OtherDto o, @RequestBody("Parcel") String s) {\n'
            '        return "ok";\n'
            "    }\n"
            "}\n",
        )
        result = link_record_enforcement("Parcel", [ctrl])
        assert result["verified"] is False


class TestEmptyRecordName:
    def test_empty_name_abstains(self, tmp_path):
        ctrl = _write(tmp_path, "ParcelController.java", _BOUND)
        for bad in ("", "   "):
            result = link_record_enforcement(bad, [ctrl])
            assert result["verified"] is False


class TestPreservedSourceRegression:
    def test_discovered_records_wellformed(self, tmp_path):
        import glob
        import os

        candidates: list[str] = []
        for root in ("fixtures", "tests/fixtures", "."):
            candidates.extend(
                glob.glob(os.path.join(root, "**", "*.java"), recursive=True)
            )
        java_files = [p for p in candidates if os.path.isfile(p)]
        if not java_files:
            pytest.skip("no java sources present")
        found = False
        for jf in java_files:
            try:
                text = Path(jf).read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if "record " not in text:
                continue
            found = True
            recs = extract_record_constraints(Path(jf))
            for rec in recs:
                assert rec["span"]["start_line"] <= rec["line"] <= rec["span"]["end_line"]
                assert isinstance(rec["params"], dict)
                assert isinstance(rec["component"], str) and rec["component"].strip()
                link = link_record_enforcement(str(rec["record"]), [Path(jf)])
                assert isinstance(link["verified"], bool)
                assert "400" not in link["reason"]
        if not found:
            pytest.skip("absent: no record decl in discovered java")
