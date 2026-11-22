# Summary of Fixes to src/bo_extract/deterministic.py

## Issues Fixed

### 1. HIGH severity: Subscript dispatch check accepts Constant slices
**Problem**: The `_has_dispatch_indirection` function was accepting `ast.Constant` as a valid slice type in subscript checks, when the rule specifies slice must NOT be Constant (only Name, Attribute, Call, BinOp allowed).

**Solution**: Updated line 499 in `_has_dispatch_indirection` function:
- Changed from: `isinstance(n.slice, (ast.Name, ast.Constant))`
- Changed to: `isinstance(n.slice, (ast.Name, ast.Attribute, ast.Call, ast.BinOp))`

### 2. MEDIUM severity: _predicate_fields called without module_consts
**Problem**: The `_dispatch_loop_decision_fields` function was calling `_predicate_fields` without passing the `module_consts` parameter, causing TypeError on Name operands.

**Solution**: Added module_consts parameter threading through the dispatch path:
- **Line 504**: Added `module_consts=None` parameter to `_dispatch_loop_decision_fields` function signature
- **Line 544**: Updated call to `_predicate_fields` inside `_dispatch_loop_decision_fields` to pass `module_consts`
- **Line 669**: Updated caller from `_deepen_constant_rules` to pass `module_consts` to `_dispatch_loop_decision_fields`
- **Line 699**: Updated caller from `_decision_rules` to pass `module_consts` to `_dispatch_loop_decision_fields`

### 3. QUESTION resolved: static getattr(x,"lit") does NOT count
**Status**: Already resolved in existing code. The `_operand` function correctly handles the rule that static `getattr(x,"lit")` should not count.

## Verification
- All 162 tests pass
- No weakening of any test conditions
- Changes are limited to src/bo_extract/deterministic.py only
- Implementation follows lean ladder principle (minimum code that passes)

## Impact
These fixes ensure:
1. Dynamic subscript detection is more accurate (rejecting Constant slices)
2. Name operands are resolved correctly using module_constants throughout the dispatch path
3. The getattr rule is properly enforced (static getattr(x,"lit") does not count as dynamic)