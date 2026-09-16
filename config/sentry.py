FILTERED_VALUE = "[Filtered]"


def _is_sensitive_key(key):
    normalized = str(key).strip().lower().replace("-", "_")
    return "email" in normalized or "password" in normalized or "authorization" in normalized or "cookie" in normalized


def scrub_sentry_event(value, hint=None):
    """Return an event copy with identity and credential fields filtered."""
    if isinstance(value, dict):
        return {key: FILTERED_VALUE if _is_sensitive_key(key) else scrub_sentry_event(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_sentry_event(item) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_sentry_event(item) for item in value)
    return value


def initialize_sentry(dsn):
    """Initialize Django error tracking only when a non-empty DSN is configured."""
    dsn = (dsn or "").strip()
    if not dsn:
        return False

    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=dsn,
        integrations=[DjangoIntegration()],
        send_default_pii=False,
        before_send=scrub_sentry_event,
    )
    return True
