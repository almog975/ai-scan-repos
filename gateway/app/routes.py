"""Explicit proxy table: exactly the /api/* paths of contracts/gateway.openapi.yaml, with the methods of the
referenced review-api.openapi.yaml path items. No catch-all: anything else under /api is 404 NOT_FOUND."""
ROUTES: dict[str, tuple[str, ...]] = {
    "/api/reviews": ("GET", "POST"),
    "/api/reviews/{id}": ("GET",),
    "/api/reviews/{id}/progress": ("GET",),
    "/api/reviews/{id}/files": ("GET",),
    "/api/reviews/{id}/attempts": ("GET",),
    "/api/reviews/{id}/pause": ("POST",),
    "/api/reviews/{id}/resume": ("POST",),
    "/api/reviews/{id}/retry": ("POST",),
    "/api/reviews/{id}/cancel": ("POST",),
    "/api/reviews/{id}/report.json": ("GET",),
    "/api/reviews/{id}/report.md": ("GET",),
    "/api/repos/check": ("GET",),
    "/api/ai-providers": ("GET",),
    "/api/admin/ai-providers": ("GET", "POST"),
    "/api/admin/ai-providers/test": ("POST",),
    "/api/admin/ai-providers/{id}": ("PATCH", "DELETE"),
    "/api/admin/ai-providers/{id}/test": ("POST",),
    "/api/admin/settings": ("GET", "PUT"),
}
STREAMING = {"/api/reviews/{id}/progress"}  # SSE: relayed chunk by chunk
