from src.schemas.anomaly import AnomalySubject, AnomalyType


def check_financial_alerts(sales_summary, thresholds) -> list[dict]:
    alerts = []

    if sales_summary.transaction_count == 0:
        return alerts

    void_rate = sales_summary.void_count / sales_summary.transaction_count
    if void_rate > thresholds.void_rate:
        alerts.append(
            {
                "type": AnomalyType.VOID_RATE,
                "subject": AnomalySubject.VOID_RATE,
                "rate": float(void_rate),
                "expected": thresholds.void_rate,
            }
        )

    refund_rate = sales_summary.refund_count / sales_summary.transaction_count
    if refund_rate > thresholds.refund_rate:
        alerts.append(
            {
                "type": AnomalyType.REFUND_RATE,
                "subject": AnomalySubject.REFUND_RATE,
                "rate": float(refund_rate),
                "expected": thresholds.refund_rate,
            }
        )

    if sales_summary.revenue > 0:
        discount_rate = sales_summary.total_discounts / sales_summary.revenue
        if discount_rate > thresholds.discount_rate:
            alerts.append(
                {
                    "type": AnomalyType.DISCOUNT_RATE,
                    "subject": AnomalySubject.DISCOUNT_RATE,
                    "rate": float(discount_rate),
                    "expected": thresholds.discount_rate,
                }
            )

    return alerts
