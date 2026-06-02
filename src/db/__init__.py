"""SQLite persistence layer for Zoho Books operational data."""

from .connection import DB_PATH, get_connection, init_database
from .repository import DatabaseRepository
from .customer_payments_sync import sync_customer_payments, sync_customer_payments_range
from .incremental_sync import incremental_params_for_module, run_incremental_sync
from .shipments_sync import sync_shipments, sync_shipments_range
from .invoices_sync import sync_invoices, sync_invoices_range
from .sales_orders_sync import sync_sales_orders, sync_sales_orders_range

__all__ = [
    "DB_PATH",
    "get_connection",
    "init_database",
    "DatabaseRepository",
    "sync_sales_orders",
    "sync_sales_orders_range",
    "sync_invoices",
    "sync_invoices_range",
    "sync_customer_payments",
    "sync_customer_payments_range",
    "sync_shipments",
    "sync_shipments_range",
    "incremental_params_for_module",
    "run_incremental_sync",
]
