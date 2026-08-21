# Pull request

## Summary

<!-- What problem does this solve, and what changes for a user or operator? -->

Fixes #

## Change type

- [ ] Bug fix
- [ ] Feature or integration
- [ ] Data source or methodology
- [ ] API, MCP, schema, or catalog contract
- [ ] Documentation or community health
- [ ] Release, deployment, security, or operations

## Evidence and boundaries

<!-- Describe affected sources, timestamps/cadence, transformations, rights,
point-in-time status, failure behavior, and any user-facing claim change. Write
"No evidence-boundary change" when none applies. -->

## Validation

| Check | Command or evidence | Result |
| --- | --- | --- |
| Targeted tests | | |
| Full applicable suite | | |
| Build or schema validation | | |
| Live probe, if authorized | | |

## Release and operations

<!-- Note migrations, new secrets, permissions, catalog copies, package/registry
publication, deploy ordering, observability, rollback, and owner-only follow-up.
Write "None" when the change has no release impact. -->

## Checklist

- [ ] I kept the change focused and did not include credentials, private data,
      caches, or restricted raw observations.
- [ ] I added or updated tests for changed behavior.
- [ ] I updated public contracts and documentation where needed.
- [ ] I documented source authority, cadence, rights, and fail-closed behavior
      for data-source changes.
- [ ] I preserved explicit missing/stale evidence instead of converting it into
      a neutral reading.
- [ ] I identified security, privacy, authentication, billing, and supply-chain
      effects.
- [ ] I identified deployment and rollback effects, including cross-repository
      catalog or generated-mirror synchronization.
- [ ] I left version bumps, tags, and publication to the release owner unless
      this pull request is explicitly the reviewed release change.
- [ ] I followed the Code of Conduct and contribution policy.
