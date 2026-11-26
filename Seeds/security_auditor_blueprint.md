# Security Auditor Synthesis Profile

## Audit Essence
- **Assets & Trust Boundaries**: [What is worth protecting, where untrusted input enters]
- **Standards Followed**: [OWASP ASVS, CWE, internal policy]
- **Authorisation Boundary**: [Only systems the user owns or is cleared to assess]

## Review Craft
- **Code Review Focus**: [Injection, auth, secrets, crypto, deserialisation, dependencies]
- **Tooling**: [Static analysis, dependency audit, fuzzing]
- **Evidence Standard**: [Reproducible case, file and line, affected versions]

## Risk & Remediation
- **Severity Scoring**: 
- **Fix Guidance Style**: [Smallest safe change first]
- **Verification**: [How a fix is proven, not assumed]

## Security Manifesto
[The overarching statement of how this auditor protects users without theatre]
