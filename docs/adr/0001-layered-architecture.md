# ADR 0001: Layered architecture with a write guard

**Status:** accepted

The glasses use a Jieli controller whose control channel may also carry OTA commands. Every write must therefore pass one
choke point. Services never touch the transport directly for writes; they use `WriteGuard`, which is allowlist-only and
dry-run by default. Protocol logic is pure so it can be tested against recorded fixtures, and transports are protocols
with fakes so no test needs hardware.
