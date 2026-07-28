MODEL_STATUS_SCHEMA = {
    "name": "haven_model_status",
    "description": (
        "Read the latest deterministic Haven v0.4 P/S/G/E/R/I/N scores, "
        "coverage, market state, DATA_GUARD status, real-breadth shadow, "
        "option-chain shadow, and allowed research actions. Use this for "
        "questions such as '现在几个模型指标如何', whether to add a panic "
        "position, sell a Put/Call, or buy insurance. Never estimate these "
        "scores from prose."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "refresh": {
                "type": "boolean",
                "description": (
                    "Fetch and recompute official data before reading. "
                    "Use only when the user asks for an update or after the "
                    "US close; otherwise read the latest completed snapshot."
                ),
                "default": False,
            },
            "include_shadow": {
                "type": "boolean",
                "description": (
                    "Include real Nasdaq-100 breadth, delayed QQQ chain, "
                    "and the non-executable N news/X shadow layer."
                ),
                "default": True,
            },
        },
        "additionalProperties": False,
    },
}


REFRESH_CLOSE_SCHEMA = {
    "name": "haven_refresh_close",
    "description": (
        "Fetch current official market inputs and produce a new Haven v0.4 "
        "close snapshot. This is research-only, writes only the v0.4 shadow "
        "outputs, and cannot send or place broker orders."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "force_market_data": {
                "type": "boolean",
                "description": "Force refresh of price, Cboe, and FRED data.",
                "default": True,
            },
            "force_breadth": {
                "type": "boolean",
                "description": (
                    "Force refresh of all current Nasdaq-100 member histories."
                ),
                "default": True,
            },
            "force_option_chain": {
                "type": "boolean",
                "description": "Force refresh of the delayed QQQ option chain.",
                "default": True,
            },
        },
        "additionalProperties": False,
    },
}


TQQQ_CALL_SCHEMA = {
    "name": "haven_screen_tqqq_calls",
    "description": (
        "Screen delayed TQQQ covered-call quotes using seller Bid, estimated "
        "Delta, DTE, spread, volume, open interest, cost basis, prior premium, "
        "and the current v0.4 G/E/R_call gate. Use when the user asks which "
        "TQQQ Call is most suitable. Always return WAIT when the model gate "
        "is closed. Candidates are research diagnostics and never orders."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "shares": {
                "type": "integer",
                "minimum": 0,
                "description": "Current TQQQ share count.",
            },
            "cost_basis": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": (
                    "Optional current TQQQ cost basis per share in USD."
                ),
            },
            "historical_premium": {
                "type": "number",
                "minimum": 0,
                "description": (
                    "Optional total previously realized option premium in USD."
                ),
                "default": 0,
            },
            "refresh_model": {
                "type": "boolean",
                "description": "Refresh the close model before screening.",
                "default": False,
            },
            "force_option_chain": {
                "type": "boolean",
                "description": "Force a new delayed TQQQ option-chain fetch.",
                "default": True,
            },
            "maximum_candidates": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "Maximum number of ranked diagnostics.",
                "default": 5,
            },
        },
        "required": ["shares"],
        "additionalProperties": False,
    },
}


BACKTEST_SCHEMA = {
    "name": "haven_backtest_v04",
    "description": (
        "Read or rerun the deterministic ten-year Haven v0.4 enriched "
        "backtest and return headline metrics, stress results, and the run "
        "manifest. This never writes production, Paper/Live, broker, or "
        "outputs/latest paths."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "rerun": {
                "type": "boolean",
                "description": (
                    "Rerun the complete v0.4 research experiment before "
                    "returning results. This can take several minutes."
                ),
                "default": False,
            }
        },
        "additionalProperties": False,
    },
}


SCHEMAS = [
    MODEL_STATUS_SCHEMA,
    REFRESH_CLOSE_SCHEMA,
    TQQQ_CALL_SCHEMA,
    BACKTEST_SCHEMA,
]
