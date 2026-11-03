-- Parameters are bound by the runner into analysis_parameters, never interpolated.
-- All rows originate from the normalized Excel detail frames, not Python totals.
SELECT o.*
FROM orders_input AS o
CROSS JOIN analysis_parameters AS p
WHERE (
    (o.order_date IS NULL AND p.include_undated)
    OR (o.order_date IS NOT NULL
        AND (p.start_date IS NULL OR o.order_date >= p.start_date)
        AND (p.end_date IS NULL OR o.order_date < p.end_date + INTERVAL '1 day'))
)
AND (p.regions IS NULL OR list_contains(p.regions, COALESCE(o.region, '(Unassigned)')))
AND (p.categories IS NULL OR list_contains(p.categories, COALESCE(o.category, '(Unassigned)')));
