# Security Policy

Seiche handles public market evidence, authenticated machine interfaces, release
credentials, and automated deployment. Please report security defects privately
so they can be investigated without putting users or infrastructure at risk.

## Supported versions

Security fixes are made on the default branch and, when warranted, released from
the latest published version. Older releases are not maintained as separate
security branches. Before reporting an issue against an old installation, first
check whether it is reproducible on the latest release or current default branch.

## Reporting a vulnerability

Open a [private GitHub security advisory](https://github.com/beepboop2025/seiche/security/advisories/new).
Do not open a public issue, discussion, or pull request for an undisclosed
vulnerability.

Include as much of the following as is safe to share:

- the affected component, URL, package version, or commit;
- a minimal reproduction and the conditions needed to trigger it;
- the security impact and who or what could be affected;
- whether the issue affects the hosted service, a local installation, or both;
- relevant logs or screenshots with tokens, personal data, and account details
  removed; and
- any suggested mitigation or disclosure constraints.

Never include credentials in a report. If sensitive supporting material cannot
be attached safely, say so in the advisory and arrange a private transfer with
the maintainer.

The maintainer will acknowledge actionable reports, investigate them, and keep
the reporter informed as a fix and disclosure plan take shape. Response time
depends on severity and maintainer availability. The project does not currently
offer a paid bug-bounty program.

## Coordinated disclosure

Please allow a reasonable period for diagnosis, patching, deployment, and user
notification before publishing details. Seiche will credit reporters who want
credit, unless doing so would create additional risk.

Security fixes follow the same release controls as other production changes:
the exact candidate is tested, release identity is verified, artifacts are
bound to immutable revisions, deployment health is proved, and rollback remains
available. A fix may be disclosed before every downstream installation updates
when active exploitation or user protection makes earlier notice necessary.

## Good-faith research

Good-faith research stays within the following boundaries:

- use accounts and data you own or have permission to test;
- avoid privacy violations, data destruction, persistence, and service
  disruption;
- stop when you encounter credentials, personal data, or access beyond what is
  needed to demonstrate the issue;
- do not use social engineering, spam, denial-of-service traffic, or attacks on
  third-party data providers; and
- make only the minimum requests needed to establish impact.

Research that follows these boundaries and is reported promptly will be treated
as an effort to improve the project, not as abuse.

## What belongs in the public issue tracker

Incorrect market observations, stale evidence clocks, methodology questions,
and ordinary reliability bugs are important, but are not automatically security
vulnerabilities. Report those through the appropriate public issue form unless
they expose confidential data, bypass authorization, compromise integrity, or
create another concrete security impact.

The canonical machine-readable contact is
[`https://seiche.info/.well-known/security.txt`](https://seiche.info/.well-known/security.txt).
