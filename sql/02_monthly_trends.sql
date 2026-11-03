-- Calendar completion precedes LAG: a missing February cannot make January the
-- previous month of March. Undated lines are reconciled in their own row.
WITH bounds AS (
    SELECT COALESCE(p.start_date, MIN(o.order_date)) AS first_date,
           COALESCE(p.end_date, MAX(o.order_date)) AS last_date
    FROM analysis_parameters AS p LEFT JOIN orders_selected AS o ON TRUE
    GROUP BY p.start_date, p.end_date
), calendar AS (
    SELECT CAST(month AS DATE) AS month
    FROM bounds, LATERAL generate_series(
        date_trunc('month', first_date), date_trunc('month', last_date), INTERVAL '1 month'
    ) AS months(month)
), observed AS (
    SELECT CAST(date_trunc('month', order_date) AS DATE) AS month,
           SUM(sales) AS sales, SUM(profit) AS profit,
           COUNT(DISTINCT order_id) AS order_count, COUNT(*) AS order_line_count,
           COUNT(*) - COUNT(sales) AS sales_unavailable_line_count,
           COUNT(*) - COUNT(profit) AS profit_unavailable_line_count,
           COUNT(*) - COUNT(order_id) AS order_id_missing_line_count
    FROM orders_selected WHERE order_date IS NOT NULL GROUP BY 1
), complete_calendar AS (
    SELECT c.month, o.sales, o.profit,
           COALESCE(o.order_count, 0) AS order_count,
           COALESCE(o.order_line_count, 0) AS order_line_count,
           COALESCE(o.sales_unavailable_line_count, 0) AS sales_unavailable_line_count,
           COALESCE(o.profit_unavailable_line_count, 0) AS profit_unavailable_line_count,
           COALESCE(o.order_id_missing_line_count, 0) AS order_id_missing_line_count,
           'calendar_month' AS record_type
    FROM calendar AS c LEFT JOIN observed AS o USING (month)
), undated AS (
    SELECT NULL::DATE AS month, SUM(sales) AS sales, SUM(profit) AS profit,
           COUNT(DISTINCT order_id) AS order_count, COUNT(*) AS order_line_count,
           COUNT(*) - COUNT(sales) AS sales_unavailable_line_count,
           COUNT(*) - COUNT(profit) AS profit_unavailable_line_count,
           COUNT(*) - COUNT(order_id) AS order_id_missing_line_count,
           'undated' AS record_type
    FROM orders_selected WHERE order_date IS NULL
), all_groups AS (
    SELECT * FROM complete_calendar UNION ALL SELECT * FROM undated
), statuses AS (
    SELECT g.*,
           CASE WHEN order_line_count = 0 THEN 'no_records' ELSE 'observed' END AS record_status,
           CASE WHEN sales IS NULL THEN 'no_usable_values'
                WHEN sales_unavailable_line_count > 0 THEN 'partial' ELSE 'complete' END AS sales_status,
           CASE WHEN NOT s.profit_available THEN 'field_missing'
                WHEN profit IS NULL THEN 'no_usable_values'
                WHEN profit_unavailable_line_count > 0 THEN 'partial' ELSE 'complete' END AS profit_status,
           CASE WHEN NOT s.profit_available THEN 'profit_field_missing'
                WHEN sales IS NULL OR profit IS NULL THEN 'no_usable_measures'
                WHEN sales_unavailable_line_count > 0 OR profit_unavailable_line_count > 0 THEN 'incomplete_measures'
                WHEN sales = 0 THEN 'zero_sales' ELSE 'available' END AS profit_margin_status
    FROM all_groups AS g CROSS JOIN source_schema AS s
), lagged AS (
    SELECT *, LAG(month) OVER w AS previous_month,
           LAG(sales) OVER w AS previous_sales, LAG(profit) OVER w AS previous_profit,
           LAG(sales_status) OVER w AS previous_sales_status,
           LAG(profit_status) OVER w AS previous_profit_status,
           LAG(order_line_count) OVER w AS previous_line_count
    FROM statuses
    WINDOW w AS (PARTITION BY record_type ORDER BY month)
), changes AS (
    SELECT *,
           CASE WHEN record_type <> 'calendar_month' THEN 'not_calendar_month'
                WHEN order_line_count = 0 THEN 'no_records'
                WHEN previous_month IS NULL THEN 'no_previous_month'
                WHEN previous_line_count = 0 THEN 'previous_no_records'
                WHEN sales_status <> 'complete' OR previous_sales_status <> 'complete' THEN 'incomplete_measures'
                WHEN previous_sales = 0 THEN 'previous_zero'
                WHEN previous_sales < 0 THEN 'previous_negative' ELSE 'available' END AS sales_mom_status,
           CASE WHEN record_type <> 'calendar_month' THEN 'not_calendar_month'
                WHEN order_line_count = 0 THEN 'no_records'
                WHEN previous_month IS NULL THEN 'no_previous_month'
                WHEN previous_line_count = 0 THEN 'previous_no_records'
                WHEN profit_status <> 'complete' OR previous_profit_status <> 'complete' THEN 'incomplete_measures'
                WHEN previous_profit = 0 THEN 'previous_zero'
                WHEN previous_profit < 0 THEN 'previous_negative' ELSE 'available' END AS profit_mom_status
    FROM lagged
)
SELECT *,
       CASE WHEN profit_margin_status = 'available' THEN profit / NULLIF(sales, 0) END AS profit_margin,
       CASE WHEN record_type = 'calendar_month' AND sales_status = 'complete'
                  AND previous_sales_status = 'complete' THEN sales - previous_sales END AS sales_mom_change,
       CASE WHEN record_type = 'calendar_month' AND profit_status = 'complete'
                  AND previous_profit_status = 'complete' THEN profit - previous_profit END AS profit_mom_change,
       CASE WHEN sales_mom_status = 'available' THEN (sales - previous_sales) / previous_sales END AS sales_mom_rate,
       CASE WHEN profit_mom_status = 'available' THEN (profit - previous_profit) / previous_profit END AS profit_mom_rate
FROM changes ORDER BY month NULLS LAST;
