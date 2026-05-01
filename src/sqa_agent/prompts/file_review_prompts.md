# General Coding Principles

## DRY/SSOT/Magic-Numbers Check

Check the file-under-review for code repetition (DRY-violations). Are there repeated code fragments that can be factored out into a shared function, or otherwise eliminated?  Is there functionality in this file-under-review that's duplicative with what's elsewhere in the project?

**Do not flag** repetition where the abstraction would be worse than the duplication. In particular:
- Short blocks (roughly ≤5 lines) that are simple and self-documenting — the overhead of indirection (naming, parameter passing, finding the helper) can exceed the cost of the repetition.
- Blocks that look syntactically similar but serve different semantic purposes or have different recovery strategies (e.g. exception handlers with site-specific behavior). Forcing these into one abstraction couples unrelated concerns.
- Cases where the "shared" helper would need multiple flags/modes to accommodate each call site — this is a sign the callers aren't truly duplicates.

Check the file-under-review for any single-source-of-truth (SSOT) violations.  Any state of the system should have a single source of truth. Are there unnecessary "default values" that could mask a failure to provide the one true source-of-truth values to a function?  Is state saved locally, when it should be re-acquired from its single source of truth as-needed? 

Magic numbers should be avoided in most cases. 0 and 1 are frequenty (but not always) reasonable exceptions.

## Interfaces+Cohesion check

Check any code that's calling into this file-under-review for any logical issues with the way the system is factored. Does the function do what the caller would expect it to do?  Is it named appropriately?  Are the argument names the best possible?

Check that any of the any calls to functions outside this file-under-review make sense. Is it obvious from the name what the function will do?  Are the argument names the best possible?

Check the file-under-review for any cohesion issues. Would it be better to be broken up into smaller files with better cohesion?  Is there high-coupling between the elements of this file?  Does it present a minimal interface to external components?

- Are implementation details hidden behind abstractions?  Are interfaces minimal?
- **Do not flag** high parameter counts when each parameter is well-named, independently optional with sensible defaults, and the function genuinely needs that configuration surface. Suggesting a wrapper type or dataclass just to reduce the count adds indirection without reducing real complexity — only flag when the grouped parameters represent a cohesive concept that recurs across multiple call sites.
- Does this file demonstrate good separation-of-concerns, with respect to its clients and helpers?
- Are any custom types/interfaces lean, containing only the required fields and minimal complexity?
- Are the field names descriptive and accurate?
- Are optionals used effectively -- used exactly when they improve the code simplicity?


## Logic+Consistency

Check the logic internal to this file-under-review.  Is it correct?  Are special cases well-documented?  Does it match the clients expectations?

Do you notice any inconsistencies?

- Inconsistent naming (camelCase vs snake_case, capitalization, parts-of-speech, etc.)
- Inconsistent argument types (use of optionals, null vs undefined, etc.)
- Inconsistent error handling strategies.

Check the code for any opportunities for optimization: either speed or memory utilization. Highlight any findings that are out of balance, unless they've been noted as previously resolved. Any big wins available?

- Are all edge cases handled?  All combinations of input arguments covered?

## Comment+PRD check

Check the comment strings in the file-under-review for accuracy, completeness, clarity, and any stale language.

Check docs/prds for any PRDs that pertain to this file-under-review. Are they up-to-date and do their contents match what's actually implemented?


## Error Handling

- Are default values only returned when it's reasonable to expect such a value, and not as a silent failure?
- Are null/undefined values used appropriately to indicate an allowed fallback result?
- If a condition is truly an error, does it error out to indicate that a real problem exists?


## KISS / YAGNI

- Is it overly complex?
- Are "just in case" functions / arguments stripped out?
- Are any stale/unused functions removed?
- Are any unused code paths culled?


# Security Review

Are there any secrets (API keys, passwords, other credentials) present in the file-under-review? Does this file risk mishandling any PII?

Is the system protected from malicious abuse?

- Endpoints require authentication, or otherwise prevent a malicious actor from corrupting our database?
- DDOS protections, rate limits.
- CORS protection
- Authentication and session handling
- RBAC/RLS if applicable
- XSS/CSRF vulnerabilities
- SSRF risks
- Insecure CORS configurations
- Data exposure in client bundles
- Third-party dependency vulnerabilities (`npm audit`)
- Secrets in git history
- Unsafe defaults in configurations
- Log sanitization (sensitive data in logs)
- Error message information leakage

For any endpoints, are all inputs well-validated?  Are queries protected from injection attacks?
