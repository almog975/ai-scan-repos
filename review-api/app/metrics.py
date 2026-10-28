from prometheus_client import Counter, Gauge

REVIEWS_CREATED = Counter("ai_scan_reviews_created_total", "Reviews created", ["depth"])
REVIEWS_RETRIED = Counter("ai_scan_reviews_retried_total", "Scanner jobs relaunched by watchdog")
REVIEWS_FAILED = Counter("ai_scan_reviews_failed_total", "Reviews marked failed")
REVIEWS_COMPLETED = Counter("ai_scan_reviews_completed_total", "Reviews completed")
REVIEWS_RUNNING = Gauge("ai_scan_reviews_running", "Reviews currently running")
