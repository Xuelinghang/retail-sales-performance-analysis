-- Validate the entire lookup tables before either LEFT JOIN, even when filters
-- would not match a bad lookup key. No DISTINCT/deduplication repairs are made.
CREATE OR REPLACE TEMP VIEW enrichment_key_checks AS
SELECT 'People.Region' AS key_name, COUNT(*) AS row_count,
       COUNT(*) - COUNT(region) AS missing_key_rows,
       COUNT(region) - COUNT(DISTINCT region) AS duplicate_key_rows
FROM people_input
UNION ALL
SELECT 'Returns.Order ID', COUNT(*), COUNT(*) - COUNT(order_id),
       COUNT(order_id) - COUNT(DISTINCT order_id)
FROM returns_input;

SELECT CASE WHEN bool_and(missing_key_rows = 0 AND duplicate_key_rows = 0)
            THEN TRUE ELSE error('Unsafe enrichment keys: inspect join_key_checks.csv. No join was executed.') END AS keys_safe
FROM enrichment_key_checks;

SELECT o.*, p.regional_manager AS assigned_regional_manager,
       r.returned AS order_return_flag,
       p.region IS NOT NULL AS manager_matched,
       r.order_id IS NOT NULL AS return_record_matched
FROM orders_selected AS o
LEFT JOIN people_input AS p ON o.region = p.region
LEFT JOIN returns_input AS r ON o.order_id = r.order_id
ORDER BY o._source_row_number;
