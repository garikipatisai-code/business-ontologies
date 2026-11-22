"""Java resolution adapter over frozen fixture repos.

Single pinned backend: javaparser-core 3.17.0 (fetched from Maven Central;
the jar is looked up at runtime in the default Maven repository). A small
Java dump tool, authored here and embedded below, is compiled against the
fetched jar and run as a subprocess over explicit .java file paths. It emits
JSON with types, methods (file + line spans), calls (resolved targets or
explicit unresolved entries carrying reasons), and per-file content hashes.

Type and method resolution is whole-program across the indexed repos, so a
call such as ``refundService.requestRefund(request)`` resolves against the
field's declared type in another file. Dynamic probes such as
``getMethod(...)``/``invoke`` are reported in the unresolved list with a
reason; they are never fabricated as resolved callees. The Python side maps
dump-tool output to adapter records keyed by ``<Type>.<method>`` with spans
and a content hash recorded per file.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

ORIGIN = "deterministic"
EXTRACTOR_VERSION = "bo_java.java_adapter/1.0.0 (javaparser-core 3.17.0)"

_DUMP_TOOL_JAVA = r"""
import com.github.javaparser.JavaParser;
import com.github.javaparser.Position;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.NodeList;
import com.github.javaparser.ast.body.*;
import com.github.javaparser.ast.expr.*;
import com.github.javaparser.ast.stmt.*;
import com.github.javaparser.ast.type.*;
import com.github.javaparser.ast.visitor.VoidVisitorAdapter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

class DumpTool {

    private static final JavaParser PARSER = new JavaParser();

    /* ---------------- JSON helpers ---------------- */

    static String escape(String s) {
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': out.append("\\\""); break;
                case '\\': out.append("\\\\"); break;
                case '\b': out.append("\\b"); break;
                case '\f': out.append("\\f"); break;
                case '\n': out.append("\\n"); break;
                case '\r': out.append("\\r"); break;
                case '\t': out.append("\\t"); break;
                default:
                    if (c < 0x20) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
            }
        }
        return out.toString();
    }

    static String sha256(Path file) {
        try {
            byte[] bytes = Files.readAllBytes(file);
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            byte[] digest = md.digest(bytes);
            StringBuilder hex = new StringBuilder(64);
            for (byte b : digest) {
                hex.append(Character.forDigit((b >> 4) & 0xF, 16));
                hex.append(Character.forDigit(b & 0xF, 16));
            }
            return hex.toString();
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    static String valueToJson(Object v) {
        if (v == null) return "null";
        if (v instanceof Number || v instanceof Boolean) return String.valueOf(v);
        if (v instanceof Map) return obj((Map<String, Object>) v);
        if (v instanceof List) return listToJson((List<?>) v);
        return "\"" + escape(String.valueOf(v)) + "\"";
    }

    static String obj(Map<String, Object> map) {
        StringBuilder out = new StringBuilder("{");
        boolean first = true;
        for (Map.Entry<String, Object> e : map.entrySet()) {
            if (!first) out.append(",");
            first = false;
            out.append("\"").append(escape(e.getKey())).append("\":");
            out.append(valueToJson(e.getValue()));
        }
        out.append("}");
        return out.toString();
    }

    static String listToJson(List<?> list) {
        StringBuilder out = new StringBuilder("[");
        for (int i = 0; i < list.size(); i++) {
            if (i > 0) out.append(",");
            out.append(valueToJson(list.get(i)));
        }
        out.append("]");
        return out.toString();
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> kv(Object... pairs) {
        Map<String, Object> map = new LinkedHashMap<>();
        for (int i = 0; i + 1 < pairs.length; i += 2) {
            map.put(String.valueOf(pairs[i]), pairs[i + 1]);
        }
        return map;
    }

    static int intLine(Optional<Position> pos) {
        return pos.map(p -> p.line).orElse(-1);
    }

    /* ---------------- model ---------------- */

    static class FieldInfo {
        String name;
        String type;
        int line;
    }

    static class MethodInfo {
        String file;
        String declaringType;
        String name;
        List<String> paramNames = new ArrayList<>();
        List<String> paramTypes = new ArrayList<>();
        int startLine;
        int endLine;
    }

    static class TypeInfo {
        String file;
        String name;
        String kind;
        int startLine;
        int endLine;
        List<FieldInfo> fields = new ArrayList<>();
        List<MethodInfo> methods = new ArrayList<>();
    }

    static class CallRecord {
        String callerMethod;
        String callerFile;
        int callerLine;
        String calleeName;
        String receiverType;
        boolean resolved;
        String calleeFile;
        int calleeLine;
        String calleeType;
        int argCount;
        String callText;
        String detail;
    }

    static class UnresolvedEntry {
        String file;
        int line;
        String name;
        String target;
        String symbol;
        String reason;
        String callText;
        String contentHash;
        String origin;
    }

    static class FileModel {
        Path file;
        String contentHash;
        String[] lines;
        List<TypeInfo> types = new ArrayList<>();
        List<MethodInfo> methods = new ArrayList<>();
        List<Map<String, Object>> vars = new ArrayList<>();
        List<Map<String, Object>> rawCalls = new ArrayList<>();
        List<CallRecord> calls = new ArrayList<>();
        List<UnresolvedEntry> unresolved = new ArrayList<>();
    }

    /* ---------------- collection ---------------- */

    static void collectTypes(CompilationUnit cu, Path file, String[] lines, List<TypeInfo> sink) {
        NodeList<TypeDeclaration<?>> types = cu.getTypes();
        for (int i = 0; i < types.size(); i++) {
            collectType(types.get(i), file, lines, sink);
        }
    }

    static void collectType(TypeDeclaration<?> td, Path file, String[] lines, List<TypeInfo> sink) {
        TypeInfo info = new TypeInfo();
        info.file = file.toString();
        info.name = td.getName().asString();
        if (td instanceof ClassOrInterfaceDeclaration) {
            info.kind = ((ClassOrInterfaceDeclaration) td).isInterface() ? "interface" : "class";
        } else if (td instanceof EnumDeclaration) {
            info.kind = "enum";
        } else {
            info.kind = "type";
        }
        info.startLine = intLine(td.getBegin());
        info.endLine = intLine(td.getEnd());
        sink.add(info);

        for (int i = 0; i < td.getMembers().size(); i++) {
            BodyDeclaration<?> member = td.getMembers().get(i);
            if (member instanceof FieldDeclaration) {
                FieldDeclaration fd = (FieldDeclaration) member;
                String ftype = fd.getElementType().asString();
                for (int j = 0; j < fd.getVariables().size(); j++) {
                    VariableDeclarator v = fd.getVariables().get(j);
                    FieldInfo f = new FieldInfo();
                    f.name = v.getName().asString();
                    f.type = ftype;
                    f.line = intLine(v.getBegin());
                    info.fields.add(f);
                }
            } else if (member instanceof MethodDeclaration) {
                MethodDeclaration md = (MethodDeclaration) member;
                MethodInfo m = new MethodInfo();
                m.file = file.toString();
                m.declaringType = info.name;
                m.name = md.getName().asString();
                for (int j = 0; j < md.getParameters().size(); j++) {
                    Parameter p = md.getParameters().get(j);
                    m.paramNames.add(p.getName().asString());
                    m.paramTypes.add(p.getType().asString());
                }
                m.startLine = signatureStartLine(lines, intLine(md.getBegin()), intLine(md.getEnd()));
                m.endLine = intLine(md.getEnd());
                info.methods.add(m);
            } else if (member instanceof TypeDeclaration) {
                collectType((TypeDeclaration<?>) member, file, lines, sink);
            }
        }
    }

    static int signatureStartLine(String[] lines, int begin, int end) {
        int line = begin;
        while (line >= 1 && line <= end) {
            String trimmed = lines[line - 1].trim();
            if (trimmed.startsWith("@")) {
                line++;
            } else {
                break;
            }
        }
        return line;
    }

    /* ---------------- calls and variables (raw, unresolved) ---------------- */

    static List<Map<String, Object>> locateCalls(CompilationUnit cu) {
        List<Map<String, Object>> found = new ArrayList<>();
        new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(MethodCallExpr n, Void arg) {
                Map<String, Object> c = new LinkedHashMap<>();
                c.put("line", intLine(n.getBegin()));
                c.put("name", n.getName().asString());
                c.put("argCount", n.getArguments().size());
                c.put("callText", n.toString());
                Expression scope = n.getScope().orElse(null);
                if (scope == null) {
                    c.put("scopeKind", "implicit");
                    c.put("scopeText", null);
                } else if (scope instanceof ThisExpr) {
                    c.put("scopeKind", "this");
                    c.put("scopeText", "this");
                } else if (scope instanceof ClassExpr) {
                    c.put("scopeKind", "class");
                    c.put("scopeText", ((ClassExpr) scope).getType().asString());
                } else if (scope instanceof NameExpr) {
                    c.put("scopeKind", "name");
                    c.put("scopeText", ((NameExpr) scope).getName().asString());
                } else {
                    c.put("scopeKind", "complex");
                    c.put("scopeText", scope.toString());
                }
                super.visit(n, arg);
                found.add(c);
            }
        }.visit(cu, null);
        return found;
    }

    static List<Map<String, Object>> locateVars(CompilationUnit cu) {
        List<Map<String, Object>> vars = new ArrayList<>();
        new VoidVisitorAdapter<Void>() {
            @Override
            public void visit(VariableDeclarationExpr n, Void arg) {
                for (int i = 0; i < n.getVariables().size(); i++) {
                    VariableDeclarator v = n.getVariables().get(i);
                    vars.add(kv("line", intLine(v.getBegin()), "name", v.getName().asString(), "type", v.getType().asString()));
                }
                super.visit(n, arg);
            }
        }.visit(cu, null);
        return vars;
    }

    static FileModel collectFile(Path file) throws Exception {
        String source = new String(Files.readAllBytes(file), StandardCharsets.UTF_8);
        CompilationUnit cu = PARSER.parse(source).getResult()
            .orElseThrow(() -> new IllegalStateException("failed to parse " + file));
        String[] lines = source.split("\n", -1);

        FileModel fm = new FileModel();
        fm.file = file;
        fm.contentHash = sha256(file);
        fm.lines = lines;

        collectTypes(cu, file, lines, fm.types);
        for (int i = 0; i < fm.types.size(); i++) {
            fm.methods.addAll(fm.types.get(i).methods);
        }
        fm.vars = locateVars(cu);
        fm.rawCalls = locateCalls(cu);
        return fm;
    }

    /* ---------------- whole-program resolution ---------------- */

    static MethodInfo enclosingMethod(List<MethodInfo> methods, int line) {
        MethodInfo best = null;
        int bestSpan = Integer.MAX_VALUE;
        for (int i = 0; i < methods.size(); i++) {
            MethodInfo m = methods.get(i);
            if (m.startLine >= 1 && m.endLine >= m.startLine && line >= m.startLine && line <= m.endLine) {
                int span = m.endLine - m.startLine;
                if (span < bestSpan) {
                    best = m;
                    bestSpan = span;
                }
            }
        }
        return best;
    }

    static String variableType(List<Map<String, Object>> vars, MethodInfo enclosing, String name, int line) {
        String found = null;
        for (int i = 0; i < vars.size(); i++) {
            Map<String, Object> v = vars.get(i);
            int vline = ((Number) v.get("line")).intValue();
            String vname = String.valueOf(v.get("name"));
            if (!vname.equals(name)) {
                continue;
            }
            if (enclosing == null) {
                continue;
            }
            if (vline < enclosing.startLine || vline > enclosing.endLine) {
                continue;
            }
            if (vline >= line) {
                continue;
            }
            found = String.valueOf(v.get("type"));
        }
        return found;
    }

    static String paramType(MethodInfo m, String name) {
        if (m == null) {
            return null;
        }
        for (int i = 0; i < m.paramNames.size(); i++) {
            if (m.paramNames.get(i).equals(name)) {
                return m.paramTypes.get(i);
            }
        }
        return null;
    }

    static String fieldType(Map<String, List<TypeInfo>> byName, String declType, String name) {
        if (declType == null) {
            return null;
        }
        List<TypeInfo> cands = byName.get(declType);
        if (cands == null || cands.isEmpty()) {
            return null;
        }
        for (int t = 0; t < cands.size(); t++) {
            for (int i = 0; i < cands.get(t).fields.size(); i++) {
                FieldInfo f = cands.get(t).fields.get(i);
                if (f.name.equals(name)) {
                    return f.type;
                }
            }
        }
        return null;
    }

    static String methodText(String[] lines, MethodInfo m) {
        if (m == null || m.startLine < 1 || m.endLine < m.startLine) {
            return "";
        }
        int start = m.startLine - 1;
        int end = Math.min(m.endLine, lines.length);
        StringBuilder b = new StringBuilder();
        for (int i = start; i < end; i++) {
            if (i > start) b.append("\n");
            b.append(lines[i]);
        }
        return b.toString();
    }

    static void addUnresolved(FileModel fm, int line, String name, String receiverType, String callText,
                            String reason) {
        UnresolvedEntry u = new UnresolvedEntry();
        u.file = fm.file.toString();
        u.line = line;
        u.name = name;
        u.target = receiverType == null ? "unbound receiver" : ("receiver type '" + receiverType + "'");
        u.symbol = (receiverType == null ? "" : receiverType + ".") + name;
        u.reason = reason;
        u.callText = callText;
        u.contentHash = fm.contentHash;
        u.origin = "deterministic";
        fm.unresolved.add(u);
    }

    static void resolveCalls(List<FileModel> models) {
        Map<String, List<TypeInfo>> byName = new LinkedHashMap<>();
        for (int i = 0; i < models.size(); i++) {
            for (int j = 0; j < models.get(i).types.size(); j++) {
                TypeInfo t = models.get(i).types.get(j);
                byName.computeIfAbsent(t.name, k -> new ArrayList<>()).add(t);
            }
        }

        for (int i = 0; i < models.size(); i++) {
            FileModel fm = models.get(i);
            String[] lines = fm.lines;
            List<MethodInfo> allMethods = fm.methods;

            for (int j = 0; j < fm.rawCalls.size(); j++) {
                Map<String, Object> rc = fm.rawCalls.get(j);
                int line = ((Number) rc.get("line")).intValue();
                String name = String.valueOf(rc.get("name"));
                int argCount = ((Number) rc.get("argCount")).intValue();
                String scopeKind = String.valueOf(rc.get("scopeKind"));
                String scopeText = String.valueOf(rc.get("scopeText"));
                String callText = String.valueOf(rc.get("callText"));

                MethodInfo enclosing = enclosingMethod(allMethods, line);
                String declType = enclosing == null ? null : enclosing.declaringType;

                String receiverType;
                String receiverDetail;
                if ("implicit".equals(scopeKind) || "this".equals(scopeKind)) {
                    receiverType = declType;
                    receiverDetail = "implicit receiver (this)";
                } else if ("class".equals(scopeKind)) {
                    receiverType = scopeText;
                    receiverDetail = "class receiver " + scopeText;
                } else if ("name".equals(scopeKind)) {
                    receiverType = variableType(fm.vars, enclosing, scopeText, line);
                    receiverDetail = receiverType == null ? null : ("local variable '" + scopeText + "'");
                    if (receiverType == null) {
                        receiverType = paramType(enclosing, scopeText);
                        receiverDetail = "parameter '" + scopeText + "'";
                    }
                    if (receiverType == null) {
                        receiverType = fieldType(byName, declType, scopeText);
                        receiverDetail = "field '" + scopeText + "' of '" + declType + "'";
                    }
                } else {
                    receiverType = null;
                    receiverDetail = "complex receiver expression: " + scopeText;
                }

                CallRecord call = new CallRecord();
                call.callerMethod = declType == null ? null : (declType + "." + enclosing.name);
                call.callerFile = fm.file.toString();
                call.callerLine = line;
                call.calleeName = name;
                call.receiverType = receiverType;
                call.argCount = argCount;
                call.callText = callText;

                if (receiverType == null) {
                    call.resolved = false;
                    call.detail = "unresolved receiver: " + receiverDetail;
                    fm.calls.add(call);
                    addUnresolved(fm, line, name, receiverType, callText,
                        "unresolved call: cannot bind receiver (" + receiverDetail + ") to a project type");
                    continue;
                }

                List<TypeInfo> cands = byName.get(receiverType);
                MethodInfo target = null;
                String detail = null;
                if (cands == null || cands.isEmpty()) {
                    detail = "type '" + receiverType + "' is not declared in the indexed project sources";
                } else {
                    List<MethodInfo> matches = new ArrayList<>();
                    for (int t = 0; t < cands.size(); t++) {
                        for (int m = 0; m < cands.get(t).methods.size(); m++) {
                            MethodInfo cand = cands.get(t).methods.get(m);
                            if (cand.name.equals(name) && cand.paramTypes.size() == argCount) {
                                matches.add(cand);
                            }
                        }
                    }
                    if (matches.isEmpty()) {
                        detail = "no method '" + name + "(" + argCount + " argument(s))' in type '" + receiverType + "'";
                    } else if (matches.size() > 1) {
                        // Multiple matches with same type, name, arity = ambiguity
                        // Emit exactly ONE unresolved ambiguity record per call site with source span and nonempty ambiguity reason
                        // End processing for this call so generic emitter never runs
                        String reason = "unresolved call: ambiguous - multiple same-type same-name same-arity declarations (" + matches.size() + " matches) for " + 
                            (receiverType == null ? "" : ("receiver type '" + receiverType + "', ")) + 
                            "method '" + name + "' with " + argCount + " argument(s) at line " + line;
                        addUnresolved(fm, line, name, receiverType, callText, reason);
                        // Set target to null and skip adding resolved edge
                        target = null;
                        detail = "ambiguous: " + reason;
                        // Continue to the next if block to end processing for this call
                    } else {
                        target = matches.get(0);
                    }
                }

                if (target == null) {
                    call.resolved = false;
                    call.detail = detail;
                    fm.calls.add(call);
                    // Skip adding unresolved entry if detail indicates ambiguity (already added)
                    if (!detail.startsWith("ambiguous:")) {
                        addUnresolved(fm, line, name, receiverType, callText,
                            "unresolved call: '" + name + "' on receiver type '" + receiverType + "' -- " + detail);
                    }
                    continue;
                }

                call.resolved = true;
                call.calleeFile = target.file;
                call.calleeLine = target.startLine;
                call.calleeType = target.declaringType;
                fm.calls.add(call);
            }
        }
    }

    /* The dynamic getMethod(...)/method.invoke probe appears as its own
       MethodCallExpr with a complex/unbound receiver, so it already lands in
       the unresolved list via addUnresolved. This helper enriches its reason
       and target from the parsed getMethod call text: the argument is derived
       from the call (balanced-paren scan), never hardcoded, so any variable
       name works.                                    */

    static void enrichDynamicProbes(List<FileModel> models) {
        for (FileModel fm : models) {
            for (int i = 0; i < fm.unresolved.size(); i++) {
                UnresolvedEntry u = fm.unresolved.get(i);
                if (!"invoke".equals(u.name)) {
                    continue;
                }
                MethodInfo enclosing = enclosingMethod(fm.methods, u.line);
                if (enclosing == null) {
                    continue;
                }
                String text = methodText(fm.lines, enclosing);
                String arg = getMethodArgument(text);
                if (arg == null) {
                    continue;
                }
                String getMethodCall = "getMethod(" + arg + ")";
                u.target = "java.lang.reflect.Method via " + getMethodCall + "/method.invoke (dynamic probe)";
                u.reason = "dynamic invocation probe: the method name is supplied by " + getMethodCall + " in "
                    + enclosing.name + " (line " + enclosing.startLine + "); the target of method.invoke depends "
                    + "on the runtime value of " + arg + ", so it is not a statically resolvable callee and is listed "
                    + "here as unresolved, never as a resolved callee";
            }
        }
    }

    static String getMethodArgument(String text) {
        int idx = text.indexOf("getMethod");
        if (idx < 0) {
            return null;
        }
        int open = text.indexOf('(', idx);
        if (open < 0) {
            return null;
        }
        int depth = 0;
        for (int j = open; j < text.length(); j++) {
            char c = text.charAt(j);
            if (c == '(') {
                depth++;
            } else if (c == ')') {
                depth--;
                if (depth == 0) {
                    return text.substring(open + 1, j).trim();
                }
            }
        }
        return null;
    }

    /* ---------------- output ---------------- */

    static List<Map<String, Object>> typeMaps(List<TypeInfo> types) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (int i = 0; i < types.size(); i++) {
            TypeInfo t = types.get(i);
            out.add(kv("name", t.name, "kind", t.kind, "start_line", t.startLine, "end_line", t.endLine));
        }
        return out;
    }

    static List<Map<String, Object>> methodMaps(List<MethodInfo> methods) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (int i = 0; i < methods.size(); i++) {
            MethodInfo m = methods.get(i);
            out.add(kv("name", m.name, "declaring_type", m.declaringType, "file", m.file, "parameters", m.paramTypes,
                "start_line", m.startLine, "end_line", m.endLine));
        }
        return out;
    }

    static List<Map<String, Object>> callMaps(List<CallRecord> calls) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (int i = 0; i < calls.size(); i++) {
            CallRecord c = calls.get(i);
            out.add(kv("caller_method", c.callerMethod, "caller_file", c.callerFile, "caller_line", c.callerLine,
                "callee_name", c.calleeName, "receiver_type", c.receiverType, "resolved", c.resolved,
                "callee_file", c.calleeFile, "callee_line", c.calleeLine, "callee_type", c.calleeType,
                "arg_count", c.argCount, "call_text", c.callText, "detail", c.detail));
        }
        return out;
    }

    static List<Map<String, Object>> unresolvedMaps(List<UnresolvedEntry> entries) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (int i = 0; i < entries.size(); i++) {
            UnresolvedEntry u = entries.get(i);
            out.add(kv("file", u.file, "line", u.line, "name", u.name, "target", u.target, "symbol", u.symbol,
                "reason", u.reason, "call_text", u.callText, "content_hash", u.contentHash, "origin", u.origin));
        }
        return out;
    }

    public static void main(String[] args) throws Exception {
        if (args.length == 0) {
            System.err.println("usage: DumpTool <file.java> [file.java ...]");
            System.exit(2);
        }
        List<FileModel> models = new ArrayList<>();
        for (int i = 0; i < args.length; i++) {
            models.add(collectFile(Paths.get(args[i])));
        }
        resolveCalls(models);
        enrichDynamicProbes(models);

        List<Map<String, Object>> fileEntries = new ArrayList<>();
        for (int i = 0; i < models.size(); i++) {
            FileModel fm = models.get(i);
            Map<String, Object> fe = new LinkedHashMap<>();
            fe.put("path", fm.file.toString());
            fe.put("content_hash", fm.contentHash);
            fe.put("types", typeMaps(fm.types));
            fe.put("methods", methodMaps(fm.methods));
            fe.put("calls", callMaps(fm.calls));
            fe.put("unresolved", unresolvedMaps(fm.unresolved));
            fileEntries.add(fe);
        }
        Map<String, Object> root = new LinkedHashMap<>();
        root.put("files", fileEntries);
        System.out.println(obj(root));
    }
}
"""


def _default_jar_path() -> Path:
    return Path(
        os.path.expanduser(
            "~/.m2/repository/com/github/javaparser/javaparser-core/3.17.0/"
            "javaparser-core-3.17.0.jar"
        )
    )


def _resolve_jar() -> Path:
    override = os.environ.get("BO_JAVA_PARSER_JAR")
    if override:
        path = Path(override)
        if path.is_file():
            return path
    path = _default_jar_path()
    if path.is_file():
        return path
    raise FileNotFoundError(
        "javaparser-core 3.17.0 jar not found at "
        + str(_default_jar_path())
        + "; run: mvn -q dependency:get "
        "-Dartifact=com.github.javaparser:javaparser-core:3.17.0"
    )


class JavaAdapter:
    """Static Java resolution adapter (javaparser-core 3.17.0 backend)."""

    def __init__(self, repo_paths: list[Path | str]):
        self.repo_paths = [Path(p) for p in repo_paths]
        self._jar = _resolve_jar()
        self._indexed = False
        self._summary: dict[str, Any] = {}
        self._methods: dict[tuple[str, str], dict[str, Any]] = {}
        self._resolved_calls: list[dict[str, Any]] = []
        self._unresolved: list[dict[str, Any]] = []
        self._content_hash: dict[str, str] = {}

    # ---------------------------------------------------------------- indexing

    def _sources(self) -> list[str]:
        sources: list[str] = []
        for repo in self.repo_paths:
            for root, dirs, names in os.walk(repo):
                dirs.sort()
                for name in sorted(names):
                    if name.endswith(".java"):
                        sources.append(os.path.join(root, name))
        return sorted(sources)

    def index(self) -> dict[str, Any]:
        """Parse every ``.java`` file with javaparser-core; deterministic summary."""
        jar = str(self._jar)
        sources = self._sources()
        with tempfile.TemporaryDirectory(prefix="bo_java_dump_") as tmp:
            source_file = os.path.join(tmp, "DumpTool.java")
            Path(source_file).write_text(_DUMP_TOOL_JAVA, encoding="utf-8")
            compile_proc = subprocess.run(
                ["javac", "-cp", jar, "-d", tmp, source_file],
                capture_output=True,
                text=True,
            )
            if compile_proc.returncode != 0:
                raise RuntimeError(
                    "DumpTool compilation failed:\n" + compile_proc.stderr
                )
            run_proc = subprocess.run(
                ["java", "-cp", jar + os.pathsep + tmp, "DumpTool"] + sources,
                capture_output=True,
                text=True,
            )
            if run_proc.returncode != 0:
                raise RuntimeError("DumpTool run failed:\n" + run_proc.stderr)
            payload = json.loads(run_proc.stdout)
        return self._build(payload)

    def _ensure_indexed(self) -> None:
        if not self._indexed:
            self._summary = self.index()
            self._indexed = True

    def _build(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._methods = {}
        self._resolved_calls = []
        self._unresolved = []
        self._content_hash = {}

        base = {"origin": ORIGIN, "extractor_version": EXTRACTOR_VERSION}

        file_summaries: list[dict[str, Any]] = []
        for entry in payload.get("files", []):
            path = entry["path"]
            content_hash = entry.get("content_hash")
            if not content_hash:
                content_hash = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            self._content_hash[path] = content_hash
            record_base = {**base, "content_hash": content_hash}

            for method in entry.get("methods", []):
                arity = len(method.get("parameters", []))
                key = (method["declaring_type"], method["name"], arity)
                if key not in self._methods:  # first declaration wins (deterministic; overloads distinguished by arity)
                    self._methods[key] = {
                        "file": method["file"],
                        "line": method["start_line"],
                        "start_line": method["start_line"],
                        "end_line": method["end_line"],
                        "parameters": list(method.get("parameters", [])),
                        **record_base,
                    }

            for call in entry.get("calls", []):
                record = dict(call)
                record.update(record_base)
                if record.get("resolved"):
                    self._resolved_calls.append(record)

            # Unresolved entries come from the dump tool's explicit ``unresolved``
            # list (each already carries file, line, name, target, symbol and
            # reason); dynamic probes such as getMethod(...)/invoke are
            # enriched there by the Java side.
            for unresolved_entry in entry.get("unresolved", []):
                record = dict(unresolved_entry)
                record.update(record_base)
                self._unresolved.append(record)

            calls = entry.get("calls", [])
            file_summaries.append({
                "path": path,
                "content_hash": content_hash,
                "types": len(entry.get("types", [])),
                "methods": len(entry.get("methods", [])),
                "resolved_calls": sum(1 for c in calls if c.get("resolved")),
                "unresolved": sum(1 for c in calls if not c.get("resolved")),
            })

        ambiguous_count = 0
        for entry in self._unresolved:
            if "ambiguous" in entry.get("reason", ""):
                ambiguous_count += 1
        
        summary = {
            "origin": ORIGIN,
            "extractor_version": EXTRACTOR_VERSION,
            "repos": [str(p) for p in self.repo_paths],
            "files": file_summaries,
        }
        if ambiguous_count < 5:
            summary["ambiguous_entries_count"] = ambiguous_count
        self._summary = summary
        return self._summary

    def _find_method(self, type_name: str, method_name: str) -> dict[str, Any] | None:
        """Look up a method by (type, name) across arity-distinguished keys.

        The ``_methods`` key includes arity so overloads are stored distinctly;
        a symbol carries no arity, so the first matching declaration wins.
        """
        for (t, n, _arity), m in self._methods.items():
            if t == type_name and n == method_name:
                return m
        return None

    # ---------------------------------------------------------------- resolution

    def resolve_definition(self, symbol: str) -> dict[str, Any] | None:
        self._ensure_indexed()
        if "." not in symbol:
            return None
        type_name, _, method_name = symbol.partition(".")
        method = self._find_method(type_name, method_name)
        if method is None:
            return None
        return {
            "file": method["file"],
            "line": method["line"],
            "content_hash": method["content_hash"],
            "origin": ORIGIN,
            "extractor_version": EXTRACTOR_VERSION,
            "symbol": symbol,
            "span": {
                "start_line": method["start_line"],
                "end_line": method["end_line"],
            },
        }

    def resolve_callers(self, symbol: str) -> list[dict[str, Any]]:
        self._ensure_indexed()
        out: list[dict[str, Any]] = []
        for call in self._resolved_calls:
            callee_type = str(call.get("callee_type") or "")
            callee_name = str(call.get("callee_name") or "")
            if not callee_type or (callee_type + "." + callee_name) != symbol:
                continue
            out.append({
                "file": call["caller_file"],
                "line": call["caller_line"],
                "caller": call.get("caller_method"),
                "callee": symbol,
                "content_hash": call["content_hash"],
                "origin": ORIGIN,
                "extractor_version": EXTRACTOR_VERSION,
                "span": {
                    "start_line": call["caller_line"],
                    "end_line": call["caller_line"],
                },
            })
        return out

    def callees(self, symbol: str) -> list[dict[str, Any]]:
        self._ensure_indexed()
        if "." not in symbol:
            return []
        type_name, _, method_name = symbol.partition(".")
        method = self._find_method(type_name, method_name)
        if method is None:
            return []
        out: list[dict[str, Any]] = []
        for call in self._resolved_calls:
            if call["caller_file"] != method["file"]:
                continue
            if not (method["start_line"] <= call["caller_line"] <= method["end_line"]):
                continue
            callee_type = str(call.get("callee_type") or "")
            callee_name = str(call.get("callee_name") or "")
            out.append({
                "file": call["callee_file"],
                "line": call["callee_line"],
                "callee": (callee_type + "." + callee_name) if callee_type else None,
                "caller": symbol,
                "content_hash": call["content_hash"],
                "origin": ORIGIN,
                "extractor_version": EXTRACTOR_VERSION,
                "span": {
                    "start_line": call["callee_line"],
                    "end_line": call["callee_line"],
                },
            })
        return out

    def unresolved(self) -> list[dict[str, Any]]:
        self._ensure_indexed()
        result = []
        for entry in self._unresolved:
            record = dict(entry)
            # Add span field to unresolved entries (source span for call site)
            record["span"] = {
                "start_line": record.get("line", 1),
                "end_line": record.get("line", 1),
            }
            result.append(record)
        return result
