-- One result row for the selected order-line population, including empty input.
WITH totals AS (
    SELECT SUM(sales) AS sales, SUM(profit) AS profit,
           COUNT(DISTINCT order_id) AS order_count, COUNT(*) AS order_line_count,
           COUNT(*) - COUNT(sales) AS sales_unavailable_line_count,
           COUNT(*) - COUNT(profit) AS profit_unavailable_line_count,
           COUNT(*) - COUNT(order_id) AS order_id_missing_line_count
    FROM orders_selected
), statuses AS (
    SELECT t.*,
           CASE WHEN sales IS NULL THEN 'no_usable_values'
                WHEN sales_unavailable_line_count > 0 THEN 'partial' ELSE 'complete' END AS sales_status,
           CASE WHEN NOT s.profit_available THEN 'field_missing'
                WHEN profit IS NULL THEN 'no_usable_values'
                WHEN profit_unavailable_line_count > 0 THEN 'partial' ELSE 'complete' END AS profit_status,
           CASE WHEN NOT s.profit_available THEN 'profit_field_missing'
                WHEN sales IS NULL OR profit IS NULL THEN 'no_usable_measures'
                WHEN sales_unavailable_line_count > 0 OR profit_unavailable_line_count > 0 THEN 'incomplete_measures'
                WHEN sales = 0 THEN 'zero_sales' ELSE 'available' END AS profit_margin_status
    FROM totals AS t CROSS JOIN source_schema AS s
)
SELECT *, CASE WHEN profit_margin_status = 'available' THEN profit / NULLIF(sales, 0) END AS profit_margin
FROM statuses;
