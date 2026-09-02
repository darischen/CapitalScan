--
-- PostgreSQL database dump
--


-- Dumped from database version 16.14 (Debian 16.14-1.pgdg13+1)
-- Dumped by pg_dump version 16.14 (Debian 16.14-1.pgdg13+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: cell_key(text, text, text, integer, text, text, text, integer, numeric); Type: FUNCTION; Schema: public; Owner: capscan
--

CREATE FUNCTION public.cell_key(p_signal_type text, p_side text, p_dd_bucket text, p_strength integer, p_entry_kind text, p_split text, p_era text, p_horizon integer, p_target numeric) RETURNS text
    LANGUAGE sql IMMUTABLE
    AS $$
          SELECT concat_ws('|',
            p_signal_type, p_side,
            coalesce(p_dd_bucket, 'all'),
            coalesce(p_strength::text, 'all'),
            p_entry_kind, p_split,
            coalesce(p_era, 'pooled'),
            'h' || p_horizon,
            't' || to_char(p_target, 'FM990.999')
          );
        $$;


ALTER FUNCTION public.cell_key(p_signal_type text, p_side text, p_dd_bucket text, p_strength integer, p_entry_kind text, p_split text, p_era text, p_horizon integer, p_target numeric) OWNER TO capscan;

--
-- Name: market_date(); Type: FUNCTION; Schema: public; Owner: capscan
--

CREATE FUNCTION public.market_date() RETURNS date
    LANGUAGE sql STABLE
    AS $$ SELECT (now() AT TIME ZONE 'America/New_York')::date $$;


ALTER FUNCTION public.market_date() OWNER TO capscan;

--
-- Name: market_is_open(); Type: FUNCTION; Schema: public; Owner: capscan
--

CREATE FUNCTION public.market_is_open() RETURNS boolean
    LANGUAGE sql STABLE
    AS $$ SELECT (now() AT TIME ZONE 'America/New_York')::time >= TIME '09:30'
             AND (now() AT TIME ZONE 'America/New_York')::time <
                 CASE WHEN EXISTS (
                        SELECT 1 FROM public.trading_days td
                         WHERE td.d = (now() AT TIME ZONE 'America/New_York')::date
                           AND td.is_early_close)
                      THEN TIME '13:00' ELSE TIME '16:00' END $$;


ALTER FUNCTION public.market_is_open() OWNER TO capscan;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


ALTER TABLE public.alembic_version OWNER TO capscan;

--
-- Name: bar_rejects; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.bar_rejects (
    id bigint NOT NULL,
    ticker text,
    ts timestamp with time zone,
    rule text NOT NULL,
    severity text NOT NULL,
    payload jsonb,
    run_id text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bar_rejects_severity_check CHECK ((severity = ANY (ARRAY['flag'::text, 'reject'::text])))
);


ALTER TABLE public.bar_rejects OWNER TO capscan;

--
-- Name: bar_rejects_id_seq; Type: SEQUENCE; Schema: public; Owner: capscan
--

CREATE SEQUENCE public.bar_rejects_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.bar_rejects_id_seq OWNER TO capscan;

--
-- Name: bar_rejects_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: capscan
--

ALTER SEQUENCE public.bar_rejects_id_seq OWNED BY public.bar_rejects.id;


--
-- Name: bars; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.bars (
    ticker text NOT NULL,
    ts timestamp with time zone NOT NULL,
    "interval" text DEFAULT '1d'::text NOT NULL,
    open numeric(12,4) NOT NULL,
    high numeric(12,4) NOT NULL,
    low numeric(12,4) NOT NULL,
    close numeric(12,4) NOT NULL,
    adj_close numeric(12,4) NOT NULL,
    volume bigint,
    adj_factor numeric(12,6) DEFAULT 1.0 NOT NULL,
    is_terminal boolean DEFAULT false NOT NULL,
    source text NOT NULL,
    ingested_at timestamp with time zone DEFAULT now() NOT NULL,
    run_id text,
    CONSTRAINT bars_check CHECK ((high >= low)),
    CONSTRAINT bars_check1 CHECK (((close >= low) AND (close <= high))),
    CONSTRAINT bars_check2 CHECK (((open >= low) AND (open <= high)))
)
WITH (autovacuum_analyze_scale_factor='0.0', autovacuum_analyze_threshold='50000');


ALTER TABLE public.bars OWNER TO capscan;

--
-- Name: bars_live; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.bars_live (
    ticker text NOT NULL,
    session_date date NOT NULL,
    ts timestamp with time zone NOT NULL,
    open numeric(12,4),
    high numeric(12,4),
    low numeric(12,4),
    close numeric(12,4) NOT NULL,
    volume bigint,
    run_id text
);


ALTER TABLE public.bars_live OWNER TO capscan;

--
-- Name: benchmarks; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.benchmarks (
    id bigint NOT NULL,
    run_id text NOT NULL,
    config_hash text NOT NULL,
    arm text NOT NULL,
    replication integer,
    split_key text,
    era text,
    total_ret numeric,
    annualized_ret numeric,
    sharpe numeric,
    max_drawdown numeric,
    frac_deployed numeric,
    capital_efficiency numeric,
    win_rate numeric,
    n_trades integer,
    terminal_value numeric,
    irr numeric,
    avg_cost_basis numeric,
    cash_drag numeric,
    capital_undeployed numeric,
    n_round_trips integer,
    avg_days_in_cash numeric,
    pre_tax_ret numeric,
    post_tax_ret numeric,
    wash_sale_flagged boolean,
    computed_at timestamp with time zone,
    git_sha text
);


ALTER TABLE public.benchmarks OWNER TO capscan;

--
-- Name: benchmarks_id_seq; Type: SEQUENCE; Schema: public; Owner: capscan
--

CREATE SEQUENCE public.benchmarks_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.benchmarks_id_seq OWNER TO capscan;

--
-- Name: benchmarks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: capscan
--

ALTER SEQUENCE public.benchmarks_id_seq OWNED BY public.benchmarks.id;


--
-- Name: cell_stats; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.cell_stats (
    cell_id text NOT NULL,
    run_id text NOT NULL,
    config_hash text NOT NULL,
    signal_type text,
    dd_bucket text,
    signal_strength integer,
    side text,
    entry_kind text,
    split_key text,
    era text,
    horizon_days integer,
    target_pct numeric,
    n_events integer,
    n_eff integer,
    n_tickers integer,
    mean_cofire numeric,
    p_hit numeric,
    baseline_empirical numeric,
    baseline_parametric numeric,
    edge numeric,
    ci_low numeric,
    ci_high numeric,
    p_value_randomization numeric,
    p_value_parametric numeric,
    q_value numeric,
    mean_ret numeric,
    median_ret numeric,
    ret_p25 numeric,
    ret_p75 numeric,
    mean_mfe numeric,
    mean_mae numeric,
    median_time_to_mfe numeric,
    capture_ratio numeric,
    p_touch_2pct numeric,
    p_touch_3pct numeric,
    p_touch_5pct numeric,
    p_touch_10pct numeric,
    median_day_touch_5pct numeric,
    exit_mix jsonb,
    earnings_frac numeric,
    suppressed boolean,
    suppress_reason text,
    computed_at timestamp with time zone,
    git_sha text,
    arm text DEFAULT 'signal'::text NOT NULL,
    CONSTRAINT cell_stats_arm_check CHECK ((arm = ANY (ARRAY['signal'::text, 'control'::text, 'benchmark'::text])))
);


ALTER TABLE public.cell_stats OWNER TO capscan;

--
-- Name: corporate_actions; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.corporate_actions (
    ticker text NOT NULL,
    ex_date date NOT NULL,
    action_type text NOT NULL,
    ratio numeric,
    amount numeric,
    CONSTRAINT corporate_actions_action_type_check CHECK ((action_type = ANY (ARRAY['split'::text, 'dividend'::text])))
);


ALTER TABLE public.corporate_actions OWNER TO capscan;

--
-- Name: earnings; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.earnings (
    ticker text NOT NULL,
    report_date date NOT NULL,
    session text,
    source text NOT NULL,
    confidence text,
    CONSTRAINT earnings_session_check CHECK ((session = ANY (ARRAY['bmo'::text, 'amc'::text, 'unknown'::text])))
);


ALTER TABLE public.earnings OWNER TO capscan;

--
-- Name: events; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.events (
    id bigint NOT NULL,
    run_id text NOT NULL,
    config_hash text NOT NULL,
    ticker text NOT NULL,
    signal_date date NOT NULL,
    signal_type text NOT NULL,
    signal_types_all text[],
    signal_strength integer,
    side text NOT NULL,
    cluster_id bigint,
    seq_in_cluster integer,
    is_cluster_head boolean,
    days_since_head integer,
    touch_level numeric(12,4),
    bb_pctb numeric(12,6),
    bb_width_pct numeric(12,6),
    k_full numeric(12,6),
    d_full numeric(12,6),
    k_fast numeric(12,6),
    k_cross_up boolean,
    k_cross_down boolean,
    atr_14 numeric(12,6),
    rv_pct_252d numeric(12,6),
    dd_52w numeric(12,6),
    sma200_slope_60 numeric(12,6),
    above_sma200 boolean,
    vol_z_20d numeric(12,6),
    days_to_earnings integer,
    vix_close numeric(12,4),
    spx_ret_1d numeric(12,6),
    dd_bucket text,
    bw_regime text,
    era text,
    cofire_count integer,
    mcap_usd numeric,
    sector text,
    entry_kind text NOT NULL,
    entry_date date,
    entry_price numeric(12,4),
    entry_gapped boolean,
    exit_date date,
    exit_price numeric(12,4),
    exit_reason text,
    holding_days integer,
    ambiguous boolean DEFAULT false NOT NULL,
    gross_ret numeric(12,6),
    net_ret numeric(12,6),
    mfe numeric(12,6),
    mae numeric(12,6),
    time_to_mfe integer,
    capture_ratio numeric(12,6),
    touched_2pct boolean,
    day_touched_2pct integer,
    touched_3pct boolean,
    day_touched_3pct integer,
    touched_5pct boolean,
    day_touched_5pct integer,
    touched_10pct boolean,
    day_touched_10pct integer,
    fwd_ret_1d numeric(12,6),
    fwd_ret_2d numeric(12,6),
    fwd_ret_3d numeric(12,6),
    fwd_ret_5d numeric(12,6),
    fwd_ret_10d numeric(12,6),
    earnings_in_window boolean,
    is_terminal boolean,
    split_key text NOT NULL,
    fwd_window_days integer,
    giveback numeric(12,6),
    peak_ret_1d numeric(12,6),
    peak_ret_2d numeric(12,6),
    peak_ret_3d numeric(12,6),
    peak_ret_5d numeric(12,6),
    peak_ret_10d numeric(12,6),
    in_trade boolean DEFAULT true NOT NULL,
    in_watch boolean,
    watch_reason text,
    bb_mid numeric(18,6),
    close numeric(18,6),
    vix_pct_252d numeric(18,6),
    CONSTRAINT events_side_check CHECK ((side = ANY (ARRAY['long'::text, 'short'::text]))),
    CONSTRAINT events_split_key_check CHECK ((split_key = ANY (ARRAY['train'::text, 'validate'::text, 'holdout'::text])))
);


ALTER TABLE public.events OWNER TO capscan;

--
-- Name: COLUMN events.mcap_usd; Type: COMMENT; Schema: public; Owner: capscan
--

COMMENT ON COLUMN public.events.mcap_usd IS 'Market cap from the universe evaluation in force at signal_date (as_of <= signal_date). Point-in-time, unlike sector.';


--
-- Name: COLUMN events.sector; Type: COMMENT; Schema: public; Owner: capscan
--

COMMENT ON COLUMN public.events.sector IS 'GICS sector from tickers.sector, which has NO history: a company reclassified in 2018 carries its post-2018 sector on its 2010 events. Mild look-ahead, accepted (ADR 135, BACKLOG). Use tickers.sector directly if you need to know it is a snapshot.';


--
-- Name: COLUMN events.bb_mid; Type: COMMENT; Schema: public; Owner: capscan
--

COMMENT ON COLUMN public.events.bb_mid IS 'Bollinger mid band at t-1, same row bb_pctb comes from.';


--
-- Name: COLUMN events.close; Type: COMMENT; Schema: public; Owner: capscan
--

COMMENT ON COLUMN public.events.close IS 'Split-adjusted close at t-1, NOT entry_price. entry_price is priced at t; using it as the denominator of atr_14/close would put the entry into a state-at-signal feature (invariant 3).';


--
-- Name: COLUMN events.vix_pct_252d; Type: COMMENT; Schema: public; Owner: capscan
--

COMMENT ON COLUMN public.events.vix_pct_252d IS 'VIX 252-day percentile at t-1, from market_days.';


--
-- Name: events_id_seq; Type: SEQUENCE; Schema: public; Owner: capscan
--

CREATE SEQUENCE public.events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.events_id_seq OWNER TO capscan;

--
-- Name: events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: capscan
--

ALTER SEQUENCE public.events_id_seq OWNED BY public.events.id;


--
-- Name: indicators; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.indicators (
    ticker text NOT NULL,
    ts timestamp with time zone NOT NULL,
    "interval" text DEFAULT '1d'::text NOT NULL,
    bb_mid numeric(12,6),
    bb_upper numeric(12,6),
    bb_lower numeric(12,6),
    bb_pctb numeric(12,6),
    bb_width numeric(12,6),
    bb_width_pct numeric(12,6),
    k_fast numeric(12,6),
    d_fast numeric(12,6),
    k_full numeric(12,6),
    d_full numeric(12,6),
    k_cross_up boolean,
    k_cross_down boolean,
    sma_200 numeric(12,6),
    sma200_slope_60 numeric(12,6),
    atr_14 numeric(12,6),
    rv_20d numeric(12,6),
    rv_pct_252d numeric(12,6),
    vol_z_20d numeric(12,6),
    dd_52w numeric(12,6),
    days_to_earnings integer,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    run_id text,
    bear_close_above_upper boolean,
    bull_close_below_lower boolean
)
WITH (autovacuum_analyze_scale_factor='0.0', autovacuum_analyze_threshold='50000', autovacuum_vacuum_scale_factor='0.0', autovacuum_vacuum_threshold='50000');


ALTER TABLE public.indicators OWNER TO capscan;

--
-- Name: market_days; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.market_days (
    ts date NOT NULL,
    spx_close numeric(12,4),
    spx_ret_1d numeric(12,6),
    vix_close numeric(12,4),
    vix_pct_252d numeric(12,6)
);


ALTER TABLE public.market_days OWNER TO capscan;

--
-- Name: order_intents; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.order_intents (
    id bigint NOT NULL,
    event_id bigint,
    ticker text,
    side text,
    quantity_basis text,
    limit_level numeric(12,4),
    stop_level numeric(12,4),
    time_in_force text,
    idempotency_key text NOT NULL,
    emitted_at timestamp with time zone DEFAULT now(),
    run_id text,
    git_sha text
);


ALTER TABLE public.order_intents OWNER TO capscan;

--
-- Name: order_intents_id_seq; Type: SEQUENCE; Schema: public; Owner: capscan
--

CREATE SEQUENCE public.order_intents_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.order_intents_id_seq OWNER TO capscan;

--
-- Name: order_intents_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: capscan
--

ALTER SEQUENCE public.order_intents_id_seq OWNED BY public.order_intents.id;


--
-- Name: outcomes; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.outcomes (
    prediction_id bigint NOT NULL,
    realized_ret_5d numeric,
    realized_mfe numeric,
    realized_mae numeric,
    touched_2 boolean,
    touched_3 boolean,
    touched_5 boolean,
    touched_10 boolean,
    pinball_loss numeric,
    brier_3pct numeric,
    resolved_at timestamp with time zone
);


ALTER TABLE public.outcomes OWNER TO capscan;

--
-- Name: path; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.path (
    event_id bigint NOT NULL,
    day_offset integer NOT NULL,
    favorable numeric(12,6) NOT NULL,
    adverse numeric(12,6) NOT NULL,
    terminal numeric(12,6) NOT NULL,
    run_id text,
    computed_at timestamp with time zone
);


ALTER TABLE public.path OWNER TO capscan;

--
-- Name: poller_sessions; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.poller_sessions (
    session_date date NOT NULL,
    started_at timestamp with time zone,
    ended_at timestamp with time zone,
    ticks_completed integer,
    ticks_expected integer,
    coverage_pct numeric(6,3),
    notes text
);


ALTER TABLE public.poller_sessions OWNER TO capscan;

--
-- Name: positions; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.positions (
    id bigint NOT NULL,
    user_id text,
    ticker text NOT NULL,
    side text NOT NULL,
    entry_date date NOT NULL,
    entry_price numeric(12,4) NOT NULL,
    quantity numeric,
    source text DEFAULT 'user_declared'::text NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    exit_date date,
    exit_price numeric(12,4),
    exit_reason text,
    realized_ret numeric(12,6),
    idempotency_key text,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.positions OWNER TO capscan;

--
-- Name: positions_id_seq; Type: SEQUENCE; Schema: public; Owner: capscan
--

CREATE SEQUENCE public.positions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.positions_id_seq OWNER TO capscan;

--
-- Name: positions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: capscan
--

ALTER SEQUENCE public.positions_id_seq OWNED BY public.positions.id;


--
-- Name: predictions; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.predictions (
    id bigint NOT NULL,
    ticker text,
    as_of date,
    model_version text,
    event_id bigint,
    q05 numeric,
    q25 numeric,
    q50 numeric,
    q75 numeric,
    q95 numeric,
    p_touch_2 numeric,
    p_touch_3 numeric,
    p_touch_5 numeric,
    p_touch_10 numeric,
    p_adverse_3 numeric,
    p_adverse_5 numeric,
    cell_id text,
    cell_p_hit numeric,
    cell_n_eff integer,
    features_json jsonb NOT NULL,
    git_sha text,
    created_at timestamp with time zone
);


ALTER TABLE public.predictions OWNER TO capscan;

--
-- Name: predictions_id_seq; Type: SEQUENCE; Schema: public; Owner: capscan
--

CREATE SEQUENCE public.predictions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.predictions_id_seq OWNER TO capscan;

--
-- Name: predictions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: capscan
--

ALTER SEQUENCE public.predictions_id_seq OWNED BY public.predictions.id;


--
-- Name: quotes_live; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.quotes_live (
    ticker text NOT NULL,
    ts timestamp with time zone NOT NULL,
    price numeric(12,4) NOT NULL,
    breached text,
    breach_depth_atr numeric(12,6),
    event_id bigint
);


ALTER TABLE public.quotes_live OWNER TO capscan;

--
-- Name: rho_era; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.rho_era (
    era text NOT NULL,
    run_id text NOT NULL,
    config_hash text NOT NULL,
    rho_empirical numeric NOT NULL,
    rho_factor_implied numeric,
    rho_gap numeric,
    n_pairs integer NOT NULL,
    n_cofire_days integer NOT NULL,
    mean_beta numeric,
    computed_at timestamp with time zone NOT NULL,
    git_sha text NOT NULL
);


ALTER TABLE public.rho_era OWNER TO capscan;

--
-- Name: runs; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.runs (
    run_id text NOT NULL,
    job text NOT NULL,
    git_sha text NOT NULL,
    params jsonb NOT NULL,
    started_at timestamp with time zone NOT NULL,
    finished_at timestamp with time zone,
    status text,
    rows_written bigint,
    notes text,
    CONSTRAINT runs_status_check CHECK ((status = ANY (ARRAY['running'::text, 'ok'::text, 'failed'::text, 'interrupted'::text])))
);


ALTER TABLE public.runs OWNER TO capscan;

--
-- Name: scheduled_runs; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.scheduled_runs (
    job text NOT NULL,
    scheduled_for timestamp with time zone NOT NULL,
    actual_start timestamp with time zone,
    delay_seconds integer,
    status text,
    run_id text
);


ALTER TABLE public.scheduled_runs OWNER TO capscan;

--
-- Name: serving_config; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.serving_config (
    only_row boolean DEFAULT true NOT NULL,
    config_hash text NOT NULL,
    exit_stoch_source text NOT NULL,
    exit_stoch_threshold numeric(12,4) NOT NULL,
    exit_stoch_threshold_short numeric(12,4) NOT NULL,
    exit_on_stoch_80 boolean NOT NULL,
    exit_on_upper_band boolean NOT NULL,
    exit_on_mid_band boolean NOT NULL,
    max_hold_days integer NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT serving_config_only_row_check CHECK (only_row),
    CONSTRAINT serving_config_stoch_source_check CHECK ((exit_stoch_source = ANY (ARRAY['k_full'::text, 'k_fast'::text])))
);


ALTER TABLE public.serving_config OWNER TO capscan;

--
-- Name: shares_outstanding; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.shares_outstanding (
    ticker text NOT NULL,
    filed_on date NOT NULL,
    period_end date,
    shares bigint NOT NULL,
    source text NOT NULL
);


ALTER TABLE public.shares_outstanding OWNER TO capscan;

--
-- Name: shares_scale_errors_pre_adr146; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.shares_scale_errors_pre_adr146 (
    ticker text,
    filed_on date,
    period_end date,
    shares bigint,
    source text
);


ALTER TABLE public.shares_scale_errors_pre_adr146 OWNER TO capscan;

--
-- Name: signal_reports; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.signal_reports (
    id bigint NOT NULL,
    event_id bigint,
    ticker text NOT NULL,
    fired_at timestamp with time zone NOT NULL,
    state_json jsonb NOT NULL,
    cell_id text,
    prediction_id bigint,
    call_overlay_json jsonb,
    channels_sent text[],
    model_version text,
    git_sha text,
    signal_type text
);


ALTER TABLE public.signal_reports OWNER TO capscan;

--
-- Name: COLUMN signal_reports.signal_type; Type: COMMENT; Schema: public; Owner: capscan
--

COMMENT ON COLUMN public.signal_reports.signal_type IS 'The signal type this report fired on, written by the poller. NULL on rows predating 2026-08-29: state_json carries indicator state but no signal type, and the events that would have supplied it were removed by ADR 150 sweeps. NULL means not recorded, never unknown-so-guessed.';


--
-- Name: signal_reports_id_seq; Type: SEQUENCE; Schema: public; Owner: capscan
--

CREATE SEQUENCE public.signal_reports_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.signal_reports_id_seq OWNER TO capscan;

--
-- Name: signal_reports_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: capscan
--

ALTER SEQUENCE public.signal_reports_id_seq OWNED BY public.signal_reports.id;


--
-- Name: tickers; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.tickers (
    ticker text NOT NULL,
    cik text,
    name text,
    sector text,
    industry text,
    exchange text,
    first_bar date,
    last_bar date,
    is_active boolean DEFAULT true NOT NULL,
    delisted_on date
);


ALTER TABLE public.tickers OWNER TO capscan;

--
-- Name: trading_days; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.trading_days (
    d date NOT NULL,
    is_early_close boolean DEFAULT false NOT NULL
);


ALTER TABLE public.trading_days OWNER TO capscan;

--
-- Name: universe; Type: TABLE; Schema: public; Owner: capscan
--

CREATE TABLE public.universe (
    ticker text NOT NULL,
    as_of date NOT NULL,
    in_train boolean NOT NULL,
    in_trade boolean NOT NULL,
    mcap_usd numeric,
    mcap_rank integer,
    adv_20d_usd numeric,
    crit_mcap boolean,
    crit_above_sma200 boolean,
    crit_sma200_slope boolean,
    crit_rel_return boolean,
    crit_rev_growth boolean,
    in_watch boolean,
    watch_reason text,
    config_hash text NOT NULL,
    crit_rel_return_history boolean,
    CONSTRAINT universe_watch_consistent CHECK (((((in_watch IS NOT TRUE) AND (watch_reason IS NULL)) OR ((in_watch IS TRUE) AND (watch_reason = ANY (ARRAY['history'::text, 'pullback'::text, 'near_trade'::text])))) AND (NOT (in_trade AND (in_watch IS TRUE)))))
);


ALTER TABLE public.universe OWNER TO capscan;

--
-- Name: COLUMN universe.config_hash; Type: COMMENT; Schema: public; Owner: capscan
--

COMMENT ON COLUMN public.universe.config_hash IS 'The config whose UniverseParams produced this row. ''unknown'' marks rows that predate this column - nothing recorded what evaluated them. Readers must scope on a real hash; see ADR 060.';


--
-- Name: COLUMN universe.crit_rel_return_history; Type: COMMENT; Schema: public; Owner: capscan
--

COMMENT ON COLUMN public.universe.crit_rel_return_history IS 'ADR 014 history gate alone: TRUE when 757 bars of return exist, NULL when they do not. Never FALSE -- ADR 149 watch route keys on NULL. Decides membership only when required_criteria names it (arm 3). NULL on rows written before c93f4a1e77b2.';


--
-- Name: v_chart; Type: VIEW; Schema: public; Owner: capscan
--

CREATE VIEW public.v_chart AS
 SELECT b.ticker,
    b.ts,
    b.open,
    b.high,
    b.low,
    b.close,
    b.volume,
    i.bb_lower,
    i.bb_mid,
    i.bb_upper,
    i.k_full,
    i.d_full,
    i.k_fast,
    i.sma_200,
    i.bb_width_pct,
    i.dd_52w,
    ev.event_ids,
    ev.signal_types,
    ev.sides,
    ev.signal_strength
   FROM ((public.bars b
     LEFT JOIN public.indicators i ON (((i.ticker = b.ticker) AND (i.ts = b.ts) AND (i."interval" = b."interval"))))
     LEFT JOIN LATERAL ( SELECT array_agg(e.id ORDER BY e.id) AS event_ids,
            array_agg(e.signal_type ORDER BY e.id) AS signal_types,
            array_agg(e.side ORDER BY e.id) AS sides,
            max(e.signal_strength) AS signal_strength
           FROM public.events e
          WHERE ((e.ticker = b.ticker) AND (e.signal_date = (b.ts)::date) AND (e.entry_kind = 'touch'::text) AND (e.is_cluster_head IS NOT FALSE) AND (e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)))) ev ON (true))
  WHERE (b."interval" = '1d'::text);


ALTER VIEW public.v_chart OWNER TO capscan;

--
-- Name: v_events; Type: VIEW; Schema: public; Owner: capscan
--

CREATE VIEW public.v_events AS
 SELECT e.id,
    e.ticker,
    t.sector,
    e.signal_date,
    e.signal_type,
    e.signal_types_all,
    e.signal_strength,
    e.cluster_id,
    e.seq_in_cluster,
    e.is_cluster_head,
    e.bb_pctb,
    e.k_full,
    e.k_fast,
    e.dd_52w,
    e.dd_bucket,
    e.above_sma200,
    e.vix_close,
    e.days_to_earnings,
    e.entry_kind,
    e.entry_date,
    e.entry_price,
    e.entry_gapped,
    e.exit_date,
    e.exit_price,
    e.exit_reason,
    e.holding_days,
    e.ambiguous,
    e.gross_ret,
    e.net_ret,
    e.mfe,
    e.mae,
    e.time_to_mfe,
    e.capture_ratio,
    e.touched_2pct,
    e.touched_3pct,
    e.touched_5pct,
    e.touched_10pct,
    e.day_touched_5pct,
    e.earnings_in_window,
    e.era,
    e.split_key
   FROM (public.events e
     JOIN public.tickers t ON ((t.ticker = e.ticker)))
  WHERE (e.config_hash = current_setting('capitalscan.default_config_hash'::text, true));


ALTER VIEW public.v_events OWNER TO capscan;

--
-- Name: v_forward; Type: VIEW; Schema: public; Owner: capscan
--

CREATE VIEW public.v_forward AS
 SELECT p.id,
    p.ticker,
    p.as_of,
    p.model_version,
    p.event_id,
    p.q05,
    p.q25,
    p.q50,
    p.q75,
    p.q95,
    p.p_touch_2,
    p.p_touch_3,
    p.p_touch_5,
    p.p_touch_10,
    p.p_adverse_3,
    p.p_adverse_5,
    p.cell_id,
    p.cell_p_hit,
    p.cell_n_eff,
    o.realized_ret_5d,
    o.realized_mfe,
    o.realized_mae,
    o.touched_2,
    o.touched_3,
    o.touched_5,
    o.touched_10,
    o.pinball_loss,
    o.brier_3pct,
    o.resolved_at,
    (o.prediction_id IS NOT NULL) AS resolved,
        CASE
            WHEN (o.prediction_id IS NULL) THEN NULL::numeric
            ELSE abs((p.p_touch_3 - ((o.touched_3)::integer)::numeric))
        END AS abs_err_3pct
   FROM (public.predictions p
     LEFT JOIN public.outcomes o ON ((o.prediction_id = p.id)));


ALTER VIEW public.v_forward OWNER TO capscan;

--
-- Name: v_ticker_state; Type: VIEW; Schema: public; Owner: capscan
--

CREATE VIEW public.v_ticker_state AS
 SELECT i.ticker,
    t.name,
    t.sector,
    i.ts AS as_of,
    b.close,
    b.volume,
    i.bb_lower,
    i.bb_mid,
    i.bb_upper,
    i.bb_pctb,
    i.bb_width,
    i.bb_width_pct,
    i.k_full,
    i.d_full,
    i.k_fast,
    i.k_cross_up,
    i.k_cross_down,
    i.sma_200,
    i.sma200_slope_60,
    i.atr_14,
    i.rv_20d,
    i.rv_pct_252d,
    i.vol_z_20d,
    i.dd_52w,
    i.days_to_earnings,
    m.vix_close,
    m.vix_pct_252d,
    m.spx_ret_1d,
    u.in_trade,
    u.mcap_usd,
    u.crit_mcap,
    u.crit_above_sma200,
    u.crit_sma200_slope,
    u.crit_rel_return,
    u.crit_rev_growth,
    (b.close > i.sma_200) AS above_sma200,
    u.in_watch,
    u.watch_reason
   FROM (((((public.tickers t
     CROSS JOIN LATERAL ( SELECT ind.ts
           FROM public.indicators ind
          WHERE ((ind.ticker = t.ticker) AND (ind."interval" = '1d'::text) AND (EXISTS ( SELECT 1
                   FROM public.bars bb
                  WHERE ((bb.ticker = ind.ticker) AND (bb.ts = ind.ts) AND (bb."interval" = ind."interval")))))
          ORDER BY ind.ts DESC
         LIMIT 1) latest)
     JOIN public.indicators i ON (((i.ticker = t.ticker) AND (i.ts = latest.ts) AND (i."interval" = '1d'::text))))
     JOIN public.bars b ON (((b.ticker = i.ticker) AND (b.ts = i.ts) AND (b."interval" = i."interval"))))
     LEFT JOIN public.market_days m ON ((m.ts = (i.ts)::date)))
     LEFT JOIN LATERAL ( SELECT u2.in_trade,
            u2.mcap_usd,
            u2.crit_mcap,
            u2.crit_above_sma200,
            u2.crit_sma200_slope,
            u2.crit_rel_return,
            u2.crit_rev_growth,
            u2.in_watch,
            u2.watch_reason
           FROM public.universe u2
          WHERE ((u2.ticker = i.ticker) AND (u2.as_of <= (i.ts)::date) AND (u2.config_hash = current_setting('capitalscan.default_config_hash'::text, true)))
          ORDER BY u2.as_of DESC
         LIMIT 1) u ON (true));


ALTER VIEW public.v_ticker_state OWNER TO capscan;

--
-- Name: v_positions; Type: VIEW; Schema: public; Owner: capscan
--

CREATE VIEW public.v_positions AS
 SELECT p.id,
    p.user_id,
    p.ticker,
    p.side,
    p.entry_date,
    p.entry_price,
    p.quantity,
    p.source,
    p.status,
    p.exit_date,
    p.exit_price,
    p.exit_reason,
    p.realized_ret,
    p.idempotency_key,
    p.created_at,
    s.close AS current_price,
        CASE
            WHEN (p.status = 'open'::text) THEN ((s.close - p.entry_price) / p.entry_price)
            ELSE p.realized_ret
        END AS unrealized_or_realized_ret,
    ( SELECT count(*) AS count
           FROM public.trading_days td
          WHERE ((td.d > p.entry_date) AND (td.d <= public.market_date()))) AS days_held,
        CASE
            WHEN (NOT c.exit_on_stoch_80) THEN NULL::boolean
            WHEN (p.side = 'short'::text) THEN (
            CASE
                WHEN (c.exit_stoch_source = 'k_fast'::text) THEN s.k_fast
                ELSE s.k_full
            END <= c.exit_stoch_threshold_short)
            ELSE (
            CASE
                WHEN (c.exit_stoch_source = 'k_fast'::text) THEN s.k_fast
                ELSE s.k_full
            END >= c.exit_stoch_threshold)
        END AS exit_signal_stoch,
        CASE
            WHEN (NOT c.exit_on_upper_band) THEN NULL::boolean
            WHEN (p.side = 'short'::text) THEN (s.close <= s.bb_lower)
            ELSE (s.close >= s.bb_upper)
        END AS exit_signal_upper_band,
        CASE
            WHEN (NOT c.exit_on_mid_band) THEN NULL::boolean
            WHEN (p.side = 'short'::text) THEN (s.close <= s.bb_mid)
            ELSE (s.close >= s.bb_mid)
        END AS exit_signal_mid_band,
    (( SELECT count(*) AS count
           FROM public.trading_days td
          WHERE ((td.d > p.entry_date) AND (td.d <= public.market_date()))) >= c.max_hold_days) AS exit_signal_timeout,
        CASE
            WHEN (c.exit_stoch_source = 'k_fast'::text) THEN s.k_fast
            ELSE s.k_full
        END AS exit_stoch_k
   FROM ((public.positions p
     LEFT JOIN public.v_ticker_state s ON ((s.ticker = p.ticker)))
     LEFT JOIN public.serving_config c ON (true));


ALTER VIEW public.v_positions OWNER TO capscan;

--
-- Name: v_screen; Type: VIEW; Schema: public; Owner: capscan
--

CREATE VIEW public.v_screen AS
 SELECT e.ticker,
    e.signal_date,
    e.signal_type,
    e.signal_types_all,
    e.signal_strength,
    e.touch_level,
    e.bb_pctb,
    e.k_full,
    e.k_fast,
    e.k_cross_up,
    e.dd_52w,
    e.dd_bucket,
    e.above_sma200,
    e.seq_in_cluster,
    e.cofire_count,
    t.sector,
    c.cell_id,
        CASE
            WHEN c.suppressed THEN NULL::numeric
            ELSE c.p_hit
        END AS p_hit,
        CASE
            WHEN c.suppressed THEN NULL::numeric
            ELSE c.baseline_empirical
        END AS baseline,
        CASE
            WHEN c.suppressed THEN NULL::numeric
            ELSE c.edge
        END AS edge,
        CASE
            WHEN c.suppressed THEN NULL::numeric
            ELSE c.ci_low
        END AS ci_low,
        CASE
            WHEN c.suppressed THEN NULL::numeric
            ELSE c.ci_high
        END AS ci_high,
    c.n_events,
    c.n_eff,
    c.q_value,
    c.suppressed,
    c.suppress_reason,
    p.q50,
    p.p_touch_3,
    p.p_touch_5,
    p.p_adverse_3,
    p.model_version
   FROM (((public.events e
     JOIN public.tickers t ON ((t.ticker = e.ticker)))
     LEFT JOIN public.cell_stats c ON (((c.signal_type = e.signal_type) AND (c.side = e.side) AND (c.dd_bucket = e.dd_bucket) AND (c.signal_strength IS NULL) AND (c.entry_kind = 'next_open'::text) AND (c.split_key = 'validate'::text) AND (c.era IS NULL) AND (c.horizon_days = 5) AND (c.target_pct = 0.03) AND (c.config_hash = current_setting('capitalscan.default_config_hash'::text, true)) AND (c.arm = 'signal'::text))))
     LEFT JOIN public.predictions p ON (((p.ticker = e.ticker) AND (p.as_of = e.signal_date))))
  WHERE (e.is_cluster_head AND (e.entry_kind = 'next_open'::text) AND e.in_trade AND (e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)));


ALTER VIEW public.v_screen OWNER TO capscan;

--
-- Name: v_screen_live; Type: VIEW; Schema: public; Owner: capscan
--

CREATE VIEW public.v_screen_live AS
 SELECT e.ticker,
    e.signal_date,
    e.signal_type,
    e.signal_types_all,
    e.signal_strength,
    e.side,
    e.touch_level,
    e.entry_price,
    e.k_fast,
    e.k_full,
    e.d_full,
    e.k_cross_up,
    e.bb_pctb,
    e.dd_52w,
    e.dd_bucket,
    e.above_sma200,
    e.seq_in_cluster,
    e.is_cluster_head,
    e.cofire_count,
    t.sector,
    ind.bb_lower,
    ind.bb_mid,
    ind.bb_upper,
    ind.ts AS band_ts,
    b.open,
    b.high,
    b.low,
    b.close,
    b.volume,
        CASE
            WHEN public.market_is_open() THEN lq.close
            ELSE NULL::numeric
        END AS live_price,
    lq.ts AS live_price_ts,
    fr.fired_at,
    rev.confirmed AS rev_confirmed,
    rev.above_band AS rev_above_band,
    rev.open_gap_atr AS rev_open_gap_atr,
    rev.rev_ts,
    c.cell_id,
        CASE
            WHEN (c.suppressed OR (NOT e.in_trade)) THEN NULL::numeric
            ELSE c.p_hit
        END AS p_hit,
        CASE
            WHEN (c.suppressed OR (NOT e.in_trade)) THEN NULL::numeric
            ELSE c.baseline_empirical
        END AS baseline,
        CASE
            WHEN (c.suppressed OR (NOT e.in_trade)) THEN NULL::numeric
            ELSE c.edge
        END AS edge,
        CASE
            WHEN (c.suppressed OR (NOT e.in_trade)) THEN NULL::numeric
            ELSE c.ci_low
        END AS ci_low,
        CASE
            WHEN (c.suppressed OR (NOT e.in_trade)) THEN NULL::numeric
            ELSE c.ci_high
        END AS ci_high,
    c.n_events,
    c.n_eff,
    c.q_value,
    (c.suppressed OR (NOT e.in_trade)) AS suppressed,
        CASE
            WHEN (NOT e.in_trade) THEN 'watch_universe'::text
            ELSE c.suppress_reason
        END AS suppress_reason,
    p.q50,
    p.p_touch_3,
    p.p_touch_5,
    p.p_adverse_3,
    p.model_version,
    e.in_watch,
    e.watch_reason
   FROM ((((((((public.events e
     LEFT JOIN LATERAL ( SELECT i2.bb_lower,
            i2.bb_mid,
            i2.bb_upper,
            i2.ts
           FROM public.indicators i2
          WHERE ((i2.ticker = e.ticker) AND (i2."interval" = '1d'::text) AND (i2.ts < e.signal_date))
          ORDER BY i2.ts DESC
         LIMIT 1) ind ON (true))
     LEFT JOIN public.bars b ON (((b.ticker = e.ticker) AND (b.ts = e.signal_date) AND (b."interval" = '1d'::text))))
     LEFT JOIN public.bars_live lq ON (((lq.ticker = e.ticker) AND (lq.session_date = public.market_date()))))
     LEFT JOIN LATERAL ( SELECT max(r.fired_at) AS fired_at
           FROM public.signal_reports r
          WHERE ((r.ticker = e.ticker) AND (((r.fired_at AT TIME ZONE 'America/New_York'::text))::date = e.signal_date) AND ((r.signal_type IS NULL) OR (r.signal_type = e.signal_type)))) fr ON (true))
     LEFT JOIN LATERAL ( SELECT (((r2.state_json -> 'bear_reversal'::text) ->> 'confirmed'::text))::boolean AS confirmed,
            (((r2.state_json -> 'bear_reversal'::text) ->> 'above_band'::text))::boolean AS above_band,
            (((r2.state_json -> 'bear_reversal'::text) ->> 'open_gap_atr'::text))::numeric AS open_gap_atr,
            r2.fired_at AS rev_ts
           FROM public.signal_reports r2
          WHERE ((r2.ticker = e.ticker) AND (((r2.fired_at AT TIME ZONE 'America/New_York'::text))::date = e.signal_date) AND (r2.state_json ? 'bear_reversal'::text))
          ORDER BY r2.fired_at DESC
         LIMIT 1) rev ON (true))
     JOIN public.tickers t ON ((t.ticker = e.ticker)))
     LEFT JOIN public.cell_stats c ON (((c.signal_type = e.signal_type) AND (c.side = e.side) AND (c.dd_bucket = e.dd_bucket) AND (c.signal_strength IS NULL) AND (c.entry_kind = 'next_open'::text) AND (c.split_key = 'validate'::text) AND (c.era IS NULL) AND (c.horizon_days = 5) AND (c.target_pct = 0.03) AND (c.config_hash = current_setting('capitalscan.default_config_hash'::text, true)) AND (c.arm = 'signal'::text))))
     LEFT JOIN public.predictions p ON (((p.ticker = e.ticker) AND (p.as_of = e.signal_date))))
  WHERE ((e.entry_kind = 'touch'::text) AND (e.in_trade OR e.in_watch) AND (e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)));


ALTER VIEW public.v_screen_live OWNER TO capscan;

--
-- Name: v_stats; Type: VIEW; Schema: public; Owner: capscan
--

CREATE VIEW public.v_stats AS
 SELECT cell_id,
    run_id,
    config_hash,
    signal_type,
    dd_bucket,
    signal_strength,
    side,
    entry_kind,
    arm,
    split_key,
    era,
    horizon_days,
    target_pct,
    n_events,
    n_eff,
    n_tickers,
    mean_cofire,
        CASE
            WHEN suppressed THEN NULL::numeric
            ELSE p_hit
        END AS p_hit,
        CASE
            WHEN suppressed THEN NULL::numeric
            ELSE baseline_empirical
        END AS baseline,
        CASE
            WHEN suppressed THEN NULL::numeric
            ELSE edge
        END AS edge,
        CASE
            WHEN suppressed THEN NULL::numeric
            ELSE ci_low
        END AS ci_low,
        CASE
            WHEN suppressed THEN NULL::numeric
            ELSE ci_high
        END AS ci_high,
    q_value,
    p_value_randomization,
    mean_ret,
    median_ret,
    ret_p25,
    ret_p75,
    mean_mfe,
    mean_mae,
    median_time_to_mfe,
    capture_ratio,
    p_touch_2pct,
    p_touch_3pct,
    p_touch_5pct,
    p_touch_10pct,
    median_day_touch_5pct,
    exit_mix,
    earnings_frac,
    suppressed,
    suppress_reason
   FROM public.cell_stats;


ALTER VIEW public.v_stats OWNER TO capscan;

--
-- Name: v_ticker_events; Type: VIEW; Schema: public; Owner: capscan
--

CREATE VIEW public.v_ticker_events AS
 SELECT e.ticker,
    t.sector,
    e.id,
    e.signal_date,
    e.signal_type,
    e.signal_types_all,
    e.signal_strength,
    e.cluster_id,
    e.seq_in_cluster,
    e.is_cluster_head,
    e.bb_pctb,
    e.k_full,
    e.k_fast,
    e.dd_52w,
    e.dd_bucket,
    e.above_sma200,
    e.vix_close,
    e.days_to_earnings,
    e.entry_kind,
    e.entry_date,
    e.entry_price,
    e.entry_gapped,
    e.exit_date,
    e.exit_price,
    e.exit_reason,
    e.holding_days,
    e.ambiguous,
    e.gross_ret,
    e.net_ret,
    e.mfe,
    e.mae,
    e.time_to_mfe,
    e.capture_ratio,
    e.touched_2pct,
    e.touched_3pct,
    e.touched_5pct,
    e.touched_10pct,
    e.day_touched_5pct,
    e.earnings_in_window,
    e.era,
    e.split_key,
    e.in_trade,
    false AS pending
   FROM (public.events e
     JOIN public.tickers t ON ((t.ticker = e.ticker)))
  WHERE ((e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)) AND (e.entry_kind = 'next_open'::text))
UNION ALL
 SELECT e.ticker,
    t.sector,
    e.id,
    e.signal_date,
    e.signal_type,
    e.signal_types_all,
    e.signal_strength,
    e.cluster_id,
    e.seq_in_cluster,
    e.is_cluster_head,
    e.bb_pctb,
    e.k_full,
    e.k_fast,
    e.dd_52w,
    e.dd_bucket,
    e.above_sma200,
    e.vix_close,
    e.days_to_earnings,
    e.entry_kind,
    e.entry_date,
    e.entry_price,
    e.entry_gapped,
    e.exit_date,
    e.exit_price,
    e.exit_reason,
    e.holding_days,
    e.ambiguous,
    e.gross_ret,
    e.net_ret,
    e.mfe,
    e.mae,
    e.time_to_mfe,
    e.capture_ratio,
    e.touched_2pct,
    e.touched_3pct,
    e.touched_5pct,
    e.touched_10pct,
    e.day_touched_5pct,
    e.earnings_in_window,
    e.era,
    e.split_key,
    e.in_trade,
    e.in_trade AS pending
   FROM (public.events e
     JOIN public.tickers t ON ((t.ticker = e.ticker)))
  WHERE ((e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)) AND (e.entry_kind = 'touch'::text) AND (NOT (EXISTS ( SELECT 1
           FROM public.events n
          WHERE ((n.config_hash = e.config_hash) AND (n.ticker = e.ticker) AND (n.signal_date = e.signal_date) AND (n.signal_type = e.signal_type) AND (n.entry_kind = 'next_open'::text))))));


ALTER VIEW public.v_ticker_events OWNER TO capscan;

--
-- Name: v_universe; Type: VIEW; Schema: public; Owner: capscan
--

CREATE VIEW public.v_universe AS
 SELECT DISTINCT ON (u.ticker) u.ticker,
    t.name,
    t.sector,
    t.industry,
    u.as_of,
    u.in_train,
    u.in_trade,
    u.mcap_usd,
    u.mcap_rank,
    u.adv_20d_usd,
    u.crit_mcap,
    u.crit_above_sma200,
    u.crit_sma200_slope,
    u.crit_rel_return,
    u.crit_rev_growth,
    t.is_active,
    t.delisted_on,
    u.in_watch,
    u.watch_reason
   FROM (public.universe u
     JOIN public.tickers t ON ((t.ticker = u.ticker)))
  WHERE (u.config_hash = current_setting('capitalscan.default_config_hash'::text, true))
  ORDER BY u.ticker, u.as_of DESC;


ALTER VIEW public.v_universe OWNER TO capscan;

--
-- Name: v_watchlist; Type: VIEW; Schema: public; Owner: capscan
--

CREATE VIEW public.v_watchlist AS
 SELECT e.ticker,
    e.signal_date,
    e.signal_type,
    e.signal_types_all,
    e.signal_strength,
    e.touch_level,
    e.bb_pctb,
    e.k_full,
    e.k_fast,
    e.k_cross_up,
    e.dd_52w,
    e.dd_bucket,
    e.above_sma200,
    e.seq_in_cluster,
    e.cofire_count,
    t.sector,
    u.watch_reason,
    u.mcap_usd,
    u.crit_rel_return,
    u.crit_above_sma200,
    u.crit_sma200_slope
   FROM ((public.events e
     JOIN public.tickers t ON ((t.ticker = e.ticker)))
     JOIN LATERAL ( SELECT u2.watch_reason,
            u2.mcap_usd,
            u2.crit_rel_return,
            u2.crit_above_sma200,
            u2.crit_sma200_slope
           FROM public.universe u2
          WHERE ((u2.ticker = e.ticker) AND (u2.as_of <= e.signal_date) AND (u2.config_hash = current_setting('capitalscan.default_config_hash'::text, true)))
          ORDER BY u2.as_of DESC
         LIMIT 1) u ON (true))
  WHERE (e.is_cluster_head AND (e.entry_kind = 'next_open'::text) AND e.in_watch AND (e.config_hash = current_setting('capitalscan.default_config_hash'::text, true)));


ALTER VIEW public.v_watchlist OWNER TO capscan;

--
-- Name: bar_rejects id; Type: DEFAULT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.bar_rejects ALTER COLUMN id SET DEFAULT nextval('public.bar_rejects_id_seq'::regclass);


--
-- Name: benchmarks id; Type: DEFAULT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.benchmarks ALTER COLUMN id SET DEFAULT nextval('public.benchmarks_id_seq'::regclass);


--
-- Name: events id; Type: DEFAULT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.events ALTER COLUMN id SET DEFAULT nextval('public.events_id_seq'::regclass);


--
-- Name: order_intents id; Type: DEFAULT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.order_intents ALTER COLUMN id SET DEFAULT nextval('public.order_intents_id_seq'::regclass);


--
-- Name: positions id; Type: DEFAULT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.positions ALTER COLUMN id SET DEFAULT nextval('public.positions_id_seq'::regclass);


--
-- Name: predictions id; Type: DEFAULT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.predictions ALTER COLUMN id SET DEFAULT nextval('public.predictions_id_seq'::regclass);


--
-- Name: signal_reports id; Type: DEFAULT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.signal_reports ALTER COLUMN id SET DEFAULT nextval('public.signal_reports_id_seq'::regclass);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: bar_rejects bar_rejects_pkey; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.bar_rejects
    ADD CONSTRAINT bar_rejects_pkey PRIMARY KEY (id);


--
-- Name: bars_live bars_live_pkey; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.bars_live
    ADD CONSTRAINT bars_live_pkey PRIMARY KEY (ticker, session_date);


--
-- Name: bars bars_pkey; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.bars
    ADD CONSTRAINT bars_pkey PRIMARY KEY (ticker, ts, "interval");


--
-- Name: benchmarks benchmarks_pkey; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.benchmarks
    ADD CONSTRAINT benchmarks_pkey PRIMARY KEY (id);


--
-- Name: cell_stats cell_stats_pkey; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.cell_stats
    ADD CONSTRAINT cell_stats_pkey PRIMARY KEY (cell_id, config_hash);


--
-- Name: corporate_actions corporate_actions_pkey; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.corporate_actions
    ADD CONSTRAINT corporate_actions_pkey PRIMARY KEY (ticker, ex_date, action_type);


--
-- Name: earnings earnings_pkey; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.earnings
    ADD CONSTRAINT earnings_pkey PRIMARY KEY (ticker, report_date);


--
-- Name: events events_config_hash_ticker_signal_date_signal_type_entry_kin_key; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_config_hash_ticker_signal_date_signal_type_entry_kin_key UNIQUE (config_hash, ticker, signal_date, signal_type, entry_kind);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (id);


--
-- Name: indicators indicators_pkey; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.indicators
    ADD CONSTRAINT indicators_pkey PRIMARY KEY (ticker, ts, "interval");


--
-- Name: market_days market_days_pkey; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.market_days
    ADD CONSTRAINT market_days_pkey PRIMARY KEY (ts);


--
-- Name: order_intents order_intents_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.order_intents
    ADD CONSTRAINT order_intents_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: order_intents order_intents_pkey; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.order_intents
    ADD CONSTRAINT order_intents_pkey PRIMARY KEY (id);


--
-- Name: outcomes outcomes_pkey; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.outcomes
    ADD CONSTRAINT outcomes_pkey PRIMARY KEY (prediction_id);


--
-- Name: path path_pkey; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.path
    ADD CONSTRAINT path_pkey PRIMARY KEY (event_id, day_offset);


--
-- Name: poller_sessions poller_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.poller_sessions
    ADD CONSTRAINT poller_sessions_pkey PRIMARY KEY (session_date);


--
-- Name: positions positions_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT positions_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: positions positions_pkey; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT positions_pkey PRIMARY KEY (id);


--
-- Name: predictions predictions_pkey; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.predictions
    ADD CONSTRAINT predictions_pkey PRIMARY KEY (id);


--
-- Name: quotes_live quotes_live_pkey; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.quotes_live
    ADD CONSTRAINT quotes_live_pkey PRIMARY KEY (ticker, ts);


--
-- Name: rho_era rho_era_pkey; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.rho_era
    ADD CONSTRAINT rho_era_pkey PRIMARY KEY (era, config_hash);


--
-- Name: runs runs_pkey; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.runs
    ADD CONSTRAINT runs_pkey PRIMARY KEY (run_id);


--
-- Name: scheduled_runs scheduled_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.scheduled_runs
    ADD CONSTRAINT scheduled_runs_pkey PRIMARY KEY (job, scheduled_for);


--
-- Name: serving_config serving_config_pkey; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.serving_config
    ADD CONSTRAINT serving_config_pkey PRIMARY KEY (only_row);


--
-- Name: shares_outstanding shares_outstanding_pkey; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.shares_outstanding
    ADD CONSTRAINT shares_outstanding_pkey PRIMARY KEY (ticker, filed_on);


--
-- Name: signal_reports signal_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.signal_reports
    ADD CONSTRAINT signal_reports_pkey PRIMARY KEY (id);


--
-- Name: tickers tickers_pkey; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.tickers
    ADD CONSTRAINT tickers_pkey PRIMARY KEY (ticker);


--
-- Name: trading_days trading_days_pkey; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.trading_days
    ADD CONSTRAINT trading_days_pkey PRIMARY KEY (d);


--
-- Name: universe universe_pkey; Type: CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.universe
    ADD CONSTRAINT universe_pkey PRIMARY KEY (ticker, as_of, config_hash);


--
-- Name: bars_ts_idx; Type: INDEX; Schema: public; Owner: capscan
--

CREATE INDEX bars_ts_idx ON public.bars USING btree (ts);


--
-- Name: benchmarks_lookup; Type: INDEX; Schema: public; Owner: capscan
--

CREATE INDEX benchmarks_lookup ON public.benchmarks USING btree (run_id, arm, split_key);


--
-- Name: events_in_trade; Type: INDEX; Schema: public; Owner: capscan
--

CREATE INDEX events_in_trade ON public.events USING btree (config_hash, split_key, signal_type) WHERE in_trade;


--
-- Name: events_lookup; Type: INDEX; Schema: public; Owner: capscan
--

CREATE INDEX events_lookup ON public.events USING btree (signal_type, split_key, dd_bucket);


--
-- Name: events_ticker_date; Type: INDEX; Schema: public; Owner: capscan
--

CREATE INDEX events_ticker_date ON public.events USING btree (ticker, signal_date);


--
-- Name: indicators_daily_latest; Type: INDEX; Schema: public; Owner: capscan
--

CREATE INDEX indicators_daily_latest ON public.indicators USING btree (ticker, ts DESC) WHERE ("interval" = '1d'::text);


--
-- Name: indicators_ts_idx; Type: INDEX; Schema: public; Owner: capscan
--

CREATE INDEX indicators_ts_idx ON public.indicators USING btree (ts);


--
-- Name: universe_config_ticker_asof_idx; Type: INDEX; Schema: public; Owner: capscan
--

CREATE INDEX universe_config_ticker_asof_idx ON public.universe USING btree (config_hash, ticker, as_of DESC);


--
-- Name: bars_live bars_live_ticker_fkey; Type: FK CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.bars_live
    ADD CONSTRAINT bars_live_ticker_fkey FOREIGN KEY (ticker) REFERENCES public.tickers(ticker);


--
-- Name: bars bars_ticker_fkey; Type: FK CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.bars
    ADD CONSTRAINT bars_ticker_fkey FOREIGN KEY (ticker) REFERENCES public.tickers(ticker);


--
-- Name: corporate_actions corporate_actions_ticker_fkey; Type: FK CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.corporate_actions
    ADD CONSTRAINT corporate_actions_ticker_fkey FOREIGN KEY (ticker) REFERENCES public.tickers(ticker);


--
-- Name: events events_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.runs(run_id);


--
-- Name: outcomes outcomes_prediction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.outcomes
    ADD CONSTRAINT outcomes_prediction_id_fkey FOREIGN KEY (prediction_id) REFERENCES public.predictions(id);


--
-- Name: path path_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.path
    ADD CONSTRAINT path_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.events(id) ON DELETE CASCADE;


--
-- Name: shares_outstanding shares_outstanding_ticker_fkey; Type: FK CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.shares_outstanding
    ADD CONSTRAINT shares_outstanding_ticker_fkey FOREIGN KEY (ticker) REFERENCES public.tickers(ticker);


--
-- Name: signal_reports signal_reports_prediction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.signal_reports
    ADD CONSTRAINT signal_reports_prediction_id_fkey FOREIGN KEY (prediction_id) REFERENCES public.predictions(id);


--
-- Name: universe universe_ticker_fkey; Type: FK CONSTRAINT; Schema: public; Owner: capscan
--

ALTER TABLE ONLY public.universe
    ADD CONSTRAINT universe_ticker_fkey FOREIGN KEY (ticker) REFERENCES public.tickers(ticker);


--
-- Name: SCHEMA public; Type: ACL; Schema: -; Owner: pg_database_owner
--

GRANT USAGE ON SCHEMA public TO capscan_ro;


--
-- Name: TABLE alembic_version; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.alembic_version TO capscan_ro;


--
-- Name: TABLE bar_rejects; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.bar_rejects TO capscan_ro;


--
-- Name: TABLE bars; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.bars TO capscan_ro;


--
-- Name: TABLE bars_live; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.bars_live TO capscan_ro;


--
-- Name: TABLE benchmarks; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.benchmarks TO capscan_ro;


--
-- Name: TABLE cell_stats; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.cell_stats TO capscan_ro;


--
-- Name: TABLE corporate_actions; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.corporate_actions TO capscan_ro;


--
-- Name: TABLE earnings; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.earnings TO capscan_ro;


--
-- Name: TABLE events; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.events TO capscan_ro;


--
-- Name: TABLE indicators; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.indicators TO capscan_ro;


--
-- Name: TABLE market_days; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.market_days TO capscan_ro;


--
-- Name: TABLE order_intents; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.order_intents TO capscan_ro;


--
-- Name: TABLE outcomes; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.outcomes TO capscan_ro;


--
-- Name: TABLE path; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.path TO capscan_ro;


--
-- Name: TABLE poller_sessions; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.poller_sessions TO capscan_ro;


--
-- Name: TABLE positions; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.positions TO capscan_ro;


--
-- Name: TABLE predictions; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.predictions TO capscan_ro;


--
-- Name: TABLE quotes_live; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.quotes_live TO capscan_ro;


--
-- Name: TABLE rho_era; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.rho_era TO capscan_ro;


--
-- Name: TABLE runs; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.runs TO capscan_ro;


--
-- Name: TABLE scheduled_runs; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.scheduled_runs TO capscan_ro;


--
-- Name: TABLE serving_config; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.serving_config TO capscan_ro;


--
-- Name: TABLE shares_outstanding; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.shares_outstanding TO capscan_ro;


--
-- Name: TABLE shares_scale_errors_pre_adr146; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.shares_scale_errors_pre_adr146 TO capscan_ro;


--
-- Name: TABLE signal_reports; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.signal_reports TO capscan_ro;


--
-- Name: TABLE tickers; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.tickers TO capscan_ro;


--
-- Name: TABLE trading_days; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.trading_days TO capscan_ro;


--
-- Name: TABLE universe; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.universe TO capscan_ro;


--
-- Name: TABLE v_chart; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.v_chart TO capscan_ro;


--
-- Name: TABLE v_events; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.v_events TO capscan_ro;


--
-- Name: TABLE v_forward; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.v_forward TO capscan_ro;


--
-- Name: TABLE v_ticker_state; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.v_ticker_state TO capscan_ro;


--
-- Name: TABLE v_positions; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.v_positions TO capscan_ro;


--
-- Name: TABLE v_screen; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.v_screen TO capscan_ro;


--
-- Name: TABLE v_screen_live; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.v_screen_live TO capscan_ro;


--
-- Name: TABLE v_stats; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.v_stats TO capscan_ro;


--
-- Name: TABLE v_ticker_events; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.v_ticker_events TO capscan_ro;


--
-- Name: TABLE v_universe; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.v_universe TO capscan_ro;


--
-- Name: TABLE v_watchlist; Type: ACL; Schema: public; Owner: capscan
--

GRANT SELECT ON TABLE public.v_watchlist TO capscan_ro;


--
-- Name: DEFAULT PRIVILEGES FOR TABLES; Type: DEFAULT ACL; Schema: public; Owner: capscan
--

ALTER DEFAULT PRIVILEGES FOR ROLE capscan IN SCHEMA public GRANT SELECT ON TABLES TO capscan_ro;


--
-- PostgreSQL database dump complete
--


