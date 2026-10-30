"""Validate synthetic policy/configuration before any material may pass review."""
from __future__ import annotations

import json
from pathlib import Path

from .domain import parse_time, utc_text


class ConfigurationError(ValueError):
    pass


def load_policy(path):
    path = Path(path)
    if not path.is_file():
        raise ConfigurationError("rules.json is required; unavailable policy cannot authorize review")
    try:
        policy = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ConfigurationError("rules.json is unavailable or invalid JSON") from exc
    if not isinstance(policy, dict):
        raise ConfigurationError("rules.json must be an object ({} explicitly enables only built-in checks)")
    known = {"version", "description", "blocked_terms", "required_terms", "risk_terms", "rules", "scheduling_policy"}
    if set(policy) - known:
        raise ConfigurationError("rules.json contains unsupported fields")
    for name in ("blocked_terms", "required_terms", "risk_terms"):
        terms = policy.get(name, [])
        if not isinstance(terms, list) or any(not isinstance(t, str) or not t.strip() for t in terms):
            raise ConfigurationError(name + " must be an array of nonempty strings")
    rules = policy.get("rules", [])
    if not isinstance(rules, list):
        raise ConfigurationError("rules must be an array")
    types = {"required_field", "required", "required_terms", "required_term", "must_include", "max_length",
             "enum", "allowed_values", "blocked_terms", "blocked_term", "forbidden", "forbidden_terms", "risk_terms", "risk_term"}
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("type", "blocked_terms") not in types:
            raise ConfigurationError("unsupported rule type")
        if rule.get("severity", "hard") not in {"hard", "block", "error", "hard_block", "risk", "soft", "warning"}:
            raise ConfigurationError("unsupported rule severity")
        if "field" in rule and (not isinstance(rule["field"], str) or not rule["field"].strip()):
            raise ConfigurationError("rule field must be a nonempty string")
        kind = rule.get("type", "blocked_terms")
        pattern = rule.get("pattern", rule.get("term"))
        if kind in {"required_field", "required"}:
            if not isinstance(rule.get("field", pattern), str) or not rule.get("field", pattern):
                raise ConfigurationError("required-field rule needs a field")
        elif kind == "max_length":
            limit = rule.get("limit", rule.get("max"))
            if type(limit) is not int or limit < 0:
                raise ConfigurationError("max_length needs a nonnegative integer limit")
        elif kind in {"enum", "allowed_values"}:
            if not rule.get("field") or not isinstance(rule.get("allowed_values", rule.get("values")), list):
                raise ConfigurationError("enum rule needs field and values array")
        else:
            patterns = pattern if isinstance(pattern, list) else [pattern]
            if not patterns or any(not isinstance(t, str) or not t.strip() for t in patterns):
                raise ConfigurationError("term rule needs nonempty pattern(s)")
    scheduling = policy.get("scheduling_policy")
    if scheduling is not None:
        if not isinstance(scheduling, dict) or set(scheduling) != {"posting_interval_minutes"}:
            raise ConfigurationError("scheduling_policy needs posting_interval_minutes only")
        interval = scheduling["posting_interval_minutes"]
        if type(interval) is not int or not 1 <= interval <= 1440:
            raise ConfigurationError("posting_interval_minutes must be an integer in 1..1440")
    return policy


def load_candidate_context(path):
    """Optional factual synthetic signals. No executable instructions or preferences."""
    path = Path(path)
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict) or set(value) != {"slots"} or not isinstance(value["slots"], list):
        raise ConfigurationError("candidate_context must contain a slots array")
    result = {}
    for item in value["slots"]:
        expected = {"start", "channel", "audience_activity_index", "moderator_available"}
        if not isinstance(item, dict) or set(item) != expected:
            raise ConfigurationError("invalid candidate context fields")
        if not isinstance(item["channel"], str) or not item["channel"].strip():
            raise ConfigurationError("candidate context needs a channel")
        if type(item["audience_activity_index"]) is not int or not 0 <= item["audience_activity_index"] <= 100:
            raise ConfigurationError("audience_activity_index must be an integer in 0..100")
        if type(item["moderator_available"]) is not bool:
            raise ConfigurationError("moderator_available must be boolean")
        key = (item["channel"], utc_text(parse_time(item["start"])))
        if key in result:
            raise ConfigurationError("duplicate candidate context")
        result[key] = {k: item[k] for k in ("audience_activity_index", "moderator_available")}
    return result
