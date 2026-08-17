class AnomalyType:
    FORECAST_RESIDUAL = "forecast_residual"
    VOID_RATE = "void_rate"
    REFUND_RATE = "refund_rate"
    DISCOUNT_RATE = "discount_rate"
    STALE_HEARTBEAT = "stale_heartbeat"
    INTRADAY_PACE = "intraday_pace"
    CONDITION_CHANGE = "condition_change"
    MARGIN_FLOOR = "margin_floor"
    
class AnomalySubject:
    TOTAL_UNITS = "total_units"
    TOTAL_REVENUE = "total_revenue"
    VOID_RATE = "void_rate"
    REFUND_RATE = "refund_rate"
    DISCOUNT_RATE = "discount_rate"
    MARGIN_FLOOR = "margin_floor"
    
class AnomalyAction:
    ACK = "ack"
    DISMISS = "dismiss"