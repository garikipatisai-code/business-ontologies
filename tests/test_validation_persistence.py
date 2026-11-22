"""Bean Validation/JPA constraint + SQL DDL/FK extraction with evidence.

Synthetic Widget/Part domain (never PetClinic literals) plus a final
read-only preserved-source regression that discovers its candidates by
shape (globs + annotation/content markers, never literal names/paths/
lines): the exposed PetClinic tree under /tmp is read, never modified.

1. Declared constraints: synthetic ``@NotNull`` + ``@Size`` on a field
   carry annotation-to-declaration spans; unannotated fields yield no
   constraint.
2. Getter constraints: an annotated getter records the derived property.
3. Inheritance present: a subclass merges the present superclass
   constraints with ``inherited`` True and the parent span cited.
4. Inheritance gap: a superclass absent from the scanned tree yields an
   ``inheritance_gap`` (unresolved), never fabricated inherited rules.
5. Enforcement verified: a ``@Valid`` bound param (or an explicit
   validator call) in the same method yields ``verified`` True.
6. Enforcement declared-only: constraints without ``@Valid`` yield
   ``verified`` False (never an HTTP 400 claim).
7. SQL DDL: ``CREATE TABLE IF NOT EXISTS`` plus inline
   ``FOREIGN KEY ... REFERENCES ... ON DELETE CASCADE`` with spans;
   ``ALTER TABLE ... ADD CONSTRAINT`` FKs with actions recorded.
8. JPA link verified: entity + table + column + FK all match.
9. Link negatives: ambiguous tables, unmatched table, unknown ref table,
   and entities without join columns never verify.
10. FK action negatives: missing/RESTRICT actions are recorded as-is,
    never CASCADE.
11. Preserved-source regression: run the new extractors over the
    read-only PetClinic tree and assert the revealed subset
    (declared ``@NotNull``/``@NotEmpty``, inherited constraint or gap,
    unenforced model, CASCADE FKs, verified JPA link where supported).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from bo_java.spring_sql_bindings import (
    extract_field_constraints,
    extract_jpa_entities,
    extract_sql_persistence,
    has_valid_enforcement,
    link_jpa_to_sql,
    resolve_inheritance,
)

_WIDGET_MODEL = """\
package example;

import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

public class Widget {

    @NotNull
    @Size(min = 1, max = 50)
    private String name;

    private int count;
}
"""

_WIDGET_GETTER = """\
public class Widget {
    private String label;

    @NotBlank
    public String getLabel() {
        return label;
    }
}
"""

_PART_MODEL = """\
public class Part {
    @NotEmpty
    private String code;
}
"""

_GADGET_MODEL = """\
public class Gadget extends Part {
    private int weight;
}
"""

_SOLO_MODEL = """\
public class Solo extends MissingBase {
    @NotNull
    private String sku;
}
"""

_WIDGET_CONTROLLER = """\
import jakarta.validation.Valid;

public class WidgetController {
    public String create(@Valid Widget widget) {
        return "ok";
    }
}
"""

_WIDGET_SERVICE = """\
public class WidgetService {
    public void save(Widget widget) {
        validator.validate(widget);
    }
}
"""

_WIDGET_ENTITY = """\
import jakarta.persistence.Entity;
import jakarta.persistence.Table;

@Entity
@Table(name = "widgets")
public class Widget {
    @ManyToOne
    @JoinColumn(name = "part_id")
    private Part part;
}
"""

_WIDGET_SCHEMA = """\
CREATE TABLE IF NOT EXISTS parts (
  id INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS widgets (
  id INTEGER PRIMARY KEY,
  part_id INTEGER NOT NULL,
  FOREIGN KEY (part_id) REFERENCES parts(id) ON DELETE CASCADE
);
"""

_ALTER_SCHEMA = """\
CREATE TABLE parts (
  id INTEGER PRIMARY KEY
);

CREATE TABLE widgets (
  id INTEGER PRIMARY KEY,
  part_id INTEGER NOT NULL
);
ALTER TABLE widgets ADD CONSTRAINT fk_widgets_parts FOREIGN KEY (part_id) REFERENCES parts (id) ON DELETE SET NULL ON UPDATE CASCADE;
"""

_AMBIGUOUS_SCHEMA = """\
CREATE TABLE widget (
  id INTEGER PRIMARY KEY
);
CREATE TABLE WIDGET (
  id INTEGER PRIMARY KEY
);
"""

_NO_ACTION_SCHEMA = """\
CREATE TABLE IF NOT EXISTS parts (
  id INTEGER PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS widgets (
  id INTEGER PRIMARY KEY,
  part_id INTEGER,
  owner_id INTEGER,
  FOREIGN KEY (part_id) REFERENCES parts(id),
  FOREIGN KEY (owner_id) REFERENCES parts(id) ON DELETE RESTRICT
);
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- declared constraints


class TestDeclaredConstraints:
    def test_notnull_and_size_with_spans(self, tmp_path):
        path = _write(tmp_path, "Widget.java", _WIDGET_MODEL)
        records = extract_field_constraints(path)
        assert len(records) == 2
        by_ann = {(r["annotation"], r["field"]): r for r in records}
        assert ("NotNull", "name") in by_ann
        assert ("Size", "name") in by_ann
        notnull = by_ann[("NotNull", "name")]
        assert notnull["kind"] == "field_constraint"
        assert notnull["class"] == "Widget"
        assert notnull["params"] == {}
        assert notnull["inherited"] is False
        assert notnull["line"] == 10
        assert notnull["span"] == {"start_line": 8, "end_line": 10}
        assert notnull["origin"] == "deterministic"
        assert notnull["content_hash"] == _hash(path)
        size = by_ann[("Size", "name")]
        assert size["params"] == {"min": "1", "max": "50"}
        assert size["line"] == 10
        assert size["span"] == {"start_line": 9, "end_line": 10}

    def test_unannotated_field_yields_no_constraint(self, tmp_path):
        path = _write(tmp_path, "Widget.java", _WIDGET_MODEL)
        records = extract_field_constraints(path)
        assert [r for r in records if r["field"] == "count"] == []

    def test_getter_constraint_uses_property_name(self, tmp_path):
        path = _write(tmp_path, "Widget.java", _WIDGET_GETTER)
        records = extract_field_constraints(path)
        assert len(records) == 1
        record = records[0]
        assert record["annotation"] == "NotBlank"
        assert record["field"] == "label"
        assert record["span"] == {"start_line": 4, "end_line": 5}


# ---------------------------------------------------------------- inheritance


class TestInheritance:
    def test_present_superclass_merges_with_parent_span(self, tmp_path):
        part = _write(tmp_path, "Part.java", _PART_MODEL)
        gadget = _write(tmp_path, "Gadget.java", _GADGET_MODEL)
        resolved = resolve_inheritance([gadget, part])
        inherited = [r for r in resolved if r.get("inherited") is True]
        assert len(inherited) == 1
        record = inherited[0]
        assert record["kind"] == "field_constraint"
        assert record["class"] == "Gadget"
        assert record["field"] == "code"
        assert record["annotation"] == "NotEmpty"
        parent_own = extract_field_constraints(part)
        assert len(parent_own) == 1
        assert record["span"] == parent_own[0]["span"]
        assert record["file"] == str(part)
        gaps = [r for r in resolved if r.get("kind") == "inheritance_gap"]
        assert gaps == []

    def test_missing_superclass_yields_gap_without_fabrication(
        self, tmp_path
    ):
        solo = _write(tmp_path, "Solo.java", _SOLO_MODEL)
        resolved = resolve_inheritance([solo])
        gaps = [r for r in resolved if r.get("kind") == "inheritance_gap"]
        assert len(gaps) == 1
        gap = gaps[0]
        assert gap["class"] == "Solo"
        assert gap["superclass"] == "MissingBase"
        assert gap["resolved"] is False
        assert gap["file"] == str(solo)
        inherited = [r for r in resolved if r.get("inherited") is True]
        assert inherited == []
        own = extract_field_constraints(solo)
        assert [(r["annotation"], r["field"]) for r in own] == [
            ("NotNull", "sku")
        ]


# ---------------------------------------------------------------- enforcement


class TestEnforcement:
    def test_valid_param_verifies(self, tmp_path):
        path = _write(tmp_path, "WidgetController.java", _WIDGET_CONTROLLER)
        result = has_valid_enforcement(path)
        assert result["kind"] == "enforcement"
        assert result["verified"] is True
        assert result["evidence_spans"] != []
        assert "400" not in result["reason"]

    def test_validator_call_verifies(self, tmp_path):
        path = _write(tmp_path, "WidgetService.java", _WIDGET_SERVICE)
        result = has_valid_enforcement(path)
        assert result["verified"] is True
        assert result["evidence_spans"] != []

    def test_declared_only_does_not_verify(self, tmp_path):
        path = _write(tmp_path, "Widget.java", _WIDGET_MODEL)
        result = has_valid_enforcement(path)
        assert result["verified"] is False
        assert result["evidence_spans"] == []
        assert "400" not in result["reason"]


# ---------------------------------------------------------------- SQL DDL


class TestSqlDdl:
    def test_if_not_exists_and_inline_fk(self, tmp_path):
        path = _write(tmp_path, "schema.sql", _WIDGET_SCHEMA)
        records = extract_sql_persistence(path)
        tables = [r for r in records if r["kind"] == "create_table"]
        assert {t["table"] for t in tables} == {"parts", "widgets"}
        widgets = next(t for t in tables if t["table"] == "widgets")
        assert widgets["if_not_exists"] is True
        assert widgets["span"] == {"start_line": 5, "end_line": 9}
        assert "FOREIGN" not in " ".join(
            c["name"] for c in widgets["columns"]
        )
        fks = [r for r in records if r["kind"] == "fk_constraint"]
        assert len(fks) == 1
        fk = fks[0]
        assert fk["table"] == "widgets"
        assert fk["columns"] == ["part_id"]
        assert fk["ref_table"] == "parts"
        assert fk["ref_columns"] == ["id"]
        assert fk["on_delete"] == "CASCADE"
        assert fk["on_update"] is None
        assert fk["line"] == 8
        assert fk["span"] == {"start_line": 8, "end_line": 8}

    def test_alter_table_fk_with_actions(self, tmp_path):
        path = _write(tmp_path, "schema.sql", _ALTER_SCHEMA)
        records = extract_sql_persistence(path)
        fks = [r for r in records if r["kind"] == "fk_constraint"]
        assert len(fks) == 1
        fk = fks[0]
        assert fk["table"] == "widgets"
        assert fk["columns"] == ["part_id"]
        assert fk["ref_table"] == "parts"
        assert fk["on_delete"] == "SET NULL"
        assert fk["on_update"] == "CASCADE"

    def test_non_cascade_actions_recorded_not_cascade(self, tmp_path):
        path = _write(tmp_path, "schema.sql", _NO_ACTION_SCHEMA)
        records = extract_sql_persistence(path)
        fks = [r for r in records if r["kind"] == "fk_constraint"]
        assert len(fks) == 2
        by_col = {tuple(f["columns"]): f for f in fks}
        assert by_col[("part_id",)]["on_delete"] is None
        assert by_col[("owner_id",)]["on_delete"] == "RESTRICT"
        for fk in fks:
            assert fk["on_delete"] != "CASCADE"


# ---------------------------------------------------------------- JPA links


class TestJpaLinks:
    def _linked(self, tmp_path, entity_text, schema_text):
        entity = _write(tmp_path, "Widget.java", entity_text)
        schema = _write(tmp_path, "schema.sql", schema_text)
        entities = extract_jpa_entities(entity)
        assert len(entities) == 1
        sql_records = extract_sql_persistence(schema)
        return entities, link_jpa_to_sql(entities, sql_records)

    def test_verified_link(self, tmp_path):
        entities, links = self._linked(
            tmp_path, _WIDGET_ENTITY, _WIDGET_SCHEMA
        )
        assert entities[0]["table"] == "widgets"
        assert len(links) == 1
        link = links[0]
        assert link["kind"] == "jpa_sql_link"
        assert link["entity"] == "Widget"
        assert link["table"] == "widgets"
        assert link["column"] == "part_id"
        assert link["verified"] is True
        assert link["fk"] is not None
        assert link["spans"]["entity"] is not None
        assert link["spans"]["table"] is not None
        assert link["spans"]["fk"] is not None

    def test_ambiguous_tables_abstain(self, tmp_path):
        entity_text = _WIDGET_ENTITY.replace('"widgets"', '"widget"')
        _, links = self._linked(
            tmp_path, entity_text, _AMBIGUOUS_SCHEMA
        )
        assert len(links) == 1
        assert links[0]["verified"] is False
        assert "ambigu" in links[0]["reason"].lower()

    def test_unmatched_table_abstains(self, tmp_path):
        entity_text = _WIDGET_ENTITY.replace('"widgets"', '"gadgets"')
        _, links = self._linked(tmp_path, entity_text, _WIDGET_SCHEMA)
        assert len(links) == 1
        assert links[0]["verified"] is False

    def test_unknown_ref_table_is_partial(self, tmp_path):
        schema = _WIDGET_SCHEMA.replace("parts(id)", "ghosts(id)")
        _, links = self._linked(tmp_path, _WIDGET_ENTITY, schema)
        assert len(links) == 1
        assert links[0]["verified"] is False

    def test_entity_without_join_columns_never_verifies(self, tmp_path):
        entity_text = """\
@Entity
@Table(name = "widgets")
public class Widget {
    private String name;
}
"""
        _, links = self._linked(tmp_path, entity_text, _WIDGET_SCHEMA)
        assert len(links) == 1
        assert links[0]["verified"] is False


# ---------------------------------------------------------------- preserved-source regression


def _petclinic_root() -> Path | None:
    override = os.environ.get("PETCLINIC_ROOT")
    if override:
        candidate = Path(override)
        if candidate.is_dir():
            return candidate
    fallback = Path("/tmp/opencode/oss-eval/spring-petclinic-rest")
    return fallback if fallback.is_dir() else None


class TestPreservedSourceRegression:
    """Revealed-source checks: discovery is shape-based throughout.

    No literal model/schema names, paths, or line numbers: entity files
    are those containing ``@Entity``, the superclass file is the one
    carrying ``@NotEmpty``, schemas are split by ``IF NOT EXISTS`` vs
    ``ADD CONSTRAINT`` content markers, and the link target is the
    entity declaring the most join columns.
    """

    def test_declared_and_inherited_constraints(self):
        root = _petclinic_root()
        if root is None:
            pytest.skip("preserved PetClinic tree absent (read-only)")
        java_files = list(root.glob("src/main/java/**/*.java"))
        assert java_files, "setup must find preserved sources"
        declared: list[dict] = []
        for java in java_files:
            declared.extend(extract_field_constraints(java))
        annotations = {r["annotation"] for r in declared}
        assert "NotNull" in annotations
        assert "NotEmpty" in annotations
        for record in declared:
            span = record["span"]
            assert span["start_line"] <= record["line"] <= span["end_line"]
        resolved = resolve_inheritance(java_files)
        inherited = [
            r
            for r in resolved
            if r.get("kind") == "field_constraint" and r.get("inherited")
        ]
        assert inherited, "setup must show an inherited declared rule"
        declared_classes = set()
        for java in java_files:
            text = java.read_text(encoding="utf-8")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("class ") or " class " in line:
                    token = stripped.split("class ", 1)[1].split()[0]
                    declared_classes.add(token.strip("{"))
        for record in inherited:
            parent_file = Path(record["file"])
            assert parent_file.is_file()
            assert record["span"]["start_line"] >= 1
        for gap in resolved:
            if gap.get("kind") != "inheritance_gap":
                continue
            assert gap["superclass"] not in declared_classes
            assert gap["resolved"] is False

    def test_model_constraints_are_declared_only(self):
        root = _petclinic_root()
        if root is None:
            pytest.skip("preserved PetClinic tree absent (read-only)")
        java_files = list(root.glob("src/main/java/**/*.java"))
        entity_files = [
            j
            for j in java_files
            if any(
                line.strip().startswith("@Entity")
                for line in j.read_text(encoding="utf-8").splitlines()
            )
        ]
        assert entity_files, "setup must find preserved entities"
        for entity in entity_files:
            if not extract_field_constraints(entity):
                continue
            result = has_valid_enforcement(entity)
            assert result["verified"] is False
            assert "400" not in result["reason"]

    def test_sql_dialects_and_jpa_link(self):
        root = _petclinic_root()
        if root is None:
            pytest.skip("preserved PetClinic tree absent (read-only)")
        schemas = list(root.glob("src/main/resources/db/*/schema.sql"))
        assert schemas, "setup must find preserved schemas"
        inline_files = [
            s
            for s in schemas
            if "IF NOT EXISTS" in s.read_text(encoding="utf-8").upper()
        ]
        alter_files = [
            s
            for s in schemas
            if "ADD CONSTRAINT" in s.read_text(encoding="utf-8").upper()
            and "ON DELETE" not in s.read_text(encoding="utf-8").upper()
        ]
        assert inline_files, "setup must find an inline-FK dialect schema"
        assert alter_files, "setup must find an ALTER-FK dialect schema"
        inline_by_file: dict[str, list[dict]] = {}
        for schema in inline_files:
            inline_by_file[str(schema)] = extract_sql_persistence(schema)
        inline_records: list[dict] = []
        for records in inline_by_file.values():
            inline_records.extend(records)
        assert any(
            r.get("kind") == "create_table" and r.get("if_not_exists")
            for r in inline_records
        )
        cascades = [
            r
            for r in inline_records
            if r.get("kind") == "fk_constraint"
            and r.get("on_delete") == "CASCADE"
        ]
        assert cascades, "setup must show revealed CASCADE FKs"
        for fk in cascades:
            assert fk["span"]["start_line"] == fk["line"]
        alter_records: list[dict] = []
        for schema in alter_files:
            alter_records.extend(extract_sql_persistence(schema))
        alter_fks = [
            r for r in alter_records if r.get("kind") == "fk_constraint"
        ]
        assert alter_fks, "setup must show revealed ALTER FKs"
        for fk in alter_fks:
            assert fk["on_delete"] is None
            assert fk["on_delete"] != "CASCADE"
        java_files = list(root.glob("src/main/java/**/*.java"))
        best: dict | None = None
        for java in java_files:
            for entity in extract_jpa_entities(java):
                if best is None or len(entity["join_columns"]) > len(
                    best["join_columns"]
                ):
                    best = entity
        assert best is not None and best["join_columns"], (
            "setup must find an entity declaring join columns"
        )
        per_file = [
            link_jpa_to_sql([best], records)
            for records in inline_by_file.values()
        ]
        per_file = [links for links in per_file if links]
        assert per_file, "setup must yield JPA links for the join target"
        assert any(
            links and all(link["verified"] for link in links)
            for links in per_file
        ), "setup must verify the join target where evidence supports"
        for links in per_file:
            for link in links:
                if link["verified"]:
                    assert link["fk"] is not None
                    assert link["spans"]["fk"] is not None


# ---------------------------------------------------------------- review fixes (MED + LOW)


class TestTransitiveInheritance:
    def test_three_level_parent_points_to_immediate(self, tmp_path):
        base = _write(tmp_path, "Base.java", "public class Base {\n    @NotNull\n    private String sku;\n}\n")
        mid = _write(tmp_path, "Mid.java", "public class Mid extends Base {\n}\n")
        leaf = _write(tmp_path, "Leaf.java", "public class Leaf extends Mid {\n}\n")
        resolved = resolve_inheritance([leaf, mid, base])
        leaf_rules = [r for r in resolved if r.get("class") == "Leaf"]
        assert leaf_rules, "leaf must inherit base constraint transitively"
        for record in leaf_rules:
            assert record["parent_class"] == "Mid"
            assert record["file"] == str(base)
        base_own = extract_field_constraints(base)
        assert leaf_rules[0]["span"] == base_own[0]["span"]


class TestValidReturnPosition:
    def test_return_position_alone_does_not_verify(self, tmp_path):
        text = (
            "import jakarta.validation.Valid;\n"
            "public class WidgetController {\n"
            "    public @Valid String create(Widget widget) {\n"
            '        return "ok";\n'
            "    }\n"
            "}\n"
        )
        path = _write(tmp_path, "WidgetController.java", text)
        result = has_valid_enforcement(path)
        assert result["verified"] is False

    def test_validated_inside_params_verifies(self, tmp_path):
        text = (
            "public class WidgetController {\n"
            "    public String create(@Validated Widget widget) {\n"
            '        return "ok";\n'
            "    }\n"
            "}\n"
        )
        path = _write(tmp_path, "WidgetController.java", text)
        result = has_valid_enforcement(path)
        assert result["verified"] is True


class TestClosingLineFk:
    def test_fk_on_closing_line_recorded(self, tmp_path):
        schema = (
            "CREATE TABLE parts (\n"
            "  id INTEGER PRIMARY KEY\n"
            ");\n"
            "CREATE TABLE widgets (\n"
            "  id INTEGER PRIMARY KEY,\n"
            "  part_id INTEGER,\n"
            "  FOREIGN KEY (part_id) REFERENCES parts(id) ON DELETE CASCADE);\n"
        )
        path = _write(tmp_path, "schema.sql", schema)
        records = extract_sql_persistence(path)
        fks = [r for r in records if r["kind"] == "fk_constraint"]
        assert len(fks) == 1
        assert fks[0]["columns"] == ["part_id"]
        assert fks[0]["on_delete"] == "CASCADE"


class TestNormalizedTableMatch:
    def test_underscore_insensitive_match_verifies(self, tmp_path):
        entity_text = _WIDGET_ENTITY.replace('"widgets"', '"widget_items"')
        schema = (
            "CREATE TABLE parts (\n"
            "  id INTEGER PRIMARY KEY\n"
            ");\n"
            "CREATE TABLE widgetitems (\n"
            "  id INTEGER PRIMARY KEY,\n"
            "  part_id INTEGER NOT NULL,\n"
            "  FOREIGN KEY (part_id) REFERENCES parts(id)\n"
            ");\n"
        )
        entity = _write(tmp_path, "Widget.java", entity_text)
        schema_path = _write(tmp_path, "schema.sql", schema)
        entities = extract_jpa_entities(entity)
        links = link_jpa_to_sql(entities, extract_sql_persistence(schema_path))
        assert len(links) == 1
        assert links[0]["verified"] is True


class TestSingleLineCreate:
    def test_single_line_create_table_detected(self, tmp_path):
        path = _write(
            tmp_path,
            "schema.sql",
            "CREATE TABLE parts (id INTEGER PRIMARY KEY);\n",
        )
        records = extract_sql_persistence(path)
        tables = [r for r in records if r["kind"] == "create_table"]
        assert [t["table"] for t in tables] == ["parts"]


class TestInlineConstraintName:
    def test_inline_constraint_name_captured(self, tmp_path):
        schema = (
            "CREATE TABLE parts (\n"
            "  id INTEGER PRIMARY KEY\n"
            ");\n"
            "CREATE TABLE widgets (\n"
            "  id INTEGER PRIMARY KEY,\n"
            "  part_id INTEGER,\n"
            "  CONSTRAINT fk_widgets_parts FOREIGN KEY (part_id) REFERENCES parts(id)\n"
            ");\n"
        )
        path = _write(tmp_path, "schema.sql", schema)
        fks = [
            r
            for r in extract_sql_persistence(path)
            if r["kind"] == "fk_constraint"
        ]
        assert len(fks) == 1
        assert fks[0]["name"] == "fk_widgets_parts"


class TestColumnTypeSplit:
    def test_data_type_does_not_absorb_constraints(self, tmp_path):
        schema = (
            "CREATE TABLE widgets (\n"
            "  id INTEGER PRIMARY KEY,\n"
            "  part_id INTEGER NOT NULL,\n"
            "  label VARCHAR(50) DEFAULT 'x'\n"
            ");\n"
        )
        path = _write(tmp_path, "schema.sql", schema)
        tables = [
            r
            for r in extract_sql_persistence(path)
            if r["kind"] == "create_table"
        ]
        widgets = next(t for t in tables if t["table"] == "widgets")
        by_name = {c["name"]: c for c in widgets["columns"]}
        assert by_name["part_id"]["data_type"] == "INTEGER"
        assert "NOT NULL" in by_name["part_id"]["constraints"]
        assert by_name["label"]["data_type"] == "VARCHAR(50)"
        assert "DEFAULT" in by_name["label"]["constraints"]


class TestMultiLineAlter:
    def test_alter_joined_across_lines(self, tmp_path):
        schema = (
            "CREATE TABLE parts (\n"
            "  id INTEGER PRIMARY KEY\n"
            ");\n"
            "CREATE TABLE widgets (\n"
            "  id INTEGER PRIMARY KEY,\n"
            "  part_id INTEGER\n"
            ");\n"
            "ALTER TABLE widgets ADD CONSTRAINT fk_x FOREIGN KEY (part_id)\n"
            "  REFERENCES parts (id) ON DELETE CASCADE;\n"
        )
        path = _write(tmp_path, "schema.sql", schema)
        fks = [
            r
            for r in extract_sql_persistence(path)
            if r["kind"] == "fk_constraint"
        ]
        assert len(fks) == 1
        assert fks[0]["columns"] == ["part_id"]
        assert fks[0]["on_delete"] == "CASCADE"
        assert fks[0]["span"]["end_line"] > fks[0]["span"]["start_line"]


class TestPackagePrivateField:
    def test_package_private_field_detected(self, tmp_path):
        text = "public class Widget {\n    @NotNull\n    String name;\n}\n"
        path = _write(tmp_path, "Widget.java", text)
        records = extract_field_constraints(path)
        assert [(r["annotation"], r["field"]) for r in records] == [
            ("NotNull", "name")
        ]


class TestGetterReturnType:
    def test_generic_qualified_getter_detected(self, tmp_path):
        text = (
            "public class Widget {\n"
            "    private java.util.Map<String, String> mapping;\n"
            "    @NotNull\n"
            "    public java.util.Map<String, String> getMapping() {\n"
            "        return mapping;\n"
            "    }\n"
            "}\n"
        )
        path = _write(tmp_path, "Widget.java", text)
        records = extract_field_constraints(path)
        assert len(records) == 1
        assert records[0]["field"] == "mapping"


class TestWrappedSignatureAbstains:
    def test_multiline_declaration_abstains(self, tmp_path):
        # Wrapped (multi-line) signatures are single-line only by design:
        # no half-parse, no enforcement claimed.
        text = (
            "public class WidgetController {\n"
            "    public String create(\n"
            "        @Valid Widget widget) {\n"
            '        return "ok";\n'
            "    }\n"
            "}\n"
        )
        path = _write(tmp_path, "WidgetController.java", text)
        result = has_valid_enforcement(path)
        assert result["verified"] is False
        assert result["methods"] == []


# ---------------------------------------------------------------- extended Bean Validation set


_EXTENDED_MODEL = """\
package example;

public class WidgetShipment {
    @Email
    private String email;

    @Positive
    private Integer count;

    @PositiveOrZero
    private Integer stock;

    @Negative
    private Integer deficit;

    @NegativeOrZero
    private Integer balance;

    @Past
    private java.util.Date created;

    @PastOrPresent
    private java.util.Date updated;

    @Future
    private java.util.Date expires;

    @FutureOrPresent
    private java.util.Date validFrom;

    @DecimalMin("0.0")
    private String minPrice;

    @DecimalMax(value = "100.0")
    private String maxPrice;
}
"""


class TestExtendedConstraintAnnotations:
    def test_all_new_annotations_found_with_spans(self, tmp_path):
        path = _write(tmp_path, "WidgetShipment.java", _EXTENDED_MODEL)
        records = extract_field_constraints(path)
        by_ann = {(r["annotation"], r["field"]): r for r in records}
        for annotation, field in [
            ("Email", "email"),
            ("Positive", "count"),
            ("PositiveOrZero", "stock"),
            ("Negative", "deficit"),
            ("NegativeOrZero", "balance"),
            ("Past", "created"),
            ("PastOrPresent", "updated"),
            ("Future", "expires"),
            ("FutureOrPresent", "validFrom"),
            ("DecimalMin", "minPrice"),
            ("DecimalMax", "maxPrice"),
        ]:
            assert (annotation, field) in by_ann, (annotation, field, records)
        for record in records:
            span = record["span"]
            assert span["start_line"] <= record["line"] <= span["end_line"]
            assert isinstance(record["params"], dict)

    def test_decimal_params_preserved(self, tmp_path):
        path = _write(tmp_path, "WidgetShipment.java", _EXTENDED_MODEL)
        records = extract_field_constraints(path)
        by_ann = {(r["annotation"], r["field"]): r for r in records}
        assert by_ann[("DecimalMin", "minPrice")]["params"] == {"value": "0.0"}
        assert by_ann[("DecimalMax", "maxPrice")]["params"] == {"value": "100.0"}

    def test_getter_with_new_annotation_uses_property_name(self, tmp_path):
        text = (
            "public class WidgetContact {\n"
            "    private String email;\n"
            "    @Email\n"
            "    public String getEmail() {\n"
            "        return email;\n"
            "    }\n"
            "}\n"
        )
        path = _write(tmp_path, "WidgetContact.java", text)
        records = extract_field_constraints(path)
        assert len(records) == 1
        assert records[0]["annotation"] == "Email"
        assert records[0]["field"] == "email"

    def test_non_validation_annotation_still_none(self, tmp_path):
        text = (
            "public class WidgetPart {\n"
            "    @Deprecated\n"
            "    private String code;\n"
            "}\n"
        )
        path = _write(tmp_path, "WidgetPart.java", text)
        assert extract_field_constraints(path) == []

    def test_extended_model_declared_only_without_valid(self, tmp_path):
        path = _write(tmp_path, "WidgetShipment.java", _EXTENDED_MODEL)
        assert extract_field_constraints(path), "setup must declare constraints"
        result = has_valid_enforcement(path)
        assert result["verified"] is False
        assert "400" not in result["reason"]


def _realworld_root() -> Path | None:
    override = os.environ.get("REALWORLD_ROOT")
    if override:
        candidate = Path(override)
        if candidate.is_dir():
            return candidate
    for base in (
        "/tmp/opencode/oss-eval-realworld/spring-boot-realworld-example-app",
        "/tmp/realworld",
        "/tmp/spring-boot-realworld-example-app",
    ):
        fallback = Path(base)
        if fallback.is_dir():
            return fallback
    return None


class TestRealWorldExposedRegression:
    def test_register_param_email_found(self):
        """REGRESSION (read-only): exposed tree @Email constraint is found
        (shape-based discovery: all ``*.java`` containing ``@Email``,
        never a literal filename)."""
        root = _realworld_root()
        if root is None:
            pytest.skip("RealWorld tree absent (read-only)")
        candidates = [
            jf
            for jf in root.glob("**/*.java")
            if jf.is_file()
            and "@Email" in jf.read_text(encoding="utf-8", errors="ignore")
        ]
        if not candidates:
            pytest.skip("Exposed @Email sources absent (no download attempted)")
        found = [r for jf in candidates for r in extract_field_constraints(jf)]
        emails = [r for r in found if r.get("annotation") == "Email"]
        assert emails, found
        for record in emails:
            span = record["span"]
            assert span["start_line"] <= record["line"] <= span["end_line"]


class TestValidInStringAbstains:
    def test_requestbody_string_valid_does_not_verify(self, tmp_path):
        text = (
            "public class WidgetController {\n"
            '    public String create(@RequestBody(" @Valid ") Widget widget) {\n'
            '        return "ok";\n'
            "    }\n"
            "}\n"
        )
        path = _write(tmp_path, "WidgetController.java", text)
        result = has_valid_enforcement(path)
        assert result["verified"] is False
        assert result["methods"] == []


class TestGetterDecapUrl:
    def test_get_url_keeps_uppercase_suffix(self, tmp_path):
        text = (
            "public class Widget {\n"
            "    private String uRL;\n"
            "    @NotNull\n"
            "    public String getURL() {\n"
            "        return uRL;\n"
            "    }\n"
            "}\n"
        )
        path = _write(tmp_path, "Widget.java", text)
        records = extract_field_constraints(path)
        assert len(records) == 1, records
        assert records[0]["field"] == "URL", records


class TestDuplicateSimpleNameAbstains:
    def test_same_name_different_files_yields_ambiguous_gap(self, tmp_path):
        pkg_a = tmp_path / "a"
        pkg_b = tmp_path / "b"
        pkg_a.mkdir()
        pkg_b.mkdir()
        base_a = pkg_a / "Widget.java"
        base_a.write_text(
            "package a;\npublic class Widget {\n    @NotNull\n    private String sku;\n}\n",
            encoding="utf-8",
        )
        base_b = pkg_b / "Widget.java"
        base_b.write_text(
            "package b;\npublic class Widget {\n    @NotNull\n    private String other;\n}\n",
            encoding="utf-8",
        )
        child = _write(
            tmp_path,
            "Gadget.java",
            "public class Gadget extends Widget {\n}\n",
        )
        resolved = resolve_inheritance([child, base_a, base_b])
        gaps = [r for r in resolved if r.get("kind") == "inheritance_gap"]
        assert gaps, resolved
        assert all(g["resolved"] is False for g in gaps), gaps
        assert any("ambiguous" in str(g.get("reason", "")).lower() for g in gaps), gaps
        inherited = [r for r in resolved if r.get("inherited") is True]
        assert inherited == [], resolved
