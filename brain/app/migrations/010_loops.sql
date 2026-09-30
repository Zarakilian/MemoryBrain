-- v3.0.0: open loops are their own type. Consolidation used to store them as
-- notes tagged "open-loop"; move those rows over so the brief and the loop
-- closer see them.
UPDATE memories SET type = 'open_loop', tags = replace(tags, '"open-loop"', '"open_loop"')
WHERE type = 'note' AND tags LIKE '%"open-loop"%';
