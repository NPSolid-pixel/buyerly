# 86eyr5v2f — Docker log rotation and artifact retention

## Scope

- Bound Docker `json-file` logs for every Compose service.
- Preserve the current and immediate rollback release while removing older
  Buyerly image tags and aged, unused build cache.
- Stop a deployment when disk usage remains critical after safe cleanup.

## Delivery

1. Apply one shared `json-file` logging contract to every service: five
   compressed files of at most 20 MB each.
2. Add a repository-scoped cleanup script that keeps at least two complete
   app/web release pairs and every image referenced by any container.
3. Prune only aged dangling images and build cache. Never inspect or prune
   Docker volumes.
4. Run cleanup before builds and after successful cutover; verify the live
   container logging configuration after every deployment.
5. Add warning/critical disk thresholds (75%/90%) and an operator procedure.

## Safety and rollback

- `KEEP_RELEASES` cannot be set below two.
- An image ID referenced by a running or stopped container is always retained.
- Cleanup targets only the explicit `buyerly-app` and `buyerly-web`
  repositories; no `docker system prune`, `image prune -a`, or `volume prune`
  operation is used.
- A dry-run mode lists image removals without mutating Docker state.

## Verification

- Static shell syntax, Compose rendering, and repository diff checks run
  locally without executing the application test suite.
- GitHub Actions validates the operational contract and full regression suite.
- Production deploy must inspect every long-lived container and confirm the
  expected driver and rotation options before the task can be completed.
