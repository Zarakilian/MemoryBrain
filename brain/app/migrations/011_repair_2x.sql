-- v3.0.0: repair what 2.x left behind, once.

-- 1. 2.x closed a superseded memory without a validity end. Take the end from
--    the memory that replaced it, so as_of shows it as true until then
--    (an archived row with no end counts as deleted).
UPDATE memories
SET valid_to = (SELECT s.timestamp FROM memories s WHERE s.id = memories.superseded_by)
WHERE status = 'archived' AND superseded_by IS NOT NULL AND valid_to IS NULL
  AND EXISTS (SELECT 1 FROM memories s WHERE s.id = memories.superseded_by);

-- 2. 2.x wrote decay into the strength column. 3.0 computes decay from time when
--    a memory is read, so an idle 2.x memory would decay twice.
UPDATE memories SET strength = 1.0 WHERE strength < 1.0;

-- 3. 2.x saved its 3,500-character default into policy rows; 3.0's default is 6,000.
UPDATE project_policy SET max_brief_chars = 6000 WHERE max_brief_chars = 3500;
