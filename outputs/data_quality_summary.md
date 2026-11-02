# Phase 1 Data Quality and Metrics

Source: `data/raw/sample_-_superstore.xlsx`. Engine: `xlrd`.
Inspection reference date: 2026-11-01. No date filter is applied.

## Base Metrics

| Metric | Value |
| --- | ---: |
| sales | 2,326,534.3543 |
| profit | 292,296.8146 |
| profit_margin | 12.563615% |
| order_count | 5,111 |
| order_line_count | 10,194 |

Amounts above are displayed to four decimal places. CSV and JSON artifacts retain calculation precision.

## Workbook Grain

- Orders: 10,194 rows, 21 fields. One product sales line item within an order.
- People: 4 rows, 2 fields. One sales region and its regional manager.
- Returns: 296 rows, 2 fields. One order-level return flag; not a returned product line.

## Review Findings

- Orders / full_row: 0 extra rows; 0 affected rows. Exact duplicate source records, including identifiers.
- Orders / order_id: 5,083 extra rows; 7,601 affected rows. Expected at line-item grain; repeated orders are not automatically errors.
- Orders / order_line_id: 0 extra rows; 0 affected rows. Expected unique source line identifier.
- Orders / content_excluding_row_id: 2 extra rows; 4 affected rows. Potential duplicates requiring review; retained in metrics.
- Orders / order_product: 11 extra rows; 22 affected rows. A candidate key only; repeated products may be split line items.
- People / full_row: 0 extra rows; 0 affected rows. Exact duplicate source records, including identifiers.
- People / region_key: 0 extra rows; 0 affected rows. Expected unique on the right side of a many-to-one join.
- Returns / full_row: 0 extra rows; 0 affected rows. Exact duplicate source records, including identifiers.
- Returns / order_id: 0 extra rows; 0 affected rows. Expected unique at the declared order-level grain.
- Orders.Order Date / after_reference_date: 938 of 10,194 rows (9.20%). Retained sample dates; this reference date is not a reporting filter.
- Orders.Ship Date / after_reference_date: 969 of 10,194 rows (9.51%). Retained sample dates; this reference date is not a reporting filter.
- Orders.Sales / iqr_outlier: 1,183 of 10,194 rows (11.60%). Review flag only; retained in totals. 1.5-IQR bounds: -271.2 to 497.92.
- Orders.Profit / iqr_outlier: 1,913 of 10,194 rows (18.77%). Review flag only; retained in totals. 1.5-IQR bounds: -39.544887 to 70.603612.
- Orders.Profit / negative_value: 1,901 of 10,194 rows (18.65%). Retained. Negative profit can indicate a loss; investigate negative sales before assuming an error.

## Reconciliation and Relationships

Reconciliation checks passed: 12. See `reconciliation.csv` for differences and availability.
Sales, Profit, and line counts are additive. Order Count uses the distinct union across groups; group order counts are not necessarily additive.
Profit Margin is SUM(Profit) / SUM(Sales), never the mean of row or group margins.
Metrics are calculated directly from Orders before any enrichment. Trial left joins use validate='many_to_one'; unsafe right-side keys block trial joins without changing the metric source.
Orders.Region -> People.Region maps regions to managers. Orders.Order ID -> Returns.Order ID attaches an order-level return flag. Orders absent from Returns are expected; missing flags do not prove the absence of returns without a completeness guarantee.

## Data Handling and Limits

- No rows are automatically deleted, deduplicated, clipped, or imputed. Review flags and negative profit remain in totals.
- Null and whitespace-only grouping values, and missing or invalid order dates, appear in an __UNASSIGNED__ group so their valid amounts and line counts reconcile.
- Non-numeric, non-finite, and missing amounts are excluded only from the affected sum, which is labeled partial or unavailable. Missing measures are not zero. Margin is unavailable when either measure is incomplete or total Sales is zero.
- Missing order identifiers are excluded from distinct Order Count but retained in line count and valid amount totals.
- IQR flags identify unusual values for review, not invalid records. Reconciliation proves consistent arithmetic and coverage, not business accuracy.
- Source dates include future sample records. Currency, tax treatment, profit cost basis, and return-list completeness remain unconfirmed. Amount totals are in source units, not confirmed USD.
- Raw source SHA-256 preserved: `5045ed4fec49cae2cc0dfc5ab0d204ba09efa767fe00c996822499e8cc42fbbd`.
