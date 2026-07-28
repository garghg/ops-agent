def check_financial_alerts(sales_summary, thresholds) -> list[str]:
    alerts = []

    if sales_summary.transaction_count == 0:
        return alerts

    void_rate = sales_summary.void_count / sales_summary.transaction_count
    if void_rate > thresholds.void_rate:
        alerts.append(
            f"Void rate {void_rate:.1%} exceeds threshold {thresholds.void_rate:.1%}"
        )

    refund_rate = sales_summary.refund_count / sales_summary.transaction_count
    if refund_rate > thresholds.refund_rate:
        alerts.append(
            f"Refund rate {refund_rate:.1%} exceeds threshold {thresholds.refund_rate:.1%}"
        )

    if sales_summary.revenue > 0:
        discount_rate = sales_summary.total_discounts / sales_summary.revenue
        if discount_rate > thresholds.discount_rate:
            alerts.append(
                f"Discount rate {discount_rate:.1%} exceeds threshold {thresholds.discount_rate:.1%}"
            )

    return alerts
