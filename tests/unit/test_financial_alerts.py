from collections import namedtuple
from decimal import Decimal

from src.schemas.template import AlertThresholds
from src.services.alert_service import check_financial_alerts

SalesSummary = namedtuple(
    "SalesSummary",
    ["transaction_count", "revenue", "total_discounts", "void_count", "refund_count"],
)


class TestNoAlerts:
    def test_all_rates_below_threshold(self):
        summary = SalesSummary(
            transaction_count=100,
            revenue=Decimal("500.00"),
            total_discounts=Decimal("10.00"),
            void_count=1,
            refund_count=1,
        )
        alerts = check_financial_alerts(summary, AlertThresholds())
        assert alerts == []

    def test_zero_transactions(self):
        summary = SalesSummary(
            transaction_count=0,
            revenue=Decimal(0),
            total_discounts=Decimal(0),
            void_count=0,
            refund_count=0,
        )
        alerts = check_financial_alerts(summary, AlertThresholds())
        assert alerts == []


class TestVoidAlert:
    def test_void_rate_above_threshold(self):
        summary = SalesSummary(
            transaction_count=100,
            revenue=Decimal("500.00"),
            total_discounts=Decimal(0),
            void_count=10,
            refund_count=0,
        )
        alerts = check_financial_alerts(summary, AlertThresholds())
        assert len(alerts) == 1
        assert "Void rate" in alerts[0]


class TestRefundAlert:
    def test_refund_rate_above_threshold(self):
        summary = SalesSummary(
            transaction_count=100,
            revenue=Decimal("500.00"),
            total_discounts=Decimal(0),
            void_count=0,
            refund_count=10,
        )
        alerts = check_financial_alerts(summary, AlertThresholds())
        assert len(alerts) == 1
        assert "Refund rate" in alerts[0]


class TestDiscountAlert:
    def test_discount_rate_above_threshold(self):
        summary = SalesSummary(
            transaction_count=100,
            revenue=Decimal("500.00"),
            total_discounts=Decimal("100.00"),
            void_count=0,
            refund_count=0,
        )
        alerts = check_financial_alerts(summary, AlertThresholds())
        assert len(alerts) == 1
        assert "Discount rate" in alerts[0]


class TestMultipleAlerts:
    def test_all_rates_above_threshold(self):
        summary = SalesSummary(
            transaction_count=100,
            revenue=Decimal("500.00"),
            total_discounts=Decimal("100.00"),
            void_count=10,
            refund_count=10,
        )
        alerts = check_financial_alerts(summary, AlertThresholds())
        assert len(alerts) == 3