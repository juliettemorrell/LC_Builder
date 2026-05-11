-- ============================================================================
-- MyAdvice Builder · Snowflake setup
--
-- Run this before pointing the Streamlit app at a real warehouse. It
-- creates only the OUTPUT tables the app needs (saved courses + saved
-- claims lessons). The INPUT tables (risk library, claim summaries, etc.)
-- are assumed to already exist — see data/cortex_setup_prompt.md for the
-- expected names + columns and how to override them with env vars when
-- they don't match.
--
-- Adjust the role name on the GRANT lines to match your deployment.
-- ============================================================================

USE DATABASE HACKATHON_DWH;
USE SCHEMA   ADVICE;

-- ----------------------------------------------------------------------------
-- Saved courses (course generator)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS GENERATED_COURSES (
    SAVE_ID    VARCHAR PRIMARY KEY,
    CREATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP,
    UPDATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP,
    TITLE      VARCHAR,
    DRIVER_ID  VARCHAR,
    PAYLOAD    VARIANT
);

-- ----------------------------------------------------------------------------
-- Saved claims lessons (claims lesson generator)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS GENERATED_LESSONS (
    SAVE_ID    VARCHAR PRIMARY KEY,
    CREATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP,
    UPDATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP,
    TITLE      VARCHAR,
    CLAIM_ID   VARCHAR,
    PAYLOAD    VARIANT
);

-- ----------------------------------------------------------------------------
-- Optional: chat-edit audit log. The app does NOT write this yet (chat
-- logging is a follow-on feature). Uncomment + run when wiring the
-- chat-logger module so we can audit what users edit.
-- ----------------------------------------------------------------------------
-- CREATE TABLE IF NOT EXISTS COURSE_EDIT_LOG (
--     LOG_ID       VARCHAR PRIMARY KEY,
--     SAVE_ID      VARCHAR,
--     SECTION_ID   VARCHAR,
--     OCCURRED_AT  TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP,
--     KIND         VARCHAR,         -- 'quick_action' | 'chat_edit' | 'regenerate'
--     INSTRUCTION  VARCHAR,
--     PROMPT       VARCHAR,         -- truncated to first 4 KB
--     BEFORE_TEXT  VARCHAR,
--     AFTER_TEXT   VARCHAR,
--     MODEL        VARCHAR,
--     TEMPERATURE  FLOAT,
--     LATENCY_MS   NUMBER
-- );

-- ----------------------------------------------------------------------------
-- Grants — adjust role names to match your environment
-- ----------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE GENERATED_COURSES TO ROLE ADVICE_BUILDATHON_ROLE;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE GENERATED_LESSONS TO ROLE ADVICE_BUILDATHON_ROLE;

-- ----------------------------------------------------------------------------
-- Sanity-check: confirm Cortex is callable. Should print 'OK'.
-- (Wrap in try/catch in your runner; if this fails the app falls back
--  to mock mode and the Tools → Connection panel will show "Mock".)
-- ----------------------------------------------------------------------------
SELECT SNOWFLAKE.CORTEX.COMPLETE(
    'claude-opus-4-7',
    'Reply with the single word: OK.',
    PARSE_JSON('{"max_tokens": 32000, "temperature": 0.0}')
) AS PING_RESULT;
