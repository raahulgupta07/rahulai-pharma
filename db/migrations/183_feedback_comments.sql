-- 183_feedback_comments.sql
-- Capture WHY behind a thumb: free-text comment, quick-pick tags, and an
-- optional user-supplied correction (the right answer / SQL). Lets 👎 carry a
-- training signal instead of a bare negative, and gives admin a review queue.
-- correction_status: 'pending' (default for any row with a correction),
-- 'promoted' (admin pushed correction → golden), 'dismissed' (admin rejected).
ALTER TABLE public.dash_feedback
    ADD COLUMN IF NOT EXISTS comment           TEXT,
    ADD COLUMN IF NOT EXISTS comment_tags      TEXT[],
    ADD COLUMN IF NOT EXISTS correction        TEXT,
    ADD COLUMN IF NOT EXISTS correction_status TEXT;

-- Review queue: open corrections, newest first.
CREATE INDEX IF NOT EXISTS idx_dash_feedback_correction_open
    ON public.dash_feedback (created_at DESC)
    WHERE correction IS NOT NULL AND correction_status = 'pending';
