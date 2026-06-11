-- CityPharma baseline schema — complete known-good DDL for a FRESH database.
-- Generated from the working reference DB. Load via scripts/init_fresh_db.sh.
-- Extensions first (pg_dump --schema filter omits them).
CREATE SCHEMA IF NOT EXISTS dash;
CREATE SCHEMA IF NOT EXISTS citypharma;
CREATE EXTENSION IF NOT EXISTS "vector" SCHEMA public;
CREATE EXTENSION IF NOT EXISTS "pgcrypto" SCHEMA dash;
CREATE EXTENSION IF NOT EXISTS "pg_trgm" SCHEMA citypharma;

--
-- PostgreSQL database dump
--

\restrict 7mCQm2rlrXfdkFhFKDGnzYlPuATnq005MEjseRsSjZEnluhZhzZ2H7sQ963B6Ke

-- Dumped from database version 18.4 (Debian 18.4-1.pgdg13+1)
-- Dumped by pg_dump version 18.4 (Debian 18.4-1.pgdg13+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: citypharma; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS citypharma;


--
-- Name: dash; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS dash;


--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS public;


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS 'standard public schema';


--
-- Name: sim_projects_set_updated_at(); Type: FUNCTION; Schema: dash; Owner: -
--

CREATE FUNCTION dash.sim_projects_set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;


--
-- Name: user_agents_set_updated_at(); Type: FUNCTION; Schema: dash; Owner: -
--

CREATE FUNCTION dash.user_agents_set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;


--
-- Name: refresh_mv_table_usage(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.refresh_mv_table_usage() RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- CONCURRENTLY needs the unique index above. Falls back to plain refresh.
    BEGIN
        REFRESH MATERIALIZED VIEW CONCURRENTLY public.mv_table_usage;
    EXCEPTION WHEN OTHERS THEN
        REFRESH MATERIALIZED VIEW public.mv_table_usage;
    END;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: articles_list_07052026; Type: TABLE; Schema: citypharma; Owner: -
--

CREATE TABLE citypharma.articles_list_07052026 (
    id bigint,
    article_code bigint,
    brand_name text,
    generic_name text,
    composition text,
    category text,
    mmreg text,
    mmlabel text,
    other text,
    indication text,
    dosage text,
    side_effect text,
    status bigint,
    created_at text,
    updated_at text
);


--
-- Name: balance_stock_07052026; Type: TABLE; Schema: citypharma; Owner: -
--

CREATE TABLE citypharma.balance_stock_07052026 (
    id text,
    site_code text,
    article_code text,
    stock_qty bigint,
    weighted_cost_price bigint,
    created_at text
);


--
-- Name: shop_flat; Type: TABLE; Schema: citypharma; Owner: -
--

CREATE TABLE citypharma.shop_flat (
    art_key text NOT NULL,
    site_code text NOT NULL,
    brand text,
    generic text,
    composition text,
    category text,
    stock_qty numeric DEFAULT 0 NOT NULL,
    cost numeric DEFAULT 0 NOT NULL,
    is_in_stock boolean DEFAULT false NOT NULL,
    linked boolean DEFAULT false NOT NULL,
    link_status text DEFAULT 'both'::text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: aos_capabilities; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.aos_capabilities (
    id bigint NOT NULL,
    name text NOT NULL,
    gated boolean DEFAULT true NOT NULL,
    default_on boolean DEFAULT false NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: aos_capabilities_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.aos_capabilities_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: aos_capabilities_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.aos_capabilities_id_seq OWNED BY dash.aos_capabilities.id;


--
-- Name: aos_cost_guard; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.aos_cost_guard (
    id integer NOT NULL,
    daily_budget numeric DEFAULT 200 NOT NULL,
    used_today numeric DEFAULT 0 NOT NULL,
    hard_stop_pct integer DEFAULT 90 NOT NULL,
    alert_pct integer DEFAULT 75 NOT NULL,
    alert_email text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: aos_cost_guard_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.aos_cost_guard_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: aos_cost_guard_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.aos_cost_guard_id_seq OWNED BY dash.aos_cost_guard.id;


--
-- Name: aos_kill_switch; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.aos_kill_switch (
    id integer NOT NULL,
    armed boolean DEFAULT true NOT NULL,
    last_changed_at timestamp with time zone DEFAULT now() NOT NULL,
    last_changed_by text
);


--
-- Name: aos_kill_switch_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.aos_kill_switch_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: aos_kill_switch_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.aos_kill_switch_id_seq OWNED BY dash.aos_kill_switch.id;


--
-- Name: aos_models; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.aos_models (
    id bigint NOT NULL,
    name text NOT NULL,
    role text,
    p95_ms integer,
    cost_per_m_in numeric,
    cost_per_m_out numeric,
    enabled boolean DEFAULT true NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: aos_models_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.aos_models_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: aos_models_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.aos_models_id_seq OWNED BY dash.aos_models.id;


--
-- Name: aos_tool_registry; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.aos_tool_registry (
    id bigint NOT NULL,
    tool_name text NOT NULL,
    owner text,
    enabled boolean DEFAULT true NOT NULL,
    calls_24h integer DEFAULT 0 NOT NULL,
    err_pct numeric DEFAULT 0 NOT NULL,
    avg_ms integer DEFAULT 0 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: aos_tool_registry_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.aos_tool_registry_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: aos_tool_registry_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.aos_tool_registry_id_seq OWNED BY dash.aos_tool_registry.id;


--
-- Name: dash_agent_schedule_runs; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_agent_schedule_runs (
    id bigint NOT NULL,
    schedule_id text NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    status text,
    response_excerpt text,
    cost_usd numeric,
    error text
);


--
-- Name: dash_agent_schedule_runs_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_agent_schedule_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_agent_schedule_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_agent_schedule_runs_id_seq OWNED BY dash.dash_agent_schedule_runs.id;


--
-- Name: dash_agent_schedules; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_agent_schedules (
    id text NOT NULL,
    project_slug text,
    created_by integer NOT NULL,
    created_by_agent text,
    name text NOT NULL,
    description text,
    schedule_kind text NOT NULL,
    cron_expr text,
    interval_seconds integer,
    next_run_at timestamp with time zone,
    prompt text NOT NULL,
    agent_target text DEFAULT 'leader'::text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    max_runs integer,
    run_count integer DEFAULT 0 NOT NULL,
    last_run_at timestamp with time zone,
    last_run_result text,
    last_run_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_agentic_state; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_agentic_state (
    id bigint NOT NULL,
    session_id text NOT NULL,
    agent_name text NOT NULL,
    key text NOT NULL,
    value jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_agentic_state_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_agentic_state_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_agentic_state_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_agentic_state_id_seq OWNED BY dash.dash_agentic_state.id;


--
-- Name: dash_anti_patterns; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_anti_patterns (
    id bigint NOT NULL,
    project_slug text,
    pattern text NOT NULL,
    why_bad text NOT NULL,
    example_failure text,
    confidence numeric(3,2) DEFAULT 0.8,
    source_dream_finding_id bigint,
    hit_count integer DEFAULT 0 NOT NULL,
    score_before numeric(3,2),
    score_after numeric(3,2),
    status text DEFAULT 'active'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    reverted_at timestamp with time zone
);


--
-- Name: dash_anti_patterns_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_anti_patterns_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_anti_patterns_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_anti_patterns_id_seq OWNED BY dash.dash_anti_patterns.id;


--
-- Name: dash_approval_audit; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_approval_audit (
    id bigint NOT NULL,
    request_id text,
    event text NOT NULL,
    actor_id integer,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_approval_audit_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_approval_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_approval_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_approval_audit_id_seq OWNED BY dash.dash_approval_audit.id;


--
-- Name: dash_approval_requests; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_approval_requests (
    id text NOT NULL,
    project_slug text,
    action_type text NOT NULL,
    resource_id text,
    payload jsonb NOT NULL,
    requested_by integer NOT NULL,
    required_approvers integer DEFAULT 1 NOT NULL,
    allowed_roles jsonb DEFAULT '["admin"]'::jsonb NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone DEFAULT (now() + '24:00:00'::interval) NOT NULL,
    resolved_at timestamp with time zone,
    execution_result jsonb
);


--
-- Name: dash_approval_signatures; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_approval_signatures (
    id bigint NOT NULL,
    request_id text NOT NULL,
    approver_id integer NOT NULL,
    decision text NOT NULL,
    reason text,
    signed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_approval_signatures_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_approval_signatures_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_approval_signatures_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_approval_signatures_id_seq OWNED BY dash.dash_approval_signatures.id;


--
-- Name: dash_attribution_credits; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_attribution_credits (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    conversion_id bigint NOT NULL,
    touchpoint_id bigint NOT NULL,
    model text NOT NULL,
    credit numeric NOT NULL,
    credited_revenue numeric,
    computed_at timestamp with time zone DEFAULT now()
);


--
-- Name: dash_attribution_credits_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_attribution_credits_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_attribution_credits_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_attribution_credits_id_seq OWNED BY dash.dash_attribution_credits.id;


--
-- Name: dash_auto_apply_history; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_auto_apply_history (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    vertical text,
    template text,
    confidence real,
    detection jsonb,
    applied boolean DEFAULT false,
    snapshot jsonb,
    applied_steps jsonb,
    error text,
    applied_by text,
    reverted boolean DEFAULT false,
    reverted_at timestamp with time zone,
    reverted_by text,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: dash_auto_apply_history_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_auto_apply_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_auto_apply_history_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_auto_apply_history_id_seq OWNED BY dash.dash_auto_apply_history.id;


--
-- Name: dash_autonomous_workflows; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_autonomous_workflows (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    template_name text,
    name text NOT NULL,
    description text,
    schedule text,
    query_template text,
    resolved_query text,
    expected_entity text,
    expected_columns jsonb DEFAULT '[]'::jsonb,
    action text,
    status text DEFAULT 'pending'::text NOT NULL,
    last_run_at timestamp with time zone,
    last_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    vertical_pack text,
    binding_resolved jsonb,
    schedule_cron text,
    schedule_action text DEFAULT 'post_insight'::text,
    schedule_email text,
    schedule_webhook text,
    max_cost_usd numeric(8,4) DEFAULT 0.50,
    daily_cap_usd numeric(8,4) DEFAULT 5.00,
    last_output jsonb,
    owner_user_id integer
);


--
-- Name: dash_autonomous_workflows_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_autonomous_workflows_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_autonomous_workflows_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_autonomous_workflows_id_seq OWNED BY dash.dash_autonomous_workflows.id;


--
-- Name: dash_autosim_runs; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_autosim_runs (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    scenario_source text NOT NULL,
    scenario jsonb NOT NULL,
    sim_project_id text,
    trigger_source text,
    trigger_user_id integer,
    status text DEFAULT 'queued'::text,
    cost_usd numeric(10,4) DEFAULT 0,
    error text,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: dash_autosim_runs_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_autosim_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_autosim_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_autosim_runs_id_seq OWNED BY dash.dash_autosim_runs.id;


--
-- Name: dash_brainbench_corpus; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_brainbench_corpus (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    session_id text NOT NULL,
    question text NOT NULL,
    expected_answer text,
    context_snapshot jsonb DEFAULT '{}'::jsonb NOT NULL,
    tools_called jsonb DEFAULT '[]'::jsonb NOT NULL,
    original_answer text,
    original_judge_score numeric(3,2),
    original_run_at timestamp with time zone,
    tags text[] DEFAULT ARRAY[]::text[],
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_brainbench_corpus_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_brainbench_corpus_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_brainbench_corpus_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_brainbench_corpus_id_seq OWNED BY dash.dash_brainbench_corpus.id;


--
-- Name: dash_campaign_events; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_campaign_events (
    id integer NOT NULL,
    campaign_id integer,
    event_type text NOT NULL,
    actor text,
    payload jsonb DEFAULT '{}'::jsonb,
    occurred_at timestamp with time zone DEFAULT now()
);


--
-- Name: dash_campaign_events_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_campaign_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_campaign_events_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_campaign_events_id_seq OWNED BY dash.dash_campaign_events.id;


--
-- Name: dash_campaigns; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_campaigns (
    id integer NOT NULL,
    project_slug text NOT NULL,
    name text NOT NULL,
    description text,
    type text DEFAULT 'manual'::text NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    target_segment text,
    target_filter jsonb DEFAULT '{}'::jsonb,
    audience_size integer DEFAULT 0,
    offer jsonb DEFAULT '{}'::jsonb,
    starts_at timestamp with time zone,
    ends_at timestamp with time zone,
    cost_budget numeric(12,2),
    created_by text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    metadata jsonb DEFAULT '{}'::jsonb
);


--
-- Name: dash_campaigns_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_campaigns_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_campaigns_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_campaigns_id_seq OWNED BY dash.dash_campaigns.id;


--
-- Name: dash_channel_messages; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_channel_messages (
    id bigint NOT NULL,
    thread_id text NOT NULL,
    direction text NOT NULL,
    external_msg_id text,
    author text,
    body text,
    attachments jsonb,
    agent_response_excerpt text,
    latency_ms integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_channel_messages_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_channel_messages_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_channel_messages_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_channel_messages_id_seq OWNED BY dash.dash_channel_messages.id;


--
-- Name: dash_channel_threads; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_channel_threads (
    id text NOT NULL,
    channel_kind text NOT NULL,
    external_id text NOT NULL,
    workspace_id text,
    channel_id text,
    project_slug text NOT NULL,
    dash_session_id text,
    external_user text,
    subject text,
    status text DEFAULT 'open'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_message_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_chemist_eval; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_chemist_eval (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    ran_at timestamp with time zone DEFAULT now() NOT NULL,
    passed integer,
    total integer,
    pct double precision,
    detail jsonb
);


--
-- Name: dash_chemist_eval_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_chemist_eval_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_chemist_eval_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_chemist_eval_id_seq OWNED BY dash.dash_chemist_eval.id;


--
-- Name: dash_compression_cache; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_compression_cache (
    cache_key text NOT NULL,
    url text NOT NULL,
    original_chars integer NOT NULL,
    compressed_chars integer NOT NULL,
    query_intent text,
    compressed_text text NOT NULL,
    model_used text,
    cost_usd numeric DEFAULT 0,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    hit_count integer DEFAULT 0 NOT NULL,
    last_hit_at timestamp with time zone
);


--
-- Name: dash_compression_stats; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_compression_stats (
    id bigint NOT NULL,
    project_slug text,
    user_id integer,
    run_id text,
    query text,
    raw_chars bigint,
    compressed_chars bigint,
    results_in integer,
    results_out integer,
    dedup_skipped integer,
    cost_usd numeric,
    latency_ms integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_compression_stats_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_compression_stats_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_compression_stats_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_compression_stats_id_seq OWNED BY dash.dash_compression_stats.id;


--
-- Name: dash_connection_audit; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_connection_audit (
    id bigint NOT NULL,
    connection_id uuid,
    user_id integer,
    action text NOT NULL,
    sql_text text,
    row_count integer,
    duration_ms integer,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_connection_audit_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_connection_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_connection_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_connection_audit_id_seq OWNED BY dash.dash_connection_audit.id;


--
-- Name: dash_connections; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_connections (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    connector_type text NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    credentials text NOT NULL,
    owner_user_id integer,
    enabled boolean DEFAULT true NOT NULL,
    allow_all_users boolean DEFAULT false NOT NULL,
    users_allowed jsonb DEFAULT '[]'::jsonb NOT NULL,
    ldap_groups_allowed jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    query_limit_per_day integer DEFAULT 1000,
    max_bytes_per_query bigint,
    secret_rotated_at timestamp with time zone DEFAULT now(),
    secret_rotation_alert_days integer DEFAULT 90,
    last_rotation_warning_at timestamp with time zone
);


--
-- Name: dash_conversions; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_conversions (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    customer_id text NOT NULL,
    transaction_id text,
    revenue numeric,
    converted_at timestamp with time zone NOT NULL,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: dash_conversions_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_conversions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_conversions_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_conversions_id_seq OWNED BY dash.dash_conversions.id;


--
-- Name: dash_correction_rules; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_correction_rules (
    id bigint NOT NULL,
    project_slug text,
    scope text DEFAULT 'project'::text NOT NULL,
    scope_target text,
    rule_text text NOT NULL,
    source_correction_id bigint,
    active boolean DEFAULT true NOT NULL,
    hit_count integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_correction_rules_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_correction_rules_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_correction_rules_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_correction_rules_id_seq OWNED BY dash.dash_correction_rules.id;


--
-- Name: dash_corrections; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_corrections (
    id bigint NOT NULL,
    project_slug text,
    run_id text,
    agent_name text,
    original_output text,
    edited_output text,
    diff_summary text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text
);


--
-- Name: dash_corrections_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_corrections_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_corrections_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_corrections_id_seq OWNED BY dash.dash_corrections.id;


--
-- Name: dash_custom_agents; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_custom_agents (
    id text NOT NULL,
    project_slug text,
    name text NOT NULL,
    description text,
    purpose text,
    base_agent text NOT NULL,
    agent_md text NOT NULL,
    scoped_skills jsonb DEFAULT '[]'::jsonb,
    scoped_tools jsonb DEFAULT '[]'::jsonb,
    persona text,
    extra_instructions text,
    created_by_agent text,
    created_by_user integer,
    source text DEFAULT 'spawned'::text,
    usage_count integer DEFAULT 0,
    last_used_at timestamp with time zone,
    success_rate numeric,
    enabled boolean DEFAULT true NOT NULL,
    is_promoted_global boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_customer_scores; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_customer_scores (
    id integer NOT NULL,
    project_slug text NOT NULL,
    customer_id text NOT NULL,
    rfm_segment text,
    rfm_r integer,
    rfm_f integer,
    rfm_m integer,
    clv_predicted numeric,
    churn_risk text,
    churn_score numeric,
    days_since_last numeric,
    order_count integer,
    total_spend numeric,
    avg_order_value numeric,
    last_computed timestamp with time zone DEFAULT now()
);


--
-- Name: dash_customer_scores_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_customer_scores_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_customer_scores_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_customer_scores_id_seq OWNED BY dash.dash_customer_scores.id;


--
-- Name: dash_daemon_leader; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_daemon_leader (
    id integer NOT NULL,
    holder text,
    heartbeat timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_deep_deck_gaps; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_deep_deck_gaps (
    id bigint NOT NULL,
    run_id bigint NOT NULL,
    rank integer NOT NULL,
    question text NOT NULL,
    rationale text,
    priority numeric(3,2),
    status text DEFAULT 'pending'::text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_deep_deck_gaps_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_deep_deck_gaps_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_deep_deck_gaps_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_deep_deck_gaps_id_seq OWNED BY dash.dash_deep_deck_gaps.id;


--
-- Name: dash_deep_deck_queries; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_deep_deck_queries (
    id bigint NOT NULL,
    run_id bigint NOT NULL,
    gap_id bigint,
    rank integer,
    question text NOT NULL,
    sql_text text NOT NULL,
    expected_shape text,
    status text DEFAULT 'pending'::text,
    row_count integer,
    columns jsonb,
    rows_preview jsonb,
    error_text text,
    duration_ms integer,
    executed_at timestamp with time zone
);


--
-- Name: dash_deep_deck_queries_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_deep_deck_queries_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_deep_deck_queries_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_deep_deck_queries_id_seq OWNED BY dash.dash_deep_deck_queries.id;


--
-- Name: dash_deep_deck_runs; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_deep_deck_runs (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    user_id integer,
    session_id text,
    status text DEFAULT 'running'::text NOT NULL,
    current_stage text,
    stage_progress integer DEFAULT 0,
    pres_id bigint,
    cost_usd numeric(10,4) DEFAULT 0,
    error_text text,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone
);


--
-- Name: dash_deep_deck_runs_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_deep_deck_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_deep_deck_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_deep_deck_runs_id_seq OWNED BY dash.dash_deep_deck_runs.id;


--
-- Name: dash_dp_budget; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_dp_budget (
    project_slug text NOT NULL,
    user_id integer NOT NULL,
    date date NOT NULL,
    budget_used numeric(8,4) DEFAULT 0 NOT NULL,
    budget_max numeric(8,4) DEFAULT 10.0 NOT NULL
);


--
-- Name: dash_dream_findings; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_dream_findings (
    id bigint NOT NULL,
    run_id bigint,
    project_slug text NOT NULL,
    finding_type text NOT NULL,
    content jsonb NOT NULL,
    confidence numeric(3,2) NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    target_table text,
    target_id bigint,
    finding_hash text NOT NULL,
    source_session_ids text[],
    reviewed_by text,
    reviewed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_dream_findings_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_dream_findings_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_dream_findings_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_dream_findings_id_seq OWNED BY dash.dash_dream_findings.id;


--
-- Name: dash_dream_lite_runs; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_dream_lite_runs (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    user_id integer,
    session_id text NOT NULL,
    triggered_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    trigger_reason text,
    episodes_consumed integer DEFAULT 0,
    persona_updated boolean DEFAULT false,
    precompute_queued integer DEFAULT 0,
    memory_ops_applied integer DEFAULT 0,
    cost_usd numeric(10,4) DEFAULT 0,
    status text DEFAULT 'running'::text,
    error text,
    is_bootstrap boolean DEFAULT false,
    friendly_status text
);


--
-- Name: dash_dream_lite_runs_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_dream_lite_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_dream_lite_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_dream_lite_runs_id_seq OWNED BY dash.dash_dream_lite_runs.id;


--
-- Name: dash_dream_precompute_cache; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_dream_precompute_cache (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    user_id integer,
    question_hash text NOT NULL,
    question_text text NOT NULL,
    sql text,
    result_json jsonb,
    result_summary text,
    ttl_until timestamp with time zone NOT NULL,
    hit_count integer DEFAULT 0 NOT NULL,
    last_hit_at timestamp with time zone,
    source_session_id text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_dream_precompute_cache_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_dream_precompute_cache_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_dream_precompute_cache_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_dream_precompute_cache_id_seq OWNED BY dash.dash_dream_precompute_cache.id;


--
-- Name: dash_dream_runs; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_dream_runs (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    sessions_scanned integer DEFAULT 0,
    findings_count integer DEFAULT 0,
    cost_usd numeric(10,4) DEFAULT 0,
    status text DEFAULT 'running'::text NOT NULL,
    error text,
    window_hours integer DEFAULT 24,
    trigger text DEFAULT 'cron'::text
);


--
-- Name: dash_dream_runs_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_dream_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_dream_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_dream_runs_id_seq OWNED BY dash.dash_dream_runs.id;


--
-- Name: dash_email_accounts; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_email_accounts (
    id text NOT NULL,
    name text NOT NULL,
    inbound_kind text NOT NULL,
    imap_host text,
    imap_port integer,
    imap_user text,
    imap_pass text,
    smtp_host text,
    smtp_port integer,
    smtp_user text,
    smtp_pass text,
    default_project_slug text,
    subject_prefix_pattern text DEFAULT '^\\[([a-z0-9_-]+)\\]'::text,
    enabled boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_entities; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_entities (
    id bigint NOT NULL,
    project_slug text,
    kind text NOT NULL,
    name text NOT NULL,
    name_normalized text NOT NULL,
    aliases text[] DEFAULT ARRAY[]::text[],
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_entities_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_entities_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_entities_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_entities_id_seq OWNED BY dash.dash_entities.id;


--
-- Name: dash_entity_links; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_entity_links (
    id bigint NOT NULL,
    project_slug text,
    src_entity_id bigint NOT NULL,
    rel text NOT NULL,
    dst_entity_id bigint NOT NULL,
    source_ref text,
    confidence real DEFAULT 1.0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_entity_links_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_entity_links_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_entity_links_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_entity_links_id_seq OWNED BY dash.dash_entity_links.id;


--
-- Name: dash_entity_memory; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_entity_memory (
    id bigint NOT NULL,
    project_slug text,
    entity_type text NOT NULL,
    entity_id text NOT NULL,
    fact text NOT NULL,
    fact_kind text DEFAULT 'observation'::text,
    confidence numeric DEFAULT 0.7,
    source text,
    source_run_id text,
    embedding public.vector(1536),
    metadata jsonb,
    created_by integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone,
    archived boolean DEFAULT false NOT NULL
);


--
-- Name: dash_entity_memory_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_entity_memory_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_entity_memory_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_entity_memory_id_seq OWNED BY dash.dash_entity_memory.id;


--
-- Name: dash_episode_buffer; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_episode_buffer (
    id bigint NOT NULL,
    session_id text NOT NULL,
    turn_id integer,
    project_slug text NOT NULL,
    user_id integer,
    poignancy integer DEFAULT 1 NOT NULL,
    question text,
    response_summary text,
    tools_used text[],
    succeeded boolean DEFAULT true,
    judge_score numeric(3,2),
    user_reaction text,
    embedding text,
    consumed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_episode_buffer_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_episode_buffer_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_episode_buffer_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_episode_buffer_id_seq OWNED BY dash.dash_episode_buffer.id;


--
-- Name: dash_eval_baselines; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_eval_baselines (
    id bigint NOT NULL,
    suite_id text NOT NULL,
    pass_rate numeric NOT NULL,
    avg_latency_ms numeric,
    set_at timestamp with time zone DEFAULT now() NOT NULL,
    set_by integer,
    source_run_id text,
    notes text
);


--
-- Name: dash_eval_baselines_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_eval_baselines_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_eval_baselines_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_eval_baselines_id_seq OWNED BY dash.dash_eval_baselines.id;


--
-- Name: dash_eval_cases; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_eval_cases (
    id text NOT NULL,
    suite_id text NOT NULL,
    name text NOT NULL,
    input_prompt text NOT NULL,
    expected_output text,
    expected_tool_calls jsonb,
    judge_prompt text,
    max_latency_ms integer,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expected_sql text,
    expected_dialect text DEFAULT 'postgres'::text,
    grading_mode text DEFAULT 'llm_judge'::text,
    generated_sql_hint text
);


--
-- Name: dash_eval_results; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_eval_results (
    id bigint NOT NULL,
    run_id text NOT NULL,
    case_id text NOT NULL,
    case_name text,
    status text NOT NULL,
    score numeric,
    actual_output text,
    judge_reason text,
    tool_calls_observed jsonb,
    latency_ms integer,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_eval_results_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_eval_results_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_eval_results_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_eval_results_id_seq OWNED BY dash.dash_eval_results.id;


--
-- Name: dash_eval_runs; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_eval_runs (
    id text NOT NULL,
    suite_id text NOT NULL,
    triggered_by integer,
    status text DEFAULT 'running'::text NOT NULL,
    total_cases integer DEFAULT 0 NOT NULL,
    passed integer DEFAULT 0 NOT NULL,
    failed integer DEFAULT 0 NOT NULL,
    pass_rate numeric,
    avg_latency_ms numeric,
    cost_usd numeric DEFAULT 0,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    notes text
);


--
-- Name: dash_eval_suites; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_eval_suites (
    id text NOT NULL,
    project_slug text,
    name text NOT NULL,
    description text,
    layer text NOT NULL,
    target_agent text,
    is_builtin boolean DEFAULT false NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_generated_files; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_generated_files (
    id text NOT NULL,
    project_slug text,
    user_id integer,
    agent_name text,
    run_id text,
    file_type text NOT NULL,
    filename text NOT NULL,
    storage_path text NOT NULL,
    size_bytes bigint,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone DEFAULT (now() + '7 days'::interval),
    kind text,
    thumbnail bytea,
    deleted_at timestamp with time zone
);


--
-- Name: dash_hitl_pending; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_hitl_pending (
    run_id text NOT NULL,
    project_slug text,
    user_id integer,
    agent_name text NOT NULL,
    action_type text NOT NULL,
    payload jsonb NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    response jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone DEFAULT (now() + '00:05:00'::interval) NOT NULL,
    responded_at timestamp with time zone,
    responded_by integer
);


--
-- Name: dash_hook_audit; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_hook_audit (
    id bigint NOT NULL,
    hook_name text NOT NULL,
    hook_kind text NOT NULL,
    tool_name text NOT NULL,
    agent_name text,
    project_slug text,
    user_id integer,
    run_id text,
    decision text,
    reason text,
    latency_ms integer,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_hook_audit_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_hook_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_hook_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_hook_audit_id_seq OWNED BY dash.dash_hook_audit.id;


--
-- Name: dash_investment_memos; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_investment_memos (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    symbol text NOT NULL,
    verdict text NOT NULL,
    conviction integer NOT NULL,
    body_md text,
    analysts_consulted text[],
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by_agent text,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT dash_investment_memos_conviction_check CHECK (((conviction >= 1) AND (conviction <= 5))),
    CONSTRAINT dash_investment_memos_verdict_check CHECK ((verdict = ANY (ARRAY['BUY'::text, 'HOLD'::text, 'PASS'::text, 'SELL'::text])))
);


--
-- Name: dash_investment_memos_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_investment_memos_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_investment_memos_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_investment_memos_id_seq OWNED BY dash.dash_investment_memos.id;


--
-- Name: dash_investment_runs; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_investment_runs (
    id bigint NOT NULL,
    project_slug text,
    symbol text,
    team_pattern text,
    status text,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    memo_id bigint,
    events jsonb DEFAULT '[]'::jsonb NOT NULL,
    error text,
    CONSTRAINT dash_investment_runs_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'done'::text, 'failed'::text]))),
    CONSTRAINT dash_investment_runs_team_pattern_check CHECK ((team_pattern = ANY (ARRAY['coordinate'::text, 'route'::text, 'broadcast'::text, 'task'::text, 'pipeline'::text])))
);


--
-- Name: dash_investment_runs_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_investment_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_investment_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_investment_runs_id_seq OWNED BY dash.dash_investment_runs.id;


--
-- Name: dash_knowledge_triples; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_knowledge_triples (
    id bigint NOT NULL,
    project_slug text,
    subject text NOT NULL,
    predicate text NOT NULL,
    object text NOT NULL,
    confidence numeric(3,2) DEFAULT 0.5,
    source_type text,
    source_id text,
    source_uri text,
    valid_at timestamp with time zone,
    invalid_at timestamp with time zone,
    expired_at timestamp with time zone,
    superseded_by bigint,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_knowledge_triples_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_knowledge_triples_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_knowledge_triples_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_knowledge_triples_id_seq OWNED BY dash.dash_knowledge_triples.id;


--
-- Name: dash_links; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_links (
    src_type text NOT NULL,
    src_id text NOT NULL,
    dst_type text NOT NULL,
    dst_id text NOT NULL,
    rel text NOT NULL,
    project_slug text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_llm_keys; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_llm_keys (
    id integer NOT NULL,
    key_label text NOT NULL,
    encrypted_key text NOT NULL,
    key_suffix text NOT NULL,
    provider text DEFAULT 'openrouter'::text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    created_by integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_used_at timestamp with time zone,
    notes text
);


--
-- Name: dash_llm_keys_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_llm_keys_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_llm_keys_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_llm_keys_id_seq OWNED BY dash.dash_llm_keys.id;


--
-- Name: dash_llm_model_catalog; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_llm_model_catalog (
    id text NOT NULL,
    name text NOT NULL,
    provider text NOT NULL,
    description text,
    context_length integer,
    pricing_prompt numeric,
    pricing_completion numeric,
    modalities jsonb DEFAULT '[]'::jsonb,
    supported_params jsonb DEFAULT '[]'::jsonb,
    top_provider jsonb,
    is_free boolean GENERATED ALWAYS AS ((pricing_prompt = (0)::numeric)) STORED,
    raw jsonb,
    synced_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_mcp_invocations; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_mcp_invocations (
    id bigint NOT NULL,
    server_id text,
    tool_name text NOT NULL,
    project_slug text,
    user_id integer,
    run_id text,
    args jsonb,
    result jsonb,
    latency_ms integer,
    status text,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_mcp_invocations_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_mcp_invocations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_mcp_invocations_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_mcp_invocations_id_seq OWNED BY dash.dash_mcp_invocations.id;


--
-- Name: dash_mcp_servers; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_mcp_servers (
    id text NOT NULL,
    project_slug text,
    name text NOT NULL,
    transport text NOT NULL,
    url text,
    command text,
    args jsonb DEFAULT '[]'::jsonb,
    env jsonb DEFAULT '{}'::jsonb,
    auth_header text,
    enabled boolean DEFAULT true NOT NULL,
    status text DEFAULT 'unknown'::text NOT NULL,
    discovered_tools jsonb DEFAULT '[]'::jsonb,
    last_health_at timestamp with time zone,
    last_error text,
    created_by integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_mcp_tool_bindings; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_mcp_tool_bindings (
    id bigint NOT NULL,
    server_id text NOT NULL,
    agent_name text NOT NULL,
    tool_name text NOT NULL,
    enabled boolean DEFAULT true NOT NULL
);


--
-- Name: dash_mcp_tool_bindings_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_mcp_tool_bindings_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_mcp_tool_bindings_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_mcp_tool_bindings_id_seq OWNED BY dash.dash_mcp_tool_bindings.id;


--
-- Name: dash_minions; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_minions (
    id bigint NOT NULL,
    project_slug text,
    kind text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    priority integer DEFAULT 5 NOT NULL,
    claimed_by text,
    lease_until timestamp with time zone,
    attempts integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 3 NOT NULL,
    result jsonb,
    error text,
    scheduled_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT dash_minions_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'done'::text, 'failed'::text, 'cancelled'::text])))
);


--
-- Name: dash_minions_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_minions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_minions_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_minions_id_seq OWNED BY dash.dash_minions.id;


--
-- Name: dash_packs; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_packs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    version text,
    manifest jsonb DEFAULT '{}'::jsonb NOT NULL,
    author text,
    source_path text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_page_evidence; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_page_evidence (
    id bigint NOT NULL,
    page_id bigint NOT NULL,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    source text,
    source_ref text,
    content text NOT NULL,
    author text
);


--
-- Name: dash_page_evidence_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_page_evidence_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_page_evidence_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_page_evidence_id_seq OWNED BY dash.dash_page_evidence.id;


--
-- Name: dash_pages; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_pages (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    page_key text NOT NULL,
    title text,
    compiled_truth text,
    compiled_at timestamp with time zone,
    compiled_by text,
    content_hash text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_pages_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_pages_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_pages_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_pages_id_seq OWNED BY dash.dash_pages.id;


--
-- Name: dash_pii_audit; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_pii_audit (
    id bigint NOT NULL,
    project_slug text,
    user_id integer,
    text_snippet text,
    detected_types text[],
    action_taken text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_pii_audit_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_pii_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_pii_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_pii_audit_id_seq OWNED BY dash.dash_pii_audit.id;


--
-- Name: dash_project_rls_config; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_project_rls_config (
    project_slug text NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    mode text DEFAULT 'advisory'::text NOT NULL,
    user_attr_keys text[] DEFAULT '{}'::text[] NOT NULL,
    table_filters jsonb DEFAULT '{}'::jsonb NOT NULL,
    default_deny boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    bypass_roles jsonb DEFAULT '["admin", "super_admin"]'::jsonb NOT NULL,
    CONSTRAINT dash_project_rls_config_mode_check CHECK ((mode = ANY (ARRAY['advisory'::text, 'rewrite'::text, 'pg_rls'::text])))
);


--
-- Name: TABLE dash_project_rls_config; Type: COMMENT; Schema: dash; Owner: -
--

COMMENT ON TABLE dash.dash_project_rls_config IS 'Per-agent (per-project) RLS config. mode: advisory=LLM-only, rewrite=SQL injection, pg_rls=Postgres policies.';


--
-- Name: COLUMN dash_project_rls_config.user_attr_keys; Type: COMMENT; Schema: dash; Owner: -
--

COMMENT ON COLUMN dash.dash_project_rls_config.user_attr_keys IS 'Attribute keys passed in via embed user_attrs (e.g. [store_id, region]).';


--
-- Name: COLUMN dash_project_rls_config.table_filters; Type: COMMENT; Schema: dash; Owner: -
--

COMMENT ON COLUMN dash.dash_project_rls_config.table_filters IS 'Per-table filter expressions: {"sales": "store_id = :store_id"}. Bind vars match user_attr_keys.';


--
-- Name: dash_refusal_marks; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_refusal_marks (
    id bigint NOT NULL,
    session_id text NOT NULL,
    question_hash text NOT NULL,
    question_preview text,
    refused_at timestamp with time zone DEFAULT now() NOT NULL,
    source text NOT NULL,
    reason text
);


--
-- Name: dash_refusal_marks_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_refusal_marks_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_refusal_marks_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_refusal_marks_id_seq OWNED BY dash.dash_refusal_marks.id;


--
-- Name: dash_rls_audit; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_rls_audit (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    user_attrs jsonb,
    external_user text,
    embed_id text,
    original_sql text NOT NULL,
    rewritten_sql text,
    mode text,
    blocked boolean DEFAULT false,
    block_reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE dash_rls_audit; Type: COMMENT; Schema: dash; Owner: -
--

COMMENT ON TABLE dash.dash_rls_audit IS 'Per-call audit of RLS rewrites/blocks. Sampled 1-in-N for non-blocked rewrites to control volume.';


--
-- Name: dash_rls_audit_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_rls_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_rls_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_rls_audit_id_seq OWNED BY dash.dash_rls_audit.id;


--
-- Name: dash_run_context_audit; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_run_context_audit (
    id bigint NOT NULL,
    run_id text NOT NULL,
    project_slug text,
    user_id integer,
    agent_name text,
    scope_id text,
    query_intent text,
    user_attrs jsonb,
    external_user text,
    trigger_kind text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_run_context_audit_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_run_context_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_run_context_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_run_context_audit_id_seq OWNED BY dash.dash_run_context_audit.id;


--
-- Name: dash_search_log; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_search_log (
    id bigint NOT NULL,
    project_slug text,
    query text,
    mode text,
    n_results integer,
    latency_ms integer,
    ts timestamp with time zone DEFAULT now()
);


--
-- Name: dash_search_log_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_search_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_search_log_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_search_log_id_seq OWNED BY dash.dash_search_log.id;


--
-- Name: dash_secret_leaks; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_secret_leaks (
    id bigint NOT NULL,
    agent_name text,
    tool_name text,
    project_slug text,
    user_id integer,
    run_id text,
    pattern_matched text NOT NULL,
    match_excerpt text,
    action text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_secret_leaks_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_secret_leaks_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_secret_leaks_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_secret_leaks_id_seq OWNED BY dash.dash_secret_leaks.id;


--
-- Name: dash_segment_snapshots; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_segment_snapshots (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    captured_at timestamp with time zone DEFAULT now(),
    rfm_distribution jsonb NOT NULL,
    churn_distribution jsonb NOT NULL,
    total_customers integer,
    metadata jsonb
);


--
-- Name: dash_segment_snapshots_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_segment_snapshots_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_segment_snapshots_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_segment_snapshots_id_seq OWNED BY dash.dash_segment_snapshots.id;


--
-- Name: dash_sim_recommendations; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_sim_recommendations (
    id bigint NOT NULL,
    source text NOT NULL,
    vertical text,
    title text NOT NULL,
    scenario_template jsonb NOT NULL,
    run_count integer DEFAULT 0,
    unique_tenants integer DEFAULT 0,
    last_recommended_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: dash_sim_recommendations_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_sim_recommendations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_sim_recommendations_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_sim_recommendations_id_seq OWNED BY dash.dash_sim_recommendations.id;


--
-- Name: dash_skill_audit_log; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_skill_audit_log (
    id bigint NOT NULL,
    skill_name text NOT NULL,
    project_slug text NOT NULL,
    candidate_sql text,
    audit_result jsonb DEFAULT '{}'::jsonb NOT NULL,
    passed boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_skill_audit_log_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_skill_audit_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_skill_audit_log_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_skill_audit_log_id_seq OWNED BY dash.dash_skill_audit_log.id;


--
-- Name: dash_skill_bindings; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_skill_bindings (
    id bigint NOT NULL,
    skill_id text NOT NULL,
    agent_name text NOT NULL,
    enabled boolean DEFAULT true NOT NULL
);


--
-- Name: dash_skill_bindings_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_skill_bindings_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_skill_bindings_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_skill_bindings_id_seq OWNED BY dash.dash_skill_bindings.id;


--
-- Name: dash_skill_drafts; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_skill_drafts (
    id text NOT NULL,
    project_slug text,
    source_run_id text,
    source_conversation_excerpt text,
    drafted_by_agent text,
    trigger_phrase text,
    iteration integer DEFAULT 1,
    proposed_name text,
    proposed_description text,
    proposed_skill_md text NOT NULL,
    frontmatter jsonb,
    verifier_results jsonb,
    status text DEFAULT 'pending'::text NOT NULL,
    rejection_reason text,
    promoted_skill_id text,
    approved_by integer,
    approved_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_skill_invocations; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_skill_invocations (
    id bigint NOT NULL,
    skill_id text NOT NULL,
    agent_name text,
    project_slug text,
    user_id integer,
    run_id text,
    trigger_phrase text,
    loaded_chars integer,
    latency_ms integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_skill_invocations_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_skill_invocations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_skill_invocations_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_skill_invocations_id_seq OWNED BY dash.dash_skill_invocations.id;


--
-- Name: dash_skill_marketplace; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_skill_marketplace (
    id bigint NOT NULL,
    name text NOT NULL,
    description text NOT NULL,
    sql_template text NOT NULL,
    params_schema jsonb DEFAULT '{}'::jsonb NOT NULL,
    template_name text NOT NULL,
    source_project_slug text,
    nominator_user_id integer,
    avg_judge_score numeric(3,2),
    source_success_count integer DEFAULT 0 NOT NULL,
    install_count integer DEFAULT 0 NOT NULL,
    total_installs_succeeded integer DEFAULT 0 NOT NULL,
    total_installs_failed integer DEFAULT 0 NOT NULL,
    description_embedding text,
    status text DEFAULT 'active'::text NOT NULL,
    tags text[] DEFAULT ARRAY[]::text[] NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_skill_marketplace_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_skill_marketplace_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_skill_marketplace_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_skill_marketplace_id_seq OWNED BY dash.dash_skill_marketplace.id;


--
-- Name: dash_skills; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_skills (
    id text NOT NULL,
    project_slug text,
    name text NOT NULL,
    category text,
    description text,
    trigger_keywords jsonb DEFAULT '[]'::jsonb,
    instructions text NOT NULL,
    tools jsonb DEFAULT '[]'::jsonb,
    is_builtin boolean DEFAULT false NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    created_by integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    runtime_role text DEFAULT 'agent_hint'::text NOT NULL
);


--
-- Name: dash_slack_channel_routes; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_slack_channel_routes (
    id bigint NOT NULL,
    workspace_id text NOT NULL,
    channel_id text NOT NULL,
    project_slug text NOT NULL,
    enabled boolean DEFAULT true NOT NULL
);


--
-- Name: dash_slack_channel_routes_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_slack_channel_routes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_slack_channel_routes_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_slack_channel_routes_id_seq OWNED BY dash.dash_slack_channel_routes.id;


--
-- Name: dash_slack_workspaces; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_slack_workspaces (
    id text NOT NULL,
    team_id text NOT NULL,
    team_name text,
    default_project_slug text,
    bot_token text NOT NULL,
    bot_user_id text,
    signing_secret text,
    enabled boolean DEFAULT true NOT NULL,
    installed_by integer,
    installed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_slide_critique; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_slide_critique (
    id bigint NOT NULL,
    pres_id bigint NOT NULL,
    slide_idx integer NOT NULL,
    pass_num integer NOT NULL,
    score numeric(3,2),
    weaknesses jsonb,
    suggested_fix text,
    accepted boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_slide_critique_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_slide_critique_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_slide_critique_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_slide_critique_id_seq OWNED BY dash.dash_slide_critique.id;


--
-- Name: dash_slide_templates; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_slide_templates (
    id bigint NOT NULL,
    project_slug text,
    name text NOT NULL,
    pptx_bytes bytea,
    config jsonb NOT NULL,
    created_by integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_slide_templates_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_slide_templates_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_slide_templates_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_slide_templates_id_seq OWNED BY dash.dash_slide_templates.id;


--
-- Name: dash_sql_validator_events; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_sql_validator_events (
    id bigint NOT NULL,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    project_slug text,
    kind text NOT NULL,
    source text,
    table_name text,
    details jsonb,
    CONSTRAINT chk_kind CHECK ((kind = ANY (ARRAY['auto_fix'::text, 'qa_drop'::text, 'chat_autofix'::text, 'reject'::text])))
);


--
-- Name: dash_sql_validator_events_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_sql_validator_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_sql_validator_events_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_sql_validator_events_id_seq OWNED BY dash.dash_sql_validator_events.id;


--
-- Name: dash_subagent_runs; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_subagent_runs (
    id bigint NOT NULL,
    agent_id text,
    agent_name text,
    parent_run_id text,
    spawned_by_agent text,
    project_slug text,
    scoped_skills_used jsonb,
    scoped_tools_used jsonb,
    input_brief text,
    output text,
    status text,
    latency_ms integer,
    cost_usd numeric,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_subagent_runs_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_subagent_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_subagent_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_subagent_runs_id_seq OWNED BY dash.dash_subagent_runs.id;


--
-- Name: dash_subscription_snapshots; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_subscription_snapshots (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    captured_at timestamp with time zone DEFAULT now(),
    period_start date NOT NULL,
    period_end date NOT NULL,
    mrr numeric,
    arr numeric,
    new_mrr numeric,
    expansion_mrr numeric,
    contraction_mrr numeric,
    churn_mrr numeric,
    reactivation_mrr numeric,
    net_new_mrr numeric,
    gross_retention_pct numeric,
    net_retention_pct numeric,
    active_subscribers integer,
    churned_subscribers integer,
    metadata jsonb
);


--
-- Name: dash_subscription_snapshots_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_subscription_snapshots_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_subscription_snapshots_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_subscription_snapshots_id_seq OWNED BY dash.dash_subscription_snapshots.id;


--
-- Name: dash_template_bindings; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_template_bindings (
    project_slug text NOT NULL,
    template_ref text NOT NULL,
    real_ref text,
    status text DEFAULT 'unbound'::text NOT NULL,
    match_method text,
    confidence numeric,
    reconciled_at timestamp with time zone DEFAULT now()
);


--
-- Name: dash_template_expectations; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_template_expectations (
    project_slug text NOT NULL,
    template_name text NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL,
    expectations jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: dash_tool_utility_scores; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_tool_utility_scores (
    id bigint NOT NULL,
    tool_name text NOT NULL,
    project_slug text,
    calls_30d integer DEFAULT 0 NOT NULL,
    success_30d integer DEFAULT 0 NOT NULL,
    avg_latency_ms numeric(10,2),
    score numeric(5,2),
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_tool_utility_scores_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_tool_utility_scores_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_tool_utility_scores_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_tool_utility_scores_id_seq OWNED BY dash.dash_tool_utility_scores.id;


--
-- Name: dash_touchpoints; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_touchpoints (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    customer_id text NOT NULL,
    channel text NOT NULL,
    campaign_id bigint,
    event_type text NOT NULL,
    event_at timestamp with time zone NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: dash_touchpoints_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_touchpoints_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_touchpoints_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_touchpoints_id_seq OWNED BY dash.dash_touchpoints.id;


--
-- Name: dash_vector_audit; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_vector_audit (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    op text NOT NULL,
    query text,
    scope_attrs jsonb,
    rows_returned integer,
    latency_ms integer,
    ts timestamp with time zone DEFAULT now()
);


--
-- Name: dash_vector_audit_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_vector_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_vector_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_vector_audit_id_seq OWNED BY dash.dash_vector_audit.id;


--
-- Name: dash_vectors; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_vectors (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    namespace text DEFAULT 'docs'::text NOT NULL,
    source_id text NOT NULL,
    source_table text,
    scope_attrs jsonb DEFAULT '{}'::jsonb NOT NULL,
    text text NOT NULL,
    text_hash text NOT NULL,
    embedding public.vector(1536) NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('english'::regconfig, text)) STORED,
    updated_at timestamp with time zone DEFAULT now(),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    tenant_namespace text DEFAULT 'default'::text NOT NULL
);


--
-- Name: dash_vectors_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_vectors_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_vectors_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_vectors_id_seq OWNED BY dash.dash_vectors.id;


--
-- Name: dash_venture_competitors; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_venture_competitors (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    deal_id uuid NOT NULL,
    name text NOT NULL,
    share_pct numeric,
    moat text,
    source text
);


--
-- Name: dash_venture_deals; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_venture_deals (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    project_slug text NOT NULL,
    name text NOT NULL,
    stage text,
    sector text,
    geography text,
    ask_amount numeric,
    pre_money numeric,
    post_money numeric,
    status text DEFAULT 'screening'::text,
    created_by integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_venture_scenarios; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_venture_scenarios (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    deal_id uuid NOT NULL,
    name text NOT NULL,
    irr numeric,
    moic numeric,
    payback_yrs numeric,
    npv numeric,
    inputs jsonb DEFAULT '{}'::jsonb NOT NULL,
    verdict text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_venture_verdict_drift; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_venture_verdict_drift (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    scenario_id uuid NOT NULL,
    deal_id uuid NOT NULL,
    project_slug text NOT NULL,
    old_verdict text,
    new_verdict text,
    old_irr numeric,
    new_irr numeric,
    old_npv numeric,
    new_npv numeric,
    detected_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_vertical_pack_history; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_vertical_pack_history (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    pack_name text NOT NULL,
    score numeric,
    workflows_installed integer,
    workflows_skipped integer,
    installed_at timestamp with time zone DEFAULT now(),
    installed_by integer
);


--
-- Name: dash_vertical_pack_history_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_vertical_pack_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_vertical_pack_history_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_vertical_pack_history_id_seq OWNED BY dash.dash_vertical_pack_history.id;


--
-- Name: dash_voice_numbers; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_voice_numbers (
    id text NOT NULL,
    phone_number text NOT NULL,
    provider text DEFAULT 'twilio'::text NOT NULL,
    account_sid text,
    auth_token text,
    default_project_slug text,
    tts_voice text DEFAULT 'Rachel'::text,
    enabled boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_workflow_defs; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_workflow_defs (
    id text NOT NULL,
    project_slug text,
    name text NOT NULL,
    description text,
    category text,
    spec jsonb NOT NULL,
    is_builtin boolean DEFAULT false NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    trigger_kind text DEFAULT 'manual'::text NOT NULL,
    cron_expr text,
    created_by integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_workflow_run_history; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_workflow_run_history (
    run_id text NOT NULL,
    workflow_id bigint NOT NULL,
    project_slug text NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    duration_ms integer,
    status text NOT NULL,
    steps_completed integer DEFAULT 0,
    steps_total integer DEFAULT 0,
    cost_usd numeric(8,4) DEFAULT 0,
    output jsonb,
    error text,
    triggered_by text DEFAULT 'cron'::text,
    source text,
    enqueued_at timestamp with time zone,
    dashboard_id text,
    owner_user_id integer
);


--
-- Name: dash_workflow_run_steps; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_workflow_run_steps (
    id bigint NOT NULL,
    run_id text NOT NULL,
    step_id text NOT NULL,
    step_kind text NOT NULL,
    iter integer DEFAULT 0,
    status text NOT NULL,
    input jsonb,
    output jsonb,
    error text,
    latency_ms integer,
    cost_usd numeric DEFAULT 0,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone
);


--
-- Name: dash_workflow_run_steps_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_workflow_run_steps_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_workflow_run_steps_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_workflow_run_steps_id_seq OWNED BY dash.dash_workflow_run_steps.id;


--
-- Name: dash_workflow_runs; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_workflow_runs (
    id text NOT NULL,
    def_id text NOT NULL,
    project_slug text,
    triggered_by integer,
    trigger_kind text DEFAULT 'manual'::text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    input_payload jsonb,
    output_payload jsonb,
    error text,
    cost_usd numeric DEFAULT 0,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone
);


--
-- Name: dash_workflow_schedules; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.dash_workflow_schedules (
    id bigint NOT NULL,
    workflow_id bigint NOT NULL,
    project_slug text,
    cron text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    next_run_at timestamp with time zone,
    last_run_at timestamp with time zone,
    last_run_id bigint,
    owner_user_id integer,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT dash_workflow_schedules_status_check CHECK ((status = ANY (ARRAY['active'::text, 'paused'::text])))
);


--
-- Name: dash_workflow_schedules_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.dash_workflow_schedules_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_workflow_schedules_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.dash_workflow_schedules_id_seq OWNED BY dash.dash_workflow_schedules.id;


--
-- Name: training_signals; Type: TABLE; Schema: dash; Owner: -
--

CREATE TABLE dash.training_signals (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    chat_id text,
    message_id text,
    question text,
    tables_hit jsonb DEFAULT '[]'::jsonb,
    sql_text text,
    sql_success boolean,
    sql_error text,
    chart_action text,
    followup_clicked boolean DEFAULT false,
    agent_used text,
    duration_ms integer,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: training_signals_id_seq; Type: SEQUENCE; Schema: dash; Owner: -
--

CREATE SEQUENCE dash.training_signals_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: training_signals_id_seq; Type: SEQUENCE OWNED BY; Schema: dash; Owner: -
--

ALTER SEQUENCE dash.training_signals_id_seq OWNED BY dash.training_signals.id;


--
-- Name: dash_action_registry; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_action_registry (
    id bigint NOT NULL,
    project_id bigint,
    name text NOT NULL,
    description text,
    method text NOT NULL,
    url_template text NOT NULL,
    header_template jsonb DEFAULT '{}'::jsonb NOT NULL,
    body_template jsonb DEFAULT '{}'::jsonb NOT NULL,
    requires_approval boolean DEFAULT true NOT NULL,
    min_approvals integer DEFAULT 1 NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    created_by text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT dash_action_registry_method_check CHECK ((method = ANY (ARRAY['POST'::text, 'PUT'::text, 'PATCH'::text, 'DELETE'::text])))
);


--
-- Name: dash_action_registry_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_action_registry_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_action_registry_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_action_registry_id_seq OWNED BY public.dash_action_registry.id;


--
-- Name: dash_admin_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_admin_settings (
    id integer NOT NULL,
    key text NOT NULL,
    value text NOT NULL,
    value_type text DEFAULT 'string'::text NOT NULL,
    scope text DEFAULT 'global'::text NOT NULL,
    project_slug text,
    description text,
    updated_by integer,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE dash_admin_settings; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.dash_admin_settings IS 'Runtime config managed via admin UI. Resolution order: project > global > env > default.';


--
-- Name: dash_admin_settings_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_admin_settings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_admin_settings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_admin_settings_id_seq OWNED BY public.dash_admin_settings.id;


--
-- Name: dash_agent_embeds; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_agent_embeds (
    id integer NOT NULL,
    embed_id text NOT NULL,
    project_slug text NOT NULL,
    public_key text NOT NULL,
    secret_key_hash text NOT NULL,
    name text,
    allowed_origins text[] DEFAULT '{}'::text[] NOT NULL,
    user_id_required boolean DEFAULT false,
    user_id_signed boolean DEFAULT true,
    auth_mode text DEFAULT 'hmac'::text,
    jwt_jwks_url text,
    rate_limit_per_min integer DEFAULT 30,
    feature_config jsonb,
    enabled boolean DEFAULT true,
    created_by integer,
    created_at timestamp with time zone DEFAULT now(),
    last_used_at timestamp with time zone,
    bound_scope_id text,
    bound_intent text DEFAULT 'public'::text,
    bound_role text,
    agent_id text,
    auto_provisioned boolean DEFAULT false,
    status text DEFAULT 'live'::text,
    primary_color text DEFAULT '#1a2b4a'::text,
    logo_url text,
    welcome_msg text DEFAULT 'Hi! How can I help?'::text,
    "position" text DEFAULT 'bottom-right'::text,
    theme text DEFAULT 'auto'::text,
    faq_mode text DEFAULT 'auto'::text,
    response_style text DEFAULT 'consumer'::text,
    access_mode text DEFAULT 'public'::text,
    test_ip_allowlist text[] DEFAULT '{}'::text[],
    max_reply_chars integer DEFAULT 600,
    rls_enabled boolean DEFAULT false,
    rls_claims jsonb DEFAULT '[]'::jsonb,
    rls_policies jsonb DEFAULT '[]'::jsonb,
    rls_claim_source text DEFAULT 'token'::text,
    secret_key_encrypted text,
    secret_key text
);


--
-- Name: TABLE dash_agent_embeds; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.dash_agent_embeds IS 'Embeddable agent widgets — public_key for browser, secret stored hashed';


--
-- Name: COLUMN dash_agent_embeds.agent_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dash_agent_embeds.agent_id IS 'When set + auto_provisioned=true, this is the default per-agent embed surfaced on settings UI';


--
-- Name: COLUMN dash_agent_embeds.status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dash_agent_embeds.status IS 'live = active + has origins; draft = no origins yet; disabled = soft-off';


--
-- Name: dash_agent_embeds_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_agent_embeds_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_agent_embeds_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_agent_embeds_id_seq OWNED BY public.dash_agent_embeds.id;


--
-- Name: dash_agent_registry; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_agent_registry (
    id bigint NOT NULL,
    agent_name text NOT NULL,
    description text,
    tags jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    category text
);


--
-- Name: dash_agent_registry_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_agent_registry_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_agent_registry_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_agent_registry_id_seq OWNED BY public.dash_agent_registry.id;


--
-- Name: dash_annotations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_annotations (
    id integer NOT NULL,
    project_slug text NOT NULL,
    table_name text NOT NULL,
    column_name text NOT NULL,
    annotation text NOT NULL,
    updated_by text,
    updated_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_annotations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_annotations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_annotations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_annotations_id_seq OWNED BY public.dash_annotations.id;


--
-- Name: dash_apigw_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_apigw_config (
    id integer DEFAULT 1 NOT NULL,
    rate_per_min integer DEFAULT 60 NOT NULL,
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT dash_apigw_config_id_check CHECK ((id = 1))
);


--
-- Name: dash_apigw_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_apigw_messages (
    id bigint NOT NULL,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    session_id text,
    key_id integer,
    service_account text,
    store_id text,
    role text,
    content text,
    masked boolean DEFAULT false NOT NULL
);


--
-- Name: dash_apigw_messages_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_apigw_messages_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_apigw_messages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_apigw_messages_id_seq OWNED BY public.dash_apigw_messages.id;


--
-- Name: dash_apigw_usage; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_apigw_usage (
    id bigint NOT NULL,
    key_id integer,
    service_account text,
    store_id text,
    scope_mode text,
    model text,
    prompt_tokens integer,
    completion_tokens integer,
    total_tokens integer,
    streamed boolean,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    cost_usd numeric(12,6) DEFAULT 0 NOT NULL,
    request_type text DEFAULT 'chat'::text NOT NULL,
    session_id text,
    status text DEFAULT 'ok'::text NOT NULL,
    latency_ms integer
);


--
-- Name: dash_apigw_usage_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_apigw_usage_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_apigw_usage_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_apigw_usage_id_seq OWNED BY public.dash_apigw_usage.id;


--
-- Name: dash_audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_audit_log (
    id integer NOT NULL,
    user_id integer,
    username text,
    action text NOT NULL,
    resource_type text,
    resource_id text,
    details text,
    created_at timestamp without time zone DEFAULT now(),
    project_slug text,
    target text,
    sources_used jsonb,
    row_count integer,
    latency_ms integer,
    cost_usd numeric(10,6)
);


--
-- Name: dash_audit_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_audit_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_audit_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_audit_log_id_seq OWNED BY public.dash_audit_log.id;


--
-- Name: dash_backup_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_backup_runs (
    id integer NOT NULL,
    env text NOT NULL,
    types text NOT NULL,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    success boolean DEFAULT true NOT NULL,
    size_bytes bigint,
    s3_key text,
    error text,
    duration_seconds integer
);


--
-- Name: TABLE dash_backup_runs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.dash_backup_runs IS 'Audit log of automated backup runs.';


--
-- Name: dash_backup_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_backup_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_backup_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_backup_runs_id_seq OWNED BY public.dash_backup_runs.id;


--
-- Name: dash_brain_access_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_brain_access_log (
    id integer NOT NULL,
    project_slug text,
    agent_name text,
    category text,
    items_accessed integer DEFAULT 0,
    accessed_at timestamp with time zone DEFAULT now()
);


--
-- Name: dash_brain_access_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_brain_access_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_brain_access_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_brain_access_log_id_seq OWNED BY public.dash_brain_access_log.id;


--
-- Name: dash_brain_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_brain_versions (
    id bigint NOT NULL,
    brain_id bigint NOT NULL,
    version integer NOT NULL,
    category text,
    name text,
    definition text,
    project_slug text,
    user_id bigint,
    metadata jsonb,
    change_type text NOT NULL,
    changed_by bigint,
    change_reason text,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: dash_brain_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_brain_versions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_brain_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_brain_versions_id_seq OWNED BY public.dash_brain_versions.id;


--
-- Name: dash_business_rules_db; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_business_rules_db (
    id integer NOT NULL,
    project_slug text NOT NULL,
    table_name text NOT NULL,
    rules jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_business_rules_db_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_business_rules_db_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_business_rules_db_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_business_rules_db_id_seq OWNED BY public.dash_business_rules_db.id;


--
-- Name: dash_chat_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_chat_sessions (
    id integer NOT NULL,
    user_id integer,
    session_id text NOT NULL,
    project_slug text,
    first_message text,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_chat_sessions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_chat_sessions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_chat_sessions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_chat_sessions_id_seq OWNED BY public.dash_chat_sessions.id;


--
-- Name: dash_column_meta; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_column_meta (
    project_slug text NOT NULL,
    table_name text NOT NULL,
    column_name text NOT NULL,
    semantic_type text,
    cardinality_class text,
    description text,
    samples jsonb DEFAULT '[]'::jsonb,
    quality jsonb DEFAULT '{}'::jsonb,
    relationships jsonb DEFAULT '[]'::jsonb,
    glossary_term text,
    glossary_link text,
    suggested_questions jsonb DEFAULT '[]'::jsonb,
    owner text,
    reviewed_at timestamp with time zone,
    provenance jsonb DEFAULT '{}'::jsonb,
    generation_model text,
    generated_at timestamp with time zone DEFAULT now()
);


--
-- Name: dash_company_brain; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_company_brain (
    id integer NOT NULL,
    category text NOT NULL,
    name text NOT NULL,
    definition text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb,
    project_slug text,
    user_id integer,
    created_by text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    source_id bigint,
    lang text DEFAULT 'en'::text
);


--
-- Name: dash_company_brain_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_company_brain_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_company_brain_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_company_brain_id_seq OWNED BY public.dash_company_brain.id;


--
-- Name: dash_dashboard_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_dashboard_audit (
    id integer NOT NULL,
    dashboard_id text NOT NULL,
    skill_versions jsonb NOT NULL,
    verified_cell_pct double precision,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_dashboard_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_dashboard_audit_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_dashboard_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_dashboard_audit_id_seq OWNED BY public.dash_dashboard_audit.id;


--
-- Name: dash_dashboard_skill_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_dashboard_skill_runs (
    id integer NOT NULL,
    project_slug text NOT NULL,
    dashboard_id text,
    skill_id text NOT NULL,
    skill_version integer,
    stage text NOT NULL,
    panel_count integer,
    verified_cell_count integer,
    judge_score integer,
    latency_ms integer,
    cost_usd double precision,
    ran_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_dashboard_skill_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_dashboard_skill_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_dashboard_skill_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_dashboard_skill_runs_id_seq OWNED BY public.dash_dashboard_skill_runs.id;


--
-- Name: dash_dashboards; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_dashboards (
    id integer NOT NULL,
    project_slug text NOT NULL,
    user_id integer NOT NULL,
    name text DEFAULT 'Dashboard'::text NOT NULL,
    widgets jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_dashboards_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_dashboards_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_dashboards_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_dashboards_id_seq OWNED BY public.dash_dashboards.id;


--
-- Name: dash_dashboards_v2; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_dashboards_v2 (
    id text NOT NULL,
    project_slug text,
    spec jsonb,
    created_at timestamp with time zone DEFAULT now(),
    session_id text,
    version integer DEFAULT 1 NOT NULL,
    parent_id text,
    label text,
    signature_hash text
);


--
-- Name: dash_decisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_decisions (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    user_id integer DEFAULT 0,
    chat_msg_id text,
    action text NOT NULL,
    owner text,
    effort text,
    risk text,
    status text DEFAULT 'open'::text NOT NULL,
    source_excerpt text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    session_id text,
    decision_text text DEFAULT ''::text NOT NULL,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    owner_user_id integer,
    due_at timestamp with time zone,
    source_message_id text
);


--
-- Name: dash_decisions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_decisions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_decisions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_decisions_id_seq OWNED BY public.dash_decisions.id;


--
-- Name: dash_deck_schedules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_deck_schedules (
    id integer NOT NULL,
    project_slug text NOT NULL,
    presentation_id integer NOT NULL,
    name text NOT NULL,
    cron text NOT NULL,
    recipients jsonb NOT NULL,
    channel text DEFAULT 'email'::text NOT NULL,
    format text DEFAULT 'pptx'::text NOT NULL,
    enabled boolean DEFAULT true,
    last_run_at timestamp without time zone,
    last_status text,
    last_error text,
    created_by integer,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_deck_schedules_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_deck_schedules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_deck_schedules_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_deck_schedules_id_seq OWNED BY public.dash_deck_schedules.id;


--
-- Name: dash_documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_documents (
    id integer NOT NULL,
    project_slug text NOT NULL,
    filename text NOT NULL,
    content text,
    file_type text,
    file_size integer DEFAULT 0,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_documents_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_documents_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_documents_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_documents_id_seq OWNED BY public.dash_documents.id;


--
-- Name: dash_drift_alerts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_drift_alerts (
    id integer NOT NULL,
    project_slug text NOT NULL,
    table_name text NOT NULL,
    alerts jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_drift_alerts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_drift_alerts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_drift_alerts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_drift_alerts_id_seq OWNED BY public.dash_drift_alerts.id;


--
-- Name: dash_embed_calls; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_embed_calls (
    id bigint NOT NULL,
    embed_id text NOT NULL,
    session_token text,
    external_user text,
    origin text,
    ip text,
    message_chars integer,
    response_chars integer,
    latency_ms integer,
    success boolean DEFAULT true NOT NULL,
    error text,
    ts timestamp with time zone DEFAULT now(),
    tokens_in integer DEFAULT 0 NOT NULL,
    tokens_out integer DEFAULT 0 NOT NULL,
    cost_usd numeric(12,6) DEFAULT 0 NOT NULL
);


--
-- Name: TABLE dash_embed_calls; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.dash_embed_calls IS 'Per-call audit log for embed chat — used by usage/sessions UI panels';


--
-- Name: dash_embed_calls_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_embed_calls_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_embed_calls_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_embed_calls_id_seq OWNED BY public.dash_embed_calls.id;


--
-- Name: dash_embed_rls_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_embed_rls_audit (
    id bigint NOT NULL,
    embed_id text,
    session_token text,
    claims jsonb,
    denied_table text,
    denied_column text,
    action text,
    sql_snippet text,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: dash_embed_rls_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_embed_rls_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_embed_rls_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_embed_rls_audit_id_seq OWNED BY public.dash_embed_rls_audit.id;


--
-- Name: dash_embed_rls_blueprints; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_embed_rls_blueprints (
    id bigint NOT NULL,
    slug text NOT NULL,
    name text NOT NULL,
    industry text,
    icon text,
    description text,
    claims jsonb DEFAULT '[]'::jsonb NOT NULL,
    policies jsonb DEFAULT '[]'::jsonb NOT NULL,
    required_tables text[] DEFAULT '{}'::text[],
    popularity integer DEFAULT 0,
    is_system boolean DEFAULT false,
    created_by integer,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: dash_embed_rls_blueprints_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_embed_rls_blueprints_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_embed_rls_blueprints_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_embed_rls_blueprints_id_seq OWNED BY public.dash_embed_rls_blueprints.id;


--
-- Name: dash_embed_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_embed_sessions (
    id bigint NOT NULL,
    embed_id text NOT NULL,
    session_token text NOT NULL,
    external_user text,
    user_attrs jsonb,
    origin text,
    ip text,
    created_at timestamp with time zone DEFAULT now(),
    expires_at timestamp with time zone NOT NULL,
    revoked boolean DEFAULT false,
    request_count integer DEFAULT 0,
    claims jsonb
);


--
-- Name: TABLE dash_embed_sessions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.dash_embed_sessions IS 'Per-host-user sessions holding short-lived chat tokens';


--
-- Name: dash_embed_sessions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_embed_sessions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_embed_sessions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_embed_sessions_id_seq OWNED BY public.dash_embed_sessions.id;


--
-- Name: dash_eval_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_eval_history (
    id integer NOT NULL,
    project_slug text NOT NULL,
    eval_id integer,
    score text NOT NULL,
    result text,
    run_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_eval_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_eval_history_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_eval_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_eval_history_id_seq OWNED BY public.dash_eval_history.id;


--
-- Name: dash_eval_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_eval_runs (
    id integer NOT NULL,
    project_slug text NOT NULL,
    total integer DEFAULT 0 NOT NULL,
    passed integer DEFAULT 0 NOT NULL,
    partial integer DEFAULT 0 NOT NULL,
    failed integer DEFAULT 0 NOT NULL,
    average_score real,
    regression_report text,
    run_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_eval_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_eval_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_eval_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_eval_runs_id_seq OWNED BY public.dash_eval_runs.id;


--
-- Name: dash_evals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_evals (
    id integer NOT NULL,
    project_slug text NOT NULL,
    question text NOT NULL,
    expected_sql text NOT NULL,
    last_result text,
    last_score text,
    last_run_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_evals_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_evals_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_evals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_evals_id_seq OWNED BY public.dash_evals.id;


--
-- Name: dash_evolution_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_evolution_runs (
    id integer NOT NULL,
    project_slug text NOT NULL,
    status text DEFAULT 'running'::text NOT NULL,
    steps_completed jsonb DEFAULT '[]'::jsonb,
    reflect_result text,
    select_result text,
    improve_result text,
    evaluate_result text,
    commit_result text,
    started_at timestamp without time zone DEFAULT now(),
    finished_at timestamp without time zone
);


--
-- Name: dash_evolution_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_evolution_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_evolution_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_evolution_runs_id_seq OWNED BY public.dash_evolution_runs.id;


--
-- Name: dash_evolved_instructions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_evolved_instructions (
    id integer NOT NULL,
    project_slug text NOT NULL,
    instructions text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    reasoning text,
    chat_count_at_generation integer DEFAULT 0,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_evolved_instructions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_evolved_instructions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_evolved_instructions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_evolved_instructions_id_seq OWNED BY public.dash_evolved_instructions.id;


--
-- Name: dash_external_facts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_external_facts (
    id integer NOT NULL,
    query_hash text NOT NULL,
    source_type text NOT NULL,
    query_text text,
    result_json jsonb,
    result_summary text,
    fetched_at timestamp with time zone DEFAULT now(),
    expires_at timestamp with time zone,
    cost_usd numeric(8,5) DEFAULT 0,
    http_status integer
);


--
-- Name: COLUMN dash_external_facts.expires_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dash_external_facts.expires_at IS 'cache TTL; web search 7d, FRED 30d, Wikipedia 90d';


--
-- Name: dash_external_facts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_external_facts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_external_facts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_external_facts_id_seq OWNED BY public.dash_external_facts.id;


--
-- Name: dash_extraction_plans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_extraction_plans (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    table_name text NOT NULL,
    source_file text,
    sheet_name text,
    file_hash text,
    strategy text NOT NULL,
    header_row integer,
    skip_rows jsonb DEFAULT '[]'::jsonb,
    blocks jsonb DEFAULT '[]'::jsonb,
    row_count_in bigint,
    row_count_out bigint,
    llm_rescued boolean DEFAULT false,
    rescue_reasoning text,
    user_overrides jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: dash_extraction_plans_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_extraction_plans_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_extraction_plans_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_extraction_plans_id_seq OWNED BY public.dash_extraction_plans.id;


--
-- Name: dash_federation_circuit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_federation_circuit (
    project_slug text NOT NULL,
    consecutive_failures integer DEFAULT 0,
    open_until timestamp with time zone,
    last_error text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE dash_federation_circuit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.dash_federation_circuit IS 'Federation circuit breaker — opens after N consecutive failures, blocks calls.';


--
-- Name: dash_feedback; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_feedback (
    id integer NOT NULL,
    user_id integer,
    project_slug text NOT NULL,
    session_id text,
    question text NOT NULL,
    answer text,
    sql_query text,
    rating text DEFAULT 'up'::text NOT NULL,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_feedback_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_feedback_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_feedback_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_feedback_id_seq OWNED BY public.dash_feedback.id;


--
-- Name: dash_guardrail_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_guardrail_audit (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    user_id integer,
    embed_id text,
    external_user text,
    question text NOT NULL,
    refusal_reason text,
    classifier text,
    matched_topic text,
    refusal_message text,
    ts timestamp with time zone DEFAULT now()
);


--
-- Name: TABLE dash_guardrail_audit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.dash_guardrail_audit IS 'Auto-scope guardrail: every off-topic refusal logged for review';


--
-- Name: dash_guardrail_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_guardrail_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_guardrail_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_guardrail_audit_id_seq OWNED BY public.dash_guardrail_audit.id;


--
-- Name: dash_hitl_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_hitl_requests (
    id bigint NOT NULL,
    request_id text NOT NULL,
    project_slug text NOT NULL,
    agent_name text NOT NULL,
    operation text NOT NULL,
    details jsonb DEFAULT '{}'::jsonb NOT NULL,
    state text DEFAULT 'pending'::text NOT NULL,
    requested_by text NOT NULL,
    responded_by text,
    response_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone DEFAULT (now() + '01:00:00'::interval) NOT NULL
);


--
-- Name: dash_hitl_requests_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_hitl_requests_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_hitl_requests_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_hitl_requests_id_seq OWNED BY public.dash_hitl_requests.id;


--
-- Name: dash_ingest_batches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_ingest_batches (
    batch_id text NOT NULL,
    project_slug text NOT NULL,
    status text DEFAULT 'staged'::text NOT NULL,
    file_count integer DEFAULT 0,
    manifest jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_ingest_contracts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_ingest_contracts (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    dataset text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    active boolean DEFAULT true NOT NULL,
    columns jsonb,
    load_key jsonb,
    period_source text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_ingest_contracts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_ingest_contracts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_ingest_contracts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_ingest_contracts_id_seq OWNED BY public.dash_ingest_contracts.id;


--
-- Name: dash_ingest_files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_ingest_files (
    id bigint NOT NULL,
    batch_id text NOT NULL,
    project_slug text,
    filename text,
    content_hash text,
    dataset text,
    verdict text,
    target_table text,
    load_key jsonb,
    score integer,
    status text,
    reason text,
    rows integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_ingest_files_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_ingest_files_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_ingest_files_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_ingest_files_id_seq OWNED BY public.dash_ingest_files.id;


--
-- Name: dash_journal; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_journal (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    project_slug text NOT NULL,
    journal_date date NOT NULL,
    stats jsonb DEFAULT '{}'::jsonb NOT NULL,
    summary_md text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_knowledge_triples; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_knowledge_triples (
    id integer NOT NULL,
    project_slug text NOT NULL,
    subject text NOT NULL,
    predicate text NOT NULL,
    object text NOT NULL,
    source_type text,
    source_id text,
    confidence double precision DEFAULT 1.0,
    inferred boolean DEFAULT false,
    community integer,
    created_at timestamp with time zone DEFAULT now(),
    extractor text DEFAULT 'llm'::text,
    extraction_cost_usd numeric(10,6) DEFAULT 0
);


--
-- Name: dash_knowledge_triples_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_knowledge_triples_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_knowledge_triples_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_knowledge_triples_id_seq OWNED BY public.dash_knowledge_triples.id;


--
-- Name: dash_llm_costs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_llm_costs (
    id bigint NOT NULL,
    project_slug text,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    task text,
    model text,
    cost_usd numeric(12,6) DEFAULT 0 NOT NULL,
    tokens_in integer DEFAULT 0 NOT NULL,
    tokens_out integer DEFAULT 0 NOT NULL,
    ok boolean DEFAULT true NOT NULL,
    meta jsonb DEFAULT '{}'::jsonb NOT NULL,
    actor text
);


--
-- Name: TABLE dash_llm_costs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.dash_llm_costs IS 'Per-call LLM cost ledger; primary source for daily/monthly cost rollups.';


--
-- Name: dash_llm_costs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_llm_costs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_llm_costs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_llm_costs_id_seq OWNED BY public.dash_llm_costs.id;


--
-- Name: dash_memories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_memories (
    id integer NOT NULL,
    user_id integer,
    project_slug text,
    scope text DEFAULT 'project'::text NOT NULL,
    fact text NOT NULL,
    source text DEFAULT 'user'::text,
    created_at timestamp without time zone DEFAULT now(),
    archived boolean DEFAULT false,
    version integer DEFAULT 1,
    parent_id integer,
    citation_count integer DEFAULT 0,
    last_cited_at timestamp with time zone,
    confidence_score numeric(4,3) DEFAULT 0.500,
    decay_resistant boolean DEFAULT false,
    lang text DEFAULT 'en'::text
);


--
-- Name: COLUMN dash_memories.decay_resistant; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dash_memories.decay_resistant IS 'core knowledge — bypasses forgetting curve';


--
-- Name: dash_memories_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_memories_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_memories_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_memories_id_seq OWNED BY public.dash_memories.id;


--
-- Name: dash_meta_learnings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_meta_learnings (
    id integer NOT NULL,
    project_slug text NOT NULL,
    error_type text NOT NULL,
    fix_strategy text NOT NULL,
    success boolean NOT NULL,
    error_message text,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_meta_learnings_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_meta_learnings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_meta_learnings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_meta_learnings_id_seq OWNED BY public.dash_meta_learnings.id;


--
-- Name: dash_metric_definitions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_metric_definitions (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    name text NOT NULL,
    synonyms jsonb DEFAULT '[]'::jsonb NOT NULL,
    description text,
    kind text DEFAULT 'count'::text NOT NULL,
    source_glob text,
    source_tables jsonb DEFAULT '[]'::jsonb NOT NULL,
    measure_col text,
    filters jsonb DEFAULT '[]'::jsonb NOT NULL,
    denom_filters jsonb DEFAULT '[]'::jsonb NOT NULL,
    group_dims jsonb DEFAULT '[]'::jsonb NOT NULL,
    default_group jsonb DEFAULT '[]'::jsonb NOT NULL,
    trim_values boolean DEFAULT true NOT NULL,
    verified_answer jsonb,
    status text DEFAULT 'draft'::text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    created_by text,
    updated_by text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    model_name text,
    raw_table_ref text,
    virtual_columns jsonb DEFAULT '[]'::jsonb NOT NULL,
    relationships jsonb DEFAULT '[]'::jsonb NOT NULL
);


--
-- Name: COLUMN dash_metric_definitions.model_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dash_metric_definitions.model_name IS 'WrenAI-style logical model name. Multiple metrics can share a model. NULL = legacy/un-MDL.';


--
-- Name: COLUMN dash_metric_definitions.virtual_columns; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dash_metric_definitions.virtual_columns IS 'Derived columns: [{name, expression, type}]. e.g., {"name":"was_successful","expression":"ot_cd=''successful''","type":"boolean"}';


--
-- Name: COLUMN dash_metric_definitions.relationships; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dash_metric_definitions.relationships IS 'Joins to other models: [{model, on, type}]. type ∈ many_to_one|one_to_many|many_to_many';


--
-- Name: dash_metric_definitions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_metric_definitions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_metric_definitions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_metric_definitions_id_seq OWNED BY public.dash_metric_definitions.id;


--
-- Name: dash_metric_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_metric_versions (
    id bigint NOT NULL,
    metric_id bigint NOT NULL,
    project_slug text NOT NULL,
    name text NOT NULL,
    snapshot jsonb,
    change_type text,
    changed_by text,
    change_reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_metric_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_metric_versions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_metric_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_metric_versions_id_seq OWNED BY public.dash_metric_versions.id;


--
-- Name: dash_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_migrations (
    filename text NOT NULL,
    applied_at timestamp with time zone DEFAULT now(),
    checksum text
);


--
-- Name: dash_notifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_notifications (
    id integer NOT NULL,
    user_id integer NOT NULL,
    type text DEFAULT 'info'::text NOT NULL,
    title text NOT NULL,
    message text,
    read boolean DEFAULT false NOT NULL,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_notifications_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_notifications_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_notifications_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_notifications_id_seq OWNED BY public.dash_notifications.id;


--
-- Name: dash_oauth_flow; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_oauth_flow (
    state text NOT NULL,
    provider text NOT NULL,
    nonce text NOT NULL,
    code_verifier text NOT NULL,
    redirect_uri text NOT NULL,
    created_at bigint NOT NULL
);


--
-- Name: dash_ontology_api_calls; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_ontology_api_calls (
    id bigint NOT NULL,
    key_id bigint NOT NULL,
    endpoint text NOT NULL,
    status_code integer,
    latency_ms integer,
    ip text,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: dash_ontology_api_calls_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_ontology_api_calls_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_ontology_api_calls_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_ontology_api_calls_id_seq OWNED BY public.dash_ontology_api_calls.id;


--
-- Name: dash_ontology_api_keys; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_ontology_api_keys (
    id bigint NOT NULL,
    name text NOT NULL,
    public_key text NOT NULL,
    secret_key_hash text NOT NULL,
    project_slug text,
    scope jsonb DEFAULT '{}'::jsonb,
    rate_limit_per_min integer DEFAULT 60,
    status text DEFAULT 'active'::text,
    allowed_origins text[],
    created_by bigint,
    created_at timestamp with time zone DEFAULT now(),
    last_used_at timestamp with time zone
);


--
-- Name: dash_ontology_api_keys_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_ontology_api_keys_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_ontology_api_keys_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_ontology_api_keys_id_seq OWNED BY public.dash_ontology_api_keys.id;


--
-- Name: dash_personas; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_personas (
    id integer NOT NULL,
    project_slug text NOT NULL,
    persona jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_personas_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_personas_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_personas_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_personas_id_seq OWNED BY public.dash_personas.id;


--
-- Name: dash_presentations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_presentations (
    id integer NOT NULL,
    project_slug text NOT NULL,
    user_id integer,
    title text NOT NULL,
    version integer DEFAULT 1,
    thinking jsonb,
    slides jsonb DEFAULT '[]'::jsonb NOT NULL,
    source_messages jsonb,
    created_at timestamp without time zone DEFAULT now(),
    audience text,
    parent_id bigint,
    critique_pass integer DEFAULT 0,
    hero_image_url text,
    narration_status text,
    render_engine text DEFAULT 'python-pptx'::text,
    rendered_pptx_path text,
    pptxgenjs_spec jsonb
);


--
-- Name: dash_presentations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_presentations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_presentations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_presentations_id_seq OWNED BY public.dash_presentations.id;


--
-- Name: dash_proactive_insights; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_proactive_insights (
    id integer NOT NULL,
    project_slug text NOT NULL,
    user_id integer,
    insight text NOT NULL,
    severity text DEFAULT 'info'::text NOT NULL,
    tables_involved text[] DEFAULT '{}'::text[],
    sql_used text,
    dismissed boolean DEFAULT false NOT NULL,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_proactive_insights_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_proactive_insights_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_proactive_insights_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_proactive_insights_id_seq OWNED BY public.dash_proactive_insights.id;


--
-- Name: dash_project_shares; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_project_shares (
    id integer NOT NULL,
    project_id integer,
    shared_with_user_id integer,
    shared_by text NOT NULL,
    role text DEFAULT 'viewer'::text NOT NULL,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_project_shares_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_project_shares_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_project_shares_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_project_shares_id_seq OWNED BY public.dash_project_shares.id;


--
-- Name: dash_projects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_projects (
    id integer NOT NULL,
    user_id integer,
    slug text NOT NULL,
    name text NOT NULL,
    agent_name text NOT NULL,
    agent_role text DEFAULT ''::text,
    agent_personality text DEFAULT 'friendly'::text,
    schema_name text NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    is_favorite boolean DEFAULT false,
    digest_enabled boolean DEFAULT false,
    digest_email_to text,
    digest_slack_enabled boolean DEFAULT false,
    digest_time_utc text DEFAULT '08:00'::text,
    last_digest_sent_at timestamp with time zone,
    last_digest_error text,
    feature_config jsonb DEFAULT '{}'::jsonb,
    contribute_to_central boolean DEFAULT true,
    receive_from_central boolean DEFAULT true
);


--
-- Name: COLUMN dash_projects.digest_enabled; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dash_projects.digest_enabled IS 'master toggle for daily digest delivery';


--
-- Name: COLUMN dash_projects.digest_email_to; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dash_projects.digest_email_to IS 'comma-separated email recipients';


--
-- Name: COLUMN dash_projects.digest_slack_enabled; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dash_projects.digest_slack_enabled IS 'send digest to SLACK_WEBHOOK_URL';


--
-- Name: COLUMN dash_projects.digest_time_utc; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dash_projects.digest_time_utc IS 'HH:MM UTC time-of-day for daily send';


--
-- Name: COLUMN dash_projects.last_digest_sent_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dash_projects.last_digest_sent_at IS 'last successful or attempted send timestamp';


--
-- Name: COLUMN dash_projects.last_digest_error; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dash_projects.last_digest_error IS 'last send error (truncated to 500)';


--
-- Name: COLUMN dash_projects.feature_config; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dash_projects.feature_config IS 'Agent creator toggles: tabs (analysis/data/query/chart/sources), tools (sql/charts/ml/dashboards/forecast/anomaly), agents (analyst/engineer/researcher/data_scientist)';


--
-- Name: COLUMN dash_projects.contribute_to_central; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dash_projects.contribute_to_central IS 'tenant opt-out from sharing PII-scrubbed generic facts';


--
-- Name: dash_projects_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_projects_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_projects_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_projects_id_seq OWNED BY public.dash_projects.id;


--
-- Name: dash_quality_scores; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_quality_scores (
    id integer NOT NULL,
    project_slug text NOT NULL,
    session_id text NOT NULL,
    score integer NOT NULL,
    reasoning text,
    created_at timestamp without time zone DEFAULT now(),
    CONSTRAINT dash_quality_scores_score_check CHECK (((score >= 1) AND (score <= 5)))
);


--
-- Name: dash_quality_scores_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_quality_scores_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_quality_scores_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_quality_scores_id_seq OWNED BY public.dash_quality_scores.id;


--
-- Name: dash_query_patterns; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_query_patterns (
    id integer NOT NULL,
    project_slug text NOT NULL,
    question text NOT NULL,
    sql text NOT NULL,
    uses integer DEFAULT 1,
    last_used timestamp without time zone DEFAULT now(),
    created_at timestamp without time zone DEFAULT now(),
    version integer DEFAULT 1,
    parent_id integer,
    tables_used text,
    join_strategy text,
    filters text,
    source text DEFAULT 'user'::text
);


--
-- Name: dash_query_patterns_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_query_patterns_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_query_patterns_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_query_patterns_id_seq OWNED BY public.dash_query_patterns.id;


--
-- Name: dash_query_plans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_query_plans (
    id integer NOT NULL,
    project_slug text NOT NULL,
    tables_involved text[] NOT NULL,
    join_strategy text,
    filters_used text,
    success boolean DEFAULT true NOT NULL,
    execution_time_ms integer,
    question text,
    sql_used text,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_query_plans_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_query_plans_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_query_plans_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_query_plans_id_seq OWNED BY public.dash_query_plans.id;


--
-- Name: dash_relationships; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_relationships (
    id integer NOT NULL,
    project_slug text NOT NULL,
    from_table text NOT NULL,
    from_column text NOT NULL,
    to_table text NOT NULL,
    to_column text NOT NULL,
    rel_type text DEFAULT 'fk'::text,
    confidence real DEFAULT 0.5,
    source text DEFAULT 'auto'::text
);


--
-- Name: dash_relationships_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_relationships_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_relationships_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_relationships_id_seq OWNED BY public.dash_relationships.id;


--
-- Name: dash_resource_registry; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_resource_registry (
    id integer NOT NULL,
    project_slug text NOT NULL,
    resource_type text NOT NULL,
    resource_count integer DEFAULT 0,
    health_score integer DEFAULT 0,
    staleness_days integer DEFAULT 0,
    last_updated timestamp without time zone DEFAULT now(),
    metadata jsonb DEFAULT '{}'::jsonb
);


--
-- Name: dash_resource_registry_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_resource_registry_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_resource_registry_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_resource_registry_id_seq OWNED BY public.dash_resource_registry.id;


--
-- Name: dash_rules_db; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_rules_db (
    id integer NOT NULL,
    project_slug text NOT NULL,
    rule_id text NOT NULL,
    name text NOT NULL,
    type text DEFAULT 'business_rule'::text NOT NULL,
    category text DEFAULT 'general'::text,
    definition text NOT NULL,
    source text DEFAULT 'user'::text,
    created_at timestamp without time zone DEFAULT now(),
    version integer DEFAULT 1,
    previous_definition text,
    lang text DEFAULT 'en'::text
);


--
-- Name: dash_rules_db_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_rules_db_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_rules_db_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_rules_db_id_seq OWNED BY public.dash_rules_db.id;


--
-- Name: dash_schedules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_schedules (
    id integer NOT NULL,
    project_slug text NOT NULL,
    user_id integer NOT NULL,
    name text NOT NULL,
    prompt text NOT NULL,
    cron text DEFAULT '0 8 * * 1'::text NOT NULL,
    timezone text DEFAULT 'UTC'::text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    output_type text DEFAULT 'dashboard'::text NOT NULL,
    email_to text,
    last_run_at timestamp without time zone,
    last_result jsonb,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_schedules_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_schedules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_schedules_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_schedules_id_seq OWNED BY public.dash_schedules.id;


--
-- Name: dash_security_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_security_events (
    id bigint NOT NULL,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    kind text NOT NULL,
    severity text DEFAULT 'WARN'::text NOT NULL,
    service_account text,
    key_id integer,
    store_id text,
    detail text,
    meta jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: dash_security_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_security_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_security_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_security_events_id_seq OWNED BY public.dash_security_events.id;


--
-- Name: dash_self_learning_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_self_learning_runs (
    id integer NOT NULL,
    project_slug text,
    cycle_num integer NOT NULL,
    status text DEFAULT 'running'::text,
    questions_generated integer DEFAULT 0,
    questions_answered integer DEFAULT 0,
    hypotheses_formed integer DEFAULT 0,
    hypotheses_verified integer DEFAULT 0,
    hypotheses_failed integer DEFAULT 0,
    facts_consolidated integer DEFAULT 0,
    facts_promoted integer DEFAULT 0,
    cost_usd numeric(10,4) DEFAULT 0,
    duration_seconds integer,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    error text,
    metadata jsonb DEFAULT '{}'::jsonb,
    logs jsonb DEFAULT '[]'::jsonb,
    current_step text,
    step_index integer DEFAULT 0,
    total_steps integer DEFAULT 8,
    summary text,
    memories_forgotten integer DEFAULT 0,
    focus text
);


--
-- Name: dash_self_learning_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_self_learning_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_self_learning_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_self_learning_runs_id_seq OWNED BY public.dash_self_learning_runs.id;


--
-- Name: dash_shared_conversations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_shared_conversations (
    token text NOT NULL,
    project_slug text,
    session_id text,
    created_by text,
    created_at timestamp with time zone DEFAULT now(),
    expires_at timestamp with time zone,
    revoked boolean DEFAULT false,
    include_lineage boolean DEFAULT true,
    snapshot jsonb
);


--
-- Name: dash_skill_overrides; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_skill_overrides (
    id integer NOT NULL,
    project_slug text NOT NULL,
    skill_id text NOT NULL,
    instructions text NOT NULL,
    created_by integer,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_skill_overrides_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_skill_overrides_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_skill_overrides_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_skill_overrides_id_seq OWNED BY public.dash_skill_overrides.id;


--
-- Name: dash_sse_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_sse_audit (
    id bigint NOT NULL,
    session_id text,
    event_name text NOT NULL,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    bytes_emitted integer,
    error text,
    project_slug text
);


--
-- Name: dash_sse_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_sse_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_sse_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_sse_audit_id_seq OWNED BY public.dash_sse_audit.id;


--
-- Name: dash_suggested_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_suggested_rules (
    id integer NOT NULL,
    project_slug text NOT NULL,
    name text NOT NULL,
    type text DEFAULT 'business_rule'::text NOT NULL,
    definition text NOT NULL,
    source_session_id text,
    status text DEFAULT 'pending'::text NOT NULL,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_suggested_rules_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_suggested_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_suggested_rules_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_suggested_rules_id_seq OWNED BY public.dash_suggested_rules.id;


--
-- Name: dash_system_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_system_status (
    id smallint DEFAULT 1 NOT NULL,
    last_backup_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT dash_system_status_singleton CHECK ((id = 1))
);


--
-- Name: dash_table_metadata; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_table_metadata (
    id integer NOT NULL,
    project_slug text NOT NULL,
    table_name text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    fingerprint text,
    row_count integer DEFAULT 0,
    col_hash text
);


--
-- Name: dash_table_metadata_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_table_metadata_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_table_metadata_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_table_metadata_id_seq OWNED BY public.dash_table_metadata.id;


--
-- Name: dash_table_usage_stats; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_table_usage_stats (
    table_fqn text NOT NULL,
    query_count_7d integer DEFAULT 0 NOT NULL,
    query_count_30d integer DEFAULT 0 NOT NULL,
    last_used_at timestamp with time zone,
    distinct_users integer DEFAULT 0 NOT NULL,
    avg_latency_ms numeric,
    error_rate numeric,
    refreshed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_tokens (
    token text NOT NULL,
    user_id integer,
    username text NOT NULL,
    expiry bigint NOT NULL
);


--
-- Name: dash_tool_patches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_tool_patches (
    id bigint NOT NULL,
    tool_name text NOT NULL,
    project_slug text,
    version integer DEFAULT 1 NOT NULL,
    old_description text,
    new_description text,
    default_args jsonb,
    reason text,
    failure_samples jsonb,
    score_before numeric(5,2),
    score_after numeric(5,2),
    shadow_pass_rate numeric(5,2),
    applied boolean DEFAULT false,
    applied_at timestamp with time zone,
    reverted boolean DEFAULT false,
    reverted_at timestamp with time zone,
    revert_reason text,
    source text DEFAULT 'auto'::text,
    created_by integer,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: TABLE dash_tool_patches; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.dash_tool_patches IS 'SkillRefinery: versioned tool description/arg patches. project_slug=NULL means global override.';


--
-- Name: dash_tool_patches_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_tool_patches_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_tool_patches_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_tool_patches_id_seq OWNED BY public.dash_tool_patches.id;


--
-- Name: dash_tool_scores; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_tool_scores (
    id bigint NOT NULL,
    tool_name text NOT NULL,
    project_slug text,
    score numeric(5,2) NOT NULL,
    success_rate numeric(5,2),
    feedback_score numeric(5,2),
    latency_p50_ms integer,
    latency_p95_ms integer,
    calls integer DEFAULT 0 NOT NULL,
    fails integer DEFAULT 0 NOT NULL,
    last_error text,
    window_days integer DEFAULT 14 NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE dash_tool_scores; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.dash_tool_scores IS 'SkillRefinery: rolling tool utility score per project. Recomputed nightly.';


--
-- Name: dash_tool_scores_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_tool_scores_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_tool_scores_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_tool_scores_id_seq OWNED BY public.dash_tool_scores.id;


--
-- Name: dash_tool_utility_scores; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_tool_utility_scores (
    id bigint NOT NULL,
    tool_name text NOT NULL,
    agent text,
    project_slug text,
    user_id integer,
    args_hash text,
    success boolean NOT NULL,
    latency_ms integer,
    error_class text,
    error_message text,
    feedback smallint,
    retry_count smallint DEFAULT 0,
    ts timestamp with time zone DEFAULT now()
);


--
-- Name: TABLE dash_tool_utility_scores; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.dash_tool_utility_scores IS 'SkillRefinery: per-call telemetry feeding nightly utility scoring + patch generation';


--
-- Name: dash_tool_utility_scores_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_tool_utility_scores_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_tool_utility_scores_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_tool_utility_scores_id_seq OWNED BY public.dash_tool_utility_scores.id;


--
-- Name: dash_traces; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_traces (
    id bigint NOT NULL,
    trace_id text NOT NULL,
    parent_id text,
    name text NOT NULL,
    kind text NOT NULL,
    project_slug text,
    status text DEFAULT 'running'::text NOT NULL,
    duration_ms integer,
    cost_usd numeric,
    tokens integer,
    error text,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    meta jsonb
);


--
-- Name: dash_traces_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_traces_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_traces_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_traces_id_seq OWNED BY public.dash_traces.id;


--
-- Name: dash_training_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_training_jobs (
    id bigint NOT NULL,
    run_id bigint,
    project_slug text,
    table_name text,
    job_type text,
    status text DEFAULT 'queued'::text,
    payload jsonb,
    result jsonb,
    error text,
    created_at timestamp with time zone DEFAULT now(),
    started_at timestamp with time zone,
    finished_at timestamp with time zone
);


--
-- Name: dash_training_jobs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_training_jobs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_training_jobs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_training_jobs_id_seq OWNED BY public.dash_training_jobs.id;


--
-- Name: dash_training_qa; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_training_qa (
    id integer NOT NULL,
    project_slug text NOT NULL,
    table_name text,
    question text NOT NULL,
    sql text,
    answer_template text,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_training_qa_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_training_qa_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_training_qa_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_training_qa_id_seq OWNED BY public.dash_training_qa.id;


--
-- Name: dash_training_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_training_runs (
    id integer NOT NULL,
    project_slug text NOT NULL,
    tables_trained integer DEFAULT 0,
    status text DEFAULT 'running'::text NOT NULL,
    steps text,
    error text,
    started_at timestamp without time zone DEFAULT now(),
    finished_at timestamp without time zone,
    logs jsonb DEFAULT '[]'::jsonb,
    current_step text,
    stage_progress integer,
    current_progress jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: dash_training_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_training_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_training_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_training_runs_id_seq OWNED BY public.dash_training_runs.id;


--
-- Name: dash_training_steps; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_training_steps (
    id bigint NOT NULL,
    run_id bigint,
    project_slug text NOT NULL,
    step_no integer,
    name text NOT NULL,
    scope text DEFAULT 'project'::text NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    fp text,
    output_ref text,
    elapsed_ms integer,
    error text,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dash_training_steps_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_training_steps_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_training_steps_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_training_steps_id_seq OWNED BY public.dash_training_steps.id;


--
-- Name: dash_upload_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_upload_cache (
    file_hash text NOT NULL,
    file_size_bytes bigint,
    file_ext text,
    plan jsonb NOT NULL,
    rescue_used boolean DEFAULT false,
    hit_count integer DEFAULT 0,
    first_seen_at timestamp with time zone DEFAULT now(),
    last_used_at timestamp with time zone DEFAULT now()
);


--
-- Name: dash_usage_budget; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_usage_budget (
    id integer DEFAULT 1 NOT NULL,
    daily_usd numeric(12,2) DEFAULT 0 NOT NULL,
    monthly_usd numeric(12,2) DEFAULT 0 NOT NULL,
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT dash_usage_budget_id_check CHECK ((id = 1))
);


--
-- Name: dash_user_preferences; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_user_preferences (
    id integer NOT NULL,
    user_id integer NOT NULL,
    project_slug text NOT NULL,
    preferences jsonb DEFAULT '{}'::jsonb NOT NULL,
    updated_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_user_preferences_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_user_preferences_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_user_preferences_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_user_preferences_id_seq OWNED BY public.dash_user_preferences.id;


--
-- Name: dash_user_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_user_roles (
    id integer NOT NULL,
    user_id integer NOT NULL,
    project_slug text NOT NULL,
    role_name text NOT NULL,
    assigned_at timestamp with time zone DEFAULT now()
);


--
-- Name: dash_user_roles_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_user_roles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_user_roles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_user_roles_id_seq OWNED BY public.dash_user_roles.id;


--
-- Name: dash_user_scopes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_user_scopes (
    id integer NOT NULL,
    user_id integer NOT NULL,
    project_slug text NOT NULL,
    scope_id text NOT NULL,
    scope_label text NOT NULL,
    role text DEFAULT 'staff'::text,
    is_default boolean DEFAULT false,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_user_scopes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_user_scopes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_user_scopes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_user_scopes_id_seq OWNED BY public.dash_user_scopes.id;


--
-- Name: dash_users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_users (
    id integer NOT NULL,
    username text NOT NULL,
    password_hash text NOT NULL,
    api_key text,
    created_at timestamp without time zone DEFAULT now(),
    email text,
    first_name text,
    last_name text,
    avatar_url text,
    department text,
    job_title text,
    phone text,
    bio text,
    timezone text DEFAULT 'UTC'::text,
    language text DEFAULT 'en'::text,
    notification_prefs jsonb DEFAULT '{"email": true, "in_app": true}'::jsonb,
    auth_provider text DEFAULT 'local'::text,
    external_id text,
    is_active boolean DEFAULT true,
    last_login timestamp without time zone,
    site_code text,
    store_id text,
    scope_mode text DEFAULT 'global'::text,
    store_ids text,
    role text DEFAULT 'user'::text
);


--
-- Name: dash_users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_users_id_seq OWNED BY public.dash_users.id;


--
-- Name: dash_verified_scores; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_verified_scores (
    id integer NOT NULL,
    project_slug text NOT NULL,
    session_id text,
    question text,
    verified text DEFAULT 'unknown'::text NOT NULL,
    expected double precision,
    got double precision,
    source_q text,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: dash_verified_scores_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_verified_scores_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_verified_scores_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_verified_scores_id_seq OWNED BY public.dash_verified_scores.id;


--
-- Name: dash_visibility_policy; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_visibility_policy (
    project_slug text NOT NULL,
    version integer DEFAULT 1,
    policy_json jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now(),
    updated_by integer
);


--
-- Name: dash_visibility_policy_drafts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_visibility_policy_drafts (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    policy_json jsonb NOT NULL,
    status text DEFAULT 'draft'::text,
    created_by integer,
    created_at timestamp with time zone DEFAULT now(),
    submitted_at timestamp with time zone,
    approvals jsonb DEFAULT '[]'::jsonb,
    required_approvals integer DEFAULT 2,
    comment text
);


--
-- Name: dash_visibility_policy_drafts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_visibility_policy_drafts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_visibility_policy_drafts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_visibility_policy_drafts_id_seq OWNED BY public.dash_visibility_policy_drafts.id;


--
-- Name: dash_visibility_policy_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_visibility_policy_history (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    version integer,
    policy_json jsonb,
    changed_at timestamp with time zone DEFAULT now(),
    changed_by integer,
    changed_fields jsonb DEFAULT '{}'::jsonb
);


--
-- Name: dash_visibility_policy_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_visibility_policy_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_visibility_policy_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_visibility_policy_history_id_seq OWNED BY public.dash_visibility_policy_history.id;


--
-- Name: dash_visibility_read_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_visibility_read_log (
    id bigint NOT NULL,
    project_slug text NOT NULL,
    viewer_user_id integer,
    viewer_scope_id text,
    target_scope_id text,
    intent text NOT NULL,
    policy_version integer,
    sql_excerpt text,
    fields_downgraded text[],
    row_count integer,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: dash_visibility_read_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_visibility_read_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_visibility_read_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_visibility_read_log_id_seq OWNED BY public.dash_visibility_read_log.id;


--
-- Name: dash_visibility_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_visibility_roles (
    id integer NOT NULL,
    project_slug text NOT NULL,
    role_name text NOT NULL,
    allowed_intents text[] DEFAULT ARRAY['private'::text],
    description text DEFAULT ''::text,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: dash_visibility_roles_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_visibility_roles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_visibility_roles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_visibility_roles_id_seq OWNED BY public.dash_visibility_roles.id;


--
-- Name: dash_workflow_runs_v2; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_workflow_runs_v2 (
    id bigint NOT NULL,
    run_id text NOT NULL,
    workflow_name text NOT NULL,
    project_slug text,
    triggered_by text,
    status text DEFAULT 'pending'::text NOT NULL,
    input_args jsonb DEFAULT '{}'::jsonb NOT NULL,
    result jsonb,
    error text,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone
);


--
-- Name: dash_workflow_runs_v2_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_workflow_runs_v2_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_workflow_runs_v2_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_workflow_runs_v2_id_seq OWNED BY public.dash_workflow_runs_v2.id;


--
-- Name: dash_workflows_db; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dash_workflows_db (
    id integer NOT NULL,
    project_slug text NOT NULL,
    name text NOT NULL,
    description text,
    steps jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    source text DEFAULT 'training'::text
);


--
-- Name: dash_workflows_db_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dash_workflows_db_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dash_workflows_db_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dash_workflows_db_id_seq OWNED BY public.dash_workflows_db.id;


--
-- Name: mv_table_usage; Type: MATERIALIZED VIEW; Schema: public; Owner: -
--

CREATE MATERIALIZED VIEW public.mv_table_usage AS
 SELECT table_fqn,
    query_count_7d,
    query_count_30d,
    last_used_at,
    distinct_users,
    avg_latency_ms,
    error_rate,
    refreshed_at
   FROM public.dash_table_usage_stats
  WITH NO DATA;


--
-- Name: v_usage_unified; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_usage_unified AS
 SELECT 'platform'::text AS src,
    dash_llm_costs.ts,
    COALESCE(dash_llm_costs.actor, 'system'::text) AS actor,
    NULL::text AS store_id,
    dash_llm_costs.model,
    COALESCE(dash_llm_costs.tokens_in, 0) AS tokens_in,
    COALESCE(dash_llm_costs.tokens_out, 0) AS tokens_out,
    COALESCE(dash_llm_costs.cost_usd, (0)::numeric) AS cost_usd,
        CASE
            WHEN dash_llm_costs.ok THEN 'ok'::text
            ELSE 'error'::text
        END AS status
   FROM public.dash_llm_costs
  WHERE (COALESCE(dash_llm_costs.task, ''::text) !~~ 'train%'::text)
UNION ALL
 SELECT 'training'::text AS src,
    dash_llm_costs.ts,
    'system'::text AS actor,
    NULL::text AS store_id,
    dash_llm_costs.model,
    COALESCE(dash_llm_costs.tokens_in, 0) AS tokens_in,
    COALESCE(dash_llm_costs.tokens_out, 0) AS tokens_out,
    COALESCE(dash_llm_costs.cost_usd, (0)::numeric) AS cost_usd,
        CASE
            WHEN dash_llm_costs.ok THEN 'ok'::text
            ELSE 'error'::text
        END AS status
   FROM public.dash_llm_costs
  WHERE (COALESCE(dash_llm_costs.task, ''::text) ~~ 'train%'::text)
UNION ALL
 SELECT
        CASE
            WHEN (dash_apigw_usage.request_type = 'embedding'::text) THEN 'embedding'::text
            ELSE 'api_key'::text
        END AS src,
    dash_apigw_usage.ts,
    COALESCE(dash_apigw_usage.service_account, 'unknown'::text) AS actor,
    dash_apigw_usage.store_id,
    dash_apigw_usage.model,
    COALESCE(dash_apigw_usage.prompt_tokens, 0) AS tokens_in,
    COALESCE(dash_apigw_usage.completion_tokens, 0) AS tokens_out,
    COALESCE(dash_apigw_usage.cost_usd, (0)::numeric) AS cost_usd,
    COALESCE(dash_apigw_usage.status, 'ok'::text) AS status
   FROM public.dash_apigw_usage
UNION ALL
 SELECT 'embed'::text AS src,
    dash_embed_calls.ts,
    COALESCE(dash_embed_calls.external_user, 'anon'::text) AS actor,
    NULL::text AS store_id,
    NULL::text AS model,
    COALESCE(dash_embed_calls.tokens_in, 0) AS tokens_in,
    COALESCE(dash_embed_calls.tokens_out, 0) AS tokens_out,
    COALESCE(dash_embed_calls.cost_usd, (0)::numeric) AS cost_usd,
        CASE
            WHEN dash_embed_calls.success THEN 'ok'::text
            ELSE 'error'::text
        END AS status
   FROM public.dash_embed_calls;


--
-- Name: VIEW v_usage_unified; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_usage_unified IS 'Normalized cross-source usage/cost spine (platform|training|api_key|embedding|embed) for the Admin Usage dashboard.';


--
-- Name: aos_capabilities id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.aos_capabilities ALTER COLUMN id SET DEFAULT nextval('dash.aos_capabilities_id_seq'::regclass);


--
-- Name: aos_cost_guard id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.aos_cost_guard ALTER COLUMN id SET DEFAULT nextval('dash.aos_cost_guard_id_seq'::regclass);


--
-- Name: aos_kill_switch id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.aos_kill_switch ALTER COLUMN id SET DEFAULT nextval('dash.aos_kill_switch_id_seq'::regclass);


--
-- Name: aos_models id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.aos_models ALTER COLUMN id SET DEFAULT nextval('dash.aos_models_id_seq'::regclass);


--
-- Name: aos_tool_registry id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.aos_tool_registry ALTER COLUMN id SET DEFAULT nextval('dash.aos_tool_registry_id_seq'::regclass);


--
-- Name: dash_agent_schedule_runs id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_agent_schedule_runs ALTER COLUMN id SET DEFAULT nextval('dash.dash_agent_schedule_runs_id_seq'::regclass);


--
-- Name: dash_agentic_state id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_agentic_state ALTER COLUMN id SET DEFAULT nextval('dash.dash_agentic_state_id_seq'::regclass);


--
-- Name: dash_anti_patterns id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_anti_patterns ALTER COLUMN id SET DEFAULT nextval('dash.dash_anti_patterns_id_seq'::regclass);


--
-- Name: dash_approval_audit id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_approval_audit ALTER COLUMN id SET DEFAULT nextval('dash.dash_approval_audit_id_seq'::regclass);


--
-- Name: dash_approval_signatures id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_approval_signatures ALTER COLUMN id SET DEFAULT nextval('dash.dash_approval_signatures_id_seq'::regclass);


--
-- Name: dash_attribution_credits id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_attribution_credits ALTER COLUMN id SET DEFAULT nextval('dash.dash_attribution_credits_id_seq'::regclass);


--
-- Name: dash_auto_apply_history id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_auto_apply_history ALTER COLUMN id SET DEFAULT nextval('dash.dash_auto_apply_history_id_seq'::regclass);


--
-- Name: dash_autonomous_workflows id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_autonomous_workflows ALTER COLUMN id SET DEFAULT nextval('dash.dash_autonomous_workflows_id_seq'::regclass);


--
-- Name: dash_autosim_runs id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_autosim_runs ALTER COLUMN id SET DEFAULT nextval('dash.dash_autosim_runs_id_seq'::regclass);


--
-- Name: dash_brainbench_corpus id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_brainbench_corpus ALTER COLUMN id SET DEFAULT nextval('dash.dash_brainbench_corpus_id_seq'::regclass);


--
-- Name: dash_campaign_events id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_campaign_events ALTER COLUMN id SET DEFAULT nextval('dash.dash_campaign_events_id_seq'::regclass);


--
-- Name: dash_campaigns id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_campaigns ALTER COLUMN id SET DEFAULT nextval('dash.dash_campaigns_id_seq'::regclass);


--
-- Name: dash_channel_messages id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_channel_messages ALTER COLUMN id SET DEFAULT nextval('dash.dash_channel_messages_id_seq'::regclass);


--
-- Name: dash_chemist_eval id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_chemist_eval ALTER COLUMN id SET DEFAULT nextval('dash.dash_chemist_eval_id_seq'::regclass);


--
-- Name: dash_compression_stats id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_compression_stats ALTER COLUMN id SET DEFAULT nextval('dash.dash_compression_stats_id_seq'::regclass);


--
-- Name: dash_connection_audit id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_connection_audit ALTER COLUMN id SET DEFAULT nextval('dash.dash_connection_audit_id_seq'::regclass);


--
-- Name: dash_conversions id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_conversions ALTER COLUMN id SET DEFAULT nextval('dash.dash_conversions_id_seq'::regclass);


--
-- Name: dash_correction_rules id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_correction_rules ALTER COLUMN id SET DEFAULT nextval('dash.dash_correction_rules_id_seq'::regclass);


--
-- Name: dash_corrections id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_corrections ALTER COLUMN id SET DEFAULT nextval('dash.dash_corrections_id_seq'::regclass);


--
-- Name: dash_customer_scores id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_customer_scores ALTER COLUMN id SET DEFAULT nextval('dash.dash_customer_scores_id_seq'::regclass);


--
-- Name: dash_deep_deck_gaps id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_deep_deck_gaps ALTER COLUMN id SET DEFAULT nextval('dash.dash_deep_deck_gaps_id_seq'::regclass);


--
-- Name: dash_deep_deck_queries id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_deep_deck_queries ALTER COLUMN id SET DEFAULT nextval('dash.dash_deep_deck_queries_id_seq'::regclass);


--
-- Name: dash_deep_deck_runs id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_deep_deck_runs ALTER COLUMN id SET DEFAULT nextval('dash.dash_deep_deck_runs_id_seq'::regclass);


--
-- Name: dash_dream_findings id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_dream_findings ALTER COLUMN id SET DEFAULT nextval('dash.dash_dream_findings_id_seq'::regclass);


--
-- Name: dash_dream_lite_runs id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_dream_lite_runs ALTER COLUMN id SET DEFAULT nextval('dash.dash_dream_lite_runs_id_seq'::regclass);


--
-- Name: dash_dream_precompute_cache id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_dream_precompute_cache ALTER COLUMN id SET DEFAULT nextval('dash.dash_dream_precompute_cache_id_seq'::regclass);


--
-- Name: dash_dream_runs id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_dream_runs ALTER COLUMN id SET DEFAULT nextval('dash.dash_dream_runs_id_seq'::regclass);


--
-- Name: dash_entities id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_entities ALTER COLUMN id SET DEFAULT nextval('dash.dash_entities_id_seq'::regclass);


--
-- Name: dash_entity_links id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_entity_links ALTER COLUMN id SET DEFAULT nextval('dash.dash_entity_links_id_seq'::regclass);


--
-- Name: dash_entity_memory id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_entity_memory ALTER COLUMN id SET DEFAULT nextval('dash.dash_entity_memory_id_seq'::regclass);


--
-- Name: dash_episode_buffer id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_episode_buffer ALTER COLUMN id SET DEFAULT nextval('dash.dash_episode_buffer_id_seq'::regclass);


--
-- Name: dash_eval_baselines id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_eval_baselines ALTER COLUMN id SET DEFAULT nextval('dash.dash_eval_baselines_id_seq'::regclass);


--
-- Name: dash_eval_results id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_eval_results ALTER COLUMN id SET DEFAULT nextval('dash.dash_eval_results_id_seq'::regclass);


--
-- Name: dash_hook_audit id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_hook_audit ALTER COLUMN id SET DEFAULT nextval('dash.dash_hook_audit_id_seq'::regclass);


--
-- Name: dash_investment_memos id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_investment_memos ALTER COLUMN id SET DEFAULT nextval('dash.dash_investment_memos_id_seq'::regclass);


--
-- Name: dash_investment_runs id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_investment_runs ALTER COLUMN id SET DEFAULT nextval('dash.dash_investment_runs_id_seq'::regclass);


--
-- Name: dash_knowledge_triples id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_knowledge_triples ALTER COLUMN id SET DEFAULT nextval('dash.dash_knowledge_triples_id_seq'::regclass);


--
-- Name: dash_llm_keys id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_llm_keys ALTER COLUMN id SET DEFAULT nextval('dash.dash_llm_keys_id_seq'::regclass);


--
-- Name: dash_mcp_invocations id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_mcp_invocations ALTER COLUMN id SET DEFAULT nextval('dash.dash_mcp_invocations_id_seq'::regclass);


--
-- Name: dash_mcp_tool_bindings id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_mcp_tool_bindings ALTER COLUMN id SET DEFAULT nextval('dash.dash_mcp_tool_bindings_id_seq'::regclass);


--
-- Name: dash_minions id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_minions ALTER COLUMN id SET DEFAULT nextval('dash.dash_minions_id_seq'::regclass);


--
-- Name: dash_page_evidence id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_page_evidence ALTER COLUMN id SET DEFAULT nextval('dash.dash_page_evidence_id_seq'::regclass);


--
-- Name: dash_pages id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_pages ALTER COLUMN id SET DEFAULT nextval('dash.dash_pages_id_seq'::regclass);


--
-- Name: dash_pii_audit id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_pii_audit ALTER COLUMN id SET DEFAULT nextval('dash.dash_pii_audit_id_seq'::regclass);


--
-- Name: dash_refusal_marks id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_refusal_marks ALTER COLUMN id SET DEFAULT nextval('dash.dash_refusal_marks_id_seq'::regclass);


--
-- Name: dash_rls_audit id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_rls_audit ALTER COLUMN id SET DEFAULT nextval('dash.dash_rls_audit_id_seq'::regclass);


--
-- Name: dash_run_context_audit id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_run_context_audit ALTER COLUMN id SET DEFAULT nextval('dash.dash_run_context_audit_id_seq'::regclass);


--
-- Name: dash_search_log id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_search_log ALTER COLUMN id SET DEFAULT nextval('dash.dash_search_log_id_seq'::regclass);


--
-- Name: dash_secret_leaks id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_secret_leaks ALTER COLUMN id SET DEFAULT nextval('dash.dash_secret_leaks_id_seq'::regclass);


--
-- Name: dash_segment_snapshots id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_segment_snapshots ALTER COLUMN id SET DEFAULT nextval('dash.dash_segment_snapshots_id_seq'::regclass);


--
-- Name: dash_sim_recommendations id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_sim_recommendations ALTER COLUMN id SET DEFAULT nextval('dash.dash_sim_recommendations_id_seq'::regclass);


--
-- Name: dash_skill_audit_log id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_skill_audit_log ALTER COLUMN id SET DEFAULT nextval('dash.dash_skill_audit_log_id_seq'::regclass);


--
-- Name: dash_skill_bindings id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_skill_bindings ALTER COLUMN id SET DEFAULT nextval('dash.dash_skill_bindings_id_seq'::regclass);


--
-- Name: dash_skill_invocations id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_skill_invocations ALTER COLUMN id SET DEFAULT nextval('dash.dash_skill_invocations_id_seq'::regclass);


--
-- Name: dash_skill_marketplace id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_skill_marketplace ALTER COLUMN id SET DEFAULT nextval('dash.dash_skill_marketplace_id_seq'::regclass);


--
-- Name: dash_slack_channel_routes id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_slack_channel_routes ALTER COLUMN id SET DEFAULT nextval('dash.dash_slack_channel_routes_id_seq'::regclass);


--
-- Name: dash_slide_critique id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_slide_critique ALTER COLUMN id SET DEFAULT nextval('dash.dash_slide_critique_id_seq'::regclass);


--
-- Name: dash_slide_templates id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_slide_templates ALTER COLUMN id SET DEFAULT nextval('dash.dash_slide_templates_id_seq'::regclass);


--
-- Name: dash_sql_validator_events id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_sql_validator_events ALTER COLUMN id SET DEFAULT nextval('dash.dash_sql_validator_events_id_seq'::regclass);


--
-- Name: dash_subagent_runs id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_subagent_runs ALTER COLUMN id SET DEFAULT nextval('dash.dash_subagent_runs_id_seq'::regclass);


--
-- Name: dash_subscription_snapshots id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_subscription_snapshots ALTER COLUMN id SET DEFAULT nextval('dash.dash_subscription_snapshots_id_seq'::regclass);


--
-- Name: dash_tool_utility_scores id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_tool_utility_scores ALTER COLUMN id SET DEFAULT nextval('dash.dash_tool_utility_scores_id_seq'::regclass);


--
-- Name: dash_touchpoints id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_touchpoints ALTER COLUMN id SET DEFAULT nextval('dash.dash_touchpoints_id_seq'::regclass);


--
-- Name: dash_vector_audit id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_vector_audit ALTER COLUMN id SET DEFAULT nextval('dash.dash_vector_audit_id_seq'::regclass);


--
-- Name: dash_vectors id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_vectors ALTER COLUMN id SET DEFAULT nextval('dash.dash_vectors_id_seq'::regclass);


--
-- Name: dash_vertical_pack_history id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_vertical_pack_history ALTER COLUMN id SET DEFAULT nextval('dash.dash_vertical_pack_history_id_seq'::regclass);


--
-- Name: dash_workflow_run_steps id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_workflow_run_steps ALTER COLUMN id SET DEFAULT nextval('dash.dash_workflow_run_steps_id_seq'::regclass);


--
-- Name: dash_workflow_schedules id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_workflow_schedules ALTER COLUMN id SET DEFAULT nextval('dash.dash_workflow_schedules_id_seq'::regclass);


--
-- Name: training_signals id; Type: DEFAULT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.training_signals ALTER COLUMN id SET DEFAULT nextval('dash.training_signals_id_seq'::regclass);


--
-- Name: dash_action_registry id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_action_registry ALTER COLUMN id SET DEFAULT nextval('public.dash_action_registry_id_seq'::regclass);


--
-- Name: dash_admin_settings id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_admin_settings ALTER COLUMN id SET DEFAULT nextval('public.dash_admin_settings_id_seq'::regclass);


--
-- Name: dash_agent_embeds id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_agent_embeds ALTER COLUMN id SET DEFAULT nextval('public.dash_agent_embeds_id_seq'::regclass);


--
-- Name: dash_agent_registry id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_agent_registry ALTER COLUMN id SET DEFAULT nextval('public.dash_agent_registry_id_seq'::regclass);


--
-- Name: dash_annotations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_annotations ALTER COLUMN id SET DEFAULT nextval('public.dash_annotations_id_seq'::regclass);


--
-- Name: dash_apigw_messages id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_apigw_messages ALTER COLUMN id SET DEFAULT nextval('public.dash_apigw_messages_id_seq'::regclass);


--
-- Name: dash_apigw_usage id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_apigw_usage ALTER COLUMN id SET DEFAULT nextval('public.dash_apigw_usage_id_seq'::regclass);


--
-- Name: dash_audit_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_audit_log ALTER COLUMN id SET DEFAULT nextval('public.dash_audit_log_id_seq'::regclass);


--
-- Name: dash_backup_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_backup_runs ALTER COLUMN id SET DEFAULT nextval('public.dash_backup_runs_id_seq'::regclass);


--
-- Name: dash_brain_access_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_brain_access_log ALTER COLUMN id SET DEFAULT nextval('public.dash_brain_access_log_id_seq'::regclass);


--
-- Name: dash_brain_versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_brain_versions ALTER COLUMN id SET DEFAULT nextval('public.dash_brain_versions_id_seq'::regclass);


--
-- Name: dash_business_rules_db id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_business_rules_db ALTER COLUMN id SET DEFAULT nextval('public.dash_business_rules_db_id_seq'::regclass);


--
-- Name: dash_chat_sessions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_chat_sessions ALTER COLUMN id SET DEFAULT nextval('public.dash_chat_sessions_id_seq'::regclass);


--
-- Name: dash_company_brain id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_company_brain ALTER COLUMN id SET DEFAULT nextval('public.dash_company_brain_id_seq'::regclass);


--
-- Name: dash_dashboard_audit id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_dashboard_audit ALTER COLUMN id SET DEFAULT nextval('public.dash_dashboard_audit_id_seq'::regclass);


--
-- Name: dash_dashboard_skill_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_dashboard_skill_runs ALTER COLUMN id SET DEFAULT nextval('public.dash_dashboard_skill_runs_id_seq'::regclass);


--
-- Name: dash_dashboards id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_dashboards ALTER COLUMN id SET DEFAULT nextval('public.dash_dashboards_id_seq'::regclass);


--
-- Name: dash_decisions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_decisions ALTER COLUMN id SET DEFAULT nextval('public.dash_decisions_id_seq'::regclass);


--
-- Name: dash_deck_schedules id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_deck_schedules ALTER COLUMN id SET DEFAULT nextval('public.dash_deck_schedules_id_seq'::regclass);


--
-- Name: dash_documents id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_documents ALTER COLUMN id SET DEFAULT nextval('public.dash_documents_id_seq'::regclass);


--
-- Name: dash_drift_alerts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_drift_alerts ALTER COLUMN id SET DEFAULT nextval('public.dash_drift_alerts_id_seq'::regclass);


--
-- Name: dash_embed_calls id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_embed_calls ALTER COLUMN id SET DEFAULT nextval('public.dash_embed_calls_id_seq'::regclass);


--
-- Name: dash_embed_rls_audit id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_embed_rls_audit ALTER COLUMN id SET DEFAULT nextval('public.dash_embed_rls_audit_id_seq'::regclass);


--
-- Name: dash_embed_rls_blueprints id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_embed_rls_blueprints ALTER COLUMN id SET DEFAULT nextval('public.dash_embed_rls_blueprints_id_seq'::regclass);


--
-- Name: dash_embed_sessions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_embed_sessions ALTER COLUMN id SET DEFAULT nextval('public.dash_embed_sessions_id_seq'::regclass);


--
-- Name: dash_eval_history id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_eval_history ALTER COLUMN id SET DEFAULT nextval('public.dash_eval_history_id_seq'::regclass);


--
-- Name: dash_eval_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_eval_runs ALTER COLUMN id SET DEFAULT nextval('public.dash_eval_runs_id_seq'::regclass);


--
-- Name: dash_evals id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_evals ALTER COLUMN id SET DEFAULT nextval('public.dash_evals_id_seq'::regclass);


--
-- Name: dash_evolution_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_evolution_runs ALTER COLUMN id SET DEFAULT nextval('public.dash_evolution_runs_id_seq'::regclass);


--
-- Name: dash_evolved_instructions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_evolved_instructions ALTER COLUMN id SET DEFAULT nextval('public.dash_evolved_instructions_id_seq'::regclass);


--
-- Name: dash_external_facts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_external_facts ALTER COLUMN id SET DEFAULT nextval('public.dash_external_facts_id_seq'::regclass);


--
-- Name: dash_extraction_plans id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_extraction_plans ALTER COLUMN id SET DEFAULT nextval('public.dash_extraction_plans_id_seq'::regclass);


--
-- Name: dash_feedback id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_feedback ALTER COLUMN id SET DEFAULT nextval('public.dash_feedback_id_seq'::regclass);


--
-- Name: dash_guardrail_audit id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_guardrail_audit ALTER COLUMN id SET DEFAULT nextval('public.dash_guardrail_audit_id_seq'::regclass);


--
-- Name: dash_hitl_requests id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_hitl_requests ALTER COLUMN id SET DEFAULT nextval('public.dash_hitl_requests_id_seq'::regclass);


--
-- Name: dash_ingest_contracts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_ingest_contracts ALTER COLUMN id SET DEFAULT nextval('public.dash_ingest_contracts_id_seq'::regclass);


--
-- Name: dash_ingest_files id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_ingest_files ALTER COLUMN id SET DEFAULT nextval('public.dash_ingest_files_id_seq'::regclass);


--
-- Name: dash_knowledge_triples id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_knowledge_triples ALTER COLUMN id SET DEFAULT nextval('public.dash_knowledge_triples_id_seq'::regclass);


--
-- Name: dash_llm_costs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_llm_costs ALTER COLUMN id SET DEFAULT nextval('public.dash_llm_costs_id_seq'::regclass);


--
-- Name: dash_memories id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_memories ALTER COLUMN id SET DEFAULT nextval('public.dash_memories_id_seq'::regclass);


--
-- Name: dash_meta_learnings id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_meta_learnings ALTER COLUMN id SET DEFAULT nextval('public.dash_meta_learnings_id_seq'::regclass);


--
-- Name: dash_metric_definitions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_metric_definitions ALTER COLUMN id SET DEFAULT nextval('public.dash_metric_definitions_id_seq'::regclass);


--
-- Name: dash_metric_versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_metric_versions ALTER COLUMN id SET DEFAULT nextval('public.dash_metric_versions_id_seq'::regclass);


--
-- Name: dash_notifications id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_notifications ALTER COLUMN id SET DEFAULT nextval('public.dash_notifications_id_seq'::regclass);


--
-- Name: dash_ontology_api_calls id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_ontology_api_calls ALTER COLUMN id SET DEFAULT nextval('public.dash_ontology_api_calls_id_seq'::regclass);


--
-- Name: dash_ontology_api_keys id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_ontology_api_keys ALTER COLUMN id SET DEFAULT nextval('public.dash_ontology_api_keys_id_seq'::regclass);


--
-- Name: dash_personas id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_personas ALTER COLUMN id SET DEFAULT nextval('public.dash_personas_id_seq'::regclass);


--
-- Name: dash_presentations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_presentations ALTER COLUMN id SET DEFAULT nextval('public.dash_presentations_id_seq'::regclass);


--
-- Name: dash_proactive_insights id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_proactive_insights ALTER COLUMN id SET DEFAULT nextval('public.dash_proactive_insights_id_seq'::regclass);


--
-- Name: dash_project_shares id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_project_shares ALTER COLUMN id SET DEFAULT nextval('public.dash_project_shares_id_seq'::regclass);


--
-- Name: dash_projects id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_projects ALTER COLUMN id SET DEFAULT nextval('public.dash_projects_id_seq'::regclass);


--
-- Name: dash_quality_scores id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_quality_scores ALTER COLUMN id SET DEFAULT nextval('public.dash_quality_scores_id_seq'::regclass);


--
-- Name: dash_query_patterns id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_query_patterns ALTER COLUMN id SET DEFAULT nextval('public.dash_query_patterns_id_seq'::regclass);


--
-- Name: dash_query_plans id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_query_plans ALTER COLUMN id SET DEFAULT nextval('public.dash_query_plans_id_seq'::regclass);


--
-- Name: dash_relationships id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_relationships ALTER COLUMN id SET DEFAULT nextval('public.dash_relationships_id_seq'::regclass);


--
-- Name: dash_resource_registry id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_resource_registry ALTER COLUMN id SET DEFAULT nextval('public.dash_resource_registry_id_seq'::regclass);


--
-- Name: dash_rules_db id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_rules_db ALTER COLUMN id SET DEFAULT nextval('public.dash_rules_db_id_seq'::regclass);


--
-- Name: dash_schedules id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_schedules ALTER COLUMN id SET DEFAULT nextval('public.dash_schedules_id_seq'::regclass);


--
-- Name: dash_security_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_security_events ALTER COLUMN id SET DEFAULT nextval('public.dash_security_events_id_seq'::regclass);


--
-- Name: dash_self_learning_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_self_learning_runs ALTER COLUMN id SET DEFAULT nextval('public.dash_self_learning_runs_id_seq'::regclass);


--
-- Name: dash_skill_overrides id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_skill_overrides ALTER COLUMN id SET DEFAULT nextval('public.dash_skill_overrides_id_seq'::regclass);


--
-- Name: dash_sse_audit id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_sse_audit ALTER COLUMN id SET DEFAULT nextval('public.dash_sse_audit_id_seq'::regclass);


--
-- Name: dash_suggested_rules id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_suggested_rules ALTER COLUMN id SET DEFAULT nextval('public.dash_suggested_rules_id_seq'::regclass);


--
-- Name: dash_table_metadata id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_table_metadata ALTER COLUMN id SET DEFAULT nextval('public.dash_table_metadata_id_seq'::regclass);


--
-- Name: dash_tool_patches id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_tool_patches ALTER COLUMN id SET DEFAULT nextval('public.dash_tool_patches_id_seq'::regclass);


--
-- Name: dash_tool_scores id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_tool_scores ALTER COLUMN id SET DEFAULT nextval('public.dash_tool_scores_id_seq'::regclass);


--
-- Name: dash_tool_utility_scores id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_tool_utility_scores ALTER COLUMN id SET DEFAULT nextval('public.dash_tool_utility_scores_id_seq'::regclass);


--
-- Name: dash_traces id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_traces ALTER COLUMN id SET DEFAULT nextval('public.dash_traces_id_seq'::regclass);


--
-- Name: dash_training_jobs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_training_jobs ALTER COLUMN id SET DEFAULT nextval('public.dash_training_jobs_id_seq'::regclass);


--
-- Name: dash_training_qa id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_training_qa ALTER COLUMN id SET DEFAULT nextval('public.dash_training_qa_id_seq'::regclass);


--
-- Name: dash_training_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_training_runs ALTER COLUMN id SET DEFAULT nextval('public.dash_training_runs_id_seq'::regclass);


--
-- Name: dash_training_steps id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_training_steps ALTER COLUMN id SET DEFAULT nextval('public.dash_training_steps_id_seq'::regclass);


--
-- Name: dash_user_preferences id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_user_preferences ALTER COLUMN id SET DEFAULT nextval('public.dash_user_preferences_id_seq'::regclass);


--
-- Name: dash_user_roles id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_user_roles ALTER COLUMN id SET DEFAULT nextval('public.dash_user_roles_id_seq'::regclass);


--
-- Name: dash_user_scopes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_user_scopes ALTER COLUMN id SET DEFAULT nextval('public.dash_user_scopes_id_seq'::regclass);


--
-- Name: dash_users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_users ALTER COLUMN id SET DEFAULT nextval('public.dash_users_id_seq'::regclass);


--
-- Name: dash_verified_scores id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_verified_scores ALTER COLUMN id SET DEFAULT nextval('public.dash_verified_scores_id_seq'::regclass);


--
-- Name: dash_visibility_policy_drafts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_visibility_policy_drafts ALTER COLUMN id SET DEFAULT nextval('public.dash_visibility_policy_drafts_id_seq'::regclass);


--
-- Name: dash_visibility_policy_history id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_visibility_policy_history ALTER COLUMN id SET DEFAULT nextval('public.dash_visibility_policy_history_id_seq'::regclass);


--
-- Name: dash_visibility_read_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_visibility_read_log ALTER COLUMN id SET DEFAULT nextval('public.dash_visibility_read_log_id_seq'::regclass);


--
-- Name: dash_visibility_roles id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_visibility_roles ALTER COLUMN id SET DEFAULT nextval('public.dash_visibility_roles_id_seq'::regclass);


--
-- Name: dash_workflow_runs_v2 id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_workflow_runs_v2 ALTER COLUMN id SET DEFAULT nextval('public.dash_workflow_runs_v2_id_seq'::regclass);


--
-- Name: dash_workflows_db id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_workflows_db ALTER COLUMN id SET DEFAULT nextval('public.dash_workflows_db_id_seq'::regclass);


--
-- Name: shop_flat shop_flat_pkey; Type: CONSTRAINT; Schema: citypharma; Owner: -
--

ALTER TABLE ONLY citypharma.shop_flat
    ADD CONSTRAINT shop_flat_pkey PRIMARY KEY (art_key, site_code);


--
-- Name: aos_capabilities aos_capabilities_name_key; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.aos_capabilities
    ADD CONSTRAINT aos_capabilities_name_key UNIQUE (name);


--
-- Name: aos_capabilities aos_capabilities_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.aos_capabilities
    ADD CONSTRAINT aos_capabilities_pkey PRIMARY KEY (id);


--
-- Name: aos_cost_guard aos_cost_guard_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.aos_cost_guard
    ADD CONSTRAINT aos_cost_guard_pkey PRIMARY KEY (id);


--
-- Name: aos_kill_switch aos_kill_switch_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.aos_kill_switch
    ADD CONSTRAINT aos_kill_switch_pkey PRIMARY KEY (id);


--
-- Name: aos_models aos_models_name_key; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.aos_models
    ADD CONSTRAINT aos_models_name_key UNIQUE (name);


--
-- Name: aos_models aos_models_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.aos_models
    ADD CONSTRAINT aos_models_pkey PRIMARY KEY (id);


--
-- Name: aos_tool_registry aos_tool_registry_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.aos_tool_registry
    ADD CONSTRAINT aos_tool_registry_pkey PRIMARY KEY (id);


--
-- Name: aos_tool_registry aos_tool_registry_tool_name_key; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.aos_tool_registry
    ADD CONSTRAINT aos_tool_registry_tool_name_key UNIQUE (tool_name);


--
-- Name: dash_agent_schedule_runs dash_agent_schedule_runs_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_agent_schedule_runs
    ADD CONSTRAINT dash_agent_schedule_runs_pkey PRIMARY KEY (id);


--
-- Name: dash_agent_schedules dash_agent_schedules_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_agent_schedules
    ADD CONSTRAINT dash_agent_schedules_pkey PRIMARY KEY (id);


--
-- Name: dash_agentic_state dash_agentic_state_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_agentic_state
    ADD CONSTRAINT dash_agentic_state_pkey PRIMARY KEY (id);


--
-- Name: dash_agentic_state dash_agentic_state_session_id_agent_name_key_key; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_agentic_state
    ADD CONSTRAINT dash_agentic_state_session_id_agent_name_key_key UNIQUE (session_id, agent_name, key);


--
-- Name: dash_anti_patterns dash_anti_patterns_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_anti_patterns
    ADD CONSTRAINT dash_anti_patterns_pkey PRIMARY KEY (id);


--
-- Name: dash_approval_audit dash_approval_audit_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_approval_audit
    ADD CONSTRAINT dash_approval_audit_pkey PRIMARY KEY (id);


--
-- Name: dash_approval_requests dash_approval_requests_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_approval_requests
    ADD CONSTRAINT dash_approval_requests_pkey PRIMARY KEY (id);


--
-- Name: dash_approval_signatures dash_approval_signatures_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_approval_signatures
    ADD CONSTRAINT dash_approval_signatures_pkey PRIMARY KEY (id);


--
-- Name: dash_approval_signatures dash_approval_signatures_request_id_approver_id_key; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_approval_signatures
    ADD CONSTRAINT dash_approval_signatures_request_id_approver_id_key UNIQUE (request_id, approver_id);


--
-- Name: dash_attribution_credits dash_attribution_credits_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_attribution_credits
    ADD CONSTRAINT dash_attribution_credits_pkey PRIMARY KEY (id);


--
-- Name: dash_auto_apply_history dash_auto_apply_history_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_auto_apply_history
    ADD CONSTRAINT dash_auto_apply_history_pkey PRIMARY KEY (id);


--
-- Name: dash_autonomous_workflows dash_autonomous_workflows_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_autonomous_workflows
    ADD CONSTRAINT dash_autonomous_workflows_pkey PRIMARY KEY (id);


--
-- Name: dash_autosim_runs dash_autosim_runs_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_autosim_runs
    ADD CONSTRAINT dash_autosim_runs_pkey PRIMARY KEY (id);


--
-- Name: dash_brainbench_corpus dash_brainbench_corpus_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_brainbench_corpus
    ADD CONSTRAINT dash_brainbench_corpus_pkey PRIMARY KEY (id);


--
-- Name: dash_campaign_events dash_campaign_events_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_campaign_events
    ADD CONSTRAINT dash_campaign_events_pkey PRIMARY KEY (id);


--
-- Name: dash_campaigns dash_campaigns_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_campaigns
    ADD CONSTRAINT dash_campaigns_pkey PRIMARY KEY (id);


--
-- Name: dash_channel_messages dash_channel_messages_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_channel_messages
    ADD CONSTRAINT dash_channel_messages_pkey PRIMARY KEY (id);


--
-- Name: dash_channel_threads dash_channel_threads_channel_kind_external_id_key; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_channel_threads
    ADD CONSTRAINT dash_channel_threads_channel_kind_external_id_key UNIQUE (channel_kind, external_id);


--
-- Name: dash_channel_threads dash_channel_threads_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_channel_threads
    ADD CONSTRAINT dash_channel_threads_pkey PRIMARY KEY (id);


--
-- Name: dash_chemist_eval dash_chemist_eval_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_chemist_eval
    ADD CONSTRAINT dash_chemist_eval_pkey PRIMARY KEY (id);


--
-- Name: dash_compression_cache dash_compression_cache_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_compression_cache
    ADD CONSTRAINT dash_compression_cache_pkey PRIMARY KEY (cache_key);


--
-- Name: dash_compression_stats dash_compression_stats_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_compression_stats
    ADD CONSTRAINT dash_compression_stats_pkey PRIMARY KEY (id);


--
-- Name: dash_connection_audit dash_connection_audit_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_connection_audit
    ADD CONSTRAINT dash_connection_audit_pkey PRIMARY KEY (id);


--
-- Name: dash_connections dash_connections_name_key; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_connections
    ADD CONSTRAINT dash_connections_name_key UNIQUE (name);


--
-- Name: dash_connections dash_connections_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_connections
    ADD CONSTRAINT dash_connections_pkey PRIMARY KEY (id);


--
-- Name: dash_conversions dash_conversions_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_conversions
    ADD CONSTRAINT dash_conversions_pkey PRIMARY KEY (id);


--
-- Name: dash_correction_rules dash_correction_rules_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_correction_rules
    ADD CONSTRAINT dash_correction_rules_pkey PRIMARY KEY (id);


--
-- Name: dash_corrections dash_corrections_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_corrections
    ADD CONSTRAINT dash_corrections_pkey PRIMARY KEY (id);


--
-- Name: dash_custom_agents dash_custom_agents_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_custom_agents
    ADD CONSTRAINT dash_custom_agents_pkey PRIMARY KEY (id);


--
-- Name: dash_custom_agents dash_custom_agents_project_slug_name_key; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_custom_agents
    ADD CONSTRAINT dash_custom_agents_project_slug_name_key UNIQUE (project_slug, name);


--
-- Name: dash_customer_scores dash_customer_scores_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_customer_scores
    ADD CONSTRAINT dash_customer_scores_pkey PRIMARY KEY (id);


--
-- Name: dash_customer_scores dash_customer_scores_project_slug_customer_id_key; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_customer_scores
    ADD CONSTRAINT dash_customer_scores_project_slug_customer_id_key UNIQUE (project_slug, customer_id);


--
-- Name: dash_daemon_leader dash_daemon_leader_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_daemon_leader
    ADD CONSTRAINT dash_daemon_leader_pkey PRIMARY KEY (id);


--
-- Name: dash_deep_deck_gaps dash_deep_deck_gaps_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_deep_deck_gaps
    ADD CONSTRAINT dash_deep_deck_gaps_pkey PRIMARY KEY (id);


--
-- Name: dash_deep_deck_queries dash_deep_deck_queries_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_deep_deck_queries
    ADD CONSTRAINT dash_deep_deck_queries_pkey PRIMARY KEY (id);


--
-- Name: dash_deep_deck_runs dash_deep_deck_runs_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_deep_deck_runs
    ADD CONSTRAINT dash_deep_deck_runs_pkey PRIMARY KEY (id);


--
-- Name: dash_dp_budget dash_dp_budget_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_dp_budget
    ADD CONSTRAINT dash_dp_budget_pkey PRIMARY KEY (project_slug, user_id, date);


--
-- Name: dash_dream_findings dash_dream_findings_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_dream_findings
    ADD CONSTRAINT dash_dream_findings_pkey PRIMARY KEY (id);


--
-- Name: dash_dream_lite_runs dash_dream_lite_runs_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_dream_lite_runs
    ADD CONSTRAINT dash_dream_lite_runs_pkey PRIMARY KEY (id);


--
-- Name: dash_dream_precompute_cache dash_dream_precompute_cache_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_dream_precompute_cache
    ADD CONSTRAINT dash_dream_precompute_cache_pkey PRIMARY KEY (id);


--
-- Name: dash_dream_runs dash_dream_runs_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_dream_runs
    ADD CONSTRAINT dash_dream_runs_pkey PRIMARY KEY (id);


--
-- Name: dash_email_accounts dash_email_accounts_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_email_accounts
    ADD CONSTRAINT dash_email_accounts_pkey PRIMARY KEY (id);


--
-- Name: dash_entities dash_entities_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_entities
    ADD CONSTRAINT dash_entities_pkey PRIMARY KEY (id);


--
-- Name: dash_entity_links dash_entity_links_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_entity_links
    ADD CONSTRAINT dash_entity_links_pkey PRIMARY KEY (id);


--
-- Name: dash_entity_memory dash_entity_memory_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_entity_memory
    ADD CONSTRAINT dash_entity_memory_pkey PRIMARY KEY (id);


--
-- Name: dash_episode_buffer dash_episode_buffer_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_episode_buffer
    ADD CONSTRAINT dash_episode_buffer_pkey PRIMARY KEY (id);


--
-- Name: dash_eval_baselines dash_eval_baselines_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_eval_baselines
    ADD CONSTRAINT dash_eval_baselines_pkey PRIMARY KEY (id);


--
-- Name: dash_eval_baselines dash_eval_baselines_suite_id_set_at_key; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_eval_baselines
    ADD CONSTRAINT dash_eval_baselines_suite_id_set_at_key UNIQUE (suite_id, set_at);


--
-- Name: dash_eval_cases dash_eval_cases_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_eval_cases
    ADD CONSTRAINT dash_eval_cases_pkey PRIMARY KEY (id);


--
-- Name: dash_eval_results dash_eval_results_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_eval_results
    ADD CONSTRAINT dash_eval_results_pkey PRIMARY KEY (id);


--
-- Name: dash_eval_runs dash_eval_runs_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_eval_runs
    ADD CONSTRAINT dash_eval_runs_pkey PRIMARY KEY (id);


--
-- Name: dash_eval_suites dash_eval_suites_name_project_slug_key; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_eval_suites
    ADD CONSTRAINT dash_eval_suites_name_project_slug_key UNIQUE (name, project_slug);


--
-- Name: dash_eval_suites dash_eval_suites_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_eval_suites
    ADD CONSTRAINT dash_eval_suites_pkey PRIMARY KEY (id);


--
-- Name: dash_generated_files dash_generated_files_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_generated_files
    ADD CONSTRAINT dash_generated_files_pkey PRIMARY KEY (id);


--
-- Name: dash_hitl_pending dash_hitl_pending_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_hitl_pending
    ADD CONSTRAINT dash_hitl_pending_pkey PRIMARY KEY (run_id);


--
-- Name: dash_hook_audit dash_hook_audit_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_hook_audit
    ADD CONSTRAINT dash_hook_audit_pkey PRIMARY KEY (id);


--
-- Name: dash_investment_memos dash_investment_memos_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_investment_memos
    ADD CONSTRAINT dash_investment_memos_pkey PRIMARY KEY (id);


--
-- Name: dash_investment_runs dash_investment_runs_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_investment_runs
    ADD CONSTRAINT dash_investment_runs_pkey PRIMARY KEY (id);


--
-- Name: dash_knowledge_triples dash_knowledge_triples_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_knowledge_triples
    ADD CONSTRAINT dash_knowledge_triples_pkey PRIMARY KEY (id);


--
-- Name: dash_links dash_links_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_links
    ADD CONSTRAINT dash_links_pkey PRIMARY KEY (src_type, src_id, dst_type, dst_id, rel);


--
-- Name: dash_llm_keys dash_llm_keys_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_llm_keys
    ADD CONSTRAINT dash_llm_keys_pkey PRIMARY KEY (id);


--
-- Name: dash_llm_model_catalog dash_llm_model_catalog_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_llm_model_catalog
    ADD CONSTRAINT dash_llm_model_catalog_pkey PRIMARY KEY (id);


--
-- Name: dash_mcp_invocations dash_mcp_invocations_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_mcp_invocations
    ADD CONSTRAINT dash_mcp_invocations_pkey PRIMARY KEY (id);


--
-- Name: dash_mcp_servers dash_mcp_servers_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_mcp_servers
    ADD CONSTRAINT dash_mcp_servers_pkey PRIMARY KEY (id);


--
-- Name: dash_mcp_tool_bindings dash_mcp_tool_bindings_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_mcp_tool_bindings
    ADD CONSTRAINT dash_mcp_tool_bindings_pkey PRIMARY KEY (id);


--
-- Name: dash_mcp_tool_bindings dash_mcp_tool_bindings_server_id_agent_name_tool_name_key; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_mcp_tool_bindings
    ADD CONSTRAINT dash_mcp_tool_bindings_server_id_agent_name_tool_name_key UNIQUE (server_id, agent_name, tool_name);


--
-- Name: dash_minions dash_minions_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_minions
    ADD CONSTRAINT dash_minions_pkey PRIMARY KEY (id);


--
-- Name: dash_packs dash_packs_name_key; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_packs
    ADD CONSTRAINT dash_packs_name_key UNIQUE (name);


--
-- Name: dash_packs dash_packs_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_packs
    ADD CONSTRAINT dash_packs_pkey PRIMARY KEY (id);


--
-- Name: dash_page_evidence dash_page_evidence_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_page_evidence
    ADD CONSTRAINT dash_page_evidence_pkey PRIMARY KEY (id);


--
-- Name: dash_pages dash_pages_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_pages
    ADD CONSTRAINT dash_pages_pkey PRIMARY KEY (id);


--
-- Name: dash_pages dash_pages_project_slug_page_key_key; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_pages
    ADD CONSTRAINT dash_pages_project_slug_page_key_key UNIQUE (project_slug, page_key);


--
-- Name: dash_pii_audit dash_pii_audit_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_pii_audit
    ADD CONSTRAINT dash_pii_audit_pkey PRIMARY KEY (id);


--
-- Name: dash_project_rls_config dash_project_rls_config_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_project_rls_config
    ADD CONSTRAINT dash_project_rls_config_pkey PRIMARY KEY (project_slug);


--
-- Name: dash_refusal_marks dash_refusal_marks_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_refusal_marks
    ADD CONSTRAINT dash_refusal_marks_pkey PRIMARY KEY (id);


--
-- Name: dash_rls_audit dash_rls_audit_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_rls_audit
    ADD CONSTRAINT dash_rls_audit_pkey PRIMARY KEY (id);


--
-- Name: dash_run_context_audit dash_run_context_audit_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_run_context_audit
    ADD CONSTRAINT dash_run_context_audit_pkey PRIMARY KEY (id);


--
-- Name: dash_search_log dash_search_log_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_search_log
    ADD CONSTRAINT dash_search_log_pkey PRIMARY KEY (id);


--
-- Name: dash_secret_leaks dash_secret_leaks_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_secret_leaks
    ADD CONSTRAINT dash_secret_leaks_pkey PRIMARY KEY (id);


--
-- Name: dash_segment_snapshots dash_segment_snapshots_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_segment_snapshots
    ADD CONSTRAINT dash_segment_snapshots_pkey PRIMARY KEY (id);


--
-- Name: dash_sim_recommendations dash_sim_recommendations_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_sim_recommendations
    ADD CONSTRAINT dash_sim_recommendations_pkey PRIMARY KEY (id);


--
-- Name: dash_skill_audit_log dash_skill_audit_log_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_skill_audit_log
    ADD CONSTRAINT dash_skill_audit_log_pkey PRIMARY KEY (id);


--
-- Name: dash_skill_bindings dash_skill_bindings_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_skill_bindings
    ADD CONSTRAINT dash_skill_bindings_pkey PRIMARY KEY (id);


--
-- Name: dash_skill_bindings dash_skill_bindings_skill_id_agent_name_key; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_skill_bindings
    ADD CONSTRAINT dash_skill_bindings_skill_id_agent_name_key UNIQUE (skill_id, agent_name);


--
-- Name: dash_skill_drafts dash_skill_drafts_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_skill_drafts
    ADD CONSTRAINT dash_skill_drafts_pkey PRIMARY KEY (id);


--
-- Name: dash_skill_invocations dash_skill_invocations_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_skill_invocations
    ADD CONSTRAINT dash_skill_invocations_pkey PRIMARY KEY (id);


--
-- Name: dash_skill_marketplace dash_skill_marketplace_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_skill_marketplace
    ADD CONSTRAINT dash_skill_marketplace_pkey PRIMARY KEY (id);


--
-- Name: dash_skills dash_skills_name_project_slug_key; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_skills
    ADD CONSTRAINT dash_skills_name_project_slug_key UNIQUE (name, project_slug);


--
-- Name: dash_skills dash_skills_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_skills
    ADD CONSTRAINT dash_skills_pkey PRIMARY KEY (id);


--
-- Name: dash_slack_channel_routes dash_slack_channel_routes_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_slack_channel_routes
    ADD CONSTRAINT dash_slack_channel_routes_pkey PRIMARY KEY (id);


--
-- Name: dash_slack_channel_routes dash_slack_channel_routes_workspace_id_channel_id_key; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_slack_channel_routes
    ADD CONSTRAINT dash_slack_channel_routes_workspace_id_channel_id_key UNIQUE (workspace_id, channel_id);


--
-- Name: dash_slack_workspaces dash_slack_workspaces_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_slack_workspaces
    ADD CONSTRAINT dash_slack_workspaces_pkey PRIMARY KEY (id);


--
-- Name: dash_slack_workspaces dash_slack_workspaces_team_id_key; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_slack_workspaces
    ADD CONSTRAINT dash_slack_workspaces_team_id_key UNIQUE (team_id);


--
-- Name: dash_slide_critique dash_slide_critique_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_slide_critique
    ADD CONSTRAINT dash_slide_critique_pkey PRIMARY KEY (id);


--
-- Name: dash_slide_templates dash_slide_templates_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_slide_templates
    ADD CONSTRAINT dash_slide_templates_pkey PRIMARY KEY (id);


--
-- Name: dash_sql_validator_events dash_sql_validator_events_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_sql_validator_events
    ADD CONSTRAINT dash_sql_validator_events_pkey PRIMARY KEY (id);


--
-- Name: dash_subagent_runs dash_subagent_runs_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_subagent_runs
    ADD CONSTRAINT dash_subagent_runs_pkey PRIMARY KEY (id);


--
-- Name: dash_subscription_snapshots dash_subscription_snapshots_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_subscription_snapshots
    ADD CONSTRAINT dash_subscription_snapshots_pkey PRIMARY KEY (id);


--
-- Name: dash_subscription_snapshots dash_subscription_snapshots_project_slug_period_start_key; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_subscription_snapshots
    ADD CONSTRAINT dash_subscription_snapshots_project_slug_period_start_key UNIQUE (project_slug, period_start);


--
-- Name: dash_template_bindings dash_template_bindings_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_template_bindings
    ADD CONSTRAINT dash_template_bindings_pkey PRIMARY KEY (project_slug, template_ref);


--
-- Name: dash_template_expectations dash_template_expectations_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_template_expectations
    ADD CONSTRAINT dash_template_expectations_pkey PRIMARY KEY (project_slug);


--
-- Name: dash_tool_utility_scores dash_tool_utility_scores_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_tool_utility_scores
    ADD CONSTRAINT dash_tool_utility_scores_pkey PRIMARY KEY (id);


--
-- Name: dash_touchpoints dash_touchpoints_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_touchpoints
    ADD CONSTRAINT dash_touchpoints_pkey PRIMARY KEY (id);


--
-- Name: dash_vector_audit dash_vector_audit_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_vector_audit
    ADD CONSTRAINT dash_vector_audit_pkey PRIMARY KEY (id);


--
-- Name: dash_vectors dash_vectors_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_vectors
    ADD CONSTRAINT dash_vectors_pkey PRIMARY KEY (id);


--
-- Name: dash_vectors dash_vectors_project_slug_namespace_source_id_key; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_vectors
    ADD CONSTRAINT dash_vectors_project_slug_namespace_source_id_key UNIQUE (project_slug, namespace, source_id);


--
-- Name: dash_venture_competitors dash_venture_competitors_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_venture_competitors
    ADD CONSTRAINT dash_venture_competitors_pkey PRIMARY KEY (id);


--
-- Name: dash_venture_deals dash_venture_deals_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_venture_deals
    ADD CONSTRAINT dash_venture_deals_pkey PRIMARY KEY (id);


--
-- Name: dash_venture_scenarios dash_venture_scenarios_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_venture_scenarios
    ADD CONSTRAINT dash_venture_scenarios_pkey PRIMARY KEY (id);


--
-- Name: dash_venture_verdict_drift dash_venture_verdict_drift_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_venture_verdict_drift
    ADD CONSTRAINT dash_venture_verdict_drift_pkey PRIMARY KEY (id);


--
-- Name: dash_vertical_pack_history dash_vertical_pack_history_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_vertical_pack_history
    ADD CONSTRAINT dash_vertical_pack_history_pkey PRIMARY KEY (id);


--
-- Name: dash_voice_numbers dash_voice_numbers_phone_number_key; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_voice_numbers
    ADD CONSTRAINT dash_voice_numbers_phone_number_key UNIQUE (phone_number);


--
-- Name: dash_voice_numbers dash_voice_numbers_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_voice_numbers
    ADD CONSTRAINT dash_voice_numbers_pkey PRIMARY KEY (id);


--
-- Name: dash_workflow_defs dash_workflow_defs_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_workflow_defs
    ADD CONSTRAINT dash_workflow_defs_pkey PRIMARY KEY (id);


--
-- Name: dash_workflow_run_history dash_workflow_run_history_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_workflow_run_history
    ADD CONSTRAINT dash_workflow_run_history_pkey PRIMARY KEY (run_id);


--
-- Name: dash_workflow_run_steps dash_workflow_run_steps_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_workflow_run_steps
    ADD CONSTRAINT dash_workflow_run_steps_pkey PRIMARY KEY (id);


--
-- Name: dash_workflow_runs dash_workflow_runs_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_workflow_runs
    ADD CONSTRAINT dash_workflow_runs_pkey PRIMARY KEY (id);


--
-- Name: dash_workflow_schedules dash_workflow_schedules_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_workflow_schedules
    ADD CONSTRAINT dash_workflow_schedules_pkey PRIMARY KEY (id);


--
-- Name: training_signals training_signals_pkey; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.training_signals
    ADD CONSTRAINT training_signals_pkey PRIMARY KEY (id);


--
-- Name: dash_entities uq_dash_entities_proj_kind_norm; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_entities
    ADD CONSTRAINT uq_dash_entities_proj_kind_norm UNIQUE (project_slug, kind, name_normalized);


--
-- Name: dash_entity_links uq_dash_entity_links; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_entity_links
    ADD CONSTRAINT uq_dash_entity_links UNIQUE (project_slug, src_entity_id, rel, dst_entity_id);


--
-- Name: dash_dream_findings uq_dream_findings_project_hash; Type: CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_dream_findings
    ADD CONSTRAINT uq_dream_findings_project_hash UNIQUE (project_slug, finding_hash);


--
-- Name: dash_action_registry dash_action_registry_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_action_registry
    ADD CONSTRAINT dash_action_registry_pkey PRIMARY KEY (id);


--
-- Name: dash_admin_settings dash_admin_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_admin_settings
    ADD CONSTRAINT dash_admin_settings_pkey PRIMARY KEY (id);


--
-- Name: dash_agent_embeds dash_agent_embeds_embed_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_agent_embeds
    ADD CONSTRAINT dash_agent_embeds_embed_id_key UNIQUE (embed_id);


--
-- Name: dash_agent_embeds dash_agent_embeds_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_agent_embeds
    ADD CONSTRAINT dash_agent_embeds_pkey PRIMARY KEY (id);


--
-- Name: dash_agent_embeds dash_agent_embeds_public_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_agent_embeds
    ADD CONSTRAINT dash_agent_embeds_public_key_key UNIQUE (public_key);


--
-- Name: dash_agent_registry dash_agent_registry_agent_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_agent_registry
    ADD CONSTRAINT dash_agent_registry_agent_name_key UNIQUE (agent_name);


--
-- Name: dash_agent_registry dash_agent_registry_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_agent_registry
    ADD CONSTRAINT dash_agent_registry_pkey PRIMARY KEY (id);


--
-- Name: dash_annotations dash_annotations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_annotations
    ADD CONSTRAINT dash_annotations_pkey PRIMARY KEY (id);


--
-- Name: dash_annotations dash_annotations_project_slug_table_name_column_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_annotations
    ADD CONSTRAINT dash_annotations_project_slug_table_name_column_name_key UNIQUE (project_slug, table_name, column_name);


--
-- Name: dash_apigw_config dash_apigw_config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_apigw_config
    ADD CONSTRAINT dash_apigw_config_pkey PRIMARY KEY (id);


--
-- Name: dash_apigw_messages dash_apigw_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_apigw_messages
    ADD CONSTRAINT dash_apigw_messages_pkey PRIMARY KEY (id);


--
-- Name: dash_apigw_usage dash_apigw_usage_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_apigw_usage
    ADD CONSTRAINT dash_apigw_usage_pkey PRIMARY KEY (id);


--
-- Name: dash_audit_log dash_audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_audit_log
    ADD CONSTRAINT dash_audit_log_pkey PRIMARY KEY (id);


--
-- Name: dash_backup_runs dash_backup_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_backup_runs
    ADD CONSTRAINT dash_backup_runs_pkey PRIMARY KEY (id);


--
-- Name: dash_brain_access_log dash_brain_access_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_brain_access_log
    ADD CONSTRAINT dash_brain_access_log_pkey PRIMARY KEY (id);


--
-- Name: dash_brain_versions dash_brain_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_brain_versions
    ADD CONSTRAINT dash_brain_versions_pkey PRIMARY KEY (id);


--
-- Name: dash_business_rules_db dash_business_rules_db_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_business_rules_db
    ADD CONSTRAINT dash_business_rules_db_pkey PRIMARY KEY (id);


--
-- Name: dash_business_rules_db dash_business_rules_db_project_slug_table_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_business_rules_db
    ADD CONSTRAINT dash_business_rules_db_project_slug_table_name_key UNIQUE (project_slug, table_name);


--
-- Name: dash_chat_sessions dash_chat_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_chat_sessions
    ADD CONSTRAINT dash_chat_sessions_pkey PRIMARY KEY (id);


--
-- Name: dash_chat_sessions dash_chat_sessions_session_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_chat_sessions
    ADD CONSTRAINT dash_chat_sessions_session_id_key UNIQUE (session_id);


--
-- Name: dash_column_meta dash_column_meta_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_column_meta
    ADD CONSTRAINT dash_column_meta_pkey PRIMARY KEY (project_slug, table_name, column_name);


--
-- Name: dash_company_brain dash_company_brain_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_company_brain
    ADD CONSTRAINT dash_company_brain_pkey PRIMARY KEY (id);


--
-- Name: dash_dashboard_audit dash_dashboard_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_dashboard_audit
    ADD CONSTRAINT dash_dashboard_audit_pkey PRIMARY KEY (id);


--
-- Name: dash_dashboard_skill_runs dash_dashboard_skill_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_dashboard_skill_runs
    ADD CONSTRAINT dash_dashboard_skill_runs_pkey PRIMARY KEY (id);


--
-- Name: dash_dashboards dash_dashboards_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_dashboards
    ADD CONSTRAINT dash_dashboards_pkey PRIMARY KEY (id);


--
-- Name: dash_dashboards_v2 dash_dashboards_v2_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_dashboards_v2
    ADD CONSTRAINT dash_dashboards_v2_pkey PRIMARY KEY (id);


--
-- Name: dash_decisions dash_decisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_decisions
    ADD CONSTRAINT dash_decisions_pkey PRIMARY KEY (id);


--
-- Name: dash_deck_schedules dash_deck_schedules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_deck_schedules
    ADD CONSTRAINT dash_deck_schedules_pkey PRIMARY KEY (id);


--
-- Name: dash_documents dash_documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_documents
    ADD CONSTRAINT dash_documents_pkey PRIMARY KEY (id);


--
-- Name: dash_drift_alerts dash_drift_alerts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_drift_alerts
    ADD CONSTRAINT dash_drift_alerts_pkey PRIMARY KEY (id);


--
-- Name: dash_embed_calls dash_embed_calls_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_embed_calls
    ADD CONSTRAINT dash_embed_calls_pkey PRIMARY KEY (id);


--
-- Name: dash_embed_rls_audit dash_embed_rls_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_embed_rls_audit
    ADD CONSTRAINT dash_embed_rls_audit_pkey PRIMARY KEY (id);


--
-- Name: dash_embed_rls_blueprints dash_embed_rls_blueprints_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_embed_rls_blueprints
    ADD CONSTRAINT dash_embed_rls_blueprints_pkey PRIMARY KEY (id);


--
-- Name: dash_embed_rls_blueprints dash_embed_rls_blueprints_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_embed_rls_blueprints
    ADD CONSTRAINT dash_embed_rls_blueprints_slug_key UNIQUE (slug);


--
-- Name: dash_embed_sessions dash_embed_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_embed_sessions
    ADD CONSTRAINT dash_embed_sessions_pkey PRIMARY KEY (id);


--
-- Name: dash_embed_sessions dash_embed_sessions_session_token_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_embed_sessions
    ADD CONSTRAINT dash_embed_sessions_session_token_key UNIQUE (session_token);


--
-- Name: dash_eval_history dash_eval_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_eval_history
    ADD CONSTRAINT dash_eval_history_pkey PRIMARY KEY (id);


--
-- Name: dash_eval_runs dash_eval_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_eval_runs
    ADD CONSTRAINT dash_eval_runs_pkey PRIMARY KEY (id);


--
-- Name: dash_evals dash_evals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_evals
    ADD CONSTRAINT dash_evals_pkey PRIMARY KEY (id);


--
-- Name: dash_evolution_runs dash_evolution_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_evolution_runs
    ADD CONSTRAINT dash_evolution_runs_pkey PRIMARY KEY (id);


--
-- Name: dash_evolved_instructions dash_evolved_instructions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_evolved_instructions
    ADD CONSTRAINT dash_evolved_instructions_pkey PRIMARY KEY (id);


--
-- Name: dash_external_facts dash_external_facts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_external_facts
    ADD CONSTRAINT dash_external_facts_pkey PRIMARY KEY (id);


--
-- Name: dash_external_facts dash_external_facts_query_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_external_facts
    ADD CONSTRAINT dash_external_facts_query_hash_key UNIQUE (query_hash);


--
-- Name: dash_extraction_plans dash_extraction_plans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_extraction_plans
    ADD CONSTRAINT dash_extraction_plans_pkey PRIMARY KEY (id);


--
-- Name: dash_federation_circuit dash_federation_circuit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_federation_circuit
    ADD CONSTRAINT dash_federation_circuit_pkey PRIMARY KEY (project_slug);


--
-- Name: dash_feedback dash_feedback_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_feedback
    ADD CONSTRAINT dash_feedback_pkey PRIMARY KEY (id);


--
-- Name: dash_guardrail_audit dash_guardrail_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_guardrail_audit
    ADD CONSTRAINT dash_guardrail_audit_pkey PRIMARY KEY (id);


--
-- Name: dash_hitl_requests dash_hitl_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_hitl_requests
    ADD CONSTRAINT dash_hitl_requests_pkey PRIMARY KEY (id);


--
-- Name: dash_hitl_requests dash_hitl_requests_request_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_hitl_requests
    ADD CONSTRAINT dash_hitl_requests_request_id_key UNIQUE (request_id);


--
-- Name: dash_ingest_batches dash_ingest_batches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_ingest_batches
    ADD CONSTRAINT dash_ingest_batches_pkey PRIMARY KEY (batch_id);


--
-- Name: dash_ingest_contracts dash_ingest_contracts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_ingest_contracts
    ADD CONSTRAINT dash_ingest_contracts_pkey PRIMARY KEY (id);


--
-- Name: dash_ingest_files dash_ingest_files_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_ingest_files
    ADD CONSTRAINT dash_ingest_files_pkey PRIMARY KEY (id);


--
-- Name: dash_journal dash_journal_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_journal
    ADD CONSTRAINT dash_journal_pkey PRIMARY KEY (id);


--
-- Name: dash_journal dash_journal_project_slug_journal_date_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_journal
    ADD CONSTRAINT dash_journal_project_slug_journal_date_key UNIQUE (project_slug, journal_date);


--
-- Name: dash_knowledge_triples dash_knowledge_triples_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_knowledge_triples
    ADD CONSTRAINT dash_knowledge_triples_pkey PRIMARY KEY (id);


--
-- Name: dash_llm_costs dash_llm_costs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_llm_costs
    ADD CONSTRAINT dash_llm_costs_pkey PRIMARY KEY (id);


--
-- Name: dash_memories dash_memories_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_memories
    ADD CONSTRAINT dash_memories_pkey PRIMARY KEY (id);


--
-- Name: dash_meta_learnings dash_meta_learnings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_meta_learnings
    ADD CONSTRAINT dash_meta_learnings_pkey PRIMARY KEY (id);


--
-- Name: dash_metric_definitions dash_metric_definitions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_metric_definitions
    ADD CONSTRAINT dash_metric_definitions_pkey PRIMARY KEY (id);


--
-- Name: dash_metric_definitions dash_metric_definitions_project_slug_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_metric_definitions
    ADD CONSTRAINT dash_metric_definitions_project_slug_name_key UNIQUE (project_slug, name);


--
-- Name: dash_metric_versions dash_metric_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_metric_versions
    ADD CONSTRAINT dash_metric_versions_pkey PRIMARY KEY (id);


--
-- Name: dash_migrations dash_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_migrations
    ADD CONSTRAINT dash_migrations_pkey PRIMARY KEY (filename);


--
-- Name: dash_notifications dash_notifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_notifications
    ADD CONSTRAINT dash_notifications_pkey PRIMARY KEY (id);


--
-- Name: dash_oauth_flow dash_oauth_flow_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_oauth_flow
    ADD CONSTRAINT dash_oauth_flow_pkey PRIMARY KEY (state);


--
-- Name: dash_ontology_api_calls dash_ontology_api_calls_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_ontology_api_calls
    ADD CONSTRAINT dash_ontology_api_calls_pkey PRIMARY KEY (id);


--
-- Name: dash_ontology_api_keys dash_ontology_api_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_ontology_api_keys
    ADD CONSTRAINT dash_ontology_api_keys_pkey PRIMARY KEY (id);


--
-- Name: dash_ontology_api_keys dash_ontology_api_keys_public_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_ontology_api_keys
    ADD CONSTRAINT dash_ontology_api_keys_public_key_key UNIQUE (public_key);


--
-- Name: dash_personas dash_personas_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_personas
    ADD CONSTRAINT dash_personas_pkey PRIMARY KEY (id);


--
-- Name: dash_personas dash_personas_project_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_personas
    ADD CONSTRAINT dash_personas_project_slug_key UNIQUE (project_slug);


--
-- Name: dash_presentations dash_presentations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_presentations
    ADD CONSTRAINT dash_presentations_pkey PRIMARY KEY (id);


--
-- Name: dash_proactive_insights dash_proactive_insights_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_proactive_insights
    ADD CONSTRAINT dash_proactive_insights_pkey PRIMARY KEY (id);


--
-- Name: dash_project_shares dash_project_shares_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_project_shares
    ADD CONSTRAINT dash_project_shares_pkey PRIMARY KEY (id);


--
-- Name: dash_project_shares dash_project_shares_project_id_shared_with_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_project_shares
    ADD CONSTRAINT dash_project_shares_project_id_shared_with_user_id_key UNIQUE (project_id, shared_with_user_id);


--
-- Name: dash_projects dash_projects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_projects
    ADD CONSTRAINT dash_projects_pkey PRIMARY KEY (id);


--
-- Name: dash_projects dash_projects_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_projects
    ADD CONSTRAINT dash_projects_slug_key UNIQUE (slug);


--
-- Name: dash_projects dash_projects_slug_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_projects
    ADD CONSTRAINT dash_projects_slug_unique UNIQUE (slug);


--
-- Name: dash_quality_scores dash_quality_scores_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_quality_scores
    ADD CONSTRAINT dash_quality_scores_pkey PRIMARY KEY (id);


--
-- Name: dash_query_patterns dash_query_patterns_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_query_patterns
    ADD CONSTRAINT dash_query_patterns_pkey PRIMARY KEY (id);


--
-- Name: dash_query_plans dash_query_plans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_query_plans
    ADD CONSTRAINT dash_query_plans_pkey PRIMARY KEY (id);


--
-- Name: dash_relationships dash_relationships_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_relationships
    ADD CONSTRAINT dash_relationships_pkey PRIMARY KEY (id);


--
-- Name: dash_relationships dash_relationships_project_slug_from_table_from_column_to_t_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_relationships
    ADD CONSTRAINT dash_relationships_project_slug_from_table_from_column_to_t_key UNIQUE (project_slug, from_table, from_column, to_table, to_column);


--
-- Name: dash_resource_registry dash_resource_registry_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_resource_registry
    ADD CONSTRAINT dash_resource_registry_pkey PRIMARY KEY (id);


--
-- Name: dash_resource_registry dash_resource_registry_project_slug_resource_type_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_resource_registry
    ADD CONSTRAINT dash_resource_registry_project_slug_resource_type_key UNIQUE (project_slug, resource_type);


--
-- Name: dash_rules_db dash_rules_db_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_rules_db
    ADD CONSTRAINT dash_rules_db_pkey PRIMARY KEY (id);


--
-- Name: dash_rules_db dash_rules_db_project_slug_rule_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_rules_db
    ADD CONSTRAINT dash_rules_db_project_slug_rule_id_key UNIQUE (project_slug, rule_id);


--
-- Name: dash_schedules dash_schedules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_schedules
    ADD CONSTRAINT dash_schedules_pkey PRIMARY KEY (id);


--
-- Name: dash_security_events dash_security_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_security_events
    ADD CONSTRAINT dash_security_events_pkey PRIMARY KEY (id);


--
-- Name: dash_self_learning_runs dash_self_learning_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_self_learning_runs
    ADD CONSTRAINT dash_self_learning_runs_pkey PRIMARY KEY (id);


--
-- Name: dash_shared_conversations dash_shared_conversations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_shared_conversations
    ADD CONSTRAINT dash_shared_conversations_pkey PRIMARY KEY (token);


--
-- Name: dash_skill_overrides dash_skill_overrides_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_skill_overrides
    ADD CONSTRAINT dash_skill_overrides_pkey PRIMARY KEY (id);


--
-- Name: dash_skill_overrides dash_skill_overrides_project_slug_skill_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_skill_overrides
    ADD CONSTRAINT dash_skill_overrides_project_slug_skill_id_key UNIQUE (project_slug, skill_id);


--
-- Name: dash_sse_audit dash_sse_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_sse_audit
    ADD CONSTRAINT dash_sse_audit_pkey PRIMARY KEY (id);


--
-- Name: dash_suggested_rules dash_suggested_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_suggested_rules
    ADD CONSTRAINT dash_suggested_rules_pkey PRIMARY KEY (id);


--
-- Name: dash_system_status dash_system_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_system_status
    ADD CONSTRAINT dash_system_status_pkey PRIMARY KEY (id);


--
-- Name: dash_table_metadata dash_table_metadata_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_table_metadata
    ADD CONSTRAINT dash_table_metadata_pkey PRIMARY KEY (id);


--
-- Name: dash_table_metadata dash_table_metadata_project_slug_table_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_table_metadata
    ADD CONSTRAINT dash_table_metadata_project_slug_table_name_key UNIQUE (project_slug, table_name);


--
-- Name: dash_table_usage_stats dash_table_usage_stats_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_table_usage_stats
    ADD CONSTRAINT dash_table_usage_stats_pkey PRIMARY KEY (table_fqn);


--
-- Name: dash_tokens dash_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_tokens
    ADD CONSTRAINT dash_tokens_pkey PRIMARY KEY (token);


--
-- Name: dash_tool_patches dash_tool_patches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_tool_patches
    ADD CONSTRAINT dash_tool_patches_pkey PRIMARY KEY (id);


--
-- Name: dash_tool_scores dash_tool_scores_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_tool_scores
    ADD CONSTRAINT dash_tool_scores_pkey PRIMARY KEY (id);


--
-- Name: dash_tool_scores dash_tool_scores_tool_name_project_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_tool_scores
    ADD CONSTRAINT dash_tool_scores_tool_name_project_slug_key UNIQUE (tool_name, project_slug);


--
-- Name: dash_tool_utility_scores dash_tool_utility_scores_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_tool_utility_scores
    ADD CONSTRAINT dash_tool_utility_scores_pkey PRIMARY KEY (id);


--
-- Name: dash_traces dash_traces_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_traces
    ADD CONSTRAINT dash_traces_pkey PRIMARY KEY (id);


--
-- Name: dash_training_jobs dash_training_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_training_jobs
    ADD CONSTRAINT dash_training_jobs_pkey PRIMARY KEY (id);


--
-- Name: dash_training_qa dash_training_qa_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_training_qa
    ADD CONSTRAINT dash_training_qa_pkey PRIMARY KEY (id);


--
-- Name: dash_training_runs dash_training_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_training_runs
    ADD CONSTRAINT dash_training_runs_pkey PRIMARY KEY (id);


--
-- Name: dash_training_steps dash_training_steps_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_training_steps
    ADD CONSTRAINT dash_training_steps_pkey PRIMARY KEY (id);


--
-- Name: dash_upload_cache dash_upload_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_upload_cache
    ADD CONSTRAINT dash_upload_cache_pkey PRIMARY KEY (file_hash);


--
-- Name: dash_usage_budget dash_usage_budget_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_usage_budget
    ADD CONSTRAINT dash_usage_budget_pkey PRIMARY KEY (id);


--
-- Name: dash_user_preferences dash_user_preferences_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_user_preferences
    ADD CONSTRAINT dash_user_preferences_pkey PRIMARY KEY (id);


--
-- Name: dash_user_preferences dash_user_preferences_user_id_project_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_user_preferences
    ADD CONSTRAINT dash_user_preferences_user_id_project_slug_key UNIQUE (user_id, project_slug);


--
-- Name: dash_user_roles dash_user_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_user_roles
    ADD CONSTRAINT dash_user_roles_pkey PRIMARY KEY (id);


--
-- Name: dash_user_roles dash_user_roles_user_id_project_slug_role_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_user_roles
    ADD CONSTRAINT dash_user_roles_user_id_project_slug_role_name_key UNIQUE (user_id, project_slug, role_name);


--
-- Name: dash_user_scopes dash_user_scopes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_user_scopes
    ADD CONSTRAINT dash_user_scopes_pkey PRIMARY KEY (id);


--
-- Name: dash_user_scopes dash_user_scopes_user_id_project_slug_scope_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_user_scopes
    ADD CONSTRAINT dash_user_scopes_user_id_project_slug_scope_id_key UNIQUE (user_id, project_slug, scope_id);


--
-- Name: dash_users dash_users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_users
    ADD CONSTRAINT dash_users_pkey PRIMARY KEY (id);


--
-- Name: dash_users dash_users_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_users
    ADD CONSTRAINT dash_users_username_key UNIQUE (username);


--
-- Name: dash_verified_scores dash_verified_scores_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_verified_scores
    ADD CONSTRAINT dash_verified_scores_pkey PRIMARY KEY (id);


--
-- Name: dash_visibility_policy_drafts dash_visibility_policy_drafts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_visibility_policy_drafts
    ADD CONSTRAINT dash_visibility_policy_drafts_pkey PRIMARY KEY (id);


--
-- Name: dash_visibility_policy_history dash_visibility_policy_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_visibility_policy_history
    ADD CONSTRAINT dash_visibility_policy_history_pkey PRIMARY KEY (id);


--
-- Name: dash_visibility_policy dash_visibility_policy_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_visibility_policy
    ADD CONSTRAINT dash_visibility_policy_pkey PRIMARY KEY (project_slug);


--
-- Name: dash_visibility_read_log dash_visibility_read_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_visibility_read_log
    ADD CONSTRAINT dash_visibility_read_log_pkey PRIMARY KEY (id);


--
-- Name: dash_visibility_roles dash_visibility_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_visibility_roles
    ADD CONSTRAINT dash_visibility_roles_pkey PRIMARY KEY (id);


--
-- Name: dash_visibility_roles dash_visibility_roles_project_slug_role_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_visibility_roles
    ADD CONSTRAINT dash_visibility_roles_project_slug_role_name_key UNIQUE (project_slug, role_name);


--
-- Name: dash_workflow_runs_v2 dash_workflow_runs_v2_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_workflow_runs_v2
    ADD CONSTRAINT dash_workflow_runs_v2_pkey PRIMARY KEY (id);


--
-- Name: dash_workflow_runs_v2 dash_workflow_runs_v2_run_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_workflow_runs_v2
    ADD CONSTRAINT dash_workflow_runs_v2_run_id_key UNIQUE (run_id);


--
-- Name: dash_workflows_db dash_workflows_db_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_workflows_db
    ADD CONSTRAINT dash_workflows_db_pkey PRIMARY KEY (id);


--
-- Name: dash_admin_settings uq_setting_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_admin_settings
    ADD CONSTRAINT uq_setting_key UNIQUE (key, scope, project_slug);


--
-- Name: shop_flat_brand_trgm; Type: INDEX; Schema: citypharma; Owner: -
--

CREATE INDEX shop_flat_brand_trgm ON citypharma.shop_flat USING gin (brand citypharma.gin_trgm_ops);


--
-- Name: shop_flat_generic_trgm; Type: INDEX; Schema: citypharma; Owner: -
--

CREATE INDEX shop_flat_generic_trgm ON citypharma.shop_flat USING gin (generic citypharma.gin_trgm_ops);


--
-- Name: shop_flat_instock_idx; Type: INDEX; Schema: citypharma; Owner: -
--

CREATE INDEX shop_flat_instock_idx ON citypharma.shop_flat USING btree (site_code) WHERE is_in_stock;


--
-- Name: shop_flat_link_status_idx; Type: INDEX; Schema: citypharma; Owner: -
--

CREATE INDEX shop_flat_link_status_idx ON citypharma.shop_flat USING btree (link_status);


--
-- Name: shop_flat_site_idx; Type: INDEX; Schema: citypharma; Owner: -
--

CREATE INDEX shop_flat_site_idx ON citypharma.shop_flat USING btree (site_code);


--
-- Name: dash_search_log_proj_ts; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX dash_search_log_proj_ts ON dash.dash_search_log USING btree (project_slug, ts DESC);


--
-- Name: dash_vec_attrs; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX dash_vec_attrs ON dash.dash_vectors USING gin (scope_attrs);


--
-- Name: dash_vec_audit_ts; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX dash_vec_audit_ts ON dash.dash_vector_audit USING btree (project_slug, ts DESC);


--
-- Name: dash_vec_fts; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX dash_vec_fts ON dash.dash_vectors USING gin (tsv);


--
-- Name: dash_vec_hnsw; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX dash_vec_hnsw ON dash.dash_vectors USING hnsw (embedding public.vector_cosine_ops);


--
-- Name: dash_vec_scope; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX dash_vec_scope ON dash.dash_vectors USING btree (project_slug, namespace);


--
-- Name: idx_aah_slug; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_aah_slug ON dash.dash_auto_apply_history USING btree (project_slug, created_at DESC);


--
-- Name: idx_agstate_session; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_agstate_session ON dash.dash_agentic_state USING btree (session_id, agent_name);


--
-- Name: idx_anti_patterns_project_confidence_active; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_anti_patterns_project_confidence_active ON dash.dash_anti_patterns USING btree (project_slug, confidence DESC) WHERE (status = 'active'::text);


--
-- Name: idx_apr_action; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_apr_action ON dash.dash_approval_requests USING btree (action_type, status);


--
-- Name: idx_apr_audit_request; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_apr_audit_request ON dash.dash_approval_audit USING btree (request_id, created_at DESC);


--
-- Name: idx_apr_pending; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_apr_pending ON dash.dash_approval_requests USING btree (status, expires_at) WHERE (status = 'pending'::text);


--
-- Name: idx_apr_project; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_apr_project ON dash.dash_approval_requests USING btree (project_slug, created_at DESC);


--
-- Name: idx_attr_slug_conv; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_attr_slug_conv ON dash.dash_attribution_credits USING btree (project_slug, conversion_id);


--
-- Name: idx_attr_tp_model; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_attr_tp_model ON dash.dash_attribution_credits USING btree (touchpoint_id, model);


--
-- Name: idx_autonomous_wf_cron; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_autonomous_wf_cron ON dash.dash_autonomous_workflows USING btree (schedule_cron) WHERE (schedule_cron IS NOT NULL);


--
-- Name: idx_autonomous_wf_owner; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_autonomous_wf_owner ON dash.dash_autonomous_workflows USING btree (owner_user_id);


--
-- Name: idx_autosim_runs_proj; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_autosim_runs_proj ON dash.dash_autosim_runs USING btree (project_slug, created_at DESC);


--
-- Name: idx_autosim_runs_status; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_autosim_runs_status ON dash.dash_autosim_runs USING btree (status, created_at DESC);


--
-- Name: idx_brainbench_corpus_project_recent; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_brainbench_corpus_project_recent ON dash.dash_brainbench_corpus USING btree (project_slug, created_at DESC);


--
-- Name: idx_brainbench_corpus_session; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_brainbench_corpus_session ON dash.dash_brainbench_corpus USING btree (session_id);


--
-- Name: idx_cagent_project; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_cagent_project ON dash.dash_custom_agents USING btree (project_slug, enabled);


--
-- Name: idx_cagent_usage; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_cagent_usage ON dash.dash_custom_agents USING btree (usage_count DESC);


--
-- Name: idx_campaign_events_camp; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_campaign_events_camp ON dash.dash_campaign_events USING btree (campaign_id, occurred_at DESC);


--
-- Name: idx_campaigns_auto_rule; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_campaigns_auto_rule ON dash.dash_campaigns USING btree (((metadata ->> 'rule'::text))) WHERE (type = 'auto'::text);


--
-- Name: idx_campaigns_project; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_campaigns_project ON dash.dash_campaigns USING btree (project_slug, created_at DESC);


--
-- Name: idx_campaigns_status; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_campaigns_status ON dash.dash_campaigns USING btree (status);


--
-- Name: idx_compcache_age; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_compcache_age ON dash.dash_compression_cache USING btree (created_at);


--
-- Name: idx_compcache_url; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_compcache_url ON dash.dash_compression_cache USING btree (url);


--
-- Name: idx_compstats_recent; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_compstats_recent ON dash.dash_compression_stats USING btree (created_at DESC);


--
-- Name: idx_conv_slug_cust_time; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_conv_slug_cust_time ON dash.dash_conversions USING btree (project_slug, customer_id, converted_at DESC);


--
-- Name: idx_corr_rules_lookup; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_corr_rules_lookup ON dash.dash_correction_rules USING btree (project_slug, scope, scope_target, active);


--
-- Name: idx_corrections_recent; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_corrections_recent ON dash.dash_corrections USING btree (project_slug, created_at DESC);


--
-- Name: idx_cs_churn; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_cs_churn ON dash.dash_customer_scores USING btree (project_slug, churn_risk);


--
-- Name: idx_cs_project; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_cs_project ON dash.dash_customer_scores USING btree (project_slug, last_computed DESC);


--
-- Name: idx_cs_segment; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_cs_segment ON dash.dash_customer_scores USING btree (project_slug, rfm_segment);


--
-- Name: idx_dash_connection_audit_conn; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dash_connection_audit_conn ON dash.dash_connection_audit USING btree (connection_id, created_at DESC);


--
-- Name: idx_dash_connections_type; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dash_connections_type ON dash.dash_connections USING btree (connector_type);


--
-- Name: idx_dash_entities_lookup; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dash_entities_lookup ON dash.dash_entities USING btree (project_slug, kind, name_normalized);


--
-- Name: idx_dash_entities_name; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dash_entities_name ON dash.dash_entities USING btree (project_slug, name_normalized);


--
-- Name: idx_dash_entity_links_dst; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dash_entity_links_dst ON dash.dash_entity_links USING btree (project_slug, dst_entity_id, rel);


--
-- Name: idx_dash_entity_links_src; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dash_entity_links_src ON dash.dash_entity_links USING btree (project_slug, src_entity_id, rel);


--
-- Name: idx_dash_links_dst; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dash_links_dst ON dash.dash_links USING btree (dst_type, dst_id);


--
-- Name: idx_dash_links_project; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dash_links_project ON dash.dash_links USING btree (project_slug);


--
-- Name: idx_dash_links_src; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dash_links_src ON dash.dash_links USING btree (src_type, src_id);


--
-- Name: idx_dash_llm_keys_enabled; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dash_llm_keys_enabled ON dash.dash_llm_keys USING btree (provider, enabled) WHERE (enabled = true);


--
-- Name: idx_dash_packs_name; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dash_packs_name ON dash.dash_packs USING btree (name);


--
-- Name: idx_dash_page_evidence_page_ts; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dash_page_evidence_page_ts ON dash.dash_page_evidence USING btree (page_id, ts DESC);


--
-- Name: idx_dash_pages_project_key; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dash_pages_project_key ON dash.dash_pages USING btree (project_slug, page_key);


--
-- Name: idx_dash_skills_runtime_role; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dash_skills_runtime_role ON dash.dash_skills USING btree (runtime_role);


--
-- Name: idx_dash_vectors_created_at; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dash_vectors_created_at ON dash.dash_vectors USING btree (created_at DESC);


--
-- Name: idx_dash_vectors_tenant; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dash_vectors_tenant ON dash.dash_vectors USING btree (project_slug, tenant_namespace);


--
-- Name: idx_dash_wf_sched_due; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dash_wf_sched_due ON dash.dash_workflow_schedules USING btree (status, next_run_at);


--
-- Name: idx_dash_wf_sched_wf; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dash_wf_sched_wf ON dash.dash_workflow_schedules USING btree (workflow_id);


--
-- Name: idx_dd_gaps_run; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dd_gaps_run ON dash.dash_deep_deck_gaps USING btree (run_id);


--
-- Name: idx_dd_queries_gap; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dd_queries_gap ON dash.dash_deep_deck_queries USING btree (gap_id);


--
-- Name: idx_dd_queries_run; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dd_queries_run ON dash.dash_deep_deck_queries USING btree (run_id);


--
-- Name: idx_dd_runs_project; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dd_runs_project ON dash.dash_deep_deck_runs USING btree (project_slug);


--
-- Name: idx_dd_runs_started; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dd_runs_started ON dash.dash_deep_deck_runs USING btree (started_at DESC);


--
-- Name: idx_dd_runs_status; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dd_runs_status ON dash.dash_deep_deck_runs USING btree (status);


--
-- Name: idx_dp_budget_date; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dp_budget_date ON dash.dash_dp_budget USING btree (date DESC);


--
-- Name: idx_dream_findings_project_status_created; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dream_findings_project_status_created ON dash.dash_dream_findings USING btree (project_slug, status, created_at DESC);


--
-- Name: idx_dream_findings_run; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dream_findings_run ON dash.dash_dream_findings USING btree (run_id);


--
-- Name: idx_dream_findings_type; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dream_findings_type ON dash.dash_dream_findings USING btree (finding_type);


--
-- Name: idx_dream_lite_runs_bootstrap; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dream_lite_runs_bootstrap ON dash.dash_dream_lite_runs USING btree (project_slug, is_bootstrap) WHERE (is_bootstrap = true);


--
-- Name: idx_dream_runs_project_started; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dream_runs_project_started ON dash.dash_dream_runs USING btree (project_slug, started_at DESC);


--
-- Name: idx_dream_runs_status_started; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_dream_runs_status_started ON dash.dash_dream_runs USING btree (status, started_at DESC);


--
-- Name: idx_entmem_entity; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_entmem_entity ON dash.dash_entity_memory USING btree (project_slug, entity_type, entity_id) WHERE (archived = false);


--
-- Name: idx_entmem_kind; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_entmem_kind ON dash.dash_entity_memory USING btree (entity_type, fact_kind);


--
-- Name: idx_entmem_recent; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_entmem_recent ON dash.dash_entity_memory USING btree (created_at DESC);


--
-- Name: idx_episode_buffer_session; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_episode_buffer_session ON dash.dash_episode_buffer USING btree (session_id, created_at DESC);


--
-- Name: idx_episode_buffer_unconsumed; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_episode_buffer_unconsumed ON dash.dash_episode_buffer USING btree (project_slug, created_at DESC) WHERE (consumed_at IS NULL);


--
-- Name: idx_eval_baseline_suite; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_eval_baseline_suite ON dash.dash_eval_baselines USING btree (suite_id, set_at DESC);


--
-- Name: idx_eval_cases_grading_mode; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_eval_cases_grading_mode ON dash.dash_eval_cases USING btree (grading_mode);


--
-- Name: idx_eval_results_run; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_eval_results_run ON dash.dash_eval_results USING btree (run_id);


--
-- Name: idx_eval_runs_recent; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_eval_runs_recent ON dash.dash_eval_runs USING btree (started_at DESC);


--
-- Name: idx_eval_runs_suite; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_eval_runs_suite ON dash.dash_eval_runs USING btree (suite_id, started_at DESC);


--
-- Name: idx_genfile_artifact_gallery; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_genfile_artifact_gallery ON dash.dash_generated_files USING btree (project_slug, run_id, created_at DESC) WHERE (deleted_at IS NULL);


--
-- Name: idx_genfile_expires; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_genfile_expires ON dash.dash_generated_files USING btree (expires_at);


--
-- Name: idx_genfile_kind; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_genfile_kind ON dash.dash_generated_files USING btree (project_slug, kind, created_at DESC) WHERE (deleted_at IS NULL);


--
-- Name: idx_genfile_project; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_genfile_project ON dash.dash_generated_files USING btree (project_slug, created_at DESC);


--
-- Name: idx_hitl_project; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_hitl_project ON dash.dash_hitl_pending USING btree (project_slug, created_at DESC);


--
-- Name: idx_hitl_status_expires; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_hitl_status_expires ON dash.dash_hitl_pending USING btree (status, expires_at);


--
-- Name: idx_hook_audit_block; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_hook_audit_block ON dash.dash_hook_audit USING btree (decision, created_at DESC) WHERE (decision = 'block'::text);


--
-- Name: idx_hook_audit_recent; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_hook_audit_recent ON dash.dash_hook_audit USING btree (created_at DESC);


--
-- Name: idx_inv_memos_proj_created; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_inv_memos_proj_created ON dash.dash_investment_memos USING btree (project_slug, created_at DESC);


--
-- Name: idx_inv_memos_proj_sym_created; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_inv_memos_proj_sym_created ON dash.dash_investment_memos USING btree (project_slug, symbol, created_at DESC);


--
-- Name: idx_inv_memos_verdict; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_inv_memos_verdict ON dash.dash_investment_memos USING btree (verdict);


--
-- Name: idx_inv_runs_proj_started; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_inv_runs_proj_started ON dash.dash_investment_runs USING btree (project_slug, started_at DESC);


--
-- Name: idx_inv_runs_status; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_inv_runs_status ON dash.dash_investment_runs USING btree (status);


--
-- Name: idx_kg_active; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_kg_active ON dash.dash_knowledge_triples USING btree (project_slug) WHERE (expired_at IS NULL);


--
-- Name: idx_kg_object; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_kg_object ON dash.dash_knowledge_triples USING btree (project_slug, object);


--
-- Name: idx_kg_subject; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_kg_subject ON dash.dash_knowledge_triples USING btree (project_slug, subject);


--
-- Name: idx_lite_runs_project_time; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_lite_runs_project_time ON dash.dash_dream_lite_runs USING btree (project_slug, triggered_at DESC);


--
-- Name: idx_lite_runs_session; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_lite_runs_session ON dash.dash_dream_lite_runs USING btree (session_id, triggered_at DESC);


--
-- Name: idx_llm_catalog_ctx; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_llm_catalog_ctx ON dash.dash_llm_model_catalog USING btree (context_length DESC NULLS LAST);


--
-- Name: idx_llm_catalog_free; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_llm_catalog_free ON dash.dash_llm_model_catalog USING btree (is_free) WHERE is_free;


--
-- Name: idx_llm_catalog_provider; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_llm_catalog_provider ON dash.dash_llm_model_catalog USING btree (provider);


--
-- Name: idx_llm_catalog_search; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_llm_catalog_search ON dash.dash_llm_model_catalog USING gin (to_tsvector('simple'::regconfig, ((((id || ' '::text) || COALESCE(name, ''::text)) || ' '::text) || provider)));


--
-- Name: idx_mcp_inv_recent; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_mcp_inv_recent ON dash.dash_mcp_invocations USING btree (created_at DESC);


--
-- Name: idx_mcp_servers_project; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_mcp_servers_project ON dash.dash_mcp_servers USING btree (project_slug);


--
-- Name: idx_minions_claim; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_minions_claim ON dash.dash_minions USING btree (claimed_by, lease_until);


--
-- Name: idx_minions_kind_status; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_minions_kind_status ON dash.dash_minions USING btree (kind, status);


--
-- Name: idx_minions_project_status; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_minions_project_status ON dash.dash_minions USING btree (project_slug, status);


--
-- Name: idx_minions_status_sched; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_minions_status_sched ON dash.dash_minions USING btree (status, scheduled_at);


--
-- Name: idx_msg_thread; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_msg_thread ON dash.dash_channel_messages USING btree (thread_id, created_at);


--
-- Name: idx_pii_audit_project_created; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_pii_audit_project_created ON dash.dash_pii_audit USING btree (project_slug, created_at DESC);


--
-- Name: idx_precompute_proj_user; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_precompute_proj_user ON dash.dash_dream_precompute_cache USING btree (project_slug, user_id, last_hit_at DESC NULLS LAST);


--
-- Name: idx_precompute_ttl; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_precompute_ttl ON dash.dash_dream_precompute_cache USING btree (ttl_until);


--
-- Name: idx_project_rls_config_enabled; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_project_rls_config_enabled ON dash.dash_project_rls_config USING btree (enabled) WHERE (enabled = true);


--
-- Name: idx_rcaudit_recent; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_rcaudit_recent ON dash.dash_run_context_audit USING btree (created_at DESC);


--
-- Name: idx_rcaudit_run; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_rcaudit_run ON dash.dash_run_context_audit USING btree (run_id);


--
-- Name: idx_refusal_marks_qhash; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_refusal_marks_qhash ON dash.dash_refusal_marks USING btree (session_id, question_hash);


--
-- Name: idx_refusal_marks_session; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_refusal_marks_session ON dash.dash_refusal_marks USING btree (session_id, refused_at DESC);


--
-- Name: idx_rls_audit_blocked; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_rls_audit_blocked ON dash.dash_rls_audit USING btree (blocked, created_at DESC) WHERE (blocked = true);


--
-- Name: idx_rls_audit_project_ts; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_rls_audit_project_ts ON dash.dash_rls_audit USING btree (project_slug, created_at DESC);


--
-- Name: idx_run_hist_dashboard; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_run_hist_dashboard ON dash.dash_workflow_run_history USING btree (dashboard_id) WHERE (dashboard_id IS NOT NULL);


--
-- Name: idx_run_hist_queued; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_run_hist_queued ON dash.dash_workflow_run_history USING btree (enqueued_at) WHERE (status = 'queued'::text);


--
-- Name: idx_run_hist_slug; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_run_hist_slug ON dash.dash_workflow_run_history USING btree (project_slug);


--
-- Name: idx_run_hist_started; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_run_hist_started ON dash.dash_workflow_run_history USING btree (started_at DESC);


--
-- Name: idx_run_hist_wf; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_run_hist_wf ON dash.dash_workflow_run_history USING btree (workflow_id, started_at DESC);


--
-- Name: idx_sched_dedup; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_sched_dedup ON dash.dash_agent_schedules USING btree (project_slug, created_by_agent, name);


--
-- Name: idx_sched_next; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_sched_next ON dash.dash_agent_schedules USING btree (enabled, next_run_at);


--
-- Name: idx_sched_project; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_sched_project ON dash.dash_agent_schedules USING btree (project_slug);


--
-- Name: idx_sched_runs_recent; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_sched_runs_recent ON dash.dash_agent_schedule_runs USING btree (schedule_id, started_at DESC);


--
-- Name: idx_sd_project; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_sd_project ON dash.dash_skill_drafts USING btree (project_slug, created_at DESC);


--
-- Name: idx_sd_status; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_sd_status ON dash.dash_skill_drafts USING btree (status, created_at DESC);


--
-- Name: idx_secret_leak_recent; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_secret_leak_recent ON dash.dash_secret_leaks USING btree (created_at DESC);


--
-- Name: idx_segment_snapshots_slug_time; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_segment_snapshots_slug_time ON dash.dash_segment_snapshots USING btree (project_slug, captured_at DESC);


--
-- Name: idx_sim_recs_popularity; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_sim_recs_popularity ON dash.dash_sim_recommendations USING btree (vertical, run_count DESC, unique_tenants DESC);


--
-- Name: idx_skill_audit_log_passed; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_skill_audit_log_passed ON dash.dash_skill_audit_log USING btree (project_slug, passed, created_at DESC);


--
-- Name: idx_skill_audit_log_recent; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_skill_audit_log_recent ON dash.dash_skill_audit_log USING btree (project_slug, created_at DESC);


--
-- Name: idx_skill_marketplace_status; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_skill_marketplace_status ON dash.dash_skill_marketplace USING btree (status);


--
-- Name: idx_skill_marketplace_tags; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_skill_marketplace_tags ON dash.dash_skill_marketplace USING gin (tags);


--
-- Name: idx_skill_marketplace_template_installs; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_skill_marketplace_template_installs ON dash.dash_skill_marketplace USING btree (template_name, install_count DESC);


--
-- Name: idx_skills_cat; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_skills_cat ON dash.dash_skills USING btree (category);


--
-- Name: idx_skills_project; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_skills_project ON dash.dash_skills USING btree (project_slug, enabled);


--
-- Name: idx_skinv_recent; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_skinv_recent ON dash.dash_skill_invocations USING btree (created_at DESC);


--
-- Name: idx_skinv_skill; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_skinv_skill ON dash.dash_skill_invocations USING btree (skill_id, created_at DESC);


--
-- Name: idx_slack_route; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_slack_route ON dash.dash_slack_channel_routes USING btree (workspace_id, channel_id);


--
-- Name: idx_slide_critique_pres; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_slide_critique_pres ON dash.dash_slide_critique USING btree (pres_id);


--
-- Name: idx_slide_templates_created_at; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_slide_templates_created_at ON dash.dash_slide_templates USING btree (created_at DESC);


--
-- Name: idx_slide_templates_project; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_slide_templates_project ON dash.dash_slide_templates USING btree (project_slug);


--
-- Name: idx_sub_snapshots_captured; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_sub_snapshots_captured ON dash.dash_subscription_snapshots USING btree (captured_at DESC);


--
-- Name: idx_sub_snapshots_slug_time; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_sub_snapshots_slug_time ON dash.dash_subscription_snapshots USING btree (project_slug, period_start DESC);


--
-- Name: idx_subrun_agent; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_subrun_agent ON dash.dash_subagent_runs USING btree (agent_id, created_at DESC);


--
-- Name: idx_subrun_recent; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_subrun_recent ON dash.dash_subagent_runs USING btree (created_at DESC);


--
-- Name: idx_svev_kind_ts; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_svev_kind_ts ON dash.dash_sql_validator_events USING btree (kind, ts DESC);


--
-- Name: idx_svev_slug_ts; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_svev_slug_ts ON dash.dash_sql_validator_events USING btree (project_slug, ts DESC);


--
-- Name: idx_thread_project; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_thread_project ON dash.dash_channel_threads USING btree (project_slug);


--
-- Name: idx_thread_recent; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_thread_recent ON dash.dash_channel_threads USING btree (last_message_at DESC);


--
-- Name: idx_tool_utility_project_score; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_tool_utility_project_score ON dash.dash_tool_utility_scores USING btree (project_slug, score DESC);


--
-- Name: idx_tool_utility_tool_score; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_tool_utility_tool_score ON dash.dash_tool_utility_scores USING btree (tool_name, score DESC);


--
-- Name: idx_tp_campaign; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_tp_campaign ON dash.dash_touchpoints USING btree (campaign_id);


--
-- Name: idx_tp_slug_cust_time; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_tp_slug_cust_time ON dash.dash_touchpoints USING btree (project_slug, customer_id, event_at DESC);


--
-- Name: idx_training_signals_chat; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_training_signals_chat ON dash.training_signals USING btree (chat_id) WHERE (chat_id IS NOT NULL);


--
-- Name: idx_training_signals_failed; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_training_signals_failed ON dash.training_signals USING btree (project_slug, created_at) WHERE (sql_success = false);


--
-- Name: idx_training_signals_project; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_training_signals_project ON dash.training_signals USING btree (project_slug, created_at DESC);


--
-- Name: idx_venture_comp_deal; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_venture_comp_deal ON dash.dash_venture_competitors USING btree (deal_id);


--
-- Name: idx_venture_deals_project; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_venture_deals_project ON dash.dash_venture_deals USING btree (project_slug);


--
-- Name: idx_venture_deals_status; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_venture_deals_status ON dash.dash_venture_deals USING btree (project_slug, status);


--
-- Name: idx_venture_drift_proj; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_venture_drift_proj ON dash.dash_venture_verdict_drift USING btree (project_slug, detected_at DESC);


--
-- Name: idx_venture_scen_deal; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_venture_scen_deal ON dash.dash_venture_scenarios USING btree (deal_id);


--
-- Name: idx_vpack_history_slug; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_vpack_history_slug ON dash.dash_vertical_pack_history USING btree (project_slug, installed_at DESC);


--
-- Name: idx_wfdef_category; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_wfdef_category ON dash.dash_workflow_defs USING btree (category);


--
-- Name: idx_wfdef_project; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_wfdef_project ON dash.dash_workflow_defs USING btree (project_slug, enabled);


--
-- Name: idx_wfrun_def; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_wfrun_def ON dash.dash_workflow_runs USING btree (def_id, started_at DESC);


--
-- Name: idx_wfrun_recent; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_wfrun_recent ON dash.dash_workflow_runs USING btree (started_at DESC);


--
-- Name: idx_wfrun_status; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_wfrun_status ON dash.dash_workflow_runs USING btree (status);


--
-- Name: idx_wfrunstep_run; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX idx_wfrunstep_run ON dash.dash_workflow_run_steps USING btree (run_id, started_at);


--
-- Name: ix_dash_aw_project; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX ix_dash_aw_project ON dash.dash_autonomous_workflows USING btree (project_slug, status);


--
-- Name: ix_dash_bindings_project; Type: INDEX; Schema: dash; Owner: -
--

CREATE INDEX ix_dash_bindings_project ON dash.dash_template_bindings USING btree (project_slug, status);


--
-- Name: uq_dash_vectors_tenant_hash; Type: INDEX; Schema: dash; Owner: -
--

CREATE UNIQUE INDEX uq_dash_vectors_tenant_hash ON dash.dash_vectors USING btree (project_slug, tenant_namespace, text_hash) WHERE (text_hash IS NOT NULL);


--
-- Name: uq_kg_spo; Type: INDEX; Schema: dash; Owner: -
--

CREATE UNIQUE INDEX uq_kg_spo ON dash.dash_knowledge_triples USING btree (project_slug, subject, predicate, object) WHERE (expired_at IS NULL);


--
-- Name: uq_precompute_question; Type: INDEX; Schema: dash; Owner: -
--

CREATE UNIQUE INDEX uq_precompute_question ON dash.dash_dream_precompute_cache USING btree (project_slug, question_hash);


--
-- Name: uq_skill_marketplace_name_template; Type: INDEX; Schema: dash; Owner: -
--

CREATE UNIQUE INDEX uq_skill_marketplace_name_template ON dash.dash_skill_marketplace USING btree (name, template_name);


--
-- Name: idx_action_registry_project_enabled; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_action_registry_project_enabled ON public.dash_action_registry USING btree (project_id, enabled);


--
-- Name: idx_admin_settings_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_admin_settings_key ON public.dash_admin_settings USING btree (key);


--
-- Name: idx_admin_settings_scope; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_admin_settings_scope ON public.dash_admin_settings USING btree (scope, project_slug);


--
-- Name: idx_agent_registry_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_registry_category ON public.dash_agent_registry USING btree (category);


--
-- Name: idx_apigw_msg_key_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_apigw_msg_key_ts ON public.dash_apigw_messages USING btree (key_id, ts DESC);


--
-- Name: idx_apigw_msg_sess; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_apigw_msg_sess ON public.dash_apigw_messages USING btree (session_id, ts);


--
-- Name: idx_apigw_usage_key_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_apigw_usage_key_ts ON public.dash_apigw_usage USING btree (key_id, ts DESC);


--
-- Name: idx_apigw_usage_type_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_apigw_usage_type_ts ON public.dash_apigw_usage USING btree (request_type, ts DESC);


--
-- Name: idx_audit_log_action_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_log_action_created ON public.dash_audit_log USING btree (action, created_at DESC);


--
-- Name: idx_audit_log_project_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_log_project_created ON public.dash_audit_log USING btree (project_slug, created_at DESC);


--
-- Name: idx_backup_runs_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_backup_runs_ts ON public.dash_backup_runs USING btree (ts DESC);


--
-- Name: idx_brain_source_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_brain_source_id ON public.dash_company_brain USING btree (source_id) WHERE (source_id IS NOT NULL);


--
-- Name: idx_brain_versions_brain_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_brain_versions_brain_id ON public.dash_brain_versions USING btree (brain_id, version DESC);


--
-- Name: idx_brain_versions_changed_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_brain_versions_changed_by ON public.dash_brain_versions USING btree (changed_by);


--
-- Name: idx_brain_versions_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_brain_versions_created_at ON public.dash_brain_versions USING btree (created_at DESC);


--
-- Name: idx_dash_audit_dash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_audit_dash ON public.dash_dashboard_audit USING btree (dashboard_id, created_at DESC);


--
-- Name: idx_dash_column_meta_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_column_meta_project ON public.dash_column_meta USING btree (project_slug);


--
-- Name: idx_dash_column_meta_table; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_column_meta_table ON public.dash_column_meta USING btree (project_slug, table_name);


--
-- Name: idx_dash_decisions_created_desc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_decisions_created_desc ON public.dash_decisions USING btree (created_at DESC);


--
-- Name: idx_dash_decisions_owner_open; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_decisions_owner_open ON public.dash_decisions USING btree (owner_user_id, status) WHERE (status = ANY (ARRAY['pending'::text, 'in_progress'::text]));


--
-- Name: idx_dash_decisions_slug_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_decisions_slug_created ON public.dash_decisions USING btree (project_slug, created_at DESC);


--
-- Name: idx_dash_decisions_slug_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_decisions_slug_status ON public.dash_decisions USING btree (project_slug, status);


--
-- Name: idx_dash_decisions_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_decisions_user ON public.dash_decisions USING btree (user_id, created_at DESC);


--
-- Name: idx_dash_ingest_batches_project_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_ingest_batches_project_created ON public.dash_ingest_batches USING btree (project_slug, created_at DESC);


--
-- Name: idx_dash_ingest_files_batch_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_ingest_files_batch_id ON public.dash_ingest_files USING btree (batch_id);


--
-- Name: idx_dash_ingest_files_project_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_ingest_files_project_hash ON public.dash_ingest_files USING btree (project_slug, content_hash);


--
-- Name: idx_dash_journal_slug_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_journal_slug_date ON public.dash_journal USING btree (project_slug, journal_date DESC);


--
-- Name: idx_dash_llm_costs_slug_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_llm_costs_slug_ts ON public.dash_llm_costs USING btree (project_slug, ts DESC);


--
-- Name: idx_dash_llm_costs_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_llm_costs_ts ON public.dash_llm_costs USING btree (ts DESC);


--
-- Name: idx_dash_metric_defs_model_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_metric_defs_model_name ON public.dash_metric_definitions USING btree (project_slug, model_name) WHERE (model_name IS NOT NULL);


--
-- Name: idx_dash_metric_defs_slug_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_metric_defs_slug_name ON public.dash_metric_definitions USING btree (project_slug, name);


--
-- Name: idx_dash_metric_defs_slug_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_metric_defs_slug_status ON public.dash_metric_definitions USING btree (project_slug, status);


--
-- Name: idx_dash_metric_versions_metric_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_metric_versions_metric_id ON public.dash_metric_versions USING btree (metric_id);


--
-- Name: idx_dash_metric_versions_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_metric_versions_slug ON public.dash_metric_versions USING btree (project_slug);


--
-- Name: idx_dash_projects_digest_enabled; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_projects_digest_enabled ON public.dash_projects USING btree (digest_enabled) WHERE (digest_enabled = true);


--
-- Name: idx_dash_projects_feature_config; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_projects_feature_config ON public.dash_projects USING gin (feature_config);


--
-- Name: idx_dash_skill_runs_dash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_skill_runs_dash ON public.dash_dashboard_skill_runs USING btree (dashboard_id, ran_at DESC);


--
-- Name: idx_dash_skill_runs_skill; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_skill_runs_skill ON public.dash_dashboard_skill_runs USING btree (skill_id, ran_at DESC);


--
-- Name: idx_dash_sse_audit_event_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_sse_audit_event_ts ON public.dash_sse_audit USING btree (event_name, ts DESC);


--
-- Name: idx_dash_sse_audit_missing; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_sse_audit_missing ON public.dash_sse_audit USING btree (session_id) WHERE (event_name = 'TeamRunContent'::text);


--
-- Name: idx_dash_sse_audit_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_sse_audit_session ON public.dash_sse_audit USING btree (session_id, ts DESC);


--
-- Name: idx_dash_table_usage_stats_last_used; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_table_usage_stats_last_used ON public.dash_table_usage_stats USING btree (last_used_at DESC NULLS LAST);


--
-- Name: idx_dash_table_usage_stats_q30d; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_table_usage_stats_q30d ON public.dash_table_usage_stats USING btree (query_count_30d DESC);


--
-- Name: idx_dash_traces_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_traces_kind ON public.dash_traces USING btree (kind);


--
-- Name: idx_dash_traces_kind_started_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_traces_kind_started_at ON public.dash_traces USING btree (kind, started_at DESC);


--
-- Name: idx_dash_traces_project_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_traces_project_slug ON public.dash_traces USING btree (project_slug);


--
-- Name: idx_dash_traces_started_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_traces_started_at ON public.dash_traces USING btree (started_at DESC);


--
-- Name: idx_dash_traces_trace_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_traces_trace_id ON public.dash_traces USING btree (trace_id);


--
-- Name: idx_dash_training_runs_status_step; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_training_runs_status_step ON public.dash_training_runs USING btree (project_slug, status);


--
-- Name: idx_dash_v2_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_v2_session ON public.dash_dashboards_v2 USING btree (project_slug, session_id, version DESC) WHERE (session_id IS NOT NULL);


--
-- Name: idx_dash_v2_signature; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dash_v2_signature ON public.dash_dashboards_v2 USING btree (project_slug, signature_hash) WHERE (signature_hash IS NOT NULL);


--
-- Name: idx_deck_schedules_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_deck_schedules_slug ON public.dash_deck_schedules USING btree (project_slug, enabled);


--
-- Name: idx_drafts_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_drafts_status ON public.dash_visibility_policy_drafts USING btree (project_slug, status, created_at DESC);


--
-- Name: idx_dtj_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dtj_project ON public.dash_training_jobs USING btree (project_slug);


--
-- Name: idx_dtj_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dtj_run ON public.dash_training_jobs USING btree (run_id);


--
-- Name: idx_dtj_status_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dtj_status_created ON public.dash_training_jobs USING btree (status, created_at);


--
-- Name: idx_embed_calls_embed_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_embed_calls_embed_ts ON public.dash_embed_calls USING btree (embed_id, ts DESC);


--
-- Name: idx_embed_calls_user_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_embed_calls_user_ts ON public.dash_embed_calls USING btree (embed_id, external_user, ts DESC);


--
-- Name: idx_embed_rls_audit_embed_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_embed_rls_audit_embed_created ON public.dash_embed_rls_audit USING btree (embed_id, created_at DESC);


--
-- Name: idx_embed_sess_emb; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_embed_sess_emb ON public.dash_embed_sessions USING btree (embed_id, created_at DESC);


--
-- Name: idx_embed_sess_token; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_embed_sess_token ON public.dash_embed_sessions USING btree (session_token);


--
-- Name: idx_embeds_agent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_embeds_agent ON public.dash_agent_embeds USING btree (project_slug, agent_id) WHERE (agent_id IS NOT NULL);


--
-- Name: idx_embeds_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_embeds_project ON public.dash_agent_embeds USING btree (project_slug, enabled);


--
-- Name: idx_external_facts_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_external_facts_hash ON public.dash_external_facts USING btree (query_hash);


--
-- Name: idx_external_facts_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_external_facts_source ON public.dash_external_facts USING btree (source_type, fetched_at DESC);


--
-- Name: idx_extraction_plans_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_extraction_plans_hash ON public.dash_extraction_plans USING btree (file_hash) WHERE (file_hash IS NOT NULL);


--
-- Name: idx_extraction_plans_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_extraction_plans_project ON public.dash_extraction_plans USING btree (project_slug, created_at DESC);


--
-- Name: idx_extraction_plans_table; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_extraction_plans_table ON public.dash_extraction_plans USING btree (project_slug, table_name);


--
-- Name: idx_guardrail_audit_proj_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_guardrail_audit_proj_ts ON public.dash_guardrail_audit USING btree (project_slug, ts DESC);


--
-- Name: idx_hitl_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hitl_created ON public.dash_hitl_requests USING btree (created_at DESC);


--
-- Name: idx_hitl_request_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hitl_request_id ON public.dash_hitl_requests USING btree (request_id);


--
-- Name: idx_hitl_state; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hitl_state ON public.dash_hitl_requests USING btree (project_slug, state);


--
-- Name: idx_ingest_contracts_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ingest_contracts_active ON public.dash_ingest_contracts USING btree (project_slug, dataset) WHERE (active = true);


--
-- Name: idx_ingest_contracts_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ingest_contracts_project ON public.dash_ingest_contracts USING btree (project_slug);


--
-- Name: idx_llm_costs_actor_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_llm_costs_actor_ts ON public.dash_llm_costs USING btree (actor, ts DESC);


--
-- Name: idx_memories_decay; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_memories_decay ON public.dash_memories USING btree (citation_count, last_cited_at) WHERE ((archived = false) OR (archived IS NULL));


--
-- Name: idx_memories_dedup; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_memories_dedup ON public.dash_memories USING btree (project_slug, scope, md5(fact)) WHERE ((archived IS NULL) OR (archived = false));


--
-- Name: idx_mv_table_usage_q30d; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mv_table_usage_q30d ON public.mv_table_usage USING btree (query_count_30d DESC);


--
-- Name: idx_ontology_calls_key_id_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ontology_calls_key_id_time ON public.dash_ontology_api_calls USING btree (key_id, created_at DESC);


--
-- Name: idx_ontology_keys_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ontology_keys_status ON public.dash_ontology_api_keys USING btree (status);


--
-- Name: idx_patterns_dedup; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_patterns_dedup ON public.dash_query_patterns USING btree (project_slug, md5(sql));


--
-- Name: idx_pres_audience; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pres_audience ON public.dash_presentations USING btree (audience);


--
-- Name: idx_pres_engine; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pres_engine ON public.dash_presentations USING btree (render_engine);


--
-- Name: idx_pres_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pres_parent ON public.dash_presentations USING btree (parent_id);


--
-- Name: idx_read_log_target; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_read_log_target ON public.dash_visibility_read_log USING btree (project_slug, target_scope_id, created_at DESC);


--
-- Name: idx_read_log_viewer; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_read_log_viewer ON public.dash_visibility_read_log USING btree (project_slug, viewer_user_id, created_at DESC);


--
-- Name: idx_rls_blueprints_industry; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rls_blueprints_industry ON public.dash_embed_rls_blueprints USING btree (industry);


--
-- Name: idx_sec_events_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sec_events_kind ON public.dash_security_events USING btree (kind, ts DESC);


--
-- Name: idx_sec_events_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sec_events_ts ON public.dash_security_events USING btree (ts DESC);


--
-- Name: idx_self_learning_cycle; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_self_learning_cycle ON public.dash_self_learning_runs USING btree (cycle_num DESC);


--
-- Name: idx_self_learning_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_self_learning_project ON public.dash_self_learning_runs USING btree (project_slug, started_at DESC);


--
-- Name: idx_shared_conversations_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_shared_conversations_expires_at ON public.dash_shared_conversations USING btree (expires_at);


--
-- Name: idx_tool_patches_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tool_patches_active ON public.dash_tool_patches USING btree (tool_name, project_slug, applied) WHERE ((applied = true) AND (reverted = false));


--
-- Name: idx_tool_patches_tool_ver; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tool_patches_tool_ver ON public.dash_tool_patches USING btree (tool_name, project_slug, version DESC);


--
-- Name: idx_tool_scores_proj; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tool_scores_proj ON public.dash_tool_scores USING btree (project_slug, score);


--
-- Name: idx_tool_utility_proj_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tool_utility_proj_ts ON public.dash_tool_utility_scores USING btree (project_slug, ts DESC);


--
-- Name: idx_tool_utility_success; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tool_utility_success ON public.dash_tool_utility_scores USING btree (tool_name, success);


--
-- Name: idx_tool_utility_tool_proj_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tool_utility_tool_proj_ts ON public.dash_tool_utility_scores USING btree (tool_name, project_slug, ts DESC);


--
-- Name: idx_training_steps_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_training_steps_lookup ON public.dash_training_steps USING btree (project_slug, name, scope, status);


--
-- Name: idx_training_steps_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_training_steps_run ON public.dash_training_steps USING btree (run_id);


--
-- Name: idx_upload_cache_last_used; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_upload_cache_last_used ON public.dash_upload_cache USING btree (last_used_at DESC);


--
-- Name: idx_user_roles_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_roles_lookup ON public.dash_user_roles USING btree (user_id, project_slug);


--
-- Name: idx_user_scopes_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_scopes_user ON public.dash_user_scopes USING btree (user_id, project_slug);


--
-- Name: idx_verified_scores_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_verified_scores_session ON public.dash_verified_scores USING btree (session_id, created_at DESC);


--
-- Name: idx_verified_scores_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_verified_scores_slug ON public.dash_verified_scores USING btree (project_slug, created_at DESC);


--
-- Name: idx_wfrunv2_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wfrunv2_run_id ON public.dash_workflow_runs_v2 USING btree (run_id);


--
-- Name: idx_wfrunv2_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wfrunv2_status ON public.dash_workflow_runs_v2 USING btree (status);


--
-- Name: idx_wfrunv2_workflow; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wfrunv2_workflow ON public.dash_workflow_runs_v2 USING btree (workflow_name, started_at DESC);


--
-- Name: uq_action_registry_project_name; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_action_registry_project_name ON public.dash_action_registry USING btree (project_id, name);


--
-- Name: uq_brain_global_name; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_brain_global_name ON public.dash_company_brain USING btree (name) WHERE (project_slug IS NULL);


--
-- Name: uq_brain_personal_name; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_brain_personal_name ON public.dash_company_brain USING btree (user_id, name) WHERE (user_id IS NOT NULL);


--
-- Name: uq_brain_slug_name; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_brain_slug_name ON public.dash_company_brain USING btree (project_slug, name) WHERE (project_slug IS NOT NULL);


--
-- Name: INDEX uq_brain_slug_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON INDEX public.uq_brain_slug_name IS 'Unique name per project (NULL=global, see uq_brain_global_name).';


--
-- Name: uq_embeds_auto_agent; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_embeds_auto_agent ON public.dash_agent_embeds USING btree (project_slug, agent_id) WHERE ((auto_provisioned = true) AND (agent_id IS NOT NULL));


--
-- Name: uq_embeds_auto_project; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_embeds_auto_project ON public.dash_agent_embeds USING btree (project_slug) WHERE ((auto_provisioned = true) AND (agent_id IS NULL));


--
-- Name: uq_ingest_contracts_pdv; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_ingest_contracts_pdv ON public.dash_ingest_contracts USING btree (project_slug, dataset, version);


--
-- Name: uq_mv_table_usage_fqn; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_mv_table_usage_fqn ON public.mv_table_usage USING btree (table_fqn);


--
-- Name: uq_training_steps_cache; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_training_steps_cache ON public.dash_training_steps USING btree (project_slug, name, scope);


--
-- Name: dash_agent_schedule_runs dash_agent_schedule_runs_schedule_id_fkey; Type: FK CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_agent_schedule_runs
    ADD CONSTRAINT dash_agent_schedule_runs_schedule_id_fkey FOREIGN KEY (schedule_id) REFERENCES dash.dash_agent_schedules(id) ON DELETE CASCADE;


--
-- Name: dash_anti_patterns dash_anti_patterns_source_dream_finding_id_fkey; Type: FK CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_anti_patterns
    ADD CONSTRAINT dash_anti_patterns_source_dream_finding_id_fkey FOREIGN KEY (source_dream_finding_id) REFERENCES dash.dash_dream_findings(id) ON DELETE SET NULL;


--
-- Name: dash_approval_signatures dash_approval_signatures_request_id_fkey; Type: FK CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_approval_signatures
    ADD CONSTRAINT dash_approval_signatures_request_id_fkey FOREIGN KEY (request_id) REFERENCES dash.dash_approval_requests(id) ON DELETE CASCADE;


--
-- Name: dash_campaign_events dash_campaign_events_campaign_id_fkey; Type: FK CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_campaign_events
    ADD CONSTRAINT dash_campaign_events_campaign_id_fkey FOREIGN KEY (campaign_id) REFERENCES dash.dash_campaigns(id) ON DELETE CASCADE;


--
-- Name: dash_channel_messages dash_channel_messages_thread_id_fkey; Type: FK CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_channel_messages
    ADD CONSTRAINT dash_channel_messages_thread_id_fkey FOREIGN KEY (thread_id) REFERENCES dash.dash_channel_threads(id) ON DELETE CASCADE;


--
-- Name: dash_connection_audit dash_connection_audit_connection_id_fkey; Type: FK CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_connection_audit
    ADD CONSTRAINT dash_connection_audit_connection_id_fkey FOREIGN KEY (connection_id) REFERENCES dash.dash_connections(id) ON DELETE CASCADE;


--
-- Name: dash_correction_rules dash_correction_rules_source_correction_id_fkey; Type: FK CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_correction_rules
    ADD CONSTRAINT dash_correction_rules_source_correction_id_fkey FOREIGN KEY (source_correction_id) REFERENCES dash.dash_corrections(id) ON DELETE SET NULL;


--
-- Name: dash_dream_findings dash_dream_findings_run_id_fkey; Type: FK CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_dream_findings
    ADD CONSTRAINT dash_dream_findings_run_id_fkey FOREIGN KEY (run_id) REFERENCES dash.dash_dream_runs(id) ON DELETE CASCADE;


--
-- Name: dash_entity_links dash_entity_links_dst_entity_id_fkey; Type: FK CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_entity_links
    ADD CONSTRAINT dash_entity_links_dst_entity_id_fkey FOREIGN KEY (dst_entity_id) REFERENCES dash.dash_entities(id) ON DELETE CASCADE;


--
-- Name: dash_entity_links dash_entity_links_src_entity_id_fkey; Type: FK CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_entity_links
    ADD CONSTRAINT dash_entity_links_src_entity_id_fkey FOREIGN KEY (src_entity_id) REFERENCES dash.dash_entities(id) ON DELETE CASCADE;


--
-- Name: dash_eval_baselines dash_eval_baselines_suite_id_fkey; Type: FK CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_eval_baselines
    ADD CONSTRAINT dash_eval_baselines_suite_id_fkey FOREIGN KEY (suite_id) REFERENCES dash.dash_eval_suites(id) ON DELETE CASCADE;


--
-- Name: dash_eval_cases dash_eval_cases_suite_id_fkey; Type: FK CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_eval_cases
    ADD CONSTRAINT dash_eval_cases_suite_id_fkey FOREIGN KEY (suite_id) REFERENCES dash.dash_eval_suites(id) ON DELETE CASCADE;


--
-- Name: dash_eval_results dash_eval_results_run_id_fkey; Type: FK CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_eval_results
    ADD CONSTRAINT dash_eval_results_run_id_fkey FOREIGN KEY (run_id) REFERENCES dash.dash_eval_runs(id) ON DELETE CASCADE;


--
-- Name: dash_eval_runs dash_eval_runs_suite_id_fkey; Type: FK CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_eval_runs
    ADD CONSTRAINT dash_eval_runs_suite_id_fkey FOREIGN KEY (suite_id) REFERENCES dash.dash_eval_suites(id) ON DELETE CASCADE;


--
-- Name: dash_mcp_tool_bindings dash_mcp_tool_bindings_server_id_fkey; Type: FK CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_mcp_tool_bindings
    ADD CONSTRAINT dash_mcp_tool_bindings_server_id_fkey FOREIGN KEY (server_id) REFERENCES dash.dash_mcp_servers(id) ON DELETE CASCADE;


--
-- Name: dash_page_evidence dash_page_evidence_page_id_fkey; Type: FK CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_page_evidence
    ADD CONSTRAINT dash_page_evidence_page_id_fkey FOREIGN KEY (page_id) REFERENCES dash.dash_pages(id) ON DELETE CASCADE;


--
-- Name: dash_project_rls_config dash_project_rls_config_project_slug_fkey; Type: FK CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_project_rls_config
    ADD CONSTRAINT dash_project_rls_config_project_slug_fkey FOREIGN KEY (project_slug) REFERENCES public.dash_projects(slug) ON DELETE CASCADE;


--
-- Name: dash_skill_bindings dash_skill_bindings_skill_id_fkey; Type: FK CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_skill_bindings
    ADD CONSTRAINT dash_skill_bindings_skill_id_fkey FOREIGN KEY (skill_id) REFERENCES dash.dash_skills(id) ON DELETE CASCADE;


--
-- Name: dash_slack_channel_routes dash_slack_channel_routes_workspace_id_fkey; Type: FK CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_slack_channel_routes
    ADD CONSTRAINT dash_slack_channel_routes_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES dash.dash_slack_workspaces(id) ON DELETE CASCADE;


--
-- Name: dash_subagent_runs dash_subagent_runs_agent_id_fkey; Type: FK CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_subagent_runs
    ADD CONSTRAINT dash_subagent_runs_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES dash.dash_custom_agents(id) ON DELETE SET NULL;


--
-- Name: dash_venture_competitors dash_venture_competitors_deal_id_fkey; Type: FK CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_venture_competitors
    ADD CONSTRAINT dash_venture_competitors_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES dash.dash_venture_deals(id) ON DELETE CASCADE;


--
-- Name: dash_venture_scenarios dash_venture_scenarios_deal_id_fkey; Type: FK CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_venture_scenarios
    ADD CONSTRAINT dash_venture_scenarios_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES dash.dash_venture_deals(id) ON DELETE CASCADE;


--
-- Name: dash_workflow_run_history dash_workflow_run_history_workflow_id_fkey; Type: FK CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_workflow_run_history
    ADD CONSTRAINT dash_workflow_run_history_workflow_id_fkey FOREIGN KEY (workflow_id) REFERENCES dash.dash_autonomous_workflows(id) ON DELETE CASCADE;


--
-- Name: dash_workflow_run_steps dash_workflow_run_steps_run_id_fkey; Type: FK CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_workflow_run_steps
    ADD CONSTRAINT dash_workflow_run_steps_run_id_fkey FOREIGN KEY (run_id) REFERENCES dash.dash_workflow_runs(id) ON DELETE CASCADE;


--
-- Name: dash_workflow_runs dash_workflow_runs_def_id_fkey; Type: FK CONSTRAINT; Schema: dash; Owner: -
--

ALTER TABLE ONLY dash.dash_workflow_runs
    ADD CONSTRAINT dash_workflow_runs_def_id_fkey FOREIGN KEY (def_id) REFERENCES dash.dash_workflow_defs(id) ON DELETE CASCADE;


--
-- Name: dash_chat_sessions dash_chat_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_chat_sessions
    ADD CONSTRAINT dash_chat_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.dash_users(id) ON DELETE CASCADE;


--
-- Name: dash_project_shares dash_project_shares_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_project_shares
    ADD CONSTRAINT dash_project_shares_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.dash_projects(id) ON DELETE CASCADE;


--
-- Name: dash_project_shares dash_project_shares_shared_with_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_project_shares
    ADD CONSTRAINT dash_project_shares_shared_with_user_id_fkey FOREIGN KEY (shared_with_user_id) REFERENCES public.dash_users(id) ON DELETE CASCADE;


--
-- Name: dash_projects dash_projects_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_projects
    ADD CONSTRAINT dash_projects_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.dash_users(id) ON DELETE CASCADE;


--
-- Name: dash_tokens dash_tokens_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_tokens
    ADD CONSTRAINT dash_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.dash_users(id) ON DELETE CASCADE;


--
-- Name: dash_user_roles dash_user_roles_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_user_roles
    ADD CONSTRAINT dash_user_roles_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.dash_users(id) ON DELETE CASCADE;


--
-- Name: dash_user_scopes dash_user_scopes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dash_user_scopes
    ADD CONSTRAINT dash_user_scopes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.dash_users(id) ON DELETE CASCADE;


--
-- Name: dash_vectors; Type: ROW SECURITY; Schema: dash; Owner: -
--

ALTER TABLE dash.dash_vectors ENABLE ROW LEVEL SECURITY;

--
-- Name: dash_vectors vec_scope; Type: POLICY; Schema: dash; Owner: -
--

CREATE POLICY vec_scope ON dash.dash_vectors USING (((COALESCE(current_setting('app.bypass_rls'::text, true), 'false'::text) = 'true'::text) OR ((project_slug = COALESCE(current_setting('app.project_slug'::text, true), ''::text)) AND ((scope_attrs = '{}'::jsonb) OR (scope_attrs @> COALESCE((NULLIF(current_setting('app.user_attrs'::text, true), ''::text))::jsonb, '{}'::jsonb))))));


--
-- PostgreSQL database dump complete
--

\unrestrict 7mCQm2rlrXfdkFhFKDGnzYlPuATnq005MEjseRsSjZEnluhZhzZ2H7sQ963B6Ke


-- ============================================================
-- POST-BASELINE RECONCILIATION (migrations 179, 180)
-- Keeps fresh-deploy schema == live. Baseline still CREATEs the
-- dormant clusters above; this block drops them + adds latency_ms
-- to v_usage_unified. Idempotent (IF EXISTS / OR REPLACE).
-- ============================================================

SET search_path = public, dash;

-- mig 178: real engine_model on usage tables (view below references it)
ALTER TABLE public.dash_apigw_usage ADD COLUMN IF NOT EXISTS engine_model text;
ALTER TABLE public.dash_embed_calls ADD COLUMN IF NOT EXISTS engine_model text;

-- mig 179: latency_ms on v_usage_unified
CREATE OR REPLACE VIEW public.v_usage_unified AS
  SELECT 'platform'::text AS src,
     dash_llm_costs.ts,
     COALESCE(dash_llm_costs.actor, 'system'::text) AS actor,
     NULL::text AS store_id,
     dash_llm_costs.model,
     COALESCE(dash_llm_costs.tokens_in, 0) AS tokens_in,
     COALESCE(dash_llm_costs.tokens_out, 0) AS tokens_out,
     COALESCE(dash_llm_costs.cost_usd, 0::numeric) AS cost_usd,
         CASE WHEN dash_llm_costs.ok THEN 'ok'::text ELSE 'error'::text END AS status,
     NULL::integer AS latency_ms
    FROM dash_llm_costs
   WHERE COALESCE(dash_llm_costs.task, ''::text) !~~ 'train%'::text
 UNION ALL
  SELECT 'training'::text AS src,
     dash_llm_costs.ts,
     'system'::text AS actor,
     NULL::text AS store_id,
     dash_llm_costs.model,
     COALESCE(dash_llm_costs.tokens_in, 0) AS tokens_in,
     COALESCE(dash_llm_costs.tokens_out, 0) AS tokens_out,
     COALESCE(dash_llm_costs.cost_usd, 0::numeric) AS cost_usd,
         CASE WHEN dash_llm_costs.ok THEN 'ok'::text ELSE 'error'::text END AS status,
     NULL::integer AS latency_ms
    FROM dash_llm_costs
   WHERE COALESCE(dash_llm_costs.task, ''::text) ~~ 'train%'::text
 UNION ALL
  SELECT
         CASE WHEN dash_apigw_usage.request_type = 'embedding'::text THEN 'embedding'::text
              ELSE 'api_key'::text END AS src,
     dash_apigw_usage.ts,
     COALESCE(dash_apigw_usage.service_account, 'unknown'::text) AS actor,
     dash_apigw_usage.store_id,
     COALESCE(NULLIF(dash_apigw_usage.engine_model, ''::text), dash_apigw_usage.model) AS model,
     COALESCE(dash_apigw_usage.prompt_tokens, 0) AS tokens_in,
     COALESCE(dash_apigw_usage.completion_tokens, 0) AS tokens_out,
     COALESCE(dash_apigw_usage.cost_usd, 0::numeric) AS cost_usd,
     COALESCE(dash_apigw_usage.status, 'ok'::text) AS status,
     dash_apigw_usage.latency_ms
    FROM dash_apigw_usage
 UNION ALL
  SELECT 'embed'::text AS src,
     dash_embed_calls.ts,
     COALESCE(dash_embed_calls.external_user, 'anon'::text) AS actor,
     NULL::text AS store_id,
     COALESCE(NULLIF(dash_embed_calls.engine_model, ''::text), 'google/gemini-3-flash-preview'::text) AS model,
     COALESCE(dash_embed_calls.tokens_in, 0) AS tokens_in,
     COALESCE(dash_embed_calls.tokens_out, 0) AS tokens_out,
     COALESCE(dash_embed_calls.cost_usd, 0::numeric) AS cost_usd,
         CASE WHEN dash_embed_calls.success THEN 'ok'::text ELSE 'error'::text END AS status,
     dash_embed_calls.latency_ms
    FROM dash_embed_calls;

-- mig 180: drop 10 dormant clusters
DROP TABLE IF EXISTS dash.dash_venture_competitors CASCADE;
DROP TABLE IF EXISTS dash.dash_venture_deals CASCADE;
DROP TABLE IF EXISTS dash.dash_venture_financials CASCADE;
DROP TABLE IF EXISTS dash.dash_venture_partners CASCADE;
DROP TABLE IF EXISTS dash.dash_venture_scenarios CASCADE;
DROP TABLE IF EXISTS dash.dash_venture_verdict_drift CASCADE;
DROP TABLE IF EXISTS dash.dash_investment_memos CASCADE;
DROP TABLE IF EXISTS dash.dash_investment_runs CASCADE;
DROP TABLE IF EXISTS dash.dash_automl_experiments CASCADE;
DROP TABLE IF EXISTS dash.dash_automl_followups CASCADE;
DROP TABLE IF EXISTS dash.dash_automl_reports CASCADE;
DROP TABLE IF EXISTS dash.dash_automl_staging CASCADE;
DROP TABLE IF EXISTS dash.dash_automl_upload_sets CASCADE;
DROP TABLE IF EXISTS dash.dash_dream_findings CASCADE;
DROP TABLE IF EXISTS dash.dash_dream_insights CASCADE;
DROP TABLE IF EXISTS dash.dash_dream_lite_runs CASCADE;
DROP TABLE IF EXISTS dash.dash_dream_precompute_cache CASCADE;
DROP TABLE IF EXISTS dash.dash_dream_runs CASCADE;
DROP TABLE IF EXISTS dash.dash_autosim_briefs CASCADE;
DROP TABLE IF EXISTS dash.dash_autosim_packs CASCADE;
DROP TABLE IF EXISTS dash.dash_autosim_runs CASCADE;
DROP TABLE IF EXISTS dash.dash_campaigns CASCADE;
DROP TABLE IF EXISTS dash.dash_campaign_events CASCADE;
DROP TABLE IF EXISTS dash.dash_campaign_metrics CASCADE;
DROP TABLE IF EXISTS dash.dash_conversions CASCADE;
DROP TABLE IF EXISTS dash.dash_touchpoints CASCADE;
DROP TABLE IF EXISTS dash.dash_customer_scores CASCADE;
DROP TABLE IF EXISTS dash.dash_attribution_credits CASCADE;
DROP TABLE IF EXISTS dash.dash_segment_snapshots CASCADE;
DROP TABLE IF EXISTS dash.dash_subscription_snapshots CASCADE;
DROP TABLE IF EXISTS dash.dash_slack_workspaces CASCADE;
DROP TABLE IF EXISTS dash.dash_slack_channel_routes CASCADE;
DROP TABLE IF EXISTS dash.dash_voice_numbers CASCADE;
DROP TABLE IF EXISTS dash.dash_email_accounts CASCADE;
DROP TABLE IF EXISTS dash.dash_channel_messages CASCADE;
DROP TABLE IF EXISTS dash.dash_channel_threads CASCADE;
DROP TABLE IF EXISTS dash.dash_slide_critique CASCADE;
DROP TABLE IF EXISTS dash.dash_slide_live_data CASCADE;
DROP TABLE IF EXISTS dash.dash_slide_narration CASCADE;
DROP TABLE IF EXISTS dash.dash_slide_templates CASCADE;
DROP TABLE IF EXISTS dash.dash_deep_deck_gaps CASCADE;
DROP TABLE IF EXISTS dash.dash_deep_deck_queries CASCADE;
DROP TABLE IF EXISTS dash.dash_deep_deck_runs CASCADE;
DROP TABLE IF EXISTS dash.dash_mcp_servers CASCADE;
DROP TABLE IF EXISTS dash.dash_mcp_invocations CASCADE;
DROP TABLE IF EXISTS dash.dash_mcp_tool_bindings CASCADE;
DROP TABLE IF EXISTS dash.dash_connections CASCADE;
DROP TABLE IF EXISTS dash.dash_connection_audit CASCADE;
DROP TABLE IF EXISTS dash.dash_connection_user_tokens CASCADE;
DROP TABLE IF EXISTS dash.dash_approval_requests CASCADE;
DROP TABLE IF EXISTS dash.dash_approval_audit CASCADE;
DROP TABLE IF EXISTS dash.dash_approval_signatures CASCADE;
DROP TABLE IF EXISTS dash.dash_hitl_pending CASCADE;
DROP TABLE IF EXISTS dash.dash_hook_audit CASCADE;
DROP TABLE IF EXISTS dash.dash_brainbench_corpus CASCADE;
DROP TABLE IF EXISTS dash.dash_brainbench_results CASCADE;
DROP TABLE IF EXISTS dash.dash_brainbench_runs CASCADE;
DROP TABLE IF EXISTS dash.dash_eval_baselines CASCADE;
DROP TABLE IF EXISTS dash.dash_eval_results CASCADE;
DROP TABLE IF EXISTS dash.dash_ab_revert_events CASCADE;
DROP TABLE IF EXISTS dash.dash_ab_revert_runs CASCADE;
DROP TABLE IF EXISTS dash.dash_sim_comparison_runs CASCADE;
DROP TABLE IF EXISTS dash.dash_sim_recommendations CASCADE;
DROP TABLE IF EXISTS dash.dash_anti_patterns CASCADE;

-- mig 181: drop skills marketplace feature (6 tables)
DROP TABLE IF EXISTS dash.dash_skill_bindings CASCADE;
DROP TABLE IF EXISTS dash.dash_skill_invocations CASCADE;
DROP TABLE IF EXISTS dash.dash_skill_drafts CASCADE;
DROP TABLE IF EXISTS dash.dash_skill_marketplace CASCADE;
DROP TABLE IF EXISTS dash.dash_skill_audit_log CASCADE;
DROP TABLE IF EXISTS dash.dash_skills CASCADE;
